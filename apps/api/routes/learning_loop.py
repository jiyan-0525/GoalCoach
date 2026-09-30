"""apps/api/routes/learning_loop.py
FastAPI router providing the unified closed-loop event endpoint: POST /api/v1/events.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from apps.api.dependencies import (
    get_current_user_optional,
    get_content_repo,
    get_grader_component,
    get_learner_repo,
    get_planning_worker,
    get_teaching_worker,
)
from goalcoach.infrastructure.config import Settings
from goalcoach.agents.grader_component import GraderComponent
from goalcoach.agents.planning_agent import PlanningWorker
from goalcoach.agents.teaching_agent import TeachingWorker
from goalcoach.application.agent_history import SessionLifecycleError
from goalcoach.application.orchestrator import (
    DeterministicOrchestrator,
    OrchestratorResponse,
)
from goalcoach.application.progress_service import ProgressService
from goalcoach.domain.enums import EventType
from goalcoach.domain.events import (
    AnswerSubmittedPayload,
    GoalCreatedPayload,
    HelpRequestedPayload,
    ReplanRequestedPayload,
    SessionEndedPayload,
    SessionStartedPayload,
)
from goalcoach.infrastructure.persistence.content_service import ContentService
from goalcoach.infrastructure.persistence.repositories import (
    ContentRepository,
    SqliteLearnerRepository,
    StaleLearnerStateError,
    UserAccount,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["learning-loop"])


class EventRequest(BaseModel):
    """Inbound request payload for the unified event dispatcher."""

    event_type: EventType
    learner_id: UUID | str | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict)


_EVENT_PAYLOAD_MODELS: dict[EventType, type[BaseModel]] = {
    EventType.GOAL_CREATED: GoalCreatedPayload,
    EventType.SESSION_STARTED: SessionStartedPayload,
    EventType.SESSION_ENDED: SessionEndedPayload,
    EventType.HELP_REQUESTED: HelpRequestedPayload,
    EventType.ANSWER_SUBMITTED: AnswerSubmittedPayload,
    EventType.REPLAN_REQUESTED: ReplanRequestedPayload,
}


def validate_event_payload(event_type: EventType, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an event payload before it reaches the orchestration boundary."""
    model = _EVENT_PAYLOAD_MODELS[event_type]
    try:
        return model.model_validate(payload).model_dump()
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False),
        ) from exc


def validate_curriculum_references(
    event_type: EventType,
    payload: dict[str, Any],
    content_repo: ContentRepository,
) -> None:
    """Reject unknown or inconsistent curriculum identifiers at the API boundary."""
    concept_id = payload.get("concept_id")
    if concept_id and content_repo.get_concept(str(concept_id)) is None:
        raise HTTPException(status_code=422, detail=f"Unknown curriculum concept: {concept_id}")

    if event_type == EventType.ANSWER_SUBMITTED:
        exercise_id = str(payload["exercise_id"])
        content_service = ContentService(content_repo)
        exercise = content_service.get_exercise(exercise_id)
        if exercise is None:
            raise HTTPException(
                status_code=422, detail=f"Unknown curriculum exercise: {exercise_id}"
            )
        if exercise.concept_id != concept_id:
            raise HTTPException(
                status_code=422,
                detail="The exercise does not belong to the submitted concept.",
            )


@router.post("/events", response_model=OrchestratorResponse)
async def dispatch_learning_event(
    req: EventRequest,
    current_user: UserAccount | None = Depends(get_current_user_optional),
    learner_repo: SqliteLearnerRepository = Depends(get_learner_repo),
    content_repo: ContentRepository = Depends(get_content_repo),
    planning_worker: PlanningWorker = Depends(get_planning_worker),
    teaching_worker: TeachingWorker = Depends(get_teaching_worker),
    grader_worker: GraderComponent = Depends(get_grader_component),
) -> OrchestratorResponse:
    """Dispatches inbound learner events through the Deterministic Orchestrator."""
    content_service = ContentService(content_repo)
    progress_service = ProgressService(learner_repo=learner_repo)
    orchestrator = DeterministicOrchestrator(
        learner_repo=learner_repo,
        content_service=content_service,
        progress_service=progress_service,
        planning_worker=planning_worker,
        teaching_worker=teaching_worker,
        grader_worker=grader_worker,
    )

    settings = Settings()
    if current_user is not None:
        resolved_learner_id: UUID | str = current_user.learner_id
        if req.learner_id is not None and str(req.learner_id) != current_user.learner_id:
            raise HTTPException(status_code=403, detail="You can only submit events for your account")
    else:
        if settings.auth_require_token:
            raise HTTPException(status_code=401, detail="Authentication required")
        resolved_learner_id = req.learner_id or "learner_001"

    payload = validate_event_payload(req.event_type, req.payload)
    validate_curriculum_references(req.event_type, payload, content_repo)

    try:
        return await orchestrator.handle_event(
            event_type=req.event_type,
            payload=payload,
            learner_id=resolved_learner_id,
        )
    except SessionLifecycleError as exc:
        raise HTTPException(
            status_code=409, detail=str(exc), headers={"X-GoalCoach-Error": "SESSION_INVALID"}
        ) from exc
    except StaleLearnerStateError as exc:
        raise HTTPException(
            status_code=409,
            detail="Your learning state changed in another request. Reload and try again.",
            headers={"X-GoalCoach-Error": "STATE_CONFLICT"},
        ) from exc
