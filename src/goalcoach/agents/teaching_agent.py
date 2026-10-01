"""Teaching Worker & Agent: Selects grounded pedagogical modalities for the active turn.

Implements PRD Section 9:
1. Pure PydanticAI Agent with strictly typed dependency injection (TeachingDeps)
   and structured output (TeachingAction).
2. Grounded practice tasks attached via Database #1 (never exposing reference answers to the client).
3. 3-stage failure fallback strategy:
   - 0 failures: Explanation with Markdown table and upcoming practice intro.
   - 1 failure / hint: Empathetic contrast hint/rule-of-thumb.
   - 2+ failures: Step-by-step deconstruction and simplified retry.
4. Deterministic heuristic fallback when LLM is unavailable or times out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from goalcoach.application.agent_history import format_agent_history
from goalcoach.domain.enums import TeachingActionKind
from goalcoach.domain.models import LearnerState, TeachingAction
from goalcoach.infrastructure.llm.pydantic_ai_models import (
    LLMUnavailableError,
    get_openrouter_model,
    get_output_retries,
    run_with_fallback,
)
from goalcoach.infrastructure.persistence.content_service import ContentService

logger = logging.getLogger(__name__)


@dataclass
class TeachingDeps:
    """Dependencies injected into the Teaching Agent per-turn."""

    state: LearnerState
    content_service: ContentService
    concept_id: str
    failed_attempts: int = 0
    learner_query: str | None = None
    excluded_exercise_id: str | None = None


TEACHING_SYSTEM_PROMPT = """You are Coach Baobao, the warm, encouraging, and adaptive Chinese Tutor.
You teach strictly within verified curriculum boundaries with clarity, empathy, and high pedagogical precision.

Core Pedagogical Philosophy:
Same concept + different error history -> different instructional action.
- Tone & Persona: Supportive, observant, and humane. Greet the learner warmly, celebrate their efforts, and explain grammatical ideas in simple, intuitive terms. Avoid cold, robotic statements.
- Strict Grounding: The student is about to practice an actual target exercise (specified in Target Upcoming Practice). Your explanation or guidance MUST directly bridge to and prepare the student for this specific practice task!
- Never Abandon the Learner: Even during retries or multiple failures, NEVER throw a naked exercise without guidance. Always deconstruct the concept step-by-step with empathy.

Cross-Session Continuity:
- Treat the compact learning history as advisory evidence, not a ban on repetition.
- Do not reproduce prior wording or an identical exercise unless repetition is pedagogically justified.
- When revisiting a concept, adapt the explanation or practice using its prior outcome and errors.

Goal Grounding:
- The learner's complete free-form goal is the authoritative teaching context.
- Interpret that goal directly when choosing examples and communicative situations.
- Do not invent material beyond the verified curriculum cards and exercises.

Pedagogical Modality Rules:
1. Fresh Encounter (failed_attempts == 0):
   - Choose `EXPLANATION` or `DIALOGUE`.
   - Provide a warm, conversational intro connecting to the communicative goal.
   - Present the target vocabulary or sentence structure using a clear Markdown table with the exact columns:
     | Character | Pinyin | Meaning |
     | :--- | :--- | :--- |
     | <Hanzi> | <tone-marked pinyin> | <English meaning> |
   - Provide 1 natural example sentence tailored to the upcoming practice task and learner interests (e.g. food, travel, business).
   - Conclude with an encouraging prompt introducing the upcoming practice.

2. Help Requested / Clarification Query / Confusion:
   - If the student asked a specific question or requested clarification (`learner_query`), your TOP PRIORITY is to directly, warmly, and accurately answer their question in `content`. Explain the nuance, character breakdown, pronunciation, or grammar rule they are confused about before connecting back to the practice.
   - Choose `HINT`, `EXPLANATION`, or `CONTRAST_EXAMPLE` depending on what best clarifies their confusion.
   - If no specific question was asked (general confusion or failed practice attempt), validate the student's effort empathetically, highlight the contrast, and give an intuitive rule-of-thumb.
   - Do NOT just repeat the earlier explanation or give away the practice answer directly.

3. Multiple Failures (failed_attempts >= 2):
   - Choose `RETRY` or `CONTRAST_EXAMPLE`.
   - Provide gentle, compassionate scaffolding: break down the target pattern into tiny bite-sized pieces (e.g., Subject + Verb + Particle).
   - Reassure the learner, highlight where the key word goes, and guide them into the retry.

Output Format:
Emit a structured `TeachingAction` containing:
- `action_kind`: The chosen modality tag.
- `concept_id`: The canonical concept tag being taught.
- `content`: The teaching text shown to the student. For explanations, it MUST include the Markdown table (`| Character | Pinyin | Meaning |`).
- `history_summary`: A self-contained semantic summary, in one or two complete English sentences and no more than 240 characters. State what was taught, the teaching strategy, and the practice objective. Do not merely copy the beginning of `content`.
- `pinyin`: Tone-marked Pinyin for any Chinese characters.

Language Requirements (STRICT):
- Instructional Medium: English ONLY unless the learners ask you to teach in other languages. All grammar explanations, instructions, guidelines, hints, structural breakdowns, and feedback MUST be written in English.
- Target Language: Mandarin Chinese. Chinese characters (Hanzi) and Pinyin are ONLY permitted as specific vocabulary examples, patterns, or target exercise items—NEVER as the explanatory language.

Explanation and exercises should be strongly relevant.
"""

teaching_agent = Agent(
    model=get_openrouter_model(),
    deps_type=TeachingDeps,
    output_type=TeachingAction,
    output_retries=get_output_retries(),
    system_prompt=TEACHING_SYSTEM_PROMPT,
)


@teaching_agent.tool
def get_concept_teaching_cards(
    ctx: RunContext[TeachingDeps], concept_id: str
) -> list[dict[str, Any]]:
    """Fetch verified vocabulary/grammar cards for the active concept from SQLite Database #1."""
    cards = ctx.deps.content_service.get_teaching_cards(concept_id)
    return [
        {
            "card_id": card.card_id,
            "concept_id": card.concept_id,
            "prompt_zh": card.prompt_zh,
            "pinyin": card.pinyin,
            "meaning_en": card.meaning_en,
            "example_zh": card.example_zh,
            "example_pinyin": card.example_pinyin,
            "example_en": card.example_en,
            "explanation_en": card.explanation_en,
        }
        for card in cards
    ]


@teaching_agent.tool
def get_concept_details(ctx: RunContext[TeachingDeps], concept_id: str) -> dict[str, Any]:
    """Fetch metadata and curriculum sequencing for the active concept."""
    concept = ctx.deps.content_service.get_concept(concept_id)
    if not concept:
        return {}
    return {
        "concept_id": concept.concept_id,
        "title_zh": concept.title_zh,
        "title_en": concept.title_en,
        "difficulty": concept.difficulty,
        "communicative_goal": concept.communicative_goal,
        "grammar_focus": concept.grammar_focus,
        "vocabulary_focus": concept.vocabulary_focus,
    }


class TeachingWorker:
    """Wrapper class managing the execution, fallback, and exercise attachment for teaching."""

    def __init__(self, agent: Agent = teaching_agent) -> None:
        self.agent = agent

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
        """Invokes the Teaching Agent with deterministic heuristic fallback."""
        candidate_exercise = self._select_candidate_exercise(
            concept_id,
            content_service,
            state=state,
            is_remedial=(failed_attempts > 0),
            excluded_exercise_id=excluded_exercise_id,
            target_exercise_id=target_exercise_id,
        )

        deps = TeachingDeps(
            state=state,
            content_service=content_service,
            concept_id=concept_id,
            failed_attempts=failed_attempts,
            learner_query=learner_query,
            excluded_exercise_id=excluded_exercise_id,
        )

        relevant_errors = [err.code for err in state.error_profile if err.concept_id == concept_id]
        history_summary = format_agent_history(state)
        interests_str = ", ".join(state.context_interests) if state.context_interests else "general"

        cards = content_service.get_teaching_cards(concept_id)
        cards_summary = []
        for card in cards:
            example = (
                f" Example: {card.example_zh} ({card.example_pinyin}) - {card.example_en}"
                if card.example_zh
                else ""
            )
            explanation = f" Note: {card.explanation_en}" if card.explanation_en else ""
            cards_summary.append(
                f"- {card.prompt_zh} ({card.pinyin}): {card.meaning_en}.{example}{explanation}"
            )
        cards_text = (
            "\n".join(cards_summary) if cards_summary else "No pre-compiled vocabulary cards."
        )

        concept = content_service.get_concept(concept_id)
        concept_info = ""
        if concept:
            concept_info = (
                f"Concept Title: {concept.title_zh} ({concept.title_en})\n"
                f"Communicative Goal: {concept.communicative_goal}\n"
                f"Grammar Focus: {concept.grammar_focus}\n"
                f"Vocabulary Focus: {concept.vocabulary_focus}\n"
            )

        options_hint = ""
        if getattr(candidate_exercise, "options", None):
            options_hint = f"\nUpcoming Practice Options: {candidate_exercise.options}"

        ex_type = getattr(candidate_exercise, "exercise_type", "mcq")

        query_instruction = ""
        if learner_query:
            query_instruction = (
                f'\nCRITICAL LEARNER QUESTION: The student specifically asks: "{learner_query}".\n'
                "You MUST directly, warmly, and clearly answer this exact question first in your explanation before bridging back to the practice.\n"
            )

        length_constraint = (
            "2. Keep it concise (under 100 words when answering a learner query or fresh explanation, under 45 words for quick hints/retries). Do NOT write long essays."
            if learner_query
            else "2. Keep it ultra-concise (under 60 words for fresh explanations, under 40 words for hints/retries). Do NOT write long essays."
        )

        prompt = (
            f"Active Concept: {concept_id}\n"
            f"{concept_info}"
            f"Learner Goal: {state.goal.title if state.goal else 'General HSK1 Chinese'}\n"
            f"Failed Attempts on this concept: {failed_attempts}\n"
            f"Recurring Error Codes: {relevant_errors}\n"
            f"Verified Curriculum Teaching Cards:\n{cards_text}\n"
            f"Target Upcoming Practice Type: {ex_type}\n"
            f"Target Upcoming Practice: {candidate_exercise.instruction or ''} -> {candidate_exercise.prompt}"
            f"{options_hint}\n"
            f"Learner Interests: {interests_str}\n"
            f"Learner Query / Context: {learner_query or 'Normal lesson progression'}\n"
            f"Exercise to replace: {excluded_exercise_id or 'None'}\n"
            f"Recent Cross-Session Learning History:\n{history_summary}\n"
            f"{query_instruction}"
            "Emit the optimal TeachingAction for this turn. Ground your explanation or guidance directly to help the student succeed on this upcoming practice task.\n"
            "CRITICAL:\n"
            "1. Write all explanations and conversational text in ENGLISH. Do not explain in Chinese.\n"
            f"{length_constraint}"
        )

        try:
            result, provider = await run_with_fallback(
                self.agent,
                prompt,
                deps=deps,
                component="teaching_agent",
            )
            action: TeachingAction = result.output
            action.metadata.update(
                {
                    "provider": provider,
                    "fallback_used": provider.startswith("ollama:"),
                    "notice": (
                        "The primary model was unavailable; the configured fallback model was used."
                        if provider.startswith("ollama:")
                        else None
                    ),
                }
            )
            if learner_query:
                action.metadata["learner_query"] = learner_query
            if action.concept_id == concept_id and action.content:
                return self._attach_selected_exercise(action, candidate_exercise)
        except LLMUnavailableError as exc:
            action = self._deterministic_fallback(
                concept_id,
                state,
                content_service,
                failed_attempts,
                learner_query=learner_query,
                candidate_exercise=candidate_exercise,
                notice=f"LLM unavailable; deterministic teaching fallback used: {exc}",
            )
            return self._attach_selected_exercise(action, candidate_exercise)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "TeachingAgent LLM execution failed (%s); using heuristic fallback.", exc
            )
            action = self._deterministic_fallback(
                concept_id,
                state,
                content_service,
                failed_attempts,
                learner_query=learner_query,
                candidate_exercise=candidate_exercise,
                notice="Teaching Agent returned an invalid action; deterministic fallback used.",
            )
            return self._attach_selected_exercise(action, candidate_exercise)

        action = self._deterministic_fallback(
            concept_id,
            state,
            content_service,
            failed_attempts,
            learner_query=learner_query,
            candidate_exercise=candidate_exercise,
            notice="Teaching Agent returned an invalid action; deterministic fallback used.",
        )
        return self._attach_selected_exercise(action, candidate_exercise)

    @staticmethod
    def _select_candidate_exercise(
        concept_id: str,
        content_service: ContentService,
        state: LearnerState | None = None,
        is_remedial: bool = False,
        excluded_exercise_id: str | None = None,
        target_exercise_id: str | None = None,
    ) -> Any:
        """Select the target exercise before agent invocation to ensure grounded teaching."""
        all_exercises = content_service.get_exercises_for_concept(
            concept_id,
            limit=10,
            randomize=False,
        )
        if not all_exercises:
            raise LookupError(f"No curriculum exercise found for concept {concept_id}")

        if target_exercise_id:
            for ex in all_exercises:
                if str(ex.exercise_id) == str(target_exercise_id):
                    return ex

        completed = set(state.today_completed_exercise_ids) if state else set()
        mistakes = set(state.today_mistake_exercise_ids) if state else set()
        recent = (
            {
                turn.exercise_id
                for turn in state.agent_history.recent_teaching_turns
                if turn.concept_id == concept_id and turn.exercise_id
            }
            if state and hasattr(state, "agent_history") and state.agent_history
            else set()
        )
        excluded = {excluded_exercise_id} if excluded_exercise_id else set()

        if is_remedial:
            candidates = [
                e
                for e in all_exercises
                if e.exercise_id not in completed
                and e.exercise_id not in mistakes
                and e.exercise_id not in recent
                and e.exercise_id not in excluded
            ]
            if not candidates:
                candidates = [
                    e
                    for e in all_exercises
                    if e.exercise_id not in completed
                    and e.exercise_id not in recent
                    and e.exercise_id not in excluded
                ]
            if not candidates:
                candidates = [
                    e
                    for e in all_exercises
                    if e.exercise_id not in completed and e.exercise_id not in excluded
                ]
            return candidates[0] if candidates else all_exercises[0]
        else:
            uncompleted = [
                e
                for e in all_exercises
                if e.exercise_id not in completed
                and e.exercise_id not in recent
                and e.exercise_id not in excluded
            ]
            if not uncompleted:
                uncompleted = [
                    e
                    for e in all_exercises
                    if e.exercise_id not in completed and e.exercise_id not in excluded
                ]
            return uncompleted[0] if uncompleted else all_exercises[0]

    @staticmethod
    def _attach_selected_exercise(
        action: TeachingAction,
        selected: Any,
    ) -> TeachingAction:
        """Attach a canonical exercise without exposing its accepted answers."""
        payload = dict(action.exercise_payload or {})
        payload.update(
            {
                "exercise_id": selected.exercise_id,
                "concept_id": selected.concept_id,
                "exercise_type": getattr(selected, "exercise_type", "unknown"),
                "prompt": selected.prompt,
                "instruction": selected.instruction or "",
                "options": getattr(selected, "options", None),
            }
        )
        action.exercise_payload = payload
        return action

    @staticmethod
    def _deterministic_fallback(
        concept_id: str,
        state: LearnerState,
        content_service: ContentService,
        failed_attempts: int,
        learner_query: str | None = None,
        candidate_exercise: Any | None = None,
        *,
        notice: str = "Deterministic teaching fallback used.",
    ) -> TeachingAction:
        """Build a transparent fallback solely from canonical curriculum material."""
        concept = content_service.get_concept(concept_id)
        cards = content_service.get_teaching_cards(concept_id)
        title_zh = concept.title_zh if concept else "你好"
        title_en = concept.title_en if concept else "Hello"

        card = cards[0] if cards else None
        pinyin = card.pinyin if card and card.pinyin else "nǐ hǎo"
        example_zh = card.example_zh if card and card.example_zh else title_zh
        example_pinyin = card.example_pinyin if card and card.example_pinyin else pinyin
        example_en = card.example_en if card and card.example_en else title_en

        metadata: dict[str, Any] = {
            "provider": "deterministic",
            "fallback_used": True,
            "notice": notice,
        }
        if learner_query:
            metadata["learner_query"] = learner_query

        if learner_query:
            content = (
                f'You asked: *"{learner_query}"*\n\n'
                f"Here is a key clarification on **{title_zh}** ({title_en}):\n\n"
                f"• Focus item: **{example_zh}** ({example_pinyin}) — {example_en}\n"
                f"• Coach tip: In Chinese sentence structure, pay close attention to the meaning of individual characters and word order.\n\n"
                f"Keep this rule in mind as you try the practice task below!"
            )
            action_kind = TeachingActionKind.HINT
            strategy = f"a targeted clarification for '{learner_query}'"
        elif failed_attempts == 0:
            is_matching = getattr(candidate_exercise, "exercise_type", "") == "matching"
            if is_matching:
                content = (
                    f"Let's learn **{title_zh}** ({title_en})!\n\n"
                    f"Review the key words below, then connect each numbered Chinese word with its English meaning."
                )
            else:
                content = (
                    f"Let's explore **{title_zh}** ({title_en})!\n\n"
                    f"| Hanzi | Pinyin | Meaning |\n"
                    f"| :--- | :--- | :--- |\n"
                    f"| {example_zh} | {example_pinyin} | {example_en} |\n\n"
                    f"Let's put this into practice below!"
                )
            action_kind = TeachingActionKind.EXPLANATION
            strategy = "a concise canonical explanation"
        elif failed_attempts == 1:
            content = (
                f"Good effort! Let's look at **{title_zh}** from a slightly different angle.\n\n"
                f"**Coach Tip:** In Chinese, sentence structure often stays very straightforward. "
                f"Pay attention to the key words and particles:\n\n"
                f"• Focus item: **{example_zh}** ({example_pinyin}) — {example_en}\n"
                f"• Rule of thumb: Check the exact word order and meaning before answering.\n\n"
                f"Take a breath and give it another try below!"
            )
            action_kind = TeachingActionKind.CONTRAST_EXAMPLE
            strategy = "a different curriculum example after confusion"
        else:
            content = (
                f"Don't worry, mastering Chinese takes patience! Let's break this down step-by-step.\n\n"
                f"For this question, remember:\n"
                f"1. What are we looking for? **{title_zh}** ({title_en}).\n"
                f"2. Look for the key element: `{example_zh}` ({example_pinyin}).\n\n"
                f"You've got this! Choose or complete the correct option below."
            )
            action_kind = TeachingActionKind.RETRY
            strategy = "a simplified retry grounded in one curriculum example"

        return TeachingAction(
            action_kind=action_kind,
            concept_id=concept_id,
            content=content,
            history_summary=f"Taught {title_en} using {strategy} for the learner goal.",
            pinyin=example_pinyin,
            metadata=metadata,
        )


# --- Legacy Compatibility Interface ---


class TutorResponse(BaseModel):
    """Backwards-compatible legacy tutor response structure."""

    reply: str = Field(default="", description="Explanations, exercises, or feedback with Pinyin")
    grammar_points: list[str] = Field(default_factory=list)
    suggested_practice: str | None = Field(default=None)
    concept_id: str = Field(default="hsk1_c01")
    is_evaluating_answer: bool = Field(default=False)
    passed: bool | None = Field(default=None)
    hint_given: bool = Field(default=False)


tutor_agent = Agent(
    model=get_openrouter_model(),
    output_type=TutorResponse,
    output_retries=get_output_retries(),
    system_prompt="You are the GoalCoach Chinese Teacher, an adaptive HSK1 Chinese tutor.",
)


async def chat_with_tutor(deps: Any, user_message: str) -> tuple[TutorResponse, str]:
    """Legacy helper for conversational tutoring."""
    result, provider = await run_with_fallback(tutor_agent, user_message, deps=deps)
    return result.output, provider


__all__ = [
    "TEACHING_SYSTEM_PROMPT",
    "TeachingDeps",
    "TeachingWorker",
    "TutorResponse",
    "chat_with_tutor",
    "teaching_agent",
    "tutor_agent",
]
