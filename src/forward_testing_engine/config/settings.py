"""Configuration settings for Forward Testing Engine."""

import os
from pathlib import Path
from dataclasses import dataclass

@dataclass
class ForwardEngineSettings:
    data_dir: Path = Path(os.environ.get("FORWARD_DATA_DIR", "/opt/data/trading-agent/data/forward_testing"))
    db_path: Path = Path(os.environ.get("TRADING_DB_PATH", "/opt/data/trading-agent/dashboard_v2/trading.db"))
    secrets_path: Path = Path(os.environ.get("SECRETS_PATH", "/opt/data/secrets/coolify.env"))
    evaluation_interval_sec: float = float(os.environ.get("EVAL_INTERVAL_SEC", "15.0"))
    db_sync_interval_sec: float = float(os.environ.get("SYNC_INTERVAL_SEC", "60.0"))
    port: int = int(os.environ.get("PORT", "8504"))
    host: str = os.environ.get("HOST", "0.0.0.0")

def get_settings() -> ForwardEngineSettings:
    return ForwardEngineSettings()
