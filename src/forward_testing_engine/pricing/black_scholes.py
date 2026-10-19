"""Container-Safe Black-Scholes Options Pricing Model.
Uses Python stdlib math without external scipy dependency to ensure 100% crash-proof
execution inside minimal Docker containers.
"""

import math
from typing import Dict, Any


def norm_cdf(x: float) -> float:
    """Standard Normal Cumulative Distribution Function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard Normal Probability Density Function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def bs_d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    """Calculate d1 and d2 parameters."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bs_call_price(S: float, K: float, T: float, r: float = 0.07, sigma: float = 0.15) -> float:
    """Theoretical European Call option price."""
    if T <= 0.0001:
        return max(0.0, S - K)
    if sigma <= 0:
        return max(0.0, S - K * math.exp(-r * T))
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    call = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return max(0.05, round(call, 2))


def bs_put_price(S: float, K: float, T: float, r: float = 0.07, sigma: float = 0.15) -> float:
    """Theoretical European Put option price."""
    if T <= 0.0001:
        return max(0.0, K - S)
    if sigma <= 0:
        return max(0.0, K * math.exp(-r * T) - S)
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    put = K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)
    return max(0.05, round(put, 2))


def bs_greeks(S: float, K: float, T: float, r: float = 0.07, sigma: float = 0.15, option_type: str = "CE") -> Dict[str, float]:
    """Compute Delta, Gamma, Theta, and Vega."""
    if T <= 0.0001 or sigma <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    gamma = norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * norm_pdf(d1) * math.sqrt(T) / 100.0

    if option_type.upper() in ["CE", "CALL"]:
        delta = norm_cdf(d1)
        theta = (- (S * norm_pdf(d1) * sigma) / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm_cdf(d2)) / 365.0
    else:
        delta = norm_cdf(d1) - 1.0
        theta = (- (S * norm_pdf(d1) * sigma) / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * norm_cdf(-d2)) / 365.0

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }
