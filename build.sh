#!/usr/bin/env bash
# Build script for Render
# Install Python dependencies into the project (system Python on Render)
set -e

pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Build complete. AI Hedge Fund Bot ready to start."
