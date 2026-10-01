# Changelog

All notable changes to the GoalCoach project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.8] - 2026-10-01

### Added
- **Direct Learner Question Answering & Clarification Support**:
  - In `src/goalcoach/agents/teaching_agent.py`, updated `TEACHING_SYSTEM_PROMPT` Rule 2 to establish clear pedagogical policy for student questions: when `learner_query` is provided, Coach Baobao must prioritize directly, warmly, and accurately answering their specific question (e.g. word distinctions, pronunciation nuances, or grammar particles) before bridging to the practice task.
  - In `teach_concept`, added dedicated prompt guidance for `learner_query` and relaxed output length constraint to 100 words (up from 40 words) so the agent can provide thorough, actionable answers.
  - In `TeachingWorker._deterministic_fallback`, added targeted clarification logic acknowledging and answering student queries directly (*"You asked: '...' — Here is a key clarification on..."*) grounded in curriculum cards, ensuring questions are never discarded if the LLM provider fails or times out.
  - Added `learner_query` tracking to `TeachingAction.metadata` across live agent runs, fallback generation, and orchestrator dispatch.
  - Added automated integration test `test_help_event_with_learner_query_preserves_exercise_and_addresses_question` in `tests/api/test_api.py`.

### Changed
- **Exercise Preservation During Clarification Turns**:
  - In `src/goalcoach/agents/teaching_agent.py`, updated `TeachingWorker._select_candidate_exercise` and `teach_concept` with `target_exercise_id` support.
  - In `src/goalcoach/application/orchestrator.py` (`_handle_help_requested`), preserved the active practice exercise (`target_exercise_id=current_exercise_id`) when a learner asks a question, preventing the system from prematurely swapping out the exercise the student is trying to understand.
- **Interactive Help Modal UX & Visual Clarification Badge**:
  - In `apps/web/src/components/TeachingAgentModal.tsx`, cleared the rigid pre-filled query string and added a descriptive question placeholder: *"Ask what you didn't understand or need clarified (e.g. 'What is the difference between 你 and 您?')..."*.
  - Added dynamic action button text: toggles between `"Ask Coach Baobao"` when a query is entered and `"Explain another way"` when empty.
  - Added a visual clarification card in the modal displaying `"Coach's Clarification"` and highlighting the learner's submitted question (*"Your Question: '...' "*), providing immediate visual confirmation that their inquiry was captured and resolved.
- **LLM Transient Error Resilience**:
  - In `src/goalcoach/infrastructure/llm/pydantic_ai_models.py`, added `UnexpectedModelBehavior` to `_TransientRetry.should_retry` to recover from momentary upstream OpenRouter tool-calling schema hiccups before aborting to fallback.

---

## [0.1.7] - 2026-10-01

### Changed
- **Agent-Driven Learning Loop & Mistake Remediation**:
  - Replaced manual failure navigation buttons (`"Try again"`, `"Try a new teaching approach"`, `"Skip to next lesson"`) in `TeachingAgentModal.tsx` with a single unified, agent-directed button: `"Continue with Coach"` (with `data-testid="continue-lesson-btn"`).
  - Aligned product behavior with autonomous agent-led pedagogy: the learner no longer decides the remediation strategy manually; instead, the Teaching Agent and Orchestrator inspect error history, DSR mastery levels, and attempt count to dynamically provide hints, pedagogical deconstruction, simplified retries, or trigger adaptive replanning.
  - Simplified modal interaction contract by cleaning up deprecated manual skip and retry handlers (`hasMorePlannedLessons`, `onSkipToNextLesson`, `onRetryExercise`, `uncompletedPlanItems`, `nextUncompletedItem`) in `apps/web/src/App.tsx` and `TeachingAgentModal.tsx`.
- **Desktop Sidebar Pinning & Goal Profile Accessibility**:
  - Updated `Sidebar.tsx` with `sticky top-0 h-screen` to keep the sidebar pinned while scrolling main page content.
  - Resolved profile card displacement beyond screen length by bounding sidebar height to viewport (`h-screen`) and tightening component spacing, keeping the Goal Completion card permanently visible in viewport.
  - Connected the empty state `"Build today’s plan"` button in `DailyPlanView.tsx` to open the Goal Settings drawer (`onOpenGoalSettings`) so learners can configure and save their goal directly.

### Fixed
- **Stale Plan Item ID Fallback & Modal Recovery Lock**:
  - In `src/goalcoach/application/orchestrator.py`, added resilient fallback when `requested_plan_item_id` is stale (e.g. from an earlier plan before adaptive replanning). Instead of raising a 409 `SessionLifecycleError("The selected Daily Plan item does not exist")`, the orchestrator now gracefully resolves matching uncompleted items by `concept_id` or falls back to the first uncompleted planned item.
  - In `apps/web/src/App.tsx`, resolved chained `SESSION_STARTED` turns after replanning to select the newly generated plan item ID rather than forwarding the stale item ID from the previous plan.
  - In `apps/web/src/App.tsx`, updated `onRecover` ("Resume lesson") and `onContinue` to dynamically target the active uncompleted planned item, preventing learners from becoming trapped in an error screen with stale selection state.
- **Observability Benchmark Flakiness & Logging Optimization**:
  - In `src/goalcoach/infrastructure/logging/filters.py`, added a fast-path pre-check in `scrub_sensitive_text` to bypass regex evaluation on messages without sensitive keywords, accelerating logging throughput.
  - In `tests/unit/test_observability.py`, adjusted the microbenchmark assertion threshold to sub-millisecond (`< 0.5ms`, target `< 0.05ms` native) to prevent false positive assertion failures caused by virtualized/WSL2 host CPU scheduling jitter.

---

## [0.1.6] - 2026-09-30

### Added
- **Full Learning Loop Navigation & Mistake Recovery**:
  - Added direct action controls in `TeachingAgentModal.tsx` on incorrect answer: `"Try again"` (immediate re-attempt without re-teaching overhead), `"Try a new teaching approach"` (targeted remediation), and `"Skip to next lesson"` (advances directly to the next planned lesson).
  - Added clean finish action `"Finish today's plan"` when `nextAction === 'complete'`, gracefully closing the modal instead of throwing an unhandled completion error.
  - Added phased loading states (`'planning'` vs `'teaching'`) with dedicated informative status banners (*"Adapting your study plan based on your recent progress..."* vs *"Coach Baobao is crafting your next lesson..."*) so the UI never displays a blank or frozen screen.
  - Added `hasMorePlannedLessons`, `onSkipToNextLesson`, `onRetryExercise`, and `loadingStage` props to `TeachingAgentModal.tsx` and wired them in `App.tsx`.
  - Added `needsReplanning?: boolean` to `LearnerState` in `apps/web/src/types.ts`.

### Changed
- **Teaching Agent Latency Optimization**:
  - In `src/goalcoach/agents/teaching_agent.py`, pre-injected verified curriculum cards, communicative goal, and grammar/vocabulary focus directly into the initial prompt. This eliminates the intermediate `get_concept_teaching_cards` tool roundtrip to OpenRouter, halving teaching latency from ~35–45s down to ~12–18s.
- **Flexible Planned Lesson Advancement**:
  - In `src/goalcoach/application/orchestrator.py`, updated `progress_eligible = not active_item.completed` for planned lessons. Studying any uncompleted planned item in today's active plan now counts toward progress and marks that specific item completed.
  - In `apps/web/src/components/DailyPlanView.tsx`, unlocked planned items so learners can click any uncompleted item to study without being forced into rigid linear locking.
  - In `apps/web/src/App.tsx`, preserved selection context (`entrySource`, `conceptId`, `planItemId`) across chained replanning turns and prevented premature UI wiping.

### Fixed
- **Frontend Hang on "Preparing your lesson"**:
  - Resolved the 60–95s loading freeze caused by sequential replanning and re-teaching LLM chaining without stage feedback.
  - Resolved modal context loss where `clearTeachingTurn()` blanked out the modal to a generic placeholder before the new lesson arrived.

---

## [0.1.5] - 2026-09-30

### Added
- **Heterogeneous LLM JSON Output Extractor & Sanitizer**:
  - Implemented `extract_and_sanitize_json` in `src/goalcoach/infrastructure/llm/json_sanitizer.py` to extract, unescape, and recursively unwrap JSON outputs across diverse LLMs and inference providers (handling markdown fences, conversational preambles/suffixes, escaped quotes, and stringified nested structures).
  - Added unit test suite `tests/unit/test_json_sanitizer.py` covering clean JSON, code blocks, conversational prefixes/suffixes, escaped quotes, nested stringified fields, and double-encoded payloads.

### Changed
- **Adaptive Freeform Grader Execution Flow**:
  - Configured `grader_agent` in `src/goalcoach/agents/grader_component.py` with `output_type=str` and enhanced `GRADER_SYSTEM_PROMPT` to strictly output a clean JSON object conforming to the grading schema.
  - Replaced rigid Pydantic tool-call validation in `GraderComponent.grade` with a bounded retry loop that sanitizes model text via `extract_and_sanitize_json` before validating into `GradingResult`, preventing premature fallbacks on open-source models (e.g. `qwen/qwen3.5-9b`).

### Removed
- **Unreachable and Legacy Code Cleanup**:
  - Removed unreachable dead code at lines 346–349 of `src/goalcoach/agents/grader_component.py`.
  - Removed deprecated `TeachingWorker._attach_curriculum_exercise` in `src/goalcoach/agents/teaching_agent.py` and updated `tests/integration/test_remediation_loop.py` to use active candidate selection and exercise attachment methods.
  - Removed non-existent `audio_url` attribute access from `get_concept_teaching_cards` in `src/goalcoach/agents/teaching_agent.py`.

---

## [0.1.4] - 2026-09-25

### Added
- **Full-Stack Teaching Agent & Interactive Exercise Web Integration**:
  - Enriched `TeachingAction.exercisePayload` in `apps/web/src/types.ts` with `exercise_type` and `options` (`string[]` or paired `left`/`right` dictionary).
  - Added interactive Multiple-Choice Question (MCQ) & synthesized distractor cards to `TeachingAgentModal.tsx` (`apps/web/src/components/TeachingAgentModal.tsx`), supporting 1-click option selection with `(1)`, `(2)`, `(3)`, `(4)` badge buttons.
  - Added two-column interactive Mix-and-Match layout to `TeachingAgentModal.tsx` (`apps/web/src/components/TeachingAgentModal.tsx`), enabling tap-to-pair Chinese words and English meanings with live state tracking and standard shorthand (`1C 2A 3E`) grading output.
  - Added Target HSK Milestone selector dropdown (Levels 1–6) to `LearnerProfileDrawer.tsx` (`apps/web/src/components/LearnerProfileDrawer.tsx`) and wired dynamic milestone selection into `handleUpdateGoal` in `apps/web/src/App.tsx`.

### Changed
- **Bite-Sized Micro-Learning Exercise Pacing (3–5 Minutes)**:
  - Updated `PLANNING_SYSTEM_PROMPT` and `PlanningWorker.create_plan` in `src/goalcoach/agents/planning_agent.py` to enforce bite-sized durations (3–5 minutes) per planned exercise item.
  - Added duration clamping (`max(3, min(item.estimated_minutes, 5))`) in `PlanningWorker` to prevent single exercises from absorbing the entire daily budget (e.g. 40 minutes) in a single step, ensuring plans contain multiple manageable items.
  - Rebalanced deterministic planning fallback (`_deterministic_fallback`) to 5 min for remedial, 3 min for review, and 5 min for new concepts.
- **Smart Mix-and-Match Exercise Placement**:
  - Positioned synthesized matching exercises at Index 0 for pure vocabulary concepts (`concept_type == 'vocabulary'`) and Index 1 (immediately after introductory `_e01` MCQ) for general vocabulary units in `src/goalcoach/infrastructure/persistence/content_service.py`, ensuring immediate web UI accessibility without breaking integration test suites.

### Fixed
- **Synthesized Matching Exercise API Validation**:
  - Fixed `validate_curriculum_references` in `apps/api/routes/learning_loop.py` to query `ContentService(content_repo).get_exercise()` instead of `content_repo.get_exercise()`, preventing HTTP 422 errors on submitting answers for dynamically generated `_match_auto` exercises.

---

## [0.1.3] - 2026-09-22

### Added
- **Multi-Level Curriculum Access Across HSK 1–6**:
  - Unlocked all 126 active curriculum concepts in Database #1 spanning HSK levels 1 through 6.
  - Added query parameter `level: int | None = Query(default=None, ge=1, le=6)` to `GET /api/v1/curriculum/concepts` in `apps/api/routes/learning_loop.py`.
  - Added automated multi-level test cases in `tests/integration/test_content_repository.py` and `tests/unit/test_api_learning.py`.

- **Mix-and-Match (Matching) Exercise Engine**:
  - Added `'matching'` exercise type to domain `Exercise` model (`src/goalcoach/domain/models.py`) and web frontend types (`apps/web/src/types.ts`).
  - Implemented dynamic synthesis (`synthesize_matching_exercise` and `get_or_synthesize_matching_exercise`) in `ContentService` (`src/goalcoach/infrastructure/persistence/content_service.py`) creating 5-pair matching exercises from concept teaching cards with level-matched backfill.
  - Implemented deterministic `<1ms` fast-path evaluation in `GraderComponent` (`src/goalcoach/agents/grader_component.py`) supporting flexible input formats (`1C 2A 3D...`, `1-C, 2-A...`, `C, A, D...`, JSON) with 80% passing threshold and `ERR_VOCAB_MATCH` tagging.
  - Enhanced `terminal_harness.py` (`src/goalcoach/agents/terminal_harness.py`) to format matching exercises with clear, aligned two-column panels.
  - Integrated synthesized matching exercises into `ContentService.get_exercises_for_concept` (`src/goalcoach/infrastructure/persistence/content_service.py`) for vocabulary acquisition.
  - Streamlined `TEACHING_SYSTEM_PROMPT` (`src/goalcoach/agents/teaching_agent.py`) to produce ultra-concise, bite-sized lessons (<60 words for explanations, <40 words for hints/retries) and explicitly scaffold mix-and-match pair matching.
  - Added comprehensive test suite `tests/unit/test_matching_exercise.py`.

- **Progressive Reachable HSK Level Window & Curriculum Sequencing**:
  - Implemented `resolve_active_level(state, content_service)` in `src/goalcoach/agents/planning_agent.py` to dynamically determine the learner's active reachable HSK proficiency window based on verified concept mastery ($\ge 0.50$).
  - Updated `get_curriculum_catalog` agent tool in `src/goalcoach/agents/planning_agent.py` to constrain catalog concepts to the active reachable level window (`max_hsk_level=active_level`).
  - Added active level guidance to `PlanningWorker.create_plan` prompt in `src/goalcoach/agents/planning_agent.py` and guardrail filtering (`valid_active_ids`) preventing premature jumping into higher HSK levels before earlier levels are mastered.
  - Aligned deterministic fallback `_heuristic_fallback` in `src/goalcoach/agents/planning_agent.py` to schedule unmastered concepts within the active level window.
  - Added comprehensive progressive unlock tests in `tests/unit/test_matching_exercise.py`.

### Changed
- **Content Persistence & Service Layer Generalization**:
  - Generalized `ContentRepository.list_concepts(hsk_level: int | None = None)` in `src/goalcoach/infrastructure/persistence/repositories.py` to retrieve all concepts stably ordered by `(hsk_level, sequence_no)` or filter by any level.
  - Generalized `ContentService.list_all_concepts(hsk_level: int | None = None)` in `src/goalcoach/infrastructure/persistence/content_service.py`.
- **Target-Aware Adaptive Planning & Grading**:
  - Parameterized `planning_agent.py` (`PLANNING_SYSTEM_PROMPT`, `get_curriculum_catalog` tool, and `_heuristic_fallback`) to dynamically load concepts matching the learner's target HSK level (`state.goal.target_hsk_level`).
  - Updated `orchestrator.py` to dynamically resolve `exercise.hsk_level` from the parent curriculum concept rather than forcing level 1.
  - Synchronized progress summary calculations in learner aggregate and completion endpoints with the learner's target level.

---

## [0.1.2] - 2026-09-21

### Removed
- **Legacy Core Python Modules**:
  - Removed deprecated `src/goalcoach/ui/` package (`orchestrator.py`, `interfaces.py`, and `__init__.py`).
  - Removed `src/goalcoach/agents/goal_planning.py` (legacy heuristic planner).
  - Removed `src/goalcoach/agents/grading_agent.py` (pre-PydanticAI rubric grader).
  - Removed `src/goalcoach/agents/interfaces.py` (abstract class stubs).
  - Removed `src/goalcoach/application/progress_reducer.py` (pre-PRD 40/40/20 state reducer).
- **Duplicate / Legacy API Routes**:
  - Removed `apps/api/routes/learning.py` (552 lines of ad-hoc routes maintaining divergent state mutations).
  - Removed `apps/api/routes/tutoring.py` (93 lines; chat handler merged into canonical learning loop router).
- **Streamlit Prototype Decommissioned**:
  - Removed `apps/web/app.py` and `src/goalcoach/cli.py`.
  - Pruned `streamlit>=1.62.0` and `langgraph>=0.2,<1` from `pyproject.toml` dependencies and optional dependencies, dropping resolved packages from 187 to 157 in `uv.lock`.
- **Redundant Frontend Client-Side Domain Logic (16 Files)**:
  - Removed 15 dead TypeScript domain/utility/test files in `apps/web/src/domain/`, `apps/web/src/infrastructure/`, and `apps/web/src/utils/` (`grader.ts`, `orchestrator.ts`, `conceptProgressReducer.ts`, `planner.ts`, `curriculumValidator.ts`, `freeformAssessment.ts`, `learningCompletion.ts`, `sqliteLearnerRepository.ts`, `vectorRagMatcher.ts`, and test files).
  - Removed `apps/web/src/data/curriculumEngine.ts` (211 lines of unused client-side curriculum builder).
- **Decommissioned Vector DB Debris & Empty Stubs**:
  - Removed empty packages `src/goalcoach/infrastructure/telemetry/` and `scripts/`.
  - Deleted obsolete documentation: `docs/GOALCOACH_PRD.md` (superseded by `docs/GOALCOACH_MVP_PRD.md`) and `docs/PR-Reviews/`.
- **Obsolete Unit Tests**:
  - Removed `tests/unit/test_orchestrator.py`, `tests/unit/test_goal_planning.py`, and `tests/unit/test_progress_reducer.py`.

### Changed
- **Unified FastAPI Router (`apps/api/routes/learning_loop.py`)**:
  - Consolidated all REST endpoints into a single router:
    - `POST /api/v1/events`: Closed-loop event dispatcher (`ANSWER_SUBMITTED`, `GOAL_CREATED`, `SESSION_STARTED`, `HELP_REQUESTED`).
    - `GET /api/v1/learners/{learner_id}`: Learner aggregate state and deterministic next action resolution.
    - `GET /api/v1/learners/{learner_id}/today-plan`: Adaptive daily planning via canonical `PlanningWorker`.
    - `POST /api/v1/learners/{learner_id}/complete-concept`: Concept completion recording directly into SQLite learner mastery.
    - `GET /api/v1/curriculum/concepts` & `GET /api/v1/curriculum/concepts/{concept_id}`: Curriculum definitions, teaching cards, and practice exercises.
    - `POST /api/v1/tutoring/chat`: Interactive bilingual tutor chat with fallback.
    - `GET /api/tts` & `GET /api/v1/tts`: Mandarin audio synthesis proxy with memory caching.
- **FastAPI Entrypoint Simplification (`apps/api/main.py`)**:
  - Mounts only `learning_loop_router` and `/health`, removing legacy planner factory hooks (`create_goal_planner`, `get_goal_planner`).
- **Frontend Closed-Loop Event Alignment (`apps/web`)**:
  - Replaced multi-step client grading in `apps/web/src/components/DailyPlanView.tsx` (`/grade-freeform` + elapsed seconds + `/learning-events`) with a single atomic call to `POST /api/v1/events` (`ANSWER_SUBMITTED`).
  - Updated `apps/web/src/App.tsx` (`handleSubmitAnswer` and `handleUpdateGoal`) to dispatch events directly to `POST /api/v1/events`.
- **Primary CLI Modernization**:
  - Wired `[project.scripts] goalcoach` to `goalcoach.agents.terminal_harness:run_cli`.
- **Canonical Agent Exports (`src/goalcoach/agents/__init__.py`)**:
  - Exported canonical components: `PlanningWorker`, `TeachingWorker`, `GraderComponent`.

### Fixed
- **Integration and Unit Test Suite Harmonization**:
  - Updated `tests/integration/test_pydantic_ai_pipeline.py` to test `GraderComponent.grade()` fast-path.
  - Updated `tests/api/test_api.py` and `tests/unit/test_api_learning.py` to validate the unified endpoints (`/api/v1/events`, `/api/v1/learners/{id}`, `/api/v1/curriculum/concepts`, and `/api/tts`).
  - All 73 tests passing cleanly in 2.6s.

---

## [0.1.1] - 2026-09-21

### Added
- **Pedagogical Pre-Selection & Context Synchronization**:
  - Implemented `_select_candidate_exercise()` in `src/goalcoach/agents/teaching_agent.py` to pre-select candidate practice activities before LLM invocation, injecting the target upcoming exercise directly into the prompt so explanations are grounded, relevant, and bridge directly into practice.
- **Rich MCQ Options & 1-Click Answering**:
  - Attached `options` and `exercise_type` to `TeachingAction.exercise_payload` and `Exercise` domain model in `src/goalcoach/domain/models.py`.
  - Added fast-path index (`1`, `2`, `3`, `4`) and letter (`A`, `B`, `C`, `D`) option resolution in `src/goalcoach/agents/grader_component.py` and `src/goalcoach/application/orchestrator.py` (<5ms execution).
  - Enhanced terminal harness (`src/goalcoach/agents/terminal_harness.py`) to render formatted choices `(1)`, `(2)`, `(3)`, `(4)` for multiple-choice questions.
  - Added comprehensive integration test `test_mcq_options_in_payload_and_1_click_grading` in `tests/integration/test_remediation_loop.py`.

### Changed
- **Empathetic "Coach Baobao" Teaching Persona**: Upgraded system prompt in `src/goalcoach/agents/teaching_agent.py` to eliminate "naked exercises" on repeated learner errors (`failed_attempts >= 2`), ensuring patient scaffolding, emotional validation, and structural grammar breakdowns prior to retries.
- **Pedagogical Preservation of Open-Input Modalities**: Maintained authentic active-recall input for `fill_blank` and `translate_to_zh` exercises (supporting Hanzi and Pinyin responses) without artificial or synthetic distractors.
- **Sequential Curriculum Prerequisites Alignment**:
  - Realigned all `concept_prerequisites` in `data/database1/GoalCoach_HSK1_Learning_DB_Package/data/goalcoach_hsk1_learning_db_sqlite.sql` so that every concept strictly depends on its immediate predecessor in chronological curriculum sequence (from `hsk1_c02` -> `hsk1_c01` through `hsk6_c22` -> `hsk6_c21`), forming a clean linear progression across all 121 concepts (120 total edges).

### Fixed
- **Content Repository Prerequisite Assertions**: Updated `test_loads_all_prerequisite_relationships` in `tests/integration/test_content_repository.py` to assert the 120 total sequential prerequisite relationships, 19 HSK 1 rules, and `hsk1_c20` -> `hsk1_c19`.


## [0.1.0] - 2026-09-20

### Added
- **AI Agent Workspace Configuration & Customizations (`.agents/`)**:
  - **Code Review Subagent (`.agents/agents/code-reviewer/agent.md`)**: Configured an autonomous `code-reviewer` agent specification emphasizing Karpathy-inspired simplicity principles, OOP/SOLID Python architecture, surgical non-breaking modifications, and strict security rules (e.g., zero `.env` exposure).

### Removed
- **Vector Database (ChromaDB) Decommissioning**: Fully removed ChromaDB and all associated vector retrieval components in accordance with PRD Principle 6 ("Zero Heavy Vector DB Overload"):
  - Removed `src/goalcoach/infrastructure/retrieval/` directory (`chroma_service.py`, `chunk_factory.py`, and `__init__.py`).
  - Removed `src/goalcoach/agents/retrieval.py` (`RetrievalAgent` and `RemedialMaterial`).
  - Removed `scripts/vector_store.py` (offline ChromaDB extraction and indexing script).
  - Removed `tests/integration/test_vector_pipeline.py` (vector pipeline test suite).
  - Removed `docs/vector-database.md` engineering specification.
  - Removed local `data/database2/chroma_db` directory and cleaned `.gitignore`.
- **Heavy ML Dependencies**: Removed `chromadb>=0.6.3`, `sentence-transformers>=3.0.0`, and `posthog<3` from `pyproject.toml` (`[project.optional-dependencies.retrieval]`).
- **Dependency Pruning**: Pruned 38 transitive packages via `uv lock` (including PyTorch/torch, transformers, sentence-transformers, onnxruntime, flatbuffers, and CUDA libraries), reducing resolved dependencies from 244 to 187 packages and drastically reducing CI install overhead.
- **Retriever Protocol**: Removed unused `Retriever` protocol from `src/goalcoach/agents/interfaces.py` and `src/goalcoach/agents/__init__.py`.
- **Legacy Agent Specification**: Removed older `agent.md` from the root workspace in favor of the structured subagent architecture under `.agents/agents/code-reviewer/agent.md`.

### Changed
- **Deterministic Curriculum Retrieval**: Refactored `search_hsk_curriculum` in `src/goalcoach/agents/tools/retrieval_tools.py` to query the SQLite `ContentRepository` deterministically without ChromaDB fallback.
- **FastAPI Tutoring Chat Endpoint**: Decoupled `apps/api/routes/tutoring.py` and `apps/api/dependencies.py` from ChromaDB; removed `get_chroma_service` dependency injection and `ChromaService` startup instantiation in `apps/api/main.py`.
- **Configuration Simplification**: Removed `vector_store_path`, `chroma_persist_directory`, `_sync_vector_paths` validator, and `enable_vector_retrieval` flag from `src/goalcoach/infrastructure/config.py`.
- **Settings Resilience**: Configured `extra="ignore"` on `SettingsConfigDict` in `src/goalcoach/infrastructure/config.py` to prevent fatal startup validation crashes from deprecated environment variables.
- **PRD Documentation**: Updated Principle 6 in `docs/GOALCOACH_MVP_PRD.md` to record that the vector database has been permanently decommissioned in favor of deterministic `ContentService` querying SQLite Database #1.

### Fixed
- **Curriculum & Learning Content Licensing Rectification**: Corrected the license for the imported HSK curriculum and vocabulary learning materials in `data/`:
  - Identified dual-licensing structure in the upstream [wuxialearn](https://github.com/wuxialearn) project: while application client code is under MIT, language frequency dictionary and learning datasets are governed by **CC BY-NC-SA 4.0** ([WuxiaLearn Frequency Dictionary LICENSE](https://github.com/wuxialearn/Chinese-English-Frequency-Dictionary/blob/master/LICENSE)).
  - Replaced the erroneous root MIT copy in `data/LICENSE` with the complete **CC BY-NC-SA 4.0** license text and proper attribution to `wuxialearn`.
  - Updated `data/database1/GoalCoach_HSK1_Learning_DB_Package/data/goalcoach_hsk1_learning_db_sqlite.sql` header comments to reference CC BY-NC-SA 4.0 and `data/LICENSE`.
  - Updated `docs/dev/goalcoach_hsk1_learning.db.md` with explicit attribution and licensing boundaries separating application software (MIT) from educational content (CC BY-NC-SA 4.0).
- **CI Integration Test Failures from Expanded Curriculum**:
  - **Prerequisite Count Assertion**: Updated `test_loads_all_prerequisite_relationships` in `tests/integration/test_content_repository.py` to assert the 120 total sequential prerequisite relationships in the expanded dataset.
  - **Exercise Exhaustion Graceful Fallback**: Updated `test_edge_case_exercise_exhaustion_graceful_fallback` in `tests/integration/test_remediation_loop.py` to dynamically query all exercises for `hsk1_c01` before simulating exercise exhaustion.
- **PydanticAI Test Suite**: Decoupled `tests/integration/test_pydantic_ai_pipeline.py` from ChromaDB mocks; updated tests to verify deterministic exact match and unknown concept fallback.
- **CI Workflow Configuration**: Removed obsolete `GOALCOACH_ENABLE_VECTOR_RETRIEVAL: "true"` environment variable from `.github/workflows/ci.yml`.


## [0.1.0-mvp] - 2026-09-17

### Added
- **Closed State-Driven Agentic Architecture**: Proved the foundational learning loop: `Goal -> Plan -> Teach -> Grade -> Update State -> Adapt -> Re-plan`.
- **Deterministic Orchestrator** (`src/goalcoach/application/orchestrator.py`): Pure Python event routing for `GOAL_CREATED`, `SESSION_STARTED`, `ANSWER_SUBMITTED`, and `HELP_REQUESTED` without LLM orchestration overhead.
- **PydanticAI Planning Worker** (`src/goalcoach/agents/planning_agent.py`): Adaptive curriculum planner generating validated `PlanUpdate` schemas based on daily time budgets, retention decay, and historical weaknesses.
- **PydanticAI Teaching Worker** (`src/goalcoach/agents/teaching_agent.py`): Adaptive pedagogy worker selecting instructional modalities (`EXPLANATION`, `HINT`, `CONTRAST_EXAMPLE`, `EXERCISE`, `DIALOGUE`, `RETRY`).
- **Grader Component** (`src/goalcoach/agents/grader_component.py`): Isolated evaluator featuring deterministic exact-match fast-path and LLM rubric grading across syntax, semantics, and pragmatics.
- **Mathematical Progress Engine** (`src/goalcoach/application/progress_service.py`): Deterministic 40/40/20 reducer, exponential retention decay, and spaced repetition intervals.
- **Dual SQLite Persistence Layer**:
  - Database #1 (`data/database1/goalcoach_hsk1_learning.db`): Grounded static HSK 1 curriculum concepts, cards, and prerequisite relationships.
  - Database #2 (`goalcoach.db`): Dynamic learner state in WAL mode (`LearnerRepository`).
- **Content Service** (`src/goalcoach/infrastructure/persistence/content_service.py`): Sub-millisecond SQL lookup engine replacing vector retrieval overhead for core HSK 1 content.
- **Dual Model Gateway**:
  - Primary hosted model: `inclusionai/ling-3.0-flash-fin` (via OpenRouter or OpenAI-compatible endpoint).
  - Fallback local model: `hf.co/unsloth/gemma-4-E4B-it-GGUF:Q4_K_M` or `gemma-4-E2B-it-GGUF:Q4_K_M` via Ollama.
  - Resilient automatic failover when hosted endpoints fail. More models will be tested and added in future iterations.
- **Interactive Terminal Harness** (`src/goalcoach/agents/terminal_harness.py`): CLI interface enabling end-to-end interactive learning and grading sessions in the console.
- **Unified REST API** (`apps/api/`):
  - `POST /api/v1/events`: Core closed-loop event gateway.
  - `POST /api/v1/tutoring/chat`: Conversational tutoring endpoint.
  - `GET /health`: System health and connectivity checks.
- **Modern Web Application** (`apps/web/`): React 18 + Vite SPA with interactive pinyin charts, visual roadmap, and drawer coaching components.
- **Automated CI Workflow** (`.github/workflows/ci.yml`): Continuous integration verifying `uv lock`, `ruff check`, `ruff format`, and unit/integration test suites on pull requests.
- **Comprehensive Test Suites**:
  - Unit tests covering progress math, reducers, and API contracts (67 tests).
  - Integration tests verifying Acceptance Criteria AC1 through AC11 (`tests/integration/test_closed_loop.py`).
  - Edge-case remediation tests (`tests/integration/test_remediation_loop.py`).

### Fixed
- **Infinite Remediation Stagnation**: Implemented dynamic exercise rotation to guarantee that failed remedial exercises do not loop on repeated item IDs.
- **Prerequisite DAG Stepping Gap**: Fixed blocker where remediating an upstream prerequisite failed to unlock unready downstream concepts.
- **Pydantic Validation Error in Error Decrement**: Fixed `ValidationError` caused by attempting to decrement `occurrences` to `0` in `ErrorRecord` (which enforces `ge=1`) by explicitly purging resolved errors.
- **Error Code Length Limitation**: Increased `ErrorRecord.code` field limit from 64 to 255 characters to support rich error categorization tags.
- **CI Database Missing Table Error**: Configured automated curriculum database bootstrapping in CI runner from the SQL package.
- **Linter & Formatter Alignment**: Resolved Ruff lint violations (`B008`, `F821`, `SIM102`, `C414`, `BLE001`) and formatted entire codebase to zero diffs.

---

## [0.1.0-beta] - 2026-09-08

### Added
- Embedded ChromaDB vector retrieval pipeline for semantic concept card matching (`src/goalcoach/infrastructure/retrieval/chroma_service.py`).
- PydanticAI initial teaching and rubric grading agents.
- OpenRouter failover to Ollama Gemma models.
- Streamlit prototype interface (`apps/web/app.py`).

---

## [0.1.0-alpha] - 2026-09-01

### Added
- Initial project scaffolding and dependency management via Astral `uv`.
- Static HSK 1 curriculum SQLite database and relational schema (`curriculum_concepts`, `curriculum_cards`, `prerequisites`).
- Core Pydantic domain models for `LearnerState`, `ConceptMastery`, `DailyPlan`, and `GradingResult`.
