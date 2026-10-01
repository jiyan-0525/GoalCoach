import React, { useMemo } from 'react';
import { ArrowRight, Check, Clock3, Target } from 'lucide-react';
import { CurriculumConcept, DailyPlan, LearnerState, LearningGoal, StudyEntrySource } from '../types.ts';

interface LessonSelection {
  entrySource: StudyEntrySource;
  conceptId?: string;
  planItemId?: string;
}

interface DailyPlanViewProps {
  plan: DailyPlan | null;
  goal: LearningGoal | null;
  concepts: CurriculumConcept[];
  learnerState: LearnerState | null;
  onStartStudy: (selection: LessonSelection) => void;
  onUpdateGoal: (goal: Partial<LearningGoal>) => void;
  onRegeneratePlan: () => void;
  onOpenGoalSettings?: () => void;
}

const ITEM_STYLE = {
  review: { label: 'Review', icon: '↻', accent: 'bg-amber-400', soft: 'bg-amber-50 border-amber-200 text-amber-900' },
  remedial: { label: 'Fix', icon: '+', accent: 'bg-rose-400', soft: 'bg-rose-50 border-rose-200 text-rose-900' },
  new: { label: 'Learn', icon: '▶', accent: 'bg-sky-400', soft: 'bg-sky-50 border-sky-200 text-sky-900' },
} as const;

export const DailyPlanView: React.FC<DailyPlanViewProps> = ({
  plan,
  concepts,
  onStartStudy,
  onRegeneratePlan,
  onOpenGoalSettings,
}) => {
  const completedCount = plan?.items.filter((item) => item.completed).length ?? 0;
  const currentItem = plan?.items.find((item) => !item.completed);
  const totalMinutes = plan?.items.reduce((sum, item) => sum + item.estimatedMinutes, 0) ?? 0;
  const remainingMinutes = plan?.items
    .filter((item) => !item.completed)
    .reduce((sum, item) => sum + item.estimatedMinutes, 0) ?? 0;
  const completion = plan?.items.length ? Math.round((completedCount / plan.items.length) * 100) : 0;

  const allocation = useMemo(() => {
    if (!plan || totalMinutes === 0) return [];
    return (['review', 'remedial', 'new'] as const).map((kind) => {
      const minutes = plan.items.filter((item) => item.kind === kind)
        .reduce((sum, item) => sum + item.estimatedMinutes, 0);
      return { kind, minutes, percent: (minutes / totalMinutes) * 100 };
    }).filter((part) => part.minutes > 0);
  }, [plan, totalMinutes]);

  if (!plan) {
    return (
      <section className="empty-state-card mx-auto max-w-2xl">
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-3xl bg-emerald-100 text-emerald-700"><Target className="h-8 w-8" /></div>
        <h2 className="mt-5 text-2xl font-extrabold">Ready to build your day?</h2>
        <p className="mt-2 text-sm text-slate-500">Your coach will turn your goal into a short, focused learning path.</p>
        <button
          type="button"
          onClick={onOpenGoalSettings || onRegeneratePlan}
          className="primary-action mt-6 cursor-pointer"
        >
          Build today’s plan
        </button>
      </section>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 pb-24">
      <section className="hero-card overflow-hidden">
        <div className="relative z-10 grid grid-cols-[1fr_auto] items-center gap-4">
          <div>
            <h1 className="text-3xl font-black tracking-tight text-emerald-950">{plan.status === 'exhausted' ? 'Complete!' : `${remainingMinutes} Mins`}</h1>
          </div>
          <div className="relative flex h-20 w-20 items-center justify-center rounded-full border-4 border-white bg-emerald-100 shadow-sm">
            <span className="text-2xl font-black text-emerald-950">{completion}%</span>
          </div>
        </div>
        <div className="relative z-10 mt-4 flex h-2 overflow-hidden rounded-full bg-emerald-100">
          {allocation.map((part) => <span key={part.kind} className={ITEM_STYLE[part.kind].accent} style={{ width: `${part.percent}%` }} />)}
        </div>
      </section>

      <ol className="space-y-3">
        {plan.items.map((item, index) => {
          const concept = concepts.find((candidate) => candidate.conceptId === item.conceptId);
          const style = ITEM_STYLE[item.kind];
          const isCurrent = currentItem?.id === item.id;
          const canOpen = true;
          const reviewOnly = item.completed;
          return (
            <li key={item.id}>
              <button
                type="button"
                disabled={!canOpen}
                onClick={() => onStartStudy({
                  entrySource: reviewOnly ? 'daily_review' : 'planned',
                  conceptId: item.conceptId,
                  planItemId: reviewOnly ? undefined : String(item.id),
                })}
                className={`lesson-card group ${isCurrent ? 'lesson-card-current' : ''} ${item.completed ? 'lesson-card-complete' : ''}`}
              >
                <span className={`lesson-step ${item.completed ? 'bg-emerald-500 text-white' : style.soft}`}>
                  {item.completed ? <Check className="h-5 w-5" /> : style.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full border px-2.5 py-1 text-[10px] font-black uppercase tracking-wider ${style.soft}`}>{style.label}</span>
                    {reviewOnly && <span className="text-[10px] font-bold text-slate-400">Practice again · no progress</span>}
                  </span>
                  <span className="mt-2 block text-left text-base font-black text-slate-950">{concept?.titleEn ?? item.objective}</span>
                  <span className="mt-0.5 block truncate text-left text-xs text-slate-500">{concept ? `${concept.titleZh} · ${concept.communicativeGoal}` : item.objective}</span>
                </span>
                <span className="hidden items-center gap-1 text-xs font-bold text-slate-400 sm:flex"><Clock3 className="h-4 w-4" />{item.estimatedMinutes} min</span>
                {canOpen && <ArrowRight className="h-5 w-5 text-emerald-600 transition-transform group-hover:translate-x-1" />}
              </button>
              {index < plan.items.length - 1 && <div className="ml-7 h-3 w-0.5 bg-slate-200" />}
            </li>
          );
        })}
      </ol>

      {plan.status === 'exhausted' && (
        <div className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5 text-center">
          <p className="font-black text-emerald-900">Nice work — today’s progress is saved.</p>
          <p className="mt-1 text-xs text-emerald-700">You can revisit any completed card without changing your progress.</p>
        </div>
      )}
    </div>
  );
};
