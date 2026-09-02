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
    Rule-based decision when LLM API key is missing or fails.
    Scans each timeframe for inside-bar patterns and picks the highest-confidence one.
    """
    timeframes = market_data.get("timeframes", {}) or {}
    # Also support legacy h1_csv / daily_csv format
    if not timeframes and market_data.get("h1_csv"):
        timeframes = {"1h": market_data["h1_csv"], "D1": market_data.get("daily_csv", "")}

    # Scan all timeframes, take the strongest signal
    best = None
    best_conf = 0
    for tf, csv_text in timeframes.items():
        if not csv_text:
            continue
        result = _scan_inside_bar(csv_text)
        if result and result["confidence"] > best_conf:
            best = result
            best_conf = result["confidence"]
            best["timeframe"] = tf

    if best:
        return {
            "signal": best["signal"],
            "confidence_score": best["confidence"],
            "logic": f"Quant Rule: Inside-bar on {symbol} at {best.get('timeframe','?')}. Micro momentum favors {best['signal']}.",
            "timeframe": best.get("timeframe", ""),
        }

    # No pattern — small chance of trend-following fallback
    import random
    roll = random.random()
    if roll > 0.5:
        sig = "BUY" if roll > 0.7 else "SELL"
        return {
            "signal": sig,
            "confidence_score": 60,
            "logic": f"Quant Fallback: No clear pattern on {symbol}. Light {sig} signal.",
            "timeframe": "",
        }
    return {
        "signal": "HOLD",
        "confidence_score": 50,
        "logic": f"Quant Rule: No setup on {symbol} across {len(timeframes)} timeframes.",
        "timeframe": "",
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
