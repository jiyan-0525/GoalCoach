import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Sidebar } from './components/Sidebar.tsx';
import { TopStatusBar } from './components/TopStatusBar.tsx';
import { BottomNav } from './components/BottomNav.tsx';
import { DailyPlanView } from './components/DailyPlanView.tsx';
import { RoadmapView } from './components/RoadmapView.tsx';
import { RetentionVisualizer } from './components/RetentionVisualizer.tsx';
import { LearnerProfileDrawer } from './components/LearnerProfileDrawer.tsx';
import { TeachingAgentModal } from './components/TeachingAgentModal.tsx';
import { LearnerState, NextAction, CurriculumConcept, GradingResult, LearningGoal, LearningLoopResponse, TeachingAction, ProgressSummary, StudyEntrySource } from './types.ts';

interface LessonSelection {
  entrySource: StudyEntrySource;
  conceptId?: string;
  planItemId?: string;
}

export function App() {
  const [learnerId] = useState('learner_001');
  const [learnerState, setLearnerState] = useState<LearnerState | null>(null);
  const [goalCompletion, setGoalCompletion] = useState(0.0);
  const [learnedProgress, setLearnedProgress] = useState(0.0);
  const [masteredProgress, setMasteredProgress] = useState(0.0);
  const [progressSummary, setProgressSummary] = useState<ProgressSummary | null>(null);
  const [nextAction, setNextAction] = useState<NextAction>('teach');
  const [concepts, setConcepts] = useState<CurriculumConcept[]>([]);
  const [roadmapCoverageRationale, setRoadmapCoverageRationale] = useState('');
  const [activeTab, setActiveTab] = useState<'plan' | 'curriculum' | 'retention'>('plan');

  const [isProfileDrawerOpen, setIsProfileDrawerOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isTeachingOpen, setIsTeachingOpen] = useState(false);
  const [teachingLoading, setTeachingLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState<'idle' | 'planning' | 'teaching'>('idle');
  const [teachingAction, setTeachingAction] = useState<TeachingAction | null>(null);
  const [teachingError, setTeachingError] = useState<string | null>(null);
  const [agentGradingResult, setAgentGradingResult] = useState<GradingResult | null>(null);
  const [agentReplanned, setAgentReplanned] = useState(false);
  const [appError, setAppError] = useState<string | null>(null);
  const activityStartedAt = useRef<number | null>(null);
  const roadmapRequestId = useRef(0);
  const teachingRequestInFlight = useRef(false);
  const lastLessonSelection = useRef<LessonSelection>({ entrySource: 'planned' });

  const clearTeachingTurn = (): void => {
    setTeachingAction(null);
    setAgentGradingResult(null);
    activityStartedAt.current = null;
  };

  const acceptLearnerState = (incoming: LearnerState) => {
    setLearnerState((current) => {
      if ((incoming.stateVersion ?? 0) < (current?.stateVersion ?? 0)) return current;
      return incoming;
    });
  };

  const goalForDisplay: LearningGoal | null = (() => {
    if (!learnerState?.goal) return null;
    return learnerState.goal;
  })();

  const parseApiError = async (response: Response, fallback: string): Promise<string> => {
    try {
      const body = await response.json() as { detail?: string | Array<{ msg?: string }> };
      if (typeof body.detail === 'string') return body.detail;
      if (Array.isArray(body.detail)) return body.detail.map((item) => item.msg).filter(Boolean).join(' ') || fallback;
    } catch {
      // The fallback below is safe for empty and non-JSON responses.
    }
    return fallback;
  };

  const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '';
  const apiUrl = (path: string): string => `${API_BASE}${path}`;

  const dispatchLearningEvent = async (
    eventType: LearningLoopResponse['eventType'],
    payload: Record<string, unknown>,
    attempt = 0,
  ): Promise<LearningLoopResponse> => {
    const response = await fetch(apiUrl('/api/v1/events'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_type: eventType, learner_id: learnerId, payload }),
    });
    if (response.status === 409) {
      const detail = await parseApiError(response, 'Your learning state changed in another request.');
      const errorCode = response.headers.get('X-GoalCoach-Error');
      if (errorCode === 'STATE_CONFLICT' && attempt === 0 && eventType !== 'ANSWER_SUBMITTED') {
        await refreshAuthoritativeState();
        return dispatchLearningEvent(eventType, payload, attempt + 1);
      }
      clearTeachingTurn();
      await refreshAuthoritativeState();
      throw new Error(`${detail} Please resume your lesson to continue.`);
    }
    if (!response.ok) {
      throw new Error(await parseApiError(response, 'GoalCoach could not complete this request.'));
    }
    return response.json() as Promise<LearningLoopResponse>;
  };

  const refreshAuthoritativeState = async (): Promise<void> => {
    const response = await fetch(apiUrl(`/api/v1/learners/${learnerId}`));
    if (!response.ok) return;
    const body = await response.json() as {
      state?: LearnerState;
      progressSummary?: ProgressSummary;
      nextAction?: NextAction;
    };
    if (body.state) acceptLearnerState(body.state);
    if (body.progressSummary) acceptProgress(body.progressSummary);
    if (body.nextAction) setNextAction(body.nextAction);
  };

  const refreshRoadmap = async (): Promise<void> => {
    const requestId = ++roadmapRequestId.current;
    const learnerResponse = await fetch(apiUrl(`/api/v1/learners/${learnerId}`));
    if (!learnerResponse.ok) {
      throw new Error(await parseApiError(learnerResponse, 'The roadmap could not be loaded.'));
    }
    const learnerBody = await learnerResponse.json() as {
      state?: LearnerState;
      progressSummary?: ProgressSummary;
    };
    if (!learnerBody.state) {
      throw new Error('The roadmap could not be loaded.');
    }
    const response = await fetch(apiUrl(`/api/v1/learners/${learnerId}/roadmap`));
    if (!response.ok) throw new Error(await parseApiError(response, 'The roadmap could not be loaded.'));
    const body = await response.json() as {
      roadmap?: CurriculumConcept[];
      roadmapCoverageRationale?: string;
      stateVersion?: number;
    };
    if (
      requestId !== roadmapRequestId.current ||
      (body.stateVersion ?? 0) !== learnerBody.state.stateVersion
    ) {
      return;
    }
    acceptLearnerState(learnerBody.state);
    if (learnerBody.progressSummary) acceptProgress(learnerBody.progressSummary);
    setConcepts(Array.isArray(body.roadmap) ? body.roadmap : []);
      setRoadmapCoverageRationale(body.roadmapCoverageRationale ?? '');
  };

  const acceptProgress = (summary: ProgressSummary): void => {
    setProgressSummary(summary);
    setGoalCompletion(summary.goalCompletion);
    setLearnedProgress(summary.learnedProgress);
    setMasteredProgress(summary.masteredProgress);
  };

  const acceptRoadmapProjection = async (
    data: LearningLoopResponse,
  ): Promise<void> => {
    if (!data.state) return;
    const response = await fetch(apiUrl(`/api/v1/learners/${learnerId}/roadmap`));
    if (!response.ok) return;
    const body = await response.json() as {
      roadmap?: CurriculumConcept[];
      roadmapCoverageRationale?: string;
      stateVersion?: number;
    };
    if ((body.stateVersion ?? 0) !== data.state.stateVersion) return;
    setConcepts(Array.isArray(body.roadmap) ? body.roadmap : []);
    setRoadmapCoverageRationale(body.roadmapCoverageRationale ?? '');
  };

  const acceptResponse = async (
    data: LearningLoopResponse,
    refreshRoadmapProjection = false,
  ): Promise<void> => {
    if (data.state) acceptLearnerState(data.state);
    if (data.progressSummary) acceptProgress(data.progressSummary);
    setNextAction(data.nextAction);
    const planningNotice = data.planUpdate?.metadata?.notice;
    if (data.planUpdate?.metadata?.fallback_used && typeof planningNotice === 'string') {
      setAppError(planningNotice);
    }
    if (refreshRoadmapProjection) await acceptRoadmapProjection(data);
  };

  const currentActivitySeconds = (): number => {
    if (activityStartedAt.current === null) return 0;
    return Math.min(
      86_400,
      Math.max(0, Math.round((Date.now() - activityStartedAt.current) / 1_000)),
    );
  };

  // Fetch initial learner state and curriculum
  useEffect(() => {
    async function init() {
      try {
        const res = await fetch(apiUrl(`/api/v1/learners/${learnerId}`));
        if (res.ok) {
          const data = await res.json();
          setNextAction(data.nextAction);
          if (data.progressSummary) acceptProgress(data.progressSummary);
          acceptLearnerState(data.state as LearnerState);
        }
        await refreshRoadmap();
      } catch (err) {
        setAppError(err instanceof Error ? err.message : 'GoalCoach could not be initialized.');
      } finally {
        setLoading(false);
      }
    }
    init();
  }, [learnerId]);

  // Replanning is an explicit backend event, never a read-only plan fetch.
  const handleRegeneratePlan = async (): Promise<void> => {
    setAppError(null);
    try {
      const data = await dispatchLearningEvent('REPLAN_REQUESTED', {
        reason: 'Learner requested a refreshed daily plan.',
      });
      await acceptResponse(data, true);
    } catch (error) {
      setAppError(error instanceof Error ? error.message : 'Today’s plan could not be refreshed.');
    }
  };

  // Handle goal update
  const handleUpdateGoal = async (updatedGoal: Partial<LearningGoal>) => {
    setAppError(null);
    setIsProfileDrawerOpen(false);
    const title = updatedGoal.title?.trim() || goalForDisplay?.title?.trim();
    const dailyMinutes = updatedGoal.dailyAvailableMinutes ?? goalForDisplay?.dailyAvailableMinutes;
    const timezone = updatedGoal.timezone ?? learnerState?.goal?.timezone
      ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? 'UTC';
    if (!title) throw new Error('Please describe your learning goal.');
    if (!Number.isInteger(dailyMinutes) || dailyMinutes! < 5 || dailyMinutes! > 120) {
      throw new Error('Daily study time must be a whole number between 5 and 120 minutes.');
    }

    const targetHskLevel = updatedGoal.targetHskLevel ?? goalForDisplay?.targetHskLevel ?? 1;

    const data = await dispatchLearningEvent('GOAL_CREATED', {
      title,
      target_hsk_level: targetHskLevel,
      daily_available_minutes: dailyMinutes,
      timezone,
    });
    if (!data.state) throw new Error('The updated learner state was missing from the server response.');
    try {
      await acceptResponse(data, true);
    } catch (error) {
      setAppError(error instanceof Error ? error.message : 'Your goal was saved, but the roadmap could not be refreshed.');
    }
  };

  const handleStartAgentSession = async (
    selection: LessonSelection = lastLessonSelection.current,
  ): Promise<void> => {
    if (teachingRequestInFlight.current) return;
    teachingRequestInFlight.current = true;
    lastLessonSelection.current = selection;
    setIsTeachingOpen(true);
    setTeachingLoading(true);
    setLoadingStage(nextAction === 'plan' || learnerState?.needsReplanning ? 'planning' : 'teaching');
    setTeachingError(null);
    setAgentGradingResult(null);
    setAgentReplanned(false);
    try {
      let data = await dispatchLearningEvent('SESSION_STARTED', {
        entry_source: selection.entrySource,
        concept_id: selection.conceptId,
        plan_item_id: selection.planItemId,
      });
      const replanned = data.replanned;
      await acceptResponse(data, replanned);
      // Planning and teaching remain separate backend events. If this turn
      // regenerated the plan, request the teaching turn only after it finishes.
      if (!data.teachingAction && data.nextAction === 'teach') {
        setLoadingStage('teaching');
        const newPlan = data.dailyPlan ?? (data.state as LearnerState | undefined)?.activePlan;
        const uncompletedItem = newPlan?.items.find((item) => !item.completed);
        const nextSelection: LessonSelection = {
          entrySource: selection.entrySource,
          conceptId: uncompletedItem?.conceptId ?? selection.conceptId,
          planItemId: uncompletedItem ? String(uncompletedItem.id) : undefined,
        };
        lastLessonSelection.current = nextSelection;
        data = await dispatchLearningEvent('SESSION_STARTED', {
          entry_source: nextSelection.entrySource,
          concept_id: nextSelection.conceptId,
          plan_item_id: nextSelection.planItemId,
        });
        await acceptResponse(data);
      }
      if (!data.teachingAction) {
        if (data.nextAction === 'complete') {
          setIsTeachingOpen(false);
          return;
        }
        throw new Error('No teaching action was returned.');
      }
      if (data.teachingAction.metadata?.plan_item_id) {
        lastLessonSelection.current = {
          ...lastLessonSelection.current,
          planItemId: String(data.teachingAction.metadata.plan_item_id),
          conceptId: data.teachingAction.conceptId,
        };
      }
      setTeachingAction(data.teachingAction);
      setAgentReplanned(replanned || data.replanned);
      activityStartedAt.current = Date.now();
    } catch (error) {
      setTeachingError(error instanceof Error ? error.message : 'The lesson could not be started.');
    } finally {
      teachingRequestInFlight.current = false;
      setTeachingLoading(false);
      setLoadingStage('idle');
    }
  };

  const handleTeachingHelp = async (query: string): Promise<void> => {
    if (!teachingAction || teachingRequestInFlight.current) return;
    teachingRequestInFlight.current = true;
    setTeachingLoading(true);
    setTeachingError(null);
    setAgentGradingResult(null);
    setAgentReplanned(false);
    try {
      const data = await dispatchLearningEvent('HELP_REQUESTED', {
        concept_id: teachingAction.conceptId,
        current_exercise_id: teachingAction.exercisePayload?.exercise_id,
        learner_query: query,
      });
      if (!data.teachingAction) throw new Error('No alternative explanation was returned.');
      setTeachingAction(data.teachingAction);
      await acceptResponse(data);
    } catch (error) {
      setTeachingError(error instanceof Error ? error.message : 'Coach help is temporarily unavailable.');
    } finally {
      teachingRequestInFlight.current = false;
      setTeachingLoading(false);
    }
  };

  const handleAgentAnswer = async (answer: string): Promise<void> => {
    if (teachingRequestInFlight.current || agentGradingResult) return;
    const exerciseId = teachingAction?.exercisePayload?.exercise_id;
    const conceptId = teachingAction?.exercisePayload?.concept_id || teachingAction?.conceptId;
    if (!exerciseId || !conceptId) {
      setTeachingError('This lesson does not contain a gradable curriculum exercise.');
      return;
    }
    teachingRequestInFlight.current = true;
    setTeachingLoading(true);
    setTeachingError(null);
    try {
      const data = await dispatchLearningEvent('ANSWER_SUBMITTED', {
        exercise_id: exerciseId,
        concept_id: conceptId,
        answer,
        time_spent_seconds: currentActivitySeconds(),
      });
      setAgentGradingResult(data.gradingResult ?? null);
      setAgentReplanned(data.replanned);
      await acceptResponse(data, true);
      activityStartedAt.current = Date.now();
    } catch (error) {
      setTeachingError(error instanceof Error ? error.message : 'Your answer could not be checked.');
    } finally {
      teachingRequestInFlight.current = false;
      setTeachingLoading(false);
    }
  };

  const handleCloseAgentSession = async (): Promise<void> => {
    if (teachingRequestInFlight.current) return;
    if (!learnerState?.activeSession) {
      clearTeachingTurn();
      setIsTeachingOpen(false);
      return;
    }
    teachingRequestInFlight.current = true;
    setTeachingLoading(true);
    setTeachingError(null);
    try {
      const data = await dispatchLearningEvent('SESSION_ENDED', {
        additional_active_seconds: teachingAction?.metadata?.progress_eligible === false
          ? 0
          : currentActivitySeconds(),
      });
      await acceptResponse(data, true);
      clearTeachingTurn();
      activityStartedAt.current = null;
      setIsTeachingOpen(false);
    } catch (error) {
      setTeachingError(error instanceof Error ? error.message : 'The study session could not be closed.');
    } finally {
      teachingRequestInFlight.current = false;
      setTeachingLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-50 flex items-center justify-center p-4 select-none">
        <div className="text-center space-y-4">
          <div className="w-12 h-12 border-4 border-zinc-950 border-t-emerald-500 rounded-full animate-spin mx-auto" />
          <h2 className="text-lg font-black text-zinc-950 tracking-tight">Starting GoalCoach...</h2>
          <p className="text-xs text-zinc-500 font-bold">Loading HSK 1 curriculum & spaced repetition path</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen text-slate-950 flex select-none">
      {/* Desktop Sidebar (Duolingo Style) */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onOpenProfile={() => setIsProfileDrawerOpen(true)}
        learnerState={learnerState}
        goalCompletion={goalCompletion}
        nextAction={nextAction}
      />

      {/* Main Layout Area */}
      <div className="flex-1 flex flex-col min-w-0 pb-20 lg:pb-0">
        {/* Top Status Bar (Duolingo Streak / Energy / Daily Quota) */}
        <TopStatusBar
          goalCompletion={goalCompletion}
          onOpenProfile={() => setIsProfileDrawerOpen(true)}
        />

        {/* Main Content View */}
        <main className="flex-1 max-w-5xl w-full mx-auto px-4 sm:px-8 py-7 sm:py-10">
          {appError && (
            <p role="alert" className="mb-5 rounded-2xl bg-rose-50 p-4 text-sm font-bold text-rose-800">
              {appError}
            </p>
          )}
          {activeTab === 'plan' && (
            <DailyPlanView
              plan={learnerState?.activePlan || null}
              goal={goalForDisplay}
              concepts={concepts}
              learnerState={learnerState}
              onStartStudy={(selection) => void handleStartAgentSession(selection)}
              onUpdateGoal={handleUpdateGoal}
              onRegeneratePlan={handleRegeneratePlan}
              onOpenGoalSettings={() => setIsProfileDrawerOpen(true)}
            />
          )}

          {activeTab === 'curriculum' && (
            <RoadmapView
              concepts={concepts}
              learnerState={learnerState}
              coverageRationale={roadmapCoverageRationale || learnerState?.roadmapCoverageRationale || ''}
              onStartConcept={(conceptId) => void handleStartAgentSession({
                entrySource: 'roadmap',
                conceptId,
              })}
            />
          )}

          {activeTab === 'retention' && (
            <RetentionVisualizer
              learnerState={learnerState}
              concepts={concepts}
              goalCompletion={goalCompletion}
              progressSummary={progressSummary}
            />
          )}
        </main>
      </div>

      {/* Mobile Bottom Navigation */}
      <BottomNav
        activeTab={activeTab}
        setActiveTab={setActiveTab}
      />

      {/* Learner Profile Drawer (Triggered by clicking Panda Logo/Name) */}
      <LearnerProfileDrawer
        isOpen={isProfileDrawerOpen}
        onClose={() => setIsProfileDrawerOpen(false)}
        displayName={learnerState?.displayName || 'Ann'}
        goal={goalForDisplay}
        learnedProgress={learnedProgress}
        masteredProgress={masteredProgress}
        onUpdateGoal={handleUpdateGoal}
      />

      <TeachingAgentModal
        isOpen={isTeachingOpen}
        action={teachingAction}
        loading={teachingLoading}
        loadingStage={loadingStage}
        error={teachingError}
        gradingResult={agentGradingResult}
        replanned={agentReplanned}
        nextAction={nextAction}
        recoveryLabel={nextAction === 'plan' ? 'Retry planning' : nextAction === 'complete' ? 'Return to plan' : 'Resume lesson'}
        onRecover={async () => {
          if (nextAction === 'complete') {
            await handleCloseAgentSession();
            return;
          }
          if (nextAction === 'plan') {
            await handleStartAgentSession({ entrySource: lastLessonSelection.current.entrySource });
            return;
          }
          if (lastLessonSelection.current.entrySource === 'planned') {
            const activePlan = learnerState?.activePlan;
            const uncompleted = activePlan?.items.find((item) => !item.completed);
            await handleStartAgentSession({
              entrySource: 'planned',
              conceptId: uncompleted?.conceptId,
              planItemId: uncompleted ? String(uncompleted.id) : undefined,
            });
            return;
          }
          await handleStartAgentSession(lastLessonSelection.current);
        }}
        onClose={handleCloseAgentSession}
        onContinue={async () => {
          if (lastLessonSelection.current.entrySource === 'planned') {
            const activePlan = learnerState?.activePlan;
            const uncompleted = activePlan?.items.find((item) => !item.completed);
            await handleStartAgentSession({
              entrySource: 'planned',
              conceptId: uncompleted?.conceptId,
              planItemId: uncompleted ? String(uncompleted.id) : undefined,
            });
            return;
          }
          await handleStartAgentSession(lastLessonSelection.current);
        }}
        onRequestHelp={handleTeachingHelp}
        onSubmitAnswer={handleAgentAnswer}
      />
    </div>
  );
}

export default App;
