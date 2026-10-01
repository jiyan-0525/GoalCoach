"""Integration test suite verifying the hardened remediation engine and edge cases.

Specifically stress-tests and validates:
1. Remediation triggers on repeated failures (occurrences >= 2) with needs_replanning = True.
2. Dynamic exercise rotation (rotates from failed e01 to unattempted e02).
3. Error profile resolution & decay on passing gating rubrics (needs_replanning = False).
4. Prerequisite DAG progression (advances to hsk1_c02 without stepping gap trap).
5. Multi-error collision resolution on same concept.
6. Exercise exhaustion graceful fallback (no IndexError).
7. Zero error profile ingress on remedial item (modality adapts to CONTRAST_EXAMPLE).
8. Strict DAG prerequisite blocking and remediated-unlocking.
9. SQLite WAL round-trip persistence of new state fields.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from goalcoach.agents.grader_component import GraderComponent
from goalcoach.agents.planning_agent import PlanningWorker
from goalcoach.agents.teaching_agent import TeachingWorker
from goalcoach.application.orchestrator import DeterministicOrchestrator, OrchestratorResponse
from goalcoach.application.progress_service import ProgressService
from goalcoach.domain.enums import EventType, PlanItemKind, TeachingActionKind
from goalcoach.domain.models import (
    ConceptMastery,
    ErrorRecord,
    GradingResult,
    LearnerState,
    LearningGoal,
    RubricScores,
    TeachingAction,
)
from goalcoach.infrastructure.persistence.content_service import ContentService
from goalcoach.infrastructure.persistence.database import (
    create_learner_schema,
    create_session_factory,
)
from goalcoach.infrastructure.persistence.repositories import (
    ContentRepository,
    SqliteLearnerRepository,
)

CONTENT_DB_PATH = Path("data/database1/goalcoach_hsk1_learning.db")
pytestmark = pytest.mark.usefixtures("planning_model_stub")


@pytest.fixture
def content_service() -> ContentService:
    if not CONTENT_DB_PATH.exists():
        pytest.skip(f"Curriculum DB missing at {CONTENT_DB_PATH}")
    factory = create_session_factory(f"sqlite:///{CONTENT_DB_PATH}")
    repo = ContentRepository(factory)
    return ContentService(repo)


@pytest.fixture
def temp_learner_repo(tmp_path: Path) -> SqliteLearnerRepository:
    db_file = tmp_path / "test_goalcoach_remediation.db"
    factory = create_session_factory(f"sqlite:///{db_file}")
    create_learner_schema(factory)
    return SqliteLearnerRepository(factory)


@pytest.fixture
def orchestrator(
    temp_learner_repo: SqliteLearnerRepository,
    content_service: ContentService,
) -> DeterministicOrchestrator:
    progress_service = ProgressService(learner_repo=temp_learner_repo)
    planning_worker = PlanningWorker(enable_prerequisites=False)
    teaching_worker = TeachingWorker()
    grader_worker = GraderComponent()

    return DeterministicOrchestrator(
        learner_repo=temp_learner_repo,
        content_service=content_service,
        progress_service=progress_service,
        planning_worker=planning_worker,
        teaching_worker=teaching_worker,
        grader_worker=grader_worker,
    )


# --- 1. Remediation Triggering & Replanning ---


async def _submit_next_wrong_answer(
    orchestrator: DeterministicOrchestrator,
    learner_id: str,
) -> tuple[str, OrchestratorResponse]:
    """Open the next teaching turn and fail its canonical pending exercise."""
    lesson = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED,
        learner_id=learner_id,
        payload={},
    )
    assert lesson.teaching_action is not None
    assert lesson.teaching_action.exercise_payload is not None
    exercise_id = str(lesson.teaching_action.exercise_payload["exercise_id"])
    result = await orchestrator.handle_event(
        event_type=EventType.ANSWER_SUBMITTED,
        learner_id=learner_id,
        payload={
            "exercise_id": exercise_id,
            "concept_id": "hsk1_c01",
            "answer": "Wrong",
        },
    )
    return exercise_id, result


def _canonical_answer(content_service: ContentService, exercise_id: str) -> str:
    exercise = content_service.get_exercise(exercise_id)
    assert exercise is not None
    if exercise.accepted_answers:
        return str(exercise.accepted_answers[0])
    if isinstance(exercise.answer, dict):
        return str(exercise.answer["value"])
    return str(exercise.answer)


def test_help_replacement_explicitly_excludes_current_exercise(
    content_service: ContentService,
) -> None:
    action = TeachingAction(
        action_kind=TeachingActionKind.CONTRAST_EXAMPLE,
        concept_id="hsk1_c01",
        content="Alternative explanation",
        history_summary="Explained the concept with a contrasting example.",
    )

    selected = TeachingWorker._select_candidate_exercise(
        action.concept_id,
        content_service,
        state=LearnerState(),
        is_remedial=True,
        excluded_exercise_id="hsk1_c01_e01",
    )
    replaced = TeachingWorker._attach_selected_exercise(action, selected)

    assert replaced.exercise_payload is not None
    assert replaced.exercise_payload["exercise_id"] != "hsk1_c01_e01"


@pytest.mark.asyncio
async def test_remediation_triggers_after_repeated_errors(
    orchestrator: DeterministicOrchestrator,
    temp_learner_repo: SqliteLearnerRepository,
) -> None:
    """Submitting 2 consecutive incorrect answers on hsk1_c01 triggers replanning with a REMEDIAL item."""
    learner_id = f"learner_trigger_{uuid4().hex[:8]}"

    # Step 1: Initialize goal
    await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={"title": "HSK 1", "daily_available_minutes": 20},
    )

    # Step 2: Fail two distinct pending teaching turns.
    first_exercise, res_1 = await _submit_next_wrong_answer(orchestrator, learner_id)
    assert res_1.grading_result.passed_gates is False
    assert res_1.replanned is False

    second_exercise, res_2 = await _submit_next_wrong_answer(orchestrator, learner_id)
    assert second_exercise != first_exercise
    assert res_2.grading_result.passed_gates is False
    assert res_2.replanned is False
    assert res_2.state is not None and res_2.state.needs_replanning is True

    # Replanning is a separate event, so one request never invokes Grader + Planner.
    replanned = await orchestrator.handle_event(
        event_type=EventType.REPLAN_REQUESTED,
        learner_id=learner_id,
        payload={"reason": "Repeated errors"},
    )
    assert replanned.replanned is True
    assert replanned.daily_plan is not None
    assert replanned.daily_plan.items[0].kind == PlanItemKind.REMEDIAL
    assert replanned.daily_plan.items[0].concept_id == "hsk1_c01"

    # Verify state in DB
    state = await temp_learner_repo.get(learner_id)
    assert state is not None
    assert state.needs_replanning is False  # Reset by orchestrator after creating adapted plan
    assert {first_exercise, second_exercise}.issubset(state.today_mistake_exercise_ids)


# --- 2. Dynamic Exercise Rotation ---


@pytest.mark.asyncio
async def test_remediation_exercise_rotates_and_does_not_repeat_e01(
    orchestrator: DeterministicOrchestrator,
) -> None:
    """When in remediation for hsk1_c01, the system must serve hsk1_c01_e02, avoiding stagnation on e01."""
    learner_id = f"learner_rotate_{uuid4().hex[:8]}"

    # Initialize goal and trigger failures
    await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={"title": "HSK 1", "daily_available_minutes": 20},
    )

    first_exercise, _ = await _submit_next_wrong_answer(orchestrator, learner_id)
    session_res = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED, learner_id=learner_id, payload={}
    )
    action = session_res.teaching_action
    assert action is not None and action.exercise_payload is not None
    assert action.exercise_payload["exercise_id"] != first_exercise

    # Modality must adapt away from EXPLANATION because failed_attempts >= 1
    assert action.action_kind in (
        TeachingActionKind.CONTRAST_EXAMPLE,
        TeachingActionKind.HINT,
        TeachingActionKind.RETRY,
        TeachingActionKind.EXERCISE,
    )


# --- 3. Error Profile Resolution & Decay ---


@pytest.mark.asyncio
async def test_remediation_success_clears_error_profile_and_resets_replanning(
    orchestrator: DeterministicOrchestrator,
    temp_learner_repo: SqliteLearnerRepository,
    content_service: ContentService,
) -> None:
    """Passing a remedial exercise resolves the concept's errors and marks it remediated today."""
    learner_id = f"learner_clear_{uuid4().hex[:8]}"

    await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={"title": "HSK 1", "daily_available_minutes": 20},
    )

    await _submit_next_wrong_answer(orchestrator, learner_id)
    await _submit_next_wrong_answer(orchestrator, learner_id)
    await orchestrator.handle_event(
        event_type=EventType.REPLAN_REQUESTED, learner_id=learner_id, payload={}
    )
    lesson = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED, learner_id=learner_id, payload={}
    )
    assert lesson.teaching_action is not None
    exercise_id = str(lesson.teaching_action.exercise_payload["exercise_id"])
    correct_answer = _canonical_answer(content_service, exercise_id)
    pass_res = await orchestrator.handle_event(
        event_type=EventType.ANSWER_SUBMITTED,
        learner_id=learner_id,
        payload={"exercise_id": exercise_id, "concept_id": "hsk1_c01", "answer": correct_answer},
    )
    assert pass_res.grading_result.passed_gates is True
    assert pass_res.replanned is False  # Must not trigger immediate turn-level replanning

    # Verify state in repository
    state = await temp_learner_repo.get(learner_id)
    assert state is not None
    # Lifelong error history is preserved; only the remediation threshold
    # counter is reset by the successful fix.
    assert state.error_profile  # historical errors remain
    assert state.remediation_counters.get("hsk1_c01", 0) == 0
    assert "hsk1_c01" in state.today_remediated_concept_ids
    assert exercise_id in state.today_completed_exercise_ids
    assert state.needs_replanning is False


# --- 4. Curriculum Progression Without Stepping Gap Trap ---


@pytest.mark.asyncio
async def test_curriculum_advances_after_remediation_without_infinite_loop(
    orchestrator: DeterministicOrchestrator,
    temp_learner_repo: SqliteLearnerRepository,
    content_service: ContentService,
) -> None:
    """After passing remediation for hsk1_c01, the system seamlessly advances to hsk1_c02."""
    learner_id = f"learner_advance_{uuid4().hex[:8]}"

    await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={"title": "HSK 1", "daily_available_minutes": 20},
    )

    await _submit_next_wrong_answer(orchestrator, learner_id)
    await _submit_next_wrong_answer(orchestrator, learner_id)
    await orchestrator.handle_event(
        event_type=EventType.REPLAN_REQUESTED, learner_id=learner_id, payload={}
    )
    lesson = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED, learner_id=learner_id, payload={}
    )
    exercise_id = str(lesson.teaching_action.exercise_payload["exercise_id"])
    correct_answer = _canonical_answer(content_service, exercise_id)
    await orchestrator.handle_event(
        event_type=EventType.ANSWER_SUBMITTED,
        learner_id=learner_id,
        payload={"exercise_id": exercise_id, "concept_id": "hsk1_c01", "answer": correct_answer},
    )

    await orchestrator.handle_event(
        event_type=EventType.REPLAN_REQUESTED, learner_id=learner_id, payload={}
    )
    next_session = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED,
        learner_id=learner_id,
        payload={},
    )

    # Verify the curriculum has moved forward to hsk1_c02
    assert next_session.teaching_action is not None
    assert next_session.teaching_action.concept_id == "hsk1_c02"
    assert next_session.teaching_action.exercise_payload["exercise_id"] == "hsk1_c02_e01"
    # Fresh encounter for hsk1_c02 should be EXPLANATION or DIALOGUE
    assert next_session.teaching_action.action_kind in (
        TeachingActionKind.EXPLANATION,
        TeachingActionKind.DIALOGUE,
    )


# --- 5. Stress Test: Multiple Distinct Errors for Same Concept ---


@pytest.mark.asyncio
async def test_edge_case_multiple_distinct_errors_for_same_concept(
    temp_learner_repo: SqliteLearnerRepository,
) -> None:
    """Learner has accumulated multiple distinct error tags for hsk1_c01; passing remediation resolves both."""
    progress_service = ProgressService(learner_repo=temp_learner_repo)

    state = LearnerState(
        learner_id="multi_error_learner",
        goal=LearningGoal(title="HSK1"),
        error_profile=[
            ErrorRecord(code="ERR_VOCAB_MEANING", concept_id="hsk1_c01", occurrences=2),
            ErrorRecord(code="ERR_QUESTION_MA", concept_id="hsk1_c01", occurrences=3),
        ],
        needs_replanning=True,
    )
    # Give active plan with remedial item
    from goalcoach.domain.models import DailyPlan, PlanItem

    state.active_plan = DailyPlan(
        learner_id=state.learner_id,
        items=[
            PlanItem(
                concept_id="hsk1_c01",
                kind=PlanItemKind.REMEDIAL,
                objective="Remediate hsk1_c01",
                estimated_minutes=10,
            )
        ],
        rationale="Remedial plan",
    )

    pass_result = GradingResult(
        exercise_id="hsk1_c01_e02",
        scores=RubricScores(
            grammatical_correctness=1.0, semantic_precision=1.0, pragmatic_appropriateness=1.0
        ),
        passed_gates=True,
        confidence=1.0,
        feedback="Perfect!",
    )

    updated_state = progress_service.apply_grading_result(state, pass_result, concept_id="hsk1_c01")

    # The lifelong error history is preserved; the remediation threshold
    # counter (not the error_profile) is what the successful fix resets.
    assert len(updated_state.error_profile) == 2  # historical errors remain
    assert updated_state.remediation_counters.get("hsk1_c01", 0) == 0
    assert updated_state.needs_replanning is False
    assert "hsk1_c01" in updated_state.today_remediated_concept_ids
    assert "hsk1_c01_e02" in updated_state.today_completed_exercise_ids


# --- 6. Stress Test: Exercise Exhaustion Graceful Fallback ---


@pytest.mark.asyncio
async def test_edge_case_exercise_exhaustion_graceful_fallback(
    content_service: ContentService,
) -> None:
    """When all exercises for a concept have been attempted, system does not crash and safely falls back."""
    teacher = TeachingWorker()
    all_c01_exercises = content_service.get_exercises_for_concept("hsk1_c01", limit=20)
    all_c01_exercise_ids = [e.exercise_id for e in all_c01_exercises]
    state = LearnerState(
        goal=LearningGoal(title="HSK1"),
        # Simulate all exercises for hsk1_c01 completed
        today_completed_exercise_ids=all_c01_exercise_ids,
    )

    # Should not raise IndexError
    action = await teacher.teach_concept(
        concept_id="hsk1_c01",
        state=state,
        content_service=content_service,
        failed_attempts=0,
    )
    assert action.exercise_payload is not None
    assert action.exercise_payload["exercise_id"] in all_c01_exercise_ids


# --- 7. Stress Test: Zero Error Profile Ingress on Remedial Item ---


@pytest.mark.asyncio
async def test_edge_case_zero_error_profile_remedial_ingress(
    orchestrator: DeterministicOrchestrator,
) -> None:
    """A REMEDIAL plan item with empty error_profile defaults failed_attempts=1 to trigger adaptive modality."""
    learner_id = f"learner_zero_err_{uuid4().hex[:8]}"

    # Manually configure state with REMEDIAL item but empty error_profile
    from goalcoach.domain.models import DailyPlan, PlanItem

    state = LearnerState(
        learner_id=learner_id,
        goal=LearningGoal(title="HSK 1"),
        error_profile=[],
        active_plan=DailyPlan(
            learner_id=learner_id,
            items=[
                PlanItem(
                    concept_id="hsk1_c01",
                    kind=PlanItemKind.REMEDIAL,
                    objective="Manual remedial allocation",
                    estimated_minutes=10,
                )
            ],
            rationale="Manually triggered remediation",
        ),
    )
    await orchestrator.learner_repo.save(state)

    session_res = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED,
        learner_id=learner_id,
        payload={},
    )
    action = session_res.teaching_action
    # Modality must adapt away from fresh EXPLANATION because it is a REMEDIAL item
    assert action.action_kind in (
        TeachingActionKind.CONTRAST_EXAMPLE,
        TeachingActionKind.HINT,
        TeachingActionKind.RETRY,
    )


# --- 8. Stress Test: Prerequisite DAG Strict Blocking and Remediated Unlocking ---


@pytest.mark.asyncio
async def test_edge_case_prerequisite_dag_blocks_unready_and_unlocks_remediated(
    content_service: ContentService,
) -> None:
    """hsk1_c02 requires hsk1_c01. It is strictly blocked if hsk1_c01 has 0 mastery, but unlocked if remediated."""
    planner = PlanningWorker(enable_prerequisites=True)

    # Case A: Clean state -> hsk1_c02 is blocked because hsk1_c01 not started
    state_a = LearnerState(goal=LearningGoal(title="HSK1", daily_available_minutes=20))
    plan_a = await planner.create_plan(state=state_a, content_service=content_service)
    assert plan_a.ordered_items[0].concept_id == "hsk1_c01"
    assert all(it.concept_id != "hsk1_c02" for it in plan_a.ordered_items)

    # Case B: hsk1_c01 was remediated today with mastery 0.25 -> hsk1_c02 is UNLOCKED
    state_b = LearnerState(
        goal=LearningGoal(title="HSK1", daily_available_minutes=20),
        mastery={
            "hsk1_c01": ConceptMastery(concept_id="hsk1_c01", mastery_score=0.25, evidence_count=1)
        },
        today_remediated_concept_ids=["hsk1_c01"],
        today_studied_concept_ids=["hsk1_c01"],
    )
    plan_b = await planner.create_plan(state=state_b, content_service=content_service)
    # The first item must be hsk1_c02 (NEW)
    assert plan_b.ordered_items[0].concept_id == "hsk1_c02"
    assert plan_b.ordered_items[0].kind == PlanItemKind.NEW
    # hsk1_c03 requires hsk1_c02, so hsk1_c03 must still be blocked!
    assert all(it.concept_id != "hsk1_c03" for it in plan_b.ordered_items)


# --- 9. Stress Test: SQLite WAL Persistence Round-Trip of New State Fields ---


@pytest.mark.asyncio
async def test_edge_case_state_persistence_and_reload_with_new_fields(
    temp_learner_repo: SqliteLearnerRepository,
) -> None:
    """State with today_completed_exercise_ids and today_remediated_concept_ids survives SQLite WAL roundtrips."""
    learner_id = f"wal_test_{uuid4().hex[:8]}"
    state = LearnerState(
        learner_id=learner_id,
        goal=LearningGoal(title="HSK1"),
        today_completed_exercise_ids=["hsk1_c01_e01", "hsk1_c01_e02"],
        today_mistake_exercise_ids=["hsk1_c01_e01"],
        today_remediated_concept_ids=["hsk1_c01"],
    )

    # Save to disk
    await temp_learner_repo.save(state)

    # Reload from disk
    reloaded = await temp_learner_repo.get(learner_id)
    assert reloaded is not None
    assert reloaded.today_completed_exercise_ids == ["hsk1_c01_e01", "hsk1_c01_e02"]
    assert reloaded.today_remediated_concept_ids == ["hsk1_c01"]
    assert reloaded.all_attempted_exercise_ids() == {"hsk1_c01_e01", "hsk1_c01_e02"}


@pytest.mark.asyncio
async def test_repeated_replan_rebuild_preserves_daily_plan_and_roadmap(
    orchestrator: DeterministicOrchestrator,
    temp_learner_repo: SqliteLearnerRepository,
) -> None:
    """Ordinary replanning preserves the frozen roadmap and does not loop on rebuild."""
    learner_id = f"learner_frozen_{uuid4().hex[:8]}"
    await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={"title": "HSK 1", "daily_available_minutes": 20},
    )
    before = await temp_learner_repo.get(learner_id)
    assert before is not None and before.active_plan is not None

    before.active_plan.items = list(reversed(before.active_plan.items))
    before.roadmap_concept_ids = list(reversed(before.roadmap_concept_ids))
    before.needs_replanning = True
    await temp_learner_repo.save(before)

    after_replan = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED,
        learner_id=learner_id,
        payload={},
    )
    assert after_replan.replanned is True
    assert after_replan.state is not None
    assert after_replan.state.roadmap_concept_ids == before.roadmap_concept_ids

    before_version = after_replan.state.state_version
    repeat = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED,
        learner_id=learner_id,
        payload={},
    )
    assert repeat.replanned is False
    assert repeat.state is not None
    assert str(repeat.state.active_plan.id) == str(after_replan.state.active_plan.id)
    assert [str(item.id) for item in repeat.state.active_plan.items] == [
        str(item.id) for item in after_replan.state.active_plan.items
    ]
    assert repeat.state.state_version == before_version + 1


@pytest.mark.asyncio
async def test_timezone_update_only_changes_daily_boundary(
    orchestrator: DeterministicOrchestrator,
    temp_learner_repo: SqliteLearnerRepository,
) -> None:
    """Changing the calendar timezone is configuration, not a new learning goal."""
    learner_id = f"learner_timezone_{uuid4().hex[:8]}"
    created = await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={"title": "Travel in China", "daily_available_minutes": 20, "timezone": "UTC"},
    )
    before = created.state
    assert before is not None and before.goal is not None and before.active_plan is not None

    updated = await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={
            "title": "Travel in China",
            "daily_available_minutes": 20,
            "timezone": "Asia/Shanghai",
        },
    )

    assert updated.state is not None
    assert updated.state.goal is not None
    assert updated.state.goal.timezone == "Asia/Shanghai"
    assert updated.state.goal_fingerprint == before.goal_fingerprint
    assert updated.state.mastery == before.mastery
    assert updated.state.concept_progress == before.concept_progress
    assert updated.state.sessions == before.sessions
    assert updated.state.roadmap_concept_ids == before.roadmap_concept_ids


# --- 10. MCQ Options & 1-Click Answering Test ---


@pytest.mark.asyncio
async def test_mcq_options_in_payload_and_1_click_grading(
    orchestrator: DeterministicOrchestrator,
    temp_learner_repo: SqliteLearnerRepository,
) -> None:
    """Exercises with options populate payload with options and accept 1-based index answers."""
    learner_id = f"mcq_test_{uuid4().hex[:8]}"

    # Start session on hsk1_c01
    await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={"title": "HSK 1", "daily_available_minutes": 20},
    )

    session_res = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED,
        learner_id=learner_id,
        payload={},
    )

    action = session_res.teaching_action
    assert action is not None
    assert action.exercise_payload is not None
    assert action.exercise_payload["exercise_id"] == "hsk1_c01_e01"

    # Verify options are present in payload
    options = action.exercise_payload.get("options")
    assert options == ["Hello", "Thank you", "Goodbye", "Sorry"]

    # Option 1 is "Hello", which is the correct answer to hsk1_c01_e01 ("你好")
    # Submitting "1" or "A" should be resolved to "Hello" and pass gates!
    pass_res = await orchestrator.handle_event(
        event_type=EventType.ANSWER_SUBMITTED,
        learner_id=learner_id,
        payload={
            "exercise_id": "hsk1_c01_e01",
            "concept_id": "hsk1_c01",
            "answer": "1",
        },
    )

    assert pass_res.grading_result.passed_gates is True
    assert pass_res.grading_result.scores.grammatical_correctness == 1.0
    assert pass_res.grading_result.scores.semantic_precision == 1.0


# --- 11. Stale Plan Item ID Resilient Fallback Test ---


@pytest.mark.asyncio
async def test_stale_plan_item_id_fallback_to_uncompleted_item(
    orchestrator: DeterministicOrchestrator,
    temp_learner_repo: SqliteLearnerRepository,
) -> None:
    """When a client provides a stale plan_item_id (e.g. from an earlier plan before replanning),

    the orchestrator falls back to the uncompleted item rather than raising SessionLifecycleError.
    """
    learner_id = f"stale_item_test_{uuid4().hex[:8]}"

    await orchestrator.handle_event(
        event_type=EventType.GOAL_CREATED,
        learner_id=learner_id,
        payload={"title": "HSK 1", "daily_available_minutes": 20},
    )

    # Pass a completely bogus / stale plan_item_id
    res = await orchestrator.handle_event(
        event_type=EventType.SESSION_STARTED,
        learner_id=learner_id,
        payload={
            "entry_source": "planned",
            "plan_item_id": "non-existent-uuid-12345",
            "concept_id": "hsk1_c01",
        },
    )

    # Must succeed cleanly by falling back to the uncompleted item
    assert res.teaching_action is not None
    assert res.teaching_action.concept_id == "hsk1_c01"
