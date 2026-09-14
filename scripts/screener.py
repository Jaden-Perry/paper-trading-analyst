"""Rules-based stock screener: momentum, trend, volume, relative valuation,
and a minimum-volatility floor (excludes "boring"/flat names, but doesn't
chase the most volatile names either - it's a floor, not a target).

Dependency-free arithmetic only (stdlib `statistics`), matching the sibling
Internship_Tracker project's convention of no numpy/pandas for small batch
jobs like this.
"""
from __future__ import annotations
import statistics

import stock_data


def _weekly_volatility_pct(closes: list[float]) -> float:
    """Mean absolute weekly % move, approximated by sampling every 5th daily
    close (5 trading days/week). Used as the "boring stock" floor.
    """
    weekly = closes[::5]
    changes = [
        abs((weekly[i] - weekly[i - 1]) / weekly[i - 1] * 100)
        for i in range(1, len(weekly))
        if weekly[i - 1]
    ]
    return statistics.mean(changes) if changes else 0.0


def _build_candidate(stock: dict, config: dict) -> dict | None:
    symbol = stock["symbol"]
    hist = stock_data.fetch_price_history(symbol)
    if hist is None:
        return None
    closes, volumes = hist["closes"], hist["volumes"]

    ma_period = config["ma_period_days"]
    if len(closes) < ma_period + 5:
        return None  # not enough history to compute a stable moving average

    price = closes[-1]
    if price < config["min_price"]:
        return None

    recent_volumes = volumes[-20:] if len(volumes) >= 20 else volumes
    avg_dollar_volume = statistics.mean(recent_volumes) * price
    if avg_dollar_volume < config["min_avg_dollar_volume"]:
        return None

    weekly_vol = _weekly_volatility_pct(closes)
    if weekly_vol < config["min_weekly_volatility_pct"]:
        return None  # too "boring" - historically flat, screened out per user preference

    fund = stock_data.fetch_fundamentals(symbol) or {}
    market_cap = fund.get("market_cap")
    if market_cap is not None and market_cap < config["min_market_cap"]:
        return None

    momentum_days = min(config["momentum_lookback_days"], len(closes) - 1)
    momentum_pct = (price - closes[-momentum_days - 1]) / closes[-momentum_days - 1] * 100

    ma = statistics.mean(closes[-ma_period:])
    above_ma = price > ma
    ma_pct_diff = (price - ma) / ma * 100

    recent_avg_vol = statistics.mean(volumes[-5:])
    baseline_window = volumes[-60:-5] if len(volumes) >= 60 else volumes[:-5]
    baseline_avg_vol = statistics.mean(baseline_window) if baseline_window else recent_avg_vol
    volume_ratio = recent_avg_vol / baseline_avg_vol if baseline_avg_vol else 1.0

    return {
        "symbol": symbol,
        "name": stock.get("name", symbol),
        "sector": stock.get("sector"),
        "price": price,
        "momentum_pct": momentum_pct,
        "above_ma": above_ma,
        "ma_pct_diff": ma_pct_diff,
        "volume_ratio": volume_ratio,
        "volume_surge": volume_ratio >= config["volume_surge_ratio"],
        "weekly_volatility_pct": weekly_vol,
        "trailing_pe": fund.get("trailing_pe"),
        "market_cap": market_cap,
    }


def screen(universe: list[dict], config: dict) -> list[dict]:
    """Returns every universe symbol that survives the hard filters (price,
    liquidity, market cap, volatility floor), each annotated with a `score`
    and sorted best-first. Callers apply their own entry threshold on score.
    """
    candidates = []
    for stock in universe:
        c = _build_candidate(stock, config)
        if c is not None:
            candidates.append(c)

    positive_pes = [c["trailing_pe"] for c in candidates if c["trailing_pe"] and c["trailing_pe"] > 0]
    median_pe = statistics.median(positive_pes) if positive_pes else None

    for c in candidates:
        pe = c["trailing_pe"]
        if pe and pe > 0 and median_pe:
            c["pe_vs_median_pct"] = (pe - median_pe) / median_pe * 100
            valuation_score = -(c["pe_vs_median_pct"] / 100)  # cheaper vs. peers -> higher score
        else:
            c["pe_vs_median_pct"] = None
            valuation_score = 0.0
        if pe and pe > config["max_pe"]:
            valuation_score -= 0.5  # penalize (not exclude) extreme valuations

        momentum_score = c["momentum_pct"] / 100
        trend_score = 0.5 if c["above_ma"] else -0.3
        volume_score = 0.3 if c["volume_surge"] else 0.0

        c["score"] = momentum_score + trend_score + volume_score + 0.5 * valuation_score

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates
