import { useRef, useState } from 'react';
import { Download } from 'lucide-react';
import { diffColor, fmt } from '../utils/format';
import type { ComparisonRow } from '../types/api';

interface Props {
  comparison: ComparisonRow[];
  onImportComplete: () => void;
}

export function HaciendaTab({ comparison, onImportComplete }: Props) {
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [uploading, setUploading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/aeat/parse', { method: 'POST', body: form });
      if (!res.ok) throw new Error(await res.text());
      setPreview(await res.json());
    } catch (err) {
      setUploadError(String(err));
    } finally {
      setUploading(false);
    }
  };

  const handleImport = async () => {
    if (!preview) return;
    setImporting(true);
    try {
      const { year, ...extracted } = preview;
      const res = await fetch('/api/aeat/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ year, extracted }),
      });
      if (!res.ok) throw new Error(await res.text());
      setPreview(null);
      if (fileRef.current) fileRef.current.value = '';
      onImportComplete();
    } finally {
      setImporting(false);
    }
  };

  const years = [...new Set(comparison.map((r) => r.year))].sort();

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-4 p-4 rounded-lg border border-zinc-700 bg-zinc-800/40">
        <div className="flex-1">
          <p className="text-sm text-zinc-300 font-medium mb-2">Upload AEAT XML borrador</p>
          <input
            ref={fileRef}
            type="file"
            accept=".xml,.xsig"
            onChange={handleUpload}
            className="text-sm text-zinc-400 file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:text-xs file:font-medium file:bg-zinc-700 file:text-zinc-200 hover:file:bg-zinc-600"
          />
          {uploadError && <p className="mt-2 text-xs text-red-400">{uploadError}</p>}
          {uploading && <p className="mt-2 text-xs text-zinc-400">Parsing…</p>}
        </div>
        {preview && (
          <button
            onClick={handleImport}
            disabled={importing}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white transition-colors whitespace-nowrap"
          >
            <Download size={15} />
            {importing ? 'Importing…' : 'Import'}
          </button>
        )}
      </div>

      {preview && (
        <div className="p-4 rounded-lg border border-zinc-700 bg-zinc-800/40">
          <p className="text-xs font-medium text-zinc-400 mb-3">Preview — casillas extracted</p>
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
            {Object.entries(preview)
              .filter(([k]) => k !== 'year')
              .map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-zinc-400">{k.replace(/_/g, ' ')}</span>
                  <span className="text-zinc-200">{fmt(v as number)}</span>
                </div>
              ))}
          </div>
        </div>
      )}

      {comparison.length > 0 ? (
        <div>
          <h3 className="text-sm font-medium text-zinc-400 mb-3">Computed vs Filed</h3>
          {years.map((year) => {
            const rows = comparison.filter((r) => r.year === year);
            return (
              <div key={year} className="mb-6">
                <p className="text-xs font-semibold text-zinc-500 mb-2">{year}</p>
                <table className="w-full text-sm text-zinc-300">
                  <thead>
                    <tr className="text-xs text-zinc-500 border-b border-zinc-700">
                      <th className="text-left py-1 pr-4">Category</th>
                      <th className="text-right pr-4">Computed</th>
                      <th className="text-right pr-4">Filed</th>
                      <th className="text-right">Diff</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.category} className="border-b border-zinc-800 hover:bg-zinc-800/40">
                        <td className="py-1 pr-4">{r.category}</td>
                        <td className="text-right pr-4">{r.computed != null ? fmt(r.computed) : '—'}</td>
                        <td className="text-right pr-4">{r.filed != null ? fmt(r.filed) : '—'}</td>
                        <td className={`text-right ${diffColor(r.diff)}`}>
                          {r.diff != null ? fmt(r.diff) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-zinc-400 text-sm">
          Run the engine and import an AEAT XML to see the comparison.
        </p>
      )}
    </div>
  );
}
