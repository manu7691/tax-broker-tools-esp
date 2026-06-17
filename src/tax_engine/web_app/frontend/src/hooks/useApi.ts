import { useState, useCallback } from 'react';
import type { ComparisonRow, EngineResult, ManualData } from '../types/api';

export type RunStatus = 'idle' | 'running' | 'success' | 'error';

export function useApi() {
  const [result, setResult] = useState<EngineResult | null>(null);
  const [manualData, setManualData] = useState<ManualData | null>(null);
  const [comparison, setComparison] = useState<ComparisonRow[]>([]);
  const [status, setStatus] = useState<RunStatus>('idle');
  const [error, setError] = useState<string | null>(null);

  const loadComparison = useCallback(async () => {
    const res = await fetch('/api/comparison');
    if (res.ok) {
      const data = await res.json();
      setComparison(data.rows ?? []);
    }
  }, []);

  const runEngine = useCallback(async () => {
    setStatus('running');
    setError(null);
    try {
      const res = await fetch('/api/run', { method: 'POST' });
      if (!res.ok) throw new Error(await res.text());
      setResult(await res.json());
      setStatus('success');
      await loadComparison();
    } catch (e) {
      setError(String(e));
      setStatus('error');
    }
  }, [loadComparison]);

  const saveManual = useCallback(async (data: ManualData) => {
    const res = await fetch('/api/manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (res.ok) setManualData(data);
  }, []);

  const loadInitialData = useCallback(async () => {
    const [resultRes, manualRes, compRes] = await Promise.all([
      fetch('/api/result'),
      fetch('/api/manual'),
      fetch('/api/comparison'),
    ]);
    if (resultRes.ok) {
      const data: EngineResult | null = await resultRes.json();
      if (data) { setResult(data); setStatus('success'); }
    }
    if (manualRes.ok) setManualData(await manualRes.json());
    if (compRes.ok) {
      const data = await compRes.json();
      setComparison(data.rows ?? []);
    }
  }, []);

  return { result, manualData, comparison, status, error, runEngine, saveManual, loadComparison, loadInitialData };
}
