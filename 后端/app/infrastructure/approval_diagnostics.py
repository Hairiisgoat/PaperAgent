"""Safe approval diagnostics; never persist request bodies or exceptions."""
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)
DIAGNOSTIC_PATH = Path(__file__).resolve().parents[3] / "logs" / "approval-diagnostics.log"


def record_missing_writing_storage(project_id: object, run_id: object) -> None:
    try:
        project, run = str(UUID(str(project_id))), str(UUID(str(run_id)))
    except (ValueError, TypeError, AttributeError):
        return
    item = {"timestamp": datetime.now(UTC).isoformat(), "project_id": project, "run_id": run,
            "code": "writing_storage_missing", "stage": "precommit_schema_check", "http_status": 503}
    try:
        DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DIAGNOSTIC_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=True, separators=(",", ":")) + "\n")
    except OSError:
        logger.warning("Approval diagnostics could not be saved")
