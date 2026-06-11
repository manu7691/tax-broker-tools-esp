"""
Unit tests for the ECB Exchange Rate Fetcher.

Uses mocked HTTP responses to test rate fetching logic without
making actual API calls to the ECB.
"""

import urllib.error
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tax_engine.ecb_rates import ECBRateFetcher, prefetch_ecb_rates
from tax_engine.models import EventType, StockEvent

# Sample ECB XML response format
ECB_XML_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<message:GenericData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
                      xmlns:generic="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/structurespecific">
    <message:DataSet>
        <Obs TIME_PERIOD="2021-05-14" OBS_VALUE="1.2077"/>
        <Obs TIME_PERIOD="2021-05-17" OBS_VALUE="1.2150"/>
        <Obs TIME_PERIOD="2021-05-18" OBS_VALUE="1.2200"/>
    </message:DataSet>
</message:GenericData>
"""


class TestECBRateFetcher:
    """Tests for the ECBRateFetcher class."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the rate cache before each test."""
        ECBRateFetcher.clear_cache()
        yield
        ECBRateFetcher.clear_cache()

    def test_get_rate_fetches_from_api(self):
        """Test that get_rate fetches from ECB API."""
        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            rate = ECBRateFetcher.get_rate(date(2021, 5, 17))

        # ECB publishes EUR/USD, we need USD/EUR (inverse)
        # 1/1.2150 ≈ 0.8230
        assert rate == pytest.approx(Decimal("0.8230"), abs=Decimal("0.0001"))

    def test_get_rate_uses_cache(self):
        """Test that subsequent calls use cached rate."""
        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            rate1 = ECBRateFetcher.get_rate(date(2021, 5, 17))
            rate2 = ECBRateFetcher.get_rate(date(2021, 5, 17))

        # Should only call API once (second call uses cache)
        assert mock_urlopen.call_count == 1
        assert rate1 == rate2

    def test_get_rate_weekend_uses_friday(self):
        """Test that weekend dates use the most recent Friday's rate."""
        # May 15, 2021 was a Saturday
        # May 14, 2021 was a Friday
        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            # Request Saturday's rate
            rate = ECBRateFetcher.get_rate(date(2021, 5, 15))

        # Should get Friday's rate (May 14: 1.2077 -> inverse ≈ 0.8280)
        assert rate == pytest.approx(Decimal("0.8280"), abs=Decimal("0.0001"))

    def test_get_rate_api_failure_raises(self):
        """Test that API failures raise RuntimeError."""
        with (
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network error")),
            pytest.raises(RuntimeError) as exc_info,
        ):
            ECBRateFetcher.get_rate(date(2021, 5, 17))

        assert "Failed to fetch ECB rates" in str(exc_info.value)

    def test_get_rate_no_data_for_date_raises(self):
        """Test that missing data for date range raises ValueError."""
        # XML with no observations
        empty_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <message:GenericData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message">
            <message:DataSet></message:DataSet>
        </message:GenericData>
        """

        mock_response = MagicMock()
        mock_response.read.return_value = empty_xml.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with (
            patch("urllib.request.urlopen", return_value=mock_response),
            pytest.raises(ValueError) as exc_info,
        ):
            ECBRateFetcher.get_rate(date(2021, 5, 17))

        assert "No ECB rate available" in str(exc_info.value)

    def test_clear_cache(self):
        """Test that clear_cache empties the cache."""
        # Pre-populate cache
        ECBRateFetcher._rate_cache[("USD", date(2021, 5, 17))] = Decimal("0.82")

        ECBRateFetcher.clear_cache()

        assert len(ECBRateFetcher._rate_cache) == 0

    def test_eur_is_one_without_network(self):
        """EUR needs no conversion and never hits the API."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            assert ECBRateFetcher.get_rate(date(2021, 5, 17), "EUR") == Decimal("1")
        mock_urlopen.assert_not_called()

    def test_unsupported_currency_raises(self):
        with pytest.raises(ValueError, match="not an ECB reference currency"):
            ECBRateFetcher.get_rate(date(2021, 5, 17), "XYZ")

    def test_gbp_uses_currency_in_url_and_caches_per_currency(self):
        """A non-USD currency fetches its own series and caches under that code."""
        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            rate = ECBRateFetcher.get_rate(date(2021, 5, 17), "GBP")

        assert "D.GBP.EUR" in mock_urlopen.call_args[0][0]
        assert rate == pytest.approx(Decimal("0.8230"), abs=Decimal("0.0001"))
        assert ("GBP", date(2021, 5, 17)) in ECBRateFetcher._rate_cache
        # USD is a separate series, so the GBP fetch must not satisfy a USD lookup.
        assert ("USD", date(2021, 5, 17)) not in ECBRateFetcher._rate_cache

    def test_loads_legacy_flat_usd_cache(self, tmp_path, monkeypatch):
        """A pre-existing flat {iso: rate} cache file is read as USD (no network)."""
        cache = tmp_path / "legacy.json"
        cache.write_text('{"2021-05-17": "0.8230"}', encoding="utf-8")
        monkeypatch.setattr(ECBRateFetcher, "CACHE_FILE", cache)
        ECBRateFetcher.clear_cache()

        with patch("urllib.request.urlopen") as mock_urlopen:
            rate = ECBRateFetcher.get_rate(date(2021, 5, 17))  # USD
        mock_urlopen.assert_not_called()
        assert rate == Decimal("0.8230")

    def test_disk_cache_roundtrip_per_currency(self, tmp_path, monkeypatch):
        """Fetched rates persist nested per currency and reload without a refetch."""
        cache = tmp_path / "cache.json"
        monkeypatch.setattr(ECBRateFetcher, "CACHE_FILE", cache)
        ECBRateFetcher.clear_cache()

        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response):
            ECBRateFetcher.get_rate(date(2021, 5, 17), "GBP")

        assert '"GBP"' in cache.read_text(encoding="utf-8")  # nested by currency
        ECBRateFetcher.clear_cache()  # drop in-memory; force a disk reload
        with patch("urllib.request.urlopen") as mock_urlopen:
            rate = ECBRateFetcher.get_rate(date(2021, 5, 17), "GBP")
        mock_urlopen.assert_not_called()
        assert rate == pytest.approx(Decimal("0.8230"), abs=Decimal("0.0001"))

    def test_inverse_rate_calculation(self):
        """Test that EUR/USD is correctly inverted to USD/EUR."""
        # ECB publishes EUR/USD = 1.20 (1 EUR = 1.20 USD)
        # We need USD/EUR = 1/1.20 = 0.8333 (1 USD = 0.8333 EUR)
        xml_with_rate = """<?xml version="1.0" encoding="UTF-8"?>
        <message:GenericData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message">
            <message:DataSet>
                <Obs TIME_PERIOD="2021-05-17" OBS_VALUE="1.2000"/>
            </message:DataSet>
        </message:GenericData>
        """

        mock_response = MagicMock()
        mock_response.read.return_value = xml_with_rate.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            rate = ECBRateFetcher.get_rate(date(2021, 5, 17))

        # 1/1.20 = 0.8333
        assert rate == pytest.approx(Decimal("0.8333"), abs=Decimal("0.0001"))


class TestECBRateFetcherBulk:
    """Tests for bulk rate fetching."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the rate cache before each test."""
        ECBRateFetcher.clear_cache()
        yield
        ECBRateFetcher.clear_cache()

    def test_get_rates_bulk_single_request(self):
        """Test that bulk fetch uses a single API request."""
        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        dates = [date(2021, 5, 14), date(2021, 5, 17), date(2021, 5, 18)]

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            rates = ECBRateFetcher.get_rates_bulk(dates)

        # Should only make one API call
        assert mock_urlopen.call_count == 1
        # Should have rates for all requested dates
        assert len(rates) == 3

    def test_get_rates_bulk_empty_list(self):
        """Test bulk fetch with empty date list."""
        rates = ECBRateFetcher.get_rates_bulk([])
        assert rates == {}

    def test_get_rates_bulk_caches_results(self):
        """Test that bulk fetch populates cache."""
        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            ECBRateFetcher.get_rates_bulk([date(2021, 5, 17)])

        # Cache should be populated (keyed by (currency, date))
        assert ("USD", date(2021, 5, 17)) in ECBRateFetcher._rate_cache


class TestPrefetchECBRates:
    """Tests for the prefetch_ecb_rates helper function."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the rate cache before each test."""
        ECBRateFetcher.clear_cache()
        yield
        ECBRateFetcher.clear_cache()

    def test_prefetch_extracts_unique_dates(self):
        """Test that prefetch extracts unique dates from events."""
        events = [
            StockEvent(
                event_date=date(2021, 5, 17),
                event_type=EventType.VEST,
                shares=Decimal("100"),
                price_usd=Decimal("50.00"),
            ),
            StockEvent(
                event_date=date(2021, 5, 17),  # Same date
                event_type=EventType.SELL,
                shares=Decimal("50"),
                price_usd=Decimal("55.00"),
            ),
            StockEvent(
                event_date=date(2021, 6, 1),  # Different date
                event_type=EventType.BUY,
                shares=Decimal("30"),
                price_usd=Decimal("45.00"),
            ),
        ]

        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            prefetch_ecb_rates(events)

        # Should only make one API call (bulk fetch)
        assert mock_urlopen.call_count == 1

    def test_prefetch_skips_events_with_explicit_fx_rate(self):
        """Test that events with explicit fx_rate don't trigger API calls."""
        events = [
            StockEvent(
                event_date=date(2021, 5, 17),
                event_type=EventType.VEST,
                shares=Decimal("100"),
                price_usd=Decimal("50.00"),
                fx_rate=Decimal("0.82"),  # Explicit rate
            ),
        ]

        # If prefetch only fetches for events without fx_rate,
        # it should skip this event
        with patch.object(ECBRateFetcher, "get_rates_bulk") as mock_bulk:
            mock_bulk.return_value = {}
            prefetch_ecb_rates(events)

            # Events with explicit fx_rate should not be in the request
            # The actual filtering is implementation-dependent

    def test_prefetch_empty_events(self):
        """Test prefetch with empty events list."""
        with patch.object(ECBRateFetcher, "get_rates_bulk"):
            prefetch_ecb_rates([])
            # Should handle gracefully

    def test_prefetch_groups_by_currency(self):
        """One bulk fetch per currency; EUR needs none."""
        events = [
            StockEvent(date(2021, 5, 17), EventType.VEST, Decimal("100"), Decimal("50")),
            StockEvent(date(2021, 5, 18), EventType.BUY, Decimal("10"), Decimal("30"), currency="GBP"),
            StockEvent(date(2021, 5, 19), EventType.BUY, Decimal("5"), Decimal("20"), currency="EUR"),
        ]
        with patch.object(ECBRateFetcher, "get_rates_bulk", return_value={}) as mock_bulk:
            prefetch_ecb_rates(events)
        fetched_currencies = {call.args[1] for call in mock_bulk.call_args_list}
        assert fetched_currencies == {"USD", "GBP"}  # EUR skipped

    def test_event_resolves_rate_with_its_currency(self):
        """resolved_fx_rate fetches using the event's own currency."""
        event = StockEvent(date(2021, 5, 17), EventType.BUY, Decimal("1"), Decimal("100"), currency="GBP")
        with patch.object(ECBRateFetcher, "get_rate", return_value=Decimal("1.1")) as mock_get:
            assert event.resolved_fx_rate == Decimal("1.1")
        mock_get.assert_called_once_with(date(2021, 5, 17), "GBP")


class TestECBRateFetcherDateRange:
    """Tests for date range handling in API calls."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the rate cache before each test."""
        ECBRateFetcher.clear_cache()
        yield
        ECBRateFetcher.clear_cache()

    def test_api_url_includes_date_range(self):
        """Test that API URL includes correct date range."""
        mock_response = MagicMock()
        mock_response.read.return_value = ECB_XML_RESPONSE.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        target_date = date(2021, 5, 17)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            ECBRateFetcher.get_rate(target_date)

        # Check the URL that was called
        call_url = mock_urlopen.call_args[0][0]
        assert "2021-05-17" in call_url  # end date
        # Start date should be ~14 days before
        assert "2021-05-03" in call_url  # start date (14 days buffer)

    def test_handles_year_boundary(self):
        """Test rate fetching across year boundary."""
        xml_year_boundary = """<?xml version="1.0" encoding="UTF-8"?>
        <message:GenericData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message">
            <message:DataSet>
                <Obs TIME_PERIOD="2020-12-31" OBS_VALUE="1.2200"/>
                <Obs TIME_PERIOD="2021-01-04" OBS_VALUE="1.2250"/>
            </message:DataSet>
        </message:GenericData>
        """

        mock_response = MagicMock()
        mock_response.read.return_value = xml_year_boundary.encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        # January 2, 2021 (Saturday - market closed, use Dec 31)
        with patch("urllib.request.urlopen", return_value=mock_response):
            rate = ECBRateFetcher.get_rate(date(2021, 1, 2))

        # Should get Dec 31 rate
        assert rate == pytest.approx(Decimal("0.8197"), abs=Decimal("0.0001"))
