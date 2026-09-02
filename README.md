# AI Hedge Fund Bot

Autonomous multi-asset AI-powered trading bot with FastAPI cyberpunk dashboard.

## Features

- 🤖 **AI Brain** — OpenRouter-compatible LLM makes BUY/SELL/HOLD decisions on macro (Daily) + micro (H1) data
- 📊 **Multi-Asset** — EUR/USD, GBP/USD, BTC/USD, XAU/USD, USD/JPY (configurable)
- 🎯 **Dynamic Risk** — Percentage-based SL/TP with MT5 digit-aware rounding
- 📈 **FastAPI Dashboard** — Cyberpunk UI with live equity, AI logic, confidence, execution feed
- 🧬 **Strategy Evolution** — Inside-bar-gated strategies with approval workflow
- 📝 **Paper Mode** — Test without real orders (default)

## Setup

```bash
# Clone
git clone https://github.com/Dkpiec/AI-Hedgefund-bot.git
cd AI-Hedgefund-bot

# Virtual environment
python -m venv venv
source venv/bin/activate   # (Windows: venv\Scripts\activate)

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your OPENROUTER_API_KEY and MT5 demo credentials

# Run
cd src
python main.py
# Open http://localhost:8000
```

## Architecture

```
src/
├── config.py                 # Central config (LLM, MT5, symbols, SL/TP)
├── ai_brain.py               # LLM decision engine (OpenRouter)
├── execution.py              # MT5 trade executor with digit rounding
├── main.py                   # FastAPI app + async trading loop
├── data/
│   └── data_engine.py        # MT5 multi-timeframe data fetcher
├── dashboard/
│   └── templates/index.html  # Cyberpunk UI
└── evolution/
    └── strategies/
        ├── base.py           # Strategy ABC with mandatory inside-bar gate
        └── inside_bar_rsi.py # Reference strategy
```

## Approval Workflow

Candidate IDs follow `CANDIDATE-YYYY-MM-XXX`. Approve via:
```
APPROVE CANDIDATE-2026-09-001 FOR PAPER TRADING
```

## Free Resources

- **OpenRouter** — free LLM models (DeepSeek V3.1, Llama 3.3, etc.)
- **MT5 Demo** — MetaQuotes demo account (free, $100K virtual)
- **Yahoo Finance** — 5y OHLCV for backtesting (via the backtest scripts)

## Status

🚧 Initial scaffold. Fill in `.env` with API keys and MT5 credentials to go live.
