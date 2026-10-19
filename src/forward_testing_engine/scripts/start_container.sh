#!/bin/bash
set -e

LOG_FILE="/opt/data/forward-testing-engine/logs/forward_boot.log"
mkdir -p /opt/data/forward-testing-engine/logs

log() {
    echo "[FORWARD CONTAINER BOOT $(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "Container started. Installing dependencies..."
pip install --no-cache-dir fastapi uvicorn pydantic requests >> "$LOG_FILE" 2>&1

log "Launching Forward Testing Engine..."
cd /opt/data/forward-testing-engine
export PYTHONPATH=/opt/data/forward-testing-engine
export PORT=8504
export HOST=0.0.0.0

exec python -m engine.main
