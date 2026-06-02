#!/bin/bash
# EPUB Translator — Linux/Mac launcher
set -e

echo
echo " ================================================"
echo "   EPUB Translator"
echo " ================================================"
echo

# Check Python
if ! command -v python3 &>/dev/null; then
    echo " [ERROR] Python3 not found. Please install Python 3.10+"
    echo "         sudo apt install python3 python3-pip  (Debian/Ubuntu)"
    exit 1
fi

echo " [1/5] Checking dependencies..."
pip3 install -q fastapi "uvicorn[standard]" python-multipart openai pyyaml beautifulsoup4 lxml tqdm 2>/dev/null

echo " [2/5] Setting up BabelDOC engine..."
if python3 -c "import babeldoc" 2>/dev/null; then
    echo "   BabelDOC: ready"
else
    echo "  Installing BabelDOC (one-time setup)..."
    pip3 install -q BabelDOC 2>/dev/null || {
        pip3 install -q uv 2>/dev/null
        python3 -m uv pip install BabelDOC 2>/dev/null
    }
    python3 -c "import babeldoc" 2>/dev/null && echo "   BabelDOC: ready" || echo "   BabelDOC: not available"
fi

echo " [3/5] Setting up PDFMathTranslate engine..."
if ! command -v pdf2zh &>/dev/null && ! [ -f "$HOME/.local/bin/pdf2zh" ]; then
    echo "  Installing PDFMathTranslate (one-time setup)..."
    pip3 install -q pdf2zh 2>/dev/null || {
        pip3 install -q uv 2>/dev/null
        python3 -m uv tool install --python 3.12 pdf2zh 2>/dev/null
    }
fi
if command -v pdf2zh &>/dev/null || [ -f "$HOME/.local/bin/pdf2zh" ]; then
    echo "   PDFMathTranslate: ready"
else
    echo "   PDFMathTranslate: not available (PDF engine will fall back to native)"
fi

echo " [4/5] Opening browser..."
if command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:8080
elif command -v open &>/dev/null; then
    open http://localhost:8080
fi

echo " [5/5] Starting server..."
echo
echo " ================================================"
echo "   Browser opened. Ctrl+C to stop."
echo " ================================================"
echo

python3 -m uvicorn server.server:app --host 127.0.0.1 --port 8080
