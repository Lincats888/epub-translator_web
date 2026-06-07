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

echo  [1/5] Checking dependencies...
pip install -q fastapi "uvicorn[standard]" python-multipart openai pyyaml beautifulsoup4 lxml tqdm cryptography langdetect python-docx "PyMuPDF>=1.24.0" easyocr opencv-python numpy Pillow 2>nul

echo  [2/5] Setting up BabelDOC engine...
python -c "import babeldoc" 2>nul
if errorlevel 1 (
    echo  Installing BabelDOC (one-time setup)...
    pip install -q BabelDOC -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com 2>nul
    python -c "import babeldoc" 2>nul
    if not errorlevel 1 (
        echo    BabelDOC: ready
    ) else (
        echo    BabelDOC: not available (PDF engine will fall back to others)
    )
) else (
    echo    BabelDOC: ready
)

echo  [3/5] Setting up PDFMathTranslate engine...
where pdf2zh >nul 2>&1
if not errorlevel 1 (
    echo    PDFMathTranslate: ready ^(PATH^)
) else if exist "%USERPROFILE%\.local\bin\pdf2zh.exe" (
    echo    PDFMathTranslate: ready ^(uv^)
) else (
    echo  Installing PDFMathTranslate (one-time setup)...
    pip install -q pdf2zh 2>nul
    where pdf2zh >nul 2>&1
    if errorlevel 1 (
        echo  Installing via uv...
        pip install -q uv 2>nul
        python -m uv tool install --python 3.12 pdf2zh 2>nul
    )
    where pdf2zh >nul 2>&1
    if not errorlevel 1 (
        echo    PDFMathTranslate: ready
    ) else if exist "%USERPROFILE%\.local\bin\pdf2zh.exe" (
        echo    PDFMathTranslate: ready
    ) else (
        echo    PDFMathTranslate: not available (PDF engine will fall back to native)
    )
)

echo  [4/5] Opening browser...
start http://localhost:8080

echo  [5/5] Starting server...
echo.
echo  ================================================
echo    Browser opened. Close this window to stop.
echo  ================================================
echo.

python -m uvicorn server.server:app --host 127.0.0.1 --port 8080

pause
