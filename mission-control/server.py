#!/usr/bin/env python3
"""Mission Control dashboard server for Radeon AI Cluster.
Serves the frontend and provides stats, service health, secrets management,
model management, agent management, command palette, and CLI terminal APIs."""

import http.server
import base64
import hashlib
import json
import subprocess
import os
import random
import re
import urllib.request
import urllib.error
import urllib.parse
import stat
import shlex
import ssl
import http.client
import threading
import time
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

BMC_HOST = "192.168.0.209"
BMC_PROXY_PORT = 8210
COMFYUI_BASE_URL = os.environ.get("COMFYUI_BASE_URL", "http://100.70.72.74:8190").rstrip("/")
COMFYUI_GALLERY_LIMIT = int(os.environ.get("COMFYUI_GALLERY_LIMIT", "18"))
COMFYUI_IMAGE_MODELS = [
    "sd_xl_turbo_1.0_fp16.safetensors",
    "sd_xl_base_1.0_0.9vae.safetensors",
]
COMFYUI_DEFAULT_MODEL = os.environ.get("COMFYUI_DEFAULT_MODEL", COMFYUI_IMAGE_MODELS[0])
COMFYUI_DEFAULT_NEGATIVE = os.environ.get(
    "COMFYUI_DEFAULT_NEGATIVE",
    "blurry, malformed hands, distorted face, extra limbs, duplicate subject, unreadable text"
)
COMFYUI_REMOTE_INPUT_DIR = os.environ.get("COMFYUI_REMOTE_INPUT_DIR", r"C:\Users\mejohnc\ComfyUI\input")
TTS_BASE_URL = os.environ.get("MISSION_TTS_BASE_URL", "http://localhost:8880").rstrip("/")
STT_BASE_URL = os.environ.get("MISSION_STT_BASE_URL", "http://localhost:8001").rstrip("/")
DEFAULT_AV_TTS_MODEL = os.environ.get("MISSION_TTS_DEFAULT_MODEL", "kokoro").strip() or "kokoro"
DEFAULT_AV_STT_MODEL = os.environ.get("MISSION_STT_DEFAULT_MODEL", "Systran/faster-whisper-small").strip() or "Systran/faster-whisper-small"
DEFAULT_AV_CLIP_FORMAT = os.environ.get("MISSION_AV_CLIP_FORMAT", "video/h264-mp4").strip() or "video/h264-mp4"
AV_CLIP_LIMIT = max(4, int(os.environ.get("MISSION_AV_CLIP_LIMIT", "8")))
MAX_REMOTE_IMAGE_BYTES = int(os.environ.get("MISSION_MAX_REMOTE_IMAGE_BYTES", str(20 * 1024 * 1024)))
QDRANT_BASE_URL = os.environ.get("QDRANT_BASE_URL", "http://localhost:6333").rstrip("/")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
KNOWLEDGE_COLLECTION = os.environ.get("MISSION_KNOWLEDGE_COLLECTION", "mission_obsidian_library")
KNOWLEDGE_EMBED_MODEL = os.environ.get("MISSION_KNOWLEDGE_EMBED_MODEL", "bge-m3:latest")
KNOWLEDGE_SAMPLE_LIMIT = int(os.environ.get("MISSION_KNOWLEDGE_SAMPLE_LIMIT", "6"))
KNOWLEDGE_RESULT_LIMIT = int(os.environ.get("MISSION_KNOWLEDGE_RESULT_LIMIT", "8"))
OBSIDIAN_VAULT_DIR = Path(os.environ.get("MISSION_OBSIDIAN_VAULT_DIR", "/opt/llm-stack/data/obsidian-headless/vault")).resolve()
OBSIDIAN_TREE_LIMIT = int(os.environ.get("MISSION_OBSIDIAN_TREE_LIMIT", "2000"))
NOTE_CHUNK_SIZE = int(os.environ.get("MISSION_NOTE_CHUNK_SIZE", "1400"))
NOTE_CHUNK_OVERLAP = int(os.environ.get("MISSION_NOTE_CHUNK_OVERLAP", "180"))
WORKBENCH_CONTEXT_LIMIT = int(os.environ.get("MISSION_WORKBENCH_CONTEXT_LIMIT", "24000"))
MISSION_DATA_DIR = Path(os.environ.get("MISSION_DATA_DIR", "/opt/llm-stack/data/mission-control")).resolve()
WORKBENCH_BUNDLES_FILE = MISSION_DATA_DIR / "workbench-bundles.json"
WORKBENCH_PROJECTS_FILE = MISSION_DATA_DIR / "workbench-projects.json"
VAULT_WATCH_INTERVAL = max(5, int(os.environ.get("MISSION_VAULT_WATCH_INTERVAL", "20")))
VAULT_WATCH_BATCH = max(1, int(os.environ.get("MISSION_VAULT_WATCH_BATCH", "4")))
IMAGE_AUTO_RELEASE_IDLE_SECONDS = max(0, int(os.environ.get("MISSION_IMAGE_AUTO_RELEASE_IDLE_SECONDS", "180")))
IMAGE_RELEASE_CHECK_INTERVAL = max(10, int(os.environ.get("MISSION_IMAGE_RELEASE_CHECK_INTERVAL", "30")))
IMAGE_RELEASE_MIN_USED_BYTES = int(float(os.environ.get("MISSION_IMAGE_RELEASE_MIN_USED_GB", "8")) * 1024 * 1024 * 1024)

WORKBENCH_BUNDLES_LOCK = threading.Lock()
WORKBENCH_PROJECTS_LOCK = threading.Lock()
VAULT_WATCH_LOCK = threading.Lock()
VAULT_WATCH_QUEUE = []
VAULT_WATCH_MANIFEST = {}
VAULT_WATCH_STATE = {
    "available": False,
    "running": False,
    "initialized": False,
    "pending_count": 0,
    "pending_paths": [],
    "processing_path": "",
    "manifest_count": 0,
    "last_scan_started_at": 0,
    "last_scan_completed_at": 0,
    "last_indexed_at": 0,
    "last_deleted_at": 0,
    "last_error": "",
    "last_scan_change_count": 0,
    "last_scan_deleted_count": 0,
    "last_scan_indexed_count": 0,
}
IMAGE_NODE_LOCK = threading.Lock()
IMAGE_NODE_STATE = {
    "auto_release_enabled": IMAGE_AUTO_RELEASE_IDLE_SECONDS > 0,
    "idle_release_after_seconds": IMAGE_AUTO_RELEASE_IDLE_SECONDS,
    "last_submit_at": 0,
    "last_release_at": 0,
    "last_release_reason": "",
    "last_release_status": "",
    "last_error": "",
    "released_since_submit": False,
}
AV_CLIP_META_CACHE = {}


def _default_model_catalog():
    return [
        {
            "id": "sd_xl_turbo_1.0_fp16.safetensors",
            "name": "sd_xl_turbo_1.0_fp16.safetensors",
            "label": "SDXL Turbo 1.0 FP16",
            "family": "sdxl_checkpoint",
            "mode": "txt2img+img2img",
        },
        {
            "id": "sd_xl_base_1.0_0.9vae.safetensors",
            "name": "sd_xl_base_1.0_0.9vae.safetensors",
            "label": "SDXL Base 1.0 + VAE",
            "family": "sdxl_checkpoint",
            "mode": "txt2img+img2img",
        },
    ]


def _ensure_mission_data_dir():
    MISSION_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


def _watch_timestamp():
    return int(time.time())


def _vault_signature(path):
    stat_info = path.stat()
    return (int(stat_info.st_mtime_ns), int(stat_info.st_size))


def _iter_vault_markdown_paths():
    if not OBSIDIAN_VAULT_DIR.exists():
        return
    for path in OBSIDIAN_VAULT_DIR.rglob("*.md"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(OBSIDIAN_VAULT_DIR).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        yield path


def _set_watch_state(**updates):
    with VAULT_WATCH_LOCK:
        VAULT_WATCH_STATE.update(updates)
        VAULT_WATCH_STATE["available"] = OBSIDIAN_VAULT_DIR.exists()
        VAULT_WATCH_STATE["pending_count"] = len(VAULT_WATCH_QUEUE)
        VAULT_WATCH_STATE["pending_paths"] = list(VAULT_WATCH_QUEUE[:6])
        VAULT_WATCH_STATE["manifest_count"] = len(VAULT_WATCH_MANIFEST)


def _clear_watch_queue_path(rel_path):
    if not rel_path:
        return
    with VAULT_WATCH_LOCK:
        while rel_path in VAULT_WATCH_QUEUE:
            VAULT_WATCH_QUEUE.remove(rel_path)
        if VAULT_WATCH_STATE.get("processing_path") == rel_path:
            VAULT_WATCH_STATE["processing_path"] = ""
        VAULT_WATCH_STATE["pending_count"] = len(VAULT_WATCH_QUEUE)
        VAULT_WATCH_STATE["pending_paths"] = list(VAULT_WATCH_QUEUE[:6])


def _enqueue_watch_path(rel_path):
    rel_path = str(rel_path or "").strip()
    if not rel_path:
        return
    with VAULT_WATCH_LOCK:
        if rel_path == VAULT_WATCH_STATE.get("processing_path"):
            return
        if rel_path not in VAULT_WATCH_QUEUE:
            VAULT_WATCH_QUEUE.append(rel_path)
        VAULT_WATCH_STATE["pending_count"] = len(VAULT_WATCH_QUEUE)
        VAULT_WATCH_STATE["pending_paths"] = list(VAULT_WATCH_QUEUE[:6])


def _mark_watch_indexed(rel_path):
    rel_path = str(rel_path or "").strip()
    if not rel_path:
        return
    try:
        path = _resolve_note_path(rel_path)
        if path.exists() and path.is_file():
            VAULT_WATCH_MANIFEST[rel_path] = _vault_signature(path)
        else:
            VAULT_WATCH_MANIFEST.pop(rel_path, None)
    except Exception:
        pass
    _clear_watch_queue_path(rel_path)
    _set_watch_state(last_indexed_at=_watch_timestamp(), last_error="")


def _mark_watch_deleted(rel_path):
    rel_path = str(rel_path or "").strip()
    if not rel_path:
        return
    VAULT_WATCH_MANIFEST.pop(rel_path, None)
    _clear_watch_queue_path(rel_path)
    _set_watch_state(last_deleted_at=_watch_timestamp(), last_error="")


def get_watch_status():
    with VAULT_WATCH_LOCK:
        return dict(VAULT_WATCH_STATE)


def _baseline_watch_manifest():
    VAULT_WATCH_MANIFEST.clear()
    if not OBSIDIAN_VAULT_DIR.exists():
        _set_watch_state(available=False, initialized=True, manifest_count=0)
        return
    for path in _iter_vault_markdown_paths():
        rel_path = _safe_note_relative(path)
        VAULT_WATCH_MANIFEST[rel_path] = _vault_signature(path)
    _set_watch_state(available=True, initialized=True, manifest_count=len(VAULT_WATCH_MANIFEST), last_error="")


def _dequeue_watch_batch(limit):
    with VAULT_WATCH_LOCK:
        batch = VAULT_WATCH_QUEUE[:limit]
        del VAULT_WATCH_QUEUE[:limit]
        VAULT_WATCH_STATE["pending_count"] = len(VAULT_WATCH_QUEUE)
        VAULT_WATCH_STATE["pending_paths"] = list(VAULT_WATCH_QUEUE[:6])
    return batch


def _vault_watch_scan():
    current = {}
    changed = []
    for path in _iter_vault_markdown_paths():
        rel_path = _safe_note_relative(path)
        signature = _vault_signature(path)
        current[rel_path] = signature
        if VAULT_WATCH_MANIFEST.get(rel_path) != signature:
            changed.append(rel_path)
    deleted = [rel_path for rel_path in VAULT_WATCH_MANIFEST.keys() if rel_path not in current]
    for rel_path in changed:
        _enqueue_watch_path(rel_path)
    for rel_path in deleted:
        try:
            qdrant_delete_path(rel_path)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        finally:
            _mark_watch_deleted(rel_path)
    for rel_path, signature in current.items():
        VAULT_WATCH_MANIFEST[rel_path] = signature
    _set_watch_state(
        manifest_count=len(VAULT_WATCH_MANIFEST),
        last_scan_change_count=len(changed),
        last_scan_deleted_count=len(deleted),
    )


def _vault_watch_process():
    indexed = 0
    for rel_path in _dequeue_watch_batch(VAULT_WATCH_BATCH):
        with VAULT_WATCH_LOCK:
            VAULT_WATCH_STATE["processing_path"] = rel_path
        try:
            reindex_note(rel_path)
            indexed += 1
            _mark_watch_indexed(rel_path)
        except FileNotFoundError:
            _mark_watch_deleted(rel_path)
        except Exception as exc:
            _enqueue_watch_path(rel_path)
            _set_watch_state(last_error=str(exc))
            with VAULT_WATCH_LOCK:
                VAULT_WATCH_STATE["processing_path"] = ""
            break
    with VAULT_WATCH_LOCK:
        VAULT_WATCH_STATE["processing_path"] = ""
        VAULT_WATCH_STATE["last_scan_indexed_count"] = indexed


def _vault_watch_loop():
    _baseline_watch_manifest()
    while True:
        started = _watch_timestamp()
        _set_watch_state(running=True, last_scan_started_at=started)
        try:
            if OBSIDIAN_VAULT_DIR.exists():
                _vault_watch_scan()
                _vault_watch_process()
            else:
                _set_watch_state(available=False, last_error="Synced vault not found")
        except Exception as exc:
            _set_watch_state(last_error=str(exc))
        finally:
            _set_watch_state(last_scan_completed_at=_watch_timestamp())
        time.sleep(VAULT_WATCH_INTERVAL)


def _load_bundles_from_disk():
    _ensure_mission_data_dir()
    if not WORKBENCH_BUNDLES_FILE.exists():
        return []
    try:
        data = json.loads(WORKBENCH_BUNDLES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    bundles = []
    for item in data:
        if not isinstance(item, dict):
            continue
        bundle_id = str(item.get("id") or "").strip()
        title = normalize_text(item.get("title") or "")
        bundle = str(item.get("bundle") or "")
        if not bundle_id or not title or not bundle.strip():
            continue
        bundles.append(
            {
                "id": bundle_id,
                "title": title[:120],
                "bundle": bundle[:WORKBENCH_CONTEXT_LIMIT],
                "context_title": normalize_text(item.get("context_title") or "")[:120],
                "provider": normalize_text(item.get("provider") or "")[:32],
                "model": normalize_text(item.get("model") or "")[:160],
                "temperature": _sanitize_float(item.get("temperature"), 0.4, 0.0, 2.0),
                "api_base": str(item.get("api_base") or "")[:240],
                "summary": normalize_text(item.get("summary") or "")[:220],
                "created_at": item.get("created_at") or _iso_now(),
                "updated_at": item.get("updated_at") or _iso_now(),
            }
        )
    bundles.sort(key=lambda entry: entry.get("updated_at") or "", reverse=True)
    return bundles[:48]


def _save_bundles_to_disk(bundles):
    _ensure_mission_data_dir()
    WORKBENCH_BUNDLES_FILE.write_text(json.dumps(bundles, indent=2), encoding="utf-8")


def list_workbench_bundles():
    with WORKBENCH_BUNDLES_LOCK:
        bundles = _load_bundles_from_disk()
    return {"bundles": bundles, "count": len(bundles)}


def save_workbench_bundle(payload):
    title = normalize_text(payload.get("title") or "")
    bundle = str(payload.get("bundle") or "")
    if not title:
        raise ValueError("Bundle title required")
    if not normalize_text(bundle):
        raise ValueError("Bundle content required")
    bundle_id = str(payload.get("id") or "").strip() or str(uuid.uuid4())
    now = _iso_now()
    new_entry = {
        "id": bundle_id,
        "title": title[:120],
        "bundle": bundle[:WORKBENCH_CONTEXT_LIMIT],
        "context_title": normalize_text(payload.get("context_title") or "")[:120],
        "provider": normalize_text(payload.get("provider") or "")[:32],
        "model": normalize_text(payload.get("model") or "")[:160],
        "temperature": _sanitize_float(payload.get("temperature"), 0.4, 0.0, 2.0),
        "api_base": str(payload.get("api_base") or "")[:240],
        "summary": normalize_text(payload.get("summary") or _trim_excerpt(bundle, 220))[:220],
        "updated_at": now,
        "created_at": now,
    }
    with WORKBENCH_BUNDLES_LOCK:
        bundles = _load_bundles_from_disk()
        preserved_created_at = now
        updated = False
        for index, entry in enumerate(bundles):
            if entry.get("id") == bundle_id:
                preserved_created_at = entry.get("created_at") or now
                bundles[index] = {**entry, **new_entry, "created_at": preserved_created_at}
                updated = True
                break
        if not updated:
            bundles.insert(0, new_entry)
        bundles.sort(key=lambda entry: entry.get("updated_at") or "", reverse=True)
        bundles = bundles[:48]
        _save_bundles_to_disk(bundles)
    return {"status": "saved", "bundle": {**new_entry, "created_at": preserved_created_at}}


def delete_workbench_bundle(bundle_id):
    key = str(bundle_id or "").strip()
    if not key:
        raise ValueError("Bundle id required")
    with WORKBENCH_BUNDLES_LOCK:
        bundles = _load_bundles_from_disk()
        next_bundles = [entry for entry in bundles if entry.get("id") != key]
        if len(next_bundles) == len(bundles):
            raise FileNotFoundError("Bundle not found")
        _save_bundles_to_disk(next_bundles)
    return {"status": "deleted", "id": key}


def _normalize_project_messages(messages):
    normalized = []
    for entry in (messages or [])[-80:]:
        role = str((entry or {}).get("role") or "assistant").strip().lower()
        if role not in {"system", "user", "assistant"}:
            role = "assistant"
        content = normalize_text(_extract_chat_text((entry or {}).get("content") or (entry or {}).get("text") or ""))
        if not content:
            continue
        normalized.append({"role": role, "content": content[:12000]})
    return normalized


def _load_projects_from_disk():
    _ensure_mission_data_dir()
    if not WORKBENCH_PROJECTS_FILE.exists():
        return []
    try:
        data = json.loads(WORKBENCH_PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    projects = []
    for item in data:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id") or "").strip()
        title = normalize_text(item.get("title") or "")
        if not project_id or not title:
            continue
        messages = _normalize_project_messages(item.get("messages") or [])
        projects.append(
            {
                "id": project_id,
                "title": title[:120],
                "summary": normalize_text(item.get("summary") or "")[:220],
                "context_title": normalize_text(item.get("context_title") or "")[:120],
                "context_bundle": str(item.get("context_bundle") or "")[:WORKBENCH_CONTEXT_LIMIT],
                "provider": normalize_text(item.get("provider") or "")[:32],
                "model": normalize_text(item.get("model") or "")[:160],
                "temperature": _sanitize_float(item.get("temperature"), 0.4, 0.0, 2.0),
                "api_base": str(item.get("api_base") or "")[:240],
                "messages": messages,
                "message_count": len(messages),
                "created_at": item.get("created_at") or _iso_now(),
                "updated_at": item.get("updated_at") or _iso_now(),
            }
        )
    projects.sort(key=lambda entry: entry.get("updated_at") or "", reverse=True)
    return projects[:36]


def _save_projects_to_disk(projects):
    _ensure_mission_data_dir()
    WORKBENCH_PROJECTS_FILE.write_text(json.dumps(projects, indent=2), encoding="utf-8")


def list_workbench_projects():
    with WORKBENCH_PROJECTS_LOCK:
        projects = _load_projects_from_disk()
    return {"projects": projects, "count": len(projects)}


def save_workbench_project(payload):
    title = normalize_text(payload.get("title") or "")
    if not title:
        raise ValueError("Project title required")
    project_id = str(payload.get("id") or "").strip() or str(uuid.uuid4())
    context_bundle = str(payload.get("context_bundle") or "")[:WORKBENCH_CONTEXT_LIMIT]
    messages = _normalize_project_messages(payload.get("messages") or [])
    if not context_bundle.strip() and not messages:
        raise ValueError("Project needs context or messages")
    now = _iso_now()
    summary_source = normalize_text(payload.get("summary") or "")
    if not summary_source:
        if messages:
            summary_source = messages[-1].get("content") or ""
        elif context_bundle:
            summary_source = context_bundle
    new_entry = {
        "id": project_id,
        "title": title[:120],
        "summary": _trim_excerpt(summary_source, 220),
        "context_title": normalize_text(payload.get("context_title") or "")[:120],
        "context_bundle": context_bundle,
        "provider": normalize_text(payload.get("provider") or "")[:32],
        "model": normalize_text(payload.get("model") or "")[:160],
        "temperature": _sanitize_float(payload.get("temperature"), 0.4, 0.0, 2.0),
        "api_base": str(payload.get("api_base") or "")[:240],
        "messages": messages,
        "message_count": len(messages),
        "updated_at": now,
        "created_at": now,
    }
    with WORKBENCH_PROJECTS_LOCK:
        projects = _load_projects_from_disk()
        preserved_created_at = now
        updated = False
        for index, entry in enumerate(projects):
            if entry.get("id") == project_id:
                preserved_created_at = entry.get("created_at") or now
                projects[index] = {**entry, **new_entry, "created_at": preserved_created_at}
                updated = True
                break
        if not updated:
            projects.insert(0, new_entry)
        projects.sort(key=lambda entry: entry.get("updated_at") or "", reverse=True)
        projects = projects[:36]
        _save_projects_to_disk(projects)
    return {"status": "saved", "project": {**new_entry, "created_at": preserved_created_at}}


def delete_workbench_project(project_id):
    key = str(project_id or "").strip()
    if not key:
        raise ValueError("Project id required")
    with WORKBENCH_PROJECTS_LOCK:
        projects = _load_projects_from_disk()
        next_projects = [entry for entry in projects if entry.get("id") != key]
        if len(next_projects) == len(projects):
            raise FileNotFoundError("Project not found")
        _save_projects_to_disk(next_projects)
    return {"status": "deleted", "id": key}


class BMCProxyHandler(http.server.BaseHTTPRequestHandler):
    """Transparent reverse proxy for the BMC/IPMI web interface."""

    STRIP_HEADERS = {"x-frame-options", "content-security-policy", "x-content-type-options",
                     "transfer-encoding", "content-encoding", "content-length"}
    BMC_ORIGIN = f"https://{BMC_HOST}"

    def _rewrite_location(self, value):
        """Rewrite redirect URLs pointing at BMC to go through proxy."""
        if value.startswith(self.BMC_ORIGIN):
            return "http://" + self.headers.get("X-Forwarded-Host", self.headers.get("Host", f"localhost:{BMC_PROXY_PORT}")) + value[len(self.BMC_ORIGIN):]
        return value

    def _rewrite_cookie(self, value):
        """Strip Domain= and Secure from Set-Cookie so cookies work over HTTP proxy."""
        parts = [p.strip() for p in value.split(";")]
        parts = [p for p in parts if not p.lower().startswith("domain=")
                 and p.lower() != "secure"
                 and not p.lower().startswith("samesite=")]
        return "; ".join(parts)

    def _proxy(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            conn = http.client.HTTPSConnection(BMC_HOST, 443, context=ctx, timeout=30)
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else None
            fwd_headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ("host", "connection", "keep-alive")}
            fwd_headers["Host"] = BMC_HOST
            # Strip stale QSESSIONID cookie from login POST — old session causes 401
            if self.command == "POST" and self.path == "/api/session":
                cookie = fwd_headers.get("Cookie", "")
                cleaned = "; ".join(p for p in cookie.split(";") if not p.strip().startswith("QSESSIONID"))
                if cleaned:
                    fwd_headers["Cookie"] = cleaned
                else:
                    fwd_headers.pop("Cookie", None)
            conn.request(self.command, self.path, body=body, headers=fwd_headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                kl = k.lower()
                if kl in self.STRIP_HEADERS:
                    continue
                if kl == "location":
                    v = self._rewrite_location(v)
                elif kl == "set-cookie":
                    v = self._rewrite_cookie(v)
                self.send_header(k, v)
            import gzip as _gz
            data = resp.read()
            resp_headers = dict((k.lower(), v) for k, v in resp.getheaders())
            enc = resp_headers.get("content-encoding", "")
            ct = resp_headers.get("content-type", "")
            # Decompress gzip (we strip content-encoding header so must decode body)
            if enc == "gzip":
                try:
                    data = _gz.decompress(data)
                except Exception:
                    pass

            # Force HTTP mode: BMC JS uses plain cookies instead of __Host- prefixed ones
            if "application/json" in ct:
                try:
                    text = data.decode("utf-8", errors="replace")
                    text = text.replace('"HTTPSEnabled": 1', '"HTTPSEnabled": 0')
                    text = text.replace('"HTTPSEnabled":1', '"HTTPSEnabled":0')
                    data = text.encode()
                except Exception:
                    pass
            # Rewrite BMC IP references in JSON so JS redirects go to proxy
            if "application/json" in ct:
                try:
                    proxy_host = self.headers.get("Host", f"192.168.0.54:{BMC_PROXY_PORT}")
                    text = data.decode("utf-8", errors="replace")
                    text = text.replace(f'"{BMC_HOST}"', f'"{proxy_host}"')
                    data = text.encode()
                except Exception:
                    pass
            # Patch JS: strip secure+SameSite so cookies work over HTTP proxy
            if "javascript" in ct or self.path.endswith(".js"):
                try:
                    text = data.decode("utf-8", errors="replace")
                    text = text.replace(";secure; SameSite=Lax", "").replace(";Samesite=Lax", "").replace("; SameSite=Lax", "")
                    data = text.encode()
                except Exception:
                    pass
            self.end_headers()
            self.wfile.write(data)
            conn.close()
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode())

    do_GET = _proxy
    do_POST = _proxy
    do_PUT = _proxy
    do_DELETE = _proxy
    do_HEAD = _proxy
    do_OPTIONS = _proxy

    def log_message(self, format, *args):
        pass

PORT = 8080
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = "/opt/llm-stack/config/secrets.json"
COMPOSE_DIR = "/opt/llm-stack/compose"
CADDY_AUTH_FILE = "/opt/llm-stack/config/caddy/mission-auth.caddy"
APP_ROUTES = {
    "/",
    "/overview",
    "/models",
    "/agents",
    "/stack",
    "/knowledge",
    "/workbench",
    "/gallery",
    "/studio",
    "/av",
    "/terminal",
    "/bmc",
    "/settings",
}

SERVICES = {
    "vLLM": {"url": "http://localhost:8000/health", "port": 8000, "link": "http://{host}:8000/docs"},
    "Open WebUI": {"url": "http://localhost:3000", "port": 3000, "link": "http://{host}:3000"},
    "SearXNG": {"url": "http://localhost:8888", "port": 8888, "link": "http://{host}:8888"},
    "Grafana": {"url": "http://localhost:3001/api/health", "port": 3001, "link": "http://{host}:3001"},
    "Qdrant": {"url": "http://localhost:6333/healthz", "port": 6333, "link": None},
    "Ollama": {"url": "http://localhost:11434/api/tags", "port": 11434, "link": None},
    "Prometheus": {"url": "http://localhost:9090/-/healthy", "port": 9090, "link": "http://{host}:9090"},
    "Qwen Agent": {"url": "http://localhost:7864", "port": 7864, "link": "http://{host}:7864"},
    "Browser Use": {"url": "http://localhost:7865/health", "port": 7865, "link": None},
    "Speaches STT": {"url": "http://localhost:8001/health", "port": 8001, "link": None},
    "Kokoro TTS": {"url": "http://localhost:8880/health", "port": 8880, "link": None},
    "Discord Bot": {"url": None, "port": None, "link": None, "container": "discord-bot"},
    "Proxima Bot": {"url": None, "port": None, "link": None, "container": "proxima-bot"},
}

# Model definitions for the management panel
MODELS = {
    "vllm-coder": {
        "name": "Qwen3-Coder",
        "gpu": 3,
        "type": "docker",
        "container": "vllm-coder",
        "managed": False,
    },
    "glm": {
        "name": "GLM-4.7-Flash",
        "gpu": 2,
        "type": "systemd",
        "service": "glm-llamacpp.service",
        "managed": True,
    },
    "qwen35": {
        "name": "Qwen3.5-35B-A3B",
        "gpu": 0,
        "type": "systemd",
        "service": "qwen35-llamacpp.service",
        "managed": True,
    },
    "gptoss": {
        "name": "GPT-OSS-20B",
        "gpu": 1,
        "type": "swap",
        "variants": {
            "base": {"service": "gptoss20b-llamacpp.service", "desc": "Q4_K_XL (12GB)"},
            "heretic": {"service": "gptoss20b-heretic-llamacpp.service", "desc": "HERETIC Q8_0 (21GB)"},
        },
        "managed": True,
    },
}

# Agent definitions
AGENTS = {
    "franklin": {
        "name": "Franklin",
        "container": "discord-bot",
        "token_env": "DISCORD_TOKEN",
        "model_env": "MODEL_NAME",
        "default_model": "Qwen3-Coder",
        "base_url_env": "VLLM_BASE_URL",
    },
    "proxima": {
        "name": "Proxima",
        "container": "proxima-bot",
        "token_env": "PROXIMA_TOKEN",
        "model_env": "MODEL_NAME",
        "default_model": "GPT-OSS-HERETIC",
        "base_url_env": "VLLM_BASE_URL",
    },
    "ghost-stream-browser": {
        "name": "Ghost Stream Browser",
        "container": "discord-browser-worker",
        "token_env": "",
        "model_env": "",
        "default_model": "Chromium + Playwright",
        "base_url_env": "",
        "description": "Persistent Discord + Rumble browser worker that cold-starts the Ghost livestream, joins The Oval Office, shares the stream tab with audio, and returns focus to the stream.",
        "console_url": "http://192.168.0.54:6086/vnc.html",
    },
}

# Command palette for the frontend
COMMAND_PALETTE = [
    {"label": "Model Status", "cmd": "model-swap status", "category": "Models"},
    {"label": "GPT-OSS \u2192 Base", "cmd": "model-swap gptoss base", "category": "Models"},
    {"label": "GPT-OSS \u2192 HERETIC", "cmd": "model-swap gptoss heretic", "category": "Models"},
    {"label": "GPT-OSS \u2192 Off", "cmd": "model-swap gptoss off", "category": "Models"},
    {"label": "GLM \u2192 Off", "cmd": "model-swap glm off", "category": "Models"},
    {"label": "Qwen3.5 \u2192 Off", "cmd": "model-swap qwen35 off", "category": "Models"},
    {"label": "Stack Status", "cmd": "stack ps", "category": "Stack"},
    {"label": "Stack Up", "cmd": "stack up", "category": "Stack"},
    {"label": "Stack Down", "cmd": "stack down", "category": "Stack"},
    {"label": "Stack Restart", "cmd": "stack restart", "category": "Stack"},
    {"label": "Stack Logs (tail)", "cmd": "stack logs | tail -50", "category": "Stack"},
    {"label": "Docker PS", "cmd": "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'", "category": "System"},
    {"label": "GPU Info", "cmd": "rocm-smi", "category": "System"},
    {"label": "Disk Usage", "cmd": "df -h / /mnt", "category": "System"},
    {"label": "Memory Usage", "cmd": "free -h", "category": "System"},
    {"label": "Top Processes", "cmd": "ps aux --sort=-%mem | head -15", "category": "System"},
    {"label": "Network Interfaces", "cmd": "ip -br addr", "category": "System"},
    {"label": "System Journal (errors)", "cmd": "journalctl -p err --since '1 hour ago' --no-pager | tail -30", "category": "System"},
    {"label": "Restart Franklin", "cmd": "docker restart discord-bot", "category": "Agents"},
    {"label": "Restart Proxima", "cmd": "docker restart proxima-bot", "category": "Agents"},
    {"label": "Restart Ghost Stream Browser", "cmd": "docker restart discord-browser-worker", "category": "Agents"},
    {"label": "Franklin Logs", "cmd": "docker logs discord-bot --tail 30", "category": "Agents"},
    {"label": "Proxima Logs", "cmd": "docker logs proxima-bot --tail 30", "category": "Agents"},
    {"label": "Ghost Stream Logs", "cmd": "docker logs discord-browser-worker --tail 60", "category": "Agents"},
    {"label": "Restart vLLM Studio", "cmd": "sudo systemctl restart vllm-studio vllm-studio-frontend", "category": "Services"},
    {"label": "Restart LiteLLM", "cmd": "docker restart vllm-studio-litellm", "category": "Services"},
    {"label": "Restart Dashboard", "cmd": "sudo systemctl restart llm-dashboard", "category": "Services"},
    {"label": "GLM Logs", "cmd": "journalctl -u glm-llamacpp --no-pager -n 30", "category": "Services"},
    {"label": "Qwen3.5 Logs", "cmd": "journalctl -u qwen35-llamacpp --no-pager -n 30", "category": "Services"},
    {"label": "GPT-OSS Logs", "cmd": "journalctl -u gptoss20b-llamacpp -u gptoss20b-heretic-llamacpp --no-pager -n 30", "category": "Services"},
    {"label": "vLLM Coder Logs", "cmd": "docker logs vllm-coder --tail 30", "category": "Services"},
]

# --- Terminal command safety ---

# Commands that are never allowed
TERMINAL_BLOCKED_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$",  # rm -rf /
    r"rm\s+-rf\s+/",
    r"mkfs\.",
    r"dd\s+.*of=/dev/",
    r":(){ :|:& };:",  # fork bomb
    r">\s*/dev/sd",
    r"chmod\s+-R\s+777\s+/\s*$",
    r"wget.*\|\s*(ba)?sh",
    r"curl.*\|\s*(ba)?sh",
]

TERMINAL_TIMEOUT = 120  # seconds


def is_command_safe(cmd):
    """Check if a command is reasonably safe to execute."""
    cmd_stripped = cmd.strip()
    if not cmd_stripped:
        return False, "Empty command"
    for pattern in TERMINAL_BLOCKED_PATTERNS:
        if re.search(pattern, cmd_stripped):
            return False, f"Blocked: matches dangerous pattern"
    return True, ""


# --- Secrets Management ---

def load_secrets():
    """Load secrets from file."""
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_secrets(secrets):
    """Save secrets to file with restricted permissions."""
    with open(SECRETS_FILE, "w") as f:
        json.dump(secrets, f, indent=2)
    os.chmod(SECRETS_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600


def get_secrets_masked():
    """Return secrets with values masked."""
    secrets = load_secrets()
    masked = {}
    for key, value in secrets.items():
        if value:
            masked[key] = value[:8] + "..." + value[-4:] if len(value) > 16 else "****"
        else:
            masked[key] = ""
    return masked


def get_mission_auth_status():
    """Return the configured Mission dashboard username without exposing the hash."""
    if not os.path.exists(CADDY_AUTH_FILE):
        return {"configured": False, "username": ""}

    try:
        with open(CADDY_AUTH_FILE, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("basicauth") or line.startswith("basic_auth") or line == "{":
                    continue
                if line == "}":
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    return {"configured": True, "username": parts[0]}
    except Exception:
        pass

    return {"configured": False, "username": ""}


def update_mission_auth(username, password):
    """Persist Mission dashboard basic auth and reload Caddy."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        return {"status": "error", "output": "Username must be 3-64 chars: letters, numbers, dot, underscore, dash."}
    if len(password) < 16:
        return {"status": "error", "output": "Password must be at least 16 characters."}

    try:
        hashed = subprocess.check_output(
            ["docker", "exec", "caddy", "caddy", "hash-password", "--plaintext", password],
            timeout=10, stderr=subprocess.STDOUT
        ).decode().strip()
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output.decode() if e.output else str(e)}
    except Exception as e:
        return {"status": "error", "output": str(e)}

    previous = None
    if os.path.exists(CADDY_AUTH_FILE):
        with open(CADDY_AUTH_FILE, "r") as f:
            previous = f.read()

    content = "basic_auth {\n\t%s %s\n}\n" % (username, hashed)
    tmp_file = CADDY_AUTH_FILE + ".tmp"

    try:
        with open(tmp_file, "w") as f:
            f.write(content)
        os.chmod(tmp_file, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_file, CADDY_AUTH_FILE)

        subprocess.check_output(
            ["docker", "exec", "caddy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile"],
            timeout=10, stderr=subprocess.STDOUT
        )
        subprocess.check_output(
            ["docker", "exec", "caddy", "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
            timeout=15, stderr=subprocess.STDOUT
        )
        return {"status": "ok", "output": "Mission dashboard access updated."}
    except subprocess.CalledProcessError as e:
        if previous is None:
            try:
                os.remove(CADDY_AUTH_FILE)
            except FileNotFoundError:
                pass
        else:
            with open(CADDY_AUTH_FILE, "w") as f:
                f.write(previous)
            os.chmod(CADDY_AUTH_FILE, stat.S_IRUSR | stat.S_IWUSR)
        return {"status": "error", "output": e.output.decode() if e.output else str(e)}
    except Exception as e:
        if previous is None:
            try:
                os.remove(CADDY_AUTH_FILE)
            except FileNotFoundError:
                pass
        else:
            with open(CADDY_AUTH_FILE, "w") as f:
                f.write(previous)
            os.chmod(CADDY_AUTH_FILE, stat.S_IRUSR | stat.S_IWUSR)
        return {"status": "error", "output": str(e)}


def restart_service(service_name):
    """Restart a docker compose service."""
    try:
        subprocess.Popen(
            ["docker", "compose", "-f",
             os.path.join(COMPOSE_DIR, "docker-compose.services.yml"),
             "up", "-d", "--force-recreate", service_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except Exception:
        return False


# --- Agent Management ---

def _read_tail_lines(path, max_lines=12):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return [line.rstrip() for line in handle.readlines()[-max_lines:] if line.strip()]
    except Exception:
        return []


def _ghost_stream_schedule():
    try:
        output = subprocess.check_output(["crontab", "-u", "mejohnc", "-l"], timeout=3, stderr=subprocess.DEVNULL).decode()
    except Exception:
        return []
    schedule = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("CRON_TZ="):
            continue
        if "worker-up.sh" in line:
            schedule.append({"cron": "@reboot", "description": "Start browser worker on host reboot"})
        elif "stream-start.sh" in line:
            schedule.append({"cron": "15 20 * * 2,5", "description": "Start Ghost stream Tuesdays and Fridays at 8:15 PM America/Chicago"})
        elif "stream-stop.sh" in line:
            schedule.append({"cron": "0 8 * * 3,6", "description": "Stop Ghost stream Wednesdays and Saturdays at 8:00 AM America/Chicago"})
    return schedule


def _ghost_stream_runtime():
    runtime = {
        "status": "stopped",
        "active_state": "Worker offline",
        "started_at": "",
        "restart_count": 0,
        "stream_active": False,
        "browser_ready": False,
        "call_connected": False,
        "stream_target_ready": False,
        "discord_url": "",
        "rumble_url": "",
        "live_tabs": [],
        "recent_logs": [],
    }
    try:
        raw = subprocess.check_output(
            ["docker", "inspect", "discord-browser-worker", "--format", "{{json .State}}"],
            timeout=4,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        state = json.loads(raw) if raw else {}
    except Exception:
        return runtime

    status = str(state.get("Status") or "stopped").strip() or "stopped"
    runtime["status"] = "running" if status == "running" else status
    runtime["started_at"] = str(state.get("StartedAt") or "").strip()
    runtime["restart_count"] = int(state.get("RestartCount") or 0)
    if status != "running":
        runtime["active_state"] = "Worker not running"
        return runtime

    runtime["browser_ready"] = True
    try:
        output = subprocess.check_output(
            [
                "docker",
                "exec",
                "discord-browser-worker",
                "sh",
                "-lc",
                "curl -s http://127.0.0.1:9222/json/list",
            ],
            timeout=4,
            stderr=subprocess.DEVNULL,
        ).decode()
        entries = json.loads(output or "[]")
    except Exception:
        entries = []

    live_tabs = []
    for item in entries:
        if item.get("type") != "page":
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or url or "").strip()
        if not url or url.startswith("chrome-extension://"):
            continue
        live_tabs.append({"title": title, "url": url})
    runtime["live_tabs"] = live_tabs[:6]

    discord_tab = next((item for item in live_tabs if "discord.com/channels/" in item["url"] and "The Oval Office" in item["title"]), None)
    if not discord_tab:
        discord_tab = next((item for item in live_tabs if "discord.com/channels/" in item["url"] and "/channels/@me" not in item["url"]), None)
    rumble_tab = next((item for item in live_tabs if "rumble.com/v" in item["url"]), None)
    if not rumble_tab:
        rumble_tab = next((item for item in live_tabs if "rumble.com/c/ghostpolitics/livestreams" in item["url"]), None)

    runtime["discord_url"] = discord_tab["url"] if discord_tab else ""
    runtime["rumble_url"] = rumble_tab["url"] if rumble_tab else ""
    runtime["call_connected"] = bool(discord_tab)
    runtime["stream_target_ready"] = bool(rumble_tab and "rumble.com/v" in rumble_tab["url"])
    runtime["stream_active"] = runtime["call_connected"] and runtime["stream_target_ready"]
    if runtime["stream_active"]:
        runtime["active_state"] = "Streaming Ghost to The Oval Office"
    elif runtime["stream_target_ready"]:
        runtime["active_state"] = "Rumble stream ready"
    elif runtime["call_connected"]:
        runtime["active_state"] = "Discord call connected"
    else:
        runtime["active_state"] = "Worker idle"

    try:
        docker_logs = subprocess.check_output(
            ["docker", "logs", "--tail", "12", "discord-browser-worker"],
            timeout=4,
            stderr=subprocess.STDOUT,
        ).decode()
        docker_lines = [line for line in docker_logs.splitlines() if line.strip()][-8:]
    except Exception:
        docker_lines = []
    runtime["recent_logs"] = (
        _read_tail_lines("/home/mejohnc/discord-browser-worker/data/logs/cron.log", 8)
        or docker_lines
        or _read_tail_lines("/home/mejohnc/discord-browser-worker/data/logs/browser-interactions.jsonl", 6)
        or _read_tail_lines("/home/mejohnc/discord-browser-worker/data/logs/x11-interactions.jsonl", 6)
        or []
    )
    return runtime

def get_agent_status():
    """Get status of all agents."""
    result = {}
    for agent_id, info in AGENTS.items():
        # Check if token is set
        has_token = False
        env_file = os.path.join(COMPOSE_DIR, ".env")
        if info.get("token_env") and os.path.exists(env_file):
            with open(env_file, "r") as ef:
                for line in ef:
                    if line.startswith(info["token_env"] + "="):
                        val = line.strip().split("=", 1)[1] if "=" in line else ""
                        has_token = bool(val.strip())
                        break

        entry = {
            "name": info["name"],
            "container": info["container"],
            "model": info["default_model"],
            "description": info.get("description", ""),
            "status": "stopped",
            "has_token": has_token,
        }
        # Check if container is running
        try:
            out = subprocess.check_output(
                ["docker", "inspect", "-f", "{{.State.Running}}", info["container"]],
                timeout=3, stderr=subprocess.DEVNULL
            ).decode().strip()
            entry["status"] = "running" if out == "true" else "stopped"
        except Exception:
            entry["status"] = "stopped"
        if agent_id == "ghost-stream-browser":
            runtime = _ghost_stream_runtime()
            entry.update(runtime)
            entry["console_url"] = info.get("console_url", "")
            entry["schedule"] = _ghost_stream_schedule()
            entry["schedule_timezone"] = "America/Chicago"
        result[agent_id] = entry
    return result


def handle_agent_update(agent_id, key, value):
    """Update an agent's configuration."""
    if agent_id not in AGENTS:
        return {"status": "error", "output": f"Unknown agent: {agent_id}"}

    agent = AGENTS[agent_id]

    if key == "token":
        # Save token to secrets.json and update .env, then restart container
        token_env = agent["token_env"]
        secrets = load_secrets()
        secrets[token_env] = value
        save_secrets(secrets)

        # Update .env file
        env_file = os.path.join(COMPOSE_DIR, ".env")
        env_lines = []
        if os.path.exists(env_file):
            with open(env_file, "r") as f:
                env_lines = [l for l in f.readlines() if not l.startswith(f"{token_env}=")]
        if value:
            env_lines.append(f"{token_env}={value}\n")
        with open(env_file, "w") as f:
            f.writelines(env_lines)
        os.chmod(env_file, stat.S_IRUSR | stat.S_IWUSR)

        return {"status": "ok", "output": f"Token updated for {agent['container']}"}

    elif key == "model":
        # Informational for now - model is configured at container level
        return {"status": "ok", "output": f"Model info noted: {value} (configured at container level)"}

    else:
        return {"status": "error", "output": f"Unknown config key: {key}"}


# --- Model Management ---

def get_model_status():
    """Get status of all models using model-swap status and systemctl."""
    result = {}

    # Get model-swap status output
    swap_output = ""
    try:
        swap_output = subprocess.check_output(
            ["/usr/local/bin/model-swap", "status"],
            timeout=10, stderr=subprocess.STDOUT
        ).decode()
    except Exception as e:
        swap_output = str(e)

    for slot, info in MODELS.items():
        entry = {
            "name": info["name"],
            "gpu": info["gpu"],
            "managed": info["managed"],
            "type": info["type"],
            "status": "unknown",
        }

        if info["type"] == "docker":
            # Check docker container
            try:
                out = subprocess.check_output(
                    ["docker", "inspect", "-f", "{{.State.Running}}", info["container"]],
                    timeout=3, stderr=subprocess.DEVNULL
                ).decode().strip()
                entry["status"] = "running" if out == "true" else "stopped"
            except Exception:
                entry["status"] = "stopped"

        elif info["type"] == "systemd":
            # Check systemd service
            try:
                subprocess.check_call(
                    ["systemctl", "is-active", "--quiet", info["service"]],
                    timeout=3
                )
                entry["status"] = "running"
            except Exception:
                entry["status"] = "stopped"

        elif info["type"] == "swap":
            # Check which variant is running
            entry["variants"] = {}
            entry["active_variant"] = None
            for var_name, var_info in info["variants"].items():
                try:
                    subprocess.check_call(
                        ["systemctl", "is-active", "--quiet", var_info["service"]],
                        timeout=3
                    )
                    entry["variants"][var_name] = "running"
                    entry["active_variant"] = var_name
                    entry["status"] = "running"
                except Exception:
                    entry["variants"][var_name] = "stopped"

            if entry["active_variant"] is None:
                entry["status"] = "stopped"

        result[slot] = entry

    return result


def handle_model_swap(slot, model):
    """Swap a model using model-swap script."""
    try:
        out = subprocess.check_output(
            ["/usr/local/bin/model-swap", slot, model],
            timeout=30, stderr=subprocess.STDOUT
        ).decode()
        return {"status": "ok", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output.decode() if e.output else str(e)}
    except Exception as e:
        return {"status": "error", "output": str(e)}


def handle_model_stop(slot):
    """Stop a model's service."""
    info = MODELS.get(slot)
    if not info or not info["managed"]:
        return {"status": "error", "output": f"Cannot manage slot: {slot}"}

    if info["type"] == "swap":
        return handle_model_swap(slot, "off")
    elif info["type"] == "systemd":
        try:
            out = subprocess.check_output(
                ["sudo", "systemctl", "stop", info["service"]],
                timeout=15, stderr=subprocess.STDOUT
            ).decode()
            return {"status": "ok", "output": out or f"Stopped {info['service']}"}
        except subprocess.CalledProcessError as e:
            return {"status": "error", "output": e.output.decode() if e.output else str(e)}
    return {"status": "error", "output": "Unknown model type"}


def handle_model_start(slot):
    """Start a model's default service."""
    info = MODELS.get(slot)
    if not info or not info["managed"]:
        return {"status": "error", "output": f"Cannot manage slot: {slot}"}

    if info["type"] == "swap":
        return handle_model_swap(slot, "base")
    elif info["type"] == "systemd":
        try:
            out = subprocess.check_output(
                ["sudo", "systemctl", "start", info["service"]],
                timeout=15, stderr=subprocess.STDOUT
            ).decode()
            return {"status": "ok", "output": out or f"Started {info['service']}"}
        except subprocess.CalledProcessError as e:
            return {"status": "error", "output": e.output.decode() if e.output else str(e)}
    return {"status": "error", "output": "Unknown model type"}


# --- Terminal ---

def execute_terminal_command(cmd):
    """Execute a terminal command with safety checks."""
    safe, reason = is_command_safe(cmd)
    if not safe:
        return {"returncode": 1, "stdout": "", "stderr": reason}

    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=TERMINAL_TIMEOUT, cwd="/opt/llm-stack"
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8192:] if len(proc.stdout) > 8192 else proc.stdout,
            "stderr": proc.stderr[-4096:] if len(proc.stderr) > 4096 else proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": f"Command timed out after {TERMINAL_TIMEOUT}s"}
    except Exception as e:
        return {"returncode": 1, "stdout": "", "stderr": str(e)}


# --- GPU / System Stats ---

def get_gpu_stats():
    gpus = []
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showtemp", "--showuse", "--showpower",
             "--showmeminfo", "vram", "--showproductname", "--json"],
            timeout=5, stderr=subprocess.DEVNULL
        ).decode()
        data = json.loads(out)
        for key in sorted(data.keys()):
            if not key.startswith("card"):
                continue
            card = data[key]
            vram_total = int(card.get("VRAM Total Memory (B)", 0)) // (1024 * 1024)
            vram_used = int(card.get("VRAM Total Used Memory (B)", 0)) // (1024 * 1024)
            temp = 0
            for k, v in card.items():
                if "edge" in k.lower() and "temperature" in k.lower():
                    try: temp = int(float(v))
                    except: pass
                    break
            power, power_cap = 0, 300
            for k, v in card.items():
                if "average" in k.lower() and "power" in k.lower():
                    try: power = int(float(v))
                    except: pass
                elif "cap" in k.lower() and "power" in k.lower() and "default" not in k.lower():
                    try: power_cap = int(float(v))
                    except: pass
            util = 0
            for k, v in card.items():
                if "gpu use" in k.lower() or "gpu activity" in k.lower():
                    try: util = int(float(str(v).replace("%", "")))
                    except: pass
            name = card.get("Card Series", card.get("Card series", "R9700"))
            gpus.append({"name": name, "temp": temp, "vram_used": vram_used,
                         "vram_total": vram_total, "util": util, "power": power, "power_cap": power_cap})
    except Exception:
        pass
    return gpus


def get_server_stats():
    cpu = ram = uptime = disk = "--"
    try:
        load = os.getloadavg()
        ncpu = os.cpu_count() or 1
        cpu = f"{load[0]:.1f} / {load[1]:.1f} / {load[2]:.1f} ({ncpu} cores)"
    except Exception: pass
    try:
        out = subprocess.check_output(["free", "-h"], timeout=3).decode()
        for line in out.split("\n"):
            if line.startswith("Mem:"):
                parts = line.split()
                ram = f"{parts[2]} / {parts[1]}"
                break
    except Exception: pass
    try:
        out = subprocess.check_output(["uptime", "-p"], timeout=3).decode().strip()
        uptime = out.replace("up ", "")
    except Exception: pass
    try:
        out = subprocess.check_output(["df", "-h", "/"], timeout=3).decode()
        lines = out.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            disk = f"{parts[2]} / {parts[1]} ({parts[4]} used)"
    except Exception: pass
    return {"cpu": cpu, "ram": ram, "uptime": uptime, "disk": disk}


def get_vllm_metrics():
    metrics = {"requests_running": 0, "requests_waiting": 0,
               "cache_usage": 0, "prompt_tps": 0, "gen_tps": 0}
    try:
        req = urllib.request.Request("http://localhost:8000/metrics")
        with urllib.request.urlopen(req, timeout=3) as r:
            text = r.read().decode()
        for line in text.split("\n"):
            if line.startswith("vllm:num_requests_running "):
                metrics["requests_running"] = int(float(line.split()[-1]))
            elif line.startswith("vllm:num_requests_waiting "):
                metrics["requests_waiting"] = int(float(line.split()[-1]))
            elif line.startswith("vllm:gpu_cache_usage_perc "):
                metrics["cache_usage"] = round(float(line.split()[-1]) * 100, 1)
            elif line.startswith("vllm:prompt_tokens_total "):
                metrics["prompt_tokens"] = int(float(line.split()[-1]))
            elif line.startswith("vllm:generation_tokens_total "):
                metrics["gen_tokens"] = int(float(line.split()[-1]))
    except Exception:
        pass
    return metrics


def get_service_health():
    results = {}
    for name, info in SERVICES.items():
        if info.get("container"):
            # Check by container status
            try:
                out = subprocess.check_output(
                    ["docker", "inspect", "-f", "{{.State.Running}}", info["container"]],
                    timeout=3, stderr=subprocess.DEVNULL
                ).decode().strip()
                results[name] = {"status": "up" if out == "true" else "down",
                                 "port": info["port"], "link": info["link"]}
            except Exception:
                results[name] = {"status": "down", "port": info["port"], "link": info["link"]}
        elif info["url"]:
            try:
                req = urllib.request.Request(info["url"])
                with urllib.request.urlopen(req, timeout=2) as r:
                    results[name] = {"status": "up", "port": info["port"], "link": info["link"]}
            except Exception:
                results[name] = {"status": "down", "port": info["port"], "link": info["link"]}
        else:
            results[name] = {"status": "unknown", "port": info["port"], "link": info["link"]}
    return results


def comfyui_request(path, timeout=20, method="GET", data=None, headers=None):
    url = f"{COMFYUI_BASE_URL}{path}"
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers.get("Content-Type", "application/octet-stream"), resp.read()


def comfyui_json(path, timeout=20, method="GET", payload=None, headers=None):
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode()
        request_headers.setdefault("Content-Type", "application/json")
    status, _, data = comfyui_request(path, timeout=timeout, method=method, data=body, headers=request_headers)
    return status, json.loads(data.decode())


def service_request_bytes(url, timeout=30, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers, resp.read()


def service_json(base_url, path, timeout=20, method="GET", payload=None, headers=None):
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode()
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(f"{base_url}{path}", data=body, method=method)
    for key, value in request_headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def service_json_url(url, timeout=20, method="GET", payload=None, headers=None):
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode()
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method)
    for key, value in request_headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _random_seed():
    return random.randrange(0, 2**63 - 1)


def _sanitize_int(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, parsed))


def _sanitize_float(value, default, minimum, maximum):
    try:
        parsed = float(value)
    except Exception:
        return default
    return max(minimum, min(maximum, parsed))


def _decode_data_url(data_url):
    raw = str(data_url or "").strip()
    if not raw or "," not in raw:
        raise ValueError("Invalid image payload")
    meta, encoded = raw.split(",", 1)
    if ";base64" not in meta:
        raise ValueError("Only base64 image payloads are supported")
    mime = meta.split(":", 1)[-1].split(";", 1)[0].strip().lower()
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError("Unsupported image type")
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
    return base64.b64decode(encoded), ext


def _encode_data_url(image_bytes, mime):
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def _encode_binary_data_url(blob_bytes, mime):
    return f"data:{mime};base64,{base64.b64encode(blob_bytes).decode()}"


def _fetch_remote_image(url):
    raw = str(url or "").strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https image URLs are supported")

    req = urllib.request.Request(
        raw,
        headers={
            "User-Agent": "MissionControl/1.0",
            "Accept": "image/png,image/jpeg,image/webp,image/*;q=0.8,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        content_type = (resp.headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
        if content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Remote URL did not return a supported image")
        data = resp.read(MAX_REMOTE_IMAGE_BYTES + 1)
        if len(data) > MAX_REMOTE_IMAGE_BYTES:
            raise ValueError("Remote image is too large")

    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[content_type]
    filename = os.path.basename(parsed.path or "").strip() or f"remote-image{ext}"
    if not filename.lower().endswith(ext):
        filename = f"{filename}{ext}"
    return {"filename": filename, "mime": content_type, "data_url": _encode_data_url(data, content_type)}


def _decode_media_data_url(data_url):
    raw = str(data_url or "").strip()
    if not raw.startswith("data:") or "," not in raw:
        raise ValueError("Invalid media data URL")
    meta, encoded = raw.split(",", 1)
    if ";base64" not in meta:
        raise ValueError("Only base64 media data URLs are supported")
    mime = meta.split(":", 1)[-1].split(";", 1)[0].strip().lower()
    allowed = {
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
    }
    if mime not in allowed:
        raise ValueError("Unsupported media type")
    return base64.b64decode(encoded), mime, allowed[mime]


def _multipart_form_data(fields, files):
    boundary = f"----MissionBoundary{uuid.uuid4().hex}"
    chunks = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )

    for field_name, filename, content_type, content in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


def comfyui_upload_image(filename, image_bytes, image_type="input", overwrite=True):
    boundary, body = _multipart_form_data(
        {"type": image_type, "overwrite": "true" if overwrite else "false"},
        [("image", filename, "application/octet-stream", image_bytes)],
    )
    _, _, data = comfyui_request(
        "/upload/image",
        timeout=120,
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return json.loads(data.decode())


def comfyui_upload_input_file(filename, file_bytes, subfolder="", image_type="input", overwrite=True, content_type="application/octet-stream"):
    fields = {"type": image_type, "overwrite": "true" if overwrite else "false"}
    if subfolder:
        fields["subfolder"] = str(subfolder).strip().replace("\\", "/").lstrip("/")
    boundary, body = _multipart_form_data(
        fields,
        [("image", filename, content_type, file_bytes)],
    )
    _, _, data = comfyui_request(
        "/upload/image",
        timeout=120,
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return json.loads(data.decode())


def comfyui_fetch_asset_bytes(filename, subfolder="", asset_type="output", timeout=60):
    query = urllib.parse.urlencode(
        {"filename": str(filename or "").strip(), "subfolder": str(subfolder or "").strip(), "type": str(asset_type or "output").strip() or "output"}
    )
    _, content_type, data = comfyui_request(f"/view?{query}", timeout=timeout)
    return content_type, data


def _safe_iso_from_ms(timestamp_ms):
    if not timestamp_ms:
        return None
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _extract_prompt_graph(entry):
    prompt = entry.get("prompt")
    if isinstance(prompt, list) and len(prompt) >= 3 and isinstance(prompt[2], dict):
        return prompt[2]
    return {}


def _extract_prompt_meta(entry):
    prompt = entry.get("prompt")
    if isinstance(prompt, list) and len(prompt) >= 4 and isinstance(prompt[3], dict):
        return prompt[3]
    return {}


def _extract_prompt_number(entry):
    prompt = entry.get("prompt")
    if isinstance(prompt, list) and prompt:
        return prompt[0]
    return None


def _extract_prompt_text(prompt_graph):
    prompts = []
    for node in prompt_graph.values():
        if node.get("class_type") != "CLIPTextEncode":
            continue
        text = str((node.get("inputs") or {}).get("text", "")).strip()
        if text and text not in prompts:
            prompts.append(text)
    return prompts[0] if prompts else ""


def _extract_checkpoint_name(prompt_graph):
    for node in prompt_graph.values():
        if node.get("class_type") == "CheckpointLoaderSimple":
            return str((node.get("inputs") or {}).get("ckpt_name", "")).strip()
    return ""


def _humanize_checkpoint_name(name):
    raw = str(name or "").strip()
    if not raw:
        return "Image model"
    stem = raw.rsplit("/", 1)[-1]
    for suffix in (".safetensors", ".ckpt", ".gguf", ".pt", ".bin"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = stem.replace("sd_xl", "SDXL").replace("sdxl", "SDXL").replace("flux1", "Flux_1")
    replacements = {
        "sd": "SD",
        "xl": "XL",
        "flux": "Flux",
        "fp16": "FP16",
        "fp8": "FP8",
        "vae": "VAE",
    }
    parts = [part for part in stem.replace("-", "_").split("_") if part]
    pretty = []
    for part in parts:
        lower = part.lower()
        pretty.append(replacements.get(lower, part.upper() if part.isupper() else part.capitalize()))
    return " ".join(pretty[:6]).strip() or "Image model"


def _classify_checkpoint_family(name):
    lower = str(name or "").lower()
    if "flux" in lower:
        return "flux_checkpoint"
    return "sdxl_checkpoint"


def _dedupe_model_catalog(items):
    seen = set()
    deduped = []
    for item in items:
        key = item.get("id") or item.get("name")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _available_checkpoint_models():
    models = []
    try:
        _, info = comfyui_json("/object_info/CheckpointLoaderSimple", timeout=20)
        ckpt_names = (((info or {}).get("CheckpointLoaderSimple") or {}).get("input") or {}).get("required", {}).get("ckpt_name", [[]])[0]
        for name in ckpt_names or []:
            lower = str(name).lower()
            if lower.startswith("ace_") or "stable-audio" in lower or "audio" in lower:
                continue
            family = _classify_checkpoint_family(name)
            models.append(
                {
                    "id": str(name),
                    "name": str(name),
                    "label": _humanize_checkpoint_name(name),
                    "family": family,
                    "mode": "txt2img+img2img",
                }
            )
    except Exception:
        pass
    return models


def _derive_image_title(prompt_text):
    text = " ".join(str(prompt_text or "").replace("\n", " ").split())
    if not text:
        return "Untitled render"

    for separator in (" --", " |", " Negative prompt:", " negative prompt:", ", cinematic", ", dramatic", ", volumetric", ", 35mm", ", highly", ", ultra", ", detailed"):
        if separator in text:
            text = text.split(separator, 1)[0].strip(" ,.;:-")

    segments = [segment.strip(" ,.;:-") for segment in text.split(",") if segment.strip(" ,.;:-")]
    candidate = next((segment for segment in segments if len(segment.split()) >= 2), segments[0] if segments else text)

    stop_phrases = [
        "cinematic",
        "dramatic",
        "volumetric fog",
        "wet concrete",
        "brass details",
        "analog command room",
        "35mm photograph",
        "physically based",
        "highly detailed",
    ]
    for phrase in stop_phrases:
        candidate = candidate.replace(phrase, " ")
        candidate = candidate.replace(phrase.title(), " ")

    words = [word for word in candidate.split() if word]
    if not words:
        words = [word for word in text.split() if word]

    trimmed = " ".join(words[:7]).strip(" ,.;:-")
    return trimmed[:1].upper() + trimmed[1:] if trimmed else "Untitled render"


def _trim_excerpt(text, limit=320):
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_\-\/]+)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FRONTMATTER_BOUNDARY = "---"


def normalize_text(text):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_frontmatter(text):
    if not str(text or "").startswith(FRONTMATTER_BOUNDARY + "\n"):
        return {}, str(text or "")
    lines = str(text).splitlines()
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == FRONTMATTER_BOUNDARY:
            end = idx
            break
    if end is None:
        return {}, str(text)
    frontmatter_lines = lines[1:end]
    body = "\n".join(lines[end + 1 :]).lstrip()
    meta = {}
    current_key = None
    for line in frontmatter_lines:
        if not line.strip():
            continue
        if re.match(r"^[A-Za-z0-9_\-]+:\s*", line):
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value:
                meta[key] = value
                current_key = None
            else:
                meta[key] = []
                current_key = key
        elif current_key and line.lstrip().startswith("- "):
            meta[current_key].append(line.split("- ", 1)[1].strip().strip('"').strip("'"))
        else:
            current_key = None
    return meta, body


def note_title(path, frontmatter):
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return Path(path).stem


def extract_links(text):
    links = []
    for match in WIKILINK_RE.findall(str(text or "")):
        link = match.split("|", 1)[0].split("#", 1)[0].strip()
        if link and link not in links:
            links.append(link)
    return links


def extract_tags(text, frontmatter):
    tags = []
    fm_tags = frontmatter.get("tags")
    if isinstance(fm_tags, list):
        for tag in fm_tags:
            tag = str(tag).strip().lstrip("#")
            if tag and tag not in tags:
                tags.append(tag)
    elif isinstance(fm_tags, str):
        tag = fm_tags.strip().lstrip("#")
        if tag:
            tags.append(tag)
    for tag in TAG_RE.findall(str(text or "")):
        if tag not in tags:
            tags.append(tag)
    return tags


def chunk_markdown(text, chunk_size=NOTE_CHUNK_SIZE, chunk_overlap=NOTE_CHUNK_OVERLAP):
    lines = str(text or "").splitlines()
    sections = []
    current_heading = []
    current_lines = []

    def flush():
        if current_lines:
            sections.append((list(current_heading), "\n".join(current_lines).strip()))

    for line in lines:
        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            current_heading[:] = current_heading[: level - 1] + [title]
            current_lines[:] = [line]
        else:
            current_lines.append(line)
    flush()

    chunks = []
    for headings, block in sections or [([], str(text or ""))]:
        block = normalize_text(block)
        if not block:
            continue
        if len(block) <= chunk_size:
            chunks.append({"headings": headings, "text": block})
            continue
        start = 0
        while start < len(block):
            end = min(len(block), start + chunk_size)
            piece = block[start:end]
            if end < len(block):
                split_at = max(piece.rfind("\n\n"), piece.rfind(". "))
                if split_at > chunk_size * 0.5:
                    end = start + split_at + 1
                    piece = block[start:end]
            chunks.append({"headings": headings, "text": normalize_text(piece)})
            if end >= len(block):
                break
            start = max(end - chunk_overlap, start + 1)
    return [chunk for chunk in chunks if chunk["text"]]


def _resolve_vault_path(relative_path="", require_path=False):
    raw = str(relative_path or "").strip().lstrip("/")
    if not raw:
        if require_path:
            raise ValueError("path required")
        return OBSIDIAN_VAULT_DIR
    resolved = (OBSIDIAN_VAULT_DIR / raw).resolve()
    if OBSIDIAN_VAULT_DIR not in resolved.parents and resolved != OBSIDIAN_VAULT_DIR:
        raise ValueError("invalid path")
    return resolved


def _resolve_note_path(relative_path):
    raw = str(relative_path or "").strip().lstrip("/")
    if not raw:
        raise ValueError("path required")
    return _resolve_vault_path(raw, require_path=True)


def _safe_note_relative(path):
    return str(Path(path).resolve().relative_to(OBSIDIAN_VAULT_DIR))


def _is_markdown_path(path):
    return Path(path).suffix.lower() in {".md", ".markdown"}


def _note_id(rel_path, chunk_index, text):
    key = hashlib.sha256(f"{rel_path}:{chunk_index}:{text}".encode()).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"obsidian://{KNOWLEDGE_COLLECTION}/{key}"))


def _resolve_model_entry(payload):
    requested = str(payload.get("model") or COMFYUI_DEFAULT_MODEL).strip()
    family = str(payload.get("model_family") or "").strip()
    catalog = _dedupe_model_catalog(_available_checkpoint_models() + _default_model_catalog())
    for entry in catalog:
        if entry.get("id") == requested or entry.get("name") == requested:
            return entry
    return {
        "id": requested,
        "name": requested,
        "label": _humanize_checkpoint_name(requested),
        "family": family or _classify_checkpoint_family(requested),
        "mode": "txt2img+img2img",
    }


def _build_sdxl_checkpoint_workflow(model_name, payload, input_image_name=None):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required")

    negative_prompt = str(payload.get("negative_prompt") or COMFYUI_DEFAULT_NEGATIVE).strip()
    width = _sanitize_int(payload.get("width"), 1024, 512, 1536)
    height = _sanitize_int(payload.get("height"), 1024, 512, 1536)
    steps = _sanitize_int(payload.get("steps"), 6 if "turbo" in model_name.lower() else 24, 1, 50)
    cfg = _sanitize_float(payload.get("cfg"), 1.8 if "turbo" in model_name.lower() else 6.0, 1.0, 20.0)
    denoise = _sanitize_float(payload.get("denoise"), 0.65, 0.05, 1.0)
    seed = _sanitize_int(payload.get("seed"), _random_seed(), 0, 2**63 - 1)
    sampler_name = str(payload.get("sampler_name") or "euler").strip()
    scheduler = str(payload.get("scheduler") or "normal").strip()
    filename_prefix = str(payload.get("filename_prefix") or "mission-studio").strip()

    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model_name}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["1", 1]}},
    }

    if input_image_name:
        workflow.update(
            {
                "4": {"class_type": "LoadImage", "inputs": {"image": input_image_name}},
                "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["1", 2]}},
                "6": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": seed,
                        "steps": steps,
                        "cfg": cfg,
                        "sampler_name": sampler_name,
                        "scheduler": scheduler,
                        "denoise": denoise,
                        "model": ["1", 0],
                        "positive": ["2", 0],
                        "negative": ["3", 0],
                        "latent_image": ["5", 0],
                    },
                },
                "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
                "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": filename_prefix, "images": ["7", 0]}},
            }
        )
    else:
        workflow.update(
            {
                "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
                "5": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": seed,
                        "steps": steps,
                        "cfg": cfg,
                        "sampler_name": sampler_name,
                        "scheduler": scheduler,
                        "denoise": 1.0,
                        "model": ["1", 0],
                        "positive": ["2", 0],
                        "negative": ["3", 0],
                        "latent_image": ["4", 0],
                    },
                },
                "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
                "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": filename_prefix, "images": ["6", 0]}},
            }
        )

    return workflow, prompt


def _build_sdxl_inpaint_workflow(model_name, payload, input_image_name, input_mask_name):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required")
    if not input_image_name:
        raise ValueError("Source image required for inpaint mode")
    if not input_mask_name:
        raise ValueError("Mask image required for inpaint mode")

    negative_prompt = str(payload.get("negative_prompt") or COMFYUI_DEFAULT_NEGATIVE).strip()
    steps = _sanitize_int(payload.get("steps"), 24, 1, 50)
    cfg = _sanitize_float(payload.get("cfg"), 6.0, 1.0, 20.0)
    denoise = _sanitize_float(payload.get("denoise"), 0.85, 0.05, 1.0)
    seed = _sanitize_int(payload.get("seed"), _random_seed(), 0, 2**63 - 1)
    sampler_name = str(payload.get("sampler_name") or "dpmpp_2m").strip()
    scheduler = str(payload.get("scheduler") or "karras").strip()
    filename_prefix = str(payload.get("filename_prefix") or "mission-studio").strip()
    grow_mask_by = _sanitize_int(payload.get("grow_mask_by"), 8, 0, 64)
    mask_channel = str(payload.get("mask_channel") or "red").strip().lower()
    if mask_channel not in {"alpha", "red", "green", "blue"}:
        mask_channel = "red"

    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model_name}},
        "2": {"class_type": "LoadImage", "inputs": {"image": input_image_name}},
        "3": {"class_type": "LoadImageMask", "inputs": {"image": input_mask_name, "channel": mask_channel}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["1", 1]}},
        "6": {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {"pixels": ["2", 0], "vae": ["1", 2], "mask": ["3", 0], "grow_mask_by": grow_mask_by},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": denoise,
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
            },
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]}},
    }
    return workflow, prompt


def _build_flux_checkpoint_workflow(model_name, payload, input_image_name=None):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required")

    negative_prompt = str(payload.get("negative_prompt") or "").strip()
    width = _sanitize_int(payload.get("width"), 1024, 512, 1536)
    height = _sanitize_int(payload.get("height"), 1024, 512, 1536)
    steps = _sanitize_int(payload.get("steps"), 4 if "schnell" in model_name.lower() else 20, 1, 50)
    denoise = _sanitize_float(payload.get("denoise"), 0.65, 0.05, 1.0)
    seed = _sanitize_int(payload.get("seed"), _random_seed(), 0, 2**63 - 1)
    sampler_name = str(payload.get("sampler_name") or "euler").strip()
    scheduler = str(payload.get("scheduler") or "simple").strip()
    filename_prefix = str(payload.get("filename_prefix") or "mission-studio").strip()

    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model_name}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["1", 1]}},
    }

    if input_image_name:
        workflow.update(
            {
                "4": {"class_type": "LoadImage", "inputs": {"image": input_image_name}},
                "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["1", 2]}},
                "6": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": seed,
                        "steps": steps,
                        "cfg": 1.0,
                        "sampler_name": sampler_name,
                        "scheduler": scheduler,
                        "denoise": denoise,
                        "model": ["1", 0],
                        "positive": ["2", 0],
                        "negative": ["3", 0],
                        "latent_image": ["5", 0],
                    },
                },
                "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
                "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": filename_prefix, "images": ["7", 0]}},
            }
        )
    else:
        workflow.update(
            {
                "4": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
                "5": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": seed,
                        "steps": steps,
                        "cfg": 1.0,
                        "sampler_name": sampler_name,
                        "scheduler": scheduler,
                        "denoise": 1.0,
                        "model": ["1", 0],
                        "positive": ["2", 0],
                        "negative": ["3", 0],
                        "latent_image": ["4", 0],
                    },
                },
                "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
                "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": filename_prefix, "images": ["6", 0]}},
            }
        )

    return workflow, prompt


def _build_flux_inpaint_workflow(model_name, payload, input_image_name, input_mask_name):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required")
    if not input_image_name:
        raise ValueError("Source image required for inpaint mode")
    if not input_mask_name:
        raise ValueError("Mask image required for inpaint mode")

    steps = _sanitize_int(payload.get("steps"), 4 if "schnell" in model_name.lower() else 20, 1, 50)
    denoise = _sanitize_float(payload.get("denoise"), 0.85, 0.05, 1.0)
    seed = _sanitize_int(payload.get("seed"), _random_seed(), 0, 2**63 - 1)
    sampler_name = str(payload.get("sampler_name") or "euler").strip()
    scheduler = str(payload.get("scheduler") or "simple").strip()
    filename_prefix = str(payload.get("filename_prefix") or "mission-studio").strip()
    grow_mask_by = _sanitize_int(payload.get("grow_mask_by"), 8, 0, 64)
    mask_channel = str(payload.get("mask_channel") or "red").strip().lower()
    if mask_channel not in {"alpha", "red", "green", "blue"}:
        mask_channel = "red"

    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model_name}},
        "2": {"class_type": "LoadImage", "inputs": {"image": input_image_name}},
        "3": {"class_type": "LoadImageMask", "inputs": {"image": input_mask_name, "channel": mask_channel}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "6": {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {"pixels": ["2", 0], "vae": ["1", 2], "mask": ["3", 0], "grow_mask_by": grow_mask_by},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": denoise,
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
            },
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]}},
    }
    return workflow, prompt


def _build_image_workflow(payload, input_image_name=None, input_mask_name=None):
    entry = _resolve_model_entry(payload)
    family = entry.get("family") or "sdxl_checkpoint"
    model_name = entry.get("name") or entry.get("id") or COMFYUI_DEFAULT_MODEL
    mode = str(payload.get("mode") or "").strip().lower()

    if mode == "inpaint":
        if family == "flux_checkpoint":
            workflow, prompt = _build_flux_inpaint_workflow(model_name, payload, input_image_name, input_mask_name)
        else:
            workflow, prompt = _build_sdxl_inpaint_workflow(model_name, payload, input_image_name, input_mask_name)
    elif family == "flux_checkpoint":
        workflow, prompt = _build_flux_checkpoint_workflow(model_name, payload, input_image_name=input_image_name)
    else:
        workflow, prompt = _build_sdxl_checkpoint_workflow(model_name, payload, input_image_name=input_image_name)

    return workflow, entry, prompt


def _extract_image_assets(outputs):
    assets = []
    for output in (outputs or {}).values():
        for image in output.get("images") or []:
            filename = str(image.get("filename", "")).strip()
            subfolder = str(image.get("subfolder", "")).strip()
            image_type = str(image.get("type", "output")).strip() or "output"
            if not filename:
                continue
            query = urllib.parse.urlencode(
                {"filename": filename, "subfolder": subfolder, "type": image_type}
            )
            assets.append(
                {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": image_type,
                    "url": f"/api/images/view?{query}",
                }
            )
    return assets


def _extract_video_assets(outputs):
    assets = []
    for output in (outputs or {}).values():
        for video in output.get("gifs") or []:
            filename = str(video.get("filename", "")).strip()
            subfolder = str(video.get("subfolder", "")).strip()
            asset_type = str(video.get("type", "output")).strip() or "output"
            if not filename:
                continue
            query = urllib.parse.urlencode(
                {"filename": filename, "subfolder": subfolder, "type": asset_type}
            )
            assets.append(
                {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": asset_type,
                    "format": str(video.get("format", "")).strip(),
                    "url": f"/api/images/view?{query}",
                }
            )
    return assets


def _humanize_video_filename(name):
    raw = str(name or "").strip()
    if not raw:
        return "Mission clip"
    stem = Path(raw).stem
    if stem.endswith("-audio"):
        stem = stem[:-6]
    stem = re.sub(r"_\d{5,}$", "", stem)
    stem = stem.replace("_", " ").replace("-", " ").strip()
    return stem.title() if stem else "Mission clip"


def _extract_completion_time(entry):
    for message in (entry.get("status") or {}).get("messages") or []:
        if not isinstance(message, list) or len(message) < 2:
            continue
        if message[0] == "execution_success":
            return _safe_iso_from_ms((message[1] or {}).get("timestamp"))
    return None


def _parse_queue_job(raw_job, running=False):
    if not isinstance(raw_job, list) or len(raw_job) < 3:
        return None
    prompt_id = str(raw_job[1]).strip()
    prompt_graph = raw_job[2] if isinstance(raw_job[2], dict) else {}
    meta = raw_job[3] if len(raw_job) > 3 and isinstance(raw_job[3], dict) else {}
    prompt_text = _extract_prompt_text(prompt_graph)
    model_name = _extract_checkpoint_name(prompt_graph)
    return {
        "prompt_id": prompt_id,
        "queue_number": raw_job[0],
        "status": "running" if running else "pending",
        "model": model_name,
        "model_label": _humanize_checkpoint_name(model_name),
        "model_family": _classify_checkpoint_family(model_name),
        "title": _derive_image_title(prompt_text),
        "prompt": prompt_text,
        "created_at": _safe_iso_from_ms(meta.get("create_time")),
    }


def _history_entry_to_image_job(prompt_id, entry):
    prompt_graph = _extract_prompt_graph(entry)
    assets = _extract_image_assets(entry.get("outputs") or {})
    if not assets:
        return None
    meta = _extract_prompt_meta(entry)
    prompt_text = _extract_prompt_text(prompt_graph)
    model_name = _extract_checkpoint_name(prompt_graph)
    return {
        "prompt_id": prompt_id,
        "queue_number": _extract_prompt_number(entry),
        "status": ((entry.get("status") or {}).get("status_str") or "complete"),
        "model": model_name,
        "model_label": _humanize_checkpoint_name(model_name),
        "model_family": _classify_checkpoint_family(model_name),
        "title": _derive_image_title(prompt_text),
        "prompt": prompt_text,
        "created_at": _safe_iso_from_ms(meta.get("create_time")),
        "completed_at": _extract_completion_time(entry),
        "images": assets,
    }


def _extract_video_combine_inputs(prompt_graph):
    for node in (prompt_graph or {}).values():
        if node.get("class_type") == "VHS_VideoCombine":
            return node.get("inputs") or {}
    return {}


def _probe_video_asset(asset, completed_at=""):
    filename = str((asset or {}).get("filename") or "").strip()
    if not filename:
        return {}
    cache_key = (
        filename,
        str((asset or {}).get("subfolder") or "").strip(),
        str((asset or {}).get("type") or "output").strip(),
        str(completed_at or ""),
    )
    cached = AV_CLIP_META_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    suffix = Path(filename).suffix or ".mp4"
    temp_path = None
    try:
        _, video_bytes = comfyui_fetch_asset_bytes(
            filename,
            subfolder=(asset or {}).get("subfolder") or "",
            asset_type=(asset or {}).get("type") or "output",
            timeout=120,
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(video_bytes)
            temp_path = handle.name
        proc = subprocess.run(
            [
                "/usr/bin/ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                temp_path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        info = json.loads(proc.stdout or "{}")
        streams = info.get("streams") or []
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        format_info = info.get("format") or {}
        duration_value = format_info.get("duration") or video_stream.get("duration") or audio_stream.get("duration")
        try:
            duration_seconds = round(float(duration_value), 2) if duration_value is not None else None
        except Exception:
            duration_seconds = None
        try:
            size_bytes = int(format_info.get("size")) if format_info.get("size") is not None else len(video_bytes)
        except Exception:
            size_bytes = len(video_bytes)
        metadata = {
            "has_audio": bool(audio_stream),
            "duration_seconds": duration_seconds,
            "width": int(video_stream.get("width") or 0) or None,
            "height": int(video_stream.get("height") or 0) or None,
            "size_bytes": size_bytes,
        }
    except Exception:
        metadata = {}
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    AV_CLIP_META_CACHE[cache_key] = dict(metadata)
    return metadata


def _history_entry_to_clip_job(prompt_id, entry):
    prompt_graph = _extract_prompt_graph(entry)
    assets = _extract_video_assets(entry.get("outputs") or {})
    if not assets:
        return None
    meta = _extract_prompt_meta(entry)
    completed_at = _extract_completion_time(entry)
    primary = assets[0]
    video_inputs = _extract_video_combine_inputs(prompt_graph)
    primary_meta = _probe_video_asset(primary, completed_at=completed_at)
    try:
        frame_rate = float(video_inputs.get("frame_rate")) if video_inputs.get("frame_rate") is not None else None
    except Exception:
        frame_rate = None
    try:
        loop_count = int(video_inputs.get("loop_count")) if video_inputs.get("loop_count") is not None else None
    except Exception:
        loop_count = None
    return {
        "prompt_id": prompt_id,
        "status": ((entry.get("status") or {}).get("status_str") or "complete"),
        "title": _humanize_video_filename(primary.get("filename")),
        "created_at": _safe_iso_from_ms(meta.get("create_time")),
        "completed_at": completed_at,
        "has_audio": bool(video_inputs.get("audio")) or bool(primary_meta.get("has_audio")),
        "frame_rate": frame_rate,
        "loop_count": loop_count,
        "duration_seconds": primary_meta.get("duration_seconds"),
        "width": primary_meta.get("width"),
        "height": primary_meta.get("height"),
        "size_bytes": primary_meta.get("size_bytes"),
        "videos": assets,
    }


def _update_image_node_state(**updates):
    with IMAGE_NODE_LOCK:
        IMAGE_NODE_STATE.update(updates)


def get_image_node_state():
    with IMAGE_NODE_LOCK:
        return dict(IMAGE_NODE_STATE)


def release_image_node(reason="manual", unload_models=True, free_memory=True):
    payload = {"unload_models": bool(unload_models), "free_memory": bool(free_memory)}
    last_exc = None
    for path in ("/free", "/api/free"):
        try:
            body = json.dumps(payload).encode()
            comfyui_request(path, timeout=30, method="POST", data=body, headers={"Content-Type": "application/json"})
            _update_image_node_state(
                last_release_at=_watch_timestamp(),
                last_release_reason=str(reason or "manual"),
                last_release_status="released",
                last_error="",
                released_since_submit=True,
            )
            return {"status": "released", "reason": str(reason or "manual"), "payload": payload}
        except Exception as exc:
            last_exc = exc
    _update_image_node_state(last_error=str(last_exc or "release failed"), last_release_status="error")
    raise RuntimeError(str(last_exc or "release failed"))


def interrupt_image_node():
    last_exc = None
    for path in ("/interrupt", "/api/interrupt"):
        try:
            comfyui_json(path, timeout=15, method="POST", payload={})
            return {"status": "interrupted"}
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(str(last_exc or "interrupt failed"))


def get_av_config():
    voices = []
    tts_models = []
    stt_models = []
    errors = []
    video_formats = []
    clip_builder_available = False
    try:
        data = service_json(TTS_BASE_URL, "/v1/models", timeout=20)
        entries = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for entry in entries or []:
            model_id = str(entry.get("id") or entry.get("name") or "").strip()
            if model_id:
                tts_models.append({"id": model_id, "label": model_id})
    except Exception as exc:
        errors.append(f"tts_models: {exc}")
    try:
        data = service_json(TTS_BASE_URL, "/v1/audio/voices", timeout=20)
        if isinstance(data, dict):
            voices = data.get("voices") or data.get("data") or []
        elif isinstance(data, list):
            voices = data
    except Exception as exc:
        errors.append(f"tts: {exc}")
    try:
        data = service_json(STT_BASE_URL, "/v1/models", timeout=20)
        entries = []
        if isinstance(data, dict):
            entries = data.get("data") or data.get("models") or []
        elif isinstance(data, list):
            entries = data
        for entry in entries:
            model_id = str(entry.get("id") or entry.get("name") or "").strip()
            if model_id:
                stt_models.append({"id": model_id, "label": model_id})
    except Exception:
        try:
            data = service_json(STT_BASE_URL, "/v1/audio/models", timeout=20)
            entries = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for entry in entries or []:
                model_id = str(entry.get("id") or entry.get("name") or "").strip()
                if model_id:
                    stt_models.append({"id": model_id, "label": model_id})
        except Exception as exc:
            errors.append(f"stt: {exc}")
    try:
        _, info = comfyui_json("/object_info/VHS_VideoCombine", timeout=20)
        combine_info = (info or {}).get("VHS_VideoCombine") or {}
        format_info = (((combine_info.get("input") or {}).get("required") or {}).get("format") or [[], {}])[0]
        video_formats = [value for value in format_info if str(value).startswith("video/")]
        clip_builder_available = bool(video_formats)
    except Exception as exc:
        errors.append(f"video: {exc}")
    return {
        "available": bool(voices or stt_models or tts_models or video_formats),
        "voices": voices,
        "tts_models": tts_models,
        "stt_models": stt_models,
        "video_tools": {
            "clip_builder_available": clip_builder_available,
            "formats": video_formats,
            "remote_input_dir": COMFYUI_REMOTE_INPUT_DIR,
        },
        "recent_clips": list_recent_av_clips(limit=AV_CLIP_LIMIT),
        "defaults": {
            "tts_model": DEFAULT_AV_TTS_MODEL,
            "stt_model": DEFAULT_AV_STT_MODEL,
            "clip_format": DEFAULT_AV_CLIP_FORMAT,
        },
        "errors": errors,
    }


def synthesize_av_speech(payload):
    text = normalize_text(payload.get("text") or "")
    if not text:
        raise ValueError("Text required")
    voice = str(payload.get("voice") or "af_sky").strip() or "af_sky"
    model = str(payload.get("model") or DEFAULT_AV_TTS_MODEL).strip() or DEFAULT_AV_TTS_MODEL
    response_format = str(payload.get("response_format") or "mp3").strip().lower() or "mp3"
    request_payload = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": response_format,
    }
    status, headers, data = service_request_bytes(
        f"{TTS_BASE_URL}/v1/audio/speech",
        timeout=180,
        method="POST",
        data=json.dumps(request_payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    content_type = (headers.get("Content-Type") or "audio/mpeg").split(";", 1)[0].strip()
    return {
        "status": "ok" if status == 200 else "error",
        "voice": voice,
        "model": model,
        "content_type": content_type,
        "audio_data_url": _encode_data_url(data, content_type),
    }


def transcribe_av_media(payload):
    media_data = payload.get("media_data")
    if not media_data:
        raise ValueError("Media data required")
    media_bytes, mime, ext = _decode_media_data_url(media_data)
    model = str(payload.get("model") or DEFAULT_AV_STT_MODEL).strip() or DEFAULT_AV_STT_MODEL
    fields = {"model": model}
    boundary, body = _multipart_form_data(fields, [("file", f"mission-transcribe{ext}", mime, media_bytes)])
    status, headers, data = service_request_bytes(
        f"{STT_BASE_URL}/v1/audio/transcriptions",
        timeout=600,
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    parsed = json.loads(data.decode())
    transcript = normalize_text(parsed.get("text") or parsed.get("transcript") or "")
    return {
        "status": "ok" if status == 200 else "error",
        "model": model,
        "transcript": transcript,
        "segments": parsed.get("segments") or [],
        "language": parsed.get("language"),
    }


def _build_av_clip_workflow(frames_directory, payload, audio_path=""):
    clip_format = str(payload.get("format") or DEFAULT_AV_CLIP_FORMAT).strip() or DEFAULT_AV_CLIP_FORMAT
    frame_rate = _sanitize_float(payload.get("frame_rate"), 8, 1, 60)
    loop_count = _sanitize_int(payload.get("loop_count"), 0, 0, 100)
    pingpong = bool(payload.get("pingpong", False))
    save_output = bool(payload.get("save_output", True))
    prefix = str(payload.get("title") or "mission-clip").strip().lower()
    prefix = re.sub(r"[^a-z0-9._-]+", "-", prefix).strip("-") or "mission-clip"
    filename_prefix = f"video/{prefix}"

    workflow = {
        "1": {
            "class_type": "VHS_LoadImagesPath",
            "inputs": {
                "directory": frames_directory,
                "image_load_cap": 0,
                "skip_first_images": 0,
                "select_every_nth": 1,
            },
        },
        "3": {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "images": ["1", 0],
                "frame_rate": frame_rate,
                "loop_count": loop_count,
                "filename_prefix": filename_prefix,
                "format": clip_format,
                "pingpong": pingpong,
                "save_output": save_output,
            },
        },
    }
    if audio_path:
        workflow["2"] = {
            "class_type": "VHS_LoadAudio",
            "inputs": {
                "audio_file": audio_path,
                "seek_seconds": 0,
                "duration": 0,
            },
        }
        workflow["3"]["inputs"]["audio"] = ["2", 0]
    return workflow


def _upload_clip_frames(payload):
    frames = payload.get("frames") or []
    if not isinstance(frames, list) or not frames:
        raise ValueError("At least one frame is required")
    if len(frames) > 24:
        raise ValueError("Clip builder supports up to 24 frames per job")

    subfolder = f"mission-clips/{uuid.uuid4().hex}"
    for index, frame in enumerate(frames, start=1):
        if not isinstance(frame, dict):
            raise ValueError("Invalid frame payload")
        if frame.get("data_url"):
            frame_bytes, ext = _decode_data_url(frame.get("data_url"))
            content_type = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[ext.lstrip(".")]
        else:
            filename = str(frame.get("filename") or "").strip()
            if not filename:
                raise ValueError("Frame filename required")
            content_type, frame_bytes = comfyui_fetch_asset_bytes(
                filename,
                subfolder=frame.get("subfolder") or "",
                asset_type=frame.get("type") or "output",
                timeout=120,
            )
            ext = Path(filename).suffix.lower() or ".png"
        upload_name = f"frame-{index:03d}{ext}"
        comfyui_upload_input_file(upload_name, frame_bytes, subfolder=subfolder, content_type=content_type)

    return subfolder


def _upload_clip_audio(payload, subfolder):
    audio_data = payload.get("audio_data")
    if not audio_data:
        return ""
    audio_bytes, mime, ext = _decode_media_data_url(audio_data)
    audio_name = f"audio{ext}"
    comfyui_upload_input_file(audio_name, audio_bytes, subfolder=subfolder, content_type=mime)
    folder = COMFYUI_REMOTE_INPUT_DIR.rstrip("\\/")
    return folder + "\\" + subfolder.replace("/", "\\") + "\\" + audio_name


def submit_av_clip(payload):
    _update_image_node_state(last_submit_at=_watch_timestamp(), released_since_submit=False, last_error="", last_release_status="")
    subfolder = _upload_clip_frames(payload)
    frames_directory = COMFYUI_REMOTE_INPUT_DIR.rstrip("\\/") + "\\" + subfolder.replace("/", "\\")
    audio_path = _upload_clip_audio(payload, subfolder)
    workflow = _build_av_clip_workflow(frames_directory, payload, audio_path=audio_path)
    status, data = comfyui_json("/prompt", timeout=120, method="POST", payload={"prompt": workflow})
    if status != 200:
        raise RuntimeError("Could not queue clip job")
    return {
        "status": "queued",
        "prompt_id": data.get("prompt_id"),
        "number": data.get("number"),
        "frame_count": len(payload.get("frames") or []),
        "has_audio": bool(audio_path),
        "format": str(payload.get("format") or DEFAULT_AV_CLIP_FORMAT),
    }


def get_av_job(prompt_id):
    prompt_id = str(prompt_id or "").strip()
    if not prompt_id:
        raise ValueError("prompt_id required")
    _, history = comfyui_json(f"/history/{prompt_id}", timeout=20)
    entry = (history or {}).get(prompt_id)
    if entry:
        clip_job = _history_entry_to_clip_job(prompt_id, entry)
        if clip_job:
            return clip_job
        return {
            "prompt_id": prompt_id,
            "status": ((entry.get("status") or {}).get("status_str") or "unknown"),
            "videos": [],
        }

    _, queue = comfyui_json("/queue", timeout=20)
    for running in queue.get("queue_running") or []:
        if len(running) > 1 and str(running[1]).strip() == prompt_id:
            return {"prompt_id": prompt_id, "status": "running", "videos": []}
    for pending in queue.get("queue_pending") or []:
        if len(pending) > 1 and str(pending[1]).strip() == prompt_id:
            return {"prompt_id": prompt_id, "status": "pending", "videos": []}
    return {"prompt_id": prompt_id, "status": "unknown", "videos": []}


def list_recent_av_clips(limit=AV_CLIP_LIMIT):
    limit = max(1, min(int(limit or AV_CLIP_LIMIT), 20))
    try:
        _, history = comfyui_json(f"/history?max_items={limit * 4}", timeout=30)
    except Exception:
        return []
    clips = []
    for prompt_id, entry in sorted(
        (history or {}).items(),
        key=lambda item: _extract_prompt_number(item[1]) or 0,
        reverse=True,
    ):
        clip_job = _history_entry_to_clip_job(prompt_id, entry)
        if clip_job:
            clips.append(clip_job)
        if len(clips) >= limit:
            break
    return clips


def _image_node_watch_loop():
    while True:
        try:
            if IMAGE_AUTO_RELEASE_IDLE_SECONDS > 0:
                dashboard = get_image_dashboard()
                queue = dashboard.get("queue") or {}
                device = dashboard.get("device") or {}
                total = int(device.get("vram_total") or 0)
                free = int(device.get("vram_free") or 0)
                used = max(0, total - free)
                state = get_image_node_state()
                idle_for = _watch_timestamp() - int(state.get("last_submit_at") or 0)
                if (
                    dashboard.get("available")
                    and not (queue.get("running_count") or 0)
                    and not (queue.get("pending_count") or 0)
                    and used >= IMAGE_RELEASE_MIN_USED_BYTES
                    and idle_for >= IMAGE_AUTO_RELEASE_IDLE_SECONDS
                    and not state.get("released_since_submit")
                ):
                    try:
                        release_image_node(reason="auto-idle")
                    except Exception as exc:
                        _update_image_node_state(last_error=str(exc), last_release_status="error")
        except Exception:
            pass
        time.sleep(IMAGE_RELEASE_CHECK_INTERVAL)


def get_image_config():
    catalog = _dedupe_model_catalog(_available_checkpoint_models() + _default_model_catalog())
    default_entry = (
        next((entry for entry in catalog if entry.get("name") == "flux1-schnell-fp8.safetensors"), None)
        or next((entry for entry in catalog if entry.get("name") == COMFYUI_DEFAULT_MODEL or entry.get("id") == COMFYUI_DEFAULT_MODEL), None)
        or (catalog[0] if catalog else None)
    )
    default_family = (default_entry or {}).get("family", _classify_checkpoint_family(COMFYUI_DEFAULT_MODEL))
    return {
        "available": True,
        "models": catalog,
        "defaults": {
            "model": (default_entry or {}).get("name", COMFYUI_DEFAULT_MODEL),
            "model_family": default_family,
            "negative_prompt": "" if default_family == "flux_checkpoint" else COMFYUI_DEFAULT_NEGATIVE,
            "width": 1024,
            "height": 1024,
            "steps": 4 if default_family == "flux_checkpoint" else 6,
            "cfg": 1.0 if default_family == "flux_checkpoint" else 1.8,
            "denoise": 0.65,
        },
    }


def _upload_source_image(payload):
    source_data = payload.get("source_image_data")
    source_asset = payload.get("source_asset") or {}

    if source_data:
        image_bytes, ext = _decode_data_url(source_data)
        filename = f"mission-studio-{uuid.uuid4().hex}{ext}"
    elif source_asset:
        filename = str(source_asset.get("filename", "")).strip()
        if not filename:
            raise ValueError("Invalid source asset")
        subfolder = str(source_asset.get("subfolder", "")).strip()
        image_type = str(source_asset.get("type", "output")).strip() or "output"
        query = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": image_type})
        _, _, image_bytes = comfyui_request(f"/view?{query}", timeout=90)
        ext = os.path.splitext(filename)[1] or ".png"
        filename = f"mission-studio-{uuid.uuid4().hex}{ext}"
    else:
        return None

    upload = comfyui_upload_image(filename, image_bytes, image_type="input", overwrite=True)
    return upload.get("name") or filename


def _upload_mask_image(payload):
    mask_data = payload.get("mask_image_data")
    mask_asset = payload.get("mask_asset") or {}

    if mask_data:
        image_bytes, ext = _decode_data_url(mask_data)
        filename = f"mission-mask-{uuid.uuid4().hex}{ext}"
    elif mask_asset:
        filename = str(mask_asset.get("filename", "")).strip()
        if not filename:
            raise ValueError("Invalid mask asset")
        subfolder = str(mask_asset.get("subfolder", "")).strip()
        image_type = str(mask_asset.get("type", "output")).strip() or "output"
        query = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": image_type})
        _, _, image_bytes = comfyui_request(f"/view?{query}", timeout=90)
        ext = os.path.splitext(filename)[1] or ".png"
        filename = f"mission-mask-{uuid.uuid4().hex}{ext}"
    else:
        return None

    upload = comfyui_upload_image(filename, image_bytes, image_type="input", overwrite=True)
    return upload.get("name") or filename


def submit_image_generation(payload):
    _update_image_node_state(last_submit_at=_watch_timestamp(), released_since_submit=False, last_error="", last_release_status="")
    input_image_name = _upload_source_image(payload)
    input_mask_name = _upload_mask_image(payload)
    workflow, model_entry, prompt_text = _build_image_workflow(
        payload, input_image_name=input_image_name, input_mask_name=input_mask_name
    )
    _, result = comfyui_json(
        "/prompt",
        timeout=60,
        method="POST",
        payload={"prompt": workflow, "client_id": "mission-control"},
    )
    prompt_id = str(result.get("prompt_id", "")).strip()
    if not prompt_id:
        raise RuntimeError("ComfyUI did not return a prompt id")
    model_name = model_entry.get("name") or model_entry.get("id") or ""
    return {
        "status": "queued",
        "prompt_id": prompt_id,
        "mode": str(payload.get("mode") or ("img2img" if input_image_name else "txt2img")).strip().lower(),
        "model": model_name,
        "model_label": model_entry.get("label") or _humanize_checkpoint_name(model_name),
        "model_family": model_entry.get("family") or _classify_checkpoint_family(model_name),
        "title": _derive_image_title(prompt_text),
        "prompt": prompt_text,
        "source_image": input_image_name,
        "mask_image": input_mask_name,
    }


def get_image_job(prompt_id):
    prompt_id = str(prompt_id or "").strip()
    if not prompt_id:
        return {"error": "prompt_id required"}

    try:
        _, history_entry = comfyui_json(f"/history/{urllib.parse.quote(prompt_id)}", timeout=20)
    except Exception:
        history_entry = {}

    entry = None
    if isinstance(history_entry, dict):
        if prompt_id in history_entry and isinstance(history_entry[prompt_id], dict):
            entry = history_entry[prompt_id]
        elif "prompt" in history_entry:
            entry = history_entry
    if entry:
        parsed = _history_entry_to_image_job(prompt_id, entry)
        if parsed:
            return {"status": "complete", "job": parsed}

    try:
        _, queue_data = comfyui_json("/queue", timeout=15)
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}

    for raw_job in queue_data.get("queue_running", []):
        parsed = _parse_queue_job(raw_job, running=True)
        if parsed and parsed.get("prompt_id") == prompt_id:
            return {"status": "running", "job": parsed}
    for raw_job in queue_data.get("queue_pending", []):
        parsed = _parse_queue_job(raw_job, running=False)
        if parsed and parsed.get("prompt_id") == prompt_id:
            return {"status": "pending", "job": parsed}

    return {"status": "unknown"}


def get_image_dashboard():
    fallback = {
        "available": False,
        "endpoint": COMFYUI_BASE_URL,
        "queue": {"running_count": 0, "pending_count": 0, "running": [], "pending": []},
        "recent": [],
    }
    try:
        _, system_stats = comfyui_json("/system_stats", timeout=15)
        _, queue_data = comfyui_json("/queue", timeout=15)
        _, history_data = comfyui_json(f"/history?max_items={COMFYUI_GALLERY_LIMIT}", timeout=20)
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback

    devices = system_stats.get("devices") or []
    device = devices[0] if devices else {}
    running_jobs = [
        parsed
        for parsed in (_parse_queue_job(item, running=True) for item in queue_data.get("queue_running", []))
        if parsed
    ]
    pending_jobs = [
        parsed
        for parsed in (_parse_queue_job(item, running=False) for item in queue_data.get("queue_pending", []))
        if parsed
    ]

    recent = []
    for prompt_id, entry in (history_data or {}).items():
        parsed = _history_entry_to_image_job(prompt_id, entry)
        if parsed:
            recent.append(parsed)

    recent.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {
        "available": True,
        "endpoint": COMFYUI_BASE_URL,
        "device": {
            "name": device.get("name", "Unknown device"),
            "type": device.get("type", "unknown"),
            "vram_total": device.get("vram_total", 0),
            "vram_free": device.get("vram_free", 0),
        },
        "queue": {
            "running_count": len(running_jobs),
            "pending_count": len(pending_jobs),
            "running": running_jobs,
            "pending": pending_jobs,
        },
        "recent": recent[:COMFYUI_GALLERY_LIMIT],
        "controls": {
            "release_available": True,
            **get_image_node_state(),
        },
    }


def get_knowledge_dashboard():
    fallback = {
        "available": False,
        "collection": KNOWLEDGE_COLLECTION,
        "embed_model": KNOWLEDGE_EMBED_MODEL,
        "status": "unavailable",
        "points_count": 0,
        "indexed_vectors_count": 0,
        "sample_notes": [],
        "watcher": get_watch_status(),
    }
    try:
        info = service_json(QDRANT_BASE_URL, f"/collections/{urllib.parse.quote(KNOWLEDGE_COLLECTION)}", timeout=15)
        count = service_json(
            QDRANT_BASE_URL,
            f"/collections/{urllib.parse.quote(KNOWLEDGE_COLLECTION)}/points/count",
            timeout=15,
            method="POST",
            payload={"exact": True},
        )
        sample = service_json(
            QDRANT_BASE_URL,
            f"/collections/{urllib.parse.quote(KNOWLEDGE_COLLECTION)}/points/scroll",
            timeout=15,
            method="POST",
            payload={"limit": KNOWLEDGE_SAMPLE_LIMIT, "with_payload": True, "with_vector": False},
        )
    except urllib.error.HTTPError as exc:
        fallback["error"] = f"Qdrant returned {exc.code}"
        return fallback
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback

    result = (info or {}).get("result") or {}
    sample_notes = []
    seen = set()
    for point in ((sample or {}).get("result") or {}).get("points") or []:
        payload = point.get("payload") or {}
        path = str(payload.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        sample_notes.append(
            {
                "title": str(payload.get("title") or os.path.splitext(os.path.basename(path))[0] or "Untitled note"),
                "path": path,
                "tags": payload.get("tags") or [],
                "modified_at": payload.get("modified_at"),
            }
        )

    return {
        "available": True,
        "collection": KNOWLEDGE_COLLECTION,
        "embed_model": KNOWLEDGE_EMBED_MODEL,
        "status": result.get("status", "unknown"),
        "points_count": ((count or {}).get("result") or {}).get("count", 0),
        "indexed_vectors_count": result.get("indexed_vectors_count", 0),
        "sample_notes": sample_notes,
        "watcher": get_watch_status(),
    }


def search_knowledge(query, limit=KNOWLEDGE_RESULT_LIMIT):
    text = " ".join(str(query or "").split())
    if not text:
        return {"available": False, "error": "query required", "results": []}

    try:
        embed_data = service_json(
            OLLAMA_BASE_URL,
            "/api/embed",
            timeout=90,
            method="POST",
            payload={"model": KNOWLEDGE_EMBED_MODEL, "input": [text]},
        )
        embedding = ((embed_data or {}).get("embeddings") or [[]])[0]
        if not embedding:
            raise RuntimeError("Embedding model returned no vector")
        search = service_json(
            QDRANT_BASE_URL,
            f"/collections/{urllib.parse.quote(KNOWLEDGE_COLLECTION)}/points/search",
            timeout=30,
            method="POST",
            payload={
                "vector": embedding,
                "limit": _sanitize_int(limit, KNOWLEDGE_RESULT_LIMIT, 1, 16),
                "with_payload": True,
                "with_vector": False,
            },
        )
    except urllib.error.HTTPError as exc:
        return {"available": False, "error": f"search failed with {exc.code}", "results": []}
    except Exception as exc:
        return {"available": False, "error": str(exc), "results": []}

    results = []
    for match in (search or {}).get("result") or []:
        payload = match.get("payload") or {}
        results.append(
            {
                "score": round(float(match.get("score") or 0.0), 4),
                "title": str(payload.get("title") or "Untitled note"),
                "path": str(payload.get("path") or ""),
                "text": _trim_excerpt(payload.get("text") or "", 320),
                "tags": payload.get("tags") or [],
                "headings": payload.get("headings") or [],
                "links": payload.get("links") or [],
                "modified_at": payload.get("modified_at"),
                "chunk_index": payload.get("chunk_index"),
            }
        )

    return {
        "available": True,
        "collection": KNOWLEDGE_COLLECTION,
        "embed_model": KNOWLEDGE_EMBED_MODEL,
        "query": text,
        "results": results,
    }


def embed_batch(texts):
    data = service_json(
        OLLAMA_BASE_URL,
        "/api/embed",
        timeout=180,
        method="POST",
        payload={"model": KNOWLEDGE_EMBED_MODEL, "input": texts},
    )
    embeddings = data.get("embeddings") or []
    if len(embeddings) != len(texts):
        raise RuntimeError(f"Embedding count mismatch: expected {len(texts)}, got {len(embeddings)}")
    return embeddings


def ensure_knowledge_collection(vector_size):
    try:
        service_json(QDRANT_BASE_URL, f"/collections/{urllib.parse.quote(KNOWLEDGE_COLLECTION)}", timeout=15)
        return
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    service_json(
        QDRANT_BASE_URL,
        f"/collections/{urllib.parse.quote(KNOWLEDGE_COLLECTION)}",
        timeout=30,
        method="PUT",
        payload={"vectors": {"size": vector_size, "distance": "Cosine"}},
    )


def qdrant_upsert(points):
    if not points:
        return
    service_json(
        QDRANT_BASE_URL,
        f"/collections/{urllib.parse.quote(KNOWLEDGE_COLLECTION)}/points?wait=true",
        timeout=180,
        method="PUT",
        payload={"points": points},
    )


def qdrant_delete_path(rel_path):
    service_json(
        QDRANT_BASE_URL,
        f"/collections/{urllib.parse.quote(KNOWLEDGE_COLLECTION)}/points/delete?wait=true",
        timeout=120,
        method="POST",
        payload={
            "filter": {
                "must": [
                    {"key": "source", "match": {"value": "obsidian"}},
                    {"key": "path", "match": {"value": rel_path}},
                ]
            }
        },
    )


def note_tree_node(path, root, depth=1, counter=None):
    if counter is None:
        counter = {"count": 0}
    counter["count"] += 1
    if counter["count"] > OBSIDIAN_TREE_LIMIT:
        return None
    rel_path = "" if path == root else str(path.relative_to(root))
    if path.is_dir():
        children = []
        visible_children = [
            child
            for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
            if not child.name.startswith(".") and (child.is_dir() or _is_markdown_path(child))
        ]
        if depth > 0:
            for child in visible_children:
                child_node = note_tree_node(child, root, depth=depth - 1, counter=counter)
                if child_node:
                    children.append(child_node)
        return {
            "type": "directory",
            "name": path.name if path != root else root.name,
            "path": rel_path,
            "children": children,
            "has_children": bool(visible_children),
            "loaded": depth > 0,
        }
    stat_info = path.stat()
    return {
        "type": "file",
        "name": path.name,
        "path": rel_path,
        "modified_at": int(stat_info.st_mtime),
        "size": stat_info.st_size,
    }


def get_notes_tree(relative_path=""):
    if not OBSIDIAN_VAULT_DIR.exists():
        return {"available": False, "error": "Synced vault not found", "root": None}
    target = _resolve_vault_path(relative_path)
    if not target.exists():
        raise FileNotFoundError("Directory not found")
    if not target.is_dir():
        raise ValueError("Tree path must be a directory")
    tree = note_tree_node(target, OBSIDIAN_VAULT_DIR, depth=1, counter={"count": 0})
    response = {
        "available": True,
        "root_path": str(OBSIDIAN_VAULT_DIR),
        "requested_path": "" if target == OBSIDIAN_VAULT_DIR else _safe_note_relative(target),
        "node": tree,
    }
    if target == OBSIDIAN_VAULT_DIR:
        response["root"] = tree
    return response


def get_note_file(relative_path):
    path = _resolve_note_path(relative_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Note not found")
    if not _is_markdown_path(path):
        raise ValueError("Only markdown notes are supported")
    content = path.read_text(encoding="utf-8", errors="ignore")
    stat_info = path.stat()
    frontmatter, body = parse_frontmatter(content)
    return {
        "path": _safe_note_relative(path),
        "name": path.name,
        "title": note_title(path, frontmatter),
        "content": content,
        "body": body,
        "frontmatter": frontmatter,
        "modified_at": int(stat_info.st_mtime),
        "size": stat_info.st_size,
    }


def reindex_note(relative_path):
    note = get_note_file(relative_path)
    rel_path = note["path"]
    text = note["content"]
    frontmatter, body = parse_frontmatter(text)
    body = normalize_text(body)

    try:
        qdrant_delete_path(rel_path)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise

    if not body:
        return {"path": rel_path, "chunks": 0, "status": "cleared"}

    title = note_title(rel_path, frontmatter)
    tags = extract_tags(text, frontmatter)
    links = extract_links(text)
    chunks = chunk_markdown(body)
    embeddings = embed_batch([chunk["text"] for chunk in chunks])
    ensure_knowledge_collection(len(embeddings[0]))
    stat_info = _resolve_note_path(rel_path).stat()
    points = []
    for index, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        points.append(
            {
                "id": _note_id(rel_path, index, chunk["text"]),
                "vector": vector,
                "payload": {
                    "vault": OBSIDIAN_VAULT_DIR.name,
                    "path": rel_path,
                    "title": title,
                    "tags": tags,
                    "links": links,
                    "headings": chunk["headings"],
                    "chunk_index": index,
                    "text": chunk["text"],
                    "modified_at": int(stat_info.st_mtime),
                    "created_at": int(stat_info.st_ctime),
                    "source": "obsidian",
                    "frontmatter": frontmatter,
                },
            }
        )
    qdrant_upsert(points)
    return {"path": rel_path, "chunks": len(points), "status": "indexed"}


def save_note_file(relative_path, content):
    path = _resolve_note_path(relative_path)
    if not _is_markdown_path(path):
        raise ValueError("Only markdown notes are supported")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content), encoding="utf-8")
    note = get_note_file(relative_path)
    index_status = reindex_note(relative_path)
    _mark_watch_indexed(note["path"])
    note["index_status"] = index_status
    return note


def move_note_file(from_path, to_path):
    source = _resolve_note_path(from_path)
    target = _resolve_note_path(to_path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError("Note not found")
    if not _is_markdown_path(source) or not _is_markdown_path(target):
        raise ValueError("Only markdown notes are supported")
    if source == target:
        raise ValueError("source and target are the same")
    if target.exists():
        raise ValueError("Target note already exists")
    old_rel = _safe_note_relative(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    try:
        qdrant_delete_path(old_rel)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    note = get_note_file(_safe_note_relative(target))
    note["index_status"] = reindex_note(note["path"])
    note["moved_from"] = old_rel
    _mark_watch_deleted(old_rel)
    _mark_watch_indexed(note["path"])
    return note


def delete_note_file(relative_path):
    path = _resolve_note_path(relative_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Note not found")
    if not _is_markdown_path(path):
        raise ValueError("Only markdown notes are supported")
    rel_path = _safe_note_relative(path)
    path.unlink()
    try:
        qdrant_delete_path(rel_path)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    _mark_watch_deleted(rel_path)
    return {"status": "deleted", "path": rel_path}


def _extract_chat_text(payload):
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        parts = []
        for item in payload:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item.get("text")))
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        if payload.get("content"):
            return _extract_chat_text(payload.get("content"))
        if payload.get("text"):
            return str(payload.get("text"))
    return str(payload)


def _workbench_messages(payload):
    raw_messages = payload.get("messages") or []
    messages = []
    context_bundle = normalize_text(payload.get("context_bundle") or "")[:WORKBENCH_CONTEXT_LIMIT]
    system_prompt = normalize_text(payload.get("system_prompt") or "")
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if context_bundle:
        messages.append(
            {
                "role": "system",
                "content": "Use this Mission context bundle as grounded working context. Prefer it over guesswork.\n\n" + context_bundle,
            }
        )
    for entry in raw_messages[-24:]:
        role = str((entry or {}).get("role") or "user").strip().lower()
        if role not in {"system", "user", "assistant"}:
            role = "user"
        content = normalize_text(_extract_chat_text((entry or {}).get("content")))
        if content:
            messages.append({"role": role, "content": content})
    return messages


def _workbench_max_tokens(payload):
    try:
        value = int(payload.get("max_tokens") or payload.get("maxTokens") or 256)
    except (TypeError, ValueError):
        value = 256
    return max(16, min(value, 2048))


def _openai_compat_path(api_base):
    parsed = urllib.parse.urlparse(api_base)
    base_path = parsed.path.rstrip("/")
    return f"{base_path}/chat/completions" if base_path.endswith("/v1") else f"{base_path}/v1/chat/completions"


def _vllm_models():
    data = service_json("http://localhost:8000", "/v1/models", timeout=15)
    models = []
    for entry in data.get("data") or []:
        model_id = str(entry.get("id") or "").strip()
        if model_id:
            models.append({"id": model_id, "label": model_id})
    return models


def _ollama_models():
    data = service_json(OLLAMA_BASE_URL, "/api/tags", timeout=15)
    models = []
    for entry in data.get("models") or []:
        model_name = str(entry.get("model") or entry.get("name") or "").strip()
        if model_name:
            models.append({"id": model_name, "label": model_name})
    return models


LOCAL_OPENAI_PROVIDERS = [
    {
        "id": "glm",
        "label": "GLM-4.7-Flash",
        "description": "Resident llama.cpp chat lane for the GLM service on the cluster.",
        "base_url": "http://localhost:8010",
    },
    {
        "id": "qwen35",
        "label": "Qwen3.5-35B-A3B",
        "description": "Resident llama.cpp chat lane for the Qwen3.5 service on the cluster.",
        "base_url": "http://localhost:8020",
    },
    {
        "id": "gptoss",
        "label": "GPT-OSS Heretic",
        "description": "Resident llama.cpp chat lane for the HERETIC GPT-OSS service on the cluster.",
        "base_url": "http://localhost:8030",
    },
]


def _openai_like_models(base_url):
    data = service_json(base_url, "/v1/models", timeout=15)
    raw_models = data.get("data") or data.get("models") or []
    models = []
    seen = set()
    for entry in raw_models:
        model_id = str(entry.get("id") or entry.get("model") or entry.get("name") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append({"id": model_id, "label": model_id})
    return models


def _local_openai_chat(provider, payload, messages, temperature):
    model = str(payload.get("model") or "").strip()
    max_tokens = _workbench_max_tokens(payload)
    if not model:
        models = _openai_like_models(provider["base_url"])
        if not models:
            raise RuntimeError(f"No models available for {provider['label']}")
        model = models[0]["id"]
    data = service_json(
        provider["base_url"],
        "/v1/chat/completions",
        timeout=180,
        method="POST",
        payload={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        },
    )
    choice = ((data.get("choices") or [{}])[0]).get("message") or {}
    return {
        "provider": provider["id"],
        "model": model,
        "message": {"role": "assistant", "content": _extract_chat_text(choice.get("content"))},
    }


def get_workbench_config():
    providers = []
    errors = []
    try:
        models = _vllm_models()
        if models:
            providers.append(
                {
                    "id": "vllm",
                    "label": "Radeon Cluster",
                    "description": "OpenAI-compatible local lane backed by the resident cluster model service.",
                    "models": models,
                }
            )
    except Exception as exc:
        errors.append(f"vllm: {exc}")

    try:
        models = _ollama_models()
        if models:
            providers.append(
                {
                    "id": "ollama",
                    "label": "Ollama Local",
                    "description": "Local chat lane through the resident Ollama service.",
                    "models": models,
                }
            )
    except Exception as exc:
        errors.append(f"ollama: {exc}")

    for provider in LOCAL_OPENAI_PROVIDERS:
        try:
            models = _openai_like_models(provider["base_url"])
            if models:
                providers.append(
                    {
                        "id": provider["id"],
                        "label": provider["label"],
                        "description": provider["description"],
                        "models": models,
                    }
                )
        except Exception as exc:
            errors.append(f"{provider['id']}: {exc}")

    providers.append(
        {
            "id": "openai_compat",
            "label": "Frontier / Custom",
            "description": "Any OpenAI-compatible endpoint such as OpenRouter, OpenAI, or another gateway.",
            "requires_api_base": True,
            "requires_api_key": True,
            "models": [],
        }
    )

    secrets = load_secrets()
    return {
        "available": bool(providers),
        "providers": providers,
        "frontier_key_configured": bool(secrets.get("CUSTOM_API_KEY")),
        "errors": errors,
        "watcher": get_watch_status(),
    }


def workbench_chat(payload):
    provider = str(payload.get("provider") or "vllm").strip().lower()
    model = str(payload.get("model") or "").strip()
    temperature = _sanitize_float(payload.get("temperature"), 0.4, 0.0, 2.0)
    max_tokens = _workbench_max_tokens(payload)
    messages = _workbench_messages(payload)
    if not messages:
        raise ValueError("At least one message is required")

    if provider == "vllm":
        if not model:
            models = _vllm_models()
            if not models:
                raise RuntimeError("No vLLM models available")
            model = models[0]["id"]
        data = service_json(
            "http://localhost:8000",
            "/v1/chat/completions",
            timeout=180,
            method="POST",
            payload={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        choice = ((data.get("choices") or [{}])[0]).get("message") or {}
        return {
            "provider": provider,
            "model": model,
            "message": {"role": "assistant", "content": _extract_chat_text(choice.get("content"))},
        }

    if provider == "ollama":
        if not model:
            models = _ollama_models()
            if not models:
                raise RuntimeError("No Ollama models available")
            model = models[0]["id"]
        data = service_json(
            OLLAMA_BASE_URL,
            "/api/chat",
            timeout=180,
            method="POST",
            payload={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        )
        message = data.get("message") or {}
        return {
            "provider": provider,
            "model": model,
            "message": {"role": "assistant", "content": _extract_chat_text(message.get("content"))},
        }

    local_provider = next((item for item in LOCAL_OPENAI_PROVIDERS if item["id"] == provider), None)
    if local_provider:
        return _local_openai_chat(local_provider, payload, messages, temperature)

    if provider == "openai_compat":
        api_base = str(payload.get("api_base") or "").strip().rstrip("/")
        api_key = str(payload.get("api_key") or "").strip() or str(load_secrets().get("CUSTOM_API_KEY") or "").strip()
        if not api_base or not api_key or not model:
            raise ValueError("api_base, api_key, and model are required for the frontier provider")
        parsed = urllib.parse.urlparse(api_base)
        target_url = f"{parsed.scheme}://{parsed.netloc}{_openai_compat_path(api_base)}"
        data = service_json_url(
            target_url,
            timeout=240,
            method="POST",
            payload={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        choice = ((data.get("choices") or [{}])[0]).get("message") or {}
        return {
            "provider": provider,
            "model": model,
            "message": {"role": "assistant", "content": _extract_chat_text(choice.get("content"))},
        }

    raise ValueError(f"Unsupported provider: {provider}")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def _read_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(content_length).decode())

    def _expected_origin(self):
        scheme = self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip()
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        host = host.split(",", 1)[0].strip()
        return f"{scheme}://{host}" if host else ""

    def _allow_mutation_request(self):
        if self.headers.get("X-Mission-Request") != "1":
            self._json_response({"error": "Forbidden"}, 403)
            return False

        origin = self.headers.get("Origin", "").strip()
        if not origin:
            self._json_response({"error": "Origin required"}, 403)
            return False

        parsed = urllib.parse.urlparse(origin)
        if f"{parsed.scheme}://{parsed.netloc}" != self._expected_origin():
            self._json_response({"error": "Cross-origin requests not allowed"}, 403)
            return False

        return True

    def do_GET(self):
        request_path = urllib.parse.urlparse(self.path).path

        if request_path == "/api/stats":
            gpus = get_gpu_stats()
            server = get_server_stats()
            data = {"gpus": gpus, **server}
            self._json_response(data)
        elif request_path == "/api/vllm":
            self._json_response(get_vllm_metrics())
        elif request_path == "/api/services":
            self._json_response(get_service_health())
        elif request_path == "/api/secrets":
            self._json_response(get_secrets_masked())
        elif request_path == "/api/models":
            self._json_response(get_model_status())
        elif request_path == "/api/agents":
            self._json_response(get_agent_status())
        elif request_path == "/api/commands":
            self._json_response(COMMAND_PALETTE)
        elif request_path == "/api/mission-auth":
            self._json_response(get_mission_auth_status())
        elif request_path == "/api/images":
            self._json_response(get_image_dashboard())
        elif request_path == "/api/images/config":
            self._json_response(get_image_config())
        elif request_path == "/api/av/config":
            self._json_response(get_av_config())
        elif request_path == "/api/av/job":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            prompt_id = (query.get("prompt_id") or [""])[0]
            try:
                self._json_response(get_av_job(prompt_id))
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
        elif request_path == "/api/workbench/config":
            self._json_response(get_workbench_config())
        elif request_path == "/api/workbench/bundles":
            self._json_response(list_workbench_bundles())
        elif request_path == "/api/workbench/projects":
            self._json_response(list_workbench_projects())
        elif request_path == "/api/images/job":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            prompt_id = (query.get("prompt_id") or [""])[0]
            self._json_response(get_image_job(prompt_id))
        elif request_path == "/api/images/view":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            filename = (query.get("filename") or [""])[0]
            image_type = (query.get("type") or ["output"])[0]
            subfolder = (query.get("subfolder") or [""])[0]
            if not filename:
                self._json_response({"error": "filename required"}, 400)
                return
            upstream_query = urllib.parse.urlencode(
                {"filename": filename, "subfolder": subfolder, "type": image_type}
            )
            try:
                _, content_type, data = comfyui_request(f"/view?{upstream_query}", timeout=60)
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "public, max-age=60")
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)
        elif request_path == "/api/knowledge":
            self._json_response(get_knowledge_dashboard())
        elif request_path == "/api/knowledge/search":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            text = (query.get("q") or [""])[0]
            limit = (query.get("limit") or [KNOWLEDGE_RESULT_LIMIT])[0]
            self._json_response(search_knowledge(text, limit=limit))
        elif request_path == "/api/notes/tree":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            path = (query.get("path") or [""])[0]
            try:
                self._json_response(get_notes_tree(path))
            except FileNotFoundError as exc:
                self._json_response({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
        elif request_path == "/api/notes/file":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            path = (query.get("path") or [""])[0]
            try:
                self._json_response(get_note_file(path))
            except FileNotFoundError as exc:
                self._json_response({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
        elif request_path in APP_ROUTES:
            self.path = "/index.html"
            super().do_GET()
        else:
            super().do_GET()

    def do_POST(self):
        if not self._allow_mutation_request():
            return

        if self.path == "/api/secrets":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()
            try:
                payload = json.loads(body)
                key = payload.get("key", "").strip()
                value = payload.get("value", "").strip()
                if not key:
                    self._json_response({"error": "Key is required"}, 400)
                    return

                secrets = load_secrets()
                if value:
                    secrets[key] = value
                else:
                    secrets.pop(key, None)
                save_secrets(secrets)

                # Sync relevant tokens to .env for docker-compose
                env_file = os.path.join(COMPOSE_DIR, ".env")
                env_tokens = ["DISCORD_TOKEN", "PROXIMA_TOKEN", "CLOUDFLARE_API_TOKEN"]
                env_lines = []
                if os.path.exists(env_file):
                    with open(env_file, "r") as f:
                        env_lines = [l for l in f.readlines()
                                     if not any(l.startswith(t + "=") for t in env_tokens)]
                for t in env_tokens:
                    if secrets.get(t):
                        env_lines.append(f"{t}={secrets[t]}\n")
                with open(env_file, "w") as f:
                    f.writelines(env_lines)
                os.chmod(env_file, stat.S_IRUSR | stat.S_IWUSR)

                self._json_response({"status": "saved", "secrets": get_secrets_masked()})
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        elif self.path == "/api/secrets/restart":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()
            try:
                payload = json.loads(body)
                service = payload.get("service", "")
                if service and restart_service(service):
                    self._json_response({"status": "restarting", "service": service})
                else:
                    self._json_response({"error": "Failed to restart"}, 500)
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        elif self.path == "/api/models/swap":
            try:
                payload = self._read_body()
                slot = payload.get("slot", "")
                model = payload.get("model", "")
                if not slot or not model:
                    self._json_response({"error": "slot and model required"}, 400)
                    return
                result = handle_model_swap(slot, model)
                self._json_response(result)
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        elif self.path == "/api/models/stop":
            try:
                payload = self._read_body()
                slot = payload.get("slot", "")
                if not slot:
                    self._json_response({"error": "slot required"}, 400)
                    return
                result = handle_model_stop(slot)
                self._json_response(result)
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        elif self.path == "/api/models/start":
            try:
                payload = self._read_body()
                slot = payload.get("slot", "")
                if not slot:
                    self._json_response({"error": "slot required"}, 400)
                    return
                result = handle_model_start(slot)
                self._json_response(result)
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        elif self.path == "/api/agents/update":
            try:
                payload = self._read_body()
                agent = payload.get("agent", "").strip()
                key = payload.get("key", "").strip()
                value = payload.get("value", "").strip()
                if not agent or not key:
                    self._json_response({"error": "agent and key required"}, 400)
                    return
                result = handle_agent_update(agent, key, value)
                self._json_response(result)
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        elif self.path == "/api/terminal":
            try:
                payload = self._read_body()
                cmd = payload.get("command", "").strip()
                if not cmd:
                    self._json_response({"error": "command required"}, 400)
                    return
                result = execute_terminal_command(cmd)
                self._json_response(result)
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        elif self.path == "/api/mission-auth":
            try:
                payload = self._read_body()
                username = payload.get("username", "").strip()
                password = payload.get("password", "").strip()
                if not username or not password:
                    self._json_response({"error": "username and password required"}, 400)
                    return
                result = update_mission_auth(username, password)
                status_code = 200 if result["status"] == "ok" else 400
                self._json_response(result, status_code)
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        elif self.path == "/api/images/generate":
            try:
                payload = self._read_body()
                result = submit_image_generation(payload)
                self._json_response(result)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/images/release":
            try:
                payload = self._read_body()
                self._json_response(
                    release_image_node(
                        reason=payload.get("reason") or "manual",
                        unload_models=payload.get("unload_models", True),
                        free_memory=payload.get("free_memory", True),
                    )
                )
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/images/interrupt":
            try:
                self._json_response(interrupt_image_node())
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/images/source-url":
            try:
                payload = self._read_body()
                url = payload.get("url", "")
                self._json_response(_fetch_remote_image(url))
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/av/tts":
            try:
                payload = self._read_body()
                self._json_response(synthesize_av_speech(payload))
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/av/transcribe":
            try:
                payload = self._read_body()
                self._json_response(transcribe_av_media(payload))
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/av/clip":
            try:
                payload = self._read_body()
                self._json_response(submit_av_clip(payload))
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/workbench/chat":
            try:
                payload = self._read_body()
                self._json_response(workbench_chat(payload))
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/workbench/bundles":
            try:
                payload = self._read_body()
                self._json_response(save_workbench_bundle(payload))
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/workbench/projects":
            try:
                payload = self._read_body()
                self._json_response(save_workbench_project(payload))
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/notes/file":
            try:
                payload = self._read_body()
                path = payload.get("path", "")
                content = payload.get("content", "")
                self._json_response(save_note_file(path, content))
            except FileNotFoundError as exc:
                self._json_response({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/notes/move":
            try:
                payload = self._read_body()
                from_path = payload.get("from_path", "")
                to_path = payload.get("to_path", "")
                self._json_response(move_note_file(from_path, to_path))
            except FileNotFoundError as exc:
                self._json_response({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        elif self.path == "/api/notes/delete":
            try:
                payload = self._read_body()
                path = payload.get("path", "")
                self._json_response(delete_note_file(path))
            except FileNotFoundError as exc:
                self._json_response({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_DELETE(self):
        if not self._allow_mutation_request():
            return
        if self.path.startswith("/api/workbench/bundles"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            bundle_id = (query.get("id") or [""])[0]
            try:
                self._json_response(delete_workbench_bundle(bundle_id))
            except FileNotFoundError as exc:
                self._json_response({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)
            return
        if self.path.startswith("/api/workbench/projects"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            project_id = (query.get("id") or [""])[0]
            try:
                self._json_response(delete_workbench_project(project_id))
            except FileNotFoundError as exc:
                self._json_response({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, 400)
            except Exception as exc:
                self._json_response({"error": str(exc)}, 502)
            return
        self.send_response(404)
        self.end_headers()

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    _ensure_mission_data_dir()
    bmc_proxy = http.server.ThreadingHTTPServer(("0.0.0.0", BMC_PROXY_PORT), BMCProxyHandler)
    threading.Thread(target=bmc_proxy.serve_forever, daemon=True).start()
    threading.Thread(target=_vault_watch_loop, daemon=True).start()
    threading.Thread(target=_image_node_watch_loop, daemon=True).start()
    print(f"BMC proxy running on http://0.0.0.0:{BMC_PROXY_PORT} -> https://{BMC_HOST}/")
    print(f"Mission Control running on http://0.0.0.0:{PORT}")
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
