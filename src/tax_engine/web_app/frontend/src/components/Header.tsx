import { AlertCircle, CheckCircle, Play, RefreshCw } from 'lucide-react';
import type { RunStatus } from '../hooks/useApi';

interface Props {
  status: RunStatus;
  computedAt: string | null;
  error: string | null;
  onRun: () => void;
}

export function Header({ status, computedAt, error, onRun }: Props) {
  const running = status === 'running';
  return (
    <header className="flex items-center justify-between px-6 py-4 border-b border-zinc-700 bg-zinc-900">
      <h1 className="text-xl font-semibold text-zinc-100">Tax Engine</h1>
      <div className="flex items-center gap-4">
        {status === 'success' && computedAt && (
          <span className="flex items-center gap-1 text-xs text-green-400">
            <CheckCircle size={14} />
            {new Date(computedAt).toLocaleTimeString('es-ES')}
          </span>
        )}
        {status === 'error' && error && (
          <span className="flex items-center gap-1 text-xs text-red-400">
            <AlertCircle size={14} />
            {error.slice(0, 80)}
          </span>
        )}
        <button
          onClick={onRun}
          disabled={running}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white transition-colors"
        >
          {running ? <RefreshCw size={15} className="animate-spin" /> : <Play size={15} />}
          {running ? 'Running…' : 'Run Engine'}
        </button>
      </div>
    </header>
  );
}
