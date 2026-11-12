"""Persist allowlisted call diagnostics only; never retain request or response bodies."""
import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args
from uuid import UUID

from app.infrastructure.glm_client import ErrorKind, LLMError, LLMResult

logger = logging.getLogger(__name__)
DIAGNOSTIC_PATH = Path(__file__).resolve().parents[3] / "logs" / "llm-diagnostics.log"
_STAGES = frozenset({"validation", "pool", "connect", "write", "read", "request", "response", "response_parse"})
_OUTCOMES = frozenset({"not_sent", "rejected", "unknown", "received"})


def _uuid(value: object) -> str | None:
    try:
        canonical = str(UUID(value)) if isinstance(value, str) else None
        return canonical if canonical == value else None
    except ValueError:
        return None


def record_call(run_id: str, call_id: str, result: LLMResult | LLMError, elapsed: float) -> None:
    """Best effort diagnostics; an I/O failure must not repeat or mask a call."""
    is_error = isinstance(result, LLMError)
    code = result.code if is_error and result.code in get_args(ErrorKind) else ("unknown" if is_error else "ok")
    stage = result.request_stage if is_error and result.request_stage in _STAGES else ("unknown" if is_error else "response")
    outcome = result.outcome if is_error and result.outcome in _OUTCOMES else ("unknown" if is_error else "received")
    status = result.http_status if type(result.http_status) is int and 100 <= result.http_status <= 599 else None
    duration = round(elapsed, 3) if isinstance(elapsed, (int, float)) and math.isfinite(elapsed) and elapsed >= 0 else None
    entry = {"timestamp": datetime.now(UTC).isoformat(), "run_id": _uuid(run_id), "call_id": _uuid(call_id),
             "code": code, "request_stage": stage, "outcome": outcome, "http_status": status,
             "provider_request_id": _uuid(result.provider_request_id), "elapsed_seconds": duration}
    try:
        DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DIAGNOSTIC_PATH.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
    except OSError:
        logger.warning("Model diagnostics could not be saved")
