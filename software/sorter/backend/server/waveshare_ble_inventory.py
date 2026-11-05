"""Status/control helpers for the esp32_ble_servo_bridge backend, used by
server/routers/servos.py. Much simpler than waveshare_inventory.py (the
wired bus's background port-scanner): there is exactly one configured BLE
device, the firmware does its own ID bookkeeping, and the bridge already
keeps a persistent connection open once the machine is running — so this
module only ever either reuses that live connection or opens a short-lived
one for a one-off setup-UI action."""

from __future__ import annotations

from typing import Any

import machine_toml
from hardware.waveshare_ble_bridge import WaveshareBleBridge, get_waveshare_ble_bridge
from server import shared_state


def _normalize_device(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _active_waveshare_ble_bridge() -> WaveshareBleBridge | None:
    active_irl = shared_state.getActiveIRL()
    if active_irl is None:
        return None
    servo_controller = getattr(active_irl, "servo_controller", None)
    return getattr(servo_controller, "bridge", None)


def _configured_waveshare_ble_device() -> str | None:
    config = machine_toml.read()
    servo = config.get("servo", {})
    if not isinstance(servo, dict):
        return None
    return _normalize_device(servo.get("device"))


def get_waveshare_ble_bridge_for_api(device: str | None = None) -> WaveshareBleBridge:
    """The running machine's own bridge if there is one (reused as-is, never
    reconnected here — the machine owns its lifecycle), otherwise a
    short-lived connection to `device` (or the configured one) for a setup
    action taken while the machine isn't running."""
    live = _active_waveshare_ble_bridge()
    if live is not None:
        return live

    target = _normalize_device(device) or _configured_waveshare_ble_device()
    if target is None:
        raise ValueError("No Waveshare BLE device configured (servo.device in machine.toml).")

    bridge = get_waveshare_ble_bridge(target)
    bridge.connect()
    return bridge


def get_status(device: str | None = None) -> dict[str, Any]:
    is_live = _active_waveshare_ble_bridge() is not None
    configured_device = _configured_waveshare_ble_device()
    target = _normalize_device(device) or configured_device

    try:
        bridge = get_waveshare_ble_bridge_for_api(device)
    except Exception as exc:
        return {
            "ok": True,
            "connected": False,
            "device": target,
            "live": is_live,
            "gates": [],
            "last_error": str(exc),
        }

    try:
        gates = bridge.refresh_known_gates()
        last_error = None
    except Exception as exc:
        gates = bridge.known_gates
        last_error = str(exc)

    return {
        "ok": True,
        "connected": bridge.connected,
        "device": bridge.address,
        "live": is_live,
        "gates": gates,
        "last_error": last_error,
    }
