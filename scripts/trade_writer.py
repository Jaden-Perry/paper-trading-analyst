"""Turn this week's buy/sell/hold decisions + signals + headlines into
written analyst-style rationales via the Anthropic API. One batched request
per run (all events at once) rather than one call per trade, to keep cost
and failure surface minimal.
"""
from __future__ import annotations
import json
import os

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are a senior quant/hedge-fund analyst writing brief trade rationales for a \
weekly automated paper-trading strategy account, owned by a college student learning systematic \
investing. For EACH symbol below, write a 2-4 sentence rationale that:
- Explains the quantitative signal that drove the decision (momentum, trend, volume, valuation) \
in plain English.
- Explains the likely qualitative/news-driven catalyst behind the stock's recent move, based ONLY \
on the headlines given. If the headlines don't clearly explain the move, say the move looks \
technical or unclear rather than inventing a reason.
- For SELL actions, clearly states which specific rule triggered the exit (stop-loss, take-profit, \
max-hold timeout, or signal reversal).
- For HOLD actions, briefly says whether the original thesis still holds.
- For BUY actions, states the entry thesis clearly.
- Describes what the automated strategy decided and why, as a factual account of the system's own \
action - does not give personal financial advice or tell the reader what to do with their own money.

Respond with ONLY a JSON object mapping each symbol (exact string given) to its rationale string. \
No other text, no markdown code fence, no commentary outside the JSON object."""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _format_event_block(event: dict) -> str:
    symbol = event["symbol"]
    action = event["action"]
    signals = event.get("signals") or {}
    headlines = event.get("headlines") or []

    lines = [f"SYMBOL: {symbol}", f"ACTION: {action.upper()}"]

    if action == "sell":
        lines.append(f"EXIT REASON (rule that triggered): {event['exit_reason']}")
        lines.append(f"REALIZED P&L: {event['realized_pnl']:+.2f} ({event['realized_pnl_pct']:+.1%})")
        lines.append(f"ORIGINAL ENTRY THESIS: {event.get('entry_thesis') or '(not recorded)'}")
    elif action == "hold":
        lines.append(f"UNREALIZED P&L SINCE ENTRY: {event['unrealized_pnl_pct']:+.1%}")
        lines.append(f"ORIGINAL ENTRY THESIS: {event.get('entry_thesis') or '(not recorded)'}")
    elif action == "buy":
        lines.append(f"ENTRY PRICE: {event['price']:.2f}, SHARES: {event['shares']:.3f}")

    if signals:
        signal_lines = [
            f"momentum (vs. lookback window): {signals.get('momentum_pct', 0):+.1f}%",
            f"price vs. moving average: {signals.get('ma_pct_diff', 0):+.1f}%",
            f"volume vs. recent baseline: {signals.get('volume_ratio', 1):.1f}x"
            + (" (surge)" if signals.get("volume_surge") else ""),
        ]
        if signals.get("trailing_pe"):
            pe_line = f"trailing P/E: {signals['trailing_pe']:.1f}"
            if signals.get("pe_vs_median_pct") is not None:
                pe_line += f" ({signals['pe_vs_median_pct']:+.0f}% vs. screened-universe median)"
            signal_lines.append(pe_line)
        lines.append("QUANT SIGNALS:\n" + "\n".join(f"  - {l}" for l in signal_lines))
    else:
        lines.append("QUANT SIGNALS: (unavailable this run)")

    if headlines:
        headline_lines = "\n".join(f"  - [{h['source']}] {h['title']}" for h in headlines)
        lines.append(f"RECENT HEADLINES:\n{headline_lines}")
    else:
        lines.append("RECENT HEADLINES: (none found)")

    return "\n".join(lines)


def generate_rationales(events: list[dict]) -> dict[str, str]:
    """Returns {symbol: rationale_text}. Raises RuntimeError immediately if
    ANTHROPIC_API_KEY is missing or empty - GitHub Actions sets a
    secrets.X-backed env var to an EMPTY STRING (not unset) when the secret
    doesn't exist, so `os.environ.get(key) or None` is required here instead
    of bare `os.environ[key]` indexing, which would not catch that case.
    """
    if not events:
        return {}

    api_key = os.environ.get("ANTHROPIC_API_KEY") or None
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set (or is empty) - cannot generate trade rationales. "
            "Set it as a repo secret in Settings > Secrets and variables > Actions."
        )

    user_prompt = "\n\n".join(_format_event_block(e) for e in events)

    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 4000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=180,
    )
    if not resp.ok:
        print(f"Anthropic API error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    data = resp.json()

    text = None
    for block in data["content"]:
        if block.get("type") == "text":
            text = block["text"]
            break
    if text is None:
        raise RuntimeError(f"No text content block in Anthropic response: {data}")

    text = _strip_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse rationale JSON from model response: {e}\nRaw: {text[:500]}")
