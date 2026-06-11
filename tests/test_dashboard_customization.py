import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from generate_charts import load_ticker_config
from tax_engine.market_data import fetch_company_name

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "src" / "tax_engine" / "templates" / "dashboard_template.html"


def test_securities_chart_template_and_generator_are_wired():
    """The per-security chart placeholder/card/i18n must match what generate_charts injects."""
    template = TEMPLATE.read_text(encoding="utf-8")
    # The generator replaces this exact token — it must exist in the template.
    assert "__SECURITIES_DATA__" in template
    assert 'id="securities-card"' in template
    assert 'id="securitiesChart"' in template
    # i18n keys present in both languages (each key appears once per locale block).
    assert template.count("securities_chart_title:") == 2
    assert template.count("securities_chart_invested:") == 2

    generator = (REPO_ROOT / "generate_charts.py").read_text(encoding="utf-8")
    assert '"__SECURITIES_DATA__"' in generator  # the replace target is still emitted

def test_load_ticker_config_txt(tmp_path):
    # Test loading from ticker.txt
    txt_file = tmp_path / "ticker.txt"
    txt_file.write_text("AAPL", encoding="utf-8")

    ticker, company_name = load_ticker_config(tmp_path)
    assert ticker == "AAPL"
    assert company_name is None

def test_load_ticker_config_json(tmp_path):
    # Test loading from ticker.json
    json_file = tmp_path / "ticker.json"
    json_file.write_text(json.dumps({"ticker": "DDOG", "company_name": "Datadog"}), encoding="utf-8")

    ticker, company_name = load_ticker_config(tmp_path)
    assert ticker == "DDOG"
    assert company_name == "Datadog"

def test_load_ticker_config_json_simple(tmp_path):
    # Test loading simple string from ticker.json
    json_file = tmp_path / "ticker.json"
    json_file.write_text(json.dumps("ESTC"), encoding="utf-8")

    ticker, company_name = load_ticker_config(tmp_path)
    assert ticker == "ESTC"
    assert company_name is None

@patch("requests.get")
def test_fetch_company_name_api(mock_get):
    # Mock search API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "quotes": [
            {"longname": "Datadog, Inc.", "shortname": "Datadog", "symbol": "DDOG"}
        ]
    }
    mock_get.return_value = mock_response

    name = fetch_company_name("DDOG")
    assert name == "Datadog, Inc."
    mock_get.assert_called_once_with(
        "https://query1.finance.yahoo.com/v1/finance/search?q=DDOG&quotesCount=1",
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=5
    )
