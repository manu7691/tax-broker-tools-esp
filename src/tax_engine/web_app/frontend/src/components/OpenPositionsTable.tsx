import { fmt, fmtShares } from '../utils/format';

interface Position {
  ticker: string;
  shares: number;
  avg_cost: number;
}

interface Props {
  positions: Position[];
  title: string;
}

export function OpenPositionsTable({ positions, title }: Props) {
  if (positions.length === 0) return null;
  return (
    <div className="mt-6">
      <h3 className="text-sm font-medium text-zinc-400 mb-2">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-zinc-300">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-zinc-700">
              <th className="text-left py-2 pr-4">Ticker</th>
              <th className="text-right pr-4">Shares</th>
              <th className="text-right">Avg Cost</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.ticker} className="border-b border-zinc-800 hover:bg-zinc-800/40">
                <td className="py-2 pr-4 font-medium text-zinc-100">{p.ticker}</td>
                <td className="text-right pr-4">{fmtShares(p.shares)}</td>
                <td className="text-right">{fmt(p.avg_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
