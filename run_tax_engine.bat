@echo off
cd /d "%~dp0"

echo ==========================================
echo    Spanish Tax Engine - Setup ^& Run
echo ==========================================
echo Working directory: %CD%

REM Check that pyproject.toml exists
if not exist "pyproject.toml" (
    echo ERROR: pyproject.toml not found in %CD%
    echo.
    echo Make sure you extracted the ENTIRE project folder and are
    echo running this script from inside the tax-engine directory.
    pause
    exit /b 1
)

REM 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

REM 2. Create Virtual Environment
if not exist ".venv" (
    echo Creating Python virtual environment...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo Error creating virtual environment.
        pause
        exit /b 1
    )
)

REM Define paths
set PYTHON_BIN=.venv\Scripts\python.exe
set PIP_BIN=.venv\Scripts\pip.exe
set PLAYWRIGHT_BIN=.venv\Scripts\playwright.exe

REM 3. Install Dependencies
echo Checking/Installing dependencies...
"%PYTHON_BIN%" -m pip install --upgrade pip setuptools wheel
"%PYTHON_BIN%" -m pip install -e .
if %errorlevel% neq 0 (
    echo Error installing dependencies.
    pause
    exit /b 1
)

REM 4. Install Playwright Browsers
echo Checking Playwright browsers...
"%PLAYWRIGHT_BIN%" install chromium
if %errorlevel% neq 0 (
    echo Error installing Playwright browsers.
    pause
    exit /b 1
)

:menu
cls
echo ==========================================
echo   Spanish Tax Engine for E-Trade ^& Revolut
echo ==========================================
echo 1. Login to E-Trade Plan (Required first)
echo 2. Download E-Trade Data (ESPP, Orders, RSU, Options)
echo 3. Add Dividend/Interest Income (optional)
echo 4. Calculate Tax ^& PDF Reports (optional: incl. Revolut)
echo 5. Generate Charts ^& Tax Dashboard (optional: incl. Revolut)
echo.
echo --- Simulation ^& Demo Data ---
echo 6. Run Demo: Calculate Tax ^& PDF Reports
echo 7. Run Demo: Generate Charts ^& Tax Dashboard
echo.
echo 8. Exit
echo ==========================================
set /p choice="Select an option (1-8): "

if "%choice%"=="1" (
    echo Running Login...
    .venv\Scripts\tax-login
    pause
    goto menu
)
if "%choice%"=="2" (
    echo Downloading Data...
    .venv\Scripts\tax-download
    pause
    goto menu
)
if "%choice%"=="3" (
    echo Add Dividend/Interest Income ^(USD + date, converted at ECB rate^)...
    .venv\Scripts\tax-savings-income
    pause
    goto menu
)
if "%choice%"=="4" (
    echo Calculating Tax...
    echo (Optional: drop a Revolut investment CSV in input\revolut\*.csv.
    echo  Set the tracked ticker (and ISIN if available^) in input\ticker.json so
    echo  same-stock rows merge into the FIFO pool; other tickers are ignored.^)
    .venv\Scripts\tax-engine
    pause
    goto menu
)
if "%choice%"=="5" (
    echo ------------------------------------------
    echo Generate Charts ^& Tax Dashboard
    echo (Defaults: auto-detected ticker/company. Peers: edit input\peers.json)
    echo ------------------------------------------
    set CHART_ARGS=
    set /p chart_ticker="Enter stock ticker (or Enter to auto-detect/fallback to DT): "
    if defined chart_ticker set CHART_ARGS=--ticker %chart_ticker%
    set /p chart_comp="Enter company name (or Enter to fetch from Yahoo Finance): "
    if defined chart_comp set CHART_ARGS=%CHART_ARGS% --company-name "%chart_comp%"
    set /p chart_price="Enter current stock price in USD (or press Enter for live): "
    if defined chart_price set CHART_ARGS=%CHART_ARGS% --current-price %chart_price%
    if not exist "input\peers.json" (
        set /p peer_input="Peer tickers, space-separated (or Enter for defaults DDOG ESTC): "
        if defined peer_input set CHART_ARGS=%CHART_ARGS% --peers %peer_input%
    )
    "%PYTHON_BIN%" generate_charts.py %CHART_ARGS%
    pause
    goto menu
)
if "%choice%"=="6" (
    echo Calculating Tax & PDF Report - demo data...
    .venv\Scripts\tax-demo
    pause
    goto menu
)
if "%choice%"=="7" (
    echo ------------------------------------------
    echo Generate Charts ^& Tax Dashboard - demo data
    echo ------------------------------------------
    "%PYTHON_BIN%" generate_charts.py --demo
    pause
    goto menu
)
if "%choice%"=="8" (
    exit /b 0
)

echo Invalid option.
pause
goto menu

