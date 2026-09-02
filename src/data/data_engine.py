"""
Data Engine - Multi-Timeframe MT5 Data Fetcher
==============================================
Fetches macro (Daily) and micro (H1) candles from MT5 demo account
and returns raw CSV text for the AI brain.
"""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
import sys
sys.path.append('..')
from config import MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER


def initialize_mt5() -> bool:
    """Initialize MT5 connection with demo account credentials."""
    if not mt5.initialize():
        print(f"[MT5] Initialization failed: {mt5.last_error()}")
        return False
    authorized = mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER)
    if not authorized:
        print(f"[MT5] Login failed: {mt5.last_error()}")
        return False
    print(f"[MT5] Connected to {MT5_SERVER} as account {MT5_ACCOUNT}")
    return True


def fetch_multi_timeframe_data(symbol: str) -> Optional[Dict]:
    """
    Fetch Daily (10 candles) + H1 (24 candles) for the given symbol.
    Returns CSV text + account equity + current ask price.
    """
    if not mt5.initialize():
        if not initialize_mt5():
            return None

    try:
        # Daily timeframe - macro trend
        daily_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 10)
        if daily_rates is None or len(daily_rates) == 0:
            print(f"[DATA] No daily data for {symbol}")
            return None
        daily_df = pd.DataFrame(daily_rates)
        daily_df['time'] = pd.to_datetime(daily_df['time'], unit='s')
        daily_csv = daily_df[['time', 'open', 'high', 'low', 'close', 'tick_vol']].to_csv(index=False)

        # H1 timeframe - micro momentum
        h1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 24)
        if h1_rates is None or len(h1_rates) == 0:
            print(f"[DATA] No H1 data for {symbol}")
            return None
        h1_df = pd.DataFrame(h1_rates)
        h1_df['time'] = pd.to_datetime(h1_df['time'], unit='s')
        h1_csv = h1_df[['time', 'open', 'high', 'low', 'close', 'tick_vol']].to_csv(index=False)

        # Account info
        account_info = mt5.account_info()
        equity = account_info.equity if account_info else 0.0

        # Current ask price
        tick = mt5.symbol_info_tick(symbol)
        ask = tick.ask if tick else 0.0

        return {
            "symbol": symbol,
            "daily_csv": daily_csv,
            "h1_csv": h1_csv,
            "equity": equity,
            "ask": ask,
        }
    except Exception as e:
        print(f"[DATA] Error fetching {symbol}: {e}")
        return None


def get_account_info() -> Dict:
    """Return current MT5 account info."""
    if not mt5.initialize():
        initialize_mt5()
    info = mt5.account_info()
    if info is None:
        return {"equity": 0, "balance": 0, "margin": 0, "free_margin": 0}
    return {
        "equity": info.equity,
        "balance": info.balance,
        "margin": info.margin,
        "free_margin": info.margin_free,
    }
