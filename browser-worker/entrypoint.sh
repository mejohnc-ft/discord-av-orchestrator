#!/usr/bin/env bash
set -euo pipefail

mkdir -p "${PROFILE_DIR}" /tmp/.X11-unix

DISPLAY_NUM="${DISPLAY#:}"
rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}"
pkill -f "Xvfb ${DISPLAY}" 2>/dev/null || true

Xvfb "${DISPLAY}" -screen 0 "${XVFB_WHD}" &
XVFB_PID=$!

fluxbox >/tmp/fluxbox.log 2>&1 &
FLUXBOX_PID=$!

x11vnc -display "${DISPLAY}" -forever -shared -nopw -rfbport "${VNC_PORT}" >/tmp/x11vnc.log 2>&1 &
X11VNC_PID=$!

/usr/share/novnc/utils/novnc_proxy --vnc "127.0.0.1:${VNC_PORT}" --listen "${NOVNC_PORT}" >/tmp/novnc.log 2>&1 &
NOVNC_PID=$!

python3 /app/control_server.py >/tmp/control-server.log 2>&1 &
CONTROL_PID=$!

cleanup() {
  kill "${CONTROL_PID}" "${NOVNC_PID}" "${X11VNC_PID}" "${FLUXBOX_PID}" "${XVFB_PID}" 2>/dev/null || true
}

trap cleanup EXIT

if [[ "${RUNNER_MODE}" != "idle" ]]; then
  python3 /app/runner.py
fi

wait -n "${NOVNC_PID}" "${X11VNC_PID}" "${FLUXBOX_PID}" "${XVFB_PID}"
