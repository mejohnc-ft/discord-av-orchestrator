from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DISCORD_CHANNEL_URL = "https://discord.com/channels/160637406985322496/465358825571221505"
RUMBLE_LIVESTREAMS_URL = "https://rumble.com/c/ghostpolitics/livestreams?e9s=src_v1_cbl"
DISPLAY = os.environ.get("DISPLAY", ":99")
CAPTURE_TITLE_HINT = os.environ.get("TAB_CAPTURE_TITLE_HINT", "Franklin Media Share")
BROADCAST_TAB_NAME = "__franklin_broadcast__"

PICKER_TILE_X = 511
PICKER_TILE_Y = 242
PICKER_SHARE_X = 827
PICKER_SHARE_Y = 510


@dataclass
class BrowserSession:
    playwright: object
    browser: Browser
    context: BrowserContext

    def close(self) -> None:
        self.browser.close()
        self.playwright.stop()


def connect_over_cdp(cdp_url: str = DEFAULT_CDP_URL) -> BrowserSession:
    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(cdp_url)
    context = browser.contexts[0]
    return BrowserSession(playwright=playwright, browser=browser, context=context)


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def find_page(context: BrowserContext, url_fragment: str) -> Page | None:
    for page in context.pages:
        if url_fragment in page.url:
            return page
    return None


def media_pages(context: BrowserContext) -> list[Page]:
    return [
        page
        for page in context.pages
        if page.url
        and (page.url.startswith("http://") or page.url.startswith("https://"))
        and not page.url.startswith("chrome-extension://")
        and "discord.com/channels/" not in page.url
    ]


def preferred_media_page(context: BrowserContext) -> Page | None:
    pages = media_pages(context)
    if not pages:
        return None

    for page in pages:
        try:
            if page.evaluate("() => window.name || ''") == BROADCAST_TAB_NAME:
                return page
        except Exception:
            continue

    # Prefer the share-target tab title if present, then a page with a video,
    # then fall back to the most recently created non-Discord tab.
    for page in pages:
        try:
            if normalize(page.title()) == CAPTURE_TITLE_HINT:
                return page
        except Exception:
            continue

    for page in pages:
        try:
            if page.locator("video").count() > 0:
                return page
        except Exception:
            continue

    return pages[-1]


def discord_pages(context: BrowserContext) -> list[Page]:
    return [
        page
        for page in context.pages
        if page.url
        and not page.url.startswith("chrome-extension://")
        and "discord.com" in (urlparse(page.url).hostname or "")
    ]


def preferred_discord_page(context: BrowserContext) -> Page | None:
    pages = discord_pages(context)
    if not pages:
        return None

    for page in pages:
        try:
            if is_streaming(page):
                return page
        except Exception:
            continue

    for page in pages:
        if "/channels/160637406985322496/" in page.url:
            return page

    for page in pages:
        if "/channels/@me" in page.url or page.url.rstrip("/") == "https://discord.com/app":
            return page

    return pages[-1]


def get_or_open_page(context: BrowserContext, url: str, url_fragment: str) -> Page:
    page = find_page(context, url_fragment)
    if page is not None:
        return page
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded")
    return page


def discord_page(context: BrowserContext) -> Page:
    page = preferred_discord_page(context)
    if page is None:
        page = context.new_page()
        page.goto("https://discord.com/app", wait_until="domcontentloaded")
    if "/channels/160637406985322496/" not in page.url:
        page.goto(DISCORD_CHANNEL_URL, wait_until="domcontentloaded")
    return page


def rumble_page(context: BrowserContext) -> Page:
    return get_or_open_page(context, RUMBLE_LIVESTREAMS_URL, "rumble.com/c/ghostpolitics/livestreams")


def classify_media_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "rumble.com" in host:
        return "rumble"
    return "generic-html5"


def normalize_media_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "youtu.be" in host:
        video_id = parsed.path.strip("/")
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    if "youtube.com" in host and parsed.path.startswith("/shorts/"):
        video_id = parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    return url


def inspect_discord_controls(cdp_url: str = DEFAULT_CDP_URL) -> list[dict]:
    session = connect_over_cdp(cdp_url)
    try:
        page = discord_page(session.context)
        page.bring_to_front()
        page.wait_for_timeout(1500)
        items = page.locator("button, [role=button], a, input, [aria-label], [title]").evaluate_all(
            """
            els => els.slice(0, 500).map((el, i) => ({
              i,
              tag: el.tagName,
              text: (el.innerText || el.textContent || '').trim(),
              aria: el.getAttribute('aria-label') || '',
              title: el.getAttribute('title') || '',
              role: el.getAttribute('role') || '',
              placeholder: el.getAttribute('placeholder') || '',
              disabled: !!el.disabled,
            }))
            """
        )
        hits = []
        for item in items:
            haystack = " ".join(
                [
                    normalize(item.get("text")),
                    normalize(item.get("aria")),
                    normalize(item.get("title")),
                    normalize(item.get("placeholder")),
                ]
            ).lower()
            if any(
                token in haystack
                for token in (
                    "share",
                    "screen",
                    "stream",
                    "live",
                    "video",
                    "call",
                    "voice",
                    "join",
                    "window",
                    "audio",
                    "full",
                )
            ):
                item["text"] = normalize(item.get("text"))
                item["aria"] = normalize(item.get("aria"))
                item["title"] = normalize(item.get("title"))
                item["placeholder"] = normalize(item.get("placeholder"))
                hits.append(item)
        return hits
    finally:
        session.close()


def print_inspect_discord_controls(cdp_url: str = DEFAULT_CDP_URL) -> None:
    print(json.dumps(inspect_discord_controls(cdp_url), indent=2))


def inspect_rumble_live_candidates(cdp_url: str = DEFAULT_CDP_URL) -> list[dict]:
    session = connect_over_cdp(cdp_url)
    try:
        page = rumble_page(session.context)
        page.goto(RUMBLE_LIVESTREAMS_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        items = page.locator("a, button, [role=button], [aria-label], [title]").evaluate_all(
            """
            els => els.slice(0, 800).map((el, i) => {
              const rect = el.getBoundingClientRect();
              return {
                i,
                tag: el.tagName,
                text: (el.innerText || el.textContent || '').trim(),
                aria: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                role: el.getAttribute('role') || '',
                href: el.getAttribute('href') || '',
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
              };
            })
            """
        )
        hits = []
        for item in items:
            haystack = " ".join(
                [
                    normalize(item.get("text")),
                    normalize(item.get("aria")),
                    normalize(item.get("title")),
                    normalize(item.get("href")),
                ]
            ).lower()
            if "live" in haystack or "watching" in haystack or "/v" in item.get("href", ""):
                item["text"] = normalize(item.get("text"))
                item["aria"] = normalize(item.get("aria"))
                item["title"] = normalize(item.get("title"))
                hits.append(item)
        hits.sort(key=lambda item: (item["y"], item["x"]))
        return hits
    finally:
        session.close()


def print_inspect_rumble_live_candidates(cdp_url: str = DEFAULT_CDP_URL) -> None:
    print(json.dumps(inspect_rumble_live_candidates(cdp_url), indent=2))


def x11_run(*args: str) -> str:
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DISPLAY": DISPLAY},
    )
    return completed.stdout.strip()


def x11_click(x: int, y: int, delay_seconds: float = 0.5) -> None:
    subprocess.run(
        ["xdotool", "mousemove", str(x), str(y), "click", "1"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DISPLAY": DISPLAY},
    )
    time.sleep(delay_seconds)


def x11_key(*keys: str, delay_seconds: float = 0.5) -> None:
    subprocess.run(
        ["xdotool", "key", *keys],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DISPLAY": DISPLAY},
    )
    time.sleep(delay_seconds)


def wait_for(page: Page, locator: Locator, timeout_ms: int = 10000) -> bool:
    try:
        locator.first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except PlaywrightTimeoutError:
        return False


def is_streaming(page: Page) -> bool:
    return page.locator("[aria-label='Stop Streaming']").count() > 0


def discord_login_required(page: Page) -> bool:
    url = page.url or ""
    if "discord.com/login" in url:
        return True
    try:
        return page.locator("input[name='email'], input[name='password']").count() > 0
    except Exception:
        return False


def ensure_discord_connected(page: Page) -> bool:
    page.bring_to_front()
    page.goto(DISCORD_CHANNEL_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if discord_login_required(page):
        raise RuntimeError("Discord login required in browser worker")
    share_controls = page.locator("button[aria-label='Share Your Screen'], [aria-label='Share Your Screen'][role='button']")
    if is_streaming(page):
        return True
    if page.get_by_text("Voice Connected", exact=False).count() > 0 and share_controls.count() > 0:
        return True

    channel = page.locator("a[aria-label*='The Oval Office']")
    if channel.count() == 0:
        channel = page.locator("[aria-label*='The Oval Office'][role='button']")
    if channel.count() == 0:
        raise RuntimeError("Could not find The Oval Office voice channel link")
    for _ in range(3):
        channel.first.dblclick(force=True)
        page.wait_for_timeout(2500)
        if share_controls.count() > 0:
            return True
    if not wait_for(page, share_controls, timeout_ms=5000):
        raise RuntimeError("Did not connect to The Oval Office")
    return True


def bring_tab_to_front(page: Page) -> None:
    page.bring_to_front()
    page.wait_for_timeout(500)


def ensure_rumble_livestream_tab(context: BrowserContext) -> Page:
    page = find_page(context, "rumble.com/c/ghostpolitics/livestreams")
    if page is None:
        page = context.new_page()
    page.goto(RUMBLE_LIVESTREAMS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    return page


def find_live_rumble_tile(page: Page) -> Locator | None:
    candidates = [
        page.locator("a").filter(has_text=re.compile(r"\\bLIVE\\b", re.I)),
        page.locator("[aria-label*='live' i] a"),
        page.locator("a[href*='/v']").filter(has=page.locator("text=/\\bLIVE\\b/i")),
        page.locator("a[href*='/v']").filter(has_text=re.compile(r"The Ghost Show", re.I)),
    ]
    for locator in candidates:
        count = locator.count()
        if count > 0:
            return locator.first
    return None


def open_rumble_live(page: Page) -> str:
    bring_tab_to_front(page)
    page.goto(RUMBLE_LIVESTREAMS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    tile = find_live_rumble_tile(page)
    if tile is None:
        raise RuntimeError("Could not find a Ghost Show live tile on the Rumble livestreams page")

    href = tile.get_attribute("href") or ""
    if href.startswith("/"):
        href = f"https://rumble.com{href}"

    tile.click()
    page.wait_for_load_state("domcontentloaded")
    if page.url == RUMBLE_LIVESTREAMS_URL and href:
        page.goto(href, wait_until="domcontentloaded")

    return page.url


def set_capture_title(page: Page) -> None:
    page.evaluate(
        """
        (tabName) => {
          try {
            window.name = tabName;
          } catch (error) {}
        }
        """,
        BROADCAST_TAB_NAME,
    )
    safe_title = json.dumps(CAPTURE_TITLE_HINT)
    page.evaluate(
        f"""
        () => {{
          try {{
            document.title = {safe_title};
            const meta = document.querySelector('meta[property="og:title"]');
            if (meta) meta.setAttribute('content', {safe_title});
          }} catch (error) {{}}
        }}
        """
    )


def wait_for_video(page: Page, timeout_ms: int = 15000, bring_to_front: bool = True) -> Locator:
    if bring_to_front:
        bring_tab_to_front(page)
    video = page.locator("video").first
    if not wait_for(page, video, timeout_ms=timeout_ms):
        raise RuntimeError("HTML5 video element did not appear")
    return video


def play_media(page: Page) -> None:
    wait_for_video(page)
    page.evaluate(
        """
        () => {
          const videos = Array.from(document.querySelectorAll('video'));
          const video = videos.find((item) => !item.ended) || videos[0];
          if (!video) return false;
          video.muted = false;
          video.volume = 1;
          video.play?.();
          return true;
        }
        """
    )
    page.wait_for_timeout(1000)


def pause_media(page: Page) -> None:
    wait_for_video(page, bring_to_front=False)
    page.evaluate(
        """
        () => {
          const videos = Array.from(document.querySelectorAll('video'));
          const video = videos.find((item) => !item.ended) || videos[0];
          if (!video) return false;
          video.pause?.();
          return true;
        }
        """
    )
    page.wait_for_timeout(500)


def set_playback_speed(page: Page, speed: float) -> float:
    wait_for_video(page, bring_to_front=False)
    applied = page.evaluate(
        """
        (targetSpeed) => {
          const videos = Array.from(document.querySelectorAll('video'));
          const video = videos.find((item) => !item.paused && !item.ended) || videos[0];
          if (!video) return null;
          video.playbackRate = targetSpeed;
          video.defaultPlaybackRate = targetSpeed;
          try {
            const player = document.getElementById('movie_player');
            if (player && typeof player.setPlaybackRate === 'function') {
              player.setPlaybackRate(targetSpeed);
            }
          } catch (error) {}
          video.dispatchEvent(new Event('ratechange', { bubbles: true }));
          return video.playbackRate;
        }
        """,
        speed,
    )
    if applied is None:
        raise RuntimeError("Could not set playback speed")
    return float(applied)


def get_playback_speed(page: Page) -> float | None:
    return page.evaluate(
        """
        () => {
          const videos = Array.from(document.querySelectorAll('video'));
          const video = videos.find((item) => !item.paused && !item.ended) || videos[0];
          return video ? video.playbackRate : null;
        }
        """
    )


def fullscreen_media(page: Page) -> None:
    bring_tab_to_front(page)
    page.wait_for_timeout(1500)
    page.keyboard.press("f")
    page.wait_for_timeout(1000)
    is_fullscreen = page.evaluate("() => !!document.fullscreenElement")
    if is_fullscreen:
        return
    page.evaluate(
        """
        () => {
          const video = document.querySelector('video');
          const target = video?.parentElement || video;
          if (!target) return false;
          target.requestFullscreen?.();
          return true;
        }
        """
    )
    page.wait_for_timeout(1000)


def start_and_fullscreen_media(page: Page, speed: float = 1.0) -> None:
    wait_for_video(page)
    set_capture_title(page)
    play_media(page)
    set_playback_speed(page, speed)
    fullscreen_media(page)


def prepare_media_for_share(page: Page, speed: float = 1.0) -> None:
    wait_for_video(page)
    set_capture_title(page)
    pause_media(page)
    set_playback_speed(page, speed)


def start_and_fullscreen_rumble(page: Page) -> None:
    start_and_fullscreen_media(page, speed=1.0)


def verify_rumble_active(page: Page) -> None:
    wait_for_video(page)
    is_playing = page.evaluate(
        """
        () => {
          const video = document.querySelector('video');
          return !!video && !video.paused && !video.ended;
        }
        """
    )
    if not is_playing:
        page.evaluate(
            """
            () => {
              const video = document.querySelector('video');
              if (!video) return false;
              video.play?.();
              return true;
            }
            """
        )
        page.wait_for_timeout(1000)


def open_media_target(context: BrowserContext, url: str) -> Page:
    page = preferred_media_page(context) or context.new_page()
    url = normalize_media_url(url)
    kind = classify_media_url(url)
    if kind == "rumble" and "ghostpolitics/livestreams" in url:
        bring_tab_to_front(page)
        page.goto(RUMBLE_LIVESTREAMS_URL, wait_until="domcontentloaded")
        open_rumble_live(page)
        return page
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if kind == "youtube":
        dismiss_youtube_overlays(page)
        optimize_youtube_playback(page)
    set_capture_title(page)
    return page


def dismiss_youtube_overlays(page: Page) -> None:
    candidates = [
        "button[aria-label='Accept all']",
        "button[aria-label='Reject all']",
        "ytd-button-renderer button",
        "button[aria-label='Close']",
    ]
    for selector in candidates:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                locator.first.click(timeout=500)
                page.wait_for_timeout(500)
        except Exception:
            continue


def optimize_youtube_playback(page: Page) -> None:
    try:
        page.evaluate(
            """
            () => {
              try {
                localStorage.setItem('yt-player-quality', JSON.stringify({
                  data: 'hd720',
                  expiration: Date.now() + 86400000,
                  creation: Date.now(),
                }));
              } catch (error) {}

              const player = document.getElementById('movie_player');
              if (player) {
                try {
                  if (typeof player.setPlaybackQualityRange === 'function') {
                    player.setPlaybackQualityRange('hd720');
                  }
                } catch (error) {}
                try {
                  if (typeof player.setPlaybackQuality === 'function') {
                    player.setPlaybackQuality('hd720');
                  }
                } catch (error) {}
              }
            }
            """
        )
    except Exception:
        pass


def stop_discord_tab_share(page: Page) -> bool:
    bring_tab_to_front(page)
    stop_button = page.locator("[aria-label='Stop Streaming']").first
    if stop_button.count() == 0:
        return False
    stop_button.click(force=True)
    page.wait_for_timeout(1500)
    return not is_streaming(page)


def start_discord_tab_share(page: Page) -> None:
    bring_tab_to_front(page)
    if is_streaming(page):
        return
    share_button = page.locator("button[aria-label='Share Your Screen'], [aria-label='Share Your Screen'][role='button']")
    if share_button.count() == 0:
        share_button = page.locator(".actionButtons_e131a9 button").filter(has=page.locator("[aria-label='Share Your Screen']"))
    if share_button.count() == 0:
        raise RuntimeError("Could not find Discord share screen control")
    share_button.first.click(force=True)
    page.wait_for_timeout(1500)
    if is_streaming(page):
        return

    x11_key("Return", delay_seconds=1.5)
    if is_streaming(page):
        return

    x11_click(PICKER_TILE_X, PICKER_TILE_Y, delay_seconds=1.0)
    x11_key("Return", delay_seconds=1.5)
    if is_streaming(page):
        return

    x11_click(PICKER_SHARE_X, PICKER_SHARE_Y, delay_seconds=1.5)
    if is_streaming(page):
        return

    x11_key("Tab", "Return", delay_seconds=1.5)
    page.wait_for_timeout(2000)
    if not is_streaming(page):
        raise RuntimeError("Discord share picker opened, but streaming did not start")


def cold_start_ghost_show(cdp_url: str = DEFAULT_CDP_URL) -> dict:
    session = connect_over_cdp(cdp_url)
    try:
        discord = discord_page(session.context)
        rumble = ensure_rumble_livestream_tab(session.context)

        ensure_discord_connected(discord)
        rumble_url = open_rumble_live(rumble)
        verify_rumble_active(rumble)
        start_discord_tab_share(discord)
        bring_tab_to_front(rumble)
        start_and_fullscreen_rumble(rumble)

        return {
            "status": "ok",
            "discord_url": discord.url,
            "rumble_url": rumble_url,
            "streaming": is_streaming(discord),
            "fullscreen": bool(rumble.evaluate("() => !!document.fullscreenElement")),
            "active_window": x11_run("xdotool", "getactivewindow"),
        }
    finally:
        session.close()


def start_media_share(url: str, speed: float = 1.0, cdp_url: str = DEFAULT_CDP_URL) -> dict:
    session = connect_over_cdp(cdp_url)
    try:
        discord = discord_page(session.context)
        was_streaming = is_streaming(discord)
        media_page = open_media_target(session.context, url)
        ensure_discord_connected(discord)
        prepare_media_for_share(media_page, speed=speed)

        # Warm swap: keep the existing Discord share alive when possible by
        # navigating the dedicated broadcast tab in place.
        if not was_streaming:
            start_discord_tab_share(discord)
        else:
            discord.wait_for_timeout(1500)
            if not is_streaming(discord):
                ensure_discord_connected(discord)
                start_discord_tab_share(discord)

        bring_tab_to_front(media_page)
        start_and_fullscreen_media(media_page, speed=speed)

        if not is_streaming(discord):
            raise RuntimeError("Media loaded, but Discord is not actively streaming")
        if "/channels/@me" in discord.url:
            raise RuntimeError("Media loaded, but Discord is no longer in the target server call")

        return {
            "status": "ok",
            "source_kind": classify_media_url(media_page.url),
            "discord_url": discord.url,
            "media_url": media_page.url,
            "title": media_page.title(),
            "streaming": is_streaming(discord),
            "fullscreen": bool(media_page.evaluate("() => !!document.fullscreenElement")),
            "playback_speed": get_playback_speed(media_page),
        }
    finally:
        session.close()


def swap_media_source(url: str, speed: float = 1.0, cdp_url: str = DEFAULT_CDP_URL) -> dict:
    session = connect_over_cdp(cdp_url)
    try:
        media_page = preferred_media_page(session.context)
        if media_page is None:
            raise RuntimeError("No active broadcast tab was found")

        url = normalize_media_url(url)
        kind = classify_media_url(url)
        if kind == "rumble" and "ghostpolitics/livestreams" in url:
            media_page.goto(RUMBLE_LIVESTREAMS_URL, wait_until="domcontentloaded")
            page_url = open_rumble_live(media_page)
        else:
            media_page.goto(url, wait_until="domcontentloaded")
            media_page.wait_for_timeout(2500)
            if kind == "youtube":
                dismiss_youtube_overlays(media_page)
                optimize_youtube_playback(media_page)
            set_capture_title(media_page)
            page_url = media_page.url

        verify_rumble_active(media_page)
        start_and_fullscreen_media(media_page, speed=speed)
        return {
            "status": "ok",
            "source_kind": classify_media_url(page_url),
            "media_url": page_url,
            "title": media_page.title(),
            "fullscreen": bool(media_page.evaluate("() => !!document.fullscreenElement")),
            "playback_speed": get_playback_speed(media_page),
        }
    finally:
        session.close()


def set_media_speed(speed: float, cdp_url: str = DEFAULT_CDP_URL) -> dict:
    session = connect_over_cdp(cdp_url)
    try:
        page = preferred_media_page(session.context)
        if page is None:
            raise RuntimeError("No active media tab with an HTML5 video was found")
        applied = set_playback_speed(page, speed)
        verified = get_playback_speed(page)
        if verified is None:
            raise RuntimeError("Playback speed verification failed")
        return {
            "status": "ok",
            "media_url": page.url,
            "title": page.title(),
            "playback_speed": verified,
        }
    finally:
        session.close()


def set_media_play_state(playing: bool, cdp_url: str = DEFAULT_CDP_URL) -> dict:
    session = connect_over_cdp(cdp_url)
    try:
        page = preferred_media_page(session.context)
        if page is None:
            raise RuntimeError("No active media tab with an HTML5 video was found")
        wait_for_video(page, bring_to_front=False)
        result = page.evaluate(
            """
            (shouldPlay) => {
              const videos = Array.from(document.querySelectorAll('video'));
              const video = videos.find((item) => !item.ended) || videos[0];
              if (!video) {
                return null;
              }
              if (shouldPlay) {
                video.play?.();
              } else {
                video.pause?.();
              }
              return {
                paused: !!video.paused,
                ended: !!video.ended,
                playbackRate: video.playbackRate,
                currentTime: video.currentTime,
              };
            }
            """,
            playing,
        )
        if result is None:
            raise RuntimeError("Could not update media play state")
        return {
            "status": "ok",
            "media_url": page.url,
            "title": page.title(),
            "playing": not bool(result.get("paused")),
            "playback_speed": result.get("playbackRate"),
            "current_time": result.get("currentTime"),
        }
    finally:
        session.close()


def stop_media_share(cdp_url: str = DEFAULT_CDP_URL) -> dict:
    session = connect_over_cdp(cdp_url)
    try:
        discord = discord_page(session.context)
        stopped = stop_discord_tab_share(discord)
        return {
            "status": "ok" if stopped else "noop",
            "discord_url": discord.url,
            "streaming": is_streaming(discord),
        }
    finally:
        session.close()


def get_stream_status(cdp_url: str = DEFAULT_CDP_URL) -> dict:
    session = connect_over_cdp(cdp_url)
    try:
        discord = preferred_discord_page(session.context)
        media_page = preferred_media_page(session.context)
        login_required = bool(discord and discord_login_required(discord))
        return {
            "status": "ok",
            "discord_connected": bool(discord and not login_required and "/channels/@me" not in discord.url),
            "login_required": login_required,
            "share_active": bool(discord and is_streaming(discord)),
            "discord_url": discord.url if discord else "",
            "active_url": media_page.url if media_page else "",
            "active_title": media_page.title() if media_page else "",
            "source_kind": classify_media_url(media_page.url) if media_page and media_page.url else "",
            "playback_speed": get_playback_speed(media_page) if media_page else None,
        }
    finally:
        session.close()


def ghost_show_refresh(cdp_url: str = DEFAULT_CDP_URL) -> dict:
    session = connect_over_cdp(cdp_url)
    try:
        discord = discord_page(session.context)
        rumble = rumble_page(session.context)
        ensure_discord_connected(discord)
        final_url = open_rumble_live(rumble)
        start_and_fullscreen_rumble(rumble)
        return {
            "status": "ok",
            "discord_url": discord.url,
            "rumble_url": final_url,
            "streaming": is_streaming(discord),
            "fullscreen": bool(rumble.evaluate("() => !!document.fullscreenElement")),
        }
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "inspect-rumble":
        print_inspect_rumble_live_candidates()
    elif len(sys.argv) > 3 and sys.argv[1] == "start-media-share":
        print(json.dumps(start_media_share(sys.argv[2], float(sys.argv[3])), indent=2))
    elif len(sys.argv) > 3 and sys.argv[1] == "swap-media-source":
        print(json.dumps(swap_media_source(sys.argv[2], float(sys.argv[3])), indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "set-media-speed":
        print(json.dumps(set_media_speed(float(sys.argv[2])), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "play-media":
        print(json.dumps(set_media_play_state(True), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "pause-media":
        print(json.dumps(set_media_play_state(False), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "stop-media-share":
        print(json.dumps(stop_media_share(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "stream-status":
        print(json.dumps(get_stream_status(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "ghost-show-refresh":
        print(json.dumps(ghost_show_refresh(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "cold-start-ghost-show":
        print(json.dumps(cold_start_ghost_show(), indent=2))
    else:
        print_inspect_discord_controls()
