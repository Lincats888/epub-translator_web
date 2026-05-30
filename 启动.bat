@echo off
title EPUB Translator

echo.
echo  ================================================
echo    EPUB Translator
echo  ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Please install Python 3.10+
    echo  Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo  [1/3] Checking dependencies...
pip install -q fastapi uvicorn[standard] python-multipart openai pyyaml beautifulsoup4 lxml tqdm 2>nul

echo  [2/3] Opening browser...
start http://localhost:8080

echo  [3/3] Starting server...
echo.
echo  ================================================
echo    Browser opened. Close this window to stop.
echo  ================================================
echo.

python -m uvicorn server.server:app --host 127.0.0.1 --port 8080

pause