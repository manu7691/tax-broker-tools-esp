export interface StockYearResult {
  year: number;
  gains: number;
  losses: number;
  blocked_losses: number;
  fees: number;
  net: number;
  taxable: number;
  tax_due: number;
}

export interface CryptoYearResult {
  year: number;
  gains: number;
  losses: number;
  blocked_losses: number;
  fees: number;
  net: number;
  taxable: number;
  tax_due: number;
}

export interface OpenPosition {
  ticker: string;
  shares: number;
  avg_cost: number;
}

export interface EngineResult {
  computed_at: string;
  max_year: number;
  years: number[];
  stock_years: StockYearResult[];
  crypto_years: CryptoYearResult[];
  open_stock_positions: OpenPosition[];
  open_crypto_positions: OpenPosition[];
  has_stock_data: boolean;
  has_crypto_data: boolean;
  warnings: string[];
  errors: string[];
}

export interface ManualYear {
  salary_eur: number;
  other_eur: number;
  notes: string;
}

export interface RealEstateYear {
  rental_eur: number;
  gains_eur: number;
  notes: string;
}

export interface AeatFiled {
  work_income_net?: number;
  dividends?: number;
  interest?: number;
  capital_gains_net?: number;
  base_ahorro?: number;
  foreign_tax_deduction?: number;
  source?: string;
}

export interface ManualData {
  work_income: Record<string, ManualYear>;
  real_estate: Record<string, RealEstateYear>;
  aeat_filed: Record<string, AeatFiled>;
}

export interface ComparisonRow {
  year: number;
  category: string;
  computed: number | null;
  filed: number | null;
  diff: number | null;
}
