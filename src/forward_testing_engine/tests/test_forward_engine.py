"""Comprehensive Unit and Integration Tests for Forward Testing Engine."""

import pytest
import sqlite3
import tempfile
from pathlib import Path

from pricing.black_scholes import norm_cdf, bs_call_price, bs_put_price, bs_greeks
from pricing.charges import OptionsCharges, EquityCharges, CurrencyCharges, CryptoCharges
from database.sync_db import init_database, sync_all_strategies
from engine.orchestrator import ForwardTestingOrchestrator


def test_black_scholes_analytical_precision():
    # Spot = 24000, Strike = 24000, T = 1 day, r = 7%, sigma = 15%
    call_px = bs_call_price(24000.0, 24000.0, 1.0 / 365.0, 0.07, 0.15)
    put_px = bs_put_price(24000.0, 24000.0, 1.0 / 365.0, 0.07, 0.15)
    assert call_px > 0.0
    assert put_px > 0.0
    assert abs(call_px - put_px) < 10.0  # Near-ATM parity

    # Greeks
    greeks = bs_greeks(24000.0, 24000.0, 1.0 / 365.0, 0.07, 0.15, "CE")
    assert 0.40 <= greeks["delta"] <= 0.60
    assert greeks["gamma"] > 0.0


def test_statutory_charges_calculator():
    # Options charges: ₹20 buy + ₹20 sell + STT
    opt_fee = OptionsCharges.calculate(entry_price=100.0, exit_price=150.0, qty=65)
    assert opt_fee > 40.0  # Must include brokerage + STT + GST
    assert opt_fee < 100.0

    # Equity charges
    eq_fee = EquityCharges.calculate(entry_price=500.0, exit_price=550.0, qty=100)
    assert eq_fee > 40.0

    # Crypto charges
    crypto_fee = CryptoCharges.calculate(entry_price=2500.0, exit_price=2600.0, qty=0.1, is_maker=True)
    assert crypto_fee > 0.0
    assert crypto_fee < 1.0


def test_sqlite_sync():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_trading.db"
        data_dir = Path(tmpdir) / "data"
        data_dir.mkdir()

        # Write sample strategy state
        strat_json = data_dir / "sample_strategy_state.json"
        strat_json.write_text(
            '{"strategy_id": "sample_strat", "name": "Sample Strategy", "initial_capital": 200000.0, "cash": 200000.0, "deployed": 0.0}'
        )

        synced = sync_all_strategies(data_dir, db_path)
        assert synced == 1

        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT strategy_id, name, cash FROM ft_strategies WHERE strategy_id='sample_strat'")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "sample_strat"
            assert row[1] == "Sample Strategy"
            assert row[2] == 200000.0


def test_orchestrator_listing():
    orch = ForwardTestingOrchestrator()
    strategies = orch.list_strategies()
    assert isinstance(strategies, list)
    assert len(strategies) >= 10  # 15 strategies exist
