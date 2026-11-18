"""Nextcloud pairing + config for uploading recognized pieces (image + part/
color), so they can later train a custom model from them. The actual upload
worker is server/nextcloud_sync.py; this router only handles the web UI's
settings and the login handshake.

Login uses Nextcloud's "Login Flow v2": no OAuth2 client needs registering
in Nextcloud beforehand, and no fixed redirect URI, so it keeps working the
same whether the browser reaches this machine over LAN or Tailscale. A POST
to /index.php/login/v2 hands back a one-time login URL plus a poll token;
the user approves the login there; a background thread here polls until
Nextcloud hands back a username and app password, which authenticate every
later WebDAV request (see nextcloud_sync.py).
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import local_state
from server.nextcloud_sync import DEFAULT_REMOTE_ROOT, getNextcloudSyncer

router = APIRouter()

_CONNECT_TIMEOUT_S = 600.0
_POLL_INTERVAL_S = 1.5

_connect_lock = threading.Lock()
_connect_state: dict[str, Any] = {"state": "idle"}


class ServerUrlPayload(BaseModel):
    server_url: str


@router.post("/api/nextcloud/connect/start")
def start_connect(payload: ServerUrlPayload) -> dict[str, Any]:
    server_url = payload.server_url.strip().rstrip("/")
    if not server_url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "server_url must start with http:// or https://")
    try:
        resp = requests.post(f"{server_url}/index.php/login/v2", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        login_url = data["login"]
        poll_endpoint = data["poll"]["endpoint"]
        poll_token = data["poll"]["token"]
    except Exception as exc:
        raise HTTPException(502, f"Could not start the Nextcloud login: {exc}") from exc

    with _connect_lock:
        _connect_state.clear()
        _connect_state.update(state="pending", server_url=server_url)
    threading.Thread(
        target=_pollForLogin, args=(server_url, poll_endpoint, poll_token),
        daemon=True, name="nextcloud-connect",
    ).start()
    return {"login_url": login_url}


def _pollForLogin(server_url: str, poll_endpoint: str, poll_token: str) -> None:
    deadline = time.monotonic() + _CONNECT_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_S)
        with _connect_lock:
            if _connect_state.get("state") != "pending":
                return  # superseded by a newer connect attempt
        try:
            resp = requests.post(poll_endpoint, data={"token": poll_token}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue  # 404 while the user hasn't approved it yet
        data = resp.json()
        login_name, app_password = data.get("loginName"), data.get("appPassword")
        if not login_name or not app_password:
            continue
        config = local_state.get_nextcloud_config() or {}
        config.update(
            server_url=data.get("server") or server_url,
            login_name=login_name,
            app_password=app_password,
            needs_reconnect=False,
        )
        config.setdefault("remote_root", DEFAULT_REMOTE_ROOT)
        config.setdefault("enabled", True)
        config.setdefault("upload_unidentified", True)
        config.setdefault("last_synced_seq", 0)
        local_state.set_nextcloud_config(config)
        with _connect_lock:
            _connect_state.update(state="connected", login_name=login_name)
        getNextcloudSyncer().reload()
        return
    with _connect_lock:
        if _connect_state.get("state") == "pending":
            _connect_state.update(state="timed_out")


@router.get("/api/nextcloud/connect/status")
def connect_status() -> dict[str, Any]:
    with _connect_lock:
        return dict(_connect_state)


class NextcloudConfigPayload(BaseModel):
    server_url: str | None = None
    remote_root: str | None = None
    min_confidence: float | None = None
    upload_unidentified: bool | None = None
    enabled: bool | None = None


@router.get("/api/nextcloud/config")
def get_config() -> dict[str, Any]:
    config = local_state.get_nextcloud_config() or {}
    return {
        "enabled": bool(config.get("enabled")),
        "server_url": config.get("server_url"),
        "remote_root": config.get("remote_root") or DEFAULT_REMOTE_ROOT,
        "min_confidence": config.get("min_confidence"),
        "upload_unidentified": config.get("upload_unidentified", True),
        "login_name": config.get("login_name"),
        "connected": bool(config.get("login_name") and config.get("app_password")),
        "needs_reconnect": bool(config.get("needs_reconnect")),
    }


@router.post("/api/nextcloud/config")
def save_config(payload: NextcloudConfigPayload) -> dict[str, Any]:
    config = local_state.get_nextcloud_config() or {}
    if payload.server_url is not None:
        config["server_url"] = payload.server_url.strip().rstrip("/")
    if payload.remote_root is not None:
        config["remote_root"] = payload.remote_root.strip() or DEFAULT_REMOTE_ROOT
    if payload.min_confidence is not None:
        config["min_confidence"] = payload.min_confidence
    if payload.upload_unidentified is not None:
        config["upload_unidentified"] = payload.upload_unidentified
    if payload.enabled is not None:
        config["enabled"] = payload.enabled
    local_state.set_nextcloud_config(config)
    getNextcloudSyncer().reload()
    return {"ok": True}


@router.post("/api/nextcloud/disconnect")
def disconnect() -> dict[str, Any]:
    config = local_state.get_nextcloud_config() or {}
    config.update(login_name=None, app_password=None, needs_reconnect=False, enabled=False)
    local_state.set_nextcloud_config(config)
    getNextcloudSyncer().reload()
    return {"ok": True}


@router.get("/api/nextcloud/status")
def status() -> dict[str, Any]:
    return getNextcloudSyncer().status()
