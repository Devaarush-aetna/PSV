@echo off
title PSV Verification Runner
cd /d %~dp0

echo ============================================================
echo  CVS Health - PSV Verification Runner
echo ============================================================
echo.

REM Check that the venv python is accessible
.venv\Scripts\python.exe --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Virtual environment not found or Python not accessible.
    echo  Expected: %~dp0.venv\Scripts\python.exe
    echo ============================================================
    pause
    exit /b 1
)

echo  Installing / verifying dependencies...
.venv\Scripts\python.exe -m pip install streamlit pandas plotly openpyxl -q ^
    --proxy http://proxy:9119 ^
    --trusted-host pypi.org ^
    --trusted-host files.pythonhosted.org ^
    --trusted-host repo-man.aetna.com ^
    2>nul

echo  Starting PSV Verification Runner...
echo  The browser will open automatically at http://localhost:8501
echo.
echo  To stop the server, close this window or press Ctrl+C
echo ============================================================
echo.

.venv\Scripts\python.exe -m streamlit run lvs\adapters\scrapers\engine\psv_ui.py --server.port 8501 --server.headless false

pause
