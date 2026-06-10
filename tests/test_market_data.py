import datetime
from unittest.mock import patch, MagicMock
from decimal import Decimal
from datetime import date
from tax_engine.market_data import fetch_latest_price, fetch_historical_market_data

@patch("requests.get")
def test_fetch_latest_price_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 123.45
                    }
                }
            ]
        }
    }
    mock_get.return_value = mock_response
    
    price = fetch_latest_price("AAPL")
    assert price == Decimal("123.45")
    mock_get.assert_called_once_with(
        "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=5
    )

@patch("requests.get")
def test_fetch_latest_price_failure(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_get.return_value = mock_response
    
    price = fetch_latest_price("AAPL")
    assert price is None

@patch("tax_engine.market_data.fetch_latest_price")
@patch("requests.get")
def test_fetch_historical_market_data_with_live_price(mock_get, mock_fetch_latest):
    # Mock historical quotes response (200 status code)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "chart": {
            "result": [
                {
                    "timestamp": [1600000000],
                    "indicators": {
                        "quote": [
                            {
                                "close": [100.0]
                            }
                        ]
                    }
                }
            ]
        }
    }
    mock_get.return_value = mock_response
    
    # Mock live price
    mock_fetch_latest.return_value = Decimal("105.50")
    
    quotes, current_price, sma50, sma200, signal, advice = fetch_historical_market_data("AAPL", date(2023, 1, 1))
    
    assert len(quotes) == 1
    assert quotes[0]["close"] == 100.0
    assert current_price == 105.50
    mock_fetch_latest.assert_called_once_with("AAPL")

@patch("tax_engine.market_data.fetch_latest_price")
@patch("requests.get")
def test_fetch_historical_market_data_fallback_to_close(mock_get, mock_fetch_latest):
    # Mock historical quotes response (200 status code)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "chart": {
            "result": [
                {
                    "timestamp": [1600000000],
                    "indicators": {
                        "quote": [
                            {
                                "close": [100.0]
                            }
                        ]
                    }
                }
            ]
        }
    }
    mock_get.return_value = mock_response
    
    # Mock live price failing / returning None
    mock_fetch_latest.return_value = None
    
    quotes, current_price, sma50, sma200, signal, advice = fetch_historical_market_data("AAPL", date(2023, 1, 1))
    
    assert len(quotes) == 1
    assert quotes[0]["close"] == 100.0
    assert current_price == 100.0
    mock_fetch_latest.assert_called_once_with("AAPL")
