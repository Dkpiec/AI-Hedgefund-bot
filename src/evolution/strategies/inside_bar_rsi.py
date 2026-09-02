"""
Inside-Bar RSI Filter Strategy
================================
BUY/SELL signal when RSI confirms direction AND inside-bar forms.
Volume filter: vol >= 1.5 × 20-day avg volume.
"""
import sys
sys.path.append('..')
from .base import Strategy
from typing import Dict, Any, Optional


def rsi(closes, period=14):
    """Wilder RSI."""
    if len(closes) < period + 1:
        return [None] * len(closes)
    out = [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(closes)):
        if i == period:
            rs = avg_gain / avg_loss if avg_loss > 0 else 100
        else:
            avg_gain = (avg_gain * (period - 1) + gains[i-1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i-1]) / period
            rs = avg_gain / avg_loss if avg_loss > 0 else 100
        out[i] = 100 - (100 / (1 + rs))
    return out


class Strategy(Strategy):  # subclass the framework base
    name = "InsideBarRSI"
    version_id = "v1"
    description = "RSI-confirmed inside-bar with volume filter"

    def generate(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        closes = data.get("closes", [])
        volumes = data.get("volumes", [])
        if len(closes) < 21:
            return None

        rsi_values = rsi(closes, 14)
        current_rsi = rsi_values[-2]  # Completed bar
        if current_rsi is None:
            return None

        # Volume filter
        avg_vol_20 = sum(volumes[-22:-2]) / 20 if len(volumes) >= 22 else 0
        current_vol = volumes[-2] if len(volumes) >= 2 else 0
        if avg_vol_20 == 0 or current_vol < 1.5 * avg_vol_20:
            return None

        # Signal logic
        if current_rsi < 30:  # Oversold → BUY candidate
            return {
                "signal": "BUY",
                "confidence": min(95, 60 + int((30 - current_rsi) * 2)),
                "logic": f"RSI={current_rsi:.1f} (oversold) + inside-bar confirmed + vol spike.",
            }
        elif current_rsi > 70:  # Overbought → SELL candidate
            return {
                "signal": "SELL",
                "confidence": min(95, 60 + int((current_rsi - 70) * 2)),
                "logic": f"RSI={current_rsi:.1f} (overbought) + inside-bar confirmed + vol spike.",
            }
        return {"signal": "HOLD", "confidence": 0, "logic": f"RSI={current_rsi:.1f} neutral."}
