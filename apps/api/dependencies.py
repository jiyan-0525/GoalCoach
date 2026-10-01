"""FastAPI dependency injectors for persistence repositories and worker adapters."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from goalcoach.agents.grader_component import GraderComponent
from goalcoach.agents.planning_agent import PlanningWorker
from goalcoach.agents.teaching_agent import TeachingWorker
from goalcoach.infrastructure.persistence.repositories import (
    ContentRepository,
    SqliteLearnerRepository,
)

_default_content_repo: ContentRepository | None = None


def get_planning_worker() -> PlanningWorker:
    """Create the configured Planning Agent application adapter."""
    return PlanningWorker()


def get_teaching_worker() -> TeachingWorker:
    """Create the configured Teaching Agent application adapter."""
    return TeachingWorker()


def get_grader_component() -> GraderComponent:
    """Create the isolated grading component."""
    return GraderComponent()


def get_learner_repo(request: Request) -> SqliteLearnerRepository:
    """Resolve learner repository from application state or create standard instance."""
    if hasattr(request.app.state, "learner_repository"):
        return cast(SqliteLearnerRepository, request.app.state.learner_repository)

    from goalcoach.infrastructure.config import Settings
    from goalcoach.infrastructure.persistence.database import (
        create_learner_schema,
        create_session_factory,
    )

    settings = Settings()
    factory = create_session_factory(settings.database_url)
    create_learner_schema(factory)
    return SqliteLearnerRepository(factory)


def get_content_repo(request: Request) -> ContentRepository:
    """Resolve content repository from application state or create singleton."""
    global _default_content_repo
    if hasattr(request.app.state, "content_repository"):
        return cast(ContentRepository, request.app.state.content_repository)

    if _default_content_repo is None:
        from goalcoach.infrastructure.config import Settings
        from goalcoach.infrastructure.persistence.database import create_session_factory

        settings = Settings()
        factory = create_session_factory(settings.content_database_url)
        _default_content_repo = ContentRepository(factory)
    return _default_content_repo
