"""File-backed, offline LangGraph interrupt and resume tests."""

import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from langgraph.types import Command

from app.agents.mock_common import mock_id
from app.graph.approval_graph import build_approval_graph
from app.graph.checkpointer import sqlite_checkpointer


PROJECT = "19f8b1b2-7dcc-44bf-92bb-dbc394c2173d"
WRONG_ID = "11111111-1111-4111-8111-111111111111"
CONFIG = {"configurable": {"thread_id": "test-stable-thread"}}
INITIAL = {"project_id": PROJECT, "current_stage": "planning"}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network access forbidden")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


def test_snapshot_and_reopen_full_path_with_partial_task_approval(tmp_path: Path):
    path = tmp_path / "checkpoints.sqlite"
    with sqlite_checkpointer(path) as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        output = graph.invoke(INITIAL, CONFIG)
        snapshot = graph.get_state(CONFIG)
        tasks = snapshot.values["tasks"]
        assert snapshot.next == ("task_approval",)
        assert snapshot.values["current_stage"] == "planning"
        assert "approved_task_ids" not in snapshot.values
        assert output["__interrupt__"][0].value == {"type": "task_approval", "task_ids": tasks}
        assert snapshot.interrupts[0].value == output["__interrupt__"][0].value
    with pytest.raises(sqlite3.ProgrammingError):
        saver.conn.execute("SELECT 1")

    with sqlite_checkpointer(path) as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        assert graph.get_state(CONFIG).values["tasks"] == tasks
        events = list(graph.stream(Command(resume={"decision": "approve", "approved_ids": [tasks[0]]}), CONFIG, stream_mode="updates"))
        names = [key for event in events for key in event]
        assert "planner" not in names and "research" in names and "writing" not in names
        snapshot = graph.get_state(CONFIG)
        assert snapshot.next == ("source_approval",)
        assert snapshot.values["approved_task_ids"] == [tasks[0]]
        sources = snapshot.values["source_ids"]
        assert sources == [mock_id(PROJECT, f"mock-source:{tasks[0]}")]
        assert "approved_source_ids" not in snapshot.values
        assert snapshot.interrupts[0].value == {"type": "source_approval", "source_ids": sources}

    with sqlite_checkpointer(path) as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        events = list(graph.stream(Command(resume={"decision": "approve", "approved_ids": sources}), CONFIG, stream_mode="updates"))
        names = [key for event in events for key in event]
        assert "planner" not in names and "research" not in names
        assert names == ["source_approval", "writing", "review"]
        snapshot = graph.get_state(CONFIG)
        assert snapshot.next == () and not snapshot.interrupts
        assert snapshot.values["tasks"] == tasks
        assert snapshot.values["approved_task_ids"] == [tasks[0]]
        assert snapshot.values["source_ids"] == sources
        assert snapshot.values["approved_source_ids"] == sources
        assert snapshot.values["current_stage"] == "review"
        assert snapshot.values["review_id"] and "user_decision" not in snapshot.values


def test_approve_all_and_reject_sources(tmp_path: Path):
    with sqlite_checkpointer(tmp_path / "all.sqlite") as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        graph.invoke(INITIAL, CONFIG)
        tasks = graph.get_state(CONFIG).values["tasks"]
        graph.invoke(Command(resume={"decision": "approve", "approved_ids": tasks}), CONFIG)
        snapshot = graph.get_state(CONFIG)
        assert snapshot.next == ("source_approval",)
        assert snapshot.values["approved_task_ids"] == tasks
        assert len(snapshot.values["source_ids"]) == len(tasks)
        graph.invoke(Command(resume={"decision": "reject"}), CONFIG)
        snapshot = graph.get_state(CONFIG)
        assert snapshot.next == () and snapshot.values["approved_source_ids"] == []
        assert snapshot.values["current_stage"] == "research"
        assert "current_chapter_id" not in snapshot.values


def test_partial_source_approval_completes(tmp_path: Path):
    with sqlite_checkpointer(tmp_path / "partial-source.sqlite") as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        graph.invoke(INITIAL, CONFIG)
        tasks = graph.get_state(CONFIG).values["tasks"]
        graph.invoke(Command(resume={"decision": "approve", "approved_ids": tasks}), CONFIG)
        sources = graph.get_state(CONFIG).values["source_ids"]
        assert len(sources) == 2
        graph.invoke(Command(resume={"decision": "approve", "approved_ids": [sources[1]]}), CONFIG)
        result = graph.get_state(CONFIG)
        assert result.next == () and result.values["current_stage"] == "review"
        assert result.values["approved_source_ids"] == [sources[1]]


def test_reject_tasks_stops_before_research_even_with_prefilled_approval(tmp_path: Path):
    with sqlite_checkpointer(tmp_path / "reject.sqlite") as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        graph.invoke({**INITIAL, "approved_task_ids": [WRONG_ID], "approved_source_ids": [WRONG_ID]}, CONFIG)
        assert "approved_task_ids" not in graph.get_state(CONFIG).values
        graph.invoke(Command(resume={"decision": "reject"}), CONFIG)
        snapshot = graph.get_state(CONFIG)
        assert snapshot.next == () and snapshot.values["approved_task_ids"] == []
        assert "source_ids" not in snapshot.values


@pytest.mark.parametrize("stage", ["task", "source"])
@pytest.mark.parametrize("response", [
    {"decision": "approve", "approved_ids": [WRONG_ID]},
    {"decision": "approve", "approved_ids": []},
    {"decision": "accept", "approved_ids": [WRONG_ID]},
    {"decision": "reject", "approved_ids": [WRONG_ID]},
    {"decision": "approve", "approved_ids": "not-a-list"},
    {"decision": "approve", "approved_ids": [1]},
    {"decision": "approve", "approved_ids": [WRONG_ID], "extra": "secret"},
    "invalid",
])
def test_invalid_response_reinterrupts_then_accepts_valid_response(tmp_path: Path, stage: str, response: object):
    with sqlite_checkpointer(tmp_path / "invalid.sqlite") as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        graph.invoke(INITIAL, CONFIG)
        tasks = graph.get_state(CONFIG).values["tasks"]
        if stage == "source":
            graph.invoke(Command(resume={"decision": "approve", "approved_ids": tasks[:1]}), CONFIG)
        before = graph.get_state(CONFIG)
        ids = before.values["tasks" if stage == "task" else "source_ids"]
        result = graph.invoke(Command(resume=response), CONFIG)
        after = graph.get_state(CONFIG)
        assert after.next == before.next
        assert after.interrupts[0].value["error"] == "invalid_approval"
        assert result["__interrupt__"][0].value["error"] == "invalid_approval"
        assert after.values == before.values
        assert "current_chapter_id" not in after.values
        graph.invoke(Command(resume={"decision": "approve", "approved_ids": ids[:1]}), CONFIG)
        assert graph.get_state(CONFIG).next == (("source_approval",) if stage == "task" else ())


@pytest.mark.parametrize("stage", ["task", "source"])
def test_duplicate_approved_ids_are_rejected(tmp_path: Path, stage: str):
    with sqlite_checkpointer(tmp_path / "duplicates.sqlite") as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        graph.invoke(INITIAL, CONFIG)
        tasks = graph.get_state(CONFIG).values["tasks"]
        if stage == "source":
            graph.invoke(Command(resume={"decision": "approve", "approved_ids": tasks}), CONFIG)
        before = graph.get_state(CONFIG)
        value = before.values["tasks" if stage == "task" else "source_ids"][0]
        graph.invoke(Command(resume={"decision": "approve", "approved_ids": [value, value]}), CONFIG)
        after = graph.get_state(CONFIG)
        assert after.next == before.next and after.values == before.values
        assert after.interrupts[0].value["error"] == "invalid_approval"


def test_wrong_thread_does_not_restore_another_thread(tmp_path: Path):
    with sqlite_checkpointer(tmp_path / "threads.sqlite") as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        graph.invoke(INITIAL, CONFIG)
        before = graph.get_state(CONFIG)
        wrong = {"configurable": {"thread_id": "different-thread"}}
        assert graph.get_state(wrong).values == {}
        with pytest.raises(KeyError):
            graph.invoke(Command(resume={"decision": "reject"}), wrong)
        after = graph.get_state(CONFIG)
        assert after.next == before.next and after.values == before.values
        assert after.interrupts == before.interrupts


def test_none_resume_does_not_approve_and_valid_retry_works(tmp_path: Path):
    with sqlite_checkpointer(tmp_path / "none.sqlite") as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        graph.invoke(INITIAL, CONFIG)
        before = graph.get_state(CONFIG)
        # Installed LangGraph treats None as no resume input and raises before our node.
        with pytest.raises(UnboundLocalError):
            graph.invoke(Command(resume=None), CONFIG)
        after = graph.get_state(CONFIG)
        assert after.next == before.next and after.values == before.values
        graph.invoke(Command(resume={"decision": "approve", "approved_ids": before.values["tasks"][:1]}), CONFIG)
        assert graph.get_state(CONFIG).next == ("source_approval",)


@pytest.mark.parametrize("path", [":memory:", "file::memory:?cache=shared"])
def test_no_in_memory_checkpointer(path: str):
    with pytest.raises(ValueError, match="file-backed"):
        with sqlite_checkpointer(Path(path)):
            pass


def test_connection_closes_when_context_raises(tmp_path: Path):
    with pytest.raises(RuntimeError, match="test failure"):
        with sqlite_checkpointer(tmp_path / "close.sqlite") as saver:
            connection = saver.conn
            raise RuntimeError("test failure")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


CHILD = r'''
import json, os, socket, sys
from pathlib import Path
from langgraph.types import Command
from app.graph.approval_graph import build_approval_graph
from app.graph.checkpointer import sqlite_checkpointer
def blocked(*args, **kwargs): raise RuntimeError("network disabled")
socket.socket.connect=blocked
socket.socket.connect_ex=blocked
socket.create_connection=blocked
path, action, thread = sys.argv[1:]
config = {"configurable": {"thread_id": thread}}
with sqlite_checkpointer(Path(path)) as saver:
    graph = build_approval_graph().compile(checkpointer=saver)
    if action == "start":
        graph.invoke({"project_id": "19f8b1b2-7dcc-44bf-92bb-dbc394c2173d", "current_stage": "planning"}, config)
    else:
        state = graph.get_state(config)
        field = "tasks" if action == "tasks" else "source_ids"
        graph.invoke(Command(resume={"decision":"approve", "approved_ids":state.values[field][:1]}), config)
    state = graph.get_state(config)
    print(json.dumps({"pid":os.getpid(), "values":state.values, "next":state.next, "interrupts":[i.value for i in state.interrupts]}))
'''


def test_cross_process_restart_and_resume(tmp_path: Path):
    path = tmp_path / "cross-process.sqlite"
    env = {**os.environ, "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}
    results = []
    for action in ("start", "tasks", "sources"):
        process = subprocess.run([sys.executable, "-c", CHILD, str(path), action, "stable-thread"], check=True, capture_output=True, text=True, env=env, timeout=30)
        results.append(json.loads(process.stdout))
    start, research, final = results
    assert len({item["pid"] for item in results}) == 3
    assert start["next"] == ["task_approval"] and start["interrupts"][0]["type"] == "task_approval"
    assert research["next"] == ["source_approval"] and research["interrupts"][0]["type"] == "source_approval"
    assert research["values"]["tasks"] == start["values"]["tasks"]
    assert final["next"] == [] and final["interrupts"] == []
    assert final["values"]["tasks"] == start["values"]["tasks"]
    assert final["values"]["approved_task_ids"] == start["values"]["tasks"][:1]
    assert final["values"]["approved_source_ids"] == research["values"]["source_ids"][:1]
    assert final["values"]["current_stage"] == "review" and "user_decision" not in final["values"]
