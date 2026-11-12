"""One-shot GLM HTTP adapter; never log requests or provider errors."""
from dataclasses import dataclass, field
from typing import Literal
import socket
import math
from uuid import UUID, uuid4
import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from app.core.config import ConfigurationError
from app.core.llm_config import LLMSettings, validate_base_url
from app.graph.state import BudgetAccounting, BudgetUsage, PaperAgentState


ErrorKind = Literal["configuration", "authentication", "timeout", "network", "dns", "connection", "invalid_request", "not_found", "rate_limited", "provider_unavailable", "invalid_json", "invalid_response", "output_truncated", "tool_calls_unsupported"]


class LLMError(Exception):
    def __init__(self, code: ErrorKind, *, http_status: int | None = None,
                 request_stage: str = "validation", retryable: bool = False,
                 provider_request_id: str | None = None, usage: BudgetUsage | None = None,
                 call_id: str | None = None, outcome: Literal["not_sent", "rejected", "unknown", "received"] = "not_sent"):
        self.code = code
        self.error_code = code
        self.error_category = code
        self.http_status = http_status
        self.request_stage = request_stage
        self.retryable = retryable
        self.provider_request_id = provider_request_id
        self.usage = dict(usage) if usage is not None else None
        self.call_id = call_id
        self.outcome = outcome
        super().__init__("Model call failed: " + code)


def _is_dns_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return True
        current = current.__cause__ or current.__context__
    return False


def _safe_request_id_value(value: object, api_key: str) -> str | None:
    # Provider values are untrusted; retain canonical UUIDs only.
    if not isinstance(value, str) or len(value) != 36 or value.casefold() in api_key.casefold():
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return str(parsed) if value.lower() == str(parsed) else None


def _safe_request_id(response: httpx.Response, api_key: str) -> str | None:
    return _safe_request_id_value(response.headers.get("x-request-id") or response.headers.get("request-id"), api_key)


@dataclass(frozen=True)
class LLMResult:
    text: str = field(repr=False)
    usage: BudgetUsage | None = None
    # No unverified model price is assumed.
    cost: None = None
    call_id: str = field(default_factory=lambda: str(uuid4()))
    http_status: int | None = None
    provider_request_id: str | None = None


def extract_usage(raw: object) -> BudgetUsage | None:
    if not isinstance(raw, dict):
        return None
    prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
    if type(prompt) is not int or type(completion) is not int or prompt < 0 or completion < 0:
        return None
    return {"prompt_tokens": prompt, "completion_tokens": completion}


class UsageAccumulator:
    """Single-threaded accumulator; do not share one instance across concurrent nodes."""
    def __init__(self, initial: BudgetUsage | None = None):
        valid = extract_usage(initial)
        if initial is not None and valid is None:
            raise ValueError("Invalid initial token usage")
        self._known: BudgetUsage = valid if valid is not None else {"prompt_tokens": 0, "completion_tokens": 0}
        self._complete = initial is None  # Legacy totals have unknown completeness.
        self._call_ids: set[str] = set()

    def add(self, usage: BudgetUsage | None, *, call_id: str) -> bool:
        try:
            canonical = str(UUID(call_id))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Invalid call identity") from None
        if canonical != call_id:
            raise ValueError("Invalid call identity")
        if canonical in self._call_ids:
            return False
        self._call_ids.add(canonical)
        valid = extract_usage(usage)
        if valid is None:
            self._complete = False
        else:
            self._known = {"prompt_tokens": self._known["prompt_tokens"] + valid["prompt_tokens"],
                           "completion_tokens": self._known["completion_tokens"] + valid["completion_tokens"]}
        return True

    def add_result(self, result: LLMResult | LLMError) -> bool:
        if result.call_id is None:
            raise ValueError("Call identity unavailable")
        return self.add(result.usage, call_id=result.call_id)

    @property
    def budget_usage(self) -> BudgetUsage | None:
        return dict(self._known) if self._complete and self._call_ids else None

    @property
    def known_usage(self) -> BudgetUsage:
        return dict(self._known)

    def snapshot(self) -> BudgetAccounting:
        return {"known_usage": dict(self._known), "complete": self._complete, "call_ids": sorted(self._call_ids)}

    @classmethod
    def from_state(cls, state: PaperAgentState) -> "UsageAccumulator":
        # Missing/malformed accounting in old checkpoints is never complete.
        raw = state.get("budget_accounting")
        if raw is not None:
            if not isinstance(raw, dict) or set(raw) - {"known_usage", "complete", "call_ids"}:
                raise ValueError("Invalid accounting checkpoint")
            known = extract_usage(raw.get("known_usage"))
            complete, ids = raw.get("complete", False), raw.get("call_ids", [])
            if known is None or type(complete) is not bool or not isinstance(ids, list):
                raise ValueError("Invalid accounting checkpoint")
            if "complete" not in raw or "call_ids" not in raw:
                complete = False
            result = cls()
            for value in ids:
                if not isinstance(value, str):
                    raise ValueError("Invalid accounting checkpoint")
                try: canonical = str(UUID(value))
                except ValueError: raise ValueError("Invalid accounting checkpoint") from None
                if value != canonical or value in result._call_ids:
                    raise ValueError("Invalid accounting checkpoint")
                result._call_ids.add(value)
            if complete and not ids and known != {"prompt_tokens": 0, "completion_tokens": 0}:
                raise ValueError("Invalid accounting checkpoint")
            result._known, result._complete = known, complete
            return result
        old = state.get("budget_usage")
        if old is not None and extract_usage(old) is None:
            raise ValueError("Invalid accounting checkpoint")
        result = cls(old)
        result._complete = False
        return result


def _messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    if not messages:
        raise LLMError("invalid_request")
    result = []
    for msg in messages:
        if isinstance(msg, SystemMessage): role = "system"
        elif isinstance(msg, HumanMessage): role = "user"
        elif isinstance(msg, AIMessage) and not msg.tool_calls: role = "assistant"
        else: raise LLMError("invalid_request")
        if not isinstance(msg.content, str) or not msg.content:
            raise LLMError("invalid_request")
        result.append({"role": role, "content": msg.content})
    return result


class GLMClient:
    def __init__(self, settings: LLMSettings, *, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    @property
    def model(self) -> str:
        return self._settings.model

    @property
    def planner_read_timeout_seconds(self) -> float:
        return self._settings.planner_read_timeout_seconds

    def invoke(self, messages: list[BaseMessage], *, max_tokens: int = 256, temperature: float | None = None, call_id: str | None = None, read_timeout_seconds: float | None = None, reasoning_effort: Literal["low", "high", "max"] | None = None) -> LLMResult:
        try:
            if call_id is not None:
                try:
                    valid = str(UUID(call_id)) == call_id
                except (ValueError, TypeError, AttributeError):
                    valid = False
                if not valid:
                    raise LLMError("invalid_request")
            return self._invoke(messages, max_tokens=max_tokens, temperature=temperature, call_id=call_id, read_timeout_seconds=read_timeout_seconds, reasoning_effort=reasoning_effort)
        except LLMError as error:
            if call_id is not None and error.call_id is None:
                try:
                    if str(UUID(call_id)) == call_id:
                        error.call_id = call_id
                except (ValueError, TypeError, AttributeError):
                    pass
            # Remove provider exceptions and internal frames (which can retain
            # request objects and credentials), including an outer active error.
            error.__context__ = None
            error.__cause__ = None
            error.__traceback__ = None
            raise

    def _invoke(self, messages: list[BaseMessage], *, max_tokens: int, temperature: float | None, call_id: str | None = None, read_timeout_seconds: float | None = None, reasoning_effort: Literal["low", "high", "max"] | None = None) -> LLMResult:
        invalid_url = False
        try:
            validate_base_url(self._settings.base_url)
        except ConfigurationError:
            invalid_url = True
        if invalid_url:
            raise LLMError("configuration")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 65536:
            raise LLMError("invalid_request")
        if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 1):
            raise LLMError("invalid_request")
        if read_timeout_seconds is not None and (isinstance(read_timeout_seconds, bool) or not isinstance(read_timeout_seconds, (int, float)) or not math.isfinite(read_timeout_seconds) or not 0 < read_timeout_seconds <= 600):
            raise LLMError("invalid_request")
        if reasoning_effort is not None and reasoning_effort not in ("low", "high", "max"):
            raise LLMError("invalid_request")
        body: dict[str, object] = {"model": self._settings.model, "messages": _messages(messages), "max_tokens": max_tokens, "stream": False}
        if temperature is not None: body["temperature"] = temperature
        if reasoning_effort is not None: body["reasoning_effort"] = reasoning_effort
        call_id = call_id or str(uuid4())
        # Correlation only: a provider request_id is not an idempotency guarantee.
        body["request_id"] = call_id
        failure: LLMError | None = None
        response: httpx.Response | None = None
        try:
            read_timeout = read_timeout_seconds if read_timeout_seconds is not None else self._settings.timeout_seconds
            timeout = httpx.Timeout(connect=min(10.0, read_timeout), write=min(20.0, read_timeout), read=read_timeout, pool=min(10.0, read_timeout))
            with httpx.Client(transport=self._transport, timeout=timeout, follow_redirects=False, trust_env=False) as client:
                response = client.post(self._settings.base_url.rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + self._settings.api_key}, json=body)
        except httpx.ConnectTimeout:
            failure = LLMError("timeout", request_stage="connect", call_id=call_id, outcome="not_sent")
        except httpx.PoolTimeout:
            failure = LLMError("timeout", request_stage="pool", call_id=call_id, outcome="not_sent")
        except httpx.WriteTimeout:
            failure = LLMError("timeout", request_stage="write", call_id=call_id, outcome="unknown")
        except httpx.ReadTimeout:
            failure = LLMError("timeout", request_stage="read", call_id=call_id, outcome="unknown")
        except httpx.TimeoutException:
            failure = LLMError("timeout", request_stage="request", call_id=call_id, outcome="unknown")
        except httpx.ConnectError as error:
            failure = LLMError("dns" if _is_dns_error(error) else "connection", request_stage="connect", call_id=call_id, outcome="not_sent")
        except httpx.NetworkError:
            failure = LLMError("network", request_stage="request", call_id=call_id, outcome="unknown")
        except (httpx.InvalidURL, UnicodeError, ValueError):
            failure = LLMError("configuration", request_stage="validation", call_id=call_id, outcome="not_sent")
        except httpx.HTTPError:
            failure = LLMError("provider_unavailable", request_stage="request", call_id=call_id, outcome="unknown")
        if failure is not None:
            raise failure
        assert response is not None
        code = response.status_code
        request_id = _safe_request_id(response, self._settings.api_key)
        if code in (401, 403): raise LLMError("authentication", http_status=code, request_stage="response", provider_request_id=request_id, call_id=call_id, outcome="rejected")
        if code == 404: raise LLMError("not_found", http_status=code, request_stage="response", provider_request_id=request_id, call_id=call_id, outcome="rejected")
        if code == 429: raise LLMError("rate_limited", http_status=code, request_stage="response", provider_request_id=request_id, call_id=call_id, outcome="rejected")
        if 400 <= code < 500: raise LLMError("invalid_request", http_status=code, request_stage="response", provider_request_id=request_id, call_id=call_id, outcome="rejected")
        if code != 200: raise LLMError("provider_unavailable", http_status=code, request_stage="response", provider_request_id=request_id, call_id=call_id, outcome="unknown")
        json_invalid = False
        try:
            data = response.json()
        except ValueError:
            data = None
            json_invalid = True
        if json_invalid:
            raise LLMError("invalid_json", http_status=code, request_stage="response_parse", provider_request_id=request_id, call_id=call_id, outcome="received")
        if isinstance(data, dict):
            request_id = _safe_request_id_value(data.get("request_id"), self._settings.api_key) or request_id
        usage = extract_usage(data.get("usage")) if isinstance(data, dict) else None
        def response_error(kind: ErrorKind) -> LLMError:
            return LLMError(kind, http_status=code, request_stage="response_parse",
                            provider_request_id=request_id, usage=usage, call_id=call_id, outcome="received")
        if not isinstance(data, dict):
            raise response_error("invalid_response")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise response_error("invalid_response")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise response_error("invalid_response")
        if message.get("role") != "assistant":
            raise response_error("invalid_response")
        reason = choice.get("finish_reason")
        tools = message.get("tool_calls")
        if tools is not None and not isinstance(tools, list):
            raise response_error("invalid_response")
        if reason == "length":
            raise response_error("output_truncated")
        if reason == "tool_calls":
            if not tools:
                raise response_error("invalid_response")
            raise response_error("tool_calls_unsupported")
        if reason != "stop" or tools:
            raise response_error("invalid_response")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise response_error("invalid_response")
        return LLMResult(text=text, usage=usage, call_id=call_id, http_status=code, provider_request_id=request_id)
