import React, { useState } from 'react';

import type { AuthResponse } from '../types.ts';

interface AuthScreenProps {
  onAuthenticated: (response: AuthResponse) => void;
}

export const AuthScreen: React.FC<AuthScreenProps> = ({ onAuthenticated }) => {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '';
  const apiUrl = (path: string): string => `${API_BASE}${path}`;

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const endpoint = mode === 'register' ? '/api/v1/auth/register' : '/api/v1/auth/login';
      const payload: Record<string, string> = {
        email: email.trim(),
        password,
      };
      if (mode === 'register') {
        payload.display_name = displayName.trim() || 'Learner';
      }
      const response = await fetch(apiUrl(endpoint), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error((body?.detail as string) || 'Authentication failed.');
      }
      onAuthenticated(body as AuthResponse);
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : 'Authentication failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <section className="w-full max-w-md rounded-[2rem] border border-slate-200 bg-white/95 p-7 shadow-[0_18px_50px_rgba(15,23,42,0.06)]">
        <h1 className="text-2xl font-black text-slate-950">GoalCoach account</h1>
        <p className="mt-2 text-sm text-slate-500">Sign in to keep your learning progress private.</p>
        <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
          {mode === 'register' && (
            <label className="block">
              <span className="mb-1 block text-xs font-black uppercase tracking-wide text-slate-500">Display name</span>
              <input
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Your name"
              />
            </label>
          )}
          <label className="block">
            <span className="mb-1 block text-xs font-black uppercase tracking-wide text-slate-500">Email</span>
            <input
              type="email"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-black uppercase tracking-wide text-slate-500">Password</span>
            <input
              type="password"
              minLength={8}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && <p role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700">{error}</p>}
          <button type="submit" disabled={loading} className="primary-action w-full">
            {loading ? 'Please wait...' : mode === 'register' ? 'Create account' : 'Sign in'}
          </button>
        </form>
        <button
          type="button"
          onClick={() => setMode(mode === 'register' ? 'login' : 'register')}
          className="mt-4 w-full text-sm font-bold text-emerald-700"
        >
          {mode === 'register' ? 'Already have an account? Sign in' : 'No account yet? Create one'}
        </button>
      </section>
    </div>
  );
};
