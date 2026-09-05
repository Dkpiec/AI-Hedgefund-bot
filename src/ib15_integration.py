"""
IB-15 Strategy Integration Module
==================================
Ties together ib15_strategy, ib15_execution, and state_store.

Handles:
1. IB-15 setup scanning on 15m candles
2. Manual approval gate ("Order is placed only after you explicitly approve")
3. Paper execution with 0.75% equity risk
4. Ongoing position management (TP1 50% @ 1.5R -> BE, TP2 3.0R / Chandelier, Time Stop)
5. Dual backup of open positions, trade history, and PnL:
   - Remote (Turso / Postgres via state_store)
   - Local disk JSON/JSONL (/data/ib15_positions.json, /data/ib15_trade_log.jsonl)
"""
import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

sys.path.append(str(Path(__file__).parent))

from evolution.strategies.ib15_strategy import IB15Strategy
from ib15_execution import (
    execute_ib15_bracket,
    check_ib15_positions,
    get_ib15_open_positions,
    has_ib15_position,
    cancel_ib15_position,
    load_ib15_positions,
    save_ib15_positions,
    _load_paper_balance,
    DATA_DIR,
)
from state_store import save_bot_state

# Instantiated strategy instance
ib15_engine = IB15Strategy()

# Pending approval queue for manual user approval
# format: {approval_id: {"symbol": symbol, "signal": signal_dict, "created_at": ts}}
_PENDING_APPROVALS: Dict[str, Dict] = {}
APPROVALS_FILE = DATA_DIR / "ib15_pending_approvals.json"


def _load_approvals():
    global _PENDING_APPROVALS
    try:
        if APPROVALS_FILE.exists():
            _PENDING_APPROVALS = json.loads(APPROVALS_FILE.read_text())
    except Exception:
        _PENDING_APPROVALS = {}


def _save_approvals():
    try:
        APPROVALS_FILE.write_text(json.dumps(_PENDING_APPROVALS, indent=2))
    except Exception as e:
        print(f"[IB15] Failed to save approvals: {e}")


# Load on module import
_load_approvals()


def convert_candles_to_ib15_format(klines: List[List]) -> Dict[str, List]:
    """Convert raw Binance klines [[open_time, open, high, low, close, vol, ...]] to strategy dict."""
    timestamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for k in klines:
        # timestamp
        ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat()
        timestamps.append(ts)
        opens.append(float(k[1]))
        highs.append(float(k[2]))
        lows.append(float(k[3]))
        closes.append(float(k[4]))
        volumes.append(float(k[5]))

    return {
        "timestamps": timestamps,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes,
    }


def scan_symbol_for_ib15(symbol: str, klines_15m: List[List], require_approval: bool = True) -> Optional[Dict]:
    """
    Scan symbol 15m candles for IB-15 setup.
    If found and require_approval=True, queue for manual user approval.
    If require_approval=False, return signal ready for execution.
    """
    if has_ib15_position(symbol):
        return None  # Max 1 open position per symbol

    data = convert_candles_to_ib15_format(klines_15m)
    signal = ib15_engine.evaluate(data)

    if not signal:
        return None

    if not signal.get("in_entry_window", True):
        # Outside 06:00-22:00 UTC window
        return None

    approval_id = f"APPR-{symbol}-{int(time.time())}"
    approval_record = {
        "approval_id": approval_id,
        "symbol": symbol,
        "signal": signal,
        "created_at": time.time(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PENDING_APPROVAL",
    }

    if require_approval:
        _PENDING_APPROVALS[approval_id] = approval_record
        _save_approvals()
        print(f"[IB15-GATE] Setup detected for {symbol}. Queued for approval (ID: {approval_id}).")
        return {
            "status": "QUEUED_FOR_APPROVAL",
            "approval_id": approval_id,
            "symbol": symbol,
            "signal": signal,
        }
    else:
        return signal


def get_pending_approvals() -> List[Dict]:
    """List all IB-15 setups waiting for user approval."""
    _load_approvals()
    return list(_PENDING_APPROVALS.values())


def approve_ib15_setup(approval_id: str, market_data: Dict) -> Dict:
    """
    User explicitly approves an IB-15 trade setup.
    Executes bracket order in paper mode and backs up state.
    """
    _load_approvals()
    record = _PENDING_APPROVALS.get(approval_id)
    if not record:
        return {"success": False, "error": f"Approval ID {approval_id} not found"}

    symbol = record["symbol"]
    signal = record["signal"]
    bracket = signal["bracket"]

    # Execute trade
    decision = {
        "logic": signal["logic"],
        "confidence_score": signal["confidence"],
        "ask_price": market_data.get("ask", bracket["entry"]),
    }

    res = execute_ib15_bracket(symbol, bracket, decision)

    if res.get("success"):
        # Remove from pending queue
        _PENDING_APPROVALS.pop(approval_id, None)
        _save_approvals()

        # Backup state to remote + local
        sync_ib15_backups_to_bot_state()
        print(f"[IB15-EXEC] Explicitly approved trade executed for {symbol}: {res['order_id']}")

    return res


def reject_ib15_setup(approval_id: str) -> Dict:
    """User rejects a pending setup."""
    _load_approvals()
    record = _PENDING_APPROVALS.pop(approval_id, None)
    if record:
        _save_approvals()
        return {"success": True, "message": f"Rejected setup {approval_id}"}
    return {"success": False, "error": f"Approval ID {approval_id} not found"}


def update_ib15_positions_loop(get_live_ticker_fn) -> List[Dict]:
    """
    Run position management cycle: checks SL, TP1 half-exit, TP2, Chandelier stop, Time stop.
    Performs immediate backup of state upon any closed trades.
    """
    closed_trades = check_ib15_positions(get_live_ticker_fn)

    if closed_trades:
        sync_ib15_backups_to_bot_state()

    return closed_trades


def sync_ib15_backups_to_bot_state() -> Dict:
    """
    Consolidates IB-15 open positions, trade history, and PnL,
    and backs them up via state_store (Turso/Postgres + local file).
    """
    positions = get_ib15_open_positions()

    # Calculate total PnL from trade log
    trade_log_file = DATA_DIR / "ib15_trade_log.jsonl"
    trade_log = []
    total_pnl = 0.0
    if trade_log_file.exists():
        for line in trade_log_file.read_text().splitlines():
            if line.strip():
                try:
                    rec = json.loads(line)
                    trade_log.append(rec)
                    if rec.get("type") == "CLOSE":
                        total_pnl += float(rec.get("pnl", 0.0))
                except Exception:
                    pass

    backup_payload = {
        "ib15_positions": positions,
        "ib15_trade_log": trade_log[-200:],  # keep last 200
        "ib15_pnl": total_pnl,
        "ib15_pending_approvals": list(_PENDING_APPROVALS.values()),
    }

    # Backup to bot_state store (Turso / Postgres / local state.json)
    save_bot_state(backup_payload)

    return {
        "open_positions_count": len(positions),
        "total_trades_logged": len(trade_log),
        "total_ib15_pnl": total_pnl,
        "pending_approvals": len(_PENDING_APPROVALS),
    }
