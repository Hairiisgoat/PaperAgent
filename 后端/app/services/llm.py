"""LLM entry point, constructed lazily; no Agent owns credentials."""
from langchain_core.messages import BaseMessage
from typing import Literal
from app.core.config import ConfigurationError
from app.core.llm_config import load_llm_settings
from app.infrastructure.glm_client import GLMClient, LLMError, LLMResult


class LLMService:
    def __init__(self, client: GLMClient):
        self._client = client

    @property
    def model(self) -> str:
        return self._client.model

    @property
    def planner_read_timeout_seconds(self) -> float:
        return self._client.planner_read_timeout_seconds

    @classmethod
    def from_settings(cls) -> "LLMService":
        try:
            return cls._from_settings()
        except LLMError as error:
            error.__context__ = None
            error.__cause__ = None
            error.__traceback__ = None
            raise

    @classmethod
    def _from_settings(cls) -> "LLMService":
        failed = False
        try:
            return cls(GLMClient(load_llm_settings()))
        except ConfigurationError:
            failed = True
        if failed:
            raise LLMError("configuration")
        raise AssertionError("Unreachable")

    def invoke(self, messages: list[BaseMessage], *, max_tokens: int = 256, temperature: float | None = None, call_id: str | None = None, read_timeout_seconds: float | None = None, reasoning_effort: Literal["low", "high", "max"] | None = None) -> LLMResult:
        try:
            if call_id is None and read_timeout_seconds is None and reasoning_effort is None:
                return self._client.invoke(messages, max_tokens=max_tokens, temperature=temperature)
            if reasoning_effort is None:
                return self._client.invoke(messages, max_tokens=max_tokens, temperature=temperature, call_id=call_id, read_timeout_seconds=read_timeout_seconds)
            return self._client.invoke(messages, max_tokens=max_tokens, temperature=temperature, call_id=call_id, read_timeout_seconds=read_timeout_seconds, reasoning_effort=reasoning_effort)
        except LLMError as error:
            error.__context__ = None
            error.__cause__ = None
            error.__traceback__ = None
            raise
