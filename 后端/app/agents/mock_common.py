"""Shared safeguards for the offline mock agents."""

from uuid import UUID, uuid5


def mock_id(project_id: str, name: str) -> str:
    """Return a stable project-scoped mock UUID; no records are created."""
    try:
        namespace = UUID(project_id)
        if str(namespace) != project_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid project ID") from None
    return str(uuid5(namespace, name))


def approved_subset(approved: object, available: object) -> bool:
    """Reject missing, empty, malformed or unavailable approval IDs."""
    return (
        isinstance(approved, list)
        and bool(approved)
        and isinstance(available, list)
        and all(isinstance(item, str) and bool(item) and item in available for item in approved)
    )


def canonical_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False
