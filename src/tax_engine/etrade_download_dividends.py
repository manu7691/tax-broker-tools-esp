import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from tax_engine.etrade_download_espp import backup_existing_file

SESSION_FILE = "input/etrade_session.json"
TARGET_URL = (
    "https://us.etrade.com/etx/sp/stockplan?accountIndex=0&traxui=tsp_accountshome#/holdings"
)
DOWNLOAD_DIR = Path("input/dividends")
TARGET_FILENAME = "Cash_Transactions.xlsx"


def download_dividends() -> None:
    if not os.path.exists(SESSION_FILE):
        print(f"Session file {SESSION_FILE} not found. Please run etrade_login.py first.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        try:
            context = browser.new_context(storage_state=SESSION_FILE)
        except Exception as e:
            print(f"Error loading session: {e}")
            print("Please run etrade_login.py to create a new session.")
            return

        page = context.new_page()

        print(f"Navigating to {TARGET_URL}")
        page.goto(TARGET_URL)

        # Wait for page load (url has holdings and not login)
        try:
            page.wait_for_url(lambda url: "holdings" in url and "login" not in url, timeout=15000)
            # Wait for either "Other Holdings" to be visible
            page.locator("text=Other Holdings").first.wait_for(timeout=15000)
        except Exception:
            print("Login session might be expired, invalid, or page took too long to load.")
            print("Please run etrade_login.py again.")
            browser.close()
            return

        print("Navigated to holdings. Expanding Other Holdings...")

        div_only_toggle_container = page.locator(
            "[data-test-id='dividends only toggle button']"
        ).first

        # 1. Expand "Other Holdings" if it's collapsed
        try:
            outer_holdings_btn = page.locator("[data-test-id='holdings.1.Other Holdings']").first
            inner_holdings_btn = outer_holdings_btn.locator("button").first

            # Wait for outer button to be visible
            outer_holdings_btn.wait_for(state="visible", timeout=15000)
            outer_holdings_btn.scroll_into_view_if_needed()

            cash_row = page.locator("tr", has_text="Cash").first

            # Check expansion state: either aria-expanded is true, OR the Cash row is already visible
            is_already_expanded = False
            try:
                if (
                    inner_holdings_btn.get_attribute("aria-expanded") == "true"
                    or cash_row.is_visible()
                ):
                    is_already_expanded = True
            except Exception:
                pass

            if not is_already_expanded:
                print("Other Holdings is collapsed. Expanding with retries...")
                for attempt in range(1, 4):
                    print(f"Other Holdings expansion attempt {attempt}...")
                    if attempt % 2 != 0:
                        inner_holdings_btn.evaluate("el => el.click()")
                    else:
                        outer_holdings_btn.evaluate("el => el.click()")

                    try:
                        cash_row.wait_for(state="visible", timeout=3000)
                        page.wait_for_timeout(1000)  # Wait for animation to settle
                        print("Other Holdings successfully expanded.")
                        break
                    except Exception:
                        print(f"Attempt {attempt} failed to expand Other Holdings.")
            else:
                print("Other Holdings is already expanded.")
        except Exception as e:
            print(f"Warning/Error finding/expanding Other Holdings: {e}")

        # 2. Expand "Cash" if collapsed
        try:
            # Wait for Cash row to be visible in the DOM
            cash_row = page.locator("tr", has_text="Cash").first
            cash_row.wait_for(state="visible", timeout=10000)
            cash_row.scroll_into_view_if_needed()

            # Wait a moment for any parent animation/scroll to settle
            page.wait_for_timeout(1000)

            # Check expansion state of Cash
            # If the button's aria-label is "collapse row", or the icon is "expand_less", or the toggle container is visible
            expand_btn = cash_row.locator(
                "button[aria-label='expand row'], button[aria-label='collapse row'], button"
            ).first
            icon = expand_btn.locator("i.material-icons").first

            is_cash_expanded = False
            try:
                aria_label = expand_btn.get_attribute("aria-label")
                icon_text = icon.inner_text().strip() if icon.is_visible() else ""
                if (
                    aria_label == "collapse row"
                    or icon_text == "expand_less"
                    or div_only_toggle_container.is_visible()
                ):
                    is_cash_expanded = True
            except Exception:
                if div_only_toggle_container.is_visible():
                    is_cash_expanded = True

            if not is_cash_expanded:
                print("Cash row details seem collapsed. Expanding Cash row with retries...")
                for attempt in range(1, 4):
                    print(f"Cash expansion attempt {attempt}...")
                    if attempt == 1:
                        try:
                            expand_btn.click(timeout=2000)
                        except Exception:
                            expand_btn.evaluate("el => el.click()")
                    elif attempt == 2:
                        first_cell = cash_row.locator("td").first
                        first_cell.evaluate("el => el.click()")
                    else:
                        cash_row.evaluate("el => el.click()")

                    try:
                        div_only_toggle_container.wait_for(state="visible", timeout=4000)
                        page.wait_for_timeout(1000)  # Wait for transition/animation to settle
                        print("Cash row successfully expanded.")
                        break
                    except Exception:
                        print(f"Attempt {attempt} failed to expand Cash row.")
            else:
                print("Cash row is already expanded.")
        except Exception as e:
            print(f"Warning/Error expanding Cash: {e}")

        # 3. Check "Dividends only" checkbox/toggle
        if div_only_toggle_container.is_visible():
            try:
                div_only_toggle_container.scroll_into_view_if_needed()

                # Check current state using dynamic lookup
                def check_current_state() -> bool:
                    fresh_input = div_only_toggle_container.locator("input").first
                    return (
                        fresh_input.get_attribute("aria-checked") == "true"
                    ) or fresh_input.is_checked()

                is_checked = check_current_state()

                if not is_checked:
                    print("Dividends only is not checked. Clicking to check it...")
                    for attempt in range(1, 4):
                        # Try clicking the label first, then the input
                        if attempt % 2 != 0:
                            div_only_toggle_container.locator("label").first.click()
                        else:
                            div_only_toggle_container.locator("input").first.evaluate(
                                "el => el.click()"
                            )

                        # Wait a bit for the state to update
                        page.wait_for_timeout(1500)

                        # Re-evaluate with fresh DOM query
                        try:
                            is_checked = check_current_state()
                        except Exception:
                            is_checked = False

                        if is_checked:
                            print("Dividends only checkbox is now checked.")
                            break
                        else:
                            print(f"Attempt {attempt} to check 'Dividends only' did not succeed.")
                else:
                    print("Dividends only is already checked.")

                # Give the page/grid ample time to reload and settle
                print("Waiting for page/grid to settle after checking dividends only...")
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"Error checking Dividends only: {e}")
        else:
            print("WARNING: Could not find 'Dividends only' toggle.")

        # 4. Click Download
        print("Triggering Excel download...")
        for attempt in range(1, 4):
            try:
                # Target the button with text "Download" and verify it contains the "file_download" icon
                download_btn = (
                    page.locator("button.btn-link:has-text('Download')")
                    .filter(has=page.locator("i:has-text('file_download')"))
                    .first
                )
                download_btn.wait_for(state="visible", timeout=5000)
                download_btn.scroll_into_view_if_needed()

                with page.expect_download(timeout=15000) as download_info:
                    try:
                        download_btn.click(timeout=3000)
                    except Exception:
                        download_btn.evaluate("el => el.click()")

                download = download_info.value
                print(f"Download started: {download.suggested_filename}")

                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                target_path = DOWNLOAD_DIR / TARGET_FILENAME

                # Backup if exists
                backup_existing_file(target_path)

                download.save_as(target_path)
                print(f"Successfully saved to {target_path}")
                break
            except Exception as e:
                print(f"Download attempt {attempt} failed: {e}")
                if attempt < 3:
                    print("Retrying download...")
                    page.wait_for_timeout(2000)
                else:
                    print("All download attempts failed.")

        page.wait_for_timeout(2000)
        browser.close()


if __name__ == "__main__":
    download_dividends()
