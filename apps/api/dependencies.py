"""FastAPI dependency injectors for persistence repositories and worker adapters."""

from __future__ import annotations

from typing import cast

from fastapi import Depends, Header, HTTPException, Request

from goalcoach.agents.grader_component import GraderComponent
from goalcoach.agents.planning_agent import PlanningWorker
from goalcoach.agents.teaching_agent import TeachingWorker
from goalcoach.infrastructure.persistence.repositories import (
    ContentRepository,
    SqliteLearnerRepository,
    UserAccount,
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


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix) :].strip()
    return None


async def get_current_user_optional(
    request: Request,
    authorization: str | None = Header(default=None),
    learner_repo: SqliteLearnerRepository = Depends(get_learner_repo),
) -> UserAccount | None:
    from goalcoach.infrastructure.auth import decode_access_token

    token = _extract_bearer_token(authorization)
    if not token:
        return None
    user_id = decode_access_token(token)
    if not user_id:
        return None
    user = await learner_repo.get_user_by_id(user_id)
    return user


async def get_current_user(
    user: UserAccount | None = Depends(get_current_user_optional),
) -> UserAccount:
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def resolve_learner_id(
    request: Request,
    learner_id: str,
    user: UserAccount | None = Depends(get_current_user_optional),
) -> str:
    from goalcoach.infrastructure.config import Settings

    settings = Settings()
    if user is None:
        if settings.auth_require_token:
            raise HTTPException(status_code=401, detail="Authentication required")
        return learner_id
    if learner_id != user.learner_id:
        raise HTTPException(status_code=403, detail="You can only access your own learner state")
    return learner_id
