import { useEffect, useState } from 'react';
import { Header } from './components/Header';
import { StocksTab } from './components/StocksTab';
import { CryptoTab } from './components/CryptoTab';
import { WorkIncomeTab } from './components/WorkIncomeTab';
import { RealEstateTab } from './components/RealEstateTab';
import { HaciendaTab } from './components/HaciendaTab';
import { useApi } from './hooks/useApi';

type Tab = 'stocks' | 'crypto' | 'work' | 'realestate' | 'hacienda';

const TABS: { id: Tab; label: string }[] = [
  { id: 'stocks', label: 'Stocks' },
  { id: 'crypto', label: 'Crypto' },
  { id: 'work', label: 'Work Income' },
  { id: 'realestate', label: 'Real Estate' },
  { id: 'hacienda', label: 'Hacienda' },
];

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>('stocks');
  const {
    result,
    manualData,
    comparison,
    status,
    error,
    runEngine,
    saveManual,
    loadComparison,
    loadInitialData,
  } = useApi();

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  const years = result?.years ?? [];

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col">
      <Header
        status={status}
        computedAt={result?.computed_at ?? null}
        error={error}
        onRun={runEngine}
      />
      <nav className="flex border-b border-zinc-700 bg-zinc-900">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`px-5 py-3 text-sm font-medium transition-colors ${
              activeTab === t.id
                ? 'text-zinc-100 border-b-2 border-blue-400'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        {activeTab === 'stocks' && <StocksTab result={result} />}
        {activeTab === 'crypto' && <CryptoTab result={result} />}
        {activeTab === 'work' && (
          <WorkIncomeTab years={years} manualData={manualData} onSave={saveManual} />
        )}
        {activeTab === 'realestate' && (
          <RealEstateTab years={years} manualData={manualData} onSave={saveManual} />
        )}
        {activeTab === 'hacienda' && (
          <HaciendaTab comparison={comparison} onImportComplete={loadComparison} />
        )}
      </main>
    </div>
  );
}
