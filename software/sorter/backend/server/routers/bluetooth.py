"""Bluetooth pairing endpoints (BlueZ / bluetoothctl).

Pairing the SC15-Bridge ESP32 (see software/firmware/sc15_bt_bridge and
hardware/bt_servo_bus.py) was previously a manual bluetoothctl session (see
that firmware's README) - this wraps the same CLI so the setup/settings page
can do it with a button instead. Mirrors wifi.py's shape (ok/error dicts,
not exceptions) since both are thin wrappers around a system CLI that can
simply fail. Not yet exercised against a real bridge or a real BlueZ
adapter.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

BRIDGE_NAME_PREFIX = "SC15-Bridge"
_SCAN_SECONDS = 8
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def _have_bluetoothctl() -> bool:
    return bool(shutil.which("bluetoothctl"))


def _run(*args: str, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bluetoothctl", *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _valid_address(address: str) -> bool:
    return bool(_MAC_RE.match(address))


def _parse_devices(listing: str) -> Dict[str, str]:
    """"Device AA:BB:CC:DD:EE:FF Some Name" lines -> {address: name}."""
    devices: Dict[str, str] = {}
    for line in listing.splitlines():
        m = re.match(r"Device ([0-9A-Fa-f:]{17}) (.+)$", line.strip())
        if m:
            devices[m.group(1).upper()] = m.group(2).strip()
    return devices


def _device_status(address: str) -> Dict[str, bool]:
    proc = _run("info", address, timeout=10.0)
    info = proc.stdout + proc.stderr
    return {
        "paired": "Paired: yes" in info,
        "trusted": "Trusted: yes" in info,
        "connected": "Connected: yes" in info,
    }


@router.get("/api/bluetooth/status")
def bluetooth_status() -> Dict[str, Any]:
    if not _have_bluetoothctl():
        return {"available": False, "devices": [], "error": "bluetoothctl not found"}
    try:
        listing = _run("devices", timeout=10.0).stdout
    except subprocess.TimeoutExpired:
        return {"available": True, "devices": [], "error": "listing known devices timed out"}
    devices = [
        {"address": address, "name": name, **_device_status(address)}
        for address, name in _parse_devices(listing).items()
    ]
    devices.sort(key=lambda d: (not d["name"].startswith(BRIDGE_NAME_PREFIX), d["name"]))
    return {"available": True, "devices": devices}


@router.post("/api/bluetooth/scan")
def bluetooth_scan() -> Dict[str, Any]:
    """Power the adapter on, scan for a few seconds, and report every
    Bluetooth Classic device seen - SC15-Bridge devices first, since the
    bridge's name is fixed (DEVICE_NAME in the firmware), but every device
    is listed in case one was renamed."""
    if not _have_bluetoothctl():
        return {"available": False, "devices": [], "error": "bluetoothctl not found"}
    try:
        _run("power", "on", timeout=10.0)
        _run("--timeout", str(_SCAN_SECONDS), "scan", "on", timeout=_SCAN_SECONDS + 5.0)
        listing = _run("devices", timeout=10.0).stdout
    except subprocess.TimeoutExpired:
        return {"available": True, "devices": [], "error": "scan timed out"}
    devices = [
        {"address": address, "name": name, **_device_status(address)}
        for address, name in _parse_devices(listing).items()
    ]
    devices.sort(key=lambda d: (not d["name"].startswith(BRIDGE_NAME_PREFIX), d["name"]))
    return {"available": True, "devices": devices}


class AddressPayload(BaseModel):
    address: str


@router.post("/api/bluetooth/pair")
def bluetooth_pair(payload: AddressPayload) -> Dict[str, Any]:
    """Pair and trust a bridge so the backend can reconnect to it without
    asking again - safe to call on an already-paired device."""
    if not _have_bluetoothctl():
        return {"ok": False, "error": "bluetoothctl not found"}
    address = payload.address.strip().upper()
    if not _valid_address(address):
        return {"ok": False, "error": f"{payload.address!r} is not a Bluetooth address (AA:BB:CC:DD:EE:FF)"}
    try:
        proc = _run("pair", address, timeout=20.0)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Pairing timed out"}
    pair_out = proc.stdout + proc.stderr
    already_paired = "already" in pair_out.lower() and "paired" in pair_out.lower()
    if "Failed" in pair_out and not already_paired:
        last_line = pair_out.strip().splitlines()[-1] if pair_out.strip() else "no response"
        return {"ok": False, "error": f"Pairing failed: {last_line}"}
    _run("trust", address, timeout=10.0)
    return {"ok": True, "address": address, **_device_status(address)}


@router.post("/api/bluetooth/remove")
def bluetooth_remove(payload: AddressPayload) -> Dict[str, Any]:
    """Forget a paired bridge (e.g. to re-pair after a factory reset)."""
    if not _have_bluetoothctl():
        return {"ok": False, "error": "bluetoothctl not found"}
    address = payload.address.strip().upper()
    if not _valid_address(address):
        return {"ok": False, "error": f"{payload.address!r} is not a Bluetooth address (AA:BB:CC:DD:EE:FF)"}
    try:
        _run("remove", address, timeout=10.0)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Removing the device timed out"}
    return {"ok": True, "address": address}
