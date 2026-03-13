# Discord Streaming Automation

Portable bundle of the Discord browser streaming stack that was built and tested on `192.168.0.54`.

This repo is split into three pieces:

- `browser-worker/`
  - Persistent Chromium + Playwright worker
  - noVNC virtual desktop
  - Discord call join + tab share automation
  - Stream control API on port `8096`
- `franklin-bot/`
  - Discord bot integration for `/s-start`, `/s-swap`, `/s-stop`, `/s-play`, `/s-pause`, `/s-speed`, `/s-status`
- `mission-control/`
  - Franklin Mission Control dashboard integration for the Ghost Stream Browser card

## Current operating model

- Persistent browser profile and pinned tabs
- Dedicated Discord tab, Rumble tab, and broadcast tab
- Browser worker resource profile:
  - `4` vCPU
  - `8 GiB` RAM
  - `1 GiB` shared memory
- AMD render device passthrough via `/dev/dri/renderD128`
- Franklin orchestrates the worker; Franklin is not the screen-sharing identity

## What is included

- Browser worker container files copied from the working local packaging tree
- Franklin bot code plus Dockerfile
- Mission Control dashboard files plus systemd unit example
- Example service compose file and environment template in `deploy/`

## What is not included

- Live browser profile data
- Discord tokens
- Existing logs, screenshots, or temporary debug scripts
- Host-specific secrets

## Suggested deployment order

1. Set up the browser worker from `browser-worker/`
2. Log the worker browser into Discord manually once via noVNC
3. Start the Franklin bot from `franklin-bot/`
4. Deploy Mission Control from `mission-control/`
5. Wire your host service manager using the files in `deploy/`

## Notes

- The worker expects a real Discord browser session, not a bot token, for the shared stream account.
- AdGuard or other required browser extensions should be managed in the persisted browser profile on the target machine.
- This repo preserves the current implementation state, including the YouTube watch-page normalization and the worker control API.
