"""
Main FastAPI App - AI Hedge Fund Bot
======================================
Provides endpoints to control the bot, view status, and serves
the cyberpunk dashboard at /
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.append(str(Path(__file__).parent))
from config import (
    SYMBOLS, DEFAULT_INTERVAL, DASHBOARD_HOST, DASHBOARD_PORT, PAPER_MODE,
    OPENROUTER_MODEL, STARTING_BALANCE, TP_PERCENT, SL_PERCENT,
)
from ai_brain import get_ai_decision, _resolve_free_model
from execution import execute_trade

# Lazy import MT5 (only required when actually trading)
def _mt5():
    from data.data_engine import fetch_multi_timeframe_data, get_account_info
    return fetch_multi_timeframe_data, get_account_info

# ============================================================================
# GLOBAL STATE
# ============================================================================
bot_state = {
    "is_running": False,
    "interval": DEFAULT_INTERVAL,
    "equity": STARTING_BALANCE,
    "balance": STARTING_BALANCE,
    "starting_balance": STARTING_BALANCE,
    "pnl": 0.0,
    "pnl_pct": 0.0,
    "last_logic": "Bot idle. Click 'Initialize Engine' to start.",
    "last_confidence": 0,
    "last_signal": "HOLD",
    "last_symbol": "",
    "trade_history": [],
    "paper_mode": PAPER_MODE,
    "started_at": None,
    "cycles_completed": 0,
    "current_model": OPENROUTER_MODEL,
    "resolved_model": OPENROUTER_MODEL,
}

# ============================================================================
# FASTAPI APP
# ============================================================================
app = FastAPI(title="AI Hedge Fund Bot", version="1.0.0")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "dashboard" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "dashboard" / "templates"))


# ============================================================================
# ROUTES
# ============================================================================
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the cyberpunk dashboard."""
    return templates.TemplateResponse(request, "index.html", {"symbols": SYMBOLS})


@app.post("/api/control")
async def control(request: Request):
    """Start/stop the bot and set interval."""
    body = await request.json()
    action = body.get("action", "").lower()
    interval = body.get("interval", DEFAULT_INTERVAL)

    if action == "start":
        bot_state["is_running"] = True
        bot_state["interval"] = int(interval)
        bot_state["started_at"] = datetime.utcnow().isoformat()
        asyncio.create_task(trading_loop())
        return {"status": "started", "interval": interval}
    elif action == "stop":
        bot_state["is_running"] = False
        return {"status": "stopped"}
    return {"error": f"Unknown action: {action}"}


@app.get("/api/status")
async def status():
    """Return current bot state."""
    return bot_state


@app.post("/api/reset")
async def reset():
    """Reset trade history and balance to starting balance."""
    bot_state["trade_history"] = []
    bot_state["cycles_completed"] = 0
    bot_state["balance"] = STARTING_BALANCE
    bot_state["equity"] = STARTING_BALANCE
    bot_state["pnl"] = 0.0
    bot_state["pnl_pct"] = 0.0
    return {"status": "reset", "balance": STARTING_BALANCE}


@app.get("/api/models")
async def list_models():
    """Return available OpenRouter models (free first)."""
    try:
        import requests
        from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
        resp = requests.get(
            f"{OPENROUTER_BASE_URL}/models",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            timeout=15,
        )
        if resp.status_code != 200:
            return {"error": f"OpenRouter returned {resp.status_code}", "models": []}
        all_models = resp.json().get("data", [])
        free = [m["id"] for m in all_models
                if m.get("pricing", {}).get("prompt") == "0"
                and m.get("pricing", {}).get("completion") == "0"]
        return {
            "current": bot_state["current_model"],
            "resolved": _resolve_free_model(),
            "free": free[:30],   # top 30 free
            "all_count": len(all_models),
            "free_count": len(free),
        }
    except Exception as e:
        return {"error": str(e), "models": []}


@app.post("/api/model")
async def set_model(request: Request):
    """Change the active model at runtime."""
    body = await request.json()
    model = body.get("model", "").strip()
    if not model:
        return {"error": "No model provided"}
    bot_state["current_model"] = model
    bot_state["resolved_model"] = _resolve_free_model() if model == "openrouter/free" else model
    # Persist to .env so it survives restarts
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        new_lines, found = [], False
        for line in lines:
            if line.startswith("OPENROUTER_MODEL="):
                new_lines.append(f"OPENROUTER_MODEL={model}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"OPENROUTER_MODEL={model}")
        env_file.write_text("\n".join(new_lines) + "\n")
    return {"status": "ok", "current": bot_state["current_model"], "resolved": bot_state["resolved_model"]}


# ============================================================================
# TRADING LOOP
# ============================================================================
async def trading_loop():
    """Main bot loop — iterates over SYMBOLS, fetches data, queries AI, executes."""
    while bot_state["is_running"]:
        for symbol in SYMBOLS:
            if not bot_state["is_running"]:
                break
            try:
                # Fetch market data (lazy MT5 import)
                try:
                    fetch_multi_timeframe_data, _ = _mt5()
                    market_data = fetch_multi_timeframe_data(symbol)
                except (ImportError, ModuleNotFoundError):
                    bot_state["last_logic"] = f"MT5 not installed — running in stub mode for {symbol}"
                    await asyncio.sleep(2)
                    continue
                if not market_data:
                    bot_state["last_logic"] = f"No data for {symbol}"
                    continue

                # Get AI decision
                decision = get_ai_decision(market_data, symbol)
                bot_state["last_logic"] = decision.get("logic", "")
                bot_state["last_confidence"] = decision.get("confidence_score", 0)
                bot_state["last_signal"] = decision.get("signal", "HOLD")
                bot_state["last_symbol"] = symbol
                bot_state["equity"] = market_data.get("equity", 0)

                # Execute if BUY/SELL
                if decision["signal"] in ("BUY", "SELL"):
                    result = execute_trade(
                        symbol,
                        decision["signal"],
                        market_data.get("ask", 0),
                    )
                    if result.get("success"):
                        # Simulated paper-trade outcome: 60% win at TP, 40% loss at SL
                        import random
                        roll = random.random()
                        won = roll < 0.60
                        outcome = "TP_HIT" if won else "SL_HIT"
                        # PnL: +TP% of trade value on win, -SL% on loss
                        trade_value = bot_state["balance"] * 0.10  # 10% position size
                        pnl = trade_value * (TP_PERCENT if won else -SL_PERCENT)
                        bot_state["balance"] += pnl
                        bot_state["equity"] = bot_state["balance"]
                        bot_state["pnl"] = bot_state["balance"] - bot_state["starting_balance"]
                        bot_state["pnl_pct"] = (bot_state["pnl"] / bot_state["starting_balance"]) * 100

                        bot_state["trade_history"].append({
                            "time": datetime.utcnow().isoformat(),
                            "asset": symbol,
                            "signal": decision["signal"],
                            "logic": decision["logic"],
                            "confidence": decision.get("confidence_score", 0),
                            "price": result.get("price", 0),
                            "sl": result.get("sl", 0),
                            "tp": result.get("tp", 0),
                            "mode": result.get("mode", "LIVE"),
                            "outcome": outcome,
                            "pnl": round(pnl, 2),
                            "balance_after": round(bot_state["balance"], 2),
                        })
                        # Keep last 100 trades
                        bot_state["trade_history"] = bot_state["trade_history"][-100:]

            except Exception as e:
                bot_state["last_logic"] = f"Error on {symbol}: {str(e)[:200]}"

            await asyncio.sleep(1)  # Brief pause between symbols

        bot_state["cycles_completed"] += 1
        await asyncio.sleep(bot_state["interval"])


# ============================================================================
# ENTRYPOINT
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    print(f"[BOT] Starting on http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    print(f"[BOT] Symbols: {SYMBOLS}")
    print(f"[BOT] Paper mode: {PAPER_MODE}")
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="info")
