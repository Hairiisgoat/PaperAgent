"""Read-only Planner reconciliation; never resumes a graph or calls a model."""
import argparse
import json
from uuid import UUID

from app.graph.checkpointer import sqlite_checkpointer
from app.graph.factory import build_graph
from app.infrastructure import database
from app.repositories.planner import PlannerRepository
from app.services.mock_start import checkpoint_path
from app.services.workflow_run import WorkflowRunService


def inspect_planner(project_id: UUID, run_id: UUID) -> dict:
    with database.SessionLocal() as session:
        run = WorkflowRunService(session).get(project_id, run_id)
        if run.graph_version != "planner_real_v1":
            raise ValueError("Not a Planner run")
        repo = PlannerRepository(session)
        gen = repo.for_run(project_id, run_id)
        if gen is None or gen.thread_id != run.thread_id:
            raise ValueError("Planner identity mismatch")
        tasks = repo.tasks(project_id, run.thread_id, gen.generation_id)
        result = {"project_id": str(project_id), "run_id": str(run_id), "thread_id": str(run.thread_id),
                  "generation_id": str(gen.generation_id), "call_id": str(gen.call_id),
                  "run_status": run.status, "generation_status": gen.status, "error_code": gen.error_code,
                  "usage_complete": bool(gen.usage_complete),
                  "prompt_tokens": gen.prompt_tokens if gen.usage_complete else None,
                  "completion_tokens": gen.completion_tokens if gen.usage_complete else None,
                  "task_count": len(tasks), "checkpoint": "unavailable", "decision": "manual_review"}
        try:
            path = checkpoint_path()
            if path.is_file():
                with sqlite_checkpointer(path, read_only=True) as saver:
                    snap = build_graph(run.graph_version).compile(checkpointer=saver).get_state(
                        {"configurable": {"thread_id": str(run.thread_id)}})
                result["checkpoint"] = "present" if (snap.config or {}).get("configurable", {}).get("checkpoint_id") else "missing"
                values = snap.values if isinstance(snap.values, dict) else {}
                metadata = snap.metadata if isinstance(snap.metadata, dict) else {}
                checkpoint_config = (snap.config or {}).get("configurable", {})
                result["checkpoint_next"] = list(snap.next)
                task_ids = [str(item.task_id) for item in tasks]
                batch_valid = (gen.status == "tasks_persisted" and gen.task_count == len(tasks)
                               and 3 <= len(tasks) <= 8 and [t.position for t in tasks] == list(range(1, len(tasks) + 1)))
                consistent = (checkpoint_config.get("thread_id") == str(run.thread_id)
                              and metadata.get("paperagent_project_id") == str(project_id)
                              and metadata.get("paperagent_run_id") == str(run_id)
                              and values.get("project_id") == str(project_id)
                              and values.get("planner_generation_id") == str(gen.generation_id)
                              and values.get("tasks") == task_ids)
                accounting = values.get("budget_accounting")
                if consistent and batch_valid and isinstance(accounting, dict):
                    known = ({"prompt_tokens": gen.prompt_tokens, "completion_tokens": gen.completion_tokens}
                             if gen.usage_complete else {"prompt_tokens": 0, "completion_tokens": 0})
                    consistent = (accounting.get("call_ids") == [str(gen.call_id)]
                                  and accounting.get("complete") is bool(gen.usage_complete)
                                  and accounting.get("known_usage") == known)
                else:
                    consistent = False
                interrupt = snap.interrupts[0].value if len(snap.interrupts) == 1 else None
                if (consistent and run.status == "waiting_human" and snap.next == ("task_approval",)
                        and isinstance(interrupt, dict) and interrupt.get("type") == "task_approval"
                        and interrupt.get("task_ids") == task_ids):
                    result["decision"] = "waiting_for_approval"
                elif batch_valid and snap.next == ("planner",) and not values.get("tasks"):
                    result["decision"] = "saved_tasks_manual_recovery_no_model"
        except Exception:
            # Do not expose filesystem paths, database errors, or checkpoint content.
            result["checkpoint"] = "unavailable"
        # In-flight and uncertain calls always require human reconciliation.
        if gen.status in {"invoking", "needs_review"}:
            result["decision"] = "manual_review"
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a Planner run without changing it")
    parser.add_argument("--project-id", required=True, type=UUID)
    parser.add_argument("--run-id", required=True, type=UUID)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_planner(args.project_id, args.run_id), ensure_ascii=False))
    except Exception:
        raise SystemExit("Planner reconciliation unavailable") from None


if __name__ == "__main__":
    main()
