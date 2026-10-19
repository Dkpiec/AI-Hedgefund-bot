"""Core Orchestrator for Forward Testing and Paper Trading Engine.
Coordinates multi-asset simulated executions, live mark-to-market valuations,
strict bracket trailing, statutory charges accounting, and database syncing.
"""

import os
import json
import time
import logging
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from config.settings import get_settings
from pricing.quotes_fetcher import get_spot_price
from pricing.black_scholes import bs_call_price, bs_put_price
from pricing.charges import OptionsCharges, EquityCharges, CurrencyCharges, CryptoCharges
from database.sync_db import sync_all_strategies

logger = logging.getLogger("forward_engine.orchestrator")


class ForwardTestingOrchestrator:
    def __init__(self):
        self.settings = get_settings()
        self.data_dir = self.settings.data_dir
        self.db_path = self.settings.db_path
        self._running = False
        self.last_sync_time = 0.0

    def list_strategies(self) -> List[Dict[str, Any]]:
        """List summary status for all forward-test strategies."""
        results = []
        if not self.data_dir.exists():
            return results
        for fname in sorted(os.listdir(self.data_dir)):
            if not fname.endswith("_state.json"):
                continue
            fpath = self.data_dir / fname
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    st = json.load(f)
                open_pos = st.get("open_positions", [])
                closed = st.get("closed_trades", [])
                total_trades = len(closed)
                wins = len([t for t in closed if t.get("gross_pnl", 0) > 0 or t.get("status") == "PROFIT"])
                win_rate = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0
                results.append({
                    "strategy_id": st.get("strategy_id", fname.replace("_state.json", "")),
                    "name": st.get("name", ""),
                    "currency": st.get("currency", "INR"),
                    "initial_capital": st.get("initial_capital", 200000.0),
                    "current_equity": st.get("current_equity", st.get("initial_capital", 200000.0)),
                    "cash": st.get("cash", 200000.0),
                    "deployed": st.get("deployed", 0.0),
                    "open_positions_count": len(open_pos),
                    "closed_trades_count": total_trades,
                    "win_rate_pct": win_rate,
                    "total_net_pnl": st.get("total_net_pnl", 0.0),
                })
            except Exception as e:
                logger.error(f"Error reading state file {fname}: {e}")
        return results

    def run_surveillance_tick(self) -> Dict[str, Any]:
        """Execute a full mark-to-market surveillance tick across all strategies."""
        actions = []
        if not self.data_dir.exists():
            return {"status": "NO_DATA_DIR", "actions": []}

        for fname in os.listdir(self.data_dir):
            if not fname.endswith("_state.json"):
                continue
            fpath = self.data_dir / fname
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                continue

            strat_id = state.get("strategy_id", fname.replace("_state.json", ""))
            open_positions = state.get("open_positions", [])
            if not open_positions:
                continue

            remaining_positions = []
            modified = False

            for pos in open_positions:
                symbol = pos.get("symbol", "")
                qty = float(pos.get("quantity", 1))
                entry_px = float(pos.get("entry_price", pos.get("entry_option_price", 0.0)))
                sl_px = float(pos.get("stop_loss", pos.get("stop_loss_option", 0.0)))
                tp_px = float(pos.get("take_profit", pos.get("take_profit_option", 0.0)))

                # Determine underlying asset for mark-to-market
                underlying = "NIFTY"
                if "BANKNIFTY" in symbol.upper():
                    underlying = "BANKNIFTY"
                elif "SENSEX" in symbol.upper():
                    underlying = "SENSEX"
                elif "CRUDE" in symbol.upper():
                    underlying = "CRUDE"
                elif "NATGAS" in symbol.upper():
                    underlying = "NATGAS"
                elif "GOLD" in symbol.upper():
                    underlying = "GOLD"
                elif "BTC" in symbol.upper():
                    underlying = "BTC"
                elif "ETH" in symbol.upper():
                    underlying = "ETH"

                spot = get_spot_price(underlying)
                cur_px = entry_px
                if spot:
                    # Estimate option mark price via Black-Scholes if strike in pos
                    strike = float(pos.get("strike", 0))
                    opt_type = pos.get("option_type", "CE")
                    if strike > 0:
                        cur_px = bs_call_price(spot, strike, 2.0 / 365.0) if opt_type.upper() == "CE" else bs_put_price(spot, strike, 2.0 / 365.0)

                pos["current_price"] = cur_px
                pnl = round((cur_px - entry_px) * qty, 2)
                pos["unrealized_pnl"] = pnl

                # Evaluate Exits
                exit_triggered = False
                exit_reason = ""
                exit_px = cur_px

                if tp_px > 0 and cur_px >= tp_px:
                    exit_triggered = True
                    exit_reason = "TAKE_PROFIT"
                    exit_px = tp_px
                elif sl_px > 0 and cur_px <= sl_px:
                    exit_triggered = True
                    exit_reason = "STOP_LOSS"
                    exit_px = sl_px

                if exit_triggered:
                    modified = True
                    gross_pnl = round((exit_px - entry_px) * qty, 2)
                    charges = OptionsCharges.calculate(entry_px, exit_px, int(qty))
                    net_pnl = round(gross_pnl - charges, 2)

                    closed_record = {
                        "trade_id": pos.get("id", f"TRD-{int(time.time()*1000)}"),
                        "symbol": symbol,
                        "entry_price": entry_px,
                        "exit_price": exit_px,
                        "quantity": qty,
                        "gross_pnl": gross_pnl,
                        "charges_paid": charges,
                        "net_pnl": net_pnl,
                        "exit_reason": exit_reason,
                        "status": "PROFIT" if net_pnl > 0 else "LOSS",
                        "exit_time": datetime.datetime.now().isoformat(),
                    }
                    state.setdefault("closed_trades", []).append(closed_record)
                    state["cash"] = round(state.get("cash", 0.0) + (exit_px * qty) - charges, 2)
                    state["deployed"] = max(0.0, round(state.get("deployed", 0.0) - (entry_px * qty), 2))
                    state["total_gross_pnl"] = round(state.get("total_gross_pnl", 0.0) + gross_pnl, 2)
                    state["total_charges_paid"] = round(state.get("total_charges_paid", 0.0) + charges, 2)
                    state["total_net_pnl"] = round(state.get("total_net_pnl", 0.0) + net_pnl, 2)

                    action_msg = f"[{strat_id}] Position closed: {symbol} @ ₹{exit_px} ({exit_reason}) | Net PnL: ₹{net_pnl:+}"
                    logger.info(action_msg)
                    actions.append(action_msg)
                else:
                    remaining_positions.append(pos)

            if modified:
                state["open_positions"] = remaining_positions
                tmp_f = str(fpath) + ".tmp"
                with open(tmp_f, "w", encoding="utf-8") as fp:
                    json.dump(state, fp, indent=2)
                os.replace(tmp_f, fpath)

        # Periodic SQLite Sync
        now = time.time()
        if (now - self.last_sync_time) >= self.settings.db_sync_interval_sec:
            sync_count = sync_all_strategies(self.data_dir, self.db_path)
            self.last_sync_time = now
            logger.info(f"[DB SYNC] Synced {sync_count} strategies into {self.db_path}")

        return {"status": "SUCCESS", "actions": actions}
