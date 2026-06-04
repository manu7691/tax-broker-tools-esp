import contextlib
import os
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

SESSION_FILE = "input/etrade_session.json"
TARGET_URL = "https://us.etrade.com/etx/sp/stockplan#/myAccount/orders"
OUTPUT_DIR = Path("input/orders")
OUTPUT_FILE = OUTPUT_DIR / "orders.xlsx"


def _get_execution_details(row, order_date: str, page) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Click the row to expand its detail, read execution dates and fees.
    Returns a dict with 'execution_date', 'Commission', 'SEC Fees', 'Disbursement Fee'.
    """
    details = {
        "execution_date": order_date,
        "Commission": "0",
        "SEC Fees": "0",
        "Disbursement Fee": "0"
    }

    # Click the expand chevron in the first cell of the row
    try:
        row.locator("td").first.click()
        # Be human-like, wait a bit for E-trade's backend
        time.sleep(1.5)
    except Exception as e:
        print(f"  WARNING: Could not click row to expand detail: {e}")
        return details

    try:
        # Wait for the Order History div to become visible (it may take a moment to load)
        order_history_div = page.locator('div[data-test-id="orders.ordertbl.odrhistoryexpand"]').first
        try:
            order_history_div.wait_for(state="visible", timeout=5000)
        except Exception:
            pass

        # --- EXTRACT FEES ---
        try:
            fees = {"Commission": "0", "SEC Fees": "0", "Brokerage Assist Fee": "0"}
            
            # Look for the specific Disbursement Details table based on user's exact DOM
            disb_table = page.locator('[data-test-id="orders.ordertbl.disbursementtbl"] table').first
            
            # Find the Disbursement Details toggle button
            disb_toggle = page.locator("text=Disbursement Details").first
            
            # E-Trade often auto-expands this section when the row is opened. 
            # If we click it when it's already expanded, we accidentally close it!
            if disb_toggle.is_visible() and not disb_table.is_visible():
                # Verify aria-expanded just to be perfectly safe
                try:
                    # Sometimes the text= locator gets the inner div, so we check parent button
                    button = disb_toggle.locator("xpath=ancestor-or-self::button").first
                    is_expanded = button.get_attribute("aria-expanded") == "true"
                except Exception:
                    is_expanded = False
                    
                if not is_expanded:
                    with contextlib.suppress(Exception):
                        disb_toggle.click(timeout=1000)
                        time.sleep(0.5)
                        
            # VERY IMPORTANT: E-Trade loads this table asynchronously via AJAX after clicking. 
            # We must wait for it to appear in the DOM.
            try:
                disb_table.wait_for(state="visible", timeout=8000)
            except Exception:
                pass
            
            if disb_table.is_visible():
                headers = disb_table.locator("thead th").all_inner_texts()
                cells = disb_table.locator("tbody td").all_inner_texts()
                
                headers = [h.strip() for h in headers]
                cells = [c.replace('$', '').replace(',', '').strip() for c in cells]
                
                def get_cell(label: str) -> str:
                    for i, h in enumerate(headers):
                        if label in h:
                            return cells[i] if i < len(cells) else "0"
                    return "0"
                    
                comm = get_cell("Commission")
                sec = get_cell("SEC Fee")
                brokerage = get_cell("Brokerage Assist Fee")
                
                if comm != "0": fees["Commission"] = comm
                if sec != "0": fees["SEC Fees"] = sec
                if brokerage != "0": fees["Brokerage Assist Fee"] = brokerage
                
            else:
                # Fallback if the table doesn't have the data-test-id
                def get_fee_fallback(label: str) -> str:
                    try:
                        el = page.locator(f"text={label}").first
                        if el.is_visible():
                            return el.evaluate('''node => {
                                let th = node.closest("th");
                                if (th) {
                                    let thead = th.closest("thead");
                                    let table = th.closest("table");
                                    if (thead && table) {
                                        let tr = th.closest("tr");
                                        let idx = Array.from(tr.children).indexOf(th);
                                        let tbody = table.querySelector("tbody");
                                        if (tbody && tbody.children.length > 0) {
                                            let dataCell = tbody.children[0].children[idx];
                                            if (dataCell) {
                                                let match = dataCell.innerText.match(/\\$([\\d,.]+)/);
                                                if (match) return match[1];
                                            }
                                        }
                                    }
                                }
                                if (node.nextElementSibling) {
                                    let match = node.nextElementSibling.innerText.match(/\\$([\\d,.]+)/);
                                    if (match) return match[1];
                                }
                                let match = node.innerText.match(/\\$([\\d,.]+)/);
                                if (match) return match[1];
                                return "0";
                            }''')
                    except Exception:
                        pass
                    return "0"
                    
                comm = get_fee_fallback("Commission")
                if comm == "0": comm = get_fee_fallback("Estimated Commission")
                sec = get_fee_fallback("SEC Fee")
                if sec == "0": sec = get_fee_fallback("Estimated SEC Fee")
                brokerage = get_fee_fallback("Brokerage Assist Fee")
                
                fees["Commission"] = comm
                fees["SEC Fees"] = sec
                fees["Brokerage Assist Fee"] = brokerage
            
            details.update(fees)
        except Exception as e:
            print(f"  WARNING: Could not parse fees: {e}")

        # --- EXTRACT EXECUTION DATE ---
        try:
            executed_cells = (
                order_history_div.locator('td[role="cell"]').filter(has_text="Order Executed").all()
            )
        except Exception as e:
            print(f"  WARNING: Could not find Order Executed cells: {e}")
            return details

        if not executed_cells:
            print(
                f"  WARNING: No 'Order Executed' entries found for order dated {order_date}, using Order Date."
            )
            return details

        exec_dates = []
        for cell in executed_cells:
            try:
                date_str = cell.locator("xpath=following-sibling::td[1]").inner_text().strip()
                if date_str:
                    exec_dates.append(date_str)
            except Exception:
                pass

        if not exec_dates:
            return details

        unique_dates = list(set(exec_dates))
        if len(unique_dates) > 1:
            print(
                f"  WARNING: Order placed on {order_date} has executions on MULTIPLE dates: "
                f"{sorted(unique_dates)}. Using the first execution date. "
                f"This order may need manual review."
            )

        details["execution_date"] = exec_dates[0]
        return details
        
    finally:
        # ALWAYS click the row again to collapse it. 
        # This prevents the DOM from getting bloated and ensures `page.locator.first` always works for the next row.
        try:
            row.locator("td").first.click()
            time.sleep(1)
        except Exception:
            pass

    if not executed_cells:
        print(
            f"  WARNING: No 'Order Executed' entries found for order dated {order_date}, using Order Date."
        )
        with contextlib.suppress(Exception):
            row.locator("td").first.click()
        return details

    # Extract the date part (MM/DD/YYYY) from the adjacent "Date & Time" cell
    exec_dates: list[str] = []
    for exec_cell in executed_cells:
        try:
            # Date & Time is the immediately following sibling td
            date_text = exec_cell.locator("xpath=following-sibling::td[1]").inner_text().strip()
            # Format: "12/08/2025 02:52:41 PM ET" — take only the date part
            exec_dates.append(date_text.split()[0])
        except Exception as e:
            print(f"  WARNING: Could not read execution date cell: {e}")

    # Collapse the detail row again
    with contextlib.suppress(Exception):
        row.locator("td").first.click()

    if not exec_dates:
        print(
            f"  WARNING: Could not parse any execution dates for order {order_date}, using Order Date."
        )
        return details

    unique_dates = set(exec_dates)
    if len(unique_dates) > 1:
        print(
            f"  WARNING: Order placed on {order_date} has executions on MULTIPLE dates: "
            f"{sorted(unique_dates)}. Using the first execution date. "
            f"This order may need manual review."
        )

    details["execution_date"] = exec_dates[0]
    return details


def download_orders() -> None:
    if not os.path.exists(SESSION_FILE):
        print(f"Session file {SESSION_FILE} not found. Please run etrade_login.py first.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        try:
            context = browser.new_context(storage_state=SESSION_FILE)
        except Exception as e:
            print(f"Error loading session: {e}")
            return

        page = context.new_page()
        print(f"Navigating to {TARGET_URL}")
        page.goto(TARGET_URL)

        # Wait for page load
        try:
            page.wait_for_url(lambda url: "orders" in url and "login" not in url, timeout=10000)
            page.locator('[data-test-id="orders.year"]').wait_for(timeout=10000)
        except Exception:
            print("Login session might be expired or page load failed.")
            browser.close()
            return

        print("Setting filters...")
        # Select Custom Year
        page.locator('[data-test-id="orders.year"]').get_by_label("Year").select_option("Custom")

        # Set Start Date
        # We need to be careful with date pickers. The user suggested clicking/dblclicking/filling.
        start_date_input = page.get_by_role("textbox", name="Start date (format: MM/DD/YY)")
        start_date_input.click()
        start_date_input.dblclick()  # Select all existing text
        start_date_input.fill("01/01/19")

        # Click Apply
        page.locator('[data-test-id="Filter applybtn"]').click()

        # Wait for results to update
        for _ in range(30):
            displays = page.locator(".spinner-overlay-spinner").evaluate_all(
                "elements => elements.map(e => window.getComputedStyle(e).display)"
            )
            if all(d == "none" for d in displays):
                break
            time.sleep(1)

        # Click View All if available
        try:
            view_all_btn = page.get_by_role("button", name="View All", exact=True)
            if view_all_btn.is_visible():
                print("Clicking 'View All'...")
                view_all_btn.click()
                # Wait for table to expand.
                # We can wait for the "Viewing X of X" text or just a static wait for now.
                time.sleep(3)
        except Exception as e:
            print(f"View All button not found or not clickable: {e}")

        print("Scraping table data...")

        # Extract data
        rows = page.locator("table[role='table'] tbody tr").all()
        print(f"Found {len(rows)} rows.")

        data = []
        for i, row in enumerate(rows):
            # Skip if it's not a data row (e.g. if there are spacer rows, though the HTML showed spTableRow)
            # The HTML shows class="spTableRow"

            cells = row.locator("td").all()
            if len(cells) < 10:
                continue

            # Indices (0-based from locator list):
            # Benefit Type: 1
            # Order Date: 2
            # Sold Qty: 8
            # Execution Price: 9

            benefit_type = cells[1].inner_text().strip()
            order_date = cells[2].inner_text().strip()
            action = cells[3].inner_text().strip()
            sold_qty = cells[8].inner_text().strip()
            exec_price = cells[9].inner_text().strip()
            status = cells[-1].inner_text().strip()

            if "Cancelled" in status:
                print(f"  Row {i + 1}: {benefit_type} {order_date} (Action: {action}) - Order Cancelled, skipping.")
                continue

            if "Sell" not in action and "Sale" not in action:
                # E-trade logs you out if you click too fast. Skip clicking on non-sell orders entirely.
                print(f"  Row {i + 1}: {benefit_type} {order_date} (Action: {action}) - Not a sell, skipping details.")
                continue

            print(
                f"  Row {i + 1}: {benefit_type} {order_date} qty={sold_qty} (Action: {action}) — fetching details & fees..."
            )
            exec_details = _get_execution_details(row, order_date, page)
            execution_date = exec_details["execution_date"]

            if execution_date != order_date:
                print(f"    Order Date: {order_date}  →  Execution Date: {execution_date}")

            if exec_details["Commission"] != "0" or exec_details["SEC Fees"] != "0":
                print(f"    Found Fees: Commission={exec_details['Commission']}, SEC={exec_details['SEC Fees']}, Brokerage Assist={exec_details['Brokerage Assist Fee']}")
            else:
                print(f"    No fees found for this order (may be a vest or no-fee transaction).")
                
            data.append(
                {
                    "Execution Date": execution_date,
                    "Order Date": order_date,
                    "Sold Qty.": sold_qty,
                    "Execution Price": exec_price,
                    "Benefit Type": benefit_type,
                    "Commission": exec_details["Commission"],
                    "SEC Fees": exec_details["SEC Fees"],
                    "Brokerage Assist Fee": exec_details["Brokerage Assist Fee"],
                }
            )

        print(f"Extracted {len(data)} records.")

        if data:
            df = pd.DataFrame(data)
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            df.to_excel(OUTPUT_FILE, index=False)
            print(f"Saved orders to {OUTPUT_FILE}")
        else:
            print("No data found.")

        browser.close()


if __name__ == "__main__":
    download_orders()
