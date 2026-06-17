import { OpenPositionsTable } from './OpenPositionsTable';
import { YearTable } from './YearTable';
import type { EngineResult } from '../types/api';

interface Props {
  result: EngineResult | null;
}

export function StocksTab({ result }: Props) {
  if (!result) return <p className="text-zinc-400 text-sm">Run the engine to see stock data.</p>;
  if (!result.has_stock_data)
    return <p className="text-zinc-400 text-sm">No stock data found in input directory.</p>;
  return (
    <div className="space-y-6">
      <YearTable rows={result.stock_years} reportUrl="/api/report/stocks" />
      <OpenPositionsTable positions={result.open_stock_positions} title="Open Positions" />
    </div>
  );
}
