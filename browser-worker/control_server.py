from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from automation import (
    get_stream_status,
    set_media_play_state,
    set_media_speed,
    start_media_share,
    swap_media_source,
    stop_media_share,
)


CONTROL_PORT = int(os.environ.get("CONTROL_PORT", "8096"))
JOB_LOCK = threading.Lock()
STATE = {
    "last_action": "idle",
    "last_error": "",
    "last_request": None,
}


def write_state(action: str, error: str = "", request: dict | None = None) -> None:
    STATE["last_action"] = action
    STATE["last_error"] = error
    STATE["last_request"] = request


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json({"status": "ok", "port": CONTROL_PORT})
            return
        if self.path == "/status":
            try:
                status = get_stream_status()
                status.update(
                    {
                        "last_action": STATE["last_action"],
                        "last_error": STATE["last_error"],
                        "last_request": STATE["last_request"],
                    }
                )
                self._json(status)
            except Exception as exc:
                self._json({"status": "error", "error": str(exc)}, 500)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/stream/start":
            payload = self._read_json()
            url = str(payload.get("url") or "").strip()
            speed = float(payload.get("speed") or 1.0)
            request = {"url": url, "speed": speed, "requestor": payload.get("requestor")}
            if not url:
                self._json({"status": "error", "error": "url required"}, 400)
                return
            if not JOB_LOCK.acquire(blocking=False):
                self._json({"status": "busy", "error": "another worker job is running"}, 409)
                return
            try:
                write_state("stream_start", request=request)
                result = start_media_share(url, speed=speed)
                write_state("stream_start_ok", request=request)
                self._json(result)
            except Exception as exc:
                write_state("stream_start_error", str(exc), request=request)
                self._json({"status": "error", "error": str(exc)}, 500)
            finally:
                JOB_LOCK.release()
            return

        if self.path == "/stream/stop":
            if not JOB_LOCK.acquire(blocking=False):
                self._json({"status": "busy", "error": "another worker job is running"}, 409)
                return
            try:
                write_state("stream_stop")
                result = stop_media_share()
                write_state("stream_stop_ok")
                self._json(result)
            except Exception as exc:
                write_state("stream_stop_error", str(exc))
                self._json({"status": "error", "error": str(exc)}, 500)
            finally:
                JOB_LOCK.release()
            return

        if self.path == "/stream/swap":
            payload = self._read_json()
            url = str(payload.get("url") or "").strip()
            speed = float(payload.get("speed") or 1.0)
            request = {"url": url, "speed": speed, "requestor": payload.get("requestor")}
            if not url:
                self._json({"status": "error", "error": "url required"}, 400)
                return
            if not JOB_LOCK.acquire(blocking=False):
                self._json({"status": "busy", "error": "another worker job is running"}, 409)
                return
            try:
                write_state("stream_swap", request=request)
                result = swap_media_source(url, speed=speed)
                write_state("stream_swap_ok", request=request)
                self._json(result)
            except Exception as exc:
                write_state("stream_swap_error", str(exc), request=request)
                self._json({"status": "error", "error": str(exc)}, 500)
            finally:
                JOB_LOCK.release()
            return

        if self.path == "/stream/speed":
            payload = self._read_json()
            speed = float(payload.get("speed") or 1.0)
            if not JOB_LOCK.acquire(blocking=False):
                self._json({"status": "busy", "error": "another worker job is running"}, 409)
                return
            try:
                write_state("stream_speed", request={"speed": speed})
                result = set_media_speed(speed)
                write_state("stream_speed_ok", request={"speed": speed})
                self._json(result)
            except Exception as exc:
                write_state("stream_speed_error", str(exc), request={"speed": speed})
                self._json({"status": "error", "error": str(exc)}, 500)
            finally:
                JOB_LOCK.release()
            return

        if self.path == "/stream/play":
            if not JOB_LOCK.acquire(blocking=False):
                self._json({"status": "busy", "error": "another worker job is running"}, 409)
                return
            try:
                write_state("stream_play")
                result = set_media_play_state(True)
                write_state("stream_play_ok")
                self._json(result)
            except Exception as exc:
                write_state("stream_play_error", str(exc))
                self._json({"status": "error", "error": str(exc)}, 500)
            finally:
                JOB_LOCK.release()
            return

        if self.path == "/stream/pause":
            if not JOB_LOCK.acquire(blocking=False):
                self._json({"status": "busy", "error": "another worker job is running"}, 409)
                return
            try:
                write_state("stream_pause")
                result = set_media_play_state(False)
                write_state("stream_pause_ok")
                self._json(result)
            except Exception as exc:
                write_state("stream_pause_error", str(exc))
                self._json({"status": "error", "error": str(exc)}, 500)
            finally:
                JOB_LOCK.release()
            return

        self._json({"error": "not found"}, 404)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
