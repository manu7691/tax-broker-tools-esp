"""
CLI-level tests for the crypto and combined reports.

The exchange CSVs are written to a temp dir and parsed through the real CLIs;
the ECB network is mocked (fx pinned to 1.0, so EUR == the USD quote) for
deterministic assertions. Mirrors the column formats shipped in
``docs/crypto-pionex.example.csv`` / ``docs/crypto-binance.example.csv``.
"""

import sys
from decimal import Decimal
from unittest.mock import patch

import pytest

from tax_engine.crypto_engine import CryptoTaxEngine
from tax_engine.models import YearlyTaxSummary

PIONEX_CSV = """date(UTC+0),symbol,side,executed_qty,amount,fee,fee_coin
2024-02-12 09:30:00,BTC_USDT,BUY,0.5,24000,0,USDT
2024-11-08 14:05:00,BTC_USDT,SELL,0.2,14400,14.40,USDT
2025-03-03 10:15:00,BTC_USDT,SELL,0.3,25200,25.20,USDT
2024-03-20 11:00:00,ETH_USDT,BUY,4,14400,0,USDT
2025-06-10 16:45:00,ETH_USDT,SELL,4,10000,10.00,USDT
2024-09-01 08:00:00,SOL_USDT,BUY,50,7250,0,USDT
"""

BINANCE_CSV = """Time,Pair,Side,Executed,Amount,Fee
24-01-15 10:30:00,SOLUSDC,BUY,50SOL,4750USDC,0.05SOL
25-04-22 12:00:00,SOLUSDT,SELL,60SOL,10200USDT,10.20USDT
"""


def _write_crypto_inputs(crypto_dir):
    (crypto_dir / "pionex").mkdir(parents=True)
    (crypto_dir / "binance").mkdir(parents=True)
    (crypto_dir / "pionex" / "trading.csv").write_text(PIONEX_CSV)
    (crypto_dir / "binance" / "Spot-Trade-History.csv").write_text(BINANCE_CSV)


@pytest.fixture
def no_network():
    """Pin every ECB lookup to 1.0 EUR/USD and skip the bulk prefetch."""
    with (
        patch(
            "tax_engine.ecb_rates.ECBRateFetcher.get_rate",
            side_effect=lambda *a, **k: Decimal("1"),
        ),
        patch("tax_engine.ecb_rates.ECBRateFetcher.get_rates_bulk", return_value={}),
    ):
        yield


def test_tax_crypto_cli_writes_csv_and_html(tmp_path, no_network, capsys):
    from tax_engine import cli_crypto

    crypto_dir = tmp_path / "crypto"
    _write_crypto_inputs(crypto_dir)
    out_dir = tmp_path / "out"

    argv = ["tax-crypto", "--input-dir", str(crypto_dir), "--output-dir", str(out_dir)]
    with patch.object(sys, "argv", argv):
        cli_crypto.main()

    # One disposals CSV plus one HTML report per language.
    csvs = list(out_dir.glob("crypto_disposals_*.csv"))
    assert len(csvs) == 1
    assert len(list(out_dir.glob("crypto_tax_report_EN_*.html"))) == 1
    assert len(list(out_dir.glob("crypto_tax_report_ES_*.html"))) == 1

    # 4 disposals: 2 BTC sells + 1 ETH sell + 1 SOL sell.
    data_rows = csvs[0].read_text().strip().splitlines()
    assert len(data_rows) - 1 == 4

    out = capsys.readouterr().out
    assert "BTC" in out and "ETH" in out and "SOL" in out


def test_tax_crypto_cli_handles_no_data(tmp_path, capsys):
    from tax_engine import cli_crypto

    empty = tmp_path / "empty"
    empty.mkdir()
    argv = ["tax-crypto", "--input-dir", str(empty), "--output-dir", str(tmp_path)]
    with patch.object(sys, "argv", argv):
        cli_crypto.main()

    assert "no trades found" in capsys.readouterr().out
    assert list(tmp_path.glob("crypto_*.csv")) == []


def test_tax_combined_cli_crypto_only(tmp_path, no_network, capsys):
    from tax_engine import cli_combined

    crypto_dir = tmp_path / "crypto"
    _write_crypto_inputs(crypto_dir)
    stock_dir = tmp_path / "stock"  # empty → crypto-only combined report
    stock_dir.mkdir()
    out_dir = tmp_path / "out"

    argv = [
        "tax-combined",
        "--input-dir",
        str(stock_dir),
        "--crypto-dir",
        str(crypto_dir),
        "--output-dir",
        str(out_dir),
        "--lang",
        "en",
    ]
    with patch.object(sys, "argv", argv):
        cli_combined.main()

    assert len(list(out_dir.glob("combined_tax_report_EN_*.html"))) == 1
    out = capsys.readouterr().out
    assert "COMBINED SAVINGS BASE" in out
    # Both crypto years appear in the per-year table.
    assert "2024" in out and "2025" in out


def test_tax_combined_cli_handles_no_data(tmp_path, capsys):
    from tax_engine import cli_combined

    stock_dir = tmp_path / "stock"
    crypto_dir = tmp_path / "crypto"
    stock_dir.mkdir()
    crypto_dir.mkdir()
    out_dir = tmp_path / "out"

    argv = [
        "tax-combined",
        "--input-dir",
        str(stock_dir),
        "--crypto-dir",
        str(crypto_dir),
        "--output-dir",
        str(out_dir),
    ]
    with patch.object(sys, "argv", argv):
        cli_combined.main()

    assert "no data found in either directory" in capsys.readouterr().out
    assert not out_dir.exists() or list(out_dir.glob("*.html")) == []


def test_merge_yearly_summaries_sums_per_year():
    stocks = {2024: YearlyTaxSummary(year=2024, total_gains=Decimal("100"))}
    crypto = {
        2024: YearlyTaxSummary(year=2024, total_losses=Decimal("-40")),
        2025: YearlyTaxSummary(year=2025, total_gains=Decimal("30")),
    }
    merged = CryptoTaxEngine.merge_yearly_summaries([stocks, crypto])

    assert merged[2024].total_gains == Decimal("100")
    assert merged[2024].total_losses == Decimal("-40")
    assert merged[2024].net_gain_loss == Decimal("60")
    assert merged[2025].total_gains == Decimal("30")
