"""Fetch current prices for benchmark tickers (index/strategy ETFs), reusing
the same chart-endpoint fetch already used for held-position prices.
"""
from __future__ import annotations

import stock_data


def fetch_benchmark_prices(benchmarks: list[dict]) -> dict[str, float]:
    prices = {}
    for b in benchmarks:
        symbol = b["symbol"]
        hist = stock_data.fetch_price_history(symbol, range_="5d")
        if hist is not None:
            prices[symbol] = hist["closes"][-1]
        else:
            print(f"benchmarks: could not fetch current price for {symbol}")
    return prices
