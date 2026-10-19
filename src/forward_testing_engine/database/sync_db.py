"""SQLite Database Synchronization Layer for Forward Testing Dashboard."""

import os
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Dict, Any, List

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ft_strategies (
    strategy_id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    initial_capital REAL,
    cash REAL,
    deployed REAL,
    total_net_pnl REAL,
    total_gross_pnl REAL,
    total_charges REAL,
    max_concurrent_positions INTEGER,
    per_trade_capital REAL,
    start_date TEXT,
    end_date TEXT,
    duration_days INTEGER,
    rules_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ft_open_positions (
    pos_id TEXT PRIMARY KEY,
    strategy_id TEXT,
    symbol TEXT,
    entry_date TEXT,
    entry_price REAL,
    quantity INTEGER,
    stop_loss REAL,
    take_profit REAL,
    current_price REAL,
    unrealized_pnl REAL,
    days_held INTEGER,
    margin_deployed REAL,
    raw_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(strategy_id) REFERENCES ft_strategies(strategy_id)
);

CREATE TABLE IF NOT EXISTS ft_closed_trades (
    trade_id TEXT PRIMARY KEY,
    strategy_id TEXT,
    symbol TEXT,
    entry_date TEXT,
    exit_date TEXT,
    entry_price REAL,
    exit_price REAL,
    quantity INTEGER,
    gross_pnl REAL,
    charges_paid REAL,
    net_pnl REAL,
    exit_reason TEXT,
    status TEXT,
    raw_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(strategy_id) REFERENCES ft_strategies(strategy_id)
);

CREATE TABLE IF NOT EXISTS ft_equity_curve (
    strategy_id TEXT,
    date TEXT,
    cash REAL,
    deployed REAL,
    total_equity REAL,
    net_pnl REAL,
    charges_paid REAL,
    PRIMARY KEY(strategy_id, date),
    FOREIGN KEY(strategy_id) REFERENCES ft_strategies(strategy_id)
);
"""


def init_database(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()


def sync_all_strategies(data_dir: Path, db_path: Path) -> int:
    """Sync all JSON state files from data_dir into SQLite database."""
    init_database(db_path)
    count = 0
    if not data_dir.exists():
        return 0

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        for fname in os.listdir(data_dir):
            if not fname.endswith("_state.json"):
                continue
            fpath = data_dir / fname
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                continue

            strat_id = state.get("strategy_id", fname.replace("_state.json", ""))
            cursor.execute(
                """
                INSERT INTO ft_strategies (
                    strategy_id, name, description, initial_capital, cash, deployed,
                    total_net_pnl, total_gross_pnl, total_charges, max_concurrent_positions,
                    per_trade_capital, start_date, end_date, duration_days, rules_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    cash=excluded.cash,
                    deployed=excluded.deployed,
                    total_net_pnl=excluded.total_net_pnl,
                    total_gross_pnl=excluded.total_gross_pnl,
                    total_charges=excluded.total_charges,
                    rules_json=excluded.rules_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    strat_id,
                    state.get("name", strat_id),
                    state.get("description", ""),
                    float(state.get("initial_capital", 200000.0)),
                    float(state.get("cash", 200000.0)),
                    float(state.get("deployed", 0.0)),
                    float(state.get("total_net_pnl", 0.0)),
                    float(state.get("total_gross_pnl", 0.0)),
                    float(state.get("total_charges_paid", 0.0)),
                    int(state.get("max_concurrent_positions", 5)),
                    float(state.get("per_trade_capital", 40000.0)),
                    state.get("start_date", ""),
                    state.get("end_date", ""),
                    int(state.get("duration_days", 180)),
                    json.dumps(state.get("rules", {})),
                ),
            )
            count += 1
        conn.commit()
    return count
