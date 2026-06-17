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
echo 2. Download E-Trade Data (ESPP, Orders, RSU, Options, Dividends)
echo 3. Add Dividend/Interest Income (optional)
echo 4. Calculate Tax ^& PDF Reports (optional: incl. Revolut)
echo 5. Generate Charts ^& Tax Dashboard (optional: incl. Revolut)
echo 6. Calculate Crypto / Combined Report (Pionex/Binance)
echo.
echo --- Simulation ^& Demo Data ---
echo 7. Run Demo: Calculate Tax ^& PDF Reports
echo 8. Run Demo: Generate Charts ^& Tax Dashboard
echo.
echo 9. Exit
echo ==========================================
set /p choice="Select an option (1-9): "

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
REM Add Dividend/Interest Income uses a label so the freshly-read prompt variable
REM expands correctly (see the :calc_tax note below).
if "%choice%"=="3" goto add_income
REM Calculate Tax uses a label (not an inline block) so the freshly-read prompt
REM variable expands correctly — %var% set with set /p inside a parenthesised
REM block is parse-time expanded (empty), which would break the toggle.
if "%choice%"=="4" goto calc_tax
if "%choice%"=="5" goto gen_charts
REM Crypto and demo options use labels so the freshly-read prompt variable
REM expands correctly (see the :calc_tax note below).
if "%choice%"=="6" goto calc_crypto
if "%choice%"=="7" goto demo_tax
if "%choice%"=="8" goto demo_charts
if "%choice%"=="9" (
    exit /b 0
)

echo Invalid option.
pause
goto menu

:gen_charts
echo ------------------------------------------
echo Generate Charts ^& Tax Dashboard
echo ------------------------------------------
set "CHART_ARGS="
set /p all_sec="Process ALL securities across brokers (portfolio mode)? [y/N]: "
if /i "%all_sec%"=="y" (
    set "CHART_ARGS=--all-securities"
    echo Portfolio mode: will process all securities present in E*TRADE and Revolut data.
) else (
    echo Single-security mode: please specify configuration override details below (or press Enter to auto-detect).
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
)
"%PYTHON_BIN%" generate_charts.py %CHART_ARGS%
pause
goto menu

:add_income
echo ------------------------------------------
echo Add Dividend/Interest Income
echo Payments are stored in USD with their date and
echo converted to EUR at the ECB rate when reports run.
echo ------------------------------------------
set /p auto_div="Auto-download dividends from E*TRADE (needs login)? [y/N]: "
if /i "%auto_div%"=="y" (
    echo Scraping E*TRADE dividends and importing them...
    .venv\Scripts\tax-download-dividends
    .venv\Scripts\tax-import-dividends
) else (
    echo Manual entry: type each payment in USD with its date.
    .venv\Scripts\tax-savings-income
)
pause
goto menu

:calc_tax
echo ------------------------------------------
echo Calculating Tax...
echo (Optional: drop Revolut investment CSV^(s^) in input\revolut\*.csv.^)
echo ------------------------------------------
set "ENGINE_ARGS="
set /p all_sec="Process ALL securities across brokers (portfolio mode)? [y/N]: "
if /i "%all_sec%"=="y" set "ENGINE_ARGS=--all-securities"
if defined ENGINE_ARGS (
    echo Portfolio mode: each security gets its own FIFO queue, rolled up into one savings base.
    echo Tip: add ISINs in input\securities.json ^('isin_map'^) to merge the same stock across brokers.
) else (
    echo Single-security mode: the ticker in input\ticker.json ^(matching Revolut rows merge in^).
)
.venv\Scripts\tax-engine %ENGINE_ARGS%
pause
goto menu

:calc_crypto
echo ------------------------------------------
echo Crypto Capital Gains (Spanish FIFO per coin)
echo Drop exchange exports in input\crypto\pionex\trading.csv
echo and/or input\crypto\binance\*Spot-Trade-History*.csv.
echo ------------------------------------------
set /p combine="Combine crypto with your stocks into one savings base? [y/N]: "
if /i "%combine%"=="y" (
    echo Combined report: merges stock + crypto gains/losses ^(bilingual HTML^).
    .venv\Scripts\tax-combined
) else (
    echo Crypto-only report: per-coin console summary, CSV, and bilingual HTML.
    .venv\Scripts\tax-crypto --input-dir input/crypto
)
pause
goto menu

:demo_tax
echo ------------------------------------------
echo Calculating Tax ^& PDF Report - demo data...
echo ------------------------------------------
set "DEMO_ARGS="
set /p demo_multi="Multi-symbol portfolio demo (several securities + a GBP one)? [y/N]: "
if /i "%demo_multi%"=="y" set "DEMO_ARGS=--all-securities"
.venv\Scripts\tax-demo %DEMO_ARGS%
pause
goto menu

:demo_charts
echo ------------------------------------------
echo Generate Charts ^& Tax Dashboard - demo data
echo ------------------------------------------
set "DEMO_ARGS="
set /p demo_multi="Multi-symbol portfolio demo (shows the per-security chart)? [y/N]: "
if /i "%demo_multi%"=="y" set "DEMO_ARGS=--all-securities"
"%PYTHON_BIN%" generate_charts.py --demo %DEMO_ARGS%
pause
goto menu

