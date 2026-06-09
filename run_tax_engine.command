#!/bin/bash

# Ensure we are in the directory of the script
cd "$(dirname "$0")"

echo "=========================================="
echo "   Spanish Tax Engine - Setup & Run"
echo "=========================================="

# 1. Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed."
    echo "Please install Python 3 from https://www.python.org/downloads/"
    echo "or run 'brew install python' if you have Homebrew."
    read -p "Press Enter to exit..."
    exit 1
fi

# 2. Create Virtual Environment if missing
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment (.venv)..."
    python3 -m venv .venv
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
    echo "   Spanish Tax Engine for E-Trade"
    echo "=========================================="
    echo "1. Login to E-Trade (Required first)"
    echo "2. Download All Data (ESPP, Orders, RSU, Options)"
    echo "3. Calculate Tax (incl. optional Revolut CSV)"
    echo "4. Add Dividend/Interest Income (optional)"
    echo "5. Run Demo"
    echo "6. Generate Charts & Tax Dashboard"
    echo "7. Exit"
    echo "=========================================="
    read -p "Select an option (1-7): " choice

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
            echo "Calculating Tax..."
            echo "(Optional: drop a Revolut investment CSV in input/revolut/*.csv."
            echo " Set the tracked ticker (and ISIN if available) in input/ticker.json"
            echo " so same-stock rows merge into the FIFO pool; other tickers are ignored.)"
            echo "------------------------------------------"
            .venv/bin/tax-engine
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        4)
            echo "------------------------------------------"
            echo "Add Dividend/Interest Income"
            echo "Enter each payment in USD with its date;"
            echo "it is converted to EUR at the ECB rate."
            echo "------------------------------------------"
            .venv/bin/tax-savings-income
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        5)
            echo "------------------------------------------"
            echo "Running Demo..."
            echo "------------------------------------------"
            .venv/bin/tax-demo
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        6)
            echo "------------------------------------------"
            echo "Generate Charts & Tax Dashboard"
            echo "(Defaults: auto-detected ticker/company. Peers: edit input/peers.json)"
            echo "------------------------------------------"
            CHART_ARGS=""
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
            "$PYTHON_BIN" generate_charts.py $CHART_ARGS
            echo ""
            read -p "Press Enter to return to menu..."
            ;;
        7)
            echo "Exiting..."
            exit 0
            ;;
        *)
            echo "Invalid option."
            read -p "Press Enter to continue..."
            ;;
    esac
done

