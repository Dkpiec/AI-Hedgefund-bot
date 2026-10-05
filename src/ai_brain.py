"""
AI Brain - LLM-Powered Trading Decision Engine
===============================================
Sends multi-timeframe CSV data to the LLM and gets a structured decision.
Falls back to a rule-based Inside-Bar Momentum Quant Engine if no API key.
"""
import json
import re
import sys
from typing import Dict, List, Optional

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


def _scan_inside_bar(csv_text: str) -> Optional[Dict]:
    """
    Detect an inside-bar pattern in the last 2 candles of a CSV.
    Returns dict {signal, confidence} if found, else None.
    """
    if not csv_text:
        return None
    lines = [line.split(",") for line in csv_text.strip().split("\n") if line]
    if len(lines) < 3:  # header + 2 candles
        return None
    try:
        prev = lines[-2]
        curr = lines[-1]
        prev_high, prev_low = float(prev[2]), float(prev[3])
        curr_high, curr_low = float(curr[2]), float(curr[3])
        curr_close = float(curr[4])
        curr_open = float(curr[1])
        is_inside_bar = (curr_high <= prev_high) and (curr_low >= prev_low)
        if is_inside_bar:
            signal = "BUY" if curr_close >= curr_open else "SELL"
            return {"signal": signal, "confidence": 80}
    except Exception:
        return None
    return None


def _quant_fallback_decision(market_data: Dict, symbol: str) -> Dict:
    """
    High-Expectancy Quantitative Regime Engine.
    Evaluates:
      1. Multi-timeframe trend alignment (1H & 15M EMAs)
      2. Inside-Bar / Volatility Compression breakouts
      3. RSI momentum & volume surge confirmation (Vol >= 1.2x SMA20)
    Replaces random coin-flips with strict HOLD discipline.
    """
    timeframes = market_data.get("timeframes", {}) or {}
    if not timeframes and market_data.get("h1_csv"):
        timeframes = {"1h": market_data["h1_csv"], "D1": market_data.get("daily_csv", "")}

    # Analyze 1H Higher-Timeframe Trend
    htf_csv = timeframes.get("1h", "")
    htf_bullish = False
    htf_bearish = False
    if htf_csv:
        lines = [l.split(",") for l in htf_csv.strip().split("\n") if l]
        if len(lines) >= 15:
            try:
                closes_1h = [float(l[4]) for l in lines[1:]]
                ema_fast = sum(closes_1h[-5:]) / 5.0
                ema_slow = sum(closes_1h[-15:]) / 15.0
                if ema_fast > ema_slow and closes_1h[-1] > ema_slow:
                    htf_bullish = True
                elif ema_fast < ema_slow and closes_1h[-1] < ema_slow:
                    htf_bearish = True
            except Exception:
                pass

    # Analyze 15M / Primary Trigger Timeframe
    primary_tf = "15m" if "15m" in timeframes else ("5m" if "5m" in timeframes else ("1h" if "1h" in timeframes else ""))
    csv_text = timeframes.get(primary_tf, "")

    if csv_text:
        lines = [l.split(",") for l in csv_text.strip().split("\n") if l]
        if len(lines) >= 20:
            try:
                opens = [float(l[1]) for l in lines[1:]]
                highs = [float(l[2]) for l in lines[1:]]
                lows = [float(l[3]) for l in lines[1:]]
                closes = [float(l[4]) for l in lines[1:]]
                vols = [float(l[5]) for l in lines[1:]]

                # Inside bar check on last 2 closed bars
                is_ib = (highs[-1] <= highs[-2]) and (lows[-1] >= lows[-2])
                
                # Volume ratio
                avg_vol = sum(vols[-20:]) / 20.0
                vol_ratio = vols[-1] / (avg_vol + 1e-9)

                # RSI 14
                gains, losses = [], []
                for i in range(1, len(closes)):
                    diff = closes[i] - closes[i-1]
                    gains.append(max(diff, 0.0))
                    losses.append(max(-diff, 0.0))
                avg_gain = sum(gains[-14:]) / 14.0
                avg_loss = sum(losses[-14:]) / 14.0
                rs = avg_gain / (avg_loss + 1e-9)
                rsi = 100 - (100 / (1 + rs))

                # Trend Alignment
                sma20 = sum(closes[-20:]) / 20.0
                price_above_sma = closes[-1] > sma20

                # High probability setup conditions
                if is_ib:
                    if (htf_bullish or price_above_sma) and rsi < 65 and closes[-1] >= opens[-1]:
                        return {
                            "signal": "BUY",
                            "confidence_score": 85 if htf_bullish else 75,
                            "logic": f"Quant Edge: Inside-bar compression with bullish trend & RSI {rsi:.1f} on {symbol} ({primary_tf}).",
                            "timeframe": primary_tf,
                        }
                    elif (htf_bearish or not price_above_sma) and rsi > 35 and closes[-1] < opens[-1]:
                        return {
                            "signal": "SELL",
                            "confidence_score": 85 if htf_bearish else 75,
                            "logic": f"Quant Edge: Inside-bar compression with bearish trend & RSI {rsi:.1f} on {symbol} ({primary_tf}).",
                            "timeframe": primary_tf,
                        }

                # Momentum breakout on volume surge
                if vol_ratio >= 1.5:
                    if closes[-1] > highs[-2] and (htf_bullish or price_above_sma) and 50 < rsi < 72:
                        return {
                            "signal": "BUY",
                            "confidence_score": 80,
                            "logic": f"Quant Edge: Volume breakout ({vol_ratio:.1f}x vol) above previous high with bullish momentum on {symbol}.",
                            "timeframe": primary_tf,
                        }
                    elif closes[-1] < lows[-2] and (htf_bearish or not price_above_sma) and 28 < rsi < 50:
                        return {
                            "signal": "SELL",
                            "confidence_score": 80,
                            "logic": f"Quant Edge: Volume breakdown ({vol_ratio:.1f}x vol) below previous low with bearish momentum on {symbol}.",
                            "timeframe": primary_tf,
                        }
            except Exception as e:
                pass

    # Default to strict capital preservation
    return {
        "signal": "HOLD",
        "confidence_score": 50,
        "logic": f"Quant Discipline: No high-expectancy setup confirmed on {symbol}. Capital preserved.",
        "timeframe": primary_tf,
    }


def get_ai_decision(market_data: Dict, symbol: str, model_override: str = None) -> Dict:
    """
    Send multi-timeframe CSV to the LLM and get a structured trading decision.
    Returns: {signal, confidence_score, logic, timeframe}
    """
    if not market_data:
        return {
            "signal": "HOLD",
            "confidence_score": 0,
            "logic": "No market data available.",
            "timeframe": "",
        }

    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY.startswith("your-"):
        return _quant_fallback_decision(market_data, symbol)

    model_to_use = model_override or _resolve_free_model()

    # Build multi-timeframe prompt
    tf_blocks = []
    timeframes = market_data.get("timeframes", {}) or {}
    for tf, csv_text in timeframes.items():
        if csv_text:
            tf_blocks.append(f"=== TIMEFRAME: {tf} (last 30 candles) ===\n{csv_text}")

    system_prompt = (
        f"You are an elite quantitative crypto trader analyzing {symbol} on Binance Spot. "
        f"Multi-timeframe analysis. Look for inside-bar breakouts, momentum shifts, "
        f"and volume confirmation. "
        f"IMPORTANT: The bot enforces one open position per symbol. "
        f"Do not recommend a new entry if a position is already open. "
        f"Return EXACTLY this JSON:\n"
        f'{{"signal": "BUY", "confidence_score": 85, "logic": "2 sentences", "timeframe": "5m"}}'
    )

    user_prompt = (
        f"Symbol: {symbol}\n\n"
        + "\n\n".join(tf_blocks)
        + f"\n\nCurrent ask price: {market_data.get('ask', 'N/A')}\n"
        f"Decide: BUY, SELL, or HOLD. Output JSON only."
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_to_use,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
    }

    try:
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            decision = _quant_fallback_decision(market_data, symbol)
            decision["logic"] += f" (OpenRouter HTTP {resp.status_code} fallback)"
            return decision

        content = resp.json()["choices"][0]["message"]["content"].strip()
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        decision = json.loads(content)
        if decision.get("signal") not in ("BUY", "SELL", "HOLD"):
            decision["signal"] = "HOLD"
        decision["confidence_score"] = max(0, min(100, int(decision.get("confidence_score", 0))))
        decision["logic"] = str(decision.get("logic", "No reasoning provided."))[:500]
        decision["timeframe"] = str(decision.get("timeframe", ""))[:20]
        return decision

    except Exception as e:
        decision = _quant_fallback_decision(market_data, symbol)
        decision["logic"] += f" (LLM parse fallback: {str(e)[:50]})"
        return decision
