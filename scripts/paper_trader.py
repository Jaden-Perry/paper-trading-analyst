"""Weekly orchestrator: screen stocks, decide buy/sell/hold for the paper
portfolio, write analyst-style rationales, and persist both the raw ledger
(data/trades.json) and the derived dashboard view (docs/portfolio.json).

Usage: python paper_trader.py [--limit N]
    --limit N   only screen the first N universe symbols (fast local testing)
Env vars: ANTHROPIC_API_KEY (required only if there's at least one buy/sell/
hold event to narrate, which is every run once positions exist)
"""
from __future__ import annotations
import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import benchmarks
import ledger as ledger_mod
import market_news
import portfolio_engine
import screener
import stock_data
import trade_writer

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "paper_trading.json"
UNIVERSE_PATH = ROOT / "config" / "stock_universe.json"
DASHBOARD_PATH = ROOT / "docs" / "portfolio.json"


def load_json(path: Path):
    with open(path) as f:
        return json.load(f)


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_held_prices(symbols: list[str]) -> dict[str, float]:
    prices = {}
    for symbol in symbols:
        hist = stock_data.fetch_price_history(symbol, range_="5d")
        if hist is not None:
            prices[symbol] = hist["closes"][-1]
        else:
            print(f"paper_trader: could not fetch current price for held position {symbol}")
    return prices


def max_drawdown_pct(history: list[dict]) -> float:
    if not history:
        return 0.0
    peak = history[0]["total_value"]
    worst = 0.0
    for h in history:
        peak = max(peak, h["total_value"])
        drawdown = (h["total_value"] - peak) / peak if peak else 0.0
        worst = min(worst, drawdown)
    return abs(worst)


def trailing_return_pct(history: list[dict], days: int) -> float | None:
    """% return from the most recent entry at/before (latest date - days) to
    the latest entry. Returns None if there's no entry that old yet - e.g. a
    1-year return with only 3 weeks of history - rather than a misleading
    number computed from less history than the label implies.
    """
    if not history:
        return None
    latest = history[-1]
    cutoff = date.fromisoformat(latest["date"]) - timedelta(days=days)
    eligible = [h for h in history if date.fromisoformat(h["date"]) <= cutoff]
    if not eligible:
        return None
    base = eligible[-1]["total_value"]
    return (latest["total_value"] - base) / base if base else None


def normalized_return_history(entries: list[dict], value_key: str) -> list[dict]:
    """Converts a [{"date":..., value_key:...}, ...] series into cumulative
    % return since the first entry, so series with different starting scales
    (e.g. portfolio dollars vs. an index's share price) can be plotted on the
    same axis.
    """
    if not entries:
        return []
    base = entries[0][value_key]
    if not base:
        return []
    return [
        {"date": e["date"], "return_pct": (e[value_key] - base) / base}
        for e in entries
    ]


def build_dashboard(ledger: dict, all_prices: dict[str, float], benchmark_config: list[dict]) -> dict:
    open_positions = []
    for pos in ledger["positions"]:
        price = all_prices.get(pos["symbol"], pos["entry_price"])
        unrealized_pnl = (price - pos["entry_price"]) * pos["shares"]
        open_positions.append(
            {
                **pos,
                "current_price": price,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": (price - pos["entry_price"]) / pos["entry_price"],
            }
        )

    closed_trades = sorted(ledger["closed_trades"], key=lambda t: t["exit_date"], reverse=True)
    total_value = ledger["cash"] + sum(p["current_price"] * p["shares"] for p in open_positions)

    wins = [t for t in ledger["closed_trades"] if t["realized_pnl"] > 0]
    win_rate = len(wins) / len(ledger["closed_trades"]) if ledger["closed_trades"] else None

    benchmark_names = {b["symbol"]: b["name"] for b in benchmark_config}
    benchmark_history = ledger.get("benchmark_history", {})
    benchmarks_out = {
        symbol: {
            "name": benchmark_names.get(symbol, symbol),
            "return_history": normalized_return_history(entries, "price"),
        }
        for symbol, entries in benchmark_history.items()
        if entries
    }

    return {
        "generated_at": now_iso(),
        "summary": {
            "total_value": total_value,
            "cash": ledger["cash"],
            "initial_capital": ledger["initial_capital"],
            "total_return_pct": (total_value - ledger["initial_capital"]) / ledger["initial_capital"],
            "num_open_positions": len(open_positions),
            "num_closed_trades": len(ledger["closed_trades"]),
            "win_rate": win_rate,
            "max_drawdown_pct": max_drawdown_pct(ledger["history"]),
            "return_1m_pct": trailing_return_pct(ledger["history"], 30),
            "return_1y_pct": trailing_return_pct(ledger["history"], 365),
        },
        "open_positions": open_positions,
        "closed_trades": closed_trades,
        "value_history": ledger["history"],
        "portfolio_return_history": normalized_return_history(ledger["history"], "total_value"),
        "benchmarks": benchmarks_out,
        "weekly_reports": list(reversed(ledger["weekly_reports"])),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only screen the first N universe symbols")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH)
    universe = load_json(UNIVERSE_PATH)
    if args.limit:
        universe = universe[: args.limit]

    ledger = ledger_mod.load_ledger(config["initial_capital"])
    today = today_str()

    print(f"paper_trader: screening {len(universe)} symbols...")
    candidates = screener.screen(universe, config)
    print(f"paper_trader: {len(candidates)} symbols passed hard filters")

    held_symbols = [p["symbol"] for p in ledger["positions"]]
    held_prices = fetch_held_prices(held_symbols)

    ledger, events = portfolio_engine.decide(ledger, candidates, held_prices, config, today)
    print(f"paper_trader: {len(events)} events this run "
          f"({sum(1 for e in events if e['action']=='buy')} buy, "
          f"{sum(1 for e in events if e['action']=='sell')} sell, "
          f"{sum(1 for e in events if e['action']=='hold')} hold)")

    for event in events:
        event["headlines"] = market_news.fetch_symbol_headlines(
            event["symbol"], config["news_feed_url_template"]
        )

    rationales = trade_writer.generate_rationales(events) if events else {}

    for event in events:
        event["rationale"] = rationales.get(event["symbol"], "")

    rationale_by_symbol = {e["symbol"]: e["rationale"] for e in events}
    for pos in ledger["positions"]:
        if pos["symbol"] in rationale_by_symbol and not pos.get("entry_thesis"):
            pos["entry_thesis"] = rationale_by_symbol[pos["symbol"]]
    for trade in ledger["closed_trades"]:
        if trade["exit_date"] == today and not trade.get("exit_thesis"):
            trade["exit_thesis"] = rationale_by_symbol.get(trade["symbol"], "")

    all_prices = {**{c["symbol"]: c["price"] for c in candidates}, **held_prices}
    total_value = ledger["cash"] + sum(
        all_prices.get(p["symbol"], p["entry_price"]) * p["shares"] for p in ledger["positions"]
    )
    ledger["history"].append({"date": today, "total_value": total_value, "cash": ledger["cash"]})
    ledger["weekly_reports"].append({"date": today, "events": events})

    benchmark_config = config.get("benchmarks", [])
    ledger.setdefault("benchmark_history", {})  # older ledgers predate this field
    bm_prices = benchmarks.fetch_benchmark_prices(benchmark_config)
    for b in benchmark_config:
        symbol = b["symbol"]
        if symbol in bm_prices:
            ledger["benchmark_history"].setdefault(symbol, []).append(
                {"date": today, "price": bm_prices[symbol]}
            )

    ledger_mod.save_ledger(ledger)
    dashboard = build_dashboard(ledger, all_prices, benchmark_config)
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_PATH, "w") as f:
        json.dump(dashboard, f, indent=2, default=str)
        f.write("\n")

    print(f"paper_trader: done. Portfolio value ${total_value:,.2f} "
          f"({dashboard['summary']['total_return_pct']:+.1%} since inception)")


if __name__ == "__main__":
    main()
