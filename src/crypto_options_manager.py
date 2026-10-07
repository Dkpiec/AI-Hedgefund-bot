"""
Crypto Options Forward Test Manager & Multi-Asset Scanner (Fixed Expiry & Anti-Loop)
===================================================================================
Tracks the 0-DTE Trend-Skewed Iron Condor forward test across multiple crypto assets (BTC, ETH).
Rules:
1. Max 2 concurrent open positions in different crypto assets (e.g. 1 on BTC and 1 on ETH).
2. Scan interval: 30 minutes.
3. Accurate 08:00 UTC daily settlement expiry calculation (no negative time-to-expiry loops).
4. Sizing: 15% risk per trade of available equity/cash ($200 baseline).
5. Exits: 65% Theta Decay (TP), -0.7R net credit (SL), or 07:30 UTC Expiry Settlement.
6. Cycle Gate: Max 1 completed trade per symbol per settlement cycle.
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
    try:
        from src.crypto_options_condor import evaluate_condor_setup, bs_call_price, bs_put_price
    except ImportError:
        from crypto_options_condor import evaluate_condor_setup, bs_call_price, bs_put_price

DATA_DIR = os.getenv("OPTIONS_DATA_DIR", "data/forward_testing")
STATE_FILE = os.path.join(DATA_DIR, "crypto_options_condor_state.json")
SUPPORTED_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
MAX_CONCURRENT_POSITIONS = 2

def get_next_settlement_expiry(dt_utc: datetime.datetime) -> datetime.datetime:
    """
    Returns the target 08:00:00 UTC settlement timestamp for a given UTC datetime.
    - If dt_utc < 07:30 UTC: target is Today at 08:00 UTC.
    - If dt_utc >= 07:30 UTC: target is Tomorrow at 08:00 UTC.
    """
    if dt_utc.hour < 7 or (dt_utc.hour == 7 and dt_utc.minute < 30):
        return dt_utc.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        tomorrow = dt_utc + datetime.timedelta(days=1)
        return tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)

def init_crypto_options_state() -> Dict[str, Any]:
    state = {
        "strategy_id": "crypto_options_condor",
        "name": "0-DTE Trend-Skewed Iron Condor (15% Risk)",
        "description": "Multi-Asset 0-DTE Iron Condor on BTC/ETH options with 50 EMA trend-skewed delta wings, 65% decay TP, -0.7R SL, max 2 concurrent positions (BTC+ETH), and 30-min scan interval.",
        "start_date": "2026-10-07",
        "end_date": "2027-04-05",
        "currency": "USD",
        "initial_capital": 200.0,
        "current_equity": 200.0,
        "cash": 200.0,
        "deployed": 0.0,
        "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
        "supported_symbols": SUPPORTED_SYMBOLS,
        "scan_interval_mins": 30,
        "risk_per_trade_pct": 0.15,
        "open_positions": [],
        "closed_trades": [],
        "equity_curve": [
            {
                "date": "2026-10-07",
                "cash": 200.0,
                "deployed": 0.0,
                "total_equity": 200.0,
                "net_pnl": 0.0,
                "charges_paid": 0.0
            }
        ],
        "win_rate_pct": 0.0,
        "profit_factor": 0.0,
        "total_gross_pnl": 0.0,
        "total_charges_paid": 0.0,
        "total_net_pnl": 0.0
    }
    save_crypto_options_state(state)
    return state

def load_crypto_options_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return init_crypto_options_state()
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return init_crypto_options_state()

def save_crypto_options_state(state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=float)

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
        except Exception:
            cur_price = float(pos.get("underlying_price", 0.0))
            
        # Parse exact target expiry for this position
        if "expiry_time" in pos:
            try:
                expiry_dt = datetime.datetime.fromisoformat(pos["expiry_time"])
                if expiry_dt.tzinfo is None:
                    expiry_dt = expiry_dt.replace(tzinfo=datetime.timezone.utc)
            except Exception:
                expiry_dt = get_next_settlement_expiry(now_utc)
        else:
            expiry_dt = get_next_settlement_expiry(now_utc)
            pos["expiry_time"] = expiry_dt.isoformat()
            
        seconds_left = (expiry_dt - now_utc).total_seconds()
        hours_left = max(0.0, seconds_left / 3600.0)
        
        # T in years for Black-Scholes
        T_cur = max(0.0001, hours_left / (365.0 * 24.0))
        iv = float(pos.get("iv", 0.50))
        r = 0.04
        
        strikes = pos["strikes"]
        cur_sc = bs_call_price(cur_price, strikes["short_call"], T_cur, r, iv)
        cur_lc = bs_call_price(cur_price, strikes["long_call"], T_cur, r, iv)
        cur_sp = bs_put_price(cur_price, strikes["short_put"], T_cur, r, iv)
        cur_lp = bs_put_price(cur_price, strikes["long_put"], T_cur, r, iv)
        
        cur_spread_val = float((cur_sc - cur_lc) + (cur_sp - cur_lp))
        pnl_per_unit = float(pos["net_credit_per_unit"] - cur_spread_val)
        gross_pnl = float(pnl_per_unit * pos["contracts"])
        
        # Check Exits
        hit_exit = None
        if seconds_left <= 1800: # Within 30 min of 08:00 UTC settlement
            hit_exit = "EXPIRY_SETTLED"
        elif pnl_per_unit >= pos["tp_credit_target"]:
            hit_exit = "TP_65_DECAY"
        elif pnl_per_unit <= pos["sl_credit_threshold"]:
            hit_exit = "SL_TIGHT (-0.7R)"
            
        if hit_exit:
            step = 100 if sym.startswith("BTC") else 10
            underlying_notional = pos["contracts"] * cur_price * (step / cur_price)
            exit_fee = float(4 * 0.0003 * underlying_notional)
            total_fee = float(pos["entry_fee"] + exit_fee)
            net_pnl = float(gross_pnl - total_fee)
            
            closed_rec = {
                "id": pos["id"],
                "symbol": sym,
                "entry_date": pos["entry_date"],
                "exit_date": today_str,
                "settlement_cycle": expiry_dt.date().isoformat(),
                "strikes": strikes,
                "net_credit": round(float(pos["net_credit_per_unit"]), 4),
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
            pos["current_underlying_price"] = round(cur_price, 2)
            pos["hours_to_expiry"] = round(hours_left, 2)
            decay_pct = max(0.0, min(100.0, (pnl_per_unit / pos["net_credit_per_unit"]) * 100.0)) if pos["net_credit_per_unit"] > 0 else 0.0
            pos["decay_progress_pct"] = round(decay_pct, 1)
            remaining_open.append(pos)

    state["open_positions"] = remaining_open
    state["deployed"] = round(sum(p["margin_deployed"] for p in state["open_positions"]), 2)

    # 2. Check for New Entries across Supported Symbols (Max 2 concurrent positions in different assets)
    active_symbols = {p["symbol"] for p in state["open_positions"]}
    target_expiry = get_next_settlement_expiry(now_utc)
    target_cycle_str = target_expiry.date().isoformat()
    
    # Check which symbols have already traded for this settlement cycle
    recent_closed_cycles = {
        (t.get("symbol"), t.get("settlement_cycle", t.get("exit_date")))
        for t in state.get("closed_trades", [])
    }
    
    for sym in SUPPORTED_SYMBOLS:
        if len(state["open_positions"]) >= MAX_CONCURRENT_POSITIONS:
            break
            
        if sym in active_symbols:
            continue
            
        # Do not churn the same symbol multiple times in the same settlement cycle
        if (sym, target_cycle_str) in recent_closed_cycles:
            continue
            
        # Available capital check (minimum $15 cash to enter a position)
        if state["cash"] < 15.0:
            continue
            
        try:
            df_klines = fetch_live_binance_klines(sym, "1h", limit=60)
            hrs_to_exp = max(1.0, (target_expiry - now_utc).total_seconds() / 3600.0)
            setup = evaluate_condor_setup(df_klines, sym, hours_to_expiry=hrs_to_exp)
        except Exception:
            continue
            
        if setup:
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
                "expiry_time": target_expiry.isoformat(),
                "settlement_cycle": target_cycle_str,
                "underlying_price": round(float(setup["underlying_price"]), 2),
                "iv": round(float(setup["iv"]), 4),
                "strikes": {
                    "short_call": float(setup["strikes"]["short_call"]),
                    "long_call": float(setup["strikes"]["long_call"]),
                    "short_put": float(setup["strikes"]["short_put"]),
                    "long_put": float(setup["strikes"]["long_put"]),
                },
                "net_credit_per_unit": round(float(setup["net_credit_per_unit"]), 4),
                "max_wing_risk_per_unit": round(float(setup["max_wing_risk_per_unit"]), 4),
                "tp_credit_target": round(float(setup["tp_credit_target"]), 4),
                "sl_credit_threshold": round(float(setup["sl_credit_threshold"]), 4),
                "contracts": round(float(contracts), 4),
                "margin_deployed": round(float(margin_required), 2),
                "entry_fee": round(float(entry_fee), 2),
                "unrealized_pnl": 0.0,
                "hours_to_expiry": round((target_expiry - now_utc).total_seconds() / 3600.0, 2),
                "decay_progress_pct": 0.0
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
    state["current_equity"] = current_equity
    
    save_crypto_options_state(state)
    return {
        "status": "OK",
        "actions": actions_taken or ["NO_CHANGE"],
        "open_positions_count": len(state["open_positions"]),
        "state": state
    }

def reset_and_seed_baseline_forward_test():
    """
    Resets the state to $200 baseline capital and enters 2 live positions (1 BTC, 1 ETH).
    """
    state = init_crypto_options_state()
    res = scan_and_update_options_forward_test()
    return res

if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        print("Resetting state to clean baseline...")
        res = reset_and_seed_baseline_forward_test()
    else:
        res = scan_and_update_options_forward_test()
    print("Scan & Update Result:", res["status"], "| Actions:", res["actions"])
    print("Portfolio State:")
    print(f"  Cash: ${res['state']['cash']} | Deployed: ${res['state']['deployed']} | Open: {len(res['state']['open_positions'])}")
    for p in res['state']['open_positions']:
        print(f"    - {p['symbol']}: Margin ${p['margin_deployed']} | Strikes: {p['strikes']} | Hours Left: {p.get('hours_to_expiry')}h")
