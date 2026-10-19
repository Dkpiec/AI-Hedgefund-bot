# Forward Testing & Paper Trading Engine

Institutional-grade, multi-asset forward testing and paper trading execution engine.

## Overview
Coordinates simulated executions, real-time mark-to-market pricing, trailing bracket management, and exact statutory charges calculations across 15 forward-testing strategies:
- **Equities:** Wyckoff VSA Pro, Wyckoff Confirmed Spring
- **Index Options:** ORB-ITM Invalidation Sniper (Nifty / BankNifty / Sensex), Nifty 09:20 Dynamic Straddle
- **Commodities:** MCX Gold 1H Trend Breakout, MCX Crude Oil Iron Condor, MCX Natural Gas Asymmetric Condor
- **Forex / CDS:** NSE CDS Currency Sniper (USDINR / EURINR), Forex D1 Macro Apex Sniper
- **Crypto Options & Futures:** Bitcoin Asymmetric Iron Condor, Bitcoin 1-DTE Profit Escalator, BTC Momentum Breakout 2x, 0-DTE Trend-Skewed Condor, ETH 3-DTE Vol-Harvest Strangle

## Architecture
- **Analytical Pricing:** Standard library Black-Scholes model (`0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))`) without scipy dependency crashes.
- **Charges Engine:** Exact Indian exchange statutory charges (brokerage, STT, turnover fee, GST, stamp duty, SEBI turnover) and Binance maker/taker fees.
- **SQLite Persistence:** Continuous synchronization of all strategy metrics, open positions, closed trades, and equity curves into `dashboard_v2/trading.db`.
- **FastAPI Endpoint:** Serves `/health`, `/strategies`, and `/evaluate` on port `8504`.

## Running Tests
```bash
PYTHONPATH=. pytest tests/ -v
```
