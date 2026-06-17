import { useEffect, useState } from 'react';
import { Save } from 'lucide-react';
import type { ManualData, ManualYear } from '../types/api';

interface Props {
  years: number[];
  manualData: ManualData | null;
  onSave: (data: ManualData) => Promise<void>;
}

const defaultYear = (): ManualYear => ({ salary_eur: 0, other_eur: 0, notes: '' });

export function WorkIncomeTab({ years, manualData, onSave }: Props) {
  const [draft, setDraft] = useState<Record<string, ManualYear>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!manualData) return;
    const initial: Record<string, ManualYear> = {};
    for (const y of years) initial[String(y)] = manualData.work_income[String(y)] ?? defaultYear();
    setDraft(initial);
  }, [manualData, years]);

  const handleChange = (year: number, field: keyof ManualYear, value: string) => {
    setDraft((prev) => ({
      ...prev,
      [String(year)]: {
        ...prev[String(year)],
        [field]: field === 'notes' ? value : Number(value),
      },
    }));
  };

  const handleSave = async () => {
    if (!manualData) return;
    setSaving(true);
    await onSave({ ...manualData, work_income: draft });
    setSaving(false);
  };

  if (!years.length)
    return <p className="text-zinc-400 text-sm">Run the engine first to see available years.</p>;

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-zinc-300">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-zinc-700">
              <th className="text-left py-2 pr-4">Year</th>
              <th className="text-right pr-4">Salary (€)</th>
              <th className="text-right pr-4">Other (€)</th>
              <th className="text-left pl-4">Notes</th>
            </tr>
          </thead>
          <tbody>
            {years.map((y) => {
              const row = draft[String(y)] ?? defaultYear();
              return (
                <tr key={y} className="border-b border-zinc-800">
                  <td className="py-2 pr-4 font-medium text-zinc-100">{y}</td>
                  <td className="pr-4">
                    <input
                      type="number"
                      step="0.01"
                      value={row.salary_eur}
                      onChange={(e) => handleChange(y, 'salary_eur', e.target.value)}
                      className="w-32 text-right bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-100 focus:outline-none focus:border-blue-500"
                    />
                  </td>
                  <td className="pr-4">
                    <input
                      type="number"
                      step="0.01"
                      value={row.other_eur}
                      onChange={(e) => handleChange(y, 'other_eur', e.target.value)}
                      className="w-32 text-right bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-100 focus:outline-none focus:border-blue-500"
                    />
                  </td>
                  <td className="pl-4">
                    <input
                      type="text"
                      value={row.notes}
                      onChange={(e) => handleChange(y, 'notes', e.target.value)}
                      className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-100 focus:outline-none focus:border-blue-500"
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button
        onClick={handleSave}
        disabled={saving}
        className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white transition-colors"
      >
        <Save size={15} />
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  );
}
