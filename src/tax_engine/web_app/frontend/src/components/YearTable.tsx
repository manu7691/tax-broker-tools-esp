import { fmt } from '../utils/format';

interface YearRow {
  year: number;
  gains: number;
  losses: number;
  blocked_losses: number;
  fees: number;
  net: number;
  taxable: number;
  tax_due: number;
}

interface Props {
  rows: YearRow[];
  reportUrl: string;
}

export function YearTable({ rows, reportUrl }: Props) {
  if (rows.length === 0) return <p className="text-zinc-400 text-sm">No data.</p>;
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-zinc-300">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-zinc-700">
              <th className="text-left py-2 pr-4">Year</th>
              <th className="text-right pr-4">Gains</th>
              <th className="text-right pr-4">Losses</th>
              <th className="text-right pr-4">Blocked</th>
              <th className="text-right pr-4">Fees</th>
              <th className="text-right pr-4">Net</th>
              <th className="text-right pr-4">Taxable</th>
              <th className="text-right">Tax Due</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.year} className="border-b border-zinc-800 hover:bg-zinc-800/40">
                <td className="py-2 pr-4 font-medium text-zinc-100">{r.year}</td>
                <td className="text-right pr-4 text-green-400">{fmt(r.gains)}</td>
                <td className="text-right pr-4 text-red-400">{fmt(r.losses)}</td>
                <td className="text-right pr-4 text-zinc-400">{fmt(r.blocked_losses)}</td>
                <td className="text-right pr-4 text-zinc-400">{fmt(r.fees)}</td>
                <td className={`text-right pr-4 ${r.net >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {fmt(r.net)}
                </td>
                <td className="text-right pr-4">{fmt(r.taxable)}</td>
                <td className="text-right text-blue-400">{fmt(r.tax_due)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <a
        href={reportUrl}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300"
      >
        View Full Report →
      </a>
    </div>
  );
}
