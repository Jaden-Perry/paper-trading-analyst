"""Pure decision logic: given the current ledger state and this week's
screened candidates, decide what to sell (stop-loss / take-profit / max-hold
timeout / signal reversal) and what to buy (moderate sizing: ~15% of
portfolio value per position, capped at max_positions concurrent holdings).

No I/O here - callers handle fetching prices/news and persisting the result.
"""
from __future__ import annotations
import copy
from datetime import date, timedelta


def _add_weeks(today: str, weeks: int) -> str:
    return (date.fromisoformat(today) + timedelta(weeks=weeks)).isoformat()


def decide(
    ledger: dict, candidates: list[dict], held_prices: dict[str, float], config: dict, today: str
) -> tuple[dict, list[dict]]:
    """Returns (updated_ledger, events). Each event describes one buy/sell/
    hold decision with the signals behind it, for trade_writer to narrate.
    """
    ledger = copy.deepcopy(ledger)
    events = []
    candidates_by_symbol = {c["symbol"]: c for c in candidates}

    remaining_positions = []
    for pos in ledger["positions"]:
        symbol = pos["symbol"]
        price = held_prices.get(symbol)
        if price is None:
            # Couldn't get a current price this run - hold as-is rather than
            # guessing; it'll be re-evaluated next run.
            remaining_positions.append(pos)
            continue

        cand = candidates_by_symbol.get(symbol)
        exit_reason = None
        if price <= pos["stop_loss"]:
            exit_reason = "stop_loss"
        elif price >= pos["take_profit"]:
            exit_reason = "take_profit"
        elif today >= pos["max_hold_until"]:
            exit_reason = "max_hold_timeout"
        elif cand is None or cand["score"] <= 0:
            exit_reason = "signal_reversal"

        if exit_reason:
            proceeds = pos["shares"] * price
            realized_pnl = proceeds - pos["shares"] * pos["entry_price"]
            realized_pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
            ledger["cash"] += proceeds
            ledger["closed_trades"].append(
                {
                    "symbol": symbol,
                    "name": pos.get("name") or (cand.get("name") if cand else symbol),
                    "shares": pos["shares"],
                    "entry_price": pos["entry_price"],
                    "entry_date": pos["entry_date"],
                    "entry_thesis": pos.get("entry_thesis", ""),
                    "exit_price": price,
                    "exit_date": today,
                    "exit_reason": exit_reason,
                    "exit_thesis": "",  # filled in after trade_writer runs
                    "realized_pnl": realized_pnl,
                    "realized_pnl_pct": realized_pnl_pct,
                }
            )
            events.append(
                {
                    "action": "sell",
                    "symbol": symbol,
                    "name": pos.get("name") or (cand.get("name") if cand else symbol),
                    "price": price,
                    "exit_reason": exit_reason,
                    "realized_pnl": realized_pnl,
                    "realized_pnl_pct": realized_pnl_pct,
                    "entry_thesis": pos.get("entry_thesis", ""),
                    "signals": cand,
                }
            )
        else:
            remaining_positions.append(pos)
            events.append(
                {
                    "action": "hold",
                    "symbol": symbol,
                    "name": pos.get("name") or (cand.get("name") if cand else symbol),
                    "price": price,
                    "unrealized_pnl_pct": (price - pos["entry_price"]) / pos["entry_price"],
                    "entry_thesis": pos.get("entry_thesis", ""),
                    "signals": cand,
                }
            )

    ledger["positions"] = remaining_positions
    held_symbols = {p["symbol"] for p in ledger["positions"]}
    open_slots = config["max_positions"] - len(ledger["positions"])

    if open_slots > 0:
        total_value = ledger["cash"] + sum(
            p["shares"] * held_prices.get(p["symbol"], p["entry_price"]) for p in ledger["positions"]
        )
        target_position_value = total_value * config["position_pct"]

        buy_candidates = [
            c for c in candidates if c["symbol"] not in held_symbols and c["score"] > 0
        ]
        for c in buy_candidates:
            if open_slots <= 0:
                break
            position_value = min(target_position_value, ledger["cash"])
            if position_value < 10:  # not enough cash left to meaningfully enter
                continue
            price = c["price"]
            shares = position_value / price
            new_pos = {
                "symbol": c["symbol"],
                "name": c.get("name", c["symbol"]),
                "shares": shares,
                "entry_price": price,
                "entry_date": today,
                "entry_thesis": "",  # filled in after trade_writer runs
                "stop_loss": price * (1 - config["stop_loss_pct"]),
                "take_profit": price * (1 + config["take_profit_pct"]),
                "max_hold_until": _add_weeks(today, config["max_hold_weeks"]),
                "entry_signals": c,
            }
            ledger["cash"] -= position_value
            ledger["positions"].append(new_pos)
            held_symbols.add(c["symbol"])
            open_slots -= 1
            events.append(
                {
                    "action": "buy",
                    "symbol": c["symbol"],
                    "name": c.get("name", c["symbol"]),
                    "price": price,
                    "shares": shares,
                    "signals": c,
                }
            )

    return ledger, events
