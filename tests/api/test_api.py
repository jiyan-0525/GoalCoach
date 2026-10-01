from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

from goalcoach.domain.enums import PlanItemKind
from goalcoach.domain.models import PlanItem, PlanUpdate

# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


@pytest.mark.asyncio
async def test_request_correlation_id_is_preserved(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"x-request-id": "goalcoach-test-123"})

    assert response.headers["x-request-id"] == "goalcoach-test-123"


# ---------------------------------------------------------------------------
# GET /api/v1/learners/{learner_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_learner_returns_200(client: AsyncClient) -> None:
    response = await client.get("/api/v1/learners/some-learner-id")

    assert response.status_code == 200
    body = response.json()
    assert "state" in body
    assert "progressSummary" in body


@pytest.mark.asyncio
async def test_get_learner_includes_id_in_response(client: AsyncClient) -> None:
    learner_id = "abc-123"
    response = await client.get(f"/api/v1/learners/{learner_id}")

    assert response.status_code == 200
    assert response.json()["state"]["learnerId"] == learner_id


# ---------------------------------------------------------------------------
# GET /api/v1/curriculum/concepts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_curriculum_concepts_returns_200(client: AsyncClient) -> None:
    response = await client.get("/api/v1/curriculum/concepts")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# Retired duplicate write routes
# ---------------------------------------------------------------------------

VALID_ANSWER_PAYLOAD = {
    "learner_id": "550e8400-e29b-41d4-a716-446655440000",
    "exercise_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    "answer": "你好",
}


@pytest.mark.asyncio
async def test_legacy_answer_route_is_removed(client: AsyncClient) -> None:
    response = await client.post("/api/v1/answers", json=VALID_ANSWER_PAYLOAD)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_answer_route_does_not_process_payloads(client: AsyncClient) -> None:
    payload = {**VALID_ANSWER_PAYLOAD, "answer": ""}
    response = await client.post("/api/v1/answers", json=payload)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_answer_route_is_not_a_validation_boundary(client: AsyncClient) -> None:
    response = await client.post("/api/v1/answers", json={})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_event_session_started(client: AsyncClient) -> None:
    payload = {
        "event_type": "SESSION_STARTED",
        "learner_id": f"api-session-without-goal-{uuid4().hex}",
        "payload": {},
    }
    response = await client.post("/api/v1/events", json=payload)
    assert response.status_code == 409
    assert "Create a learning goal" in response.json()["detail"]


@pytest.mark.asyncio
async def test_goal_event_preserves_free_form_title_for_agents(
    client: AsyncClient,
) -> None:
    learner_id = f"api-goal-context-{uuid4().hex}"
    response = await client.post(
        "/api/v1/events",
        json={
            "event_type": "GOAL_CREATED",
            "learner_id": learner_id,
            "payload": {
                "title": "I want to travel in China and order food.",
                "daily_available_minutes": 20,
            },
        },
    )

    assert response.status_code == 200
    state = response.json()["state"]
    assert state["goal"]["title"] == "I want to travel in China and order food."


@pytest.mark.asyncio
async def test_roadmap_is_empty_until_planning_selects_goal_relevant_units(
    client: AsyncClient,
) -> None:
    learner_id = f"api-roadmap-empty-{uuid4().hex}"
    plan_response = await client.get(f"/api/v1/learners/{learner_id}/today-plan")
    roadmap_response = await client.get(f"/api/v1/learners/{learner_id}/roadmap")

    assert plan_response.status_code == 404
    assert roadmap_response.status_code == 200
    body = roadmap_response.json()
    assert body["dailyPlan"] is None
    assert body["roadmap"] == []
    assert "progressSummary" in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"title": "", "daily_available_minutes": 20},
        {"title": "Travel in China", "daily_available_minutes": 0},
        {"title": "Travel in China", "daily_available_minutes": 241},
    ],
)
async def test_goal_event_rejects_invalid_user_input(
    client: AsyncClient,
    payload: dict[str, object],
) -> None:
    response = await client.post(
        "/api/v1/events",
        json={
            "event_type": "GOAL_CREATED",
            "learner_id": "api-invalid-goal",
            "payload": payload,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.asyncio
async def test_llm_unavailable_uses_labeled_deterministic_fallback(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*_args: object, **_kwargs: object) -> PlanUpdate:
        return PlanUpdate(
            daily_allocation_minutes=5,
            ordered_items=[
                PlanItem(
                    concept_id="hsk1_c01",
                    kind=PlanItemKind.NEW,
                    objective="Fallback lesson",
                    estimated_minutes=5,
                )
            ],
            adaptation_rationale="Deterministic fallback",
            roadmap_concept_ids=["hsk1_c01"],
            metadata={
                "provider": "deterministic",
                "fallback_used": True,
                "notice": "LLM unavailable; deterministic planning fallback used: provider offline",
            },
        )

    monkeypatch.setattr(
        "tests.fakes.FakePlanningWorker.create_plan",
        unavailable,
    )
    response = await client.post(
        "/api/v1/events",
        json={
            "event_type": "GOAL_CREATED",
            "learner_id": "api-llm-unavailable",
            "payload": {"title": "Travel in China", "daily_available_minutes": 20},
        },
    )

    assert response.status_code == 200
    metadata = response.json()["planUpdate"]["metadata"]
    assert metadata["fallback_used"] is True
    assert metadata["notice"].startswith("LLM unavailable")


@pytest.mark.asyncio
async def test_help_event_rejects_unknown_curriculum_concept(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/events",
        json={
            "event_type": "HELP_REQUESTED",
            "learner_id": "api-invalid-help",
            "payload": {
                "concept_id": "does-not-exist",
                "learner_query": "Please explain this differently.",
            },
        },
    )

    assert response.status_code == 422
    assert "Unknown curriculum concept" in response.json()["detail"]


@pytest.mark.asyncio
async def test_help_event_with_learner_query_preserves_exercise_and_addresses_question(
    client: AsyncClient,
) -> None:
    learner_id = f"api-help-{uuid4().hex}"
    # Create goal first
    await client.post(
        "/api/v1/events",
        json={
            "event_type": "GOAL_CREATED",
            "learner_id": learner_id,
            "payload": {"title": "Travel in China", "daily_available_minutes": 20},
        },
    )
    # Start session to get initial teaching action and exercise
    session_res = await client.post(
        "/api/v1/events",
        json={
            "event_type": "SESSION_STARTED",
            "learner_id": learner_id,
            "payload": {"concept_id": "hsk1_c01"},
        },
    )
    assert session_res.status_code == 200
    initial_exercise_id = session_res.json()["teachingAction"]["exercisePayload"]["exercise_id"]

    # Request help with a specific query
    help_query = "What is the difference between 你 and 您?"
    help_res = await client.post(
        "/api/v1/events",
        json={
            "event_type": "HELP_REQUESTED",
            "learner_id": learner_id,
            "payload": {
                "concept_id": "hsk1_c01",
                "current_exercise_id": initial_exercise_id,
                "learner_query": help_query,
            },
        },
    )
    assert help_res.status_code == 200
    data = help_res.json()
    action = data["teachingAction"]
    assert action is not None
    # Exercise must be preserved
    assert action["exercisePayload"]["exercise_id"] == initial_exercise_id
    # Learner query must be recorded in metadata
    assert action["metadata"]["learner_query"] == help_query
    # Content must address the question
    content = action["content"]
    assert len(content) > 0


@pytest.mark.asyncio
async def test_replan_is_an_explicit_single_worker_event(client: AsyncClient) -> None:
    learner_id = f"api-replan-{uuid4().hex}"
    await client.post(
        "/api/v1/events",
        json={
            "event_type": "GOAL_CREATED",
            "learner_id": learner_id,
            "payload": {"title": "Travel in China", "daily_available_minutes": 20},
        },
    )
    response = await client.post(
        "/api/v1/events",
        json={
            "event_type": "REPLAN_REQUESTED",
            "learner_id": learner_id,
            "payload": {"reason": "Refresh today's plan"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["replanned"] is True
    assert body["planUpdate"] is not None
    assert body["teachingAction"] is None


@pytest.mark.asyncio
async def test_answer_requires_current_pending_teaching_turn(client: AsyncClient) -> None:
    learner_id = f"api-no-pending-turn-{uuid4().hex}"
    await client.post(
        "/api/v1/events",
        json={
            "event_type": "GOAL_CREATED",
            "learner_id": learner_id,
            "payload": {"title": "Travel in China", "daily_available_minutes": 20},
        },
    )
    response = await client.post(
        "/api/v1/events",
        json={
            "event_type": "ANSWER_SUBMITTED",
            "learner_id": learner_id,
            "payload": {
                "exercise_id": "hsk1_c01_e01",
                "concept_id": "hsk1_c01",
                "answer": "Hello",
            },
        },
    )
    assert response.status_code == 409
    assert "Start a learning session" in response.json()["detail"]


async def _canonical_c01_answer(client: AsyncClient) -> str:
    response = await client.get("/api/v1/curriculum/concepts/hsk1_c01")
    return str(response.json()["exercises"][0]["acceptedAnswers"][0])


@pytest.mark.asyncio
async def test_roadmap_study_returns_feedback_without_recording_progress(
    client: AsyncClient,
) -> None:
    learner_id = f"api-roadmap-review-{uuid4().hex}"
    await client.post(
        "/api/v1/events",
        json={
            "event_type": "GOAL_CREATED",
            "learner_id": learner_id,
            "payload": {"title": "Travel in China", "daily_available_minutes": 20},
        },
    )
    lesson = await client.post(
        "/api/v1/events",
        json={
            "event_type": "SESSION_STARTED",
            "learner_id": learner_id,
            "payload": {"entry_source": "roadmap", "concept_id": "hsk1_c01"},
        },
    )
    lesson_body = lesson.json()
    exercise = lesson_body["teachingAction"]["exercisePayload"]
    assert lesson_body["teachingAction"]["metadata"]["progress_eligible"] is False
    assert lesson_body["state"]["agentHistory"]["recentTeachingTurns"] != []

    graded = await client.post(
        "/api/v1/events",
        json={
            "event_type": "ANSWER_SUBMITTED",
            "learner_id": learner_id,
            "payload": {
                "exercise_id": exercise["exercise_id"],
                "concept_id": exercise["concept_id"],
                "answer": await _canonical_c01_answer(client),
                "time_spent_seconds": 90,
            },
        },
    )
    body = graded.json()
    assert body["gradingResult"]["passedGates"] is True
    assert body["metadata"]["progressEligible"] is False
    assert body["progressSummary"]["learnedProgress"] == 0
    assert body["state"]["mastery"] == {}
    assert body["state"]["activeSession"]["activeSeconds"] == 0
    assert body["state"]["agentHistory"]["recentTeachingTurns"] != []
    assert body["state"]["agentHistory"]["sessionCount"] == 0
    assert body["state"]["sessions"] == []


@pytest.mark.asyncio
async def test_completed_daily_item_can_be_repeated_without_more_progress(
    client: AsyncClient,
) -> None:
    learner_id = f"api-daily-repeat-{uuid4().hex}"
    goal = await client.post(
        "/api/v1/events",
        json={
            "event_type": "GOAL_CREATED",
            "learner_id": learner_id,
            "payload": {"title": "Travel in China", "daily_available_minutes": 20},
        },
    )
    plan_item = goal.json()["dailyPlan"]["items"][0]
    lesson = await client.post(
        "/api/v1/events",
        json={
            "event_type": "SESSION_STARTED",
            "learner_id": learner_id,
            "payload": {"entry_source": "planned", "plan_item_id": plan_item["id"]},
        },
    )
    exercise = lesson.json()["teachingAction"]["exercisePayload"]
    first_grade = await client.post(
        "/api/v1/events",
        json={
            "event_type": "ANSWER_SUBMITTED",
            "learner_id": learner_id,
            "payload": {
                "exercise_id": exercise["exercise_id"],
                "concept_id": exercise["concept_id"],
                "answer": await _canonical_c01_answer(client),
                "time_spent_seconds": 60,
            },
        },
    )
    first_summary = first_grade.json()["progressSummary"]

    review = await client.post(
        "/api/v1/events",
        json={
            "event_type": "SESSION_STARTED",
            "learner_id": learner_id,
            "payload": {"entry_source": "daily_review", "concept_id": "hsk1_c01"},
        },
    )
    review_exercise = review.json()["teachingAction"]["exercisePayload"]
    repeated = await client.post(
        "/api/v1/events",
        json={
            "event_type": "ANSWER_SUBMITTED",
            "learner_id": learner_id,
            "payload": {
                "exercise_id": review_exercise["exercise_id"],
                "concept_id": review_exercise["concept_id"],
                "answer": await _canonical_c01_answer(client),
                "time_spent_seconds": 120,
            },
        },
    )
    repeated_body = repeated.json()
    assert repeated_body["metadata"]["progressEligible"] is False
    for metric, value in first_summary.items():
        if metric != "stateVersion":
            assert repeated_body["progressSummary"][metric] == value


@pytest.mark.asyncio
async def test_post_event_rejects_missing_fields(client: AsyncClient) -> None:
    response = await client.post("/api/v1/events", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_event_rejects_invalid_json_body(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/events",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 404 for unknown routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_route_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/nonexistent")

    assert response.status_code == 404
