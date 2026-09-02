"""
Data Engine - Multi-Timeframe Data Fetcher
==========================================
Fetches macro (Daily) and micro (H1) candles from MT5 demo account.
If MT5 is missing (e.g. running on Linux / Render / Streamlit Cloud),
falls back to paper data generator so the AI brain can analyze and trade.
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
import sys
sys.path.append('..')
from config import MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    MT5_AVAILABLE = False
    mt5 = None


def initialize_mt5() -> bool:
    """Initialize MT5 connection with demo account credentials."""
    if not MT5_AVAILABLE or mt5 is None:
        return False
    if not mt5.initialize():
        print(f"[MT5] Initialization failed: {mt5.last_error()}")
        return False
    authorized = mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER)
    if not authorized:
        print(f"[MT5] Login failed: {mt5.last_error()}")
        return False
    print(f"[MT5] Connected to {MT5_SERVER} as account {MT5_ACCOUNT}")
    return True


BASE_PRICES = {
    "EURUSDm": 1.0850,
    "GBPUSDm": 1.2650,
    "BTCUSDm": 65000.0,
    "XAUUSDm": 2400.0,
    "USDJPYm": 155.0,
}


def _generate_paper_data(symbol: str) -> Dict:
    """
    Generate realistic multi-timeframe candle data for paper mode.
    Simulates Daily (10 candles) + H1 (24 candles) with market structure.
    """
    base = BASE_PRICES.get(symbol, 1.0)
    now = datetime.now(timezone.utc)

    # 10 Daily candles
    daily_rows = []
    price = base
    for i in range(10, 0, -1):
        dt = now - timedelta(days=i)
        chg = float(np.random.normal(0, 0.005) * price)
        close = price + chg
        high = max(price, close) + abs(float(np.random.normal(0, 0.003) * price))
        low = min(price, close) - abs(float(np.random.normal(0, 0.003) * price))
        vol = int(np.random.uniform(1000, 5000))
        daily_rows.append({
            'time': dt.strftime('%Y-%m-%d %H:%M:%S'),
            'open': round(price, 5),
            'high': round(high, 5),
            'low': round(low, 5),
            'close': round(close, 5),
            'tick_vol': vol
        })
        price = close

    daily_df = pd.DataFrame(daily_rows)
    daily_csv = daily_df.to_csv(index=False)

    # 24 H1 candles
    h1_rows = []
    for i in range(24, 0, -1):
        dt = now - timedelta(hours=i)
        chg = float(np.random.normal(0, 0.002) * price)
        close = price + chg
        high = max(price, close) + abs(float(np.random.normal(0, 0.001) * price))
        low = min(price, close) - abs(float(np.random.normal(0, 0.001) * price))
        vol = int(np.random.uniform(100, 500))
        h1_rows.append({
            'time': dt.strftime('%Y-%m-%d %H:%M:%S'),
            'open': round(price, 5),
            'high': round(high, 5),
            'low': round(low, 5),
            'close': round(close, 5),
            'tick_vol': vol
        })
        price = close

    h1_df = pd.DataFrame(h1_rows)
    h1_csv = h1_df.to_csv(index=False)

    return {
        "symbol": symbol,
        "daily_csv": daily_csv,
        "h1_csv": h1_csv,
        "equity": 5000.0,
        "ask": round(price, 5),
        "paper_mode": True,
    }


def fetch_multi_timeframe_data(symbol: str) -> Optional[Dict]:
    """
    Fetch Daily (10 candles) + H1 (24 candles) for the given symbol.
    Uses MT5 if available; falls back to paper data generator if on Linux/Render.
    """
    if MT5_AVAILABLE and mt5 is not None:
        try:
            if not mt5.initialize():
                if not initialize_mt5():
                    return _generate_paper_data(symbol)

            daily_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 10)
            if daily_rates is not None and len(daily_rates) > 0:
                daily_df = pd.DataFrame(daily_rates)
                daily_df['time'] = pd.to_datetime(daily_df['time'], unit='s')
                daily_csv = daily_df[['time', 'open', 'high', 'low', 'close', 'tick_vol']].to_csv(index=False)

                h1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 24)
                if h1_rates is not None and len(h1_rates) > 0:
                    h1_df = pd.DataFrame(h1_rates)
                    h1_df['time'] = pd.to_datetime(h1_df['time'], unit='s')
                    h1_csv = h1_df[['time', 'open', 'high', 'low', 'close', 'tick_vol']].to_csv(index=False)

                    account_info = mt5.account_info()
                    equity = account_info.equity if account_info else 5000.0
                    tick = mt5.symbol_info_tick(symbol)
                    ask = tick.ask if tick else 0.0

                    return {
                        "symbol": symbol,
                        "daily_csv": daily_csv,
                        "h1_csv": h1_csv,
                        "equity": equity,
                        "ask": ask,
                        "paper_mode": False,
                    }
        except Exception as e:
            print(f"[DATA] MT5 fetch error for {symbol}: {e}")

    # Fallback for Linux / Render / Streamlit Cloud
    return _generate_paper_data(symbol)


def get_account_info() -> Dict:
    """Return current MT5 or paper account info."""
    if MT5_AVAILABLE and mt5 is not None:
        try:
            if not mt5.initialize():
                initialize_mt5()
            info = mt5.account_info()
            if info is not None:
                return {
                    "equity": info.equity,
                    "balance": info.balance,
                    "margin": info.margin,
                    "free_margin": info.margin_free,
                }
        except Exception:
            pass
    return {"equity": 5000.0, "balance": 5000.0, "margin": 0, "free_margin": 5000.0}
