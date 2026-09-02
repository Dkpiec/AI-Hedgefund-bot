"""
AI Brain - LLM-Powered Trading Decision Engine
===============================================
Calls OpenRouter (or DeepSeek/OpenAI) to get BUY/SELL/HOLD decisions
with confidence scores and reasoning.
"""
import requests
import json
import sys
sys.path.append('.')
from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    DEEPSEEK_API_KEY,
)


def get_ai_decision(market_data: dict, symbol: str) -> dict:
    """
    Send Daily + H1 CSV to the LLM and get a structured trading decision.
    Returns: {"signal": "BUY"|"SELL"|"HOLD", "confidence_score": 0-100, "logic": "..."}
    """
    if not market_data:
        return {"signal": "HOLD", "confidence_score": 0, "logic": "No market data available."}

    # Build the prompts
    system_prompt = (
        f"You are an elite quantitative trader analyzing {symbol}. "
        f"Analyze price action, market structure, trend, momentum, and volume. "
        f"Return your decision in EXACTLY this JSON format:\n"
        f'{{"signal": "BUY", "confidence_score": 85, "logic": "2 sentences explaining your reasoning"}}'
    )

    user_prompt = (
        f"Symbol: {symbol}\n\n"
        f"=== DAILY TIMEFRAME (Macro Trend - 10 candles) ===\n"
        f"{market_data.get('daily_csv', '')}\n\n"
        f"=== H1 TIMEFRAME (Micro Momentum - 24 candles) ===\n"
        f"{market_data.get('h1_csv', '')}\n\n"
        f"Current ask price: {market_data.get('ask', 'N/A')}\n\n"
        f"Decide: BUY, SELL, or HOLD. Output JSON only."
    )

    # OpenRouter call (supports free models)
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENROUTER_MODEL,
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
            timeout=60,
        )
        if resp.status_code != 200:
            return {
                "signal": "HOLD",
                "confidence_score": 0,
                "logic": f"API error {resp.status_code}: {resp.text[:200]}",
            }

        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Try to parse JSON from the response
        # Sometimes LLMs wrap JSON in markdown code blocks
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        decision = json.loads(content)

        # Validate
        if decision.get("signal") not in ("BUY", "SELL", "HOLD"):
            decision["signal"] = "HOLD"
        decision["confidence_score"] = max(0, min(100, int(decision.get("confidence_score", 0))))
        decision["logic"] = str(decision.get("logic", "No reasoning provided."))[:500]

        return decision

    except json.JSONDecodeError as e:
        return {
            "signal": "HOLD",
            "confidence_score": 0,
            "logic": f"LLM returned non-JSON: {content[:200]}",
        }
    except Exception as e:
        return {
            "signal": "HOLD",
            "confidence_score": 0,
            "logic": f"Request failed: {str(e)[:200]}",
        }
