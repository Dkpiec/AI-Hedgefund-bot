"""
AI Brain - High-Expectancy Quantitative Decision Engine
========================================================
Implements V2 (48H VCP Breakout Pro with Macro BTC & Adaptive Gating).
Also supports LLM execution via OpenRouter when available.
"""
import json
import re
import sys
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
import requests

sys.path.append('.')
from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
)


def _resolve_free_model() -> str:
    """If OPENROUTER_MODEL is 'openrouter/free', fetch the first free model from /models."""
    if OPENROUTER_MODEL != "openrouter/free":
        return OPENROUTER_MODEL
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY.startswith("your-"):
        return "openrouter/free"
    try:
        resp = requests.get(
            f"{OPENROUTER_BASE_URL}/models",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            timeout=10,
        )
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            for m in models:
                pricing = m.get("pricing", {})
                if pricing.get("prompt") == "0" and pricing.get("completion") == "0":
                    return m.get("id", "")
    except Exception:
        pass
    return "meta-llama/llama-3.3-70b-instruct:free"


def scan_vcp_breakout_v2(market_data: Dict, symbol: str) -> Dict:
    """
    48H VCP Breakout Pro V2 Strategy:
    1. Macro Filter: BTC 1H Close > 50 EMA (when BTC data available)
    2. Range Contraction: 12h range < 70% of 48h range
    3. Breakout: Close > 48h High & Volume >= 2.0x SMA20(Vol) & Close in top 25% of bar
    4. Structural Brackets: SL at Base Low (capped at 2.5%), TP1 = +2.0R, TP2 = +4.0R
    """
    timeframes = market_data.get("timeframes", {}) or {}
    if not timeframes and market_data.get("h1_csv"):
        timeframes = {"1h": market_data["h1_csv"], "D1": market_data.get("daily_csv", "")}

    primary_tf = "1h" if "1h" in timeframes else ("15m" if "15m" in timeframes else "")
    csv_text = timeframes.get(primary_tf, "")

    if not csv_text:
        return {
            "signal": "HOLD",
            "confidence_score": 0,
            "logic": f"No {primary_tf} candle data available for {symbol}.",
            "timeframe": primary_tf,
        }

    lines = [l.split(",") for l in csv_text.strip().split("\n") if l]
    if len(lines) < 55:  # Need at least 48 bars + warmup
        return {
            "signal": "HOLD",
            "confidence_score": 0,
            "logic": f"Insufficient {primary_tf} history ({len(lines)-1} bars < 55 required).",
            "timeframe": primary_tf,
        }

    try:
        opens = [float(l[1]) for l in lines[1:]]
        highs = [float(l[2]) for l in lines[1:]]
        lows = [float(l[3]) for l in lines[1:]]
        closes = [float(l[4]) for l in lines[1:]]
        vols = [float(l[5]) for l in lines[1:]]

        # 1. Macro BTC check if present in market_data
        btc_csv = market_data.get("btc_1h_csv", "")
        if btc_csv:
            btc_lines = [l.split(",") for l in btc_csv.strip().split("\n") if l]
            if len(btc_lines) >= 50:
                btc_closes = [float(l[4]) for l in btc_lines[1:]]
                btc_ema50 = pd.Series(btc_closes).ewm(span=50).mean().iloc[-1]
                if btc_closes[-1] < btc_ema50:
                    return {
                        "signal": "HOLD",
                        "confidence_score": 40,
                        "logic": f"Macro BTC Downtrend (BTC ${btc_closes[-1]:.0f} < 50 EMA ${btc_ema50:.0f}). Altcoin breakouts gated.",
                        "timeframe": primary_tf,
                    }

        # 2. VCP Contraction: 12-bar range < 70% of 48-bar range
        high_48 = max(highs[-49:-1])
        low_48 = min(lows[-49:-1])
        range_48 = high_48 - low_48
        if range_48 <= 0:
            return {"signal": "HOLD", "confidence_score": 30, "logic": "Zero 48H range.", "timeframe": primary_tf}

        high_12 = max(highs[-13:-1])
        low_12 = min(lows[-13:-1])
        range_12 = high_12 - low_12
        if range_12 > 0.70 * range_48:
            return {
                "signal": "HOLD",
                "confidence_score": 45,
                "logic": f"VCP Gated: 12h range ({range_12/range_48*100:.1f}%) not contracted under 70% threshold.",
                "timeframe": primary_tf,
            }

        # 3. Volume & Breakout Confirmation
        cur_close = closes[-1]
        cur_high = highs[-1]
        cur_low = lows[-1]
        cur_vol = vols[-1]
        avg_vol_20 = sum(vols[-21:-1]) / 20.0
        bar_range = cur_high - cur_low

        if bar_range <= 0 or avg_vol_20 <= 0:
            return {"signal": "HOLD", "confidence_score": 30, "logic": "Illiquid/Flat bar.", "timeframe": primary_tf}

        vol_ratio = cur_vol / avg_vol_20
        close_pos_in_bar = (cur_close - cur_low) / bar_range

        # Trigger Condition: Break 48h High + 2.0x Vol + Top 25% Close
        if cur_close > high_48 and vol_ratio >= 2.0 and close_pos_in_bar >= 0.75:
            base_low = low_12
            sl = max(base_low, cur_close * 0.975)  # Cap initial risk at 2.5% max
            risk = cur_close - sl
            if risk <= 0:
                return {"signal": "HOLD", "confidence_score": 30, "logic": "Invalid risk bracket.", "timeframe": primary_tf}

            tp1 = cur_close + 2.0 * risk
            tp2 = cur_close + 4.0 * risk

            return {
                "signal": "BUY",
                "confidence_score": 90,
                "sl": round(sl, 6),
                "tp1": round(tp1, 6),
                "tp2": round(tp2, 6),
                "risk": round(risk, 6),
                "logic": (
                    f"V2 VCP Breakout: {symbol} broke 48h High (${high_48:.4f}) with {vol_ratio:.1f}x volume expansion "
                    f"and top-tier close ({close_pos_in_bar*100:.0f}%). SL=${sl:.4f} (-{risk/cur_close*100:.2f}%), TP1=${tp1:.4f}, TP2=${tp2:.4f}."
                ),
                "timeframe": primary_tf,
            }

    except Exception as e:
        return {
            "signal": "HOLD",
            "confidence_score": 20,
            "logic": f"V2 Engine error analyzing {symbol}: {str(e)[:100]}",
            "timeframe": primary_tf,
        }

    return {
        "signal": "HOLD",
        "confidence_score": 50,
        "logic": f"Quant Discipline: No VCP Breakout setup on {symbol}. Capital preserved.",
        "timeframe": primary_tf,
    }


def get_ai_decision(market_data: Dict, symbol: str, model_override: str = None) -> Dict:
    """
    Primary Entry Point for Trading Decisions.
    Uses the deterministic V2 quantitative engine directly.
    """
    if not market_data:
        return {
            "signal": "HOLD",
            "confidence_score": 0,
            "logic": "No market data available.",
            "timeframe": "",
        }

    return scan_vcp_breakout_v2(market_data, symbol)
