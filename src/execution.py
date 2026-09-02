"""
Execution Engine - MT5 Trade Executor
======================================
Sends BUY/SELL orders to MT5 with dynamic SL/TP rounded to symbol digits.
"""
import sys
sys.path.append('.')
from config import SL_PERCENT, TP_PERCENT, LOT_SIZE, PAPER_MODE


def _mt5():
    import MetaTrader5 as mt5
    return mt5


def execute_trade(symbol: str, signal: str, ask_price: float = 0.0) -> dict:
    """
    Execute a BUY or SELL trade on MT5.
    Calculates SL/TP dynamically and rounds to correct decimal places.
    Returns trade result dict.
    """
    if signal not in ("BUY", "SELL"):
        return {"success": False, "error": f"Invalid signal: {signal}"}

    # Paper trading mode - log only
    if PAPER_MODE:
        return {
            "success": True,
            "mode": "PAPER",
            "symbol": symbol,
            "signal": signal,
            "price": ask_price,
            "sl": ask_price * (1 - SL_PERCENT) if signal == "BUY" else ask_price * (1 + SL_PERCENT),
            "tp": ask_price * (1 + TP_PERCENT) if signal == "BUY" else ask_price * (1 - TP_PERCENT),
        }

    # Live MT5 execution
    try:
        mt5 = _mt5()
    except (ImportError, ModuleNotFoundError):
        return {"success": False, "error": "MT5 not installed; enable PAPER_MODE for testing."}
    if not mt5.initialize():
        return {"success": False, "error": "MT5 not initialized"}

    # Get symbol info for digit rounding
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return {"success": False, "error": f"Symbol {symbol} not found"}
    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            return {"success": False, "error": f"Failed to select {symbol}"}

    digits = symbol_info.digits
    point = symbol_info.point

    # Current price
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"success": False, "error": f"No tick data for {symbol}"}

    if signal == "BUY":
        price = tick.ask
        sl = round(price * (1 - SL_PERCENT), digits)
        tp = round(price * (1 + TP_PERCENT), digits)
        order_type = mt5.ORDER_TYPE_BUY
    else:  # SELL
        price = tick.bid
        sl = round(price * (1 + SL_PERCENT), digits)
        tp = round(price * (1 - TP_PERCENT), digits)
        order_type = mt5.ORDER_TYPE_SELL

    # Build request
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 234000,
        "comment": "AI Hedge Fund Bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        return {"success": False, "error": f"order_send returned None: {mt5.last_error()}"}

    return {
        "success": result.retcode == mt5.TRADE_RETCODE_DONE,
        "retcode": result.retcode,
        "order_id": result.order,
        "symbol": symbol,
        "signal": signal,
        "price": result.price,
        "sl": sl,
        "tp": tp,
        "comment": result.comment,
    }
