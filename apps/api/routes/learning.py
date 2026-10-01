"""apps/api/routes/learning.py
REST endpoints providing curriculum access, text-to-speech, grading, and learner state management.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from apps.api.dependencies import get_content_repo, get_learner_repo
from goalcoach.application.orchestrator import derive_next_action
from goalcoach.application.progress_reducer import compute_progress_summary
from goalcoach.domain.models import (
    LearnerState,
)
from goalcoach.infrastructure.persistence.models import (
    ContentExercise,
    CurriculumConcept,
    TeachingCard,
)
from goalcoach.infrastructure.persistence.repositories import (
    ContentRepository,
    SqliteLearnerRepository,
)

router = APIRouter(tags=["learning"])

tts_cache: dict[str, bytes] = {}


def serialize_concept(c: CurriculumConcept) -> dict[str, Any]:
    meta = c.metadata_json or {}
    return {
        "conceptId": c.concept_id,
        "hskLevel": c.hsk_level,
        "sequenceNo": c.sequence_no,
        "slug": c.slug,
        "titleZh": c.title_zh,
        "titleEn": c.title_en,
        "conceptType": c.concept_type,
        "category": meta.get("category", "Grammar"),
        "module": meta.get("module", "Greetings"),
        "theme": meta.get("theme", "general"),
        "tags": meta.get("tags", []),
        "isCoreGrammar": meta.get("is_core_grammar", False),
        "communicativeGoal": c.communicative_goal,
        "grammarFocus": c.grammar_focus or [],
        "vocabularyFocus": c.vocabulary_focus or [],
        "difficulty": c.difficulty,
        "estimatedMinutes": c.estimated_minutes,
    }


def build_roadmap_projection(
    state: LearnerState,
    concepts: list[CurriculumConcept],
) -> list[dict[str, Any]]:
    """Project canonical content plus the active plan from one learner state.

    The roadmap never owns a separate completion model: node status is the
    persisted ``concept_progress`` and today's queue is ``active_plan``.
    """
    by_id = {concept.concept_id: concept for concept in concepts}
    ordered_ids = [concept_id for concept_id in state.roadmap_concept_ids if concept_id in by_id]
    plan_items = {
        item.concept_id: item for item in (state.active_plan.items if state.active_plan else [])
    }
    return [
        {
            **serialize_concept(by_id[concept_id]),
            "progress": state.concept_progress.get(concept_id),
            "dailyPlanItem": plan_items.get(concept_id),
        }
        for concept_id in ordered_ids
    ]


def serialize_card(card: TeachingCard) -> dict[str, Any]:
    return {
        "id": card.card_id,
        "conceptId": card.concept_id,
        "cardOrder": card.card_order,
        "cardType": card.card_type,
        "promptZh": card.prompt_zh,
        "pinyin": card.pinyin,
        "meaningEn": card.meaning_en,
        "explanationEn": card.explanation_en,
        "exampleZh": card.example_zh,
        "examplePinyin": card.example_pinyin,
        "exampleEn": card.example_en,
        "payload": card.payload or {},
    }


def serialize_exercise(ex: ContentExercise) -> dict[str, Any]:
    ans = ex.answer
    if isinstance(ans, dict):
        ans_str = ans.get("value") or ans.get("text") or str(ans)
    else:
        ans_str = str(ans or "")

    accepted = []
    if isinstance(ex.accepted_answers, list):
        accepted.extend([str(a) for a in ex.accepted_answers])
    elif ex.accepted_answers:
        accepted.append(str(ex.accepted_answers))
    if ans_str and ans_str not in accepted:
        accepted.append(ans_str)

    return {
        "id": ex.exercise_id,
        "conceptId": ex.concept_id,
        "exerciseOrder": ex.exercise_order,
        "exerciseType": ex.exercise_type,
        "prompt": ex.prompt,
        "promptPinyin": ex.prompt_pinyin,
        "instruction": ex.instruction,
        "answer": ans_str,
        "options": ex.options or [],
        "acceptedAnswers": accepted,
        "explanation": ex.explanation or "",
        "targetTokens": ex.target_tokens or [],
        "errorTags": ex.error_tags or [],
        "difficulty": ex.difficulty,
    }


async def get_or_create_learner(
    learner_id: str,
    repo: SqliteLearnerRepository,
) -> LearnerState:
    state = await repo.get(learner_id)
    if not state:
        state = LearnerState(
            learner_id=learner_id,
            display_name=f"Learner {learner_id}",
        )
        await repo.save(state)
    return state


# --- 1. Text to Speech ---


@router.get("/api/tts")
async def text_to_speech(text: str = Query(..., min_length=1)) -> Response:
    """Proxy Google TTS audio with text normalization and memory caching."""
    clean = re.sub(r"[^\w\u4e00-\u9fa5]+", " ", text).strip()
    if not clean:
        raise HTTPException(status_code=400, detail="No speakable text")

    if clean in tts_cache:
        return Response(
            content=tts_cache[clean],
            media_type="audio/mpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=zh-CN&client=tw-ob&q={quote(clean)}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code != 200:
                raise HTTPException(status_code=502, detail="TTS upstream error")
            tts_cache[clean] = res.content
            return Response(
                content=res.content,
                media_type="audio/mpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"TTS network error: {exc}") from exc


# --- 2. Learner State Projections ---


@router.get("/api/v1/learners/{learner_id}")
async def get_learner_aggregate(
    learner_id: str,
    learner_repo: SqliteLearnerRepository = Depends(get_learner_repo),
    content_repo: ContentRepository = Depends(get_content_repo),
) -> dict[str, Any]:
    """Fetch the authoritative learner state and its progress projection."""
    state = await get_or_create_learner(learner_id, learner_repo)
    concepts = content_repo.list_concepts()
    summary = compute_progress_summary(state, concepts)
    return {
        "state": state,
        "overallProgress": state.overall_progress(),
        "nextAction": derive_next_action(state),
        "progressSummary": summary,
    }


@router.get("/api/v1/learners/{learner_id}/today-plan")
async def get_today_plan(
    learner_id: str,
    learner_repo: SqliteLearnerRepository = Depends(get_learner_repo),
) -> Any:
    """Return the persisted Agent-generated daily plan without mutating state."""
    state = await get_or_create_learner(learner_id, learner_repo)
    if state.active_plan is None:
        raise HTTPException(
            status_code=404,
            detail="No daily plan exists. Create a goal through POST /api/v1/events first.",
        )
    return state.active_plan


@router.get("/api/v1/learners/{learner_id}/roadmap")
async def get_learner_roadmap(
    learner_id: str,
    learner_repo: SqliteLearnerRepository = Depends(get_learner_repo),
    content_repo: ContentRepository = Depends(get_content_repo),
) -> dict[str, Any]:
    """Return the learner-specific roadmap and today's plan from one state snapshot."""
    state = await get_or_create_learner(learner_id, learner_repo)
    concepts = content_repo.list_concepts()
    return {
        "stateVersion": state.state_version,
        "nextAction": derive_next_action(state),
        "dailyPlan": state.active_plan,
        "roadmap": build_roadmap_projection(state, concepts),
        "roadmapCoverageRationale": state.roadmap_coverage_rationale,
        "progressSummary": compute_progress_summary(state, concepts),
    }


# --- 3. Curriculum Content Queries ---


@router.get("/api/v1/curriculum/concepts")
async def list_curriculum_concepts(
    level: int | None = Query(default=None),
    hsk_level: int | None = Query(default=None),
    content_repo: ContentRepository = Depends(get_content_repo),
) -> list[dict[str, Any]]:
    """List all active curriculum concepts."""
    target_level = level if level is not None else hsk_level
    concepts = content_repo.list_concepts(hsk_level=target_level)
    return [serialize_concept(c) for c in concepts]


@router.get("/api/v1/curriculum/concepts/{concept_id}")
async def get_curriculum_concept_details(
    concept_id: str,
    content_repo: ContentRepository = Depends(get_content_repo),
) -> dict[str, Any]:
    """Return concept details, teaching cards, and practice exercises."""
    concept = content_repo.get_concept(concept_id)
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    cards = content_repo.get_teaching_cards(concept.concept_id)
    exercises = content_repo.get_exercises(concept.concept_id, limit=5, randomize=False)

    return {
        "concept": serialize_concept(concept),
        "cards": [serialize_card(c) for c in cards],
        "exercises": [serialize_exercise(e) for e in exercises],
    }
