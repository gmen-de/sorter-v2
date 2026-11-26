"""The StudSphere settings page: the connection's settings, its status, a
connection test and a resync. studsphere_sync.py does the work."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import local_state
import studsphere_sync

router = APIRouter()


class StudSphereConfigPayload(BaseModel):
    enabled: bool | None = None
    url: str | None = None
    # Blank keeps the saved key; the page never gets the key back.
    api_key: str | None = None
    condition: str | None = None


def _publicConfig(config: dict[str, Any]) -> dict[str, Any]:
    key = config.get("api_key") or ""
    return {
        "enabled": bool(config.get("enabled")),
        "url": config.get("url") or "",
        "has_api_key": bool(key),
        "api_key_hint": f"{key[:8]}…{key[-4:]}" if len(key) > 12 else None,
        "condition": config.get("condition") or "used",
    }


@router.get("/api/studsphere")
def get_studsphere() -> dict[str, Any]:
    config = local_state.get_studsphere_config()
    return {"config": _publicConfig(config), "status": studsphere_sync.getStudSphereSyncer().status()}


@router.post("/api/studsphere/config")
def save_studsphere(payload: StudSphereConfigPayload) -> dict[str, Any]:
    config = local_state.get_studsphere_config()
    before = (bool(config.get("enabled")), config.get("url") or "", config.get("api_key") or "")
    if payload.url is not None:
        try:
            config["url"] = studsphere_sync.normalizeUrl(payload.url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if payload.api_key is not None and payload.api_key.strip():
        config["api_key"] = payload.api_key.strip()
    if payload.condition is not None:
        if payload.condition not in studsphere_sync.CONDITIONS:
            raise HTTPException(400, "Condition must be used or new.")
        config["condition"] = payload.condition
    if payload.enabled is not None:
        config["enabled"] = payload.enabled
    if config.get("enabled") and not (config.get("url") and config.get("api_key")):
        raise HTTPException(400, "Enter the StudSphere address and the API key before turning it on.")
    local_state.set_studsphere_config(config)

    syncer = studsphere_sync.getStudSphereSyncer()
    after = (bool(config.get("enabled")), config.get("url") or "", config.get("api_key") or "")
    if not after[0]:
        syncer.disable()
    elif after != before:
        # Turned on, or pointed somewhere else: start over from the bins'
        # current contents.
        syncer.resync()
    return get_studsphere()


@router.post("/api/studsphere/test")
def test_studsphere() -> dict[str, Any]:
    config = local_state.get_studsphere_config()
    if not (config.get("url") and config.get("api_key")):
        raise HTTPException(400, "Save the StudSphere address and the API key first.")
    try:
        return studsphere_sync.getStudSphereSyncer().hello(config)
    except studsphere_sync.StudSphereError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/api/studsphere/resync")
def resync_studsphere() -> dict[str, Any]:
    config = local_state.get_studsphere_config()
    if not config.get("enabled"):
        raise HTTPException(400, "Turn the StudSphere connection on first.")
    studsphere_sync.getStudSphereSyncer().resync()
    return get_studsphere()
