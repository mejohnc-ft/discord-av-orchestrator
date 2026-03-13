FROM compose-browser-use

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fluxbox \
    imagemagick \
    libva-drm2 \
    libva2 \
    mesa-va-drivers \
    mesa-vulkan-drivers \
    novnc \
    vainfo \
    websockify \
    x11-utils \
    xdotool \
    xinput \
    x11vnc \
    xvfb \
    xterm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh
COPY automation.py /app/automation.py
COPY control_server.py /app/control_server.py
COPY recorder.py /app/recorder.py
COPY runner.py /app/runner.py

RUN chmod +x /app/entrypoint.sh

ENV DISPLAY=:99
ENV XVFB_WHD=1600x900x24
ENV NOVNC_PORT=6080
ENV VNC_PORT=5900
ENV PROFILE_DIR=/data/profile
ENV RUNNER_MODE=idle
ENV START_URL=https://discord.com/app
ENV CONTROL_PORT=8096

ENTRYPOINT ["/app/entrypoint.sh"]
