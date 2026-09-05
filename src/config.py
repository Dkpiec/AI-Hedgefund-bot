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

# File where the paper-mode virtual USDT balance is persisted.
# Lives next to trading.db so PnL and balance survive restarts.
PAPER_BALANCE_FILE = Path(__file__).resolve().parent.parent / "data" / "paper_balance.txt"

# ============================================================================
# TRADING CONFIGURATION
# ============================================================================
# Timeframe options the user can pick from the dashboard dropdown
# 15m = chart candles (M15) the AI analyzes; user-approved 2026-09-03
CHART_TIMEFRAMES = ["15m", "1h"]
DEFAULT_CHART_TIMEFRAME = "15m"

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
DEFAULT_SCAN_INTERVAL = "5m"

# Minimum AI decision confidence score required to fire a trade (0-100)
CONFIDENCE_THRESHOLD = 60

# Maximum price filter (USD) — skip any symbol priced above this
# (excludes BTC, ETH, BNB by design; user-set on 2026-09-02)
MAX_PRICE_USD = float(os.getenv("MAX_PRICE_USD", "2000"))

# Minimum 24h volume filter (USDT) — only trade liquid assets
MIN_24H_VOLUME_USDT = float(os.getenv("MIN_24H_VOLUME_USDT", "25_000_000"))  # halved from 50M to expand universe

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
RISK_PER_TRADE = 0.06  # 6% of capital per trade (bumped from 5% to keep notional above $10 min after 1st trade)

# Trading fees (Binance spot/maker-taker)
MAKER_FEE = 0.0003  # 0.03%
TAKER_FEE = 0.0005  # 0.05%

# ============================================================================
# TIERED POSITION SIZING (user-set 2026-09-03)
# ============================================================================
# Tiered position sizing based on the running paper balance. Position size
# bumps +$10 every time the balance crosses the next $400 threshold.
#
#   Balance band       | Position size | Risk per trade (0.5% of size)
#   -------------------|---------------|------------------------------
#   $0     – $400      | $10           | $0.05
#   $400   – $800      | $20           | $0.10
#   $800   – $1200     | $30           | $0.15
#   $1200  – $1600     | $40           | $0.20
#   $1600  – $2000     | $50           | $0.25
#   $2000  – $2400     | $60           | $0.30
#   ... +$400 per tier | +$10 position | +$0.05 risk
#
# "Balance increases by $200 → position size +$10" but starting from the $200
# base, so the first threshold is $400 (= 2 × $200), the next is $800, etc.
#
# The risk per trade here is the MONEY risked on the trade (0.5% of position
# notional), NOT a percentage of account equity. This is informational and
# stored on each trade for the strategy_evolution module to grade strategies.
POSITION_TIER_SIZE = 10.0          # base position size ($10 at tier 0)
POSITION_TIER_BALANCE_STEP = 200   # $200 of balance growth per tier
POSITION_TIER_BASE_BALANCE = 200   # $200 is the "starting equity" — tier bumps every +$200
RISK_PCT_OF_POSITION = 0.005       # 0.5% of position size = money risked per trade


def get_position_size_for_balance(balance: float) -> float:
    """
    Return the position size (in USDT) for a given account balance, using the
    tiered schedule above. Each +$200 in balance above $200 bumps position size
    by +$10. So balance $0–$400 → $10, $400–$800 → $20, etc.
    """
    if balance < 0:
        return POSITION_TIER_SIZE
    growth = max(0.0, balance - POSITION_TIER_BASE_BALANCE)
    tier = int(growth // POSITION_TIER_BALANCE_STEP)
    return POSITION_TIER_SIZE * (1 + tier)


def get_risk_per_trade_for_balance(balance: float) -> float:
    """
    Return the money risked per trade (in USDT) for a given account balance:
    0.5% of the position size.
    """
    return get_position_size_for_balance(balance) * RISK_PCT_OF_POSITION


def get_current_tier(balance: float) -> int:
    """Return the current tier index (0-based). Tier 0 = $0–$400, tier 1 = $400–$800, etc."""
    if balance < 0:
        return 0
    growth = max(0.0, balance - POSITION_TIER_BASE_BALANCE)
    return int(growth // POSITION_TIER_BALANCE_STEP)

# Order management
ORDER_TIMEOUT_SECONDS = 300   # 5 min — cancel unfilled limit orders
STRICT_LIMIT_ORDERS = True    # market orders disabled; only limit orders

# Position management
# Only one open position per symbol at a time.
# Bot will not place a new order for a symbol that has an unfilled/filled
# order tracked in OPEN_ORDERS. User-set 2026-09-02.
ONE_POSITION_PER_SYMBOL = True

# Scanner cadence (seconds between full multi-timeframe scans)
DEFAULT_INTERVAL = 300        # 5 min (matches fastest timeframe)

# ============================================================================
# DASHBOARD CONFIGURATION
# ============================================================================
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8000

# HTTP Basic Auth for /protected routes (empty string disables)
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "Dkpiec")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "Amogh@123")

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================
DB_PATH = os.getenv("DB_PATH", "trading.db")
