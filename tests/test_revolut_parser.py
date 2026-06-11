"""
Tests for the optional Revolut parser (revolut_parser.py).

Key behaviour under test:
- Two-section CSV ("Income from Sells" + "Other income & fees") is split correctly.
- Rows are filtered to the tracked security by ISIN (preferred) or ticker.
- Each kept sell row yields a BUY (acquisition) + SELL (sale) StockEvent with
  per-share prices derived from cost basis / proceeds.
- EUR rows convert 1:1; unsupported currencies are skipped; USD income uses ECB.
- Absent folder / missing filter key return empty results.
- merge_savings_income sums two per-year RCM maps without mutating inputs.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tax_engine.models import EventType, SavingsIncomeYear
from tax_engine.revolut_parser import (
    load_revolut_dividends_by_symbol,
    load_revolut_savings_income,
    load_revolut_trade_events,
    merge_savings_income,
)
from tax_engine.securities import IsinCache


@pytest.fixture()  # type: ignore[misc]
def isolated_isin_cache(tmp_path: Path, monkeypatch: Any) -> Any:
    """Isolate the persistent ISIN cache so all-securities resolution stays hermetic."""
    monkeypatch.setattr(IsinCache, "CACHE_FILE", tmp_path / ".isin_cache.json")
    IsinCache.clear()
    yield
    IsinCache.clear()


# Tracked security for these tests = Tesla.
TSLA_ISIN = "US88160R1014"

# Mirrors the real export: a header title line, a CSV header, data rows, a blank
# line, then the second section. Multiple tickers are present on purpose so the
# filter has something to exclude.
_SELLS_CSV = """Income from Sells
Date acquired,Date sold,Symbol,Security name,ISIN,Country,Quantity,Cost basis,Gross proceeds,Gross PnL,Currency
2020-07-02,2020-07-09,TSLA,Tesla,US88160R1014,US,0.00413701,5.00,5.69,0.69,USD
2020-07-06,2020-07-09,NVDA,NVIDIA,US67066G1040,US,0.06804582,26.61,28.05,1.44,USD
2021-10-21,2023-08-18,TSLA,Tesla,US88160R1014,US,0.03350847,9.99,7.17,-2.82,USD
2021-10-25,2023-08-18,ADBE,Adobe,US00724F1012,US,0.00807877,5.21,4.08,-1.13,USD

Other income & fees
Date,Symbol,Security name,ISIN,Country,Gross amount,Withholding tax,Net Amount,Currency
2022-03-15,TSLA,Tesla,US88160R1014,US,10.00,1.50,8.50,USD
2022-06-15,NVDA,NVIDIA,US67066G1040,US,4.00,0.60,3.40,USD
"""


@pytest.fixture()  # type: ignore[misc]
def input_dir(tmp_path: Path) -> Path:
    """An input dir with a Revolut CSV written to input/revolut/account.csv."""
    revolut_dir = tmp_path / "revolut"
    revolut_dir.mkdir(parents=True)
    (revolut_dir / "account.csv").write_text(_SELLS_CSV, encoding="utf-8")
    return tmp_path


class TestSellFiltering:
    def test_keeps_only_matching_isin(self, input_dir: Path) -> None:
        events = load_revolut_trade_events(input_dir, isin=TSLA_ISIN)
        # 2 TSLA rows -> 2 BUY + 2 SELL = 4 events (NVDA/ADBE excluded).
        assert len(events) == 4
        symbols = {e.notes.split("(")[1].rstrip(")") for e in events}
        assert symbols == {"TSLA"}

    def test_falls_back_to_symbol_when_no_isin(self, input_dir: Path) -> None:
        events = load_revolut_trade_events(input_dir, symbol="adbe")
        assert len(events) == 2  # one ADBE row -> BUY + SELL
        assert all("ADBE" in e.notes for e in events)

    def test_no_filter_key_returns_empty(self, input_dir: Path) -> None:
        assert load_revolut_trade_events(input_dir) == []

    def test_absent_folder_returns_empty(self, tmp_path: Path) -> None:
        assert load_revolut_trade_events(tmp_path, isin=TSLA_ISIN) == []


class TestSellSynthesis:
    def test_buy_and_sell_pair_per_row(self, input_dir: Path) -> None:
        events = load_revolut_trade_events(input_dir, isin=TSLA_ISIN)
        buys = [e for e in events if e.event_type == EventType.BUY]
        sells = [e for e in events if e.event_type == EventType.SELL]
        assert len(buys) == 2
        assert len(sells) == 2

    def test_dates_and_prices(self, input_dir: Path) -> None:
        events = load_revolut_trade_events(input_dir, isin=TSLA_ISIN)
        # First TSLA row: acquired 2020-07-02 qty 0.00413701 cost 5.00 -> sold 2020-07-09 proceeds 5.69
        buy = next(
            e for e in events if e.event_type == EventType.BUY and e.event_date == date(2020, 7, 2)
        )
        sell = next(
            e for e in events if e.event_type == EventType.SELL and e.event_date == date(2020, 7, 9)
        )
        assert buy.shares == Decimal("0.00413701")
        # 5.00 / 0.00413701 = 1208.598... rounded to 4dp
        assert buy.price_usd == (Decimal("5.00") / Decimal("0.00413701")).quantize(
            Decimal("0.0001")
        )
        assert sell.price_usd == (Decimal("5.69") / Decimal("0.00413701")).quantize(
            Decimal("0.0001")
        )

    def test_usd_rows_leave_fx_unresolved(self, input_dir: Path) -> None:
        # USD events defer FX to the ECB fetcher (fx_rate stays None at parse time).
        events = load_revolut_trade_events(input_dir, isin=TSLA_ISIN)
        assert all(e.fx_rate is None for e in events)

    def test_events_tagged_with_revolut_broker(self, input_dir: Path) -> None:
        events = load_revolut_trade_events(input_dir, isin=TSLA_ISIN)
        assert events  # sanity
        assert all(e.broker == "Revolut" for e in events)


class TestAllSecurities:
    """all_securities=True emits every ticker from the gains export, ISIN-tagged."""

    def test_emits_all_tickers_unfiltered(self, input_dir: Path) -> None:
        events = load_revolut_trade_events(input_dir, all_securities=True)
        # 4 sell rows (TSLA, NVDA, TSLA, ADBE) -> 4 BUY + 4 SELL = 8 events.
        assert len(events) == 8
        tickers = {e.symbol for e in events}
        assert tickers == {"TSLA", "NVDA", "ADBE"}

    def test_events_carry_symbol_and_isin(self, input_dir: Path) -> None:
        events = load_revolut_trade_events(input_dir, all_securities=True)
        nvda = next(e for e in events if e.symbol == "NVDA")
        assert nvda.isin == "US67066G1040"
        assert nvda.broker == "Revolut"
        # Every emitted event is ISIN-tagged (gains export carries ISIN per row).
        assert all(e.isin for e in events)

    def test_filtered_mode_still_tags_symbol_and_isin(self, input_dir: Path) -> None:
        # Even when filtering to one security, events should carry identity now.
        events = load_revolut_trade_events(input_dir, isin=TSLA_ISIN)
        assert all(e.symbol == "TSLA" and e.isin == TSLA_ISIN for e in events)

    def test_all_securities_income_unfiltered(self, input_dir: Path) -> None:
        with patch("tax_engine.revolut_parser.ECBRateFetcher.get_rate", return_value=Decimal("1")):
            income = load_revolut_savings_income(input_dir, all_securities=True)
        # Both the TSLA (2022) and NVDA (2022) dividend rows are summed.
        assert set(income) == {2022}
        assert income[2022].dividends_eur == Decimal("14.00")  # 10 + 4


class TestCurrencyHandling:
    def test_eur_rows_convert_one_to_one(self, tmp_path: Path) -> None:
        csv_text = (
            "Income from Sells\n"
            "Date acquired,Date sold,Symbol,Security name,ISIN,Country,Quantity,"
            "Cost basis,Gross proceeds,Gross PnL,Currency\n"
            "2021-01-01,2021-06-01,TSLA,Tesla,US88160R1014,US,2,100.00,150.00,50.00,EUR\n"
        )
        (tmp_path / "revolut").mkdir()
        (tmp_path / "revolut" / "eur.csv").write_text(csv_text, encoding="utf-8")
        events = load_revolut_trade_events(tmp_path, isin=TSLA_ISIN)
        assert {e.fx_rate for e in events} == {Decimal("1")}
        sell = next(e for e in events if e.event_type == EventType.SELL)
        assert sell.price_usd == Decimal("75.0000")  # 150 / 2

    def test_gbp_rows_defer_to_ecb(self, tmp_path: Path) -> None:
        # GBP is an ECB reference currency: events carry currency=GBP and an
        # unresolved fx_rate (the engine converts via the ECB GBP series per date).
        csv_text = (
            "Income from Sells\n"
            "Date acquired,Date sold,Symbol,Security name,ISIN,Country,Quantity,"
            "Cost basis,Gross proceeds,Gross PnL,Currency\n"
            "2021-01-01,2021-06-01,TSLA,Tesla,US88160R1014,US,2,100.00,150.00,50.00,GBP\n"
        )
        (tmp_path / "revolut").mkdir()
        (tmp_path / "revolut" / "gbp.csv").write_text(csv_text, encoding="utf-8")
        events = load_revolut_trade_events(tmp_path, isin=TSLA_ISIN)
        assert len(events) == 2
        assert all(e.currency == "GBP" and e.fx_rate is None for e in events)

    def test_truly_unsupported_currency_skipped(self, tmp_path: Path) -> None:
        # A currency the ECB does not publish is skipped (no rate source).
        csv_text = (
            "Income from Sells\n"
            "Date acquired,Date sold,Symbol,Security name,ISIN,Country,Quantity,"
            "Cost basis,Gross proceeds,Gross PnL,Currency\n"
            "2021-01-01,2021-06-01,TSLA,Tesla,US88160R1014,US,2,100.00,150.00,50.00,XYZ\n"
        )
        (tmp_path / "revolut").mkdir()
        (tmp_path / "revolut" / "xyz.csv").write_text(csv_text, encoding="utf-8")
        assert load_revolut_trade_events(tmp_path, isin=TSLA_ISIN) == []


# Account-movements export: full transaction log, no ISIN column, many types.
# DT appears only as buys (never sold) — the case that the gains export misses.
_MOVEMENTS_CSV = """Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate
2020-07-02T13:45:45.840502Z,,CASH TOP-UP,,,USD 5,USD,1.1267
2020-07-02T13:46:17.850572Z,TSLA,BUY - MARKET,0.00413701,USD 1208.60,USD 5,USD,1.1270
2020-07-09T14:47:08.498269Z,TSLA,SELL - MARKET,0.08223272,USD 1374.39,USD 112,USD,1.1302
2022-08-25T08:30:58.876448Z,TSLA,STOCK SPLIT,0.02233898,,USD 0,USD,1.0000
2023-06-25T10:18:16.585364Z,TSLA,TRANSFER FROM REVOLUT TRADING LTD TO REVOLUT SECURITIES EUROPE UAB,0.03350847,,USD 0,USD,1.0899
2023-08-07T19:58:10.939Z,DT,BUY - MARKET,0.02272727,USD 48.40,USD 1.10,USD,1.1018
2023-08-18T13:57:54.620Z,DT,BUY - MARKET,0.17277259,USD 46.13,USD 9.05,USD,1.0887
2023-08-18T13:30:01.749Z,ADBE,SELL - MARKET,0.00807877,USD 505.03,USD 2.98,USD,1.0878
"""


class TestMovementsFormat:
    @pytest.fixture()  # type: ignore[misc]
    def movements_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "revolut").mkdir(parents=True)
        (tmp_path / "revolut" / "account-data.csv").write_text(_MOVEMENTS_CSV, encoding="utf-8")
        return tmp_path

    def test_dt_buys_only_no_sell(self, movements_dir: Path) -> None:
        # DT was bought twice, never sold -> two BUY events, no SELL.
        events = load_revolut_trade_events(movements_dir, symbol="DT")
        assert [e.event_type for e in events] == [EventType.BUY, EventType.BUY]
        assert all(e.broker == "Revolut" for e in events)
        assert all("DT" in e.notes for e in events)

    def test_dt_buy_parsed_values(self, movements_dir: Path) -> None:
        events = load_revolut_trade_events(movements_dir, symbol="DT")
        first = next(e for e in events if e.event_date == date(2023, 8, 7))
        assert first.shares == Decimal("0.02272727")
        assert first.price_usd == Decimal("48.40")  # from "USD 48.40"
        assert first.fx_rate is None  # USD -> ECB resolves per date

    def test_filters_other_tickers(self, movements_dir: Path) -> None:
        # TSLA buy/sell and ADBE sell must be excluded when tracking DT.
        events = load_revolut_trade_events(movements_dir, symbol="DT")
        assert all("TSLA" not in e.notes and "ADBE" not in e.notes for e in events)

    def test_tsla_buy_sell_split_transfer(self, movements_dir: Path) -> None:
        # For TSLA: BUY + SELL become events; STOCK SPLIT and TRANSFER are skipped.
        events = load_revolut_trade_events(movements_dir, symbol="TSLA")
        assert [e.event_type for e in events] == [EventType.BUY, EventType.SELL]

    def test_isin_only_config_still_skips_movements(self, movements_dir: Path) -> None:
        # Movements export has no ISIN; without a ticker it cannot be filtered.
        assert load_revolut_trade_events(movements_dir, isin="US2681501092") == []

    def test_no_dividend_section_means_no_income(self, movements_dir: Path) -> None:
        assert load_revolut_savings_income(movements_dir, symbol="DT") == {}


class TestMovementsAllSecurities:
    """all_securities=True emits every ticker from the movements export and
    resolves each ticker→ISIN (user map → ISINs learned from the gains export)."""

    @pytest.fixture()  # type: ignore[misc]
    def movements_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "revolut").mkdir(parents=True)
        (tmp_path / "revolut" / "account-data.csv").write_text(_MOVEMENTS_CSV, encoding="utf-8")
        return tmp_path

    def test_emits_all_tickers_unfiltered(
        self, movements_dir: Path, isolated_isin_cache: Any
    ) -> None:
        events = load_revolut_trade_events(movements_dir, all_securities=True)
        assert {e.symbol for e in events} == {"TSLA", "DT", "ADBE"}
        # No ISIN source available -> every movements event groups by ticker.
        assert all(e.isin is None for e in events)
        assert all(e.broker == "Revolut" for e in events)

    def test_per_ticker_split_does_not_cross_tickers(
        self, movements_dir: Path, isolated_isin_cache: Any
    ) -> None:
        # The TSLA split must rescale only TSLA trades; DT buys are untouched.
        events = load_revolut_trade_events(movements_dir, all_securities=True)
        dt = [e for e in events if e.symbol == "DT"]
        assert [e.shares for e in dt] == [Decimal("0.02272727"), Decimal("0.17277259")]

    def test_isin_map_resolves_ticker(self, movements_dir: Path, isolated_isin_cache: Any) -> None:
        dt_isin = "US26614N1028"
        events = load_revolut_trade_events(
            movements_dir, all_securities=True, isin_map={"DT": dt_isin}
        )
        assert all(e.isin == dt_isin for e in events if e.symbol == "DT")

    def test_learns_isin_from_gains_export_in_same_folder(
        self, movements_dir: Path, isolated_isin_cache: Any
    ) -> None:
        # Drop a gains export (which carries ISINs) next to the movements file.
        (movements_dir / "revolut" / "gains.csv").write_text(_SELLS_CSV, encoding="utf-8")
        events = load_revolut_trade_events(movements_dir, all_securities=True)
        # Every TSLA event (gains AND movements) now carries the learned ISIN.
        tsla = [e for e in events if e.symbol == "TSLA"]
        assert tsla and all(e.isin == TSLA_ISIN for e in tsla)
        # DT appears only in movements (not the gains export) -> stays unresolved.
        assert all(e.isin is None for e in events if e.symbol == "DT")


class TestStockSplitNormalization:
    """A tracked ticker that splits must not break FIFO (more sold than bought)."""

    # TSLA: buy pre-split, 3:1 split adds 2x shares, sell the full post-split qty.
    _SPLIT_CSV = (
        "Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate\n"
        "2021-10-21T19:57:01.240112Z,TSLA,BUY - MARKET,0.01116949,USD 894.40,USD 9.99,USD,1.16\n"
        "2022-08-25T08:30:58.876448Z,TSLA,STOCK SPLIT,0.02233898,,USD 0,USD,1.00\n"
        "2023-08-18T13:30:01.415Z,TSLA,SELL - MARKET,0.03350847,USD 213.98,USD 6.07,USD,1.08\n"
    )

    @pytest.fixture()  # type: ignore[misc]
    def split_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "revolut").mkdir(parents=True)
        (tmp_path / "revolut" / "movements.csv").write_text(self._SPLIT_CSV, encoding="utf-8")
        return tmp_path

    def test_buy_rescaled_to_post_split_terms(self, split_dir: Path) -> None:
        events = load_revolut_trade_events(split_dir, symbol="TSLA")
        buy = next(e for e in events if e.event_type == EventType.BUY)
        sell = next(e for e in events if e.event_type == EventType.SELL)
        # Buy shares scaled 0.01116949 -> 0.03350847 (×3); matches the sell quantity.
        assert buy.shares == Decimal("0.03350847")
        assert sell.shares == Decimal("0.03350847")
        # Price divided by 3; cost basis preserved (~9.99).
        assert buy.price_usd == Decimal("894.40") / 3
        assert (buy.shares * buy.price_usd).quantize(Decimal("0.01")) == Decimal("9.99")

    def test_engine_processes_without_oversell(self, split_dir: Path) -> None:
        from tax_engine import TaxEngine

        events = load_revolut_trade_events(split_dir, symbol="TSLA")
        engine = TaxEngine()
        engine.process_all(events)  # must not raise "sell more than held"
        assert engine.state.total_shares == Decimal("0")
        # Sold at 213.98 vs cost-basis ~298.13/share -> a realized loss.
        sells = [pe for pe in engine.processed_events if pe.event.event_type == EventType.SELL]
        assert len(sells) == 1
        assert sells[0].realized_gain_loss < 0

    def test_unknown_holding_split_skipped_gracefully(self, tmp_path: Path) -> None:
        # A split with no prior holding can't be sized; warn and leave shares as-is.
        csv_text = (
            "Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate\n"
            "2022-08-25T08:30:58Z,TSLA,STOCK SPLIT,0.5,,USD 0,USD,1.00\n"
            "2023-01-01T00:00:00Z,TSLA,BUY - MARKET,1,USD 100,USD 100,USD,1.00\n"
        )
        (tmp_path / "revolut").mkdir()
        (tmp_path / "revolut" / "m.csv").write_text(csv_text, encoding="utf-8")
        events = load_revolut_trade_events(tmp_path, symbol="TSLA")
        assert [e.event_type for e in events] == [EventType.BUY]
        assert events[0].shares == Decimal("1")  # untouched


class TestTitlelessGainsFormat:
    def test_parses_header_only_gains_file(self, tmp_path: Path) -> None:
        # Some exports omit the "Income from Sells" title and start at the header.
        csv_text = (
            "Date acquired,Date sold,Symbol,Security name,ISIN,Country,Quantity,"
            "Cost basis,Gross proceeds,Gross PnL,Currency\n"
            "2021-01-01,2021-06-01,TSLA,Tesla,US88160R1014,US,2,100.00,150.00,50.00,USD\n"
        )
        (tmp_path / "revolut").mkdir()
        (tmp_path / "revolut" / "data.csv").write_text(csv_text, encoding="utf-8")
        events = load_revolut_trade_events(tmp_path, isin=TSLA_ISIN)
        assert [e.event_type for e in events] == [EventType.BUY, EventType.SELL]


class TestSavingsIncome:
    def test_usd_income_filtered_and_converted(self, input_dir: Path) -> None:
        with patch(
            "tax_engine.revolut_parser.ECBRateFetcher.get_rate", return_value=Decimal("0.9")
        ):
            income = load_revolut_savings_income(input_dir, isin=TSLA_ISIN)
        assert set(income) == {2022}  # only the TSLA dividend row
        entry = income[2022]
        assert entry.dividends_eur == Decimal("9.00")  # 10.00 * 0.9
        assert entry.foreign_tax_eur == Decimal("1.35")  # 1.50 * 0.9

    def test_income_absent_without_filter(self, input_dir: Path) -> None:
        assert load_revolut_savings_income(input_dir) == {}

    def test_dividends_by_symbol(self, input_dir: Path) -> None:
        # The "Other income & fees" section has a TSLA (10.00) and an NVDA (4.00)
        # dividend row; per-symbol totals are gross EUR (ECB rate mocked to 1).
        with patch("tax_engine.revolut_parser.ECBRateFetcher.get_rate", return_value=Decimal("1")):
            by_symbol = load_revolut_dividends_by_symbol(input_dir)
        assert by_symbol == {"TSLA": Decimal("10.00"), "NVDA": Decimal("4.00")}

    def test_dividends_by_symbol_absent_folder(self, tmp_path: Path) -> None:
        assert load_revolut_dividends_by_symbol(tmp_path) == {}

    def test_symbol_less_income_classified_as_interest(self, tmp_path: Path) -> None:
        # A symbol-bearing row is a dividend; a symbol-less row is cash interest.
        csv_text = (
            "Other income & fees\n"
            "Date,Symbol,Security name,ISIN,Country,Gross amount,Withholding tax,Net Amount,Currency\n"
            "2022-03-15,TSLA,Tesla,US88160R1014,US,10.00,0.00,10.00,EUR\n"
            "2022-07-01,,Cash,,,5.00,0.00,5.00,EUR\n"
        )
        (tmp_path / "revolut").mkdir()
        (tmp_path / "revolut" / "income.csv").write_text(csv_text, encoding="utf-8")
        income = load_revolut_savings_income(tmp_path, all_securities=True)
        assert income[2022].dividends_eur == Decimal("10.00")  # TSLA has a symbol
        assert income[2022].interest_eur == Decimal("5.00")  # symbol-less → interest


class TestMergeSavingsIncome:
    def test_sums_without_mutating_inputs(self) -> None:
        base = {2022: SavingsIncomeYear(year=2022, dividends_eur=Decimal("100"))}
        extra = {
            2022: SavingsIncomeYear(
                year=2022, dividends_eur=Decimal("9"), foreign_tax_eur=Decimal("1")
            ),
            2023: SavingsIncomeYear(year=2023, dividends_eur=Decimal("5")),
        }
        merged = merge_savings_income(base, extra)
        assert merged[2022].dividends_eur == Decimal("109")
        assert merged[2022].foreign_tax_eur == Decimal("1")
        assert merged[2023].dividends_eur == Decimal("5")
        # Inputs untouched.
        assert base[2022].dividends_eur == Decimal("100")
