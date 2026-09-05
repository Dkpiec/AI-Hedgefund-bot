"""
Execution Engine - Paper + Binance Spot Forward-Testing Executor
================================================================
Two execution modes:

1. PAPER (forward test): no API key. Every signal is tracked in OPEN_ORDERS,
   filled instantly at the live ask price, and monitored for SL/TP hits
   against real Binance public ticker prices. $200 starting balance, virtual.

2. LIVE_LIMIT: signed Binance client places a real limit order on testnet
   or mainnet, with the existing 5-min auto-cancel.

SL/TP are computed from the entry price, not the limit ask, so the R:R
ratio is exact. `check_paper_sl_tp()` is called by main.py every cycle.
"""
import math
import sys
import time
from datetime import datetime
from typing import Dict, Optional

sys.path.append('..')
from config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    BINANCE_TESTNET,
    ORDER_TIMEOUT_SECONDS,
    PAPER_BALANCE_FILE,
    RISK_PER_TRADE,
    SL_PERCENT,
    STARTING_BALANCE,
    STRICT_LIMIT_ORDERS,
    TP_PERCENT,
)
from state_store import load_open_orders, save_open_orders
from data.data_engine import get_account_info, get_live_ticker, get_symbol_info

# In-memory tracking of open orders: {order_id: {symbol, side, qty, price, placed_at, sl, tp, mode, ...}}
OPEN_ORDERS: Dict[str, Dict] = {}
# Load persisted open orders so the engine continues where it left off.
OPEN_ORDERS.update(load_open_orders())

# Per-order counter for paper mode order ids (PAPER-1, PAPER-2, ...)
_paper_id_counter = [0]

# Persisted virtual paper balance (USD). Lives in a file so it survives restarts.
def _load_paper_balance() -> float:
    try:
        return float(PAPER_BALANCE_FILE.read_text().strip())
    except Exception:
        return float(STARTING_BALANCE)


def _save_paper_balance(balance: float) -> None:
    try:
        PAPER_BALANCE_FILE.write_text(f"{balance:.6f}\n")
    except Exception as e:
        print(f"[EXEC] Could not persist paper balance: {e}")


def get_paper_balance() -> float:
    """Current virtual USDT balance (paper mode)."""
    return _load_paper_balance()


_client = None


def _get_client():
    """Lazy-initialize signed Binance client. Requires API key."""
    global _client
    if _client is not None:
        return _client
    try:
        from binance.client import Client
        if not BINANCE_API_KEY or BINANCE_API_KEY.startswith("your-"):
            return None
        _client = Client(BINANCE_API_KEY, BINANCE_API_SECRET, testnet=BINANCE_TESTNET)
        return _client
    except Exception as e:
        print(f"[EXEC] Binance client init failed: {e}")
        return None


def is_live_mode() -> bool:
    """True only if a signed Binance client is available AND user wants real orders."""
    return _get_client() is not None and STRICT_LIMIT_ORDERS


def _round_step(value: float, step_size: float) -> float:
    """Round value down to nearest step_size increment."""
    if step_size <= 0:
        return value
    precision = int(round(-math.log10(step_size)))
    return math.floor(value / step_size) * step_size


def _calc_qty(price: float, symbol: str, account_free: float) -> float:
    """
    Position size = RISK_PER_TRADE (5%) of free balance, capped at the trade value.
    For spot: qty = (balance * RISK_PER_TRADE) / price.
    Rounded to LOT_SIZE step.
    """
    if price <= 0 or account_free <= 0:
        return 0.0
    target_value = account_free * RISK_PER_TRADE
    raw_qty = target_value / price
    info = get_symbol_info(symbol)
    step = info.get("stepSize", 0.0)
    min_qty = info.get("minQty", 0.0)
    qty = _round_step(raw_qty, step) if step else raw_qty
    if qty < min_qty:
        return 0.0
    return qty


def execute_trade(symbol: str, signal: str, ask_price: float, decision: dict) -> dict:
    """
    Place a strict limit order for the given signal.
    Returns order dict with status, id, price, qty, sl, tp.
    decision is the full AI decision (used to attach logic and confidence).
    """
    if signal not in ("BUY", "SELL"):
        return {"success": False, "error": f"Invalid signal: {signal}"}

    client = _get_client()
    if client is None:
        return _success_paper(symbol, signal, ask_price, decision)

    if not STRICT_LIMIT_ORDERS:
        return {"success": False, "error": "Market orders disabled; STRICT_LIMIT_ORDERS=true"}

    acct = get_account_info()
    free = acct.get("free", 0.0)
    if free <= 0:
        return {"success": False, "error": "No free USDT balance"}

    qty = _calc_qty(ask_price, symbol, free)
    if qty <= 0:
        return {"success": False, "error": f"Position size below minimum for {symbol}"}

    # Limit price = current ask (BUY) or current bid (SELL); use ask for both
    # as a conservative estimate; tighten 0.05% to encourage fills
    limit_price = ask_price
    if signal == "BUY":
        limit_price = ask_price * 1.0005
    else:
        limit_price = ask_price * 0.9995

    info = get_symbol_info(symbol)
    tick = info.get("tickSize", 0.0)
    if tick > 0:
        precision = int(round(-math.log10(tick)))
        limit_price = round(limit_price, precision)
    else:
        limit_price = round(limit_price, 6)

    # Compute SL/TP from the limit price
    if signal == "BUY":
        sl = round(limit_price * (1 - SL_PERCENT), 6)
        tp = round(limit_price * (1 + TP_PERCENT), 6)
    else:
        sl = round(limit_price * (1 + SL_PERCENT), 6)
        tp = round(limit_price * (1 - TP_PERCENT), 6)

    try:
        if signal == "BUY":
            order = client.order_limit_buy(
                symbol=symbol,
                quantity=qty,
                price=str(limit_price),
                timeInForce="GTC",
            )
        else:
            order = client.order_limit_sell(
                symbol=symbol,
                quantity=qty,
                price=str(limit_price),
                timeInForce="GTC",
            )
        order_id = str(order.get("orderId", ""))
        OPEN_ORDERS[order_id] = {
            "symbol": symbol,
            "side": signal,
            "qty": qty,
            "price": limit_price,
            "sl": sl,
            "tp": tp,
            "placed_at": time.time(),
            "logic": decision.get("logic", ""),
            "confidence": decision.get("confidence_score", 0),
            "timeframe": decision.get("timeframe", ""),
        }
        save_open_orders(OPEN_ORDERS)
        return {
            "success": True,
            "mode": "LIVE_LIMIT",
            "order_id": order_id,
            "symbol": symbol,
            "signal": signal,
            "price": limit_price,
            "qty": qty,
            "sl": sl,
            "tp": tp,
        }
    except Exception as e:
        return {"success": False, "error": f"order_limit failed: {e}"}


def _success_paper(symbol: str, signal: str, ask_price: float, decision: dict) -> dict:
    """
    Open a tracked paper position: filled instantly at the ask price.
    The position lives in OPEN_ORDERS until SL or TP is hit (see check_paper_sl_tp).
    Virtual USDT balance is deducted for the entry notional.

    Position size uses the tiered schedule from config.get_position_size_for_balance:
        balance $0–$400  →  $10 / trade
        balance $400–$600  →  $20 / trade
        +$10 / trade per +$200 in balance
    """
    if ask_price <= 0:
        return {"success": False, "error": f"Invalid ask price for {symbol}"}

    # STRICT RULE: reject any symbol whose unit price exceeds MAX_PRICE_USD.
    # This prevents BTC ($65k), ETH ($3k), BNB ($600+) from being traded
    # with our small capital base. Rule is intentional and must not be bypassed.
    from config import MAX_PRICE_USD as _MAX_PRICE
    if ask_price > _MAX_PRICE:
        return {"success": False, "error": f"RULE VIOLATION: {symbol} price ${ask_price:.2f} exceeds MAX_PRICE_USD=${_MAX_PRICE:.0f}. Trade blocked."}

    from config import get_position_size_for_balance, get_risk_per_trade_for_balance, get_current_tier

    free = _load_paper_balance()
    if free <= 0:
        return {"success": False, "error": "Paper balance is $0 — cannot open new position"}

    # Tiered position sizing: every +$200 in balance → +$10 position size
    target_value = get_position_size_for_balance(free)
    # Cap target at what we can actually afford (leave a small buffer)
    target_value = min(target_value, max(0.0, free - 0.01))

    info = get_symbol_info(symbol)
    step = info.get("stepSize", 0.0)

    # Compute qty from the target dollar value
    if step > 0 and ask_price > 0:
        raw_qty = target_value / ask_price
        qty = _round_step(raw_qty, step)
        notional = qty * ask_price
    else:
        qty = target_value / ask_price if ask_price > 0 else 0
        notional = target_value

    if notional < 10.0:
        # Notional is below the $10 Binance minimum because rounding the qty DOWN
        # to the step size dropped the dollar value under $10. Round the qty UP to
        # the next step so the notional clears $10, but cap at what we can afford.
        if step > 0 and ask_price > 0:
            target_qty = 10.0 / ask_price  # qty needed for $10 exactly
            bumped = (int(target_qty / step) + 1) * step  # always round UP
            # Cap at what we can afford (leave 1 cent buffer)
            max_affordable = (free - 0.01) / ask_price
            max_qty = int(max_affordable / step) * step
            qty = min(bumped, max_qty)
            notional = qty * ask_price
    if notional < 10.0:
        return {"success": False, "error": f"Notional ${notional:.2f} below $10 minimum for {symbol} (free=${free:.2f})"}

    # Track the risk-per-trade in the order record (for strategy evolution)
    risk_per_trade = get_risk_per_trade_for_balance(free)
    current_tier = get_current_tier(free)

    # Deduct notional from virtual balance
    new_balance = free - notional
    _save_paper_balance(new_balance)

    if signal == "BUY":
        sl = round(ask_price * (1 - SL_PERCENT), 6)
        tp = round(ask_price * (1 + TP_PERCENT), 6)
    else:
        sl = round(ask_price * (1 + SL_PERCENT), 6)
        tp = round(ask_price * (1 - TP_PERCENT), 6)

    _paper_id_counter[0] += 1
    order_id = f"PAPER-{_paper_id_counter[0]}"
    OPEN_ORDERS[order_id] = {
        "symbol": symbol,
        "side": signal,
        "qty": qty,
        "price": ask_price,
        "sl": sl,
        "tp": tp,
        "placed_at": time.time(),
        "filled_at": time.time(),
        "logic": decision.get("logic", ""),
        "confidence": decision.get("confidence_score", 0),
        "timeframe": decision.get("timeframe", ""),
        "mode": "PAPER",
        "notional": notional,
        "risk_per_trade": risk_per_trade,
        "tier": current_tier,
    }
    save_open_orders(OPEN_ORDERS)
    return {
        "success": True,
        "mode": "PAPER",
        "order_id": order_id,
        "symbol": symbol,
        "signal": signal,
        "price": ask_price,
        "qty": qty,
        "sl": sl,
        "tp": tp,
        "notional": notional,
        "risk_per_trade": risk_per_trade,
        "tier": current_tier,
    }


def check_paper_sl_tp() -> list:
    """
    For every open PAPER position, fetch the live ticker and check whether
    SL or TP was hit. On hit, close the position: compute PnL, add notional
    + PnL back to the virtual balance, append a closed-trade record to a
    in-memory list (caller is responsible for pushing to trade_history),
    and remove the order from OPEN_ORDERS.

    Returns a list of closed-trade dicts. Empty list if nothing closed.
    """
    closed = []
    for oid, o in list(OPEN_ORDERS.items()):
        if o.get("mode") != "PAPER":
            continue  # LIVE_LIMIT positions are managed by sync_open_orders
        symbol = o["symbol"]
        side = o["side"]
        entry = o["price"]
        sl = o["sl"]
        tp = o["tp"]
        qty = o["qty"]

        live = get_live_ticker(symbol)
        if live is None or live <= 0:
            continue  # no live price; skip this cycle

        hit = None
        exit_price = None
        if side == "BUY":
            # Long: SL triggered if live <= sl, TP triggered if live >= tp
            if live <= sl:
                hit, exit_price = "SL_HIT", sl
            elif live >= tp:
                hit, exit_price = "TP_HIT", tp
        else:  # SELL (short for paper)
            if live >= sl:
                hit, exit_price = "SL_HIT", sl
            elif live <= tp:
                hit, exit_price = "TP_HIT", tp

        if not hit:
            continue

        # Compute PnL in USDT
        if side == "BUY":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty

        notional = o.get("notional", qty * entry)
        # Return notional + PnL to virtual balance
        new_balance = _load_paper_balance() + notional + pnl
        _save_paper_balance(new_balance)

        closed.append({
            "order_id": oid,
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "qty": qty,
            "sl": sl,
            "tp": tp,
            "pnl": pnl,
            "outcome": hit,
            "notional": notional,
            "balance_after": new_balance,
            "closed_at": time.time(),
            "logic": o.get("logic", ""),
            "confidence": o.get("confidence", 0),
            "timeframe": o.get("timeframe", ""),
            "mode": "PAPER",
            "risk_per_trade": o.get("risk_per_trade", 0.0),
            "tier": o.get("tier", 0),
        })
        OPEN_ORDERS.pop(oid, None)
        save_open_orders(OPEN_ORDERS)
    return closed


def cancel_expired_orders() -> list:
    """
    Cancel any tracked LIVE limit orders that have exceeded ORDER_TIMEOUT_SECONDS.
    PAPER positions are NOT cancelled here — they live until SL/TP hits (forward test).
    Returns list of cancelled order dicts.
    """
    client = _get_client()
    now = time.time()
    cancelled = []
    expired_ids = [
        oid for oid, o in OPEN_ORDERS.items()
        if o.get("mode") != "PAPER"  # skip paper positions
        and (now - o.get("placed_at", 0)) > ORDER_TIMEOUT_SECONDS
    ]
    for oid in expired_ids:
        o = OPEN_ORDERS[oid]
        if client is not None:
            try:
                client.cancel_order(symbol=o["symbol"], orderId=oid)
            except Exception as e:
                # Order may already be filled or cancelled — just drop it from tracking
                print(f"[EXEC] Cancel error for {oid}: {e}")
        cancelled.append({"order_id": oid, "symbol": o["symbol"]})
        OPEN_ORDERS.pop(oid, None)
    save_open_orders(OPEN_ORDERS)
    return cancelled


def mark_order_filled(order_id: str) -> Optional[Dict]:
    """Remove an order from tracking once it has filled. Returns the order data."""
    result = OPEN_ORDERS.pop(order_id, None)
    save_open_orders(OPEN_ORDERS)
    return result


def get_open_orders() -> list:
    """Return a snapshot of currently tracked open orders."""
    res = []
    now = time.time()
    for oid, o in OPEN_ORDERS.items():
        placed = o.get("placed_at") or o.get("created_at") or 0
        if isinstance(placed, str):
            try:
                from datetime import datetime, timezone
                placed = datetime.fromisoformat(placed.replace("Z", "+00:00")).timestamp()
            except Exception:
                placed = now
        age = max(0.0, now - float(placed)) if placed else 0.0
        res.append({"order_id": oid, **o, "age_seconds": round(age, 1)})
    return res


def has_open_position(symbol: str) -> bool:
    """
    Return True if there is any tracked order/position for this symbol.
    Used by the trading loop to enforce: one open position per symbol at a time.
    """
    for o in OPEN_ORDERS.values():
        if o.get("symbol") == symbol:
            return True
    return False


def get_open_position(symbol: str) -> list:
    """Return all tracked orders for the given symbol."""
    return [
        {"order_id": oid, **o}
        for oid, o in OPEN_ORDERS.items()
        if o.get("symbol") == symbol
    ]


def sync_open_orders() -> dict:
    """
    Poll Binance for the status of every tracked LIVE order.
    PAPER positions are NOT touched here — they're managed by check_paper_sl_tp().

    - FILLED orders are removed from OPEN_ORDERS (position is closed).
    - CANCELED / REJECTED / EXPIRED orders are removed.
    - Still NEW / PARTIALLY_FILLED stay tracked.

    Returns a small stats dict {filled: N, removed: N, kept: N}.
    """
    client = _get_client()
    if client is None:
        # Paper mode: all positions live until SL/TP. Just count, don't remove.
        kept = sum(1 for o in OPEN_ORDERS.values() if o.get("mode") == "PAPER")
        return {"filled": 0, "removed": 0, "kept": kept}

    filled = 0
    removed = 0
    kept = 0
    to_remove = []

    for oid, o in list(OPEN_ORDERS.items()):
        if o.get("mode") == "PAPER":
            kept += 1  # leave paper positions alone
            continue
        symbol = o.get("symbol")
        try:
            order = client.get_order(symbol=symbol, orderId=oid)
            status = order.get("status", "")
            if status == "FILLED":
                # Order filled — position is now open in the wallet.
                # The next position check will see real Binance balance, not OPEN_ORDERS.
                to_remove.append(oid)
                filled += 1
            elif status in ("CANCELED", "REJECTED", "EXPIRED"):
                to_remove.append(oid)
                removed += 1
            else:
                # NEW / PARTIALLY_FILLED — still working
                kept += 1
        except Exception as e:
            # Order may have been deleted server-side (e.g., after testnet reset).
            # Treat as removed to avoid blocking the symbol forever.
            print(f"[EXEC] sync error for {oid}: {e}")
            to_remove.append(oid)
            removed += 1

    for oid in to_remove:
        OPEN_ORDERS.pop(oid, None)

    return {"filled": filled, "removed": removed, "kept": kept}
