"""Deterministic Orchestrator for GoalCoach event routing and state lifecycle management."""

from __future__ import annotations

import logging
from datetime import datetime
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import Field

from goalcoach.infrastructure.logging.context import bind_context, reset_context

logger = logging.getLogger("goalcoach.application.orchestrator")

from goalcoach.application.agent_history import (
    SessionLifecycleError,
    close_active_session,
    record_grading_outcome,
    record_session_started,
    record_teaching_turn,
    require_pending_teaching_turn,
)
from goalcoach.application.progress_reducer import compute_progress_summary
from goalcoach.application.progress_service import ProgressService
from goalcoach.domain.enums import EventType, PlanItemKind, PlanStatus, StudyEntrySource
from goalcoach.domain.events import InboundEvent
from goalcoach.domain.models import (
    CURRENT_ROADMAP_SCHEMA_VERSION,
    ActiveLearningSession,
    DailyPlan,
    DomainBaseModel,
    Exercise,
    GradingResult,
    LearnerState,
    LearningGoal,
    PlanUpdate,
    ProgressSummary,
    TeachingAction,
    utc_now,
)
from goalcoach.infrastructure.persistence.content_service import ContentService
from goalcoach.infrastructure.persistence.learner_repository import SqliteLearnerRepository


def derive_next_action(state: LearnerState) -> str:
    """Derive the client instruction exclusively from authoritative state."""
    if state.goal is None:
        return "set_goal"
    if state.active_plan is None:
        return "plan"
    if state.active_plan.status == PlanStatus.EXHAUSTED or all(
        item.completed for item in state.active_plan.items
    ):
        return "complete"
    return "plan" if state.needs_replanning else "teach"


class PlanningWorkerPort(Protocol):
    """Application-facing contract for the Planning Agent."""

    async def create_plan(
        self,
        state: LearnerState,
        content_service: ContentService,
        *,
        allow_roadmap_changes: bool = False,
    ) -> PlanUpdate: ...


class TeachingWorkerPort(Protocol):
    """Application-facing contract for the Teaching Agent."""

    async def teach_concept(
        self,
        concept_id: str,
        state: LearnerState,
        content_service: ContentService,
        failed_attempts: int = 0,
        learner_query: str | None = None,
        excluded_exercise_id: str | None = None,
        target_exercise_id: str | None = None,
    ) -> TeachingAction: ...


class GraderPort(Protocol):
    """Application-facing contract for the isolated grader component."""

    async def grade(self, exercise: Exercise, answer: str) -> GradingResult: ...


class OrchestratorResponse(DomainBaseModel):
    """Unified response envelope returned by the Deterministic Orchestrator."""

    status: str = "success"
    event_type: EventType
    learner_id: str | UUID
    teaching_action: TeachingAction | None = None
    plan_update: PlanUpdate | None = None
    daily_plan: DailyPlan | None = None
    grading_result: GradingResult | None = None
    replanned: bool = False
    state: LearnerState | None = None
    progress_summary: ProgressSummary | None = None
    next_action: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeterministicOrchestrator:
    """Routes inbound learner events strictly without multi-agent chaining."""

    def __init__(
        self,
        learner_repo: SqliteLearnerRepository,
        content_service: ContentService,
        progress_service: ProgressService,
        planning_worker: PlanningWorkerPort,
        teaching_worker: TeachingWorkerPort,
        grader_worker: GraderPort,
    ) -> None:
        self.learner_repo = learner_repo
        self.content_service = content_service
        self.progress_service = progress_service
        self.planning_worker = planning_worker
        self.teaching_worker = teaching_worker
        self.grader_worker = grader_worker

    def _progress_summary(self, state: LearnerState) -> ProgressSummary:
        """Return the one backend-owned progress projection for every event response."""
        return compute_progress_summary(
            state,
            self.content_service.list_all_concepts(),
        )

    async def _persist_state(self, state: LearnerState) -> None:
        """Persist one authoritative mutation after refreshing its goal identity."""
        state.goal_fingerprint = state.goal.fingerprint if state.goal else ""
        state.updated_at = utc_now()
        await self.learner_repo.save(state)

    async def handle_event(
        self,
        event_type: EventType | str,
        payload: dict[str, Any],
        learner_id: str | UUID,
    ) -> OrchestratorResponse:
        """Handle an inbound event according to the deterministic priority lifecycle."""
        if isinstance(event_type, str):
            event_type = EventType(event_type)

        concept_id = payload.get("concept_id")
        tokens = bind_context(
            learner_id=str(learner_id),
            concept_id=str(concept_id) if concept_id else None,
        )
        route_start = perf_counter()

        inbound = InboundEvent(
            event_type=event_type,
            learner_id=learner_id,
            payload=payload,
            timestamp=utc_now(),
        )

        try:
            logger.info(
                "Event %s received for learner %s",
                event_type.value,
                str(learner_id),
                extra={
                    "extra": {
                        "event_type": event_type.value,
                        "payload_keys": sorted(payload.keys()),
                    }
                },
            )
            response = await self.dispatch(inbound)
            routing_latency_us = int((perf_counter() - route_start) * 1_000_000)
            logger.info(
                "Event %s resolved -> next_action: %s (replanned=%s, %dus)",
                response.event_type.value,
                response.next_action,
                response.replanned,
                routing_latency_us,
                extra={
                    "extra": {
                        "event_type": response.event_type.value,
                        "next_action": response.next_action,
                        "replanned": response.replanned,
                        "routing_latency_us": routing_latency_us,
                    }
                },
            )
            return response
        finally:
            reset_context(tokens)

    async def dispatch(self, event: InboundEvent) -> OrchestratorResponse:
        """Dispatch event according to its type."""
        learner_id = str(event.learner_id)
        state = await self.learner_repo.get(learner_id)
        if state is None:
            state = LearnerState(
                learner_id=learner_id,
                display_name=f"Learner {learner_id}",
            )
            await self.learner_repo.save(state)
        elif (
            state.goal is not None
            and state.goal_fingerprint
            and state.goal_fingerprint != state.goal.fingerprint
        ):
            state.reset_learning_state()

        match event.event_type:
            case EventType.GOAL_CREATED:
                return await self._handle_goal_created(state, event.payload)
            case EventType.SESSION_STARTED:
                return await self._handle_session_started(state, event.payload)
            case EventType.SESSION_ENDED:
                return await self._handle_session_ended(state, event.payload)
            case EventType.HELP_REQUESTED:
                return await self._handle_help_requested(state, event.payload)
            case EventType.ANSWER_SUBMITTED:
                return await self._handle_answer_submitted(state, event.payload)
            case EventType.REPLAN_REQUESTED:
                return await self._handle_replan_requested(state, event.payload)
            case _:
                raise ValueError(f"Unknown event type: {event.event_type}")

    async def _handle_goal_created(
        self,
        state: LearnerState,
        payload: dict[str, Any],
    ) -> OrchestratorResponse:
        """Process GOAL_CREATED: configure goal and call Planning Agent to generate initial plan."""
        current_goal = state.goal or LearningGoal(title="HSK 1 Goal")
        title = payload.get("title", current_goal.title)
        target_hsk_level = payload.get("target_hsk_level", current_goal.target_hsk_level)
        daily_minutes = payload.get("daily_available_minutes", current_goal.daily_available_minutes)
        timezone = payload.get("timezone", current_goal.timezone)
        goal_changed = state.goal is None or (
            title.strip().casefold() != current_goal.title.strip().casefold()
            or target_hsk_level != current_goal.target_hsk_level
        )
        if goal_changed:
            state.reset_learning_state()
        state.goal = LearningGoal(
            id=current_goal.id,
            title=title,
            target_hsk_level=target_hsk_level,
            daily_available_minutes=daily_minutes,
            timezone=timezone,
        )
        state.needs_replanning = False
        state.goal_fingerprint = state.goal.fingerprint
        plan_update = await self.planning_worker.create_plan(
            state=state,
            content_service=self.content_service,
            allow_roadmap_changes=goal_changed or not state.roadmap_concept_ids,
        )

        # Convert PlanUpdate to DailyPlan
        daily_plan = DailyPlan(
            learner_id=state.learner_id,
            date=utc_now(),
            status=PlanStatus.ACTIVE,
            items=plan_update.ordered_items,
            rationale=plan_update.adaptation_rationale,
            generated_at=utc_now(),
        )
        state.active_plan = daily_plan
        state.roadmap_concept_ids = plan_update.roadmap_concept_ids
        state.roadmap_adjustments = plan_update.roadmap_adjustments
        state.roadmap_coverage_rationale = plan_update.roadmap_coverage_rationale
        state.roadmap_schema_version = CURRENT_ROADMAP_SCHEMA_VERSION
        await self._persist_state(state)

        return OrchestratorResponse(
            event_type=EventType.GOAL_CREATED,
            learner_id=state.learner_id,
            plan_update=plan_update,
            daily_plan=daily_plan,
            state=state,
            progress_summary=self._progress_summary(state),
            next_action=derive_next_action(state),
        )

    async def _handle_session_started(
        self,
        state: LearnerState,
        payload: dict[str, Any],
    ) -> OrchestratorResponse:
        """Process SESSION_STARTED: verify active plan and call Teaching Agent for active item."""
        if state.goal is None:
            raise SessionLifecycleError("Create a learning goal before starting a session")
        entry_source = StudyEntrySource(payload.get("entry_source", StudyEntrySource.PLANNED))
        requested_concept_id = payload.get("concept_id")
        requested_plan_item_id = payload.get("plan_item_id")
        plan = state.active_plan
        learner_timezone = ZoneInfo(state.goal.timezone)
        learner_today = datetime.now(learner_timezone).date()
        if (
            entry_source == StudyEntrySource.PLANNED
            and plan is not None
            and plan.status == PlanStatus.EXHAUSTED
            and plan.date.astimezone(learner_timezone).date() == learner_today
        ):
            return OrchestratorResponse(
                event_type=EventType.SESSION_STARTED,
                learner_id=state.learner_id,
                daily_plan=plan,
                state=state,
                progress_summary=self._progress_summary(state),
                next_action=derive_next_action(state),
            )
        planned_minutes = payload.get("preferred_duration_minutes") or (
            state.goal.daily_available_minutes
        )
        if entry_source == StudyEntrySource.PLANNED:
            if (
                state.active_session is not None
                and not state.active_session.has_progress_eligible_activity
                and state.active_session.current_entry_source != StudyEntrySource.PLANNED
            ):
                close_active_session(state)
            record_session_started(
                state,
                planned_minutes=planned_minutes,
                focus=payload.get("session_focus"),
            )
        elif state.active_session is None:
            state.active_session = ActiveLearningSession(
                planned_minutes=planned_minutes,
                focus=payload.get("session_focus"),
                has_progress_eligible_activity=False,
            )
        plan = state.active_plan
        if entry_source == StudyEntrySource.PLANNED and state.active_session is not None:
            planned_minutes = min(planned_minutes, state.active_session.planned_minutes)
        plan_needs_regen = entry_source == StudyEntrySource.PLANNED and (
            plan is None
            or plan.status == PlanStatus.INVALID
            or plan.date.astimezone(learner_timezone).date() != learner_today
            or state.needs_replanning
            or sum(item.estimated_minutes for item in plan.items) > planned_minutes
        )

        replanned = False
        if plan_needs_regen:
            plan_update: PlanUpdate
            plan_update = await self.planning_worker.create_plan(
                state=state,
                content_service=self.content_service,
                allow_roadmap_changes=False,
            )

            plan = DailyPlan(
                learner_id=state.learner_id,
                date=utc_now(),
                status=PlanStatus.ACTIVE,
                items=plan_update.ordered_items,
                rationale=plan_update.adaptation_rationale,
                generated_at=utc_now(),
            )
            state.active_plan = plan
            state.roadmap_concept_ids = plan_update.roadmap_concept_ids
            state.roadmap_adjustments = plan_update.roadmap_adjustments
            state.roadmap_coverage_rationale = plan_update.roadmap_coverage_rationale
            state.roadmap_schema_version = CURRENT_ROADMAP_SCHEMA_VERSION
            state.needs_replanning = False
            replanned = True
            await self._persist_state(state)
            return OrchestratorResponse(
                event_type=EventType.SESSION_STARTED,
                learner_id=state.learner_id,
                plan_update=plan_update,
                daily_plan=plan,
                replanned=True,
                state=state,
                progress_summary=self._progress_summary(state),
                next_action=derive_next_action(state),
            )

        active_item = None
        progress_eligible = False
        if entry_source == StudyEntrySource.PLANNED:
            first_uncompleted = next((item for item in plan.items if not item.completed), None)
            if requested_plan_item_id:
                active_item = next(
                    (item for item in plan.items if str(item.id) == str(requested_plan_item_id)),
                    None,
                )
                if active_item is None:
                    logger.info(
                        "Requested plan item %s not found in plan; falling back to requested concept or first uncompleted",
                        requested_plan_item_id,
                    )
                    if requested_concept_id:
                        active_item = next(
                            (
                                item
                                for item in plan.items
                                if item.concept_id == str(requested_concept_id)
                                and not item.completed
                            ),
                            None,
                        )
                    if active_item is None:
                        active_item = first_uncompleted
            else:
                active_item = first_uncompleted
            if active_item is None:
                raise SessionLifecycleError("Today's planned learning is complete")
            progress_eligible = not active_item.completed
            concept_id = active_item.concept_id
        else:
            if not requested_concept_id:
                raise SessionLifecycleError("Select a concept for self-directed review")
            if self.content_service.get_concept(str(requested_concept_id)) is None:
                raise SessionLifecycleError("The selected roadmap concept does not exist")
            concept_id = str(requested_concept_id)

        # Derive failure history for active item
        failed_attempts = sum(
            err.occurrences for err in state.error_profile if err.concept_id == concept_id
        )
        if (
            active_item is not None
            and active_item.kind == PlanItemKind.REMEDIAL
            and failed_attempts == 0
        ):
            failed_attempts = 1

        # Invoke Teaching Agent
        teaching_action = await self.teaching_worker.teach_concept(
            concept_id=concept_id,
            state=state,
            content_service=self.content_service,
            failed_attempts=failed_attempts,
        )
        teaching_action.metadata.update(
            {
                "entry_source": entry_source.value,
                "progress_eligible": progress_eligible,
                "progress_notice": (
                    "This planned lesson counts toward progress."
                    if progress_eligible
                    else "Self-directed review does not change progress or study time."
                ),
            }
        )
        if state.active_session is not None:
            state.active_session.current_turn_progress_eligible = progress_eligible
            state.active_session.current_entry_source = entry_source
            state.active_session.has_progress_eligible_activity |= progress_eligible
            state.active_session.pending_concept_id = teaching_action.concept_id
            state.active_session.pending_exercise_id = (
                str((teaching_action.exercise_payload or {}).get("exercise_id") or "") or None
            )

        record_teaching_turn(state, teaching_action)
        await self._persist_state(state)

        return OrchestratorResponse(
            event_type=EventType.SESSION_STARTED,
            learner_id=state.learner_id,
            daily_plan=plan,
            teaching_action=teaching_action,
            replanned=replanned,
            state=state,
            progress_summary=self._progress_summary(state),
            next_action=derive_next_action(state),
        )

    async def _handle_session_ended(
        self,
        state: LearnerState,
        payload: dict[str, Any],
    ) -> OrchestratorResponse:
        """Close the active session and persist its bounded summary."""
        summary = close_active_session(
            state,
            additional_active_seconds=payload.get("additional_active_seconds", 0),
        )
        await self._persist_state(state)
        return OrchestratorResponse(
            event_type=EventType.SESSION_ENDED,
            learner_id=state.learner_id,
            daily_plan=state.active_plan,
            state=state,
            progress_summary=self._progress_summary(state),
            next_action=derive_next_action(state),
            metadata={"sessionSummary": summary.model_dump(mode="json", by_alias=True)},
        )

    async def _handle_help_requested(
        self,
        state: LearnerState,
        payload: dict[str, Any],
    ) -> OrchestratorResponse:
        """Process HELP_REQUESTED: invoke Teaching Agent with failed attempt history to switch strategy."""
        concept_id = payload.get("concept_id") or "hsk1_c01"
        learner_query = payload.get("learner_query")
        current_exercise_id = payload.get("current_exercise_id")
        active_session = state.active_session
        progress_eligible = bool(active_session and active_session.current_turn_progress_eligible)
        entry_source = (
            active_session.current_entry_source.value
            if active_session is not None
            else StudyEntrySource.ROADMAP.value
        )

        teaching_action = await self.teaching_worker.teach_concept(
            concept_id=concept_id,
            state=state,
            content_service=self.content_service,
            failed_attempts=1 if not learner_query else 0,
            learner_query=learner_query,
            target_exercise_id=current_exercise_id if learner_query else None,
            excluded_exercise_id=current_exercise_id if not learner_query else None,
        )
        teaching_action.metadata.update(
            {
                "entry_source": entry_source,
                "progress_eligible": progress_eligible,
                "progress_notice": (
                    "This planned lesson counts toward progress."
                    if progress_eligible
                    else "Self-directed review does not change progress or study time."
                ),
            }
        )
        if learner_query:
            teaching_action.metadata["learner_query"] = learner_query

        if state.active_session is not None:
            state.active_session.pending_concept_id = teaching_action.concept_id
            state.active_session.pending_exercise_id = (
                str((teaching_action.exercise_payload or {}).get("exercise_id") or "") or None
            )
        if progress_eligible:
            record_teaching_turn(state, teaching_action, learner_query=learner_query)
        await self._persist_state(state)

        return OrchestratorResponse(
            event_type=EventType.HELP_REQUESTED,
            learner_id=state.learner_id,
            teaching_action=teaching_action,
            state=state,
            progress_summary=self._progress_summary(state),
            next_action=derive_next_action(state),
        )

    async def _handle_answer_submitted(
        self,
        state: LearnerState,
        payload: dict[str, Any],
    ) -> OrchestratorResponse:
        """Grade one pending exercise and persist its deterministic state transition."""
        exercise_id = payload["exercise_id"]
        answer = payload["answer"]

        # 1. Fetch exercise definition from content service
        content_ex = self.content_service.get_exercise(exercise_id)
        if content_ex:
            concept_id = payload.get("concept_id") or content_ex.concept_id
            ref_answers = list(content_ex.accepted_answers) if content_ex.accepted_answers else []
            ans_val = (
                content_ex.answer.get("value")
                if isinstance(content_ex.answer, dict)
                else str(content_ex.answer or "")
            )
            if ans_val and ans_val not in ref_answers:
                ref_answers.append(ans_val)

            concept = self.content_service.get_concept(content_ex.concept_id)
            exercise_level = (
                concept.hsk_level if concept else (state.goal.target_hsk_level if state.goal else 1)
            )
            exercise = Exercise(
                id=content_ex.exercise_id,
                concept_id=content_ex.concept_id,
                exercise_type=getattr(content_ex, "exercise_type", "meaning_mcq"),
                prompt=content_ex.prompt,
                target_instruction=content_ex.instruction or "",
                reference_answers=ref_answers,
                options=content_ex.options,
                hsk_level=exercise_level,
                metadata=content_ex.metadata_json or {},
            )
        else:
            raise ValueError(
                f"Unknown exercise_id {exercise_id!r}; answers can only be graded "
                "against canonical curriculum exercises"
            )

        pending_turn = require_pending_teaching_turn(
            state,
            concept_id=concept_id,
            exercise_id=str(exercise_id),
        )

        # 2. Grade answer via Grader Component
        grading_result = await self.grader_worker.grade(exercise=exercise, answer=answer)

        # 3. Only the first completion of the current Planning Agent item is
        # authoritative learning evidence. Roadmap and repeat-review attempts
        # receive feedback but cannot mutate progress, mastery, or study time.
        occurred_at = utc_now()
        time_spent_seconds = payload.get("time_spent_seconds", 0)
        progress_eligible = pending_turn.progress_eligible
        learning_event = None
        if progress_eligible:
            learning_event = self.progress_service.build_learning_event(
                state,
                grading_result,
                concept_id,
                at=occurred_at,
                time_spent_seconds=time_spent_seconds,
            )
            state = self.progress_service.apply_grading_result(
                state=state,
                result=grading_result,
                concept_id=concept_id,
                at=occurred_at,
                time_spent_seconds=time_spent_seconds,
            )
        if progress_eligible:
            record_grading_outcome(
                state,
                concept_id=concept_id,
                exercise_id=str(exercise_id),
                result=grading_result,
                time_spent_seconds=time_spent_seconds,
            )
        elif state.active_session is not None:
            state.active_session.pending_concept_id = None
            state.active_session.pending_exercise_id = None

        # If answer passed, mark item completed in active plan
        if progress_eligible and grading_result.passed_gates and state.active_plan:
            for item in state.active_plan.items:
                if item.concept_id == concept_id and not item.completed:
                    item.completed = True
                    break
            if all(item.completed for item in state.active_plan.items):
                state.active_plan.status = PlanStatus.EXHAUSTED

        # 4. Replanning gate: ProgressService marks the state only. Planning runs
        # on the next SESSION_STARTED or explicit REPLAN_REQUESTED event, keeping
        # the one-reasoning-worker-per-event invariant intact.
        replanned = False
        plan_update: PlanUpdate | None = None

        if learning_event is not None:
            await self.learner_repo.save_with_event(state, learning_event)
        else:
            await self._persist_state(state)

        return OrchestratorResponse(
            event_type=EventType.ANSWER_SUBMITTED,
            learner_id=state.learner_id,
            grading_result=grading_result,
            plan_update=plan_update,
            daily_plan=state.active_plan,
            replanned=replanned,
            state=state,
            progress_summary=self._progress_summary(state),
            next_action=derive_next_action(state),
            metadata={
                "progressEligible": progress_eligible,
                "progressNotice": (
                    "Progress and study time were updated from this planned lesson."
                    if progress_eligible
                    else "Self-directed review feedback was not added to progress or study time."
                ),
            },
        )

    async def _handle_replan_requested(
        self,
        state: LearnerState,
        payload: dict[str, Any],
    ) -> OrchestratorResponse:
        """Regenerate the daily plan through one explicit Planning Agent event."""
        if state.goal is None:
            raise SessionLifecycleError("Create a learning goal before requesting a new plan")
        plan_update = await self.planning_worker.create_plan(
            state=state,
            content_service=self.content_service,
            allow_roadmap_changes=False,
        )
        plan = DailyPlan(
            learner_id=state.learner_id,
            date=utc_now(),
            status=PlanStatus.ACTIVE,
            items=plan_update.ordered_items,
            rationale=plan_update.adaptation_rationale,
            generated_at=utc_now(),
        )
        state.active_plan = plan
        state.roadmap_concept_ids = plan_update.roadmap_concept_ids
        state.roadmap_adjustments = plan_update.roadmap_adjustments
        state.roadmap_coverage_rationale = plan_update.roadmap_coverage_rationale
        state.roadmap_schema_version = CURRENT_ROADMAP_SCHEMA_VERSION
        state.needs_replanning = False
        remediated_ids = {
            item.concept_id for item in plan.items if item.kind == PlanItemKind.REMEDIAL
        }
        for concept_id in remediated_ids:
            state.remediation_counters.pop(concept_id, None)
        await self._persist_state(state)
        return OrchestratorResponse(
            event_type=EventType.REPLAN_REQUESTED,
            learner_id=state.learner_id,
            plan_update=plan_update,
            daily_plan=plan,
            replanned=True,
            state=state,
            progress_summary=self._progress_summary(state),
            next_action=derive_next_action(state),
            metadata={"reason": payload.get("reason")},
        )

    @staticmethod
    def _deterministic_fallback_grade(exercise: Exercise, answer: str) -> GradingResult:
        from uuid import uuid4

        from goalcoach.domain.models import RubricScores

        clean_answer = answer.strip()

        if getattr(exercise, "exercise_type", "") == "matching" or (
            isinstance(exercise.options, dict)
            and "left" in exercise.options
            and "right" in exercise.options
        ):
            from goalcoach.agents.grader_component import GraderComponent

            return GraderComponent._grade_matching_exercise(
                exercise, clean_answer, exercise.id or uuid4()
            )

        accepted = [a.strip() for a in exercise.reference_answers]
        resolved = clean_answer

        if exercise.options and isinstance(exercise.options, list):
            if clean_answer.isdigit():
                idx = int(clean_answer) - 1
                if 0 <= idx < len(exercise.options):
                    resolved = exercise.options[idx].strip()
            elif clean_answer.upper() in ("A", "B", "C", "D"):
                idx = ord(clean_answer.upper()) - ord("A")
                if 0 <= idx < len(exercise.options):
                    resolved = exercise.options[idx].strip()

        passed = clean_answer in accepted or resolved in accepted
        score = 1.0 if passed else 0.4

        return GradingResult(
            exercise_id=exercise.id or uuid4(),
            scores=RubricScores(
                grammatical_correctness=score,
                semantic_precision=score,
                pragmatic_appropriateness=score,
            ),
            passed_gates=passed,
            confidence=1.0,
            feedback="Correct." if passed else "Please try again.",
            detected_errors=[] if passed else ["ERR_INCORRECT"],
            grader_version="deterministic-fallback",
        )


__all__ = [
    "DeterministicOrchestrator",
    "OrchestratorResponse",
    "derive_next_action",
]
