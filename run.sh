#!/bin/bash
# PixelForge Pro — Start Script (Linux/macOS)
cd "$(dirname "$0")/backend"
echo "=== PixelForge Pro v6 ==="
echo "Checking dependencies..."
python -m pip install -q fastapi uvicorn httpx python-multipart 2>&1 | tail -2
echo "Starting server at http://localhost:8000"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
