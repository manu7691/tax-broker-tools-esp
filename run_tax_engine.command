#!/bin/bash

# Ensure we are in the directory of the script
cd "$(dirname "$0")"

echo "=========================================="
echo "   Spanish Tax Engine - Setup & Run"
echo "=========================================="

# 1. Find a suitable Python 3 version (>= 3.10)
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &> /dev/null; then
        ver=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
        if [ $? -eq 0 ]; then
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then
                PYTHON_CMD="$cmd"
                break
            fi
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "Error: Python >= 3.10 is required, but not found."
    echo "Please install Python 3.10+ (e.g., via 'brew install python@3.12' or from python.org)."
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Using Python executable: $PYTHON_CMD ($($PYTHON_CMD --version))"

# 2. Create Virtual Environment if missing
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment (.venv)..."
    "$PYTHON_CMD" -m venv .venv
    if [ $? -ne 0 ]; then
        echo "Error creating virtual environment."
        read -p "Press Enter to exit..."
        exit 1
    fi
fi

# Define paths to venv executables
PYTHON_BIN=".venv/bin/python3"
PLAYWRIGHT_BIN=".venv/bin/playwright"

# 3. Install Dependencies (using venv python directly)
echo "Checking/Installing dependencies..."
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel
"$PYTHON_BIN" -m pip install -e .
if [ $? -ne 0 ]; then
    echo "Error installing dependencies."
    read -p "Press Enter to exit..."
    exit 1
fi

# 4. Install Playwright Browsers
echo "Checking Playwright browsers..."
"$PLAYWRIGHT_BIN" install chromium
if [ $? -ne 0 ]; then
    echo "Error installing Playwright browsers."
    read -p "Press Enter to exit..."
    exit 1
fi

# 5. Main Menu Loop
while true; do
    clear
    echo "=========================================="
    echo "  Spanish Tax Engine for E-Trade & Revolut"
    echo "=========================================="
    echo "1. Login to E-Trade Plan (Required first)"
    echo "2. Download E-Trade Data (ESPP, Orders, RSU, Options, Dividends)"
    echo "3. Add Dividend/Interest Income (optional)"
    echo "4. Calculate Tax & PDF Reports (optional: incl. Revolut)"
    echo "5. Generate Charts & Tax Dashboard (optional: incl. Revolut)"
    echo "6. Calculate Crypto / Combined Report (Pionex/Binance)"
    echo ""
    echo "--- Simulation & Demo Data ---"
    echo "7. Run Demo: Calculate Tax & PDF Reports"
    echo "8. Run Demo: Generate Charts & Tax Dashboard"
    echo ""
    echo "9. Exit"
    echo "=========================================="
    read -p "Select an option (1-9): " choice

    case $choice in
        1)
            echo "------------------------------------------"
            echo "Running Login..."
            echo "A browser window will open. Please log in."
            echo "------------------------------------------"
            .venv/bin/tax-login
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        2)
            echo "------------------------------------------"
            echo "Downloading Data..."
            echo "------------------------------------------"
            .venv/bin/tax-download
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        3)
            echo "------------------------------------------"
            echo "Add Dividend/Interest Income"
            echo "Payments are stored in USD with their date and"
            echo "converted to EUR at the ECB rate when reports run."
            echo "------------------------------------------"
            read -p "Auto-download dividends from E*TRADE (needs login)? [y/N]: " auto_div
            case "$auto_div" in
                [Yy]*)
                    echo "Scraping E*TRADE dividends and importing them..."
                    .venv/bin/tax-download-dividends
                    .venv/bin/tax-import-dividends
                    ;;
                *)
                    echo "Manual entry: type each payment in USD with its date."
                    .venv/bin/tax-savings-income
                    ;;
            esac
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        4)
            echo "------------------------------------------"
            echo "Calculating Tax..."
            echo "(Optional: drop Revolut investment CSV(s) in input/revolut/*.csv.)"
            echo "------------------------------------------"
            read -p "Process ALL securities across brokers (portfolio mode)? [y/N]: " all_sec
            ENGINE_ARGS=""
            case "$all_sec" in
                [Yy]*)
                    ENGINE_ARGS="--all-securities"
                    echo "Portfolio mode: each security gets its own FIFO queue, rolled up into one savings base."
                    echo "Tip: add ISINs in input/securities.json ('isin_map') to merge the same stock across brokers."
                    ;;
                *)
                    echo "Single-security mode: the ticker in input/ticker.json (matching Revolut rows merge in)."
                    ;;
            esac
            .venv/bin/tax-engine $ENGINE_ARGS
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        5)
            echo "------------------------------------------"
            echo "Generate Charts & Tax Dashboard"
            echo "------------------------------------------"
            read -p "Process ALL securities across brokers (portfolio mode)? [y/N]: " all_sec
            CHART_ARGS=""
            case "$all_sec" in
                [Yy]*)
                    CHART_ARGS="--all-securities"
                    echo "Portfolio mode: will process all securities present in E*TRADE and Revolut data."
                    ;;
                *)
                    echo "Single-security mode: please specify configuration override details below (or press Enter to auto-detect)."
                    read -p "Enter stock ticker (or Enter to auto-detect/fallback to DT): " chart_ticker
                    if [ -n "$chart_ticker" ]; then
                        CHART_ARGS="--ticker $chart_ticker"
                        read -p "Enter company name (or Enter to fetch from Yahoo Finance): " chart_comp
                        if [ -n "$chart_comp" ]; then
                            CHART_ARGS="$CHART_ARGS --company-name \"$chart_comp\""
                        fi
                    fi
                    read -p "Enter current stock price in USD (or press Enter for live): " chart_price
                    if [ -n "$chart_price" ]; then
                        CHART_ARGS="$CHART_ARGS --current-price $chart_price"
                    fi
                    if [ ! -f "input/peers.json" ]; then
                        read -p "Peer tickers to compare, space-separated (or Enter for defaults DDOG ESTC): " peer_input
                        if [ -n "$peer_input" ]; then
                            CHART_ARGS="$CHART_ARGS --peers $peer_input"
                        fi
                    fi
                    ;;
            esac
            "$PYTHON_BIN" generate_charts.py $CHART_ARGS
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        6)
            echo "------------------------------------------"
            echo "Crypto Capital Gains (Spanish FIFO per coin)"
            echo "Drop exchange exports in input/crypto/pionex/trading.csv"
            echo "and/or input/crypto/binance/*Spot-Trade-History*.csv."
            echo "------------------------------------------"
            read -p "Combine crypto with your stocks into one savings base? [y/N]: " combine
            case "$combine" in
                [Yy]*)
                    echo "Combined report: merges stock + crypto gains/losses (bilingual HTML)."
                    .venv/bin/tax-combined
                    ;;
                *)
                    echo "Crypto-only report: per-coin console summary, CSV, and bilingual HTML."
                    .venv/bin/tax-crypto --input-dir input/crypto
                    ;;
            esac
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        7)
            echo "------------------------------------------"
            echo "Calculating Tax & PDF Report - demo data..."
            echo "------------------------------------------"
            read -p "Multi-symbol portfolio demo (several securities + a GBP one)? [y/N]: " demo_multi
            case "$demo_multi" in
                [Yy]*) .venv/bin/tax-demo --all-securities ;;
                *)     .venv/bin/tax-demo ;;
            esac
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        8)
            echo "------------------------------------------"
            echo "Generate Charts & Tax Dashboard - demo data"
            echo "------------------------------------------"
            read -p "Multi-symbol portfolio demo (shows the per-security chart)? [y/N]: " demo_multi
            case "$demo_multi" in
                [Yy]*) "$PYTHON_BIN" generate_charts.py --demo --all-securities ;;
                *)     "$PYTHON_BIN" generate_charts.py --demo ;;
            esac
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        9)
            echo "Exiting..."
            exit 0
            ;;
        *)
            echo "Invalid option."
            read -p "Press Enter to continue..."
            ;;
    esac
done

