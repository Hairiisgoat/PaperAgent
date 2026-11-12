"""Project policy and resource-scope tests with simulated resources only."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.services.project_scope import ProjectConflict, ProjectNotFound, ProjectScope, ResourceNotFound


class MockBase(DeclarativeBase):
    pass


class MockResource(MockBase):
    __tablename__ = "mock_only_resource"
    id: Mapped[object] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[object] = mapped_column(Uuid, nullable=False)


class UnscopedResource(MockBase):
    __tablename__ = "mock_unscoped"
    id: Mapped[object] = mapped_column(Uuid, primary_key=True)


@pytest.fixture
def setup_scope():
    session, repository = MagicMock(), MagicMock()
    repository.get.return_value = SimpleNamespace(lifecycle_status="active")
    return ProjectScope(session, repository), session, repository


@pytest.mark.parametrize("state,action,allowed", [
    ("active", "read", True), ("active", "edit", True), ("active", "archive", True),
    ("active", "restore", False), ("active", "delete", True),
    ("archived", "read", True), ("archived", "edit", False), ("archived", "archive", False),
    ("archived", "restore", True), ("archived", "delete", True),
    ("unexpected", "read", False),
])
def test_parent_policy_and_locking(setup_scope, state, action, allowed):
    scope, _, repository = setup_scope
    repository.get.return_value.lifecycle_status = state
    project_id = uuid4()
    if allowed:
        assert scope.require_project(project_id, action=action).lifecycle_status == state
    else:
        with pytest.raises(ProjectConflict):
            scope.require_project(project_id, action=action)
    repository.get.assert_called_once_with(project_id, exclude_status="deleted", for_update=action != "read")


@pytest.mark.parametrize("value", [None, SimpleNamespace(lifecycle_status="deleted"), SimpleNamespace(lifecycle_status="DELETED")])
def test_missing_and_deleted_project_are_not_found(setup_scope, value):
    scope, _, repository = setup_scope
    repository.get.return_value = value
    with pytest.raises(ProjectNotFound):
        scope.require_project(uuid4(), action="read")


def test_resource_query_requires_both_ids_and_rejects_cross_project(setup_scope):
    scope, session, repository = setup_scope
    project_a, project_b, resource_id = uuid4(), uuid4(), uuid4()
    resource = SimpleNamespace(id=resource_id, project_id=project_a)
    observed = []
    def lookup(stmt):
        params = stmt.compile().params
        assert set(params.values()) == {resource_id, current_project[0]}
        observed.append(str(stmt))
        return resource if params["id_1"] == resource.id and params["project_id_1"] == resource.project_id else None
    session.scalar.side_effect = lookup
    current_project = [project_a]
    assert scope.require_resource(MockResource, project_a, resource_id, access="read") is resource
    current_project[0] = project_b
    with pytest.raises(ResourceNotFound):
        scope.require_resource(MockResource, project_b, resource_id, access="read")
    assert all("mock_only_resource.id" in sql and "mock_only_resource.project_id" in sql for sql in observed)
    assert repository.get.call_count == 2


def test_resource_parent_guard_and_write_lock(setup_scope):
    scope, session, repository = setup_scope
    project_id, resource_id = uuid4(), uuid4()
    repository.get.return_value.lifecycle_status = "archived"
    session.scalar.return_value = object()
    assert scope.require_resource(MockResource, project_id, resource_id, access="read") is session.scalar.return_value
    session.scalar.reset_mock()
    with pytest.raises(ProjectConflict):
        scope.require_resource(MockResource, project_id, resource_id, access="write")
    session.scalar.assert_not_called()
    repository.get.return_value.lifecycle_status = "active"
    scope.require_resource(MockResource, project_id, resource_id, access="write")
    assert session.scalar.call_args.args[0]._for_update_arg is not None
    repository.get.assert_called_with(project_id, exclude_status="deleted", for_update=True)


def test_nonexistent_parent_or_unscoped_model_never_loads_resource(setup_scope):
    scope, session, repository = setup_scope
    repository.get.return_value = None
    with pytest.raises(ProjectNotFound):
        scope.require_resource(MockResource, uuid4(), uuid4(), access="read")
    session.scalar.assert_not_called()
    repository.get.return_value = SimpleNamespace(lifecycle_status="active")
    with pytest.raises(ValueError, match="project_id"):
        scope.require_resource(UnscopedResource, uuid4(), uuid4(), access="read")
    session.scalar.assert_not_called()


def test_deleted_parent_and_missing_resource_are_not_visible(setup_scope):
    scope, session, repository = setup_scope
    repository.get.return_value = SimpleNamespace(lifecycle_status="deleted")
    with pytest.raises(ProjectNotFound):
        scope.require_resource(MockResource, uuid4(), uuid4(), access="read")
    session.scalar.assert_not_called()
    repository.get.return_value = SimpleNamespace(lifecycle_status="active")
    session.scalar.return_value = None
    with pytest.raises(ResourceNotFound):
        scope.require_resource(MockResource, uuid4(), uuid4(), access="read")


def test_resource_error_maps_to_non_disclosing_404():
    from app.main import resource_not_found_handler
    response = resource_not_found_handler(None, ResourceNotFound("internal details"))
    assert response.status_code == 404
    assert response.body == b'{"detail":"Resource not found"}'
