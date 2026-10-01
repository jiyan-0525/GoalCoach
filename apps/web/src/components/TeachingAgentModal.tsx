import React, { useEffect, useState } from 'react';
import { BookOpenText, CheckCircle2, HelpCircle, Lightbulb, Send, Sparkles, X } from 'lucide-react';
import { GradingResult, NextAction, TeachingAction } from '../types.ts';

interface TeachingAgentModalProps {
  isOpen: boolean;
  action: TeachingAction | null;
  loading: boolean;
  loadingStage?: 'idle' | 'planning' | 'teaching';
  error: string | null;
  gradingResult: GradingResult | null;
  replanned: boolean;
  nextAction?: NextAction;
  recoveryLabel: string;
  onRecover: () => Promise<void>;
  onClose: () => Promise<void>;
  onContinue: () => Promise<void>;
  onRequestHelp: (query: string) => Promise<void>;
  onSubmitAnswer: (answer: string) => Promise<void>;
}

const TeachingContent: React.FC<{ content: string }> = ({ content }) => (
  <div className="space-y-3 text-sm leading-7 text-slate-700">
    {content.split('\n').map((line, index) => {
      if (/^\|\s*:?-+/.test(line)) return null;
      if (line.trim().startsWith('|')) {
        const cells = line.split('|').slice(1, -1).map((cell) => cell.trim());
        const isHeader = cells.some((cell) => cell === 'Character');
        return (
          <div key={`${line}-${index}`} className={`grid grid-cols-3 gap-3 rounded-xl px-3 py-2 ${isHeader ? 'bg-indigo-100 font-black text-indigo-950' : 'border border-indigo-100 bg-white'}`}>
            {cells.map((cell, cellIndex) => <span key={`${cell}-${cellIndex}`}>{cell.replaceAll('**', '')}</span>)}
          </div>
        );
      }
      const normalized = line.replaceAll('**', '');
      return normalized
        ? <p key={`${line}-${index}`} className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">{normalized}</p>
        : null;
    })}
  </div>
);

export const TeachingAgentModal: React.FC<TeachingAgentModalProps> = ({
  isOpen,
  action,
  loading,
  loadingStage = 'idle',
  error,
  gradingResult,
  replanned,
  nextAction,
  recoveryLabel,
  onRecover,
  onClose,
  onContinue,
  onRequestHelp,
  onSubmitAnswer,
}) => {
  const [answer, setAnswer] = useState('');
  const [selectedLeft, setSelectedLeft] = useState<string | null>(null);
  const [matchedPairs, setMatchedPairs] = useState<Record<string, string>>({});
  const [helpQuery, setHelpQuery] = useState('I do not understand this yet. Please explain it differently.');

  useEffect(() => {
    setAnswer('');
    setSelectedLeft(null);
    setMatchedPairs({});
  }, [action?.exercisePayload?.exercise_id]);

  if (!isOpen) return null;

  const exercise = action?.exercisePayload;
  const isMatching = exercise?.exercise_type === 'matching' || (
    Boolean(exercise?.options) &&
    typeof exercise?.options === 'object' &&
    !Array.isArray(exercise?.options) &&
    'left' in (exercise!.options as object) &&
    'right' in (exercise!.options as object)
  );
  const matchingOptions = isMatching
    ? (exercise?.options as { left: Array<{ id: string; word: string; pinyin?: string }>; right: Array<{ id: string; meaning: string }> })
    : null;
  const listOptions = Array.isArray(exercise?.options) && exercise.options.length > 0
    ? exercise.options
    : null;

  const handleSelectLeft = (leftId: string) => {
    if (gradingResult) return;
    setSelectedLeft(selectedLeft === leftId ? null : leftId);
  };

  const handleSelectRight = (rightId: string) => {
    if (gradingResult) return;
    if (selectedLeft) {
      const next = { ...matchedPairs, [selectedLeft]: rightId };
      setMatchedPairs(next);
      setSelectedLeft(null);
      const str = Object.entries(next)
        .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
        .map(([l, r]) => `${l}${r}`)
        .join(' ');
      setAnswer(str);
    }
  };

  const countsTowardProgress = action?.metadata?.progress_eligible !== false;
  const progressNotice = typeof action?.metadata?.progress_notice === 'string'
    ? action.metadata.progress_notice
    : null;

  const loadingMessage = loadingStage === 'planning'
    ? 'Adapting your study plan based on your recent progress…'
    : loadingStage === 'teaching'
    ? 'Coach Baobao is crafting your next lesson…'
    : 'Adapting the lesson to your goal and progress…';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-3 backdrop-blur-sm sm:p-5">
      <div className="max-h-[94vh] w-full max-w-3xl overflow-y-auto rounded-[2rem] border border-white/30 bg-[#f8faf7] p-5 shadow-2xl sm:p-7">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.16em] text-emerald-700"><Sparkles className="h-4 w-4" />Your private coach</p>
            <h2 className="mt-1 text-2xl font-black text-slate-950">{action?.actionKind?.replaceAll('_', ' ') || 'Preparing your lesson'}</h2>
          </div>
          <button type="button" disabled={loading} onClick={() => void onClose()} aria-label="Close lesson" className="rounded-xl p-2 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-950">
            <X className="h-5 w-5" />
          </button>
        </div>

        {loading && (
          <div className="mt-4 flex items-center gap-3 rounded-2xl border border-indigo-200 bg-indigo-50/90 px-4 py-3 text-xs font-bold text-indigo-950 shadow-sm animate-pulse">
            <Sparkles className="h-4 w-4 animate-spin text-indigo-600" />
            <span>{loadingMessage}</span>
          </div>
        )}
        {error && <p role="alert" className="mt-6 rounded-2xl bg-rose-50 p-4 text-sm font-bold text-rose-800">{error}</p>}
        {error && !action && !loading && (
          <button type="button" onClick={() => void onRecover()} className="mt-4 rounded-xl bg-zinc-950 px-4 py-3 font-bold text-white">
            {recoveryLabel}
          </button>
        )}

        {!action && loading && (
          <div className="mt-6 flex flex-col items-center justify-center space-y-3 py-12 text-center">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-indigo-200 border-t-indigo-600" />
            <p className="text-sm font-bold text-zinc-600">{loadingMessage}</p>
          </div>
        )}

        {action && (
          <div className={`mt-6 space-y-5 transition-opacity duration-200 ${loading ? 'opacity-40 pointer-events-none' : ''}`}>
            <div className={`flex items-center gap-3 rounded-2xl px-4 py-3 text-xs font-bold ${countsTowardProgress ? 'bg-emerald-100 text-emerald-900' : 'bg-indigo-100 text-indigo-900'}`}>
              {countsTowardProgress ? <CheckCircle2 className="h-5 w-5" /> : <BookOpenText className="h-5 w-5" />}
              <span>{progressNotice ?? (countsTowardProgress ? 'This planned lesson counts toward progress.' : 'Free practice · progress and study time stay unchanged.')}</span>
            </div>

            <section className="rounded-3xl border border-indigo-100 bg-indigo-50/70 p-4 sm:p-5">
              <h3 className="mb-3 flex items-center gap-2 text-xs font-black uppercase tracking-wider text-indigo-800"><Lightbulb className="h-4 w-4" />Coach’s explanation</h3>
              <TeachingContent content={action.content} />
            </section>
            {action.pinyin && <p className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-900"><span className="mr-2 text-xs uppercase tracking-wider">Say it</span>{action.pinyin}</p>}

            {exercise?.prompt && (
              <form
                className="space-y-4 rounded-3xl border border-emerald-200 bg-gradient-to-br from-emerald-50 to-lime-50 p-5 sm:p-6"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (gradingResult) return;
                  void onSubmitAnswer(answer.trim());
                }}
              >
                <p className="text-xs font-black uppercase tracking-[0.16em] text-emerald-800">Try it yourself</p>
                {exercise.instruction && <p className="text-sm font-medium text-zinc-600">{exercise.instruction}</p>}
                <p className="text-xl font-black leading-8 text-slate-950">{exercise.prompt}</p>

                {/* Multiple-choice option buttons (1-click answering) */}
                {listOptions && (
                  <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                    {listOptions.map((opt, idx) => {
                      const optNum = String(idx + 1);
                      const isSelected = answer.trim() === opt || answer.trim() === optNum;
                      return (
                        <button
                          key={opt}
                          type="button"
                          disabled={loading || gradingResult !== null}
                          onClick={() => {
                            setAnswer(opt);
                          }}
                          className={`flex items-center gap-3 rounded-2xl border-2 px-4 py-3 text-left text-sm font-bold transition-all ${
                            isSelected
                              ? 'border-emerald-600 bg-emerald-100 text-emerald-950 shadow-sm'
                              : 'border-slate-200 bg-white text-slate-800 hover:border-emerald-300 hover:bg-emerald-50/50'
                          }`}
                        >
                          <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-black ${isSelected ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-600'}`}>
                            {idx + 1}
                          </span>
                          <span className="flex-1">{opt}</span>
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Mix and Match 2-column layout */}
                {matchingOptions && (
                  <div className="space-y-3">
                    <p className="text-xs font-bold text-emerald-900">
                      Tap a Chinese word on the left, then tap its matching meaning on the right:
                    </p>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {/* Left Column: Chinese Words */}
                      <div className="space-y-2">
                        <span className="text-[11px] font-black uppercase tracking-wider text-slate-500">Chinese Words</span>
                        {matchingOptions.left.map((item) => {
                          const isPicked = selectedLeft === item.id;
                          const currentMatch = matchedPairs[item.id];
                          return (
                            <button
                              key={item.id}
                              type="button"
                              onClick={() => handleSelectLeft(item.id)}
                              disabled={loading || gradingResult !== null}
                              className={`flex w-full items-center justify-between rounded-xl border-2 px-3 py-2.5 text-left transition-all ${
                                isPicked
                                  ? 'border-emerald-600 bg-emerald-100 text-emerald-950 ring-2 ring-emerald-400'
                                  : currentMatch
                                  ? 'border-indigo-300 bg-indigo-50 text-indigo-950'
                                  : 'border-slate-200 bg-white hover:border-slate-300'
                              }`}
                            >
                              <div className="flex items-center gap-2">
                                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-200 text-[10px] font-black text-slate-700">
                                  {item.id}
                                </span>
                                <span className="font-bold text-slate-900">{item.word}</span>
                                {item.pinyin && <span className="text-xs text-slate-500">({item.pinyin})</span>}
                              </div>
                              {currentMatch && (
                                <span className="rounded-md bg-indigo-200 px-1.5 py-0.5 text-[10px] font-black text-indigo-900">
                                  → {currentMatch}
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>

                      {/* Right Column: Meanings */}
                      <div className="space-y-2">
                        <span className="text-[11px] font-black uppercase tracking-wider text-slate-500">Meanings</span>
                        {matchingOptions.right.map((item) => {
                          const matchedLeftId = Object.entries(matchedPairs).find(([, r]) => r === item.id)?.[0];
                          return (
                            <button
                              key={item.id}
                              type="button"
                              onClick={() => handleSelectRight(item.id)}
                              disabled={loading || gradingResult !== null}
                              className={`flex w-full items-center justify-between rounded-xl border-2 px-3 py-2.5 text-left transition-all ${
                                matchedLeftId
                                  ? 'border-indigo-300 bg-indigo-50 text-indigo-950'
                                  : selectedLeft
                                  ? 'border-emerald-400 bg-white hover:bg-emerald-50'
                                  : 'border-slate-200 bg-white hover:border-slate-300'
                              }`}
                            >
                              <div className="flex items-center gap-2">
                                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-200 text-[10px] font-black text-slate-700">
                                  {item.id}
                                </span>
                                <span className="text-sm font-semibold text-slate-900">{item.meaning}</span>
                              </div>
                              {matchedLeftId && (
                                <span className="rounded-md bg-indigo-200 px-1.5 py-0.5 text-[10px] font-black text-indigo-900">
                                  {matchedLeftId}
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex flex-col gap-2 sm:flex-row">
                  <input
                    value={answer}
                    onChange={(event) => setAnswer(event.currentTarget.value)}
                    className="min-w-0 flex-1 rounded-xl border-2 border-zinc-200 bg-white px-4 py-3 outline-none focus:border-emerald-500"
                    placeholder={
                      isMatching
                        ? "Matching pairs (e.g. 1C 2A 3E)..."
                        : listOptions
                        ? "Click an option above or type here..."
                        : "Type your answer…"
                    }
                  />
                  <button disabled={!answer.trim() || loading || gradingResult !== null} className="flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-5 py-3 font-black text-emerald-950 shadow-[0_4px_0_#15803d] active:translate-y-0.5 active:shadow-none disabled:opacity-50"><Send className="h-4 w-4" />Check</button>
                </div>
              </form>
            )}

            {gradingResult && (
              <div className={`rounded-2xl p-4 text-sm font-bold ${gradingResult.passedGates ? 'bg-emerald-50 text-emerald-900' : 'bg-amber-50 text-amber-950'}`}>
                <p>{gradingResult.feedback}</p>
                {(replanned || nextAction === 'plan') && (
                  <p className="mt-2 text-xs font-bold text-amber-800">
                    Your daily plan is adjusting because this error has repeated.
                  </p>
                )}
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <button
                    data-testid="continue-lesson-btn"
                    type="button"
                    disabled={loading}
                    onClick={() => {
                      setAnswer('');
                      setSelectedLeft(null);
                      setMatchedPairs({});
                      if (gradingResult.passedGates && nextAction === 'complete') {
                        void onClose();
                      } else {
                        void onContinue();
                      }
                    }}
                    className={`rounded-xl px-5 py-2.5 text-xs font-black text-white transition-colors disabled:opacity-50 ${
                      gradingResult.passedGates
                        ? 'bg-emerald-600 hover:bg-emerald-700'
                        : 'bg-zinc-950 hover:bg-zinc-800'
                    }`}
                  >
                    {gradingResult.passedGates
                      ? (nextAction === 'complete' ? "Finish today's plan" : 'Continue to next lesson')
                      : 'Continue with Coach'}
                  </button>
                </div>
              </div>
            )}

            <div className="rounded-3xl border border-slate-200 bg-white p-4 sm:p-5">
              <label htmlFor="coach-help" className="flex items-center gap-2 text-xs font-black uppercase tracking-wider text-zinc-600"><HelpCircle className="h-4 w-4" />Need a different explanation?</label>
              <textarea id="coach-help" value={helpQuery} onChange={(event) => setHelpQuery(event.currentTarget.value)} className="mt-3 min-h-20 w-full rounded-xl border-2 border-zinc-200 p-3 text-sm outline-none focus:border-emerald-500" />
              <button type="button" disabled={!helpQuery.trim() || loading} onClick={() => void onRequestHelp(helpQuery.trim())} className="mt-3 rounded-xl bg-zinc-900 px-4 py-2.5 text-xs font-black text-white disabled:opacity-50">Explain another way</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
