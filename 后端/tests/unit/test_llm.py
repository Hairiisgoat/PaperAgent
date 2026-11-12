"""Offline GLM tests. The live test is opt-in and never inspects .env in default runs."""
import json
import os
from pathlib import Path
from uuid import uuid4
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from app.core.config import ConfigurationError
from app.core.llm_config import LLMSettings, load_llm_settings
from app.infrastructure.glm_client import GLMClient, LLMError, UsageAccumulator
from app.services.llm import LLMService


@pytest.fixture
def settings():
    return LLMSettings(api_key="local-test-secret", model="test-model")


def test_configuration_is_lazy_and_redacted(tmp_path, monkeypatch):
    for name in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "settings.env"
    path.write_text('LLM_API_KEY=local-test-secret\nLLM_MODEL=test-model\nLLM_TIMEOUT_SECONDS=2\n', encoding="utf8")
    settings = load_llm_settings(path)
    assert settings.model == "test-model" and settings.timeout_seconds == 2
    assert "local-test-secret" not in repr(settings)
    monkeypatch.setenv("LLM_MODEL", "override")
    assert load_llm_settings(path).model == "override"
    with pytest.raises(ConfigurationError): load_llm_settings(tmp_path / "missing.env")
    path.write_text('LLM_API_KEY=x\nLLM_MODEL=y\nLLM_BASE_URL=http://example.invalid\n', encoding="utf8")
    with pytest.raises(ConfigurationError): load_llm_settings(path)


def test_success_and_usage_accumulation(settings):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "example output"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 12, "completion_tokens": 3}})
    service = LLMService(GLMClient(settings, transport=httpx.MockTransport(respond)))
    result = service.invoke([SystemMessage(content="system"), HumanMessage(content="hello"), AIMessage(content="prior")], max_tokens=16)
    assert result.text == "example output" and result.usage == {"prompt_tokens": 12, "completion_tokens": 3} and result.cost is None
    assert "example output" not in repr(result) and len(requests) == 1
    assert requests[0].url.path.endswith('/chat/completions')
    totals = UsageAccumulator()
    totals.add({"prompt_tokens": 2, "completion_tokens": 1}, call_id=str(uuid4()))
    totals.add_result(result); totals.add(result.usage, call_id=str(uuid4()))
    assert totals.budget_usage == {"prompt_tokens": 26, "completion_tokens": 7}
    serde = JsonPlusSerializer()
    assert serde.loads_typed(serde.dumps_typed({"budget_usage": totals.budget_usage})) == {"budget_usage": totals.budget_usage}
    totals.add(None, call_id=str(uuid4())); totals.add(result.usage, call_id=str(uuid4()))
    assert totals.budget_usage is None and totals.known_usage == {"prompt_tokens": 38, "completion_tokens": 10}


@pytest.mark.parametrize('effort', ['low', 'high', 'max'])
def test_reasoning_effort_is_optional_and_explicit(settings, effort):
    sent = []
    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]})
    service = LLMService(GLMClient(settings, transport=httpx.MockTransport(respond)))
    service.invoke([HumanMessage(content='test')], reasoning_effort=effort)
    service.invoke([HumanMessage(content='test')])
    assert sent[0]['reasoning_effort'] == effort
    assert 'reasoning_effort' not in sent[1]


def test_invalid_reasoning_effort_is_rejected_offline(settings):
    client = GLMClient(settings, transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError('network forbidden'))))
    with pytest.raises(LLMError, match='invalid_request'):
        client.invoke([HumanMessage(content='test')], reasoning_effort='unsupported')


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 1}, {"prompt_tokens": -1, "completion_tokens": 2}, {"prompt_tokens": True, "completion_tokens": 2}])
def test_missing_or_invalid_usage_is_unknown(settings, usage):
    client = GLMClient(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": usage})))
    assert client.invoke([HumanMessage(content="test")]).usage is None


@pytest.mark.parametrize("status,category", [(401,"authentication"),(403,"authentication"),(400,"invalid_request"),(404,"not_found"),(429,"rate_limited"),(500,"provider_unavailable"),(302,"provider_unavailable")])
def test_http_errors_are_safe_and_not_retried(settings, status, category):
    seen = []
    def respond(request):
        seen.append(1)
        return httpx.Response(status, text="local-test-secret provider-private-error")
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(respond)).invoke([HumanMessage(content="private prompt")])
    assert exc.value.code == category and "secret" not in str(exc.value) and len(seen) == 1


@pytest.mark.parametrize("exception,category", [(httpx.ReadTimeout("local-test-secret"),"timeout"), (httpx.ConnectError("local-test-secret"),"connection")])
def test_transport_errors_safe(settings, exception, category):
    def fail(request): raise exception
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(fail)).invoke([HumanMessage(content="private prompt")])
    assert exc.value.code == category and "secret" not in str(exc.value)


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}])
def test_invalid_response(settings, body):
    with pytest.raises(LLMError, match="invalid_response"):
        GLMClient(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))).invoke([HumanMessage(content="test")])


def test_invalid_parameters_without_network(settings):
    def forbidden(request): raise AssertionError("network forbidden")
    client = GLMClient(settings, transport=httpx.MockTransport(forbidden))
    with pytest.raises(LLMError, match="invalid_request"): client.invoke([])
    with pytest.raises(LLMError, match="invalid_request"): client.invoke([HumanMessage(content="test")], max_tokens=0)


@pytest.mark.skipif(os.getenv("PAPERAGENT_LLM_LIVE_TEST") != "1", reason="Real GLM call requires explicit opt-in")
def test_opt_in_one_live_call():
    try:
        result = LLMService.from_settings().invoke([HumanMessage(content="Reply OK.")], max_tokens=16)
    except LLMError as error:
        print("Live diagnostic:", {"error_category": error.error_category,
              "http_status": error.http_status, "request_stage": error.request_stage,
              "retryable": error.retryable, "provider_request_id": error.provider_request_id})
        pytest.fail("Live model call failed: " + error.code, pytrace=False)
    assert result.text
    print("Live call succeeded; usage:", result.usage if result.usage is not None else "unknown")


def test_missing_key_is_a_safe_lazy_service_error(monkeypatch):
    def fail(): raise ConfigurationError("local-test-secret")
    monkeypatch.setattr("app.services.llm.load_llm_settings", fail)
    with pytest.raises(LLMError) as exc:
        LLMService.from_settings()
    assert exc.value.code == "configuration" and "local-test-secret" not in str(exc.value)


def test_budget_usage_roundtrips_real_file_checkpointer(tmp_path):
    from langgraph.checkpoint.base import empty_checkpoint
    from app.graph.checkpointer import sqlite_checkpointer
    path = tmp_path / "usage.sqlite"
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"budget_usage": {"prompt_tokens": 17, "completion_tokens": 5}}
    config = {"configurable": {"thread_id": "usage-test", "checkpoint_ns": ""}}
    with sqlite_checkpointer(path) as saver:
        saver.put(config, checkpoint, {"source": "input", "step": 0, "parents": {}}, {})
    with sqlite_checkpointer(path, read_only=True) as saver:
        restored = saver.get_tuple(config)
        assert restored is not None
        assert restored.checkpoint["channel_values"]["budget_usage"] == {"prompt_tokens": 17, "completion_tokens": 5}


def test_dns_error_classified_without_leaking_details(settings):
    import socket
    def fail(request):
        try:
            raise socket.gaierror("local-test-secret dns-private")
        except socket.gaierror as error:
            raise httpx.ConnectError("local-test-secret") from error
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(fail)).invoke([HumanMessage(content="private prompt")])
    assert exc.value.code == "dns" and exc.value.request_stage == "connect"
    assert not exc.value.retryable and "secret" not in str(exc.value)


def test_non_json_is_distinct_and_safe(settings):
    def respond(request): return httpx.Response(200, text="local-test-secret private prompt")
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(respond)).invoke([HumanMessage(content="private prompt")])
    assert exc.value.code == "invalid_json" and exc.value.http_status == 200
    assert exc.value.request_stage == "response_parse" and "secret" not in str(exc.value)


@pytest.mark.parametrize("status,kind,retryable", [(401,"authentication",False),(404,"not_found",False),(429,"rate_limited",False),(500,"provider_unavailable",False)])
def test_safe_http_diagnostics(settings, status, kind, retryable):
    request_id = "ac156e32-e185-48b7-91d4-405fd2403ad9"
    def respond(request): return httpx.Response(status, headers={"x-request-id": request_id}, text="local-test-secret private prompt")
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(respond)).invoke([HumanMessage(content="private prompt")])
    error = exc.value
    assert (error.error_code, error.error_category, error.http_status, error.request_stage, error.retryable, error.provider_request_id) == (kind, kind, status, "response", retryable, request_id)
    assert "secret" not in str(error) and "private" not in str(error)


def test_untrusted_request_id_is_discarded(settings):
    def respond(request): return httpx.Response(401, headers={"x-request-id": "local-test-secret"})
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(respond)).invoke([HumanMessage(content="test")])
    assert exc.value.provider_request_id is None


@pytest.mark.parametrize("base", ["https://api.z.ai/api/paas/v4", "https://api.z.ai/api/coding/paas/v4"])
@pytest.mark.parametrize("trailing", ["", "/"])
def test_exact_url_and_model_are_preserved(base, trailing):
    import json
    settings = LLMSettings(api_key="local-test-secret", model="GLM-5.3-Flash", base_url=base + trailing)
    def inspect(request):
        assert str(request.url) == base + "/chat/completions"
        assert json.loads(request.content)["model"] == "GLM-5.3-Flash"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]})
    result = GLMClient(settings, transport=httpx.MockTransport(inspect)).invoke([HumanMessage(content="test")])
    assert result.usage is None


def test_live_test_prints_safe_diagnostics_without_traceback(monkeypatch, capsys):
    class FakeService:
        def invoke(self, *args, **kwargs):
            raise LLMError("not_found", http_status=404, request_stage="response")
    monkeypatch.setattr(LLMService, "from_settings", lambda: FakeService())
    with pytest.raises(pytest.fail.Exception):
        test_opt_in_one_live_call()
    output = capsys.readouterr().out
    assert "not_found" in output and "404" in output
    assert "local-test-secret" not in output


@pytest.mark.parametrize("message,reason,expected", [
    ({"content": "final"}, "stop", "success"),
    ({"content": "final", "reasoning_content": "hidden-sensitive"}, "stop", "success"),
    ({"content": None, "reasoning_content": "hidden-sensitive"}, "stop", "invalid_response"),
    ({"content": ""}, "stop", "invalid_response"),
    ({"content": "   "}, "stop", "invalid_response"),
    ({"content": "partial", "reasoning_content": "hidden-sensitive"}, "length", "output_truncated"),
    ({"content": None, "reasoning_content": "hidden-sensitive"}, "length", "output_truncated"),
    ({"content": "text", "tool_calls": [{"type": "function"}]}, "tool_calls", "tool_calls_unsupported"),
    ({"content": None, "tool_calls": [{"type": "function"}]}, "tool_calls", "tool_calls_unsupported"),
    ({"content": "text", "tool_calls": [{"type": "function"}]}, "stop", "invalid_response"),
    ({"content": "text", "tool_calls": "malformed"}, "stop", "invalid_response"),
    ({"content": None, "tool_calls": []}, "tool_calls", "invalid_response"),
    ({"content": "text"}, None, "invalid_response"),
    ({"content": "text"}, "unknown-sensitive", "invalid_response"),
    ("invalid-message", "stop", "invalid_response"),
])
def test_finish_reason_and_content_contract(settings, message, reason, expected):
    if isinstance(message, dict):
        message = {"role": "assistant", **message}
    body = {"choices": [{"message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 32}}
    count = []
    def response(request):
        count.append(1)
        return httpx.Response(200, json=body)
    client = GLMClient(settings, transport=httpx.MockTransport(response))
    if expected == "success":
        result = client.invoke([HumanMessage(content="local-test")])
        assert result.text == "final" and result.usage == body["usage"]
        assert "hidden-sensitive" not in repr(result)
    else:
        with pytest.raises(LLMError) as exc:
            client.invoke([HumanMessage(content="local-test")])
        assert exc.value.code == expected and exc.value.usage == body["usage"]
        assert exc.value.http_status == 200 and not exc.value.retryable
        assert "hidden-sensitive" not in str(exc.value) and "partial" not in str(exc.value)
    assert len(count) == 1


@pytest.mark.parametrize("body", [
    {"choices": []}, {"choices": [None]}, {"choices": [{"message": None, "finish_reason": "stop"}]},
    {"choices": [{"message": {"role": "assistant", "content": "text"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": "17", "completion_tokens": 32}},
])
def test_response_shape_and_invalid_usage(settings, body):
    client = GLMClient(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    if body.get("choices") and isinstance(body["choices"][0], dict) and isinstance(body["choices"][0].get("message"), dict):
        assert client.invoke([HumanMessage(content="local-test")]).usage is None
    else:
        with pytest.raises(LLMError, match="invalid_response"):
            client.invoke([HumanMessage(content="local-test")])


def test_error_chain_does_not_retain_request_or_raw_response(settings):
    def network(request):
        raise httpx.ConnectError("local-test-secret", request=request)
    client = GLMClient(settings, transport=httpx.MockTransport(network))
    with pytest.raises(LLMError) as exc:
        client.invoke([HumanMessage(content="private prompt")])
    assert exc.value.__context__ is None and exc.value.__cause__ is None
    assert "local-test-secret" not in str(exc.value) and exc.value.outcome == "not_sent"
    def bad_json(request):
        return httpx.Response(200, text="local-test-secret provider-private-error")
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(bad_json)).invoke([HumanMessage(content="private prompt")])
    assert exc.value.__context__ is None and exc.value.__cause__ is None
    assert exc.value.code == "invalid_json" and exc.value.outcome == "received"
    assert "secret" not in str(exc.value) and "private" not in repr(exc.value)


def test_configuration_io_and_invalid_url_are_sanitized(monkeypatch, settings):
    def broken(*args, **kwargs): raise PermissionError("local-test-secret private-path")
    monkeypatch.setattr("app.core.llm_config.dotenv_values", broken)
    with pytest.raises(LLMError) as exc:
        LLMService.from_settings()
    assert exc.value.code == "configuration" and exc.value.__context__ is None and exc.value.__cause__ is None
    assert "secret" not in str(exc.value) and "private-path" not in str(exc.value)
    settings = LLMSettings(api_key="local-test-secret", model="test-model", base_url="https://example.invalid:bad")
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(lambda request: pytest.fail("request must not be sent"))).invoke([HumanMessage(content="private prompt")])
    assert exc.value.code == "configuration" and exc.value.outcome == "not_sent"
    assert exc.value.__context__ is None and exc.value.__cause__ is None


@pytest.mark.parametrize("url", ["http://example.com/v4", "https://example.com:bad/v4", "https://example.com:65536/v4", "https://example.com:0/v4", "https://bad_host/v4", "https://user:secret@example.com/v4", "https://example.com/v4?secret=x", "https://example.com/v4#x", "https://example.com/space here"])
def test_url_validation_rejects_unsafe_or_invalid_config(tmp_path, monkeypatch, url):
    for name in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.env"
    path.write_text(f"LLM_API_KEY=local-test-secret\nLLM_MODEL=test\nLLM_BASE_URL={url}\n", encoding="utf8")
    with pytest.raises(ConfigurationError) as exc: load_llm_settings(path)
    assert exc.value.__context__ is None and exc.value.__cause__ is None
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("status,outcome", [(400,"rejected"),(401,"rejected"),(429,"rejected"),(500,"unknown"),(503,"unknown")])
def test_http_outcomes_are_not_automatic_retry(settings, status, outcome):
    client = GLMClient(settings, transport=httpx.MockTransport(lambda request: httpx.Response(status, text="private")))
    with pytest.raises(LLMError) as exc: client.invoke([HumanMessage(content="test")])
    assert exc.value.outcome == outcome and not exc.value.retryable
    assert exc.value.call_id is not None and exc.value.__context__ is None


def test_timeout_outcome_is_unknown(settings):
    def timeout(request): raise httpx.ReadTimeout("private", request=request)
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(timeout)).invoke([HumanMessage(content="test")])
    assert exc.value.outcome == "unknown" and not exc.value.retryable
    assert exc.value.__context__ is None and exc.value.__cause__ is None


@pytest.mark.parametrize("exception,stage,outcome", [
    (httpx.ConnectTimeout, "connect", "not_sent"),
    (httpx.PoolTimeout, "pool", "not_sent"),
    (httpx.WriteTimeout, "write", "unknown"),
    (httpx.ReadTimeout, "read", "unknown"),
])
def test_timeout_phases_are_safe_and_never_retried(settings, exception, stage, outcome):
    calls = []
    def fail(request):
        calls.append(request.extensions["timeout"])
        raise exception("sensitive-secret", request=request)
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(fail)).invoke(
            [HumanMessage(content="private prompt")], read_timeout_seconds=120)
    assert exc.value.request_stage == stage and exc.value.outcome == outcome
    assert not exc.value.retryable and len(calls) == 1
    assert calls[0] == {"connect": 10.0, "write": 20.0, "read": 120, "pool": 10.0}
    assert "sensitive-secret" not in str(exc.value) and exc.value.__context__ is None


def test_safe_diagnostics_allowlist(tmp_path, monkeypatch):
    import json
    import app.infrastructure.llm_diagnostics as diagnostics
    target = tmp_path / "diagnostics.log"
    monkeypatch.setattr(diagnostics, "DIAGNOSTIC_PATH", target)
    run_id, call_id = str(uuid4()), str(uuid4())
    err = LLMError("timeout", request_stage="read", outcome="unknown", call_id=call_id,
                   provider_request_id="secret-prompt-and-key")
    diagnostics.record_call(run_id, call_id, err, 3.25)
    raw = target.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert "secret" not in raw and data["provider_request_id"] is None
    assert data["call_id"] == call_id and data["request_stage"] == "read" and data["outcome"] == "unknown"


def test_success_exposes_only_allowlisted_request_id(settings):
    request_id = str(uuid4())
    body = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
    client = GLMClient(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body, headers={"x-request-id": request_id})))
    result = client.invoke([HumanMessage(content="test")])
    assert result.http_status == 200 and result.provider_request_id == request_id and result.usage is None


def test_call_id_is_sent_as_provider_correlation_id(settings):
    import json
    call_id = str(uuid4())
    seen = []
    def respond(request):
        seen.append(json.loads(request.content)["request_id"])
        return httpx.Response(200, json={"request_id": call_id, "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]})
    result = GLMClient(settings, transport=httpx.MockTransport(respond)).invoke([HumanMessage(content="test")], call_id=call_id)
    assert seen == [call_id] and result.provider_request_id == call_id


def test_planner_timeout_config_is_bounded(tmp_path, monkeypatch):
    for key in ("LLM_API_KEY", "LLM_MODEL", "LLM_PLANNER_READ_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "settings.env"
    path.write_text("LLM_API_KEY=x\nLLM_MODEL=y\n", encoding="utf8")
    assert load_llm_settings(path).planner_read_timeout_seconds == 120
    monkeypatch.setenv("LLM_PLANNER_READ_TIMEOUT_SECONDS", "601")
    with pytest.raises(ConfigurationError): load_llm_settings(path)


@pytest.mark.parametrize("role", [None, "user", "system", 1])
def test_response_requires_assistant_role(settings, role):
    body={"choices":[{"finish_reason":"stop", "message":{"role":role,"content":"text"}}]}
    with pytest.raises(LLMError, match="invalid_response"):
        GLMClient(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200,json=body))).invoke([HumanMessage(content="test")])


def test_accounting_deduplicates_results_and_unknowns(settings):
    from app.infrastructure.glm_client import LLMResult
    first=LLMResult(text="private",usage={"prompt_tokens":5,"completion_tokens":3})
    second=LLMResult(text="private",usage=None)
    assert first.call_id != second.call_id
    totals=UsageAccumulator()
    assert totals.add_result(first) and not totals.add_result(first)
    assert totals.known_usage == {"prompt_tokens":5,"completion_tokens":3}
    assert totals.budget_usage == totals.known_usage
    assert totals.add_result(second) and not totals.add_result(second)
    assert totals.budget_usage is None
    assert totals.snapshot()["complete"] is False and len(totals.snapshot()["call_ids"]) == 2
    assert UsageAccumulator.from_state({"project_id":"test","current_stage":"planning","budget_usage":{"prompt_tokens":3,"completion_tokens":1}}).snapshot()["complete"] is False
    assert UsageAccumulator.from_state({"project_id":"test","current_stage":"planning"}).snapshot()["complete"] is False
    with pytest.raises(ValueError): totals.add({"prompt_tokens":1,"completion_tokens":1},call_id="invalid")


def test_accounting_checkpoint_close_reopen_and_dedup(tmp_path):
    from langgraph.checkpoint.base import empty_checkpoint
    from app.graph.checkpointer import sqlite_checkpointer
    from app.infrastructure.glm_client import LLMResult
    first=LLMResult(text="hidden",usage={"prompt_tokens":8,"completion_tokens":2})
    unknown=LLMResult(text="hidden",usage=None)
    acc=UsageAccumulator(); acc.add_result(first); acc.add_result(unknown)
    state={"project_id":str(uuid4()),"current_stage":"planning","budget_accounting":acc.snapshot()}
    cp=empty_checkpoint(); cp['channel_values']=state
    config={"configurable":{"thread_id":"accounting-checkpoint","checkpoint_ns":""}}
    path=tmp_path/'accounting.sqlite'
    with sqlite_checkpointer(path) as saver: saver.put(config,cp,{"source":"input","step":0,"parents":{}},{})
    with sqlite_checkpointer(path,read_only=True) as saver:
        restored=saver.get_tuple(config)
        assert restored is not None
        restored_state=restored.checkpoint['channel_values']
    rebuilt=UsageAccumulator.from_state(restored_state)
    assert rebuilt.snapshot()==acc.snapshot()
    assert not rebuilt.add_result(first) and not rebuilt.add_result(unknown)
    next_result=LLMResult(text="hidden",usage={"prompt_tokens":2,"completion_tokens":1})
    rebuilt.add_result(next_result)
    assert rebuilt.known_usage == {"prompt_tokens":10,"completion_tokens":3}
    assert rebuilt.budget_usage is None


def test_call_ids_unique_for_same_inputs(settings):
    body={"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"text"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}
    client=GLMClient(settings,transport=httpx.MockTransport(lambda request:httpx.Response(200,json=body)))
    a=client.invoke([HumanMessage(content="test")]);b=client.invoke([HumanMessage(content="test")])
    assert a.call_id != b.call_id
    totals=UsageAccumulator();totals.add_result(a);totals.add_result(b);assert totals.budget_usage=={"prompt_tokens":2,"completion_tokens":2}


def test_service_drops_exception_chain_even_inside_outer_handler(settings):
    import traceback
    def fail(request): raise httpx.ConnectError("synthetic-secret", request=request)
    service=LLMService(GLMClient(settings,transport=httpx.MockTransport(fail)))
    try:
        raise RuntimeError("outer-sensitive-error")
    except RuntimeError:
        with pytest.raises(LLMError) as exc:
            service.invoke([HumanMessage(content="private prompt")])
    error=exc.value
    assert error.__context__ is None and error.__cause__ is None
    filenames=[entry.filename for entry in traceback.extract_tb(error.__traceback__)]
    assert not any(Path(name).name in {'glm_client.py', 'llm.py'} for name in filenames)
    assert 'synthetic-secret' not in str(error) and 'outer-sensitive-error' not in str(error)


def test_config_exception_chain_even_inside_outer_handler(monkeypatch):
    def fail(): raise ConfigurationError("private-path")
    monkeypatch.setattr("app.services.llm.load_llm_settings", fail)
    try:
        raise RuntimeError("outer-sensitive-error")
    except RuntimeError:
        with pytest.raises(LLMError) as exc: LLMService.from_settings()
    assert exc.value.__context__ is None and exc.value.__cause__ is None


def test_parse_error_usage_and_call_identity_survive_safely(settings):
    body={"choices":[{"finish_reason":"length","message":{"role":"assistant","content":"partial"}}],"usage":{"prompt_tokens":3,"completion_tokens":4}}
    with pytest.raises(LLMError) as exc:
        GLMClient(settings, transport=httpx.MockTransport(lambda req:httpx.Response(200,json=body))).invoke([HumanMessage(content="test")])
    assert exc.value.code == "output_truncated" and exc.value.outcome == "received"
    assert exc.value.call_id and exc.value.usage == {"prompt_tokens":3,"completion_tokens":4}
    totals=UsageAccumulator();assert totals.add_result(exc.value) and not totals.add_result(exc.value)
    assert totals.budget_usage == {"prompt_tokens":3,"completion_tokens":4}


def test_legacy_accounting_missing_completeness_remains_unknown():
    previous=str(uuid4())
    state={"project_id":"test","current_stage":"planning", "budget_accounting":{
        "known_usage":{"prompt_tokens":3,"completion_tokens":1},"call_ids":[previous]}}
    restored=UsageAccumulator.from_state(state)
    assert restored.snapshot()["complete"] is False
    assert not restored.add({"prompt_tokens":3,"completion_tokens":1},call_id=previous)
    restored.add({"prompt_tokens":1,"completion_tokens":1},call_id=str(uuid4()))
    assert restored.budget_usage is None


def test_accounting_in_real_graph_state_survives_interrupt_and_restart(tmp_path):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from app.graph.checkpointer import sqlite_checkpointer
    from app.graph.state import PaperAgentState
    from app.infrastructure.glm_client import LLMResult
    known=LLMResult(text="private",usage={"prompt_tokens":4,"completion_tokens":2})
    missing=LLMResult(text="private",usage=None)
    def prepare(state):
        acc=UsageAccumulator();acc.add_result(known);acc.add_result(missing)
        return {"budget_accounting":acc.snapshot()}
    def approval(state):
        interrupt("test")
        return {}
    def build():
        graph=StateGraph(PaperAgentState)
        graph.add_node("prepare",prepare);graph.add_node("approval",approval)
        graph.add_edge(START,"prepare");graph.add_edge("prepare","approval");graph.add_edge("approval",END)
        return graph
    path=tmp_path/'graph-accounting.sqlite'
    config={"configurable":{"thread_id":"accounting-graph"}}
    with sqlite_checkpointer(path) as saver:
        graph=build().compile(checkpointer=saver)
        graph.invoke({"project_id":str(uuid4()),"current_stage":"planning"},config)
        before=graph.get_state(config)
        assert before.next == ("approval",)
    with sqlite_checkpointer(path) as saver:
        graph=build().compile(checkpointer=saver)
        after=graph.get_state(config)
        assert after.values['budget_accounting']==before.values['budget_accounting']
        acc=UsageAccumulator.from_state(after.values)
        assert not acc.add_result(known) and not acc.add_result(missing)
        assert acc.budget_usage is None and acc.known_usage=={"prompt_tokens":4,"completion_tokens":2}
        graph.invoke(Command(resume="continue"),config)
        assert graph.get_state(config).next == ()


def test_request_id_does_not_expose_part_of_api_key():
    request_id='ac156e32-e185-48b7-91d4-405fd2403ad9'
    settings=LLMSettings(api_key='synthetic-'+request_id,model='test')
    with pytest.raises(LLMError) as exc:
        GLMClient(settings,transport=httpx.MockTransport(lambda req:httpx.Response(401,headers={'x-request-id':request_id}))).invoke([HumanMessage(content='test')])
    assert exc.value.provider_request_id is None
