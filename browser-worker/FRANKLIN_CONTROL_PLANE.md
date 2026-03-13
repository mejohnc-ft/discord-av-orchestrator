# Franklin Browser Control Plane

## Goal

Let `Franklin` act as the Discord-native control surface for the existing `discord-browser-worker` without changing the worker's Discord identity.

The browser worker remains the account that:
- joins `The Oval Office`
- starts tab share with audio
- keeps the media tab in focus

`Franklin` only orchestrates the worker.

## Operating Model

- The browser worker stays running continuously.
- The persisted Chromium profile remains mounted at `/home/mejohnc/discord-browser-worker/data/profile`.
- Cold-start scheduling is no longer required for normal use.
- The browser worker exposes a small local control API.
- `Franklin` slash commands call that API.
- Mission Control reads the same API for status.

## Scope

### In scope

- Start sharing a YouTube or Rumble URL through the existing worker
- Adjust HTML5 video playback speed
- Stop an active share
- Report worker status in Discord
- Reuse the existing Discord account/session already cached in the browser worker

### Out of scope

- Franklin joining Discord voice directly
- Replacing the worker's Discord account
- Multi-stream concurrency
- Full browser general-purpose remote control through Franklin

## Architecture

### Components

1. `Franklin Discord Bot`
- Receives slash commands from Discord users
- Validates permissions and arguments
- Calls the worker control API
- Returns success/failure/status to Discord

2. `Browser Worker Control API`
- Runs on the host or inside `discord-browser-worker`
- Owns job state and serialization
- Calls browser automation routines in `automation.py`

3. `Browser Worker Runtime`
- Existing Chromium + Xvfb + noVNC + persisted profile
- Existing Discord join/share behavior
- Existing CDP-based browser control

4. `Mission Control`
- Reads worker state for `/agents`
- Shows last action, active URL, playback speed, share state, and recent logs

## Network Recommendation

Preferred first implementation:
- expose the worker control API on a host port
- let `Franklin` call it via `http://host.docker.internal:<port>`

Reason:
- `discord-bot` and `discord-browser-worker` are currently on different Docker networks
- this avoids immediate Compose network surgery

Later improvement:
- attach the worker to `llm-net` and use a shared service name

## Control API

### `GET /status`

Returns:
- `worker_running`
- `browser_ready`
- `discord_connected`
- `share_active`
- `active_url`
- `active_title`
- `source_kind`
- `playback_speed`
- `started_at`
- `last_action`
- `last_error`

### `POST /stream/start`

Body:
```json
{
  "url": "https://youtube.com/watch?v=...",
  "speed": 1.25,
  "channel": "The Oval Office",
  "requestor": "discord-user-id-or-name"
}
```

Behavior:
- ensure worker is up
- open URL in media tab
- wait for HTML5 video
- start playback
- set speed
- ensure Discord is connected to `The Oval Office`
- start tab share with audio
- return focus to the media tab

### `POST /stream/stop`

Behavior:
- stop active Discord share if present
- leave the media tab open unless explicitly told to close it

### `POST /stream/speed`

Body:
```json
{
  "speed": 1.5
}
```

Behavior:
- update active HTML5 video playback rate
- return effective playback rate

### `POST /stream/open`

Body:
```json
{
  "url": "https://youtube.com/watch?v=..."
}
```

Behavior:
- open media without starting share
- useful for staging content before going live

## Franklin Commands

### Required slash commands

- `/stream_start url:<link> speed:<number>`
- `/stream_stop`
- `/stream_speed speed:<number>`
- `/stream_status`
- `/stream_open url:<link>`

### Optional later commands

- `/stream_pause`
- `/stream_resume`
- `/stream_seek seconds:<int>`
- `/stream_replace url:<link> speed:<number>`

## Worker State Model

Single active job only.

```json
{
  "job_id": "uuid",
  "status": "idle|starting|sharing|stopping|error",
  "source_kind": "youtube|rumble|generic-html5",
  "active_url": "",
  "active_title": "",
  "playback_speed": 1.0,
  "discord_connected": false,
  "share_active": false,
  "started_at": "",
  "updated_at": "",
  "requestor": "",
  "last_action": "",
  "last_error": ""
}
```

## Media Automation Strategy

### YouTube

- open direct watch URL
- dismiss cookie or minor overlays if needed
- locate HTML5 `video`
- call JS:
  - `video.play()`
  - `video.playbackRate = <speed>`
- fullscreen if desired

### Rumble

- keep the current proven Ghost flow
- also support direct video URLs

### Generic HTML5

- if `document.querySelector("video")` exists, use the same JS controls

## Playback Speed

Primary control path:
- direct JS on the page's HTML5 video element

Secondary/manual path:
- install `HTML5 Video Speed Controller` in the worker profile

Why:
- JS is deterministic for Franklin commands
- extension is still useful through noVNC for manual intervention

## Discord Share Path

Keep the current approach:
- browser worker's persisted Discord session
- existing `The Oval Office` join logic
- existing native picker/tab-share flow

Do not move this responsibility to Franklin.

## Permissions

Franklin commands should be restricted by one of:
- allowlisted Discord role IDs
- allowlisted channel IDs
- both

Recommended:
- only trusted roles may run `stream_start`, `stream_stop`, `stream_speed`
- `stream_status` can be broader

## Logging

### Control API logs

- one JSONL file for command requests/results
- one current-state JSON file

Suggested paths:
- `/home/mejohnc/discord-browser-worker/data/logs/control-plane.jsonl`
- `/home/mejohnc/discord-browser-worker/data/logs/control-state.json`

### Mission Control display

Show:
- active state
- current URL/title
- current speed
- requestor
- last action
- last error
- recent control-plane log lines

## Failure Handling

### Expected failures

- worker unavailable
- YouTube page changed
- no HTML5 video found
- Discord disconnected
- native share picker mismatch
- active job already running

### API behavior

Return structured errors with:
- `status`
- `error_code`
- `message`
- `recoverable`

## Implementation Phases

### Phase 1

- worker control API
- `status`, `start`, `stop`, `speed`
- YouTube support
- Franklin slash commands

### Phase 2

- Mission Control reads control-plane state directly
- better event logs
- `open`, `pause`, `resume`

### Phase 3

- role/channel policy
- queue/replace semantics
- richer site-specific handlers

## Recommendation

Build the first version as:
- a small Python HTTP server near the worker
- host-port exposed
- Franklin calling it from Discord slash commands
- direct HTML5 JS playback control
- existing Discord share logic reused unchanged

This is the fastest path with the least risk because it preserves the only part that already proved reliable: the worker's own Discord session and share behavior.
