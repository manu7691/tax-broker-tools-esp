"""
Tests for the security-identity layer (securities.py).

Covers the ISIN-first grouping key, the Security value object, ticker→ISIN
resolution via the user map, and loading the optional input/securities.json.
"""

from pathlib import Path
from typing import Any

import pytest

from tax_engine.securities import (
    IsinCache,
    SecuritiesConfig,
    Security,
    build_isin_resolver,
    grouping_key,
    load_securities_config,
    resolve_isin,
)

TSLA_ISIN = "US88160R1014"
NVDA_ISIN = "US67066G1040"


class TestGroupingKey:
    def test_isin_is_authoritative(self) -> None:
        # ISIN wins even when a (different) ticker is also given.
        assert grouping_key(TSLA_ISIN, "WHATEVER") == TSLA_ISIN

    def test_isin_normalized_upper_and_stripped(self) -> None:
        assert grouping_key("  us88160r1014 ", None) == TSLA_ISIN

    def test_falls_back_to_ticker_prefixed(self) -> None:
        # Ticker-only key is prefixed so it can never collide with a real ISIN.
        assert grouping_key(None, "tsla") == "@TSLA"

    def test_unknown_when_neither_present(self) -> None:
        assert grouping_key(None, None) == "@UNKNOWN"
        assert grouping_key("", "  ") == "@UNKNOWN"


class TestSecurity:
    def test_normalizes_and_keys_by_isin(self) -> None:
        sec = Security(isin=" us88160r1014 ", ticker=" tsla ", name="Tesla")
        assert sec.isin == TSLA_ISIN
        assert sec.ticker == "TSLA"
        assert sec.key == TSLA_ISIN
        assert sec.label == "TSLA"

    def test_label_falls_back_to_isin_then_unknown(self) -> None:
        assert Security(isin=TSLA_ISIN).label == TSLA_ISIN
        assert Security().label == "UNKNOWN"
        assert Security().key == "@UNKNOWN"

    def test_ticker_only_security_key(self) -> None:
        assert Security(ticker="nvda").key == "@NVDA"


class TestResolveIsin:
    def test_resolves_case_insensitively(self) -> None:
        assert resolve_isin("tsla", {"TSLA": TSLA_ISIN}) == TSLA_ISIN
        assert resolve_isin("TSLA", {"tsla": TSLA_ISIN.lower()}) == TSLA_ISIN

    def test_unknown_ticker_returns_none(self) -> None:
        assert resolve_isin("NVDA", {"TSLA": TSLA_ISIN}) is None
        assert resolve_isin(None, {"TSLA": TSLA_ISIN}) is None


class TestSecuritiesConfig:
    def test_include_empty_passes_everything(self) -> None:
        cfg = SecuritiesConfig()
        assert cfg.is_included(TSLA_ISIN, "TSLA") is True
        assert cfg.is_included(None, "ANY") is True

    def test_include_matches_isin_or_ticker(self) -> None:
        cfg = SecuritiesConfig(include=["TSLA", "US00724F1012"])
        assert cfg.is_included(TSLA_ISIN, "TSLA") is True  # by ticker
        assert cfg.is_included("US00724F1012", "ADBE") is True  # by ISIN
        assert cfg.is_included("US67066G1040", "NVDA") is False  # excluded

    def test_resolve_isin_delegates_to_map(self) -> None:
        cfg = SecuritiesConfig(isin_map={"TSLA": TSLA_ISIN})
        assert cfg.resolve_isin("tsla") == TSLA_ISIN
        assert cfg.resolve_isin("NVDA") is None


class TestLoadSecuritiesConfig:
    def test_absent_file_is_empty_all_pass_config(self, tmp_path: Path) -> None:
        cfg = load_securities_config(tmp_path)
        assert cfg.include == []
        assert cfg.isin_map == {}
        assert cfg.primary is None
        assert cfg.is_included(None, "ANYTHING") is True

    def test_parses_full_config(self, tmp_path: Path) -> None:
        (tmp_path / "securities.json").write_text(
            f'{{"include": ["dt", "tsla"], "isin_map": {{"TSLA": "{TSLA_ISIN}"}}, "primary": "dt"}}',
            encoding="utf-8",
        )
        cfg = load_securities_config(tmp_path)
        assert cfg.include == ["DT", "TSLA"]
        assert cfg.isin_map == {"TSLA": TSLA_ISIN}
        assert cfg.primary == "DT"

    def test_malformed_json_is_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "securities.json").write_text("{not json", encoding="utf-8")
        cfg = load_securities_config(tmp_path)
        assert cfg == SecuritiesConfig()

    def test_non_object_json_is_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "securities.json").write_text('["dt"]', encoding="utf-8")
        cfg = load_securities_config(tmp_path)
        assert cfg == SecuritiesConfig()


@pytest.fixture()  # type: ignore[misc]
def isolated_isin_cache(tmp_path: Path, monkeypatch: Any) -> Any:
    """Point the persistent ISIN cache at a temp file and clear it around the test."""
    monkeypatch.setattr(IsinCache, "CACHE_FILE", tmp_path / ".isin_cache.json")
    IsinCache.clear()
    yield
    IsinCache.clear()


class TestBuildIsinResolver:
    def test_user_map_wins_over_learned(self) -> None:
        resolve = build_isin_resolver(
            {"TSLA": TSLA_ISIN}, learned={"TSLA": NVDA_ISIN}, use_cache=False
        )
        assert resolve("tsla") == TSLA_ISIN

    def test_learned_used_when_absent_from_map(self) -> None:
        resolve = build_isin_resolver({}, learned={"NVDA": NVDA_ISIN}, use_cache=False)
        assert resolve("nvda") == NVDA_ISIN

    def test_unknown_ticker_returns_none(self) -> None:
        resolve = build_isin_resolver({"TSLA": TSLA_ISIN}, use_cache=False)
        assert resolve("ZZZ") is None
        assert resolve(None) is None

    def test_network_fallback_and_error_swallowed(self) -> None:
        resolve = build_isin_resolver(
            {}, use_cache=False, network=lambda t: NVDA_ISIN if t == "NVDA" else None
        )
        assert resolve("nvda") == NVDA_ISIN
        assert resolve("zzz") is None

        def boom(_t: str) -> str | None:
            raise RuntimeError("network down")

        resolve_err = build_isin_resolver({}, use_cache=False, network=boom)
        assert resolve_err("anything") is None

    def test_resolution_is_written_back_to_cache(self, isolated_isin_cache: Any) -> None:
        # A map hit seeds the cache; a later cache-only resolver then finds it.
        build_isin_resolver({"TSLA": TSLA_ISIN})("TSLA")
        assert build_isin_resolver({})("tsla") == TSLA_ISIN


class TestIsinCache:
    def test_put_and_get_roundtrip_through_disk(self, isolated_isin_cache: Any) -> None:
        IsinCache.put("tsla", TSLA_ISIN)
        IsinCache.clear()  # drop in-memory; force a reload from disk
        assert IsinCache.get("TSLA") == TSLA_ISIN

    def test_ignores_blank_entries(self, isolated_isin_cache: Any) -> None:
        IsinCache.put("", TSLA_ISIN)
        IsinCache.put("TSLA", "")
        assert IsinCache.get("TSLA") is None
