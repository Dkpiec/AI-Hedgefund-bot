"""Main Service Entrypoint for Forward Testing and Paper Trading Engine.
Runs the continuous multi-asset surveillance loop and serves a lightweight
FastAPI monitoring interface on port 8504.
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
import uvicorn

from config.settings import get_settings
from engine.orchestrator import ForwardTestingOrchestrator

log_file = Path("/opt/data/forward-testing-engine/logs/forward_engine.log")
log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(log_file), mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("forward_engine.main")

settings = get_settings()
orchestrator = ForwardTestingOrchestrator()

app = FastAPI(
    title="Forward Testing & Paper Trading Engine",
    description="Multi-Asset Institutional Paper Trading & Forward Testing Orchestrator",
    version="1.0.0",
)


@app.get("/health")
def health():
    return {
        "status": "ONLINE",
        "engine": "ForwardTestingEngine",
        "version": "1.0.0",
        "strategies_count": len(orchestrator.list_strategies()),
    }


@app.get("/strategies")
def get_strategies():
    return {
        "count": len(orchestrator.list_strategies()),
        "strategies": orchestrator.list_strategies(),
    }


@app.post("/evaluate")
def trigger_evaluation():
    res = orchestrator.run_surveillance_tick()
    return res


async def background_surveillance_worker():
    """Continuous background loop evaluating positions and syncing states."""
    logger.info(f"[WORKER] Starting background surveillance loop (interval: {settings.evaluation_interval_sec}s)...")
    while True:
        try:
            orchestrator.run_surveillance_tick()
        except Exception as e:
            logger.error(f"[WORKER] Surveillance loop error: {e}")
        await asyncio.sleep(settings.evaluation_interval_sec)


@app.on_event("startup")
async def on_startup():
    logger.info("Initializing Forward Testing Engine...")
    asyncio.create_task(background_surveillance_worker())


if __name__ == "__main__":
    logger.info(f"Starting Forward Testing Engine on {settings.host}:{settings.port}...")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
