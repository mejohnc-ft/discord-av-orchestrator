from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value is not None and value != "" else default


PROFILE_DIR = Path(env("PROFILE_DIR", "/data/profile"))
START_URL = env("START_URL", "https://discord.com/app")
SECONDARY_URLS = [value.strip() for value in env("SECONDARY_URLS", "").split(",") if value.strip()]
BROADCAST_URL = env("BROADCAST_URL", "https://www.youtube.com/")
RUNNER_MODE = env("RUNNER_MODE", "idle")
TARGET_URL = env("TARGET_URL", START_URL)
TAB_CAPTURE_TITLE_HINT = env("TAB_CAPTURE_TITLE_HINT", "Franklin Media Share")
MANUAL_HOLD_SECONDS = int(env("MANUAL_HOLD_SECONDS", "3600"))
POST_LOGIN_WAIT_SECONDS = int(env("POST_LOGIN_WAIT_SECONDS", "15"))
REMOTE_DEBUGGING_PORT = env("REMOTE_DEBUGGING_PORT", "9222")
BROWSER_EXECUTABLE = env(
    "BROWSER_EXECUTABLE",
    "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
)
BROADCAST_TAB_NAME = "__franklin_broadcast__"


def log(message: str) -> None:
    print(message, flush=True)


def launch_context(playwright):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chromium = playwright.chromium
    return chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        args=[
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        viewport={"width": 1440, "height": 900},
    )


def is_discord_url(url: str) -> bool:
    return "discord.com" in (urlparse(url).hostname or "")


def is_rumble_livestreams_url(url: str) -> bool:
    parsed = urlparse(url)
    return "rumble.com" in (parsed.hostname or "") and "/c/ghostpolitics/livestreams" in parsed.path


def is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return "youtube.com" in host or "youtu.be" in host


def normalize_browser_session() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{REMOTE_DEBUGGING_PORT}")
        try:
            context = browser.contexts[0]
            pages = [page for page in context.pages if not page.url.startswith("chrome-extension://")]

            discord_page = None
            rumble_page = None
            broadcast_page = None
            duplicate_pages = []

            for page in pages:
                url = page.url or ""
                try:
                    page_name = page.evaluate("() => window.name || ''")
                except Exception:
                    page_name = ""
                try:
                    title = page.title()
                except Exception:
                    title = ""

                if page_name == BROADCAST_TAB_NAME or title.strip() == TAB_CAPTURE_TITLE_HINT:
                    if broadcast_page is None:
                        broadcast_page = page
                    else:
                        duplicate_pages.append(page)
                    continue
                if is_discord_url(url):
                    if discord_page is None or "/channels/160637406985322496/" in url:
                        if discord_page is not None and discord_page is not page:
                            duplicate_pages.append(discord_page)
                        discord_page = page
                    else:
                        duplicate_pages.append(page)
                    continue
                if is_rumble_livestreams_url(url):
                    if rumble_page is None:
                        rumble_page = page
                    else:
                        duplicate_pages.append(page)
                    continue
                if is_youtube_url(url):
                    if broadcast_page is None:
                        broadcast_page = page
                    else:
                        duplicate_pages.append(page)
                    continue
                if url.startswith("chrome://new-tab-page") or url.startswith("chrome://newtab") or url.startswith("chrome-untrusted://new-tab-page") or url == "about:blank":
                    duplicate_pages.append(page)

            if discord_page is None:
                discord_page = context.new_page()
                discord_page.goto(START_URL, wait_until="domcontentloaded")
            if rumble_page is None:
                rumble_page = context.new_page()
                rumble_page.goto(SECONDARY_URLS[0], wait_until="domcontentloaded")
            if broadcast_page is None:
                broadcast_page = context.new_page()
                broadcast_page.goto(BROADCAST_URL, wait_until="domcontentloaded")

            try:
                broadcast_page.evaluate(
                    """
                    ([tabName, tabTitle]) => {
                      window.name = tabName;
                      document.title = tabTitle;
                    }
                    """,
                    [BROADCAST_TAB_NAME, TAB_CAPTURE_TITLE_HINT],
                )
            except Exception:
                pass

            for page in duplicate_pages:
                try:
                    if not page.is_closed():
                        page.close()
                except Exception:
                    continue
        finally:
            playwright.stop()


def profile_has_existing_session() -> bool:
    if not PROFILE_DIR.exists():
        return False
    default_dir = PROFILE_DIR / "Default"
    session_dir = PROFILE_DIR / "Sessions"
    return (
        default_dir.exists()
        or session_dir.exists()
        or any(PROFILE_DIR.iterdir())
    )


def launch_raw_browser(urls: list[str], restore_session: bool = False) -> subprocess.Popen[str]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
        path = PROFILE_DIR / name
        try:
            if path.is_symlink() or path.exists():
                path.unlink()
        except FileNotFoundError:
            pass
    args = [
        BROWSER_EXECUTABLE,
        f"--user-data-dir={PROFILE_DIR}",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--disable-session-crashed-bubble",
        "--noerrdialogs",
        "--ignore-gpu-blocklist",
        "--enable-gpu-rasterization",
        "--enable-zero-copy",
        "--enable-accelerated-video-decode",
        "--enable-features=VaapiVideoDecoder,VaapiVideoEncoder,CanvasOopRasterization",
        "--start-maximized",
        "--window-size=1440,900",
        f"--remote-debugging-port={REMOTE_DEBUGGING_PORT}",
        "--remote-debugging-address=0.0.0.0",
    ]
    if restore_session:
        args.append("--restore-last-session")
    if TAB_CAPTURE_TITLE_HINT:
        args.append(f"--auto-select-tab-capture-source-by-title={TAB_CAPTURE_TITLE_HINT}")
        args.append(f"--auto-select-desktop-capture-source={TAB_CAPTURE_TITLE_HINT}")
    if not restore_session:
        args.extend(urls)
    return subprocess.Popen(args)


def wait_for_browser(process: subprocess.Popen[str], seconds: int) -> int:
    deadline = time.time() + seconds
    while time.time() < deadline:
        code = process.poll()
        if code is not None:
            return code
        time.sleep(1)
    return 0


def stop_browser(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def manual_login(page) -> int:
    log(f"Opening {START_URL} for manual login")
    page.goto(START_URL, wait_until="domcontentloaded")
    log(
        "Manual login window is ready. Open noVNC, sign in to Discord, complete 2FA if needed, "
        "and leave the browser profile logged in."
    )
    time.sleep(MANUAL_HOLD_SECONDS)
    return 0


def reuse_profile(page) -> int:
    log(f"Opening {TARGET_URL} with persisted profile")
    page.goto(TARGET_URL, wait_until="domcontentloaded")
    time.sleep(POST_LOGIN_WAIT_SECONDS)
    title = page.title()
    log(f"Loaded page title: {title}")
    return 0


def manual_login_raw() -> int:
    log(f"Opening {START_URL} in raw Chromium for manual login")
    process = launch_raw_browser([START_URL, *SECONDARY_URLS, BROADCAST_URL], restore_session=False)
    try:
        log(
            "Manual login window is ready. Open noVNC, sign in to Discord, complete 2FA if needed, "
            "and leave the browser profile logged in."
        )
        return wait_for_browser(process, MANUAL_HOLD_SECONDS)
    finally:
        stop_browser(process)


def open_raw() -> int:
    log(f"Opening {TARGET_URL} in raw Chromium with persisted profile")
    restore = profile_has_existing_session()
    bootstrap_urls = [TARGET_URL, *SECONDARY_URLS, BROADCAST_URL]
    process = launch_raw_browser(bootstrap_urls, restore_session=restore)
    try:
        return wait_for_browser(process, POST_LOGIN_WAIT_SECONDS)
    finally:
        stop_browser(process)


def browser_daemon_raw() -> int:
    log(f"Starting persistent raw Chromium at {TARGET_URL}")
    restore = profile_has_existing_session()
    bootstrap_urls = [TARGET_URL, *SECONDARY_URLS, BROADCAST_URL]
    if restore:
        log("Existing browser profile detected; restoring previous session without opening bootstrap URLs")
    else:
        log(f"No existing browser session detected; bootstrapping tabs: {bootstrap_urls}")
    process = launch_raw_browser(bootstrap_urls, restore_session=restore)
    try:
        time.sleep(8)
        normalize_browser_session()
        return process.wait()
    finally:
        stop_browser(process)


def main() -> int:
    if RUNNER_MODE == "manual-login":
        return manual_login_raw()
    if RUNNER_MODE == "open":
        return open_raw()
    if RUNNER_MODE == "browser-daemon":
        return browser_daemon_raw()

    with sync_playwright() as playwright:
        context = launch_context(playwright)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            if RUNNER_MODE == "manual-login-playwright":
                return manual_login(page)
            if RUNNER_MODE == "open-playwright":
                return reuse_profile(page)
            log(f"Unknown RUNNER_MODE: {RUNNER_MODE}")
            return 2
        except PlaywrightTimeoutError as exc:
            log(f"Playwright timeout: {exc}")
            return 1
        finally:
            context.close()


if __name__ == "__main__":
    sys.exit(main())
