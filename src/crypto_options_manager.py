"""
Crypto Options Forward Test Manager & Multi-Asset Scanner
=========================================================
Tracks the 0-DTE Trend-Skewed Iron Condor forward test across multiple crypto assets (BTC, ETH).
Rules:
- Max 2 concurrent open positions across different crypto assets (e.g., 1 BTC + 1 ETH).
- 15% risk allocation per trade ($200 initial baseline capital).
- 30-minute scan interval for position updates, exits, and new entries.
- Real Binance Options Maker (0.02%) & Taker (0.03%) fees.
- Integrated into dashboard_v2 and AI-Hedgefund-bot state.
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

SUPPORTED_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
MAX_CONCURRENT_POSITIONS = 2

def load_crypto_options_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r") as f:
                state = json.load(f)
                # Ensure updated rules and max_positions metadata
                state["max_concurrent_positions"] = MAX_CONCURRENT_POSITIONS
                state["supported_symbols"] = SUPPORTED_SYMBOLS
                state["scan_interval"] = "30m"
                return state
        except Exception:
            pass
            
    today_str = datetime.date.today().isoformat()
    end_date_str = (datetime.date.today() + datetime.timedelta(days=180)).isoformat()
    state = {
        "strategy_id": "crypto_options_condor",
        "name": "0-DTE Trend-Skewed Iron Condor (Multi-Asset 15% Risk)",
        "description": "Intraday 0-DTE Asian session Iron Condor on BTC & ETH options (max 2 concurrent positions) with 50 EMA trend-skewed delta wings, 65% decay TP, -0.7R SL, and 30-minute scan interval.",
        "start_date": today_str,
        "end_date": end_date_str,
        "duration_days": 180,
        "initial_capital": 200.0,
        "cash": 200.0,
        "deployed": 0.0,
        "risk_per_trade_pct": 0.15,
        "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
        "supported_symbols": SUPPORTED_SYMBOLS,
        "scan_interval": "30m",
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
            "session_entry_utc": "00:00 - 04:00 UTC",
            "session_exit_utc": "07:30 UTC",
            "scan_interval": "Every 30 Minutes",
            "max_positions": "2 concurrent trades across distinct cryptos (e.g. 1 BTC + 1 ETH)",
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
    Called every 30 minutes by cron / dashboard to manage open positions and scan for new entries
    across supported crypto assets (BTC, ETH). Max 2 concurrent positions in different assets.
    """
    state = load_crypto_options_state()
    today_str = datetime.date.today().isoformat()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    actions_taken = []
    
    # 1. Update and manage all existing Open Positions
    remaining_open = []
    for pos in state.get("open_positions", []):
        sym = pos["symbol"]
        try:
            df_live = fetch_live_binance_klines(sym, "1h", limit=10)
            cur_price = float(df_live['close'].iloc[-1])
        except Exception as e:
            # Fallback to last recorded price if network glitch
            cur_price = pos.get("underlying_price", 0.0)
            
        # Calculate current time to expiry (08:00 UTC)
        expiry_today = now_utc.replace(hour=8, minute=0, second=0, microsecond=0)
        hours_left = max(0.01, (expiry_today - now_utc).total_seconds() / 3600.0)
        T_cur = hours_left / (365.0 * 24.0)
        iv = pos.get("iv", 0.50)
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
            actions_taken.append(f"CLOSED {sym} ({hit_exit}): Net PnL ${net_pnl:.2f}")
        else:
            pos["unrealized_pnl"] = round(gross_pnl, 2)
            pos["current_spread_val"] = round(cur_spread_val, 4)
            pos["current_underlying_price"] = cur_price
            remaining_open.append(pos)

    state["open_positions"] = remaining_open
    state["deployed"] = round(sum(p["margin_deployed"] for p in state["open_positions"]), 2)

    # 2. Check for New Entries across Supported Symbols (Max 2 concurrent positions in different assets)
    active_symbols = {p["symbol"] for p in state["open_positions"]}
    
    for sym in SUPPORTED_SYMBOLS:
        if len(state["open_positions"]) >= MAX_CONCURRENT_POSITIONS:
            break
            
        if sym in active_symbols:
            continue
            
        # Available capital check (minimum $15 cash to enter a position)
        if state["cash"] < 15.0:
            continue
            
        try:
            df_klines = fetch_live_binance_klines(sym, "1h", limit=60)
            setup = evaluate_condor_setup(df_klines, sym)
        except Exception as e:
            continue
            
        if setup:
            # Sizing: 15% risk of total current equity or available cash
            total_equity = state["cash"] + state["deployed"]
            risk_usd = round(min(state["cash"], total_equity * state.get("risk_per_trade_pct", 0.15)), 2)
            if risk_usd < 10.0:
                risk_usd = round(state["cash"] * 0.5, 2)
                
            contracts = risk_usd / setup["max_wing_risk_per_unit"]
            step = 100 if sym.startswith("BTC") else 10
            notional = contracts * setup["underlying_price"] * (step / setup["underlying_price"])
            entry_fee = (2 * 0.0002 + 2 * 0.0003) * notional
            margin_required = risk_usd
            
            sym_clean = sym.replace("USDT", "")
            pos_rec = {
                "id": f"CONDOR_{sym_clean}_{today_str}_{len(state['closed_trades']) + len(state['open_positions']) + 1}",
                "symbol": sym,
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
            state["deployed"] = round(state["deployed"] + margin_required, 2)
            state["open_positions"].append(pos_rec)
            active_symbols.add(sym)
            actions_taken.append(f"ENTERED {sym}: Margin ${margin_required:.2f}, Credit ${setup['net_credit_per_unit']:.2f}")

    # 3. Recalculate portfolio metrics
    closed = state.get("closed_trades", [])
    wins = [t for t in closed if t.get("net_pnl", 0) > 0 or t.get("status") == "PROFIT"]
    losses = [t for t in closed if t.get("net_pnl", 0) <= 0 and t.get("status") != "PROFIT"]
    state["win_rate_pct"] = round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0
    tot_win = sum(t["net_pnl"] for t in wins)
    tot_loss = abs(sum(t["net_pnl"] for t in losses))
    state["profit_factor"] = round(tot_win / tot_loss, 2) if tot_loss > 0 else (999.0 if tot_win > 0 else 0.0)
    state["total_net_pnl"] = round(sum(t.get("net_pnl", 0.0) for t in closed), 2)
    state["total_charges_paid"] = round(sum(t.get("fees", 0.0) for t in closed), 2)
    
    current_equity = round(state["cash"] + state["deployed"], 2)
    state["equity_curve"].append({
        "date": today_str,
        "cash": state["cash"],
        "deployed": state["deployed"],
        "total_equity": current_equity,
        "net_pnl": state["total_net_pnl"],
        "charges_paid": state["total_charges_paid"]
    })
    
    save_crypto_options_state(state)
    return {
        "status": "OK",
        "actions": actions_taken or ["NO_CHANGE"],
        "open_positions_count": len(state["open_positions"]),
        "state": state
    }

if __name__ == "__main__":
    res = scan_and_update_options_forward_test()
    print("Scan & Update Result:", res["status"], "| Actions:", res["actions"])
    print("Portfolio State:")
    print(f"  Cash: ${res['state']['cash']} | Deployed: ${res['state']['deployed']} | Open: {len(res['state']['open_positions'])}")
    for p in res['state']['open_positions']:
        print(f"    - {p['symbol']}: Margin ${p['margin_deployed']} | Strikes: {p['strikes']}")
