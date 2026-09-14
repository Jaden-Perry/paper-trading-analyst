"""Fetch daily price history and basic fundamentals from Yahoo Finance's
public endpoints. The chart endpoint needs no auth beyond a browser-like
User-Agent; the quoteSummary endpoint (fundamentals) additionally requires a
session cookie + crumb token, fetched once per run and reused.
"""
from __future__ import annotations
import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
QUOTE_SUMMARY_URL = (
    "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
)
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
COOKIE_SEED_URL = "https://fc.yahoo.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_session = None
_crumb = None


def _get_session_and_crumb():
    """Lazily fetches a session cookie + crumb (required by quoteSummary,
    unlike the unauthenticated chart endpoint) once per process and reuses
    it across all symbols in a run.
    """
    global _session, _crumb
    if _session is not None:
        return _session, _crumb

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        session.get(COOKIE_SEED_URL, timeout=15)  # seeds a cookie needed by getcrumb
        resp = session.get(CRUMB_URL, timeout=15)
        resp.raise_for_status()
        crumb = resp.text.strip()
    except Exception as e:
        print(f"stock_data: failed to fetch Yahoo crumb (fundamentals will be unavailable): {e}")
        crumb = None

    _session, _crumb = session, crumb
    return _session, _crumb


def fetch_price_history(symbol: str, range_: str = "6mo", interval: str = "1d") -> dict | None:
    """Returns {"dates": [...], "closes": [...], "volumes": [...]} of daily
    bars, oldest first, with None gaps (holidays/halts) dropped. Returns None
    on failure rather than raising - one bad symbol shouldn't sink the run.
    """
    try:
        resp = requests.get(
            CHART_URL.format(symbol=symbol),
            params={"range": range_, "interval": interval},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        closes_raw = quote["close"]
        volumes_raw = quote["volume"]

        dates, closes, volumes = [], [], []
        for ts, c, v in zip(timestamps, closes_raw, volumes_raw):
            if c is None:
                continue
            dates.append(ts)
            closes.append(c)
            volumes.append(v or 0)

        if len(closes) < 2:
            return None

        return {"symbol": symbol, "dates": dates, "closes": closes, "volumes": volumes}
    except Exception as e:
        print(f"stock_data: failed to fetch price history for {symbol}: {e}")
        return None


def fetch_fundamentals(symbol: str) -> dict | None:
    """Returns {"trailing_pe": float|None, "market_cap": float|None} or None
    on failure. Missing individual fields (e.g. no P/E for unprofitable
    companies) are returned as None rather than failing the whole fetch.
    """
    session, crumb = _get_session_and_crumb()
    if crumb is None:
        return None
    try:
        params = {"modules": "defaultKeyStatistics,summaryDetail", "crumb": crumb}
        resp = session.get(QUOTE_SUMMARY_URL.format(symbol=symbol), params=params, timeout=15)
        resp.raise_for_status()
        result = resp.json()["quoteSummary"]["result"][0]
        summary_detail = result.get("summaryDetail", {})
        key_stats = result.get("defaultKeyStatistics", {})

        def _raw(block: dict, key: str):
            v = block.get(key)
            return v.get("raw") if isinstance(v, dict) else None

        trailing_pe = _raw(summary_detail, "trailingPE") or _raw(key_stats, "trailingPE")
        market_cap = _raw(summary_detail, "marketCap") or _raw(key_stats, "marketCap")

        return {"trailing_pe": trailing_pe, "market_cap": market_cap}
    except Exception as e:
        print(f"stock_data: failed to fetch fundamentals for {symbol}: {e}")
        return None
