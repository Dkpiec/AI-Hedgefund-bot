"""
Execution Engine - Binance Spot Limit Order Executor
=====================================================
Places STRICT LIMIT orders only (no market orders).
Auto-cancels unfilled limit orders after ORDER_TIMEOUT_SECONDS.
Enforces 5% risk-per-trade and LOT_SIZE/price filters.
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
    RISK_PER_TRADE,
    SL_PERCENT,
    STRICT_LIMIT_ORDERS,
    TP_PERCENT,
)
from data.data_engine import get_account_info, get_symbol_info

# In-memory tracking of open orders: {order_id: {symbol, side, qty, price, placed_at, sl, tp}}
OPEN_ORDERS: Dict[str, Dict] = {}

_client = None


def _get_client():
    """Lazy-initialize Binance client."""
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
    """Return a paper-mode success when no API key is set."""
    limit_price = ask_price
    if signal == "BUY":
        sl = ask_price * (1 - SL_PERCENT)
        tp = ask_price * (1 + TP_PERCENT)
    else:
        sl = ask_price * (1 + SL_PERCENT)
        tp = ask_price * (1 - TP_PERCENT)
    return {
        "success": True,
        "mode": "PAPER",
        "symbol": symbol,
        "signal": signal,
        "price": limit_price,
        "qty": 0.0,
        "sl": sl,
        "tp": tp,
    }


def cancel_expired_orders() -> list:
    """
    Cancel any tracked orders that have exceeded ORDER_TIMEOUT_SECONDS.
    Returns list of cancelled order ids.
    """
    client = _get_client()
    now = time.time()
    cancelled = []
    expired_ids = [
        oid for oid, o in OPEN_ORDERS.items()
        if (now - o.get("placed_at", 0)) > ORDER_TIMEOUT_SECONDS
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
    return cancelled


def mark_order_filled(order_id: str) -> Optional[Dict]:
    """Remove an order from tracking once it has filled. Returns the order data."""
    return OPEN_ORDERS.pop(order_id, None)


def get_open_orders() -> list:
    """Return a snapshot of currently tracked open orders."""
    return [
        {"order_id": oid, **o, "age_seconds": time.time() - o.get("placed_at", 0)}
        for oid, o in OPEN_ORDERS.items()
    ]
