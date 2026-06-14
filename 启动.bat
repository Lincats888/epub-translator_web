@echo off
cd /d "%~dp0"
title EPUB Translator

echo.
echo  ================================================
echo    EPUB Translator
echo  ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Install Python 3.10+
    echo  https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo  [1/4] Checking dependencies...
pip install -q fastapi "uvicorn[standard]" python-multipart openai pyyaml beautifulsoup4 lxml tqdm cryptography langdetect python-docx "PyMuPDF>=1.24.0" easyocr opencv-python numpy Pillow 2>nul
echo    Dependencies: OK

echo  [2/4] Checking engines...
python -c "import babeldoc" 2>nul && (
    echo    BabelDOC: ready
) || (
    echo    Installing BabelDOC...
    pip install -q BabelDOC -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com 2>nul
    python -c "import babeldoc" 2>nul && echo    BabelDOC: ready || echo    BabelDOC: not available
)

where pdf2zh >nul 2>&1 && (
    echo    PDFMathTranslate: ready
) || (
    if exist "%USERPROFILE%\.local\bin\pdf2zh.exe" (
        echo    PDFMathTranslate: ready
    ) else (
        echo    PDFMathTranslate: not available ^(optional^)
    )
)

echo  [3/4] Starting server...
start "EPUB Translator Server" python -m uvicorn server.server:app --host 127.0.0.1 --port 8080

echo  [4/4] Waiting for server...
REM Wait until server is actually listening
:waitloop
timeout /t 2 /nobreak >nul
curl -s -o nul http://127.0.0.1:8080 2>nul && goto openbrowser
goto waitloop

:openbrowser
echo  Opening browser...
start http://localhost:8080

echo  ================================================
echo    Server is running at http://localhost:8080
echo    Close the server window to stop.
echo  ================================================
pause
