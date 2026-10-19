"""Multi-Asset Spot and Contract Quote Fetcher with in-memory caching."""

import time
import json
import urllib.request
from typing import Dict, Any, Optional

_CACHE: Dict[str, tuple[float, float]] = {}  # symbol -> (price, timestamp)
CACHE_TTL_SEC = 5.0

TICKER_MAP = {
    "NIFTY": "%5ENSEI",
    "BANKNIFTY": "%5ENSEBANK",
    "SENSEX": "%5EBSESN",
    "CRUDE": "CL=F",
    "NATGAS": "NG=F",
    "GOLD": "GC=F",
    "USDINR": "USDINR=X",
    "EURINR": "EURINR=X",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}


def get_spot_price(symbol: str) -> Optional[float]:
    """Fetch spot price from Yahoo Finance with TTL caching."""
    sym_upper = symbol.upper()
    now = time.time()
    if sym_upper in _CACHE:
        val, ts = _CACHE[sym_upper]
        if (now - ts) < CACHE_TTL_SEC:
            return val

    ticker = TICKER_MAP.get(sym_upper, sym_upper)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        result = data["chart"]["result"][0]
        meta = result.get("meta", {})
        price = float(meta.get("regularMarketPrice", 0.0))
        if price > 0:
            _CACHE[sym_upper] = (price, now)
            return price
    except Exception:
        pass

    # Return cached value if available on error
    if sym_upper in _CACHE:
        return _CACHE[sym_upper][0]
    return None
