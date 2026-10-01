# Changelog — 2026-10-01

Scope: Teaching Agent clarification and question-answering support (`HELP_REQUESTED`), exercise preservation, deterministic fallback enhancement, and interactive modal UX improvements.

## 1. Direct Question Answering & Pedagogical Clarification

- **Pedagogical Policy Update**:
  - Updated `TEACHING_SYSTEM_PROMPT` Rule 2 in `src/goalcoach/agents/teaching_agent.py` to establish explicit handling for student inquiries. When `learner_query` is provided, Coach Baobao must prioritize directly, warmly, and accurately resolving the student's question (explaining nuances, vocabulary distinctions, pronunciation tones, or grammatical particles) before bridging to the practice task.
- **Prompt Guidance & Word Count Relaxation**:
  - In `teach_concept`, injected an explicit instruction when `learner_query` is present.
  - Relaxed the word limit to 100 words (up from 40 words) when answering student queries, giving the agent sufficient room to explain grammatical concepts properly without truncating into cryptic hints.
- **Query Tracking in Metadata**:
  - Attached `learner_query` to `TeachingAction.metadata` across live agent runs, deterministic fallback generation, and orchestrator event handling.

## 2. Deterministic Fallback Graceful Handling

- In `TeachingWorker._deterministic_fallback`, eliminated the silent discarding of `learner_query`.
- Fallback now explicitly acknowledges the student's question (*"You asked: '...' — Here is a key clarification on..."*) and provides targeted curriculum explanations derived from SQLite Database #1 teaching cards, ensuring students always receive an explanation even during LLM provider downtime or network timeouts.

## 3. Exercise Preservation During Questions

- **Exercise Target Support**:
  - Added `target_exercise_id` parameter to `TeachingWorker._select_candidate_exercise` and `teach_concept`.
- **Orchestration Behavior**:
  - In `DeterministicOrchestrator._handle_help_requested` (`src/goalcoach/application/orchestrator.py`), retained the active practice exercise (`target_exercise_id=current_exercise_id`) when a learner asks a question, preventing the system from prematurely discarding the exercise the student is actively trying to understand.

## 4. Frontend Interactive Modal UX

- In `apps/web/src/components/TeachingAgentModal.tsx`:
  - Removed the hardcoded default query string (`"I do not understand this yet. Please explain it differently."`) from state initialization, replacing it with an intuitive placeholder.
  - Dynamic button labeling: Toggles between `"Ask Coach Baobao"` when text is entered and `"Explain another way"` when empty.
  - Added a visual clarification badge: When answering a learner query, the modal displays `"Coach's Clarification"` and quotes the learner's question (*"Your Question: '...' "*), giving clear, immediate confirmation that their inquiry was handled.

## 5. LLM Transient Error Resilience

- In `src/goalcoach/infrastructure/llm/pydantic_ai_models.py`, added `UnexpectedModelBehavior` to `_TransientRetry.should_retry` to recover from momentary upstream OpenRouter tool-calling schema hiccups before falling back.

## 6. Testing & Verification

- Added integration test `test_help_event_with_learner_query_preserves_exercise_and_addresses_question` in `tests/api/test_api.py`.
- Updated `tests/fakes.py` (`FakeTeachingWorker`) with `target_exercise_id` and query metadata.
- All 149 backend tests pass (`pytest`).
- Frontend builds cleanly (`tsc -b && vite build`) with zero type errors.
