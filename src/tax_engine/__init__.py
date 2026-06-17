"""
Spanish Tax Engine for E-Trade RSUs and ESPP

A tax calculation engine implementing the Spanish FIFO cost basis method
for stocks acquired through RSU vesting and ESPP purchases.
"""

from .crypto_engine import CryptoTaxEngine, generate_combined_html
from .crypto_parser import (
    CryptoTrade,
    load_crypto_trades,
    trades_to_events_by_coin,
)
from .ecb_rates import ECBRateFetcher, prefetch_ecb_rates
from .models import (
    CarryforwardLedger,
    CarryforwardYear,
    EventType,
    ProcessedEvent,
    SavingsIncomeYear,
    SavingsLedger,
    SavingsLedgerYear,
    StockEvent,
    TaxEngineState,
    YearlyTaxSummary,
)
from .options_parser import load_options_events
from .portfolio import (
    PortfolioResult,
    SecurityResult,
    group_events_by_security,
    run_portfolio,
)
from .revolut_parser import load_revolut_events
from .rsu_parser import load_rsu_events
from .sample_data import (
    create_sample_dividends_by_symbol,
    create_sample_espp_map,
    create_sample_events_with_ecb_rates,
    create_sample_events_with_manual_fx,
    create_sample_multi_security_events,
    create_sample_savings_income,
)
from .securities import (
    IsinCache,
    SecuritiesConfig,
    Security,
    build_isin_resolver,
    grouping_key,
    load_securities_config,
    resolve_isin,
)
from .tax_engine import TaxEngine

__version__ = "0.1.0"

__all__ = [
    "EventType",
    "StockEvent",
    "ProcessedEvent",
    "YearlyTaxSummary",
    "CarryforwardLedger",
    "CarryforwardYear",
    "SavingsIncomeYear",
    "SavingsLedger",
    "SavingsLedgerYear",
    "TaxEngineState",
    "ECBRateFetcher",
    "prefetch_ecb_rates",
    "TaxEngine",
    "create_sample_events_with_manual_fx",
    "create_sample_events_with_ecb_rates",
    "create_sample_multi_security_events",
    "create_sample_savings_income",
    "create_sample_espp_map",
    "create_sample_dividends_by_symbol",
    "load_rsu_events",
    "load_options_events",
    "load_revolut_events",
    "Security",
    "SecuritiesConfig",
    "grouping_key",
    "resolve_isin",
    "build_isin_resolver",
    "IsinCache",
    "load_securities_config",
    "PortfolioResult",
    "SecurityResult",
    "group_events_by_security",
    "run_portfolio",
    "CryptoTaxEngine",
    "generate_combined_html",
    "CryptoTrade",
    "load_crypto_trades",
    "trades_to_events_by_coin",
]
