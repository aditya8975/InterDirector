@echo off
:: PixelForge Pro — Start Script (Windows)
cd /d "%~dp0backend"
echo === PixelForge Pro v6 ===
echo Checking dependencies...
pip install -q fastapi uvicorn httpx python-multipart
echo Starting server at http://localhost:8000
echo Open http://localhost:8000 in your browser
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
