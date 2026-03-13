from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


DISPLAY = os.environ.get("DISPLAY", ":99")
LOG_PATH = Path(os.environ.get("X11_RECORDER_LOG_PATH", "/app/logs/x11-interactions.jsonl"))
SHOT_DIR = Path(os.environ.get("X11_RECORDER_SHOT_DIR", "/app/logs/x11-shots"))
SHOT_INTERVAL_SECONDS = float(os.environ.get("X11_RECORDER_SHOT_INTERVAL_SECONDS", "1.5"))


def run(*args: str) -> str:
    completed = subprocess.run(args, check=False, capture_output=True, text=True, env={**os.environ, "DISPLAY": DISPLAY})
    return completed.stdout.strip()


def append(event: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")


def active_window() -> dict:
    window_id = run("xdotool", "getactivewindow")
    if not window_id:
        return {"window_id": "", "window_name": "", "window_class": "", "geometry": ""}
    return {
        "window_id": window_id,
        "window_name": run("xdotool", "getwindowname", window_id),
        "window_class": run("xprop", "-id", window_id, "WM_CLASS"),
        "geometry": run("xdotool", "getwindowgeometry", "--shell", window_id),
    }


def mouse_state() -> dict:
    output = run("xdotool", "getmouselocation", "--shell")
    data: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key.lower()] = value
    return data


def screenshot() -> str:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{int(time.time() * 1000)}.png"
    path = SHOT_DIR / filename
    subprocess.run(
        ["import", "-window", "root", str(path)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DISPLAY": DISPLAY},
    )
    return str(path)


def main() -> int:
    running = True

    def handle_signal(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    append({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "type": "x11-recorder-started", "payload": {}})
    last_window_id = ""
    last_mouse = ""
    last_shot = 0.0

    while running:
        now = time.time()
        window = active_window()
        mouse = mouse_state()
        mouse_key = f"{mouse.get('x','')}:{mouse.get('y','')}:{mouse.get('screen','')}:{mouse.get('window','')}"

        if window.get("window_id") != last_window_id:
            append(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "type": "active-window",
                    "payload": window,
                }
            )
            last_window_id = window.get("window_id", "")

        if mouse_key != last_mouse:
            append(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "type": "mouse",
                    "payload": mouse,
                }
            )
            last_mouse = mouse_key

        if now - last_shot >= SHOT_INTERVAL_SECONDS:
            append(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "type": "screenshot",
                    "payload": {"path": screenshot(), "window": window},
                }
            )
            last_shot = now

        time.sleep(0.2)

    append({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "type": "x11-recorder-stopped", "payload": {}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
