"""
State persistence for the AI Hedge Fund Bot.

Solves the "engine restarts fresh on every code push" problem.

bot_state (in main.py) and OPEN_ORDERS (in execution.py) used to live
only in process memory — when Render restarts the FastAPI process
(after a git push, or a cold start, or a redeploy), both dicts were
wiped back to defaults: $200 starting balance, 0 trades, 0 positions.

This module persists the bits that matter for a "real-world" feel:

  - trade_history, balance, equity, pnl, pnl_pct, starting_balance
  - open_orders (the active positions, so SL/TP continues tracking)
  - cycles_completed
  - current_model / resolved_model / scan_interval / chart_timeframe
  - started_at, is_running
  - universe

Everything is written to JSON files under data/. Writes are atomic
(write to .tmp then rename) so a crash mid-write can't corrupt the
state.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Resolve data/ next to the repo root (same convention as PAPER_BALANCE_FILE)
_REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_DIR / "state.json"
ORDERS_FILE = DATA_DIR / "paper_orders.json"

# Fields in bot_state we DO persist. Anything not listed here is
# recomputed/derived and should not be snapshotted.
_PERSISTED_BOT_KEYS = [
    "starting_balance",
    "balance",
    "equity",
    "free",
    "pnl",
    "pnl_pct",
    "trade_history",
    "cancelled_orders",
    "cycles_completed",
    "current_model",
    "resolved_model",
    "scan_interval",
    "interval",
    "chart_timeframe",
    "started_at",
    "is_running",
    "universe",
]


def _atomic_write(path: Path, payload: str) -> None:
    """Write atomically: write to .tmp, then os.replace. Crash-safe."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(payload)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[STATE_STORE] Failed to write {path}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# bot_state (the financial state in main.py)
# ---------------------------------------------------------------------------

def save_bot_state(state: Dict[str, Any]) -> None:
    """Snapshot the persistable fields of bot_state to disk."""
    snapshot = {k: state.get(k) for k in _PERSISTED_BOT_KEYS if k in state}
    _atomic_write(STATE_FILE, json.dumps(snapshot, indent=2, default=str))


def load_bot_state(into: Dict[str, Any]) -> bool:
    """Merge persisted bot_state fields into `into`. Returns True if a
    snapshot was found and applied. Fields that aren't on disk stay as
    their in-process defaults."""
    if not STATE_FILE.exists():
        return False
    try:
        snap = json.loads(STATE_FILE.read_text())
    except Exception as e:
        print(f"[STATE_STORE] state.json unreadable, ignoring: {e}", file=sys.stderr)
        return False
    for k, v in snap.items():
        into[k] = v
    return True


# ---------------------------------------------------------------------------
# OPEN_ORDERS (the live positions dict in execution.py)
# ---------------------------------------------------------------------------

def save_open_orders(orders: Dict[str, Dict]) -> None:
    _atomic_write(ORDERS_FILE, json.dumps(orders, indent=2, default=str))


def load_open_orders() -> Dict[str, Dict]:
    if not ORDERS_FILE.exists():
        return {}
    try:
        return json.loads(ORDERS_FILE.read_text())
    except Exception as e:
        print(f"[STATE_STORE] paper_orders.json unreadable, ignoring: {e}", file=sys.stderr)
        return {}
