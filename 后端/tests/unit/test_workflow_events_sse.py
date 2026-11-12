"""Real HTTP SSE against a subprocess with isolated temporary databases."""
import os
import socket
import subprocess
import sys
import time
from uuid import UUID

import httpx
import pytest


CHILD = r"""
import os, sys
from uuid import UUID
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.infrastructure import database
from app.models import PaperProject
from app.models.base import Base
from app.main import app
from app.services.workflow_events import broker, EventService
from app.schemas.workflow_event import ProgressPayload
from app.schemas.workflow_approval import ApprovalSubmission
from app.services.workflow_resume import queue_approval
from app.services.workflow_run import WorkflowRunService
from app.graph.approval_graph import build_approval_graph
from app.graph.checkpointer import sqlite_checkpointer
import uvicorn
db, checkpoint, port = sys.argv[1:]
os.environ["LANGGRAPH_SQLITE_PATH"] = checkpoint
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
engine = create_engine("sqlite:///" + db, connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
with database.SessionLocal() as session:
    if session.get(PaperProject, UUID("f030f412-5374-4608-bdef-de4245810e95")) is None:
        session.add(PaperProject(id=UUID("f030f412-5374-4608-bdef-de4245810e95"), title="isolated", topic="test", citation_style="APA"))
    session.commit()
@app.get("/__test/subscriber-count")
def subscriber_count():
    with broker._lock:
        return {"count":sum(map(len,broker._subscribers.values()))}
@app.post("/__test/publish/{project}/{run}")
def publish(project: UUID, run: UUID):
    with database.SessionLocal() as session:
        service = EventService(session)
        item = service.prepare(project, run, "progress", stage="planning", agent_name="Planner", status="waiting_human",
            payload=ProgressPayload(stage="planning", agent_name="Planner", status="waiting_human", message="Waiting for approval"))
        service.commit_and_notify(item)
    return {"sequence":item.sequence}
@app.post("/__test/queue/{project}/{run}")
def queue(project: UUID, run: UUID, body: ApprovalSubmission):
    with database.SessionLocal() as session:
        response, evt, _ = queue_approval(session, project, run, body)
        EventService(session).commit_and_notify(evt)
        return response
@app.post("/__test/initial/{project}")
def initial(project: UUID):
    with database.SessionLocal() as session:
        run = WorkflowRunService(session).create(project)
        run.current_stage = "planning"
        evt = EventService(session).prepare(project, run.run_id, "progress", stage="planning", agent_name=None,
            status="queued", payload=ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued"))
        EventService(session).commit_and_notify(evt)
        return {"run_id": str(run.run_id), "thread_id": str(run.thread_id)}
@app.post("/__test/claim/{project}/{run_id}")
def claim(project: UUID, run_id: UUID):
    with database.SessionLocal() as session:
        run = WorkflowRunService(session).transition(project, run_id, "running", current_stage="planning")
        evt = EventService(session).prepare(project, run_id, "progress", stage="planning", agent_name="Planner", status="running",
            payload=ProgressPayload(stage="planning", agent_name="Planner", status="running", message="Workflow running"))
        EventService(session).commit_and_notify(evt)
        with sqlite_checkpointer(__import__('pathlib').Path(checkpoint)) as saver:
            graph = build_approval_graph().compile(checkpointer=saver)
            graph.invoke(
                {"project_id": str(project), "current_stage": "planning"},
                {"configurable": {"thread_id": str(run.thread_id)}})
            state = graph.get_state({"configurable": {"thread_id": str(run.thread_id)}})
            return {"checkpoint_id": state.config["configurable"]["checkpoint_id"]}
uvicorn.run(app, host="127.0.0.1", port=int(port), log_level="error")
"""


def read_stream_and_disconnect(port, url, cursor=None, target="approval_required"):
    with socket.create_connection(("127.0.0.1", port), timeout=8) as conn:
        cursor_header = f"Last-Event-ID: {cursor}\r\n" if cursor else ""
        request = f"GET {url} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{cursor_header}Connection: close\r\n\r\n"
        conn.sendall(request.encode("ascii"))
        data = b""
        while f"event: {target}\n".encode() not in data:
            chunk = conn.recv(65536)
            if not chunk:
                raise AssertionError("SSE stream ended")
            data += chunk
        conn.shutdown(socket.SHUT_RDWR)
        return data


def test_real_tcp_stream_replay_and_disconnect(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(tmp_path / "business.sqlite"), str(tmp_path / "checkpoints.sqlite"), str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=8) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                if time.monotonic() >= deadline:
                    raise AssertionError("test server failed to start")
                time.sleep(0.1)
            project = "f030f412-5374-4608-bdef-de4245810e95"
            response = client.post(f"/api/projects/{project}/runs")
            assert response.status_code == 202
            run = response.json()["run_id"]
            UUID(run)
            url = f"/api/projects/{project}/workflow-runs/{run}/events"
            data = read_stream_and_disconnect(port, url)
            assert b"HTTP/1.1 200" in data and b"text/event-stream" in data and b"no-cache" in data
            assert data.index(b"id: 1\n") < data.index(b"id: 2\n") < data.index(b"id: 3\n")
            data = read_stream_and_disconnect(port, url, "1")
            assert b"id: 1\n" not in data and data.index(b"id: 2\n") < data.index(b"id: 3\n")
            data = read_stream_and_disconnect(port, url, "3", "heartbeat")
            assert b"event: heartbeat\n" in data and b"\nid: " not in data and b'"sequence":null' in data
            with socket.create_connection(("127.0.0.1", port), timeout=8) as conn:
                conn.sendall(f"GET {url} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nLast-Event-ID: 3\r\n\r\n".encode("ascii"))
                received = conn.recv(8192)
                assert b"HTTP/1.1 200" in received
                assert client.post(f"/__test/publish/{project}/{run}").json()["sequence"] == 4
                while b"id: 4\n" not in received:
                    received += conn.recv(8192)
                assert b"event: progress\n" in received
                conn.shutdown(socket.SHUT_RDWR)
            deadline = time.monotonic() + 8
            while client.get("/__test/subscriber-count").json()["count"] != 0:
                if time.monotonic() >= deadline:
                    raise AssertionError("subscriber was not released")
                time.sleep(0.1)
            detail = client.get(f"/api/projects/{project}/workflow-runs/{run}")
            assert detail.status_code == 200 and detail.json()["status"] == "waiting_human"
            approval_url = f"/api/projects/{project}/workflow-runs/{run}/approval"
            approval = client.get(approval_url).json()
            resumed = client.post(approval_url, json={"decision": "approve", "approved_ids": approval["item_ids"][:1]})
            assert resumed.status_code == 202
            next_run = resumed.json()["run_id"]
            next_url = f"/api/projects/{project}/workflow-runs/{next_run}/events"
            replay = read_stream_and_disconnect(port, next_url)
            assert replay.index(b"id: 1\n") < replay.index(b"id: 2\n") < replay.index(b"id: 3\n")
            assert b'"approval_type":"source_approval"' in replay
            replay = read_stream_and_disconnect(port, next_url, "1")
            assert b"id: 1\n" not in replay and replay.index(b"id: 2\n") < replay.index(b"id: 3\n")
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_sse_replay_after_process_restart_recovers_queued_intent(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    args = [sys.executable, "-c", CHILD, str(tmp_path / "business.sqlite"), str(tmp_path / "checkpoints.sqlite"), str(port)]
    env = {**os.environ, "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}
    def launch():
        return subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def wait_ready(client):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if client.get("/health").status_code == 200: return
            except httpx.ConnectError: pass
            time.sleep(0.1)
        raise AssertionError("server did not start")
    process = launch()
    project = "f030f412-5374-4608-bdef-de4245810e95"
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=8) as client:
            wait_ready(client)
            first = client.post(f"/api/projects/{project}/runs").json()
            approval_url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
            deadline = time.monotonic() + 8
            while True:
                approval = client.get(approval_url)
                if approval.status_code == 200: break
                assert time.monotonic() < deadline
                time.sleep(0.05)
            queued = client.post(f"/__test/queue/{project}/{first['run_id']}", json={
                "decision": "approve", "approved_ids": approval.json()["item_ids"][:1]}).json()
            process.terminate(); process.wait(timeout=8)
            process = launch()
            wait_ready(client)
            stream = f"/api/projects/{project}/workflow-runs/{queued['run_id']}/events"
            replay = read_stream_and_disconnect(port, stream)
            assert replay.index(b"id: 1\n") < replay.index(b"id: 2\n") < replay.index(b"id: 3\n")
            assert b'"approval_type":"source_approval"' in replay
            replay = read_stream_and_disconnect(port, stream, "1")
            assert b"id: 1\n" not in replay and replay.index(b"id: 2\n") < replay.index(b"id: 3\n")
    finally:
        process.terminate()
        try: process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


@pytest.mark.parametrize("already_running", [False, True])
def test_initial_intent_survives_real_server_process_restart(tmp_path, already_running):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    args = [sys.executable, "-c", CHILD, str(tmp_path / "business.sqlite"), str(tmp_path / "checkpoints.sqlite"), str(port)]
    env = {**os.environ, "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}
    def launch():
        return subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def ready(client):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if client.get("/health").status_code == 200: return
            except httpx.ConnectError: pass
            time.sleep(0.1)
        raise AssertionError("server did not start")
    process = launch()
    project = "f030f412-5374-4608-bdef-de4245810e95"
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=8) as client:
            ready(client)
            initial = client.post(f"/__test/initial/{project}").json()
            if already_running:
                claim = client.post(f"/__test/claim/{project}/{initial['run_id']}")
                assert claim.status_code == 200
                checkpoint_id = claim.json()["checkpoint_id"]
            process.terminate(); process.wait(timeout=8)
            process = launch(); ready(client)
            url = f"/api/projects/{project}/workflow-runs/{initial['run_id']}/events"
            if already_running:
                detail = client.get(f"/api/projects/{project}/workflow-runs/{initial['run_id']}").json()
                assert detail["status"] == "running" and detail["thread_id"] == initial["thread_id"]
                from app.graph.approval_graph import build_approval_graph
                from app.graph.checkpointer import sqlite_checkpointer
                with sqlite_checkpointer(tmp_path / "checkpoints.sqlite", read_only=True) as saver:
                    snap = build_approval_graph().compile(checkpointer=saver).get_state({"configurable": {"thread_id": initial["thread_id"]}})
                    assert snap.next == ("task_approval",) and snap.config["configurable"]["checkpoint_id"] == checkpoint_id
                return
            replay = read_stream_and_disconnect(port, url)
            assert replay.index(b"id: 1\n") < replay.index(b"id: 2\n") < replay.index(b"id: 3\n")
            assert b'"approval_type":"task_approval"' in replay
            replay = read_stream_and_disconnect(port, url, "1")
            assert b"id: 1\n" not in replay and b"id: 3\n" in replay
            detail = client.get(f"/api/projects/{project}/workflow-runs/{initial['run_id']}").json()
            assert detail["thread_id"] == initial["thread_id"] and detail["status"] == "waiting_human"
    finally:
        process.terminate()
        try: process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
