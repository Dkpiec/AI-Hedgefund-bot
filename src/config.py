"""
AI Hedge Fund Bot - Configuration
==================================
Central configuration for the autonomous trading bot.
Supports OpenRouter (multi-model) or direct DeepSeek/OpenAI/Anthropic APIs.
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
# if "openrouter/free" is set, the AI brain picks the first free model from /models
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")

# Legacy direct API keys (optional fallback)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ============================================================================
# MT5 DEMO ACCOUNT CONFIGURATION
# ============================================================================
MT5_ACCOUNT = int(os.getenv("MT5_ACCOUNT", "0"))           # Your MT5 demo account number
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")               # Your MT5 password
MT5_SERVER = os.getenv("MT5_SERVER", "MetaQuotes-Demo")    # e.g. MetaQuotes-Demo

# Starting demo account balance (used when MT5 is not connected / paper mode)
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "100"))

# ============================================================================
# TRADING CONFIGURATION
# ============================================================================
# Multi-asset symbol universe (MT5 symbol names)
SYMBOLS = [
    "EURUSDm",   # Euro / US Dollar
    "GBPUSDm",   # British Pound / US Dollar
    "BTCUSDm",   # Bitcoin / US Dollar
    "XAUUSDm",   # Gold / US Dollar
    "USDJPYm",   # US Dollar / Japanese Yen
]

# Risk management (percentage-based)
SL_PERCENT = 0.002     # 0.2% stop loss
TP_PERCENT = 0.004     # 0.4% take profit (2:1 R:R)

# Position sizing (lots)
LOT_SIZE = 0.01        # Conservative starting size for demo

# Trading interval (seconds)
DEFAULT_INTERVAL = 30

# Paper trading mode (no real orders)
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"

# ============================================================================
# DASHBOARD CONFIGURATION
# ============================================================================
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8000

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================
DB_PATH = os.getenv("DB_PATH", "trading.db")
