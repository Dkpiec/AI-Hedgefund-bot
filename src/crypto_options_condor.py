"""
0-DTE Trend-Skewed Iron Condor Strategy for Crypto Options (BTC/ETH)
====================================================================
Session Time: 00:00 UTC entry -> 08:00 UTC settlement (Asian Session)
Capital Allocation: $200 initial capital, 15% risk per daily session
Friction: Binance Options 0.02% Maker (short entry) / 0.03% Taker (long wing / exits)
"""
import math
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, S - K)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(S * norm_cdf(d1) - K * np.exp(-r * T) * norm_cdf(d2))

def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, K - S)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(K * np.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1))

def evaluate_condor_setup(df_1h: pd.DataFrame, symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
    """
    Evaluates current candle data to construct 0-DTE Trend-Skewed Iron Condor.
    Requires at least 50 bars of 1h data.
    """
    if len(df_1h) < 50:
        return None
        
    df = df_1h.copy()
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
        
    # Realized volatility (24h)
    log_ret = np.log(df['close'] / df['close'].shift(1))
    rv_24h = float(log_ret.tail(24).std() * np.sqrt(365 * 24))
    iv = min(1.50, max(0.30, rv_24h * 1.20))
    
    # IV Regime Filter: 35% to 85%
    if iv > 0.85 or iv < 0.35:
        return None
        
    ema50 = float(df['close'].ewm(span=50).mean().iloc[-1])
    cur_price = float(df['close'].iloc[-1])
    is_bullish = cur_price > ema50
    
    # 8 hours to daily settlement (08:00 UTC)
    T0 = 8.0 / (365.0 * 24.0)
    r = 0.04
    daily_sd = cur_price * (iv * np.sqrt(T0))
    
    # Skew strikes based on trend direction
    if is_bullish:
        call_mult, put_mult = 2.0, 1.6
    else:
        call_mult, put_mult = 1.6, 2.0
        
    step = 100 if symbol.startswith("BTC") else 10
    K_sc = float(round(cur_price + call_mult * daily_sd, -2 if symbol.startswith("BTC") else -1))
    K_lc = float(K_sc + 2 * step)
    K_sp = float(round(cur_price - put_mult * daily_sd, -2 if symbol.startswith("BTC") else -1))
    K_lp = float(K_sp - 2 * step)
    
    sc_p = float(bs_call_price(cur_price, K_sc, T0, r, iv))
    lc_p = float(bs_call_price(cur_price, K_lc, T0, r, iv))
    sp_p = float(bs_put_price(cur_price, K_sp, T0, r, iv))
    lp_p = float(bs_put_price(cur_price, K_lp, T0, r, iv))
    
    net_credit = float((sc_p - lc_p) + (sp_p - lp_p))
    max_wing_risk = float((2 * step) - net_credit)
    
    if net_credit <= 0 or max_wing_risk <= 0:
        return None
        
    return {
        "symbol": symbol,
        "underlying_price": cur_price,
        "iv": iv,
        "is_bullish": is_bullish,
        "strikes": {
            "short_call": K_sc,
            "long_call": K_lc,
            "short_put": K_sp,
            "long_put": K_lp,
        },
        "prices": {
            "short_call": sc_p,
            "long_call": lc_p,
            "short_put": sp_p,
            "long_put": lp_p,
        },
        "net_credit_per_unit": round(net_credit, 4),
        "max_wing_risk_per_unit": round(max_wing_risk, 4),
        "tp_credit_target": round(0.65 * net_credit, 4),
        "sl_credit_threshold": round(-0.70 * net_credit, 4),
    }
