"""Deterministic worker doubles for closed-loop application tests."""

from goalcoach.domain.enums import PlanItemKind, TeachingActionKind
from goalcoach.domain.models import (
    Exercise,
    GradingResult,
    LearnerState,
    PlanItem,
    PlanUpdate,
    RubricScores,
    TeachingAction,
)
from goalcoach.infrastructure.persistence.content_service import ContentService


class FakePlanningWorker:
    """Produce a state-conditioned plan without network access."""

    async def create_plan(
        self,
        state: LearnerState,
        content_service: ContentService,
        *,
        allow_roadmap_changes: bool = False,
    ) -> PlanUpdate:
        concepts = content_service.list_all_concepts()
        roadmap = [concept.concept_id for concept in concepts]
        weak = next(
            (error.concept_id for error in state.error_profile if error.occurrences >= 2),
            None,
        )
        concept_id = weak or next(
            (cid for cid in roadmap if cid not in state.today_studied_concept_ids),
            roadmap[0],
        )
        kind = PlanItemKind.REMEDIAL if weak else PlanItemKind.NEW
        minutes = min(
            state.active_session.planned_minutes
            if state.active_session
            else (state.goal.daily_available_minutes if state.goal else 20),
            10,
        )
        return PlanUpdate(
            daily_allocation_minutes=minutes,
            ordered_items=[
                PlanItem(
                    concept_id=concept_id,
                    kind=kind,
                    objective=f"Practice {concept_id}",
                    estimated_minutes=minutes,
                )
            ],
            adaptation_rationale="Prioritize current learner evidence.",
            roadmap_adjustments=["Prioritize recurring errors"] if weak else [],
            roadmap_concept_ids=roadmap,
        )


class FakeTeachingWorker:
    """Return adaptive teaching actions grounded in canonical exercises."""

    async def teach_concept(
        self,
        concept_id: str,
        state: LearnerState,
        content_service: ContentService,
        failed_attempts: int = 0,
        learner_query: str | None = None,
        excluded_exercise_id: str | None = None,
        target_exercise_id: str | None = None,
    ) -> TeachingAction:
        exercises = content_service.get_exercises_for_concept(concept_id, limit=10)
        if target_exercise_id:
            exercise = next(
                (item for item in exercises if item.exercise_id == target_exercise_id),
                exercises[0],
            )
        else:
            exercise = next(
                (item for item in exercises if item.exercise_id != excluded_exercise_id),
                exercises[0],
            )
        action_kind = (
            TeachingActionKind.EXPLANATION
            if failed_attempts == 0
            else TeachingActionKind.CONTRAST_EXAMPLE
        )
        metadata = {"provider": "fake"}
        if learner_query:
            metadata["learner_query"] = learner_query
        return TeachingAction(
            action_kind=action_kind,
            concept_id=concept_id,
            content=f"Teach {concept_id} for {state.goal.title if state.goal else 'the goal'}.",
            history_summary=f"Taught {concept_id} using {action_kind.value}.",
            exercise_payload={
                "exercise_id": exercise.exercise_id,
                "concept_id": concept_id,
                "prompt": exercise.prompt,
                "instruction": exercise.instruction or "",
            },
            metadata=metadata,
        )


class FakeGraderComponent:
    """Grade canonical exact answers and emit stable failure evidence."""

    async def grade(self, exercise: Exercise, answer: str) -> GradingResult:
        references = [value.strip().casefold() for value in exercise.reference_answers]
        passed = answer.strip().casefold() in references
        score = 1.0 if passed else 0.3
        return GradingResult(
            exercise_id=exercise.id,
            scores=RubricScores(
                grammatical_correctness=score,
                semantic_precision=score,
                pragmatic_appropriateness=score,
            ),
            passed_gates=passed,
            confidence=1.0,
            feedback="Correct." if passed else "Review the target structure.",
            detected_errors=[] if passed else ["ERR_TEST_STRUCTURE"],
            grader_version="test-double",
        )
