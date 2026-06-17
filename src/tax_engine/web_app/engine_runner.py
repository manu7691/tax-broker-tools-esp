"""Orchestrate all tax engines and return structured results for the web UI."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass
class StockYearResult:
    year: int
    gains: float
    losses: float
    blocked_losses: float
    fees: float
    net: float
    taxable: float
    tax_due: float


@dataclass
class CryptoYearResult:
    year: int
    gains: float
    losses: float
    blocked_losses: float
    fees: float
    net: float
    taxable: float
    tax_due: float


@dataclass
class EngineResult:
    computed_at: str
    max_year: int
    years: list[int]
    stock_years: list[StockYearResult]
    crypto_years: list[CryptoYearResult]
    hacienda_years: list[dict[str, Any]]  # Decimal already converted to float
    open_stock_positions: list[dict[str, Any]]
    open_crypto_positions: list[dict[str, Any]]
    has_stock_data: bool
    has_crypto_data: bool
    warnings: list[str]
    errors: list[str]


@dataclass
class EngineRunOutput:
    result: EngineResult
    stock_engine: Any  # TaxEngine | None
    stock_securities: Any  # list[SecurityResult] | None
    crypto_engine: Any  # CryptoTaxEngine | None
    espp_discounts: dict[int, Decimal] = field(default_factory=dict)
    espp_early_sales: dict[int, Decimal] = field(default_factory=dict)
    opening_losses: dict[int, Decimal] = field(default_factory=dict)
    savings_income: dict[int, Any] = field(default_factory=dict)


def run_engines(input_dir: Path) -> EngineRunOutput:
    from tax_engine import load_rsu_events
    from tax_engine.cli_main import (
        build_espp_purchase_map,
        build_portfolio_or_engine,
        calculate_espp_discounts,
        detect_espp_early_sales,
        load_events_from_excel,
        load_options_stock_events,
        load_orders_from_excel,
        load_prior_losses,
        load_savings_income,
        load_security_config,
    )
    from tax_engine.crypto_engine import CryptoTaxEngine
    from tax_engine.crypto_parser import load_crypto_trades, trades_to_events_by_coin
    from tax_engine.report import ReportRenderer
    from tax_engine.revolut_parser import load_revolut_events, merge_savings_income
    from tax_engine.securities import load_securities_config

    max_year = date.today().year - 1
    warnings: list[str] = []
    errors: list[str] = []

    opening_losses = load_prior_losses(input_dir / "prior_losses.json")
    savings_income = load_savings_income(input_dir / "savings_income.json")

    config_symbol, config_isin = load_security_config(input_dir)
    securities_config = load_securities_config(input_dir)
    all_securities = (input_dir / "securities.json").exists()

    revolut_events: list[Any] = []
    try:
        revolut_events, revolut_income = load_revolut_events(
            input_dir,
            isin=config_isin,
            symbol=config_symbol,
            all_securities=all_securities,
            isin_map=securities_config.isin_map,
        )
        if revolut_income:
            savings_income = merge_savings_income(savings_income, revolut_income)
    except Exception:
        pass  # Revolut data is optional

    espp_events: list[Any] = []
    if (input_dir / "espp" / "BenefitHistory.xlsx").exists():
        try:
            espp_events = load_events_from_excel(input_dir)
        except Exception as e:
            warnings.append(f"ESPP load failed: {e}")

    sell_events = load_orders_from_excel(input_dir)

    rsu_events: list[Any] = []
    try:
        rsu_events = load_rsu_events(input_dir / "rsu")
    except Exception as e:
        warnings.append(f"RSU load failed: {e}")

    options_events: list[Any] = []
    try:
        options_events = load_options_stock_events(input_dir)
    except Exception as e:
        warnings.append(f"Options load failed: {e}")

    etrade_events = espp_events + sell_events + rsu_events + options_events

    stock_engine = None
    stock_securities = None
    espp_discounts: dict[int, Decimal] = {}
    espp_early_sales: dict[int, Decimal] = {}

    if etrade_events or revolut_events:
        try:
            stock_engine, stock_securities, _ = build_portfolio_or_engine(
                etrade_events,
                revolut_events,
                all_securities=all_securities,
                securities_config=securities_config,
                primary_symbol=config_symbol,
                primary_isin=config_isin,
            )
            espp_discounts = calculate_espp_discounts(input_dir)
            espp_map = build_espp_purchase_map(input_dir)
            espp_early_sales, _ = detect_espp_early_sales(
                stock_engine.processed_events, espp_map
            )
        except Exception as e:
            errors.append(f"Stock engine failed: {e}\n{traceback.format_exc()}")
            stock_engine = None

    crypto_engine = None
    crypto_dir = input_dir / "crypto"
    if crypto_dir.exists():
        try:
            trades = load_crypto_trades(crypto_dir)
            if trades:
                events_by_coin = trades_to_events_by_coin(trades)
                if events_by_coin:
                    crypto_engine = CryptoTaxEngine()
                    crypto_engine.process(events_by_coin)
        except Exception as e:
            errors.append(f"Crypto engine failed: {e}")

    stock_years: list[StockYearResult] = []
    if stock_engine:
        for s in stock_engine.get_all_yearly_summaries():
            if s.year <= max_year:
                stock_years.append(
                    StockYearResult(
                        year=s.year,
                        gains=float(s.total_gains),
                        losses=float(s.total_losses),
                        blocked_losses=float(s.blocked_losses),
                        fees=float(s.total_fees_eur),
                        net=float(s.net_gain_loss),
                        taxable=float(s.taxable_gain),
                        tax_due=float(s.tax_due),
                    )
                )

    crypto_years: list[CryptoYearResult] = []
    if crypto_engine:
        for year, s in sorted(crypto_engine.combined_summaries().items()):
            if year <= max_year:
                crypto_years.append(
                    CryptoYearResult(
                        year=year,
                        gains=float(s.total_gains),
                        losses=float(s.total_losses),
                        blocked_losses=float(s.blocked_losses),
                        fees=float(s.total_fees_eur),
                        net=float(s.net_gain_loss),
                        taxable=float(s.taxable_gain),
                        tax_due=float(s.tax_due),
                    )
                )

    hacienda_years: list[dict[str, Any]] = []
    if stock_engine:
        try:
            renderer = ReportRenderer(stock_engine)
            transm_ctx = renderer._transmisiones_context(max_year=max_year)
            si_arg = savings_income if savings_income else None
            ol_arg = opening_losses if opening_losses else None
            loss_ctx = renderer._loss_context(si_arg, ol_arg, max_year=max_year)
            espp_early_arg = espp_early_sales if espp_early_sales else None
            h_ctx = renderer._hacienda_summary_context(
                transm_ctx["transm_rows"], loss_ctx, si_arg, espp_early_arg, max_year=max_year
            )
            for row in h_ctx["hacienda_years"]:
                hacienda_years.append(
                    {
                        k: float(v)
                        if isinstance(v, Decimal)
                        else (v.isoformat() if isinstance(v, date) else v)
                        for k, v in row.items()
                    }
                )
        except Exception as e:
            errors.append(f"Hacienda context failed: {e}")

    # Annotate each hacienda row with the crypto net for that year
    crypto_by_year = {r.year: r for r in crypto_years}
    for row in hacienda_years:
        y = row["year"]
        cr = crypto_by_year.get(y)
        row["crypto_net"] = cr.net if cr else 0.0
        row["combined_net"] = row.get("saldo_neto", 0.0) + row["crypto_net"]

    open_stock_positions: list[dict[str, Any]] = []
    if stock_securities:
        for r in stock_securities:
            st = r.engine.state
            if st.total_shares > 0:
                open_stock_positions.append(
                    {
                        "symbol": r.security.label,
                        "shares": float(st.total_shares),
                        "cost_basis_eur": float(st.total_portfolio_cost_eur),
                        "avg_cost_eur": float(st.avg_cost_eur),
                    }
                )
    elif stock_engine and stock_engine.state.total_shares > 0:
        st = stock_engine.state
        open_stock_positions.append(
            {
                "symbol": config_symbol or "Stock",
                "shares": float(st.total_shares),
                "cost_basis_eur": float(st.total_portfolio_cost_eur),
                "avg_cost_eur": float(st.avg_cost_eur),
            }
        )

    open_crypto_positions: list[dict[str, Any]] = []
    if crypto_engine:
        for pos in crypto_engine.open_positions():
            qty = float(pos.quantity)
            open_crypto_positions.append(
                {
                    "symbol": pos.coin,
                    "shares": qty,
                    "cost_basis_eur": float(pos.cost_basis_eur),
                    "avg_cost_eur": float(pos.cost_basis_eur) / qty if qty > 0 else 0.0,
                }
            )

    all_years = sorted({r.year for r in stock_years} | {r.year for r in crypto_years})

    result = EngineResult(
        computed_at=datetime.now().isoformat(timespec="seconds"),
        max_year=max_year,
        years=all_years,
        stock_years=stock_years,
        crypto_years=crypto_years,
        hacienda_years=hacienda_years,
        open_stock_positions=open_stock_positions,
        open_crypto_positions=open_crypto_positions,
        has_stock_data=bool(etrade_events or revolut_events),
        has_crypto_data=bool(crypto_engine),
        warnings=warnings,
        errors=errors,
    )

    return EngineRunOutput(
        result=result,
        stock_engine=stock_engine,
        stock_securities=stock_securities,
        crypto_engine=crypto_engine,
        espp_discounts=espp_discounts,
        espp_early_sales=espp_early_sales,
        opening_losses=opening_losses,
        savings_income=savings_income,
    )


def generate_stock_html(output: EngineRunOutput, lang: str = "en") -> str | None:
    if output.stock_engine is None:
        return None
    from tax_engine.report import ReportRenderer

    renderer = ReportRenderer(output.stock_engine)
    return renderer.generate_html_content(
        lang=lang,
        espp_discounts=output.espp_discounts or None,
        espp_early_sale_discounts=output.espp_early_sales or None,
        opening_losses=output.opening_losses or None,
        savings_income=output.savings_income or None,
        securities=output.stock_securities,
    )


def generate_crypto_html(output: EngineRunOutput, lang: str = "en") -> str | None:
    if output.crypto_engine is None:
        return None
    return output.crypto_engine.generate_html(lang=lang)
