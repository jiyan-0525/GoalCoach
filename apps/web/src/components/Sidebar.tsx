import React from 'react';
import { 
  Compass, 
  TrendingUp,
  BookOpen,
} from 'lucide-react';
import { PandaMascot } from './PandaMascot.tsx';
import { GoalCoachLogo } from './GoalCoachLogo.tsx';
import { LearnerState, NextAction } from '../types.ts';

interface SidebarProps {
  activeTab: 'plan' | 'curriculum' | 'retention';
  setActiveTab: (tab: 'plan' | 'curriculum' | 'retention') => void;
  onOpenProfile?: () => void;
  learnerState: LearnerState | null;
  goalCompletion: number;
  nextAction: NextAction;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  onOpenProfile,
  learnerState,
  goalCompletion,
  nextAction,
}) => {
  const overallPercent = Math.round(Math.max(0, Math.min(100, goalCompletion)));
  return (
    <aside className="w-68 shrink-0 hidden lg:flex flex-col border-r border-slate-200/80 bg-white/85 backdrop-blur-xl sticky top-0 h-screen px-5 py-5 select-none shadow-[10px_0_35px_rgba(15,23,42,0.03)] justify-between overflow-y-auto">
      {/* Brand Header with Bamboo Panda Logo (Clickable to open profile) */}
      <div className="px-2 mb-6">
        <GoalCoachLogo 
          size="md" 
          showSubtitle={true} 
          onClick={onOpenProfile} 
          isClickable={true} 
        />
      </div>

      {/* Main Navigation Links (Duolingo Style 3D Action Buttons) */}
      <nav className="space-y-2 flex-1">
        <button
          id="sidebar-btn-learn"
          onClick={() => setActiveTab('plan')}
          className={`w-full flex items-center gap-3.5 px-4 py-3 rounded-2xl text-xs font-black uppercase tracking-wider transition-all cursor-pointer ${
            activeTab === 'plan'
              ? 'bg-emerald-50 text-emerald-800 border-2 border-emerald-600 shadow-[0_3px_0_#16a34a]'
              : 'text-zinc-600 hover:bg-zinc-100 border-2 border-transparent'
          }`}
        >
          <Compass className="w-5 h-5" />
          <span>Today</span>
        </button>

        <button
          id="sidebar-btn-curriculum"
          onClick={() => setActiveTab('curriculum')}
          className={`w-full flex items-center gap-3.5 px-4 py-3 rounded-2xl text-xs font-black uppercase tracking-wider transition-all cursor-pointer ${
            activeTab === 'curriculum'
              ? 'bg-emerald-50 text-emerald-800 border-2 border-emerald-600 shadow-[0_3px_0_#16a34a]'
              : 'text-zinc-600 hover:bg-zinc-100 border-2 border-transparent'
          }`}
        >
          <BookOpen className="w-5 h-5" />
          <span>Roadmap</span>
        </button>

        <button
          id="sidebar-btn-retention"
          onClick={() => setActiveTab('retention')}
          className={`w-full flex items-center gap-3.5 px-4 py-3 rounded-2xl text-xs font-black uppercase tracking-wider transition-all cursor-pointer ${
            activeTab === 'retention'
              ? 'bg-emerald-50 text-emerald-800 border-2 border-emerald-600 shadow-[0_3px_0_#16a34a]'
              : 'text-zinc-600 hover:bg-zinc-100 border-2 border-transparent'
          }`}
        >
          <TrendingUp className="w-5 h-5" />
          <span>Progress</span>
        </button>
      </nav>

      {/* Mini Mascot Card at Sidebar Bottom (Click to open profile) */}
      <div 
        onClick={onOpenProfile}
        className="mt-4 bg-slate-900 rounded-3xl p-3.5 text-white space-y-2.5 shadow-[0_4px_0_#0f172a] border border-slate-800 cursor-pointer group hover:border-emerald-500 transition-colors"
        title="Click to view and edit study profile"
      >
        <div className="flex items-center gap-3">
          <PandaMascot mood="cheering" size={44} />
          <div>
            <div className="text-xs font-black text-emerald-400 uppercase tracking-wider group-hover:text-emerald-300 transition-colors">
              Goal Completion
            </div>
            <div className="text-[11px] text-zinc-300 font-medium leading-tight mt-0.5">
              {overallPercent}%
            </div>
          </div>
        </div>

        {/* Progress bar inside card */}
        <div className="space-y-1 pt-2 border-t border-slate-800">
          <div className="flex justify-between text-[10px] font-black text-slate-400">
            <span>Current progress</span>
            <span className="text-emerald-400">{overallPercent}%</span>
          </div>
          <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden p-0.5 border border-slate-700">
            <div
              className="h-full bg-emerald-500 rounded-full transition-all duration-500"
              style={{ width: `${overallPercent}%` }}
            />
          </div>
        </div>
      </div>
    </aside>
  );
};
