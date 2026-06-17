import { OpenPositionsTable } from './OpenPositionsTable';
import { YearTable } from './YearTable';
import type { EngineResult } from '../types/api';

interface Props {
  result: EngineResult | null;
}

export function CryptoTab({ result }: Props) {
  if (!result) return <p className="text-zinc-400 text-sm">Run the engine to see crypto data.</p>;
  if (!result.has_crypto_data)
    return <p className="text-zinc-400 text-sm">No crypto data found in input directory.</p>;
  return (
    <div className="space-y-6">
      <YearTable rows={result.crypto_years} reportUrl="/api/report/crypto" />
      <OpenPositionsTable positions={result.open_crypto_positions} title="Open Positions" />
    </div>
  );
}
