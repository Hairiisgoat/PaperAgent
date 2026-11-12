"""Research node: consume only durable approval and commit a complete batch."""
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from app.graph.state import PaperAgentState
from app.infrastructure import database
from app.infrastructure.crossref import search_crossref
from app.services.research_quality import build_query, rank_candidates
from app.models.research_source import ResearchSource, ResearchSourceTask
from app.models.workflow_approval_decision import WorkflowApprovalDecision
from app.repositories.planner import PlannerRepository
from app.services.workflow_run import WorkflowRunService


def research_node(state: PaperAgentState, config: RunnableConfig) -> dict:
    project_id = UUID(state["project_id"])
    run_id = UUID(config["metadata"]["paperagent_run_id"])
    thread_id = UUID(config["configurable"]["thread_id"])
    generation_id = UUID(state["planner_generation_id"])
    approved = state.get("approved_task_ids", [])
    if not approved or len(approved) != len(set(approved)):
        raise RuntimeError("Research approval unavailable")
    with database.SessionLocal() as session:
        runs = WorkflowRunService(session)
        runs.scope.require_project(project_id, action="edit")
        run = runs.repository.get(project_id, run_id)
        decision = session.scalar(select(WorkflowApprovalDecision).where(
            WorkflowApprovalDecision.project_id == project_id, WorkflowApprovalDecision.run_id == run_id))
        repo = PlannerRepository(session)
        generation = repo.generation(project_id, thread_id, generation_id)
        tasks = repo.tasks(project_id, thread_id, generation_id)
        by_id = {str(task.task_id): task for task in tasks}
        if (not run or run.thread_id != thread_id or run.graph_version != "planner_research_v2"
                or run.status != "running" or not decision or decision.thread_id != thread_id
                or decision.approval_type != "task_approval" or decision.decision != "approve"
                or set(decision.approved_ids) != set(approved) or not set(approved).issubset(by_id)
                or not generation or generation.status != "tasks_persisted"):
            raise RuntimeError("Research approval inconsistent")
        prior = list(session.scalars(select(ResearchSource).where(
            ResearchSource.project_id == project_id, ResearchSource.thread_id == thread_id,
            ResearchSource.run_id == run_id, ResearchSource.generation_id == generation_id).order_by(ResearchSource.doi)))
        if prior:
            links = list(session.execute(select(ResearchSourceTask.source_id, ResearchSourceTask.task_id).join(
                ResearchSource, ResearchSource.source_id == ResearchSourceTask.source_id).where(
                ResearchSource.project_id == project_id, ResearchSource.run_id == run_id)))
            if not links or not {str(task_id) for _, task_id in links}.issubset(set(approved)) or {source.source_id for source in prior} != {source_id for source_id, _ in links}:
                raise RuntimeError("Research batch inconsistent")
            return {"current_stage": "research", "source_ids": [str(source.source_id) for source in prior]}
        queries = [(UUID(task_id), by_id[task_id].title, by_id[task_id].description, by_id[task_id].expected_output) for task_id in approved]
        topic = generation.input_snapshot.get('topic', '') if isinstance(generation.input_snapshot, dict) else ''
        if not isinstance(topic, str):
            topic = ''

    found = {}
    for task_id, title, description, expected in queries:
        query = build_query(title, description, expected, topic=topic)
        candidates = search_crossref(query, limit=20)
        # Stable ranking preserves provider order for ties. Keep low-evidence
        # candidates as a fallback; a lexical heuristic must not silently
        # discard papers that use a different language or terminology.
        for item in rank_candidates(candidates, title, description, expected, topic=topic):
            if item.doi not in found:
                found[item.doi] = (item, [])
            found[item.doi][1].append((task_id, query))
    if not found:
        raise RuntimeError("No scholarly results")

    with database.SessionLocal() as session:
        runs = WorkflowRunService(session)
        runs.scope.require_project(project_id, action="edit")
        run = runs.repository.get(project_id, run_id, for_update=True)
        if not run or run.thread_id != thread_id or run.status != "running" or run.graph_version != "planner_research_v2":
            raise RuntimeError("Research attempt inconsistent")
        if session.scalar(select(ResearchSource.source_id).where(ResearchSource.project_id == project_id, ResearchSource.run_id == run_id).limit(1)):
            raise RuntimeError("Research batch already exists")
        sources = []
        for doi, (item, links) in sorted(found.items()):
            source = ResearchSource(source_id=uuid4(), project_id=project_id, thread_id=thread_id,
                run_id=run_id, generation_id=generation_id, doi=doi, title=item.title, authors=list(item.authors),
                year=item.year, journal=item.journal, url=item.url, abstract=item.abstract)
            session.add(source)
            session.flush()
            session.add_all([ResearchSourceTask(source_id=source.source_id, task_id=task_id, search_query=query) for task_id, query in links])
            sources.append(source)
        session.commit()
        return {"current_stage": "research", "source_ids": [str(source.source_id) for source in sources]}
