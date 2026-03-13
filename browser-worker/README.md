# Discord Browser Worker

Reference container for a scheduled browser worker with:

- Playwright + Chromium
- Xvfb virtual display
- Fluxbox window manager
- x11vnc + noVNC for remote manual control
- persistent browser profile volume

## Sign-in model

Use a real Discord browser session, not a bot token.

1. Build and start the worker:

```bash
docker compose up -d --build
```

2. Switch it into manual login mode:

```bash
docker compose stop discord-browser-worker
RUNNER_MODE=manual-login docker compose up -d discord-browser-worker
```

3. Open noVNC:

```text
http://<host>:6086/vnc.html
```

4. Manually log into Discord in the browser window.
5. Complete 2FA if prompted.
6. Leave the profile logged in.

The persisted Chromium profile is stored in `./data/profile`. Future runs reuse that profile, so you do not need to store a Discord password or token in the container config.

Manual login mode now launches the raw Chromium binary instead of a Playwright-controlled browser. That is intentional, because some sites with anti-bot checks are more likely to challenge or loop when the browser is visibly automation-driven.

## Reuse the logged-in profile

For a simple scheduled open/reuse flow:

```bash
docker compose stop discord-browser-worker
RUNNER_MODE=open TARGET_URL=https://discord.com/app docker compose up -d discord-browser-worker
```

This also uses raw Chromium with the same persisted profile.

## Persistent browser daemon

For automation that attaches over CDP later, keep Chromium running continuously:

```bash
docker compose stop discord-browser-worker
RUNNER_MODE=browser-daemon TARGET_URL=https://discord.com/channels/160637406985322496/465358825571221505 docker compose up -d --build discord-browser-worker
```

This keeps the raw Chromium session alive with:

- the persisted Discord login/profile
- remote debugging on port `9222` inside the container
- noVNC still available on `http://<host>:6086/vnc.html`

Then run the Ghost Show refresh flow on demand:

```bash
docker exec discord-browser-worker python3 /app/automation.py ghost-show-refresh
```

`ghost-show-refresh` assumes the Discord browser window is already the shared window. It:

1. Reuses the existing browser session over CDP.
2. Ensures the Discord channel page is open.
3. Opens `https://rumble.com/c/ghostpolitics/livestreams?e9s=src_v1_cbl`.
4. Clicks the top-left Ghost Show tile, preferring a live-marked tile when present.
5. Starts playback and fullscreens the Rumble page.

This is the reliable first automation layer because it avoids the browser-native screen-share picker. If you keep the Discord browser window as the actively shared window, scheduled runs only need to refresh the shared content.

## Interaction recorder

If you want to teach the cold-start flow by demonstration, use the recorder:

```bash
docker exec -d discord-browser-worker python3 /app/recorder.py
```

It attaches to the live Chromium session over CDP and writes JSONL events to:

```text
/home/mejohnc/discord-browser-worker/data/logs/browser-interactions.jsonl
```

Recorded events include:

- page URL and title
- clicks with coordinates
- element text, aria-label, title, role, href, and CSS-path candidates
- typed input values
- key presses

This is better than a screen recording for DOM-driven steps because it gives replayable selectors. A screen recording is still useful for native browser UI like Chrome's share-picker if we need to capture that layer too.

## X11 recorder

For native browser chrome and the Chrome share-picker, use the X11-side recorder:

```bash
docker exec -d discord-browser-worker python3 /app/x11_recorder.py
```

It writes:

- interaction log:
  `/home/mejohnc/discord-browser-worker/data/logs/x11-interactions.jsonl`
- periodic screenshots:
  `/home/mejohnc/discord-browser-worker/data/logs/x11-shots/`

This recorder is lower fidelity than DOM logging, but it captures the pieces the DOM recorder cannot see:

- active window changes
- pointer movement targets
- periodic screenshots of native UI

Recommended workflow for teaching the cold-start share flow:

1. Start both recorders.
2. Perform the full flow once in noVNC.
3. Stop the recorders.
4. Use the DOM trace for page-level selectors and the X11 trace/screenshots for the native share picker.

## Scheduling

Recommended pattern on the remote host:

- keep the image and profile volume persistent
- use the new `cron_upsert_job` agent tool or `systemd` timers to run `docker exec` against the long-lived browser-daemon container

Example cron command body:

```bash
docker exec discord-browser-worker python3 /app/automation.py ghost-show-refresh
```

## Important caveats

- This scaffold does not implement Discord’s share-screen click flow yet.
- Screen sharing is the brittle part and may break on permission prompts or UI changes.
- `sessionStorage` is not persisted automatically; cookies and local-storage-style state are.
- If Discord invalidates the session, repeat the manual login flow.
