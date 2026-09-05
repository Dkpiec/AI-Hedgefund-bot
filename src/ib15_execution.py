"""
IB-15 Execution Engine
=======================
Handles bracket order execution, partial TP, trailing stops, time stops,
and PostgreSQL backup (+ local JSON cache) for the IB-15 strategy.
"""
import time
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

sys.path.append(str(Path(__file__).parent.parent))
from config import (
    PAPER_BALANCE_FILE,
    STARTING_BALANCE,
    get_position_size_for_balance,
    get_risk_per_trade_for_balance,
    get_current_tier,
    MAX_PRICE_USD,
)
from state_store import load_open_orders, save_open_orders

# ---------------------------------------------------------------------------
# In-memory tracking for IB-15 bracket positions
# ---------------------------------------------------------------------------

_IB15_POSITIONS: Dict[str, Dict] = {}  # order_id -> position dict
_ib15_id_counter = [0]
_lock = threading.Lock()

# Local backup files
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
IB15_POSITIONS_FILE = DATA_DIR / "ib15_positions.json"
IB15_TRADE_LOG_FILE = DATA_DIR / "ib15_trade_log.jsonl"
IB15_STATE_FILE = DATA_DIR / "ib15_state.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[IB15-EXEC] Failed to save {path.name}: {e}")


def _append_jsonl(path: Path, record: Dict) -> None:
    try:
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"[IB15-EXEC] Failed to append {path.name}: {e}")


# ---------------------------------------------------------------------------
# Persistence load/save
# ---------------------------------------------------------------------------

def load_ib15_positions() -> None:
    """Load IB-15 positions from local JSON backup."""
    global _IB15_POSITIONS
    data = _load_json(IB15_POSITIONS_FILE, {})
    with _lock:
        _IB15_POSITIONS = data


def save_ib15_positions() -> None:
    """Save IB-15 positions to local JSON backup."""
    with _lock:
        _save_json(IB15_POSITIONS_FILE, _IB15_POSITIONS)


def load_ib15_state() -> Dict:
    """Load IB-15 engine state (last scan, approval queue, etc.)."""
    return _load_json(IB15_STATE_FILE, {})


def save_ib15_state(state: Dict) -> None:
    """Save IB-15 engine state."""
    _save_json(IB15_STATE_FILE, state)


# ---------------------------------------------------------------------------
# Balance handling (paper mode)
# ---------------------------------------------------------------------------

def _load_paper_balance() -> float:
    try:
        return float(PAPER_BALANCE_FILE.read_text().strip())
    except Exception:
        return float(STARTING_BALANCE)


def _save_paper_balance(balance: float) -> None:
    try:
        PAPER_BALANCE_FILE.write_text(f"{balance:.6f}\n")
    except Exception as e:
        print(f"[IB15-EXEC] Could not persist paper balance: {e}")


# ---------------------------------------------------------------------------
# Bracket order execution
# ---------------------------------------------------------------------------

def execute_ib15_bracket(symbol: str, bracket: Dict, decision: Dict) -> Dict:
    """
    Execute an IB-15 bracket order in paper mode.
    Returns dict with success status, order_id, and full position data.
    """
    direction = bracket["direction"]
    entry = bracket["entry"]
    stop = bracket["stop"]
    tp1 = bracket["tp1"]
    tp2 = bracket["tp2"]
    risk = bracket["risk"]
    atr = bracket["atr"]
    time_stop_bars = bracket.get("time_stop_bars", 8)

    ask_price = decision.get("ask_price", entry)

    # Price filter
    if ask_price > MAX_PRICE_USD:
        return {"success": False, "error": f"Price ${ask_price:.2f} exceeds MAX_PRICE_USD={MAX_PRICE_USD}"}

    # Position sizing: risk 0.75% of equity per trade (IB-15 default)
    # Under 4x USDT Futures leverage: required margin = notional / 4.0
    LEVERAGE = 4
    free = _load_paper_balance()
    if free <= 0:
        return {"success": False, "error": "Paper balance is $0"}

    risk_pct = 0.0075  # 0.75% per IB-15 rules
    target_risk_usd = free * risk_pct

    # Use risk distance to compute qty
    qty = target_risk_usd / risk if risk > 0 else 0
    notional = qty * ask_price
    margin_required = notional / float(LEVERAGE)

    if notional < 10.0:
        # Round up to meet $10 minimum
        from execution import _round_step, get_symbol_info
        info = get_symbol_info(symbol)
        step = info.get("stepSize", 0.0)
        if step > 0 and ask_price > 0:
            target_qty = 10.0 / ask_price
            bumped = (int(target_qty / step) + 1) * step
            max_affordable_margin = (free - 0.01)
            max_affordable_notional = max_affordable_margin * LEVERAGE
            max_qty = int((max_affordable_notional / ask_price) / step) * step if step > 0 else target_qty
            qty = min(bumped, max_qty)
            notional = qty * ask_price
            margin_required = notional / float(LEVERAGE)

    if notional < 10.0:
        return {"success": False, "error": f"Notional ${notional:.2f} below $10 minimum"}

    if margin_required > free - 0.01:
        return {"success": False, "error": f"Required 4x margin ${margin_required:.2f} exceeds free cash ${free:.2f}"}

    # Deduct margin required (4x leverage) from virtual cash balance
    new_balance = free - margin_required
    _save_paper_balance(new_balance)

    # Create position record
    _ib15_id_counter[0] += 1
    order_id = f"IB15-PAPER-{_ib15_id_counter[0]}"
    now_ts = time.time()

    position = {
        "order_id": order_id,
        "symbol": symbol,
        "direction": direction,
        "instrument": "USDT_FUTURES",
        "leverage": LEVERAGE,
        "margin_required": margin_required,
        "side": "BUY" if direction == "long" else "SELL",
        "status": "ACTIVE",
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "atr": atr,
        "risk": risk,
        "qty": qty,
        "notional": notional,
        "tp1_filled": False,
        "remaining_qty": qty,
        "entry_bar_index": bracket.get("breakout_bar_index", 0),
        "bars_since_entry": 0,
        "highest_since_entry": entry if direction == "long" else None,
        "lowest_since_entry": entry if direction == "short" else None,
        "time_stop_bars": time_stop_bars,
        "chandelier_mult": bracket.get("chandelier_mult", 2.0),
        "placed_at": now_ts,
        "filled_at": now_ts,
        "logic": decision.get("logic", ""),
        "confidence": decision.get("confidence_score", 0),
        "bracket": bracket,
        "mode": "PAPER",
        "tier": get_current_tier(free),
        "risk_per_trade": get_risk_per_trade_for_balance(free),
    }

    with _lock:
        _IB15_POSITIONS[order_id] = position
    save_ib15_positions()

    # Log to trade log
    _append_jsonl(IB15_TRADE_LOG_FILE, {
        "type": "OPEN",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "order_id": order_id,
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "qty": qty,
        "notional": notional,
        "balance_after": new_balance,
        "bracket": bracket,
    })

    return {
        "success": True,
        "mode": "PAPER",
        "order_id": order_id,
        "symbol": symbol,
        "signal": "BUY" if direction == "long" else "SELL",
        "price": entry,
        "qty": qty,
        "sl": stop,
        "tp1": tp1,
        "tp2": tp2,
        "notional": notional,
        "risk_per_trade": position["risk_per_trade"],
        "tier": position["tier"],
    }


# ---------------------------------------------------------------------------
# Position management: SL/TP/Time stop / Chandelier
# ---------------------------------------------------------------------------

def check_ib15_positions(get_live_ticker) -> List[Dict]:
    """
    Check all active IB-15 positions against live ticker.
    Returns list of closed trade records.
    """
    closed = []
    now_ts = time.time()

    with _lock:
        positions = dict(_IB15_POSITIONS)

    for oid, pos in positions.items():
        if pos.get("status") != "ACTIVE":
            continue

        symbol = pos["symbol"]
        direction = pos["direction"]
        entry = pos["entry"]
        stop = pos["stop"]
        tp1 = pos["tp1"]
        tp2 = pos["tp2"]
        atr = pos["atr"]
        qty = pos["qty"]
        remaining_qty = pos.get("remaining_qty", qty)
        tp1_filled = pos.get("tp1_filled", False)
        bars_since_entry = pos.get("bars_since_entry", 0)
        highest = pos.get("highest_since_entry", entry)
        lowest = pos.get("lowest_since_entry", entry)

        live = get_live_ticker(symbol)
        if live is None or live <= 0:
            continue

        hit = None
        exit_price = None
        close_pct = 1.0  # default: close 100%

        if direction == "long":
            # Update highest for chandelier
            if live > highest:
                highest = live
                pos["highest_since_entry"] = highest

            if not tp1_filled:
                # Phase 1: waiting for TP1 or SL
                if live <= stop:
                    hit, exit_price = "SL_HIT", stop
                elif live >= tp1:
                    hit, exit_price = "TP1_HALF", tp1
                    close_pct = 0.5
            else:
                # Phase 2: TP1 hit, remaining 50% with chandelier trailing
                chandelier_stop = highest - pos["chandelier_mult"] * atr
                # Update stop to breakeven after TP1
                if stop < entry:
                    stop = entry
                    pos["stop"] = entry
                if live <= stop:
                    hit, exit_price = "BE_SL_HIT", stop
                elif live <= chandelier_stop:
                    hit, exit_price = "CHANDELIER", chandelier_stop
                elif live >= tp2:
                    hit, exit_price = "TP2", tp2
        else:  # short
            if live < lowest:
                lowest = live
                pos["lowest_since_entry"] = lowest

            if not tp1_filled:
                if live >= stop:
                    hit, exit_price = "SL_HIT", stop
                elif live <= tp1:
                    hit, exit_price = "TP1_HALF", tp1
                    close_pct = 0.5
            else:
                chandelier_stop = lowest + pos["chandelier_mult"] * atr
                if stop > entry:
                    stop = entry
                    pos["stop"] = entry
                if live >= stop:
                    hit, exit_price = "BE_SL_HIT", stop
                elif live >= chandelier_stop:
                    hit, exit_price = "CHANDELIER", chandelier_stop
                elif live <= tp2:
                    hit, exit_price = "TP2", tp2

        # Time stop check: if +1R not reached within time_stop_bars
        if hit is None:
            pos["bars_since_entry"] = bars_since_entry + 1
            risk = pos.get("risk", 0.0)
            time_stop_bars = pos.get("time_stop_bars", 8)
            one_r = entry + risk if direction == "long" else entry - risk
            if direction == "long":
                if bars_since_entry >= time_stop_bars and live < one_r:
                    hit, exit_price = "TIME_STOP", live
            else:
                if bars_since_entry >= time_stop_bars and live > one_r:
                    hit, exit_price = "TIME_STOP", live

        if hit:
            # Portion of position being closed
            closed_notional = pos.get("notional", qty * entry) * close_pct
            leverage = pos.get("leverage", 4)
            closed_margin = pos.get("margin_required", closed_notional / leverage) * close_pct

            # Compute PnL for the closed portion
            if direction == "long":
                pnl = (exit_price - entry) * qty * close_pct
            else:
                pnl = (entry - exit_price) * qty * close_pct

            # Credit back closed portion's margin + PnL (4x futures leverage accounting)
            new_balance = _load_paper_balance() + closed_margin + pnl
            _save_paper_balance(new_balance)

            closed_trade = {
                "order_id": oid,
                "symbol": symbol,
                "direction": direction,
                "side": pos["side"],
                "entry": entry,
                "exit": exit_price,
                "qty": qty * close_pct,
                "close_pct": close_pct,
                "sl": stop,
                "tp1": tp1,
                "tp2": tp2,
                "pnl": pnl,
                "outcome": hit,
                "notional": closed_notional,
                "balance_after": new_balance,
                "closed_at": now_ts,
                "logic": pos.get("logic", ""),
                "confidence": pos.get("confidence", 0),
                "mode": "PAPER",
                "risk_per_trade": pos.get("risk_per_trade", 0.0),
                "tier": pos.get("tier", 0),
            }

            # If partial close (TP1_HALF), update position for remaining half
            if hit == "TP1_HALF":
                with _lock:
                    pos["tp1_filled"] = True
                    pos["remaining_qty"] = qty * (1.0 - close_pct)
                    pos["notional"] = pos.get("notional", qty * entry) * (1.0 - close_pct)
                    pos["stop"] = entry  # Move SL to breakeven
                    _IB15_POSITIONS[oid] = pos
                save_ib15_positions()
                closed.append(closed_trade)
                continue

            # Full close - remove position
            with _lock:
                _IB15_POSITIONS.pop(oid, None)
            save_ib15_positions()

            # Log
            _append_jsonl(IB15_TRADE_LOG_FILE, {
                "type": "CLOSE",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **closed_trade,
            })

            closed.append(closed_trade)

    return closed


def get_ib15_open_positions() -> List[Dict]:
    """Get all active IB-15 positions."""
    with _lock:
        res = []
        now = time.time()
        for oid, pos in _IB15_POSITIONS.items():
            if pos.get("status") == "ACTIVE":
                placed = pos.get("placed_at") or pos.get("created_at") or 0
                if isinstance(placed, str):
                    try:
                        from datetime import datetime, timezone
                        placed = datetime.fromisoformat(placed.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        placed = now
                age = max(0.0, now - float(placed)) if placed else 0.0
                res.append({"order_id": oid, **pos, "age_seconds": round(age, 1)})
        return res


def has_ib15_position(symbol: str) -> bool:
    """Check if an IB-15 position exists for symbol."""
    with _lock:
        for pos in _IB15_POSITIONS.values():
            if pos.get("symbol") == symbol and pos.get("status") == "ACTIVE":
                return True
    return False


def cancel_ib15_position(order_id: str) -> bool:
    """Manually cancel/close an IB-15 position."""
    with _lock:
        pos = _IB15_POSITIONS.pop(order_id, None)
    if pos:
        save_ib15_positions()
        # Return notional to balance
        free = _load_paper_balance()
        notional = pos.get("notional", 0)
        _save_paper_balance(free + notional)
        return True
    return False


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

load_ib15_positions()