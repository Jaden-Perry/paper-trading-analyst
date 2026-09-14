"""Read/write helpers for the persistent paper-trading ledger, mirroring the
sibling Internship_Tracker project's load_json/save_json bootstrap pattern.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "trades.json"


def load_ledger(initial_capital: float) -> dict:
    if not LEDGER_PATH.exists():
        return {
            "cash": initial_capital,
            "initial_capital": initial_capital,
            "positions": [],
            "closed_trades": [],
            "history": [],
            "weekly_reports": [],
        }
    with open(LEDGER_PATH) as f:
        return json.load(f)


def save_ledger(ledger: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "w") as f:
        json.dump(ledger, f, indent=2, default=str)
        f.write("\n")
