"""
AI Hedge Fund Bot - Configuration
==================================
Central configuration for the autonomous trading bot.
Binance Spot Testnet mode by default (Linux-friendly, no broker install).
"""
import os
from pathlib import Path

# Auto-load .env from project root (one level up from src/)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv not installed; rely on real env vars

# ============================================================================
# LLM API CONFIGURATION
# ============================================================================
# OpenRouter (supports free models + multiple providers)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "your-openrouter-api-key-here")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Default model: auto-resolve to the current OpenRouter free model at runtime
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")

# Legacy direct API keys (optional fallback)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ============================================================================
# BINANCE SPOT TESTNET CONFIGURATION
# ============================================================================
# Testnet endpoint: https://testnet.binance.vision
# Get free testnet API keys at: https://testnet.binance.vision/
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

# Starting testnet account balance in USDT
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "200"))

# ============================================================================
# TRADING CONFIGURATION
# ============================================================================
# Timeframe options the user can pick from the dashboard dropdown
# 1h = chart candles (H1) the AI analyzes; user-approved default 2026-09-02
CHART_TIMEFRAMES = ["1h"]
DEFAULT_CHART_TIMEFRAME = "1h"

# Loop cycle intervals (how often the AI re-checks the market)
# Dropdown: 1 min, 5 min, 15 min, 30 min, 1 hour
SCAN_INTERVALS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}
SCAN_INTERVAL_OPTIONS = ["1m", "5m", "15m", "30m", "1h"]
DEFAULT_SCAN_INTERVAL = "1h"

# Maximum price filter (USD) — skip any symbol priced above this
# (excludes BTC, ETH, BNB by design; user-set on 2026-09-02)
MAX_PRICE_USD = float(os.getenv("MAX_PRICE_USD", "2000"))

# Minimum 24h volume filter (USDT) — only trade liquid assets
MIN_24H_VOLUME_USDT = float(os.getenv("MIN_24H_VOLUME_USDT", "50_000_000"))

# Candidate universe — filtered at startup by price + volume rules
# Conservative list of liquid USDT pairs that usually price under $2000
CANDIDATE_SYMBOLS = [
    "SOLUSDT",    # Solana — liquid, mid-cap
    "XRPUSDT",    # Ripple — high volume
    "ADAUSDT",    # Cardano
    "DOGEUSDT",   # Dogecoin
    "AVAXUSDT",   # Avalanche
    "LINKUSDT",   # Chainlink
    "DOTUSDT",    # Polkadot
    "MATICUSDT",  # Polygon
    "LTCUSDT",    # Litecoin
    "NEARUSDT",   # Near Protocol
    "ATOMUSDT",   # Cosmos
    "ALGOUSDT",   # Algorand
    "XLMUSDT",    # Stellar
    "VETUSDT",    # VeChain
    "ICPUSDT",    # Internet Computer
]

# These get populated by data_engine._filter_universe() at startup
SYMBOLS = []

# Risk management
# Crypto is 5-10x more volatile than Forex, so we need wider stops and targets
SL_PERCENT = 0.0075   # 0.75% stop loss
TP_PERCENT = 0.015    # 1.5% take profit (2:1 R:R)
RISK_PER_TRADE = 0.05  # 5% of capital per trade (user-set 2026-09-02)

# Order management
ORDER_TIMEOUT_SECONDS = 300   # 5 min — cancel unfilled limit orders
STRICT_LIMIT_ORDERS = True    # market orders disabled; only limit orders

# Scanner cadence (seconds between full multi-timeframe scans)
DEFAULT_INTERVAL = 300        # 5 min (matches fastest timeframe)

# ============================================================================
# DASHBOARD CONFIGURATION
# ============================================================================
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8000

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================
DB_PATH = os.getenv("DB_PATH", "trading.db")
