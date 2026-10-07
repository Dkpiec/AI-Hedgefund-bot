"""
Crypto Options Forward Test Manager & Daily Scanner
===================================================
Tracks the 0-DTE Trend-Skewed Iron Condor forward test with $200 initial capital,
15% risk sizing, real Binance Options Maker (0.02%) & Taker (0.03%) fees.
Integrated into dashboard_v2 and AI-Hedgefund-bot state.
"""
import os
import sys
import json
import datetime
import urllib.request
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional

try:
    from src.crypto_options_condor import evaluate_condor_setup, bs_call_price, bs_put_price
except ImportError:
    from crypto_options_condor import evaluate_condor_setup, bs_call_price, bs_put_price

DATA_DIR = os.getenv("OPTIONS_DATA_DIR", "data/forward_testing")
STATE_PATH = os.path.join(DATA_DIR, "crypto_options_condor_state.json")

def load_crypto_options_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
            
    today_str = datetime.date.today().isoformat()
    end_date_str = (datetime.date.today() + datetime.timedelta(days=180)).isoformat()
    state = {
        "strategy_id": "crypto_options_condor",
        "name": "0-DTE Trend-Skewed Iron Condor (15% Risk)",
        "description": "Daily 00:00 UTC Asian session Iron Condor on BTC/ETH options with 50 EMA trend-skewed delta wings, 65% decay TP, -0.7R SL, and 15% risk compounding.",
        "start_date": today_str,
        "end_date": end_date_str,
        "duration_days": 180,
        "initial_capital": 200.0,
        "cash": 200.0,
        "deployed": 0.0,
        "risk_per_trade_pct": 0.15,
        "currency": "USD",
        "open_positions": [],
        "closed_trades": [],
        "total_gross_pnl": 0.0,
        "total_charges_paid": 0.0,
        "total_net_pnl": 0.0,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0,
        "equity_curve": [
            {
                "date": today_str,
                "cash": 200.0,
                "deployed": 0.0,
                "total_equity": 200.0,
                "net_pnl": 0.0,
                "charges_paid": 0.0
            }
        ],
        "rules": {
            "session_entry_utc": "00:00 UTC",
            "session_exit_utc": "07:30 UTC",
            "iv_filter": "35% <= IV <= 85%",
            "trend_skew": "50 EMA on 1H (2.0σ/1.6σ delta wings)",
            "take_profit": "65% Net Credit Decay",
            "stop_loss": "-0.70x Net Credit Loss",
            "fee_model": "Binance Options 0.02% Maker / 0.03% Taker"
        }
    }
    save_crypto_options_state(state)
    return state

def save_crypto_options_state(state: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, default=str)

def fetch_live_binance_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 60) -> pd.DataFrame:
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    df = pd.DataFrame(data, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'qav', 'num_trades', 'taker_base', 'taker_quote', 'ignore'
    ])
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    return df

def scan_and_update_options_forward_test() -> Dict[str, Any]:
    """
    Called by cron / dashboard to scan for entry or update existing 0-DTE Condor positions.
    """
    state = load_crypto_options_state()
    today_str = datetime.date.today().isoformat()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    # 1. Update Open Positions (if any)
    if state["open_positions"]:
        pos = state["open_positions"][0]
        sym = pos["symbol"]
        df_live = fetch_live_binance_klines(sym, "1h", limit=10)
        cur_price = float(df_live['close'].iloc[-1])
        
        # Calculate current time to expiry (08:00 UTC)
        expiry_today = now_utc.replace(hour=8, minute=0, second=0, microsecond=0)
        hours_left = max(0.01, (expiry_today - now_utc).total_seconds() / 3600.0)
        T_cur = hours_left / (365.0 * 24.0)
        iv = pos["iv"]
        r = 0.04
        
        strikes = pos["strikes"]
        cur_sc = bs_call_price(cur_price, strikes["short_call"], T_cur, r, iv)
        cur_lc = bs_call_price(cur_price, strikes["long_call"], T_cur, r, iv)
        cur_sp = bs_put_price(cur_price, strikes["short_put"], T_cur, r, iv)
        cur_lp = bs_put_price(cur_price, strikes["long_put"], T_cur, r, iv)
        
        cur_spread_val = (cur_sc - cur_lc) + (cur_sp - cur_lp)
        pnl_per_unit = pos["net_credit_per_unit"] - cur_spread_val
        gross_pnl = pnl_per_unit * pos["contracts"]
        
        # Check Exits
        hit_exit = None
        if pnl_per_unit >= pos["tp_credit_target"]:
            hit_exit = "TP_65_DECAY"
        elif pnl_per_unit <= pos["sl_credit_threshold"]:
            hit_exit = "SL_TIGHT (-0.7R)"
        elif now_utc.hour >= 8 or (now_utc.hour == 7 and now_utc.minute >= 30):
            hit_exit = "EXPIRY_SETTLED"
            
        if hit_exit:
            step = 100 if sym.startswith("BTC") else 10
            underlying_notional = pos["contracts"] * cur_price * (step / cur_price)
            exit_fee = 4 * 0.0003 * underlying_notional
            total_fee = pos["entry_fee"] + exit_fee
            net_pnl = gross_pnl - total_fee
            
            closed_rec = {
                "id": pos["id"],
                "symbol": sym,
                "entry_date": pos["entry_date"],
                "exit_date": today_str,
                "strikes": strikes,
                "net_credit": pos["net_credit_per_unit"],
                "gross_pnl": round(gross_pnl, 2),
                "fees": round(total_fee, 2),
                "net_pnl": round(net_pnl, 2),
                "exit_reason": hit_exit,
                "status": "PROFIT" if net_pnl > 0 else "LOSS"
            }
            state["closed_trades"].append(closed_rec)
            state["cash"] = round(state["cash"] + pos["margin_deployed"] + net_pnl, 2)
            state["deployed"] = 0.0
            state["open_positions"] = []
            
            # Recalculate portfolio metrics
            wins = [t for t in state["closed_trades"] if t["net_pnl"] > 0]
            losses = [t for t in state["closed_trades"] if t["net_pnl"] <= 0]
            state["win_rate_pct"] = round(len(wins) / len(state["closed_trades"]) * 100.0, 1) if state["closed_trades"] else 0.0
            tot_win = sum(t["net_pnl"] for t in wins)
            tot_loss = abs(sum(t["net_pnl"] for t in losses))
            state["profit_factor"] = round(tot_win / tot_loss, 2) if tot_loss > 0 else 999.0
            state["total_net_pnl"] = round(sum(t["net_pnl"] for t in state["closed_trades"]), 2)
            state["total_charges_paid"] = round(sum(t["fees"] for t in state["closed_trades"]), 2)
            
            state["equity_curve"].append({
                "date": today_str,
                "cash": state["cash"],
                "deployed": 0.0,
                "total_equity": state["cash"],
                "net_pnl": state["total_net_pnl"],
                "charges_paid": state["total_charges_paid"]
            })
            save_crypto_options_state(state)
            return {"status": "CLOSED", "trade": closed_rec, "state": state}
        else:
            pos["unrealized_pnl"] = round(gross_pnl, 2)
            pos["current_spread_val"] = round(cur_spread_val, 4)
            save_crypto_options_state(state)
            return {"status": "OPEN", "position": pos, "state": state}
            
    # 2. Check for New Daily Entry (00:00 - 03:00 UTC)
    if not state["open_positions"] and state["cash"] >= 20.0:
        df_btc = fetch_live_binance_klines("BTCUSDT", "1h", limit=60)
        setup = evaluate_condor_setup(df_btc, "BTCUSDT")
        if setup:
            risk_usd = state["cash"] * state["risk_per_trade_pct"]
            contracts = risk_usd / setup["max_wing_risk_per_unit"]
            step = 100
            notional = contracts * setup["underlying_price"] * (step / setup["underlying_price"])
            entry_fee = (2 * 0.0002 + 2 * 0.0003) * notional
            margin_required = risk_usd
            
            pos_rec = {
                "id": f"CONDOR_BTC_{today_str}",
                "symbol": "BTCUSDT",
                "entry_date": today_str,
                "entry_time": now_utc.isoformat(),
                "underlying_price": setup["underlying_price"],
                "iv": setup["iv"],
                "strikes": setup["strikes"],
                "net_credit_per_unit": setup["net_credit_per_unit"],
                "max_wing_risk_per_unit": setup["max_wing_risk_per_unit"],
                "tp_credit_target": setup["tp_credit_target"],
                "sl_credit_threshold": setup["sl_credit_threshold"],
                "contracts": round(contracts, 4),
                "margin_deployed": round(margin_required, 2),
                "entry_fee": round(entry_fee, 2),
                "unrealized_pnl": 0.0
            }
            state["cash"] = round(state["cash"] - margin_required, 2)
            state["deployed"] = round(margin_required, 2)
            state["open_positions"].append(pos_rec)
            save_crypto_options_state(state)
            return {"status": "ENTERED", "position": pos_rec, "state": state}
            
    return {"status": "IDLE", "state": state}

if __name__ == "__main__":
    res = scan_and_update_options_forward_test()
    print("Scan & Update Result:", res["status"])
    print("Portfolio State:", json.dumps(res["state"], indent=2))
