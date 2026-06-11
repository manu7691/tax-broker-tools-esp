"""
Security identity and the optional multi-security configuration.

Spanish FIFO is **per homogeneous security**, and "homogeneous" is defined by
**ISIN** — so the canonical grouping key for the portfolio runner is the ISIN,
with the ticker used only for display and as a fallback when no ISIN is known.

This module provides:

* :class:`Security` — a small value object (isin, ticker, name, country) plus a
  stable ``key`` used to bucket events into one FIFO queue per security.
* :class:`SecuritiesConfig` — the optional ``input/securities.json`` config
  (``include`` filter, ``isin_map`` ticker→ISIN overrides, ``primary`` security).
* :func:`grouping_key` / :func:`resolve_isin` — the helpers the portfolio runner
  and the platform parsers use to attach/derive an ISIN.

Phase 1 keeps resolution offline (the user-supplied ``isin_map``); the online
OpenFIGI/Yahoo lookup described in the plan is deferred to a later phase.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


def _norm_isin(isin: str | None) -> str | None:
    """Normalize an ISIN to upper-case with surrounding whitespace stripped."""
    cleaned = (isin or "").strip().upper()
    return cleaned or None


def _norm_ticker(ticker: str | None) -> str | None:
    """Normalize a ticker to upper-case with surrounding whitespace stripped."""
    cleaned = (ticker or "").strip().upper()
    return cleaned or None


def grouping_key(isin: str | None, symbol: str | None) -> str:
    """Return the FIFO grouping key for a security identity.

    ISIN is authoritative (it defines "homogeneous" in Spanish law). When no ISIN
    is known we fall back to the ticker, prefixed so it can never collide with a
    real ISIN; a missing identity buckets under ``"@UNKNOWN"``.
    """
    norm_isin = _norm_isin(isin)
    if norm_isin:
        return norm_isin
    norm_symbol = _norm_ticker(symbol)
    if norm_symbol:
        return f"@{norm_symbol}"
    return "@UNKNOWN"


@dataclass(frozen=True)
class Security:
    """Canonical identity of a tradable security (ISIN-keyed)."""

    isin: str | None = None
    ticker: str | None = None
    name: str = ""
    country: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "isin", _norm_isin(self.isin))
        object.__setattr__(self, "ticker", _norm_ticker(self.ticker))

    @property
    def key(self) -> str:
        """Stable grouping key (ISIN, else ``@TICKER``, else ``@UNKNOWN``)."""
        return grouping_key(self.isin, self.ticker)

    @property
    def label(self) -> str:
        """Human-facing label for tables/charts (ticker preferred, else ISIN)."""
        return self.ticker or self.isin or "UNKNOWN"


@dataclass
class SecuritiesConfig:
    """Parsed ``input/securities.json`` (all fields optional)."""

    include: list[str] = field(default_factory=list)  # tickers/ISINs to keep; [] = all
    isin_map: dict[str, str] = field(default_factory=dict)  # ticker -> ISIN overrides
    primary: str | None = None  # the "primary"/employer security (ticker or ISIN)

    def is_included(self, isin: str | None, symbol: str | None) -> bool:
        """Whether a security passes the ``include`` allow-list (empty = all)."""
        if not self.include:
            return True
        wanted = {w.strip().upper() for w in self.include}
        return bool(
            (_norm_isin(isin) and _norm_isin(isin) in wanted)
            or (_norm_ticker(symbol) and _norm_ticker(symbol) in wanted)
        )

    def resolve_isin(self, symbol: str | None) -> str | None:
        """Look a ticker up in the user ``isin_map`` (offline). ``None`` if absent."""
        return resolve_isin(symbol, self.isin_map)


def resolve_isin(symbol: str | None, isin_map: dict[str, str]) -> str | None:
    """Resolve a ticker to an ISIN via a user-supplied ``{ticker: ISIN}`` map.

    Case-insensitive on the ticker. Returns ``None`` when the ticker is unknown,
    leaving the caller to fall back to ticker-based grouping (with its caveat).
    """
    norm = _norm_ticker(symbol)
    if not norm:
        return None
    for raw_ticker, raw_isin in isin_map.items():
        if _norm_ticker(raw_ticker) == norm:
            return _norm_isin(raw_isin)
    return None


class IsinCache:
    """Persistent ticker→ISIN cache (JSON on disk), mirroring ``ECBRateFetcher``.

    Once a ticker's ISIN is known from *any* source (the user map, a gains export
    that carries the ISIN, or a network lookup), it is written here so later runs
    — even ones that only have the ticker-only movements export — still group that
    security under its ISIN. Override the path with the ``ISIN_CACHE`` env var.
    """

    CACHE_FILE = Path(os.environ.get("ISIN_CACHE", ".isin_cache.json"))
    _cache: dict[str, str] = {}
    _loaded: bool = False

    @classmethod
    def _load(cls) -> None:
        if cls._loaded:
            return
        cls._loaded = True
        try:
            with open(cls.CACHE_FILE, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if isinstance(raw, dict):
            for ticker, isin in raw.items():
                nt, ni = _norm_ticker(ticker), _norm_isin(isin)
                if nt and ni:
                    cls._cache.setdefault(nt, ni)

    @classmethod
    def get(cls, ticker: str | None) -> str | None:
        cls._load()
        return cls._cache.get(_norm_ticker(ticker) or "")

    @classmethod
    def put(cls, ticker: str | None, isin: str | None) -> None:
        nt, ni = _norm_ticker(ticker), _norm_isin(isin)
        if not (nt and ni) or cls.get(nt) == ni:
            return
        cls._cache[nt] = ni
        try:
            with open(cls.CACHE_FILE, "w", encoding="utf-8") as fh:
                json.dump(cls._cache, fh, indent=0, sort_keys=True)
        except OSError:
            pass

    @classmethod
    def clear(cls) -> None:
        """Clear the in-memory cache (does not delete the disk file)."""
        cls._cache.clear()
        cls._loaded = False


def build_isin_resolver(
    isin_map: dict[str, str] | None = None,
    learned: dict[str, str] | None = None,
    *,
    use_cache: bool = True,
    network: "Callable[[str], str | None] | None" = None,
) -> "Callable[[str | None], str | None]":
    """Return a ticker→ISIN resolver consulting sources in priority order.

    1. the user ``isin_map`` (``input/securities.json``),
    2. ISINs ``learned`` from ISIN-bearing feeds in the same input (e.g. the
       Revolut realized-gains export, which carries an ISIN per row),
    3. the persistent :class:`IsinCache` (``use_cache``),
    4. an optional ``network`` lookup (off by default; pluggable, e.g. OpenFIGI).

    Any positive resolution is written back to the cache so it sticks. Returns
    ``None`` when the ticker cannot be resolved, leaving the caller to fall back
    to ticker-based grouping (a ticker that never changed ISIN is safe; the only
    risk is cross-broker merge accuracy for a renamed/re-ISIN'd security).
    """
    user = {
        nt: ni
        for k, v in (isin_map or {}).items()
        if (nt := _norm_ticker(k)) and (ni := _norm_isin(v))
    }
    learned_n = {
        nt: ni
        for k, v in (learned or {}).items()
        if (nt := _norm_ticker(k)) and (ni := _norm_isin(v))
    }

    def resolve(ticker: str | None) -> str | None:
        nt = _norm_ticker(ticker)
        if not nt:
            return None
        hit = user.get(nt) or learned_n.get(nt)
        if hit:
            if use_cache:
                IsinCache.put(nt, hit)
            return hit
        if use_cache and (cached := IsinCache.get(nt)):
            return cached
        if network:
            try:
                net = _norm_isin(network(nt))
            except Exception:
                net = None
            if net:
                if use_cache:
                    IsinCache.put(nt, net)
                return net
        return None

    return resolve


def load_securities_config(input_dir: Path) -> SecuritiesConfig:
    """Load ``input/securities.json`` if present, else an empty (all-pass) config.

    The file is fully optional and backward compatible: with no file, ``include``
    is empty (every detected security is kept) and ``isin_map`` is empty, so the
    behaviour falls back to whatever the caller already does.
    """
    path = input_dir / "securities.json"
    if not path.exists():
        return SecuritiesConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: could not read {path}: {e}; ignoring multi-security config.")
        return SecuritiesConfig()
    if not isinstance(raw, dict):
        print(f"Warning: {path} is not a JSON object; ignoring multi-security config.")
        return SecuritiesConfig()

    include = raw.get("include") or []
    if not isinstance(include, list):
        print(f"Warning: 'include' in {path} is not a list; ignoring it.")
        include = []
    isin_map = raw.get("isin_map") or {}
    if not isinstance(isin_map, dict):
        print(f"Warning: 'isin_map' in {path} is not an object; ignoring it.")
        isin_map = {}
    primary = raw.get("primary")
    primary = str(primary).strip().upper() if primary else None

    return SecuritiesConfig(
        include=[str(x).strip().upper() for x in include if str(x).strip()],
        isin_map={str(k): str(v) for k, v in isin_map.items()},
        primary=primary,
    )
