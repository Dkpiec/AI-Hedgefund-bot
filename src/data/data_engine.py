"""
Data Engine - Multi-Timeframe Binance Data Fetcher
==================================================
Fetches Daily/H1/4H candles from Binance Spot (or Testnet).
Filters universe by price cap and 24h volume at startup.
Falls back to deterministic paper data if Binance is unreachable.
"""
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.append('..')
from config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    BINANCE_TESTNET,
    CANDIDATE_SYMBOLS,
    MAX_PRICE_USD,
    MIN_24H_VOLUME_USDT,
    STARTING_BALANCE,
)

# Lazy import — binance package is heavy
_client = None
_public_client = None


def _get_client():
    """Lazy-initialize signed Binance client (testnet or mainnet). Requires API key."""
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
        print(f"[DATA] Signed Binance client init failed: {e}")
        return None


def _get_public_client():
    """Lazy-initialize public Binance client (NO key needed).
    Used for real forward testing: fetch live prices + klines from binance.com
    without authentication. Always returns a client if the package imports.
    """
    global _public_client
    if _public_client is not None:
        return _public_client
    try:
        from binance.client import Client
        _public_client = Client("", "")  # empty creds = public endpoints only
        return _public_client
    except Exception as e:
        print(f"[DATA] Public Binance client init failed: {e}")
        return None


def get_live_ticker(symbol: str) -> Optional[float]:
    """Fetch current price for a symbol from Binance public API. Returns None on failure."""
    client = _get_public_client()
    if client is None:
        return None
    try:
        t = client.get_symbol_ticker(symbol=symbol)
        return float(t.get("price", 0) or 0)
    except Exception as e:
        print(f"[DATA] Ticker error for {symbol}: {e}")
        return None


def _klines_to_csv(klines, interval_label: str) -> str:
    """Convert raw klines list to a CSV string the LLM can read."""
    if not klines:
        return ""
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df["time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df[["time", "open", "high", "low", "close", "volume"]]
    return df.to_csv(index=False)


def filter_universe() -> List[str]:
    """
    Filter CANDIDATE_SYMBOLS by current price and 24h volume.
    Sets the module-level SYMBOLS list. Returns it.
    Called once at startup.
    """
    import config as cfg
    client = _get_client()
    if client is None:
        # No API key yet — use all candidates (filter at first scan)
        cfg.SYMBOLS = list(CANDIDATE_SYMBOLS)
        return cfg.SYMBOLS

    qualified = []
    for symbol in CANDIDATE_SYMBOLS:
        try:
            ticker = client.get_ticker(symbol=symbol)
            price = float(ticker.get("lastPrice", 0) or 0)
            quote_vol = float(ticker.get("quoteVolume", 0) or 0)

            if price <= 0 or quote_vol <= 0:
                continue
            if price > MAX_PRICE_USD:
                continue
            if quote_vol < MIN_24H_VOLUME_USDT:
                continue
            qualified.append(symbol)
        except Exception as e:
            print(f"[DATA] Filter error for {symbol}: {e}")
            continue

    if not qualified:
        # All candidates were filtered out (price/volume rules). Return empty
        # rather than falling back to unfiltered CANDIDATE_SYMBOLS — doing so
        # would let BTC/ETH/BNB through despite the MAX_PRICE_USD rule.
        # The trading loop handles an empty universe gracefully (no trades fired).
        print(f"[DATA] filter_universe: all {len(CANDIDATE_SYMBOLS)} candidates filtered out "
              f"(MAX_PRICE_USD=${MAX_PRICE_USD}, MIN_VOL=${MIN_24H_VOLUME_USDT:,.0f}). "
              "Returning empty universe — no trades will fire this cycle.")
        cfg.SYMBOLS = []
        return []

    cfg.SYMBOLS = qualified
    return qualified


def fetch_multi_timeframe_data(symbol: str, timeframes: Optional[List[str]] = None) -> Optional[Dict]:
    """
    Fetch multiple timeframes for the given symbol.
    Uses the public Binance client (no key needed) for forward testing.
    Falls back to signed client if available, then to synthetic data.

    Returns dict: {symbol, timeframes: {tf: csv_string}, ask, paper_mode}
    """
    timeframes = timeframes or ["1h"]
    # Prefer public client (no key, no account needed) for real forward testing
    client = _get_public_client() or _get_client()

    if client is None:
        return _generate_paper_data(symbol, timeframes)

    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        ask = float(ticker.get("price", 0) or 0)
        if ask <= 0:
            raise ValueError(f"Non-positive ask for {symbol}")
    except Exception as e:
        print(f"[DATA] Ticker error for {symbol}: {e}")
        return _generate_paper_data(symbol, timeframes)

    tf_data = {}
    for tf in timeframes:
        try:
            # Binance klines API limit is 1000 per call
            klines = client.get_klines(
                symbol=symbol,
                interval=tf,
                limit=100,
            )
            tf_data[tf] = _klines_to_csv(klines, tf)
            time.sleep(0.05)  # gentle rate limit
        except Exception as e:
            print(f"[DATA] Klines error for {symbol} {tf}: {e}")
            tf_data[tf] = ""

    return {
        "symbol": symbol,
        "timeframes": tf_data,
        "ask": ask,
        "paper_mode": False,
    }


def _generate_paper_data(symbol: str, timeframes: List[str]) -> Dict:
    """
    Deterministic paper data when Binance is unreachable.
    Generates synthetic candles anchored to a base price per symbol.
    """
    base_prices = {
        "SOLUSDT": 150.0,
        "XRPUSDT": 0.55,
        "ADAUSDT": 0.45,
        "DOGEUSDT": 0.15,
        "AVAXUSDT": 35.0,
        "LINKUSDT": 18.0,
        "DOTUSDT": 7.0,
        "MATICUSDT": 0.65,
        "LTCUSDT": 90.0,
        "NEARUSDT": 5.5,
        "ATOMUSDT": 9.0,
        "ALGOUSDT": 0.20,
        "XLMUSDT": 0.12,
        "VETUSDT": 0.03,
        "ICPUSDT": 12.0,
    }
    base = base_prices.get(symbol, 100.0)

    tf_data = {}
    now_ms = int(time.time() * 1000)
    for tf in timeframes:
        n_candles = 100
        tf_minutes = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}.get(tf, 60)
        rows = []
        price = base
        for i in range(n_candles, 0, -1):
            ts = now_ms - i * tf_minutes * 60 * 1000
            chg = float(np.random.normal(0, 0.005)) * price
            close = max(0.01, price + chg)
            high = max(price, close) * (1 + abs(float(np.random.normal(0, 0.002))))
            low = min(price, close) * (1 - abs(float(np.random.normal(0, 0.002))))
            vol = int(np.random.uniform(1000, 50000))
            rows.append({
                "open_time": ts,
                "open": round(price, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": vol,
            })
            price = close
        tf_data[tf] = _klines_to_csv(rows, tf)

    return {
        "symbol": symbol,
        "timeframes": tf_data,
        "ask": round(base, 6),
        "paper_mode": True,
    }


def get_account_info() -> Dict:
    """Return current Binance account info (free USDT, balances, etc.)."""
    client = _get_client()
    if client is None:
        return {
            "equity": STARTING_BALANCE,
            "balance": STARTING_BALANCE,
            "free": STARTING_BALANCE,
        }
    try:
        info = client.get_account()
        # Find USDT free balance
        free = 0.0
        for b in info.get("balances", []):
            if b.get("asset") == "USDT":
                free = float(b.get("free", 0) or 0)
                break
        return {
            "equity": free,
            "balance": free,
            "free": free,
        }
    except Exception as e:
        print(f"[DATA] Account info error: {e}")
        return {"equity": STARTING_BALANCE, "balance": STARTING_BALANCE, "free": STARTING_BALANCE}


def get_symbol_info(symbol: str) -> Dict:
    """Return LOT_SIZE filter info for a symbol: minQty, stepSize, minNotional."""
    client = _get_client()
    if client is None:
        return {"minQty": 0.001, "stepSize": 0.001, "minNotional": 10.0, "priceFilter": 0.0001}
    try:
        info = client.get_symbol_info(symbol)
        if not info:
            return {}
        filters = {f["filterType"]: f for f in info.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        price_filter = filters.get("PRICE_FILTER", {})
        return {
            "minQty": float(lot.get("minQty", 0) or 0),
            "stepSize": float(lot.get("stepSize", 0) or 0),
            "minNotional": float(notional.get("minNotional", notional.get("notional", 0)) or 0),
            "tickSize": float(price_filter.get("tickSize", 0) or 0),
        }
    except Exception as e:
        print(f"[DATA] Symbol info error for {symbol}: {e}")
        return {}
