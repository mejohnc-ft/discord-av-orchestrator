from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright


CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:9222")
LOG_PATH = Path(os.environ.get("RECORDER_LOG_PATH", "/app/logs/browser-interactions.jsonl"))

INJECT_SCRIPT = r"""
(() => {
  if (window.__codexRecorderInstalled) return;
  window.__codexRecorderInstalled = true;

  function norm(value) {
    return (value || "").replace(/\s+/g, " ").trim().slice(0, 300);
  }

  function cssPath(el) {
    if (!(el instanceof Element)) return "";
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += "#" + CSS.escape(node.id);
        parts.unshift(part);
        break;
      }
      const classNames = Array.from(node.classList || []).slice(0, 2).map((c) => "." + CSS.escape(c)).join("");
      if (classNames) {
        part += classNames;
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((child) => child.tagName === node.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function describeTarget(el) {
    const rect = el.getBoundingClientRect();
    return {
      tag: el.tagName,
      text: norm(el.innerText || el.textContent),
      ariaLabel: norm(el.getAttribute("aria-label")),
      title: norm(el.getAttribute("title")),
      role: norm(el.getAttribute("role")),
      name: norm(el.getAttribute("name")),
      placeholder: norm(el.getAttribute("placeholder")),
      href: norm(el.getAttribute("href")),
      dataTestId: norm(el.getAttribute("data-testid")),
      cssPath: cssPath(el),
      rect: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
    };
  }

  function record(type, payload) {
    try {
      window.codexRecordEvent({
        type,
        page: {
          url: location.href,
          title: document.title,
        },
        ts: new Date().toISOString(),
        payload,
      });
    } catch (error) {
      console.error("codex recorder error", error);
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target && event.target.closest ? event.target.closest("*") : event.target;
    if (!target || !(target instanceof Element)) return;
    record("click", {
      x: Math.round(event.clientX),
      y: Math.round(event.clientY),
      target: describeTarget(target),
    });
  }, true);

  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!target || !(target instanceof Element)) return;
    record("input", {
      value: "value" in target ? String(target.value).slice(0, 300) : "",
      target: describeTarget(target),
    });
  }, true);

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (!target || !(target instanceof Element)) return;
    record("keydown", {
      key: event.key,
      code: event.code,
      ctrlKey: !!event.ctrlKey,
      altKey: !!event.altKey,
      metaKey: !!event.metaKey,
      shiftKey: !!event.shiftKey,
      target: describeTarget(target),
    });
  }, true);

  window.addEventListener("focus", () => {
    record("window-focus", {});
  });

  record("recorder-attached", {});
})();
"""


def append_jsonl(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")


def attach_to_page(page: Page) -> None:
    page.add_init_script(INJECT_SCRIPT)
    try:
        page.evaluate(INJECT_SCRIPT)
    except Exception as exc:  # noqa: BLE001
        append_jsonl(
            LOG_PATH,
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "type": "attach-error",
                "page": {"url": page.url, "title": ""},
                "payload": {"error": str(exc)},
            },
        )


def attach_to_context(context: BrowserContext) -> None:
    context.expose_binding("codexRecordEvent", lambda _source, payload: append_jsonl(LOG_PATH, payload))
    context.add_init_script(INJECT_SCRIPT)
    for page in context.pages:
        attach_to_page(page)
    context.on("page", attach_to_page)


def main() -> int:
    running = True

    def handle_signal(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        attach_to_context(context)
        append_jsonl(
            LOG_PATH,
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "type": "recorder-started",
                "page": {"url": "", "title": ""},
                "payload": {"cdp_url": CDP_URL},
            },
        )
        try:
            while running:
                time.sleep(0.5)
        finally:
            append_jsonl(
                LOG_PATH,
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "type": "recorder-stopped",
                    "page": {"url": "", "title": ""},
                    "payload": {},
                },
            )
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
