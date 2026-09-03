"""
Main FastAPI App - AI Hedge Fund Bot (Binance Spot Testnet)
============================================================
Single-timeframe scanner over a USDT-only universe of liquid Binance
pairs under MAX_PRICE_USD. User picks the active timeframe from a
dashboard dropdown; loop interval matches the timeframe (1h=3600s).
Strict limit orders with 5-min auto-cancel. 5% capital risk per trade.
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
    CANDIDATE_SYMBOLS,
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    CHART_TIMEFRAMES,
    DEFAULT_CHART_TIMEFRAME,
    DEFAULT_SCAN_INTERVAL,
    MAX_PRICE_USD,
    OPENROUTER_MODEL,
    RISK_PER_TRADE,
    SCAN_INTERVAL_OPTIONS,
    SCAN_INTERVALS,
    SL_PERCENT,
    STARTING_BALANCE,
    SYMBOLS,
    TP_PERCENT,
)
from ai_brain import get_ai_decision
from data.data_engine import (
    filter_universe,
    fetch_multi_timeframe_data,
    get_account_info,
)
from execution import (
    cancel_expired_orders,
    check_paper_sl_tp,
    execute_trade,
    get_open_orders,
    get_paper_balance,
    has_open_position,
    is_live_mode,
    sync_open_orders,
)

# ============================================================================
# GLOBAL STATE
# ============================================================================
bot_state = {
    "is_running": False,
    "scan_interval": DEFAULT_SCAN_INTERVAL,
    "interval": SCAN_INTERVALS[DEFAULT_SCAN_INTERVAL],
    "chart_timeframe": DEFAULT_CHART_TIMEFRAME,
    "equity": STARTING_BALANCE,
    "balance": STARTING_BALANCE,
    "free": STARTING_BALANCE,
    "starting_balance": STARTING_BALANCE,
    "pnl": 0.0,
    "pnl_pct": 0.0,
    "risk_per_trade": RISK_PER_TRADE,
    "last_logic": "Bot idle. Click 'Initialize Engine' to start.",
    "last_confidence": 0,
    "last_signal": "HOLD",
    "last_symbol": "",
    "last_timeframe": "",
    "trade_history": [],
    "open_orders": [],
    "cancelled_orders": [],
    "started_at": None,
    "next_scan_at": None,
    "cycles_completed": 0,
    "current_model": OPENROUTER_MODEL,
    "resolved_model": OPENROUTER_MODEL,
    "universe": SYMBOLS,
    "scan_interval_options": SCAN_INTERVAL_OPTIONS,
    "chart_timeframe_options": CHART_TIMEFRAMES,
}

# ============================================================================
# FASTAPI APP
# ============================================================================
app = FastAPI(title="AI Hedge Fund Bot", version="2.0.0")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "dashboard" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "dashboard" / "templates"))


# ============================================================================
# STARTUP — filter universe once
# ============================================================================
@app.on_event("startup")
async def startup_event():
    try:
        qualified = filter_universe()
        bot_state["universe"] = qualified
        print(f"[BOT] Universe after price+volume filter: {qualified}")
    except Exception as e:
        print(f"[BOT] Universe filter failed: {e}")


# ============================================================================
# ROUTES
# ============================================================================
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    response = templates.TemplateResponse(request, "index.html", {
        "symbols": bot_state["universe"] or [],
        "scan_intervals": SCAN_INTERVAL_OPTIONS,
        "active_scan_interval": bot_state["scan_interval"],
        "chart_timeframe": bot_state["chart_timeframe"],
    })
    # Prevent any browser/proxy caching so the dashboard always reflects the live API
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/test-dots", response_class=HTMLResponse)
async def test_dots():
    return HTMLResponse(content=open(BASE_DIR / "dashboard" / "templates" / "test_dots.html").read())


@app.post("/api/control")
async def control(request: Request):
    body = await request.json()
    action = body.get("action", "").lower()
    if action == "start":
        bot_state["is_running"] = True
        bot_state["interval"] = SCAN_INTERVALS[bot_state["scan_interval"]]
        bot_state["started_at"] = datetime.utcnow().isoformat()
        bot_state["next_scan_at"] = datetime.utcnow().isoformat()
        asyncio.create_task(trading_loop())
        return {
            "status": "started",
            "interval": bot_state["interval"],
            "scan_interval": bot_state["scan_interval"],
            "chart_timeframe": bot_state["chart_timeframe"],
        }
    elif action == "stop":
        bot_state["is_running"] = False
        return {"status": "stopped"}
    return {"error": f"Unknown action: {action}"}


@app.post("/api/scan-interval")
async def set_scan_interval(request: Request):
    """
    Change the loop cycle interval (how often the AI re-checks the market).
    Valid options: 1m, 5m, 15m, 30m, 1h
    """
    body = await request.json()
    interval_key = body.get("interval", "").strip()
    if interval_key not in SCAN_INTERVAL_OPTIONS:
        return {"error": f"Invalid interval. Choose from {SCAN_INTERVAL_OPTIONS}"}
    bot_state["scan_interval"] = interval_key
    bot_state["interval"] = SCAN_INTERVALS[interval_key]
    return {
        "status": "ok",
        "scan_interval": interval_key,
        "interval_seconds": bot_state["interval"],
    }


@app.get("/api/status")
async def status():
    # Refresh balance: paper mode reads the virtual persisted balance, live mode reads Binance
    try:
        if is_live_mode():
            acct = get_account_info()
            if acct.get("free") is not None:
                bot_state["free"] = acct["free"]
                bot_state["balance"] = acct["balance"]
                bot_state["equity"] = acct["equity"]
        else:
            # Paper mode: pull live virtual balance + compute equity from open positions
            bal = get_paper_balance()
            # Mark-to-market open paper positions at current price
            from data.data_engine import get_live_ticker
            unrealized = 0.0
            for o in bot_state.get("open_orders", []):
                if o.get("mode") == "PAPER":
                    px = get_live_ticker(o["symbol"])
                    if px:
                        if o["side"] == "BUY":
                            unrealized += (px - o["price"]) * o["qty"]
                        else:
                            unrealized += (o["price"] - px) * o["qty"]
            bot_state["free"] = bal
            bot_state["balance"] = bal
            bot_state["equity"] = bal + unrealized
        bot_state["pnl"] = bot_state["balance"] - bot_state["starting_balance"]
        bot_state["pnl_pct"] = (bot_state["pnl"] / bot_state["starting_balance"]) * 100
    except Exception:
        pass
    bot_state["open_orders"] = get_open_orders()
    bot_state["mode"] = "LIVE" if is_live_mode() else "PAPER"
    bot_state["paper_mode"] = not is_live_mode()  # back-compat for the dashboard JS
    # Tiered position sizing info (0.5% of position size, position size scales +$10 per +$200 balance)
    from config import (
        get_position_size_for_balance,
        get_risk_per_trade_for_balance,
        get_current_tier,
    )
    bal = bot_state.get("balance", 0.0)
    bot_state["position_size"] = get_position_size_for_balance(bal)
    bot_state["risk_per_trade_usdt"] = get_risk_per_trade_for_balance(bal)
    bot_state["tier"] = get_current_tier(bal)
    return bot_state


@app.post("/api/reset")
async def reset():
    bot_state["trade_history"] = []
    bot_state["cancelled_orders"] = []
    bot_state["cycles_completed"] = 0
    bot_state["balance"] = bot_state["starting_balance"]
    bot_state["equity"] = bot_state["starting_balance"]
    bot_state["free"] = bot_state["starting_balance"]
    bot_state["pnl"] = 0.0
    bot_state["pnl_pct"] = 0.0
    # Also clear in-memory open positions in execution.py and reset the paper balance file
    from execution import OPEN_ORDERS
    OPEN_ORDERS.clear()
    from execution import _save_paper_balance
    _save_paper_balance(bot_state["starting_balance"])
    return {"status": "reset", "balance": bot_state["starting_balance"]}


@app.post("/api/refresh-universe")
async def refresh_universe():
    qualified = filter_universe()
    bot_state["universe"] = qualified
    return {"status": "refreshed", "universe": qualified, "count": len(qualified)}


@app.post("/api/cancel-expired")
async def cancel_expired():
    cancelled = cancel_expired_orders()
    bot_state["cancelled_orders"] = cancelled
    return {"cancelled": cancelled, "count": len(cancelled)}


@app.get("/api/models")
async def list_models():
    try:
        import requests as _req
        from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
        resp = _req.get(
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
            "free": free[:30],
            "all_count": len(all_models),
            "free_count": len(free),
        }
    except Exception as e:
        return {"error": str(e), "models": []}


def _resolve_free_model():
    from ai_brain import _resolve_free_model as rfm
    return rfm()


@app.post("/api/model")
async def set_model(request: Request):
    body = await request.json()
    model = body.get("model", "").strip()
    if not model:
        return {"error": "No model provided"}
    bot_state["current_model"] = model
    bot_state["resolved_model"] = _resolve_free_model() if model == "openrouter/free" else model
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
# TRADING LOOP — single active timeframe, interval matches candle period
# ============================================================================
async def trading_loop():
    """
    Scans the universe on the chart timeframe (1h) every `scan_interval` seconds.
    User can change scan_interval mid-run via /api/scan-interval.
    Chart timeframe is fixed (currently 1h) — change in config.py to alter.
    """
    while bot_state["is_running"]:
        interval_key = bot_state["scan_interval"]
        interval = SCAN_INTERVALS[interval_key]
        chart_tf = bot_state["chart_timeframe"]

        # 0a. Check SL/TP on any open PAPER positions against live ticker
        try:
            closed = check_paper_sl_tp()
            for c in closed:
                bot_state["trade_history"].append({
                    "time": datetime.utcnow().isoformat(),
                    "asset": c["symbol"],
                    "signal": c["side"],
                    "logic": c.get("logic", ""),
                    "confidence": c.get("confidence", 0),
                    "timeframe": c.get("timeframe", ""),
                    "entry": c["entry"],
                    "price": c["exit"],
                    "exit": c["exit"],
                    "qty": c["qty"],
                    "sl": c["sl"],
                    "tp": c["tp"],
                    "order_id": c["order_id"],
                    "mode": c.get("mode", "PAPER"),
                    "status": c["outcome"],   # TP_HIT or SL_HIT
                    "pnl": c["pnl"],
                    "balance_after": c["balance_after"],
                    "notional": c.get("notional", 0),
                    "risk_per_trade": c.get("risk_per_trade", 0),
                    "tier": c.get("tier", 0),
                })
                bot_state["last_logic"] = (
                    f"CLOSED {c['side']} {c['symbol']} @ {c['exit']:.4f} "
                    f"({c['outcome']}) PnL=${c['pnl']:+.2f}"
                )
            bot_state["trade_history"] = bot_state["trade_history"][-200:]
        except Exception as e:
            print(f"[BOT] check_paper_sl_tp error: {e}")

        # 0b. Sync open orders with Binance (live limit orders; no-op in paper mode)
        try:
            sync_open_orders()
        except Exception as e:
            print(f"[BOT] sync_open_orders error: {e}")

        for symbol in bot_state["universe"] or SYMBOLS:
            if not bot_state["is_running"]:
                break
            try:
                # 1. Cancel any expired orders
                cancelled = cancel_expired_orders()
                if cancelled:
                    bot_state["cancelled_orders"] = (
                        bot_state.get("cancelled_orders", []) + cancelled
                    )[-50:]

                # 2. Fetch data for the chart timeframe (H1)
                market_data = fetch_multi_timeframe_data(symbol, [chart_tf])
                if not market_data:
                    bot_state["last_logic"] = f"No data for {symbol}"
                    continue

                # 3. Get AI decision
                decision = get_ai_decision(market_data, symbol)
                bot_state["last_logic"] = decision.get("logic", "")
                bot_state["last_confidence"] = decision.get("confidence_score", 0)
                bot_state["last_signal"] = decision.get("signal", "HOLD")
                bot_state["last_symbol"] = symbol
                bot_state["last_timeframe"] = chart_tf

                # 4. Skip if confidence too low
                if decision.get("confidence_score", 0) < 55:
                    bot_state["last_logic"] += " (skipped: low confidence)"
                    print(f"[SCAN] {symbol} skip: conf={decision.get('confidence_score',0)} signal={decision.get('signal','?')}")
                    continue

                # 5. Execute on BUY/SELL — but ONLY if no open position for this symbol
                if decision["signal"] in ("BUY", "SELL"):
                    if has_open_position(symbol):
                        bot_state["last_logic"] = (
                            f"{symbol}: position already open, skipping {decision['signal']}"
                        )
                        print(f"[SKIP] {symbol} {decision['signal']}: position already open")
                        continue
                    print(f"[FIRE] {symbol} {decision['signal']} conf={decision.get('confidence_score',0)}")
                    result = execute_trade(
                        symbol,
                        decision["signal"],
                        market_data.get("ask", 0),
                        decision,
                    )
                    if result.get("success"):
                        bot_state["trade_history"].append({
                            "time": datetime.utcnow().isoformat(),
                            "asset": symbol,
                            "signal": decision["signal"],
                            "logic": decision.get("logic", ""),
                            "confidence": decision.get("confidence_score", 0),
                            "timeframe": chart_tf,
                            "price": result.get("price", 0),
                            "qty": result.get("qty", 0),
                            "sl": result.get("sl", 0),
                            "tp": result.get("tp", 0),
                            "order_id": result.get("order_id", ""),
                            "mode": result.get("mode", "LIVE"),
                            "status": "PLACED",
                            "notional": result.get("notional", 0),
                            "risk_per_trade": result.get("risk_per_trade", 0),
                            "tier": result.get("tier", 0),
                        })
                        bot_state["trade_history"] = bot_state["trade_history"][-100:]

            except Exception as e:
                bot_state["last_logic"] = f"Error on {symbol}: {str(e)[:200]}"

            await asyncio.sleep(0.5)

        bot_state["cycles_completed"] += 1
        bot_state["next_scan_at"] = (
            datetime.utcnow().timestamp() + interval
        )
        # Sleep in 1s chunks so /api/control stop and /api/scan-interval stay responsive
        for _ in range(interval):
            if not bot_state["is_running"]:
                break
            # Pick up scan_interval changes mid-sleep
            if bot_state["scan_interval"] != interval_key:
                bot_state["interval"] = SCAN_INTERVALS[bot_state["scan_interval"]]
                break
            await asyncio.sleep(1)


# ============================================================================
# ENTRYPOINT
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    print(f"[BOT] Starting on http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    print(f"[BOT] Candidate universe: {CANDIDATE_SYMBOLS}")
    print(f"[BOT] Chart timeframe (candles the AI analyzes): {DEFAULT_CHART_TIMEFRAME}")
    print(f"[BOT] Default scan interval: {DEFAULT_SCAN_INTERVAL} ({SCAN_INTERVALS[DEFAULT_SCAN_INTERVAL]}s)")
    print(f"[BOT] Available scan intervals: {SCAN_INTERVAL_OPTIONS}")
    print(f"[BOT] Risk per trade: {RISK_PER_TRADE*100:.1f}%")
    print(f"[BOT] Max price filter: ${MAX_PRICE_USD}")
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="info")
