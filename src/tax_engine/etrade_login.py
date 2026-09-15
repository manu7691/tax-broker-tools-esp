import json
import os
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SESSION_FILE = "input/etrade_session.json"
TARGET_URL = "https://us.etrade.com/etx/sp/stockplan#/myAccount/benefitHistory"

# E-Trade server-side sessions are short lived. Replaying a stale storage_state
# sends an expired SiteMinder SMSESSION cookie, which redirect-loops instead of
# cleanly showing the login form, so drop anything older than this.
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60


def _session_is_usable(path: str) -> bool:
    """Reject a saved session that is too old or has no live auth cookies."""
    try:
        age = time.time() - os.path.getmtime(path)
        if age > SESSION_MAX_AGE_SECONDS:
            print(f"Ignoring saved session: {age / 3600:.1f}h old (max 12h).")
            return False

        with open(path) as fh:
            state = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"Ignoring unreadable session file: {exc}")
        return False

    now = time.time()
    for cookie in state.get("cookies", []):
        if cookie.get("name") in ("SMSESSION", "ETSESSION", "JSESSIONID"):
            expires = cookie.get("expires", -1)
            if expires in (-1, None) or expires > now:
                return True

    print("Ignoring saved session: no live auth cookies.")
    return False


def login() -> None:
    with sync_playwright() as p:
        # Use the real Google Chrome install when present. Playwright's bundled
        # "chromium" is Google Chrome for Testing, which reports
        # sec-ch-ua: "Chromium" rather than "Google Chrome" -- on its own enough
        # for E-Trade's risk engine to stall the post-login page.
        # --disable-blink-features=AutomationControlled is what genuinely makes
        # navigator.webdriver report false; without it Chrome reports true.
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
        ]
        try:
            browser = p.chromium.launch(channel="chrome", headless=False, args=launch_args)
            print("Using installed Google Chrome.")
        except PlaywrightError:
            print("Google Chrome not found; falling back to bundled Chromium.")
            browser = p.chromium.launch(headless=False, args=launch_args)

        # Do NOT spoof the user agent: Playwright cannot rewrite the matching
        # Sec-CH-UA client hints, so an override contradicts the real headers.
        # Likewise no navigator patching -- Chrome already reports
        # navigator.webdriver === false and a genuine PluginArray, and faking
        # them is more detectable than leaving them alone.
        context_options: dict[str, object] = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }

        if os.path.exists(SESSION_FILE) and _session_is_usable(SESSION_FILE):
            print(f"Loading session from {SESSION_FILE}")
            context = browser.new_context(storage_state=SESSION_FILE, **context_options)  # pyright: ignore[reportArgumentType]
        else:
            print("Starting new session")
            context = browser.new_context(**context_options)  # pyright: ignore[reportArgumentType]

        page = context.new_page()

        print(f"Navigating to {TARGET_URL}")
        page.goto(TARGET_URL)

        # The URL may change immediately, so wait before inspecting it.
        time.sleep(2)

        if "login" in page.url:
            print("Login required. Please log in manually in the browser window.")
            print("Waiting for successful login...")

            # Generous timeout because MFA is done by hand.
            try:
                page.wait_for_url(
                    lambda url: "stockplan" in url and "login" not in url, timeout=300000
                )  # 5 minutes timeout
                print("Login detected!")
            except Exception:
                print("Timeout or error waiting for login.")
                browser.close()
                return

        print("Successfully on the Stock Plan page.")

        # Ensure the input directory exists
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)

        # Save the session state
        context.storage_state(path=SESSION_FILE)
        print(f"Session saved to {SESSION_FILE}")

        # Keep browser open for a moment to see result
        time.sleep(2)
        browser.close()


if __name__ == "__main__":
    login()
