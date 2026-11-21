"""Layer servos, on PCA9685 channels or the Waveshare BLE gate bridge: settings, speeds, calibration and moves."""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import machine_toml
from hardware_settings.servos import SPEEDS, ServoSpeeds, applyServoSpeeds, readServoSpeeds, saveServoSpeeds
from bin_layout_store import set_servo_channel_angle
from irl.bin_layout import channelIdForLayer, getBinLayout
from server import shared_state
from server.routers.steppers import _ensure_not_homing
from hardware import esp32_flash
from hardware.waveshare_ble_bridge import scan_devices as _scan_waveshare_ble_devices
from server.waveshare_ble_inventory import (
    _active_waveshare_ble_bridge,
    get_status as _get_waveshare_ble_status,
    get_waveshare_ble_bridge_for_api,
)

_SERVO_BACKENDS = {"pca9685", "waveshare_ble"}

router = APIRouter()

_PCA9685_CHANNEL_COUNT = 16


class ServoChannelConfigPayload(BaseModel):
    id: int | None = None
    invert: bool = False


class ServoHardwareSettingsPayload(BaseModel):
    backend: str = "pca9685"
    open_speed: Optional[int] = None
    close_speed: Optional[int] = None
    homing_speed: Optional[int] = None
    device: Optional[str] = None  # BLE address, for backend == "waveshare_ble"
    channels: List[ServoChannelConfigPayload] = []


class ServoNudgePayload(BaseModel):
    degrees: int


class ServoLayerMovePayload(BaseModel):
    angle: int


class ServoLayerGatePayload(BaseModel):
    state: str  # "open" | "close"


class ServoLayerLockPayload(BaseModel):
    which: str  # "open" | "closed"
    angle: Optional[int] = None


class ServoLayerClearPayload(BaseModel):
    which: str = "both"  # "open" | "closed" | "both"


def _distribution_layer_count() -> int:
    active_irl = shared_state.getActiveIRL()
    if active_irl is not None:
        layout = getattr(active_irl, "distribution_layout", None)
        if layout is not None and hasattr(layout, "layers"):
            return len(layout.layers)
    return len(getBinLayout().layers)


def _pca_available_servo_channels() -> List[int]:
    active_irl = shared_state.getActiveIRL()
    if active_irl is not None:
        interfaces = getattr(active_irl, "interfaces", {})
        channels: set[int] = set()
        if isinstance(interfaces, dict):
            for interface in interfaces.values():
                for servo in getattr(interface, "servos", []):
                    channel = getattr(servo, "channel", None)
                    if isinstance(channel, int) and not isinstance(channel, bool):
                        channels.add(channel)
        if channels:
            return sorted(channels)
    return list(range(_PCA9685_CHANNEL_COUNT))


def _waveshare_ble_available_gate_ids(config: Dict[str, Any]) -> List[int]:
    """Gate numbers to suggest in the setup UI: configured ones, plus
    whatever the bridge most recently reported reachable. Never opens a new
    BLE connection just to answer this — only uses what's already cached."""
    found_ids: set[int] = set()

    servo = config.get("servo", {})
    if isinstance(servo, dict):
        channels_raw = servo.get("channels", [])
        if isinstance(channels_raw, list):
            for item in channels_raw:
                if not isinstance(item, dict):
                    continue
                channel_id = item.get("id")
                if isinstance(channel_id, int) and not isinstance(channel_id, bool) and channel_id > 0:
                    found_ids.add(channel_id)

    bridge = _active_waveshare_ble_bridge()
    if bridge is not None:
        found_ids.update(bridge.known_gates)

    return sorted(found_ids)


def _live_servo_for_layer(layer_index: int) -> Any:
    active_irl = shared_state.getActiveIRL()
    if active_irl is None:
        raise HTTPException(status_code=503, detail="Servo controller not initialized.")

    servos = list(getattr(active_irl, "servos", []))
    if layer_index < 0:
        raise HTTPException(status_code=404, detail=f"Unknown storage layer {layer_index + 1}.")
    if layer_index >= len(servos):
        # The live servos are built when the machine homes, so a layer added
        # since then has none yet.
        raise HTTPException(
            status_code=409,
            detail=f"Layer {layer_index + 1} has no servo running yet. Save the layers, then restart the backend.",
        )
    return servos[layer_index]


def _live_servo_feedback_for_layer(layer_index: int, servo: Any | None = None) -> Dict[str, Any]:
    servo = servo if servo is not None else _live_servo_for_layer(layer_index)
    channel = getattr(servo, "channel", None)
    base: Dict[str, Any] = {
        "layer_index": layer_index,
        "channel": channel,
        "available": False,
    }

    if servo is None:
        return base

    if hasattr(servo, "feedback"):
        try:
            feedback = servo.feedback()
            if isinstance(feedback, dict):
                return {
                    "layer_index": layer_index,
                    **feedback,
                }
        except Exception as e:
            return {**base, "error": str(e)}

    if not hasattr(servo, "position"):
        return base

    try:
        position = int(servo.position)
        result = {
            **base,
            "available": True,
            "position": position,
            "angle": position // 10,
            "is_open": bool(servo.isOpen()) if hasattr(servo, "isOpen") else None,
        }
        if hasattr(servo, "is_calibrated"):
            result.update(_servo_calibration_state(servo))
        return result
    except Exception as e:
        return {**base, "error": str(e)}


def _apply_standard_speed(servo: Any) -> None:
    # Jogs run at the standard speed, which the servo was given at start-up and
    # again whenever the speeds are saved (hardware_settings.servos).
    if hasattr(servo, "apply_homing_speed"):
        servo.apply_homing_speed()


def _apply_speeds_live(speeds: ServoSpeeds) -> bool:
    """Give the running servos their saved speeds. False when there are none
    running or one did not answer; they get them at the next start anyway."""
    active_irl = shared_state.getActiveIRL()
    servos = list(getattr(active_irl, "servos", [])) if active_irl is not None else []
    try:
        for servo in servos:
            applyServoSpeeds(servo, speeds)
    except Exception:
        return False
    return bool(servos)


def _servo_hardware_issues() -> List[Dict[str, Any]]:
    active_irl = shared_state.getActiveIRL()
    if active_irl is None:
        return []
    servo_controller = getattr(active_irl, "servo_controller", None)
    issues = getattr(servo_controller, "issues", None)
    if not isinstance(issues, list):
        return []
    return [issue for issue in issues if isinstance(issue, dict)]


def _servo_settings_from_config(config: Dict[str, Any]) -> Dict[str, Any]:
    servo = config.get("servo", {})
    if not isinstance(servo, dict):
        servo = {}

    backend = servo.get("backend", "pca9685")
    if backend not in _SERVO_BACKENDS:
        backend = "pca9685"

    device = servo.get("device")
    if device is not None and not isinstance(device, str):
        device = None

    layer_count = _distribution_layer_count()
    parsed_channels: List[Dict[str, Any]] = []
    channels_raw = servo.get("channels", [])
    if isinstance(channels_raw, list):
        for item in channels_raw:
            if not isinstance(item, dict):
                continue
            channel_id = item.get("id")
            if channel_id is None:
                parsed_channels.append({"id": None, "invert": bool(item.get("invert", False))})
                continue
            if not isinstance(channel_id, int) or isinstance(channel_id, bool):
                parsed_channels.append({"id": None, "invert": bool(item.get("invert", False))})
                continue
            parsed_channels.append(
                {
                    "id": channel_id,
                    "invert": bool(item.get("invert", False)),
                }
            )

    channels: List[Dict[str, Any]] = []
    for index in range(layer_count):
        existing = parsed_channels[index] if index < len(parsed_channels) else None
        default_id = index + 1 if backend == "waveshare_ble" else index
        channels.append(
            {
                "id": (int(existing["id"]) if existing is not None and existing["id"] is not None else None)
                if existing is not None
                else default_id,
                "invert": bool(existing["invert"]) if existing is not None else False,
            }
        )

    if backend == "pca9685":
        available_channel_ids = _pca_available_servo_channels()
    else:
        available_channel_ids = _waveshare_ble_available_gate_ids(config)

    return {
        "backend": backend,
        **asdict(readServoSpeeds(config)),
        "device": device.strip() if isinstance(device, str) and device.strip() else None,
        "channels": channels,
        "layer_count": layer_count,
        "available_channel_ids": available_channel_ids,
        "supports_calibration": backend == "waveshare_ble",
        "issues": _servo_hardware_issues(),
    }


@router.get("/api/hardware-config/servo")
def get_servo_hardware_config() -> Dict[str, Any]:
    return _servo_settings_from_config(machine_toml.read())


@router.post("/api/hardware-config/servo")
def save_servo_hardware_config(
    payload: ServoHardwareSettingsPayload,
) -> Dict[str, Any]:
    backend = payload.backend if payload.backend in _SERVO_BACKENDS else "pca9685"
    # Speeds the request leaves out stay as saved (the setup wizard sends none).
    speed_changes = {key: getattr(payload, key) for key in SPEEDS if key in payload.model_fields_set}
    device = payload.device.strip() if isinstance(payload.device, str) and payload.device.strip() else None
    available_pca_channels = _pca_available_servo_channels()
    # Validate against the persisted layout we actually index into below, not the
    # live distribution layout. After a layer add/remove the storage-layers save
    # has already rewritten the config, but the running hardware still carries the
    # old layer count until a restart — trusting it here rejected valid saves.
    current_layers = getBinLayout().layers
    layer_count = len(current_layers)

    if len(payload.channels) != layer_count:
        raise HTTPException(
            status_code=400,
            detail=f"Expected {layer_count} layer servo assignments, got {len(payload.channels)}.",
        )

    channels: List[Dict[str, Any]] = []
    seen_ids: set[int] = set()
    for index, channel in enumerate(payload.channels):
        channel_id = int(channel.id) if channel.id is not None else None
        layer_enabled = bool(getattr(current_layers[index], "enabled", True))

        if channel_id is None:
            if layer_enabled:
                raise HTTPException(
                    status_code=400,
                    detail=f"Layer {index + 1} needs a servo assignment while the layer is active.",
                )
            channels.append({"id": None, "invert": bool(channel.invert)})
            continue

        if backend == "waveshare_ble":
            valid = 1 <= channel_id <= 60
            help_text = "a gate number between 1 and 60"
        else:
            valid = channel_id >= 0 and (
                not available_pca_channels or channel_id in available_pca_channels
            )
            help_text = "a valid PCA servo channel"

        if not valid:
            raise HTTPException(
                status_code=400,
                detail=f"Layer {index + 1} needs {help_text}.",
            )
        if channel_id in seen_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Servo assignment {channel_id} is used more than once.",
            )
        seen_ids.add(channel_id)
        channels.append({"id": channel_id, "invert": bool(channel.invert)})

    if backend == "waveshare_ble" and device is None:
        raise HTTPException(
            status_code=400,
            detail="The Waveshare BLE backend needs the bridge board's BLE address.",
        )

    with machine_toml.edit() as config:
        previous = _servo_settings_from_config(config)

        servo_table = config.get("servo")
        if not isinstance(servo_table, dict):
            servo_table = {}
        servo_table["backend"] = backend
        servo_table["channels"] = channels
        servo_table.pop("port", None)
        if backend == "waveshare_ble" and device is not None:
            servo_table["device"] = device
        else:
            servo_table.pop("device", None)
        servo_table.pop("highest_seen_id", None)
        config["servo"] = servo_table

    try:
        speeds = saveServoSpeeds(speed_changes) if speed_changes else readServoSpeeds()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    previous_ids = [int(channel["id"]) if channel["id"] is not None else None for channel in previous["channels"]]
    channel_ids = [int(channel["id"]) if channel["id"] is not None else None for channel in channels]
    channel_inverts = [bool(channel["invert"]) for channel in channels]

    structural_change = (
        backend != previous["backend"]
        or channel_ids != previous_ids
        or (backend == "waveshare_ble" and device != previous["device"])
    )

    active_irl = shared_state.getActiveIRL()
    has_live_hardware = active_irl is not None

    applied_live = False
    if not structural_change and has_live_hardware:
        try:
            live_servos = list(getattr(active_irl, "servos", [])) if active_irl is not None else []
            if len(live_servos) == len(channels):
                for index, servo in enumerate(live_servos):
                    invert = channel_inverts[index]
                    if backend == "waveshare_ble":
                        if hasattr(servo, "set_invert"):
                            servo.set_invert(invert)
                    else:
                        # PCA open/closed angles are calibrated per-layer via the
                        # Servo Layer Calibrator, not here — only apply speed.
                        applyServoSpeeds(servo, speeds)
                applied_live = True
        except Exception:
            applied_live = False

    restart_required = structural_change and has_live_hardware

    if applied_live:
        message = "Servo settings saved and applied live."
    elif structural_change and has_live_hardware:
        message = "Servo settings saved. Re-home hardware to apply changes."
    else:
        message = "Servo settings saved."

    return {
        "ok": True,
        "settings": _servo_settings_from_config(config),
        "applied_live": applied_live,
        "restart_required": restart_required,
        "message": message,
    }


class ServoSpeedSettingsPayload(BaseModel):
    open_speed: Optional[int] = None
    close_speed: Optional[int] = None
    homing_speed: Optional[int] = None


@router.post("/api/hardware-config/servo/speeds")
def save_servo_speeds(payload: ServoSpeedSettingsPayload) -> Dict[str, Any]:
    """Save the speeds sent (null goes back to the firmware default; one left
    out stays as saved) and give them to the running servos."""
    changes = {key: getattr(payload, key) for key in SPEEDS if key in payload.model_fields_set}
    try:
        speeds = saveServoSpeeds(changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    applied_live = _apply_speeds_live(speeds)
    return {"ok": True, **asdict(speeds), "applied_live": applied_live}


@router.post("/api/hardware-config/servo/layers/{layer_index}/nudge")
def nudge_layer_servo(layer_index: int, payload: ServoNudgePayload) -> Dict[str, Any]:
    _ensure_not_homing("nudge a servo")
    servo = _live_servo_for_layer(layer_index)

    if not hasattr(servo, "move_to") or not hasattr(servo, "position"):
        raise HTTPException(status_code=500, detail="Servo does not support position-based movement.")

    try:
        _apply_standard_speed(servo)

        current_pos = int(servo.position)
        # PCA ServoMotor.position returns tenths-of-degrees, move_to takes degrees 0-180.
        current_angle = current_pos // 10

        new_angle = max(0, min(180, current_angle + payload.degrees))
        servo.move_to(new_angle)
        feedback = _live_servo_feedback_for_layer(layer_index, servo)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to nudge layer {layer_index + 1} servo: {e}")

    return {
        "ok": True,
        "layer_index": layer_index,
        "degrees": payload.degrees,
        "new_angle": new_angle,
        "feedback": feedback,
    }


@router.post("/api/hardware-config/servo/layers/{layer_index}/move-to")
def move_to_layer_servo(layer_index: int, payload: ServoLayerMovePayload) -> Dict[str, Any]:
    _ensure_not_homing("move a servo")
    servo = _live_servo_for_layer(layer_index)
    if not hasattr(servo, "move_to"):
        raise HTTPException(status_code=500, detail="Servo does not support position-based movement.")

    angle = max(0, min(180, int(payload.angle)))
    try:
        _apply_standard_speed(servo)
        servo.move_to(angle)
        feedback = _live_servo_feedback_for_layer(layer_index, servo)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move layer {layer_index + 1} servo: {e}")

    return {
        "ok": True,
        "layer_index": layer_index,
        "new_angle": angle,
        "feedback": feedback,
    }


@router.post("/api/hardware-config/servo/layers/{layer_index}/set-gate")
def set_layer_gate(layer_index: int, payload: ServoLayerGatePayload) -> Dict[str, Any]:
    """Open or close a layer's gate the same way production sorting does
    (``servo.open()``/``.close()`` on the live object) - backend-agnostic,
    unlike ``/move-to`` (PCA9685 angle) or the Waveshare BLE gate routes.
    Built for diagnostics/benchmarking: real sorting never calls this
    directly, it goes through positioning.py's own door-select logic.

    Timing note: PCA9685's ``open()``/``close()`` return once the move is
    accepted, not once the servo arrives; the Waveshare BLE bridge blocks
    until its firmware confirms arrival. ``elapsed_ms`` reflects whichever
    of those this call actually did - meaningful for BLE, closer to zero
    for PCA9685.
    """
    _ensure_not_homing("set a layer gate")
    state = payload.state.strip().lower()
    if state not in {"open", "close"}:
        raise HTTPException(status_code=400, detail="state must be 'open' or 'close'.")

    servo = _live_servo_for_layer(layer_index)
    method = getattr(servo, "open" if state == "open" else "close", None)
    if method is None:
        raise HTTPException(status_code=500, detail="Servo does not support open/close.")

    started_at = time.monotonic()
    try:
        method()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to {state} layer {layer_index + 1}: {e}")
    elapsed_ms = round((time.monotonic() - started_at) * 1000)

    return {
        "ok": True,
        "layer_index": layer_index,
        "state": state,
        "elapsed_ms": elapsed_ms,
    }


def _servo_calibration_state(servo: Any) -> Dict[str, Any]:
    open_angle = getattr(servo, "open_angle", None)
    closed_angle = getattr(servo, "closed_angle", None)
    return {
        "open_angle": open_angle if isinstance(open_angle, int) else None,
        "closed_angle": closed_angle if isinstance(closed_angle, int) else None,
        "calibrated": bool(getattr(servo, "is_calibrated", True)),
    }


@router.post("/api/hardware-config/servo/layers/{layer_index}/lock")
def lock_layer_servo_angle(layer_index: int, payload: ServoLayerLockPayload) -> Dict[str, Any]:
    _ensure_not_homing("lock a servo angle")
    if payload.which not in {"open", "closed"}:
        raise HTTPException(status_code=400, detail="which must be 'open' or 'closed'.")
    servo = _live_servo_for_layer(layer_index)

    if payload.angle is not None:
        angle = max(0, min(180, int(payload.angle)))
    else:
        current = getattr(servo, "angle", None)
        if current is None:
            position = getattr(servo, "position", None)
            current = int(position) // 10 if position is not None else None
        if current is None:
            raise HTTPException(
                status_code=400,
                detail="Could not read the servo's current angle to lock in.",
            )
        angle = max(0, min(180, int(current)))

    layout = getBinLayout()
    if layer_index < 0 or layer_index >= len(layout.layers):
        raise HTTPException(status_code=404, detail=f"Unknown storage layer {layer_index + 1}.")
    # Calibration is per-channel (machine-level), not stored on the layout — so
    # locking an angle never touches bin_layout / geometry.
    channel_id = channelIdForLayer(layout, layer_index)
    if payload.which == "open":
        set_servo_channel_angle(channel_id, "open", angle)
        if hasattr(servo, "set_open_angle"):
            servo.set_open_angle(angle)
    else:
        set_servo_channel_angle(channel_id, "closed", angle)
        if hasattr(servo, "set_closed_angle"):
            servo.set_closed_angle(angle)

    state = _servo_calibration_state(servo)
    return {
        "ok": True,
        "layer_index": layer_index,
        "channel_id": channel_id,
        "which": payload.which,
        "angle": angle,
        **state,
        "feedback": _live_servo_feedback_for_layer(layer_index, servo),
        "message": (
            f"Layer {layer_index + 1} {payload.which} angle locked at {angle}°."
        ),
    }


@router.post("/api/hardware-config/servo/layers/{layer_index}/clear")
def clear_layer_servo_angle(layer_index: int, payload: ServoLayerClearPayload) -> Dict[str, Any]:
    _ensure_not_homing("clear a servo angle")
    if payload.which not in {"open", "closed", "both"}:
        raise HTTPException(status_code=400, detail="which must be 'open', 'closed' or 'both'.")
    servo = _live_servo_for_layer(layer_index)

    layout = getBinLayout()
    if layer_index < 0 or layer_index >= len(layout.layers):
        raise HTTPException(status_code=404, detail=f"Unknown storage layer {layer_index + 1}.")

    channel_id = channelIdForLayer(layout, layer_index)
    if payload.which in {"open", "both"}:
        set_servo_channel_angle(channel_id, "open", None)
        if hasattr(servo, "set_open_angle"):
            servo.set_open_angle(None)
    if payload.which in {"closed", "both"}:
        set_servo_channel_angle(channel_id, "closed", None)
        if hasattr(servo, "set_closed_angle"):
            servo.set_closed_angle(None)

    state = _servo_calibration_state(servo)
    return {
        "ok": True,
        "layer_index": layer_index,
        "which": payload.which,
        **state,
        "feedback": _live_servo_feedback_for_layer(layer_index, servo),
        "message": (
            f"Layer {layer_index + 1} calibration cleared."
            if payload.which == "both"
            else f"Layer {layer_index + 1} {payload.which} angle cleared."
        ),
    }


@router.get("/api/hardware/servo-status")
def get_servo_status() -> Dict[str, Any]:
    """Per-layer servo online state for debugging the red
    "Servo bus offline" banner. Returns one entry per layer with its
    live ``available`` flag plus the aggregate ``bus_online`` status —
    useful when the operator has reconnected the Waveshare BLE bridge and
    wants to verify the bus is back before pressing Resume.
    """
    active_irl = shared_state.getActiveIRL()
    layers: list[dict[str, Any]] = []
    any_online = False
    if active_irl is not None:
        servos = list(getattr(active_irl, "servos", []) or [])
        for index, servo in enumerate(servos):
            available = bool(getattr(servo, "available", True))
            if available:
                any_online = True
            layers.append(
                {
                    "layer_index": index,
                    "available": available,
                    "channel": getattr(servo, "channel", None),
                    "name": getattr(servo, "_name", f"layer_{index}_servo"),
                }
            )
    stats = None
    try:
        if shared_state.gc_ref is not None:
            stats = getattr(
                shared_state.gc_ref.runtime_stats, "servo_bus_offline_since_ts", None
            )
    except Exception:
        stats = None
    return {
        "ok": True,
        "bus_online": any_online,
        "layers": layers,
        "offline_since_ts": stats,
    }


# ---------------------------------------------------------------------------
# Waveshare BLE gate bridge (esp32_ble_servo_bridge) — all servo intelligence
# (open/closed/blocked, auto ID assignment, calibration) lives on the board
# itself, so there's no raw position/nudge/lock-angle surface here.
# ---------------------------------------------------------------------------


class WaveshareBleGateMovePayload(BaseModel):
    position: str  # "open" | "close"


@router.get("/api/hardware-config/waveshare-ble/scan")
def scan_waveshare_ble_devices(timeout: float = 5.0) -> Dict[str, Any]:
    """Nearby BLE devices, for the pairing UI. Independent of any configured
    or live bridge — just a discovery scan to pick an address from."""
    try:
        devices = _scan_waveshare_ble_devices(timeout=max(1.0, min(timeout, 20.0)))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"BLE scan failed: {exc}")
    return {"ok": True, "devices": devices}


@router.get("/api/hardware-config/waveshare-ble/status")
def get_waveshare_ble_status(device: str | None = None) -> Dict[str, Any]:
    return _get_waveshare_ble_status(device=device)


@router.post("/api/hardware-config/waveshare-ble/rescan")
def rescan_waveshare_ble(device: str | None = None) -> Dict[str, Any]:
    _ensure_not_homing("rescan the Waveshare BLE bridge")
    return _get_waveshare_ble_status(device=device)


@router.post("/api/hardware-config/waveshare-ble/learn")
def learn_waveshare_ble_servo(device: str | None = None) -> Dict[str, Any]:
    """Assign the next free gate ID (11, 12, 13, ...) to a servo sitting at
    the factory-default ID and calibrate it. Connect exactly one new servo
    to the bus before calling this — see the firmware README's one-at-a-time
    onboarding flow."""
    _ensure_not_homing("learn a new Waveshare BLE servo")
    try:
        bridge = get_waveshare_ble_bridge_for_api(device)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        response = bridge.learn()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LEARN failed: {exc}")

    if response.startswith("LEARN NONE"):
        raise HTTPException(
            status_code=404,
            detail="No new servo found at the factory-default ID. Connect one new servo and retry.",
        )
    if response.startswith("LEARN ERR") or (response.startswith("LEARNED") and response.endswith("NOCAL")):
        message = (
            f"A new servo got an ID but calibration failed ({response}). It can be retried with "
            "the per-gate calibrate action."
            if response.startswith("LEARNED")
            else f"LEARN failed: {response}"
        )
        gate = None
        if response.startswith("LEARNED"):
            parts = response.split()
            gate = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        try:
            bridge.refresh_known_gates()
        except Exception:
            pass
        return {"ok": True, "raw": response, "gate": gate, "calibrated": False, "message": message}

    parts = response.split()
    gate = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    try:
        bridge.refresh_known_gates()
    except Exception:
        pass
    return {
        "ok": True,
        "raw": response,
        "gate": gate,
        "calibrated": True,
        "message": f"Gate {gate} learned and calibrated." if gate is not None else response,
    }


@router.post("/api/hardware-config/waveshare-ble/reset-all")
def reset_all_waveshare_ble_servos(device: str | None = None) -> Dict[str, Any]:
    """Factory-reset every servo currently reachable (ID back to 1, angle
    limits back to 0-1023) so the one-at-a-time LEARN flow can restart."""
    _ensure_not_homing("factory-reset the Waveshare BLE servos")
    try:
        bridge = get_waveshare_ble_bridge_for_api(device)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        response = bridge.reset_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RESETALL failed: {exc}")

    try:
        bridge.refresh_known_gates()
    except Exception:
        pass

    parts = response.split()
    count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    return {
        "ok": True,
        "raw": response,
        "count": count,
        "message": (
            f"{count} servo(s) reset to factory defaults." if count is not None else response
        ),
    }


@router.post("/api/hardware-config/waveshare-ble/gates/{gate}/move")
def move_waveshare_ble_gate(gate: int, payload: WaveshareBleGateMovePayload) -> Dict[str, Any]:
    _ensure_not_homing("move a Waveshare BLE gate")
    if gate < 1 or gate > 60:
        raise HTTPException(status_code=400, detail="Gate must be between 1 and 60.")
    target = (payload.position or "").lower().strip()
    if target not in {"open", "close"}:
        raise HTTPException(status_code=400, detail="position must be 'open' or 'close'.")

    try:
        bridge = get_waveshare_ble_bridge_for_api()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        response = bridge.open_gate(gate) if target == "open" else bridge.close_gate(gate)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to move gate {gate}: {exc}")

    state = response.split()[1] if len(response.split()) > 1 else "ERR"
    return {
        "ok": True,
        "gate": gate,
        "raw": response,
        "state": state,
        "message": f"Gate {gate}: {state}.",
    }


@router.get("/api/hardware-config/waveshare-ble/gates/{gate}/status")
def status_waveshare_ble_gate(gate: int) -> Dict[str, Any]:
    if gate < 1 or gate > 60:
        raise HTTPException(status_code=400, detail="Gate must be between 1 and 60.")

    try:
        bridge = get_waveshare_ble_bridge_for_api()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        response = bridge.status_gate(gate)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read gate {gate}: {exc}")

    state = response.split()[1] if len(response.split()) > 1 else "ERR"
    return {"ok": True, "gate": gate, "raw": response, "state": state}


@router.get("/api/hardware-config/waveshare-ble/demo")
def get_waveshare_ble_demo_mode(device: str | None = None) -> Dict[str, Any]:
    try:
        bridge = get_waveshare_ble_bridge_for_api(device)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    try:
        enabled = bridge.get_demo_mode()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read demo mode: {exc}")
    return {"ok": True, "demo_mode": enabled}


class WaveshareBleDemoModePayload(BaseModel):
    enabled: bool


@router.post("/api/hardware-config/waveshare-ble/demo")
def set_waveshare_ble_demo_mode(
    payload: WaveshareBleDemoModePayload, device: str | None = None
) -> Dict[str, Any]:
    _ensure_not_homing("change the Waveshare BLE bridge's demo mode")
    try:
        bridge = get_waveshare_ble_bridge_for_api(device)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    try:
        enabled = bridge.set_demo_mode(payload.enabled)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not change demo mode: {exc}")
    try:
        bridge.refresh_known_gates()
    except Exception:
        pass
    return {
        "ok": True,
        "demo_mode": enabled,
        "message": f"Demo mode {'enabled' if enabled else 'disabled'} — the bridge no longer drives real servos."
        if enabled
        else "Demo mode disabled — the bridge drives the real servo bus again.",
    }


@router.post("/api/hardware-config/waveshare-ble/gates/{gate}/calibrate")
def calibrate_waveshare_ble_gate(gate: int) -> Dict[str, Any]:
    _ensure_not_homing("calibrate a Waveshare BLE gate")
    if gate < 1 or gate > 60:
        raise HTTPException(status_code=400, detail="Gate must be between 1 and 60.")

    try:
        bridge = get_waveshare_ble_bridge_for_api()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        response = bridge.calibrate_gate(gate)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calibration failed: {exc}")

    if not response.startswith("CALIBRATED"):
        raise HTTPException(status_code=500, detail=f"Calibration failed: {response}")
    return {"ok": True, "gate": gate, "raw": response, "message": f"Gate {gate} calibrated."}


# ---------------------------------------------------------------------------
# ESP32 firmware flashing — downloads a release asset built by
# .github/workflows/firmware-release.yml (family "esp32_ble_bridge" in
# /api/firmware/releases) and flashes it over USB-serial with esptool.
# Separate job machinery from server/routers/firmware.py's: that one
# identifies boards over the sorter's own MCU bus protocol and juggles two
# different bootloader-entry mechanisms, neither of which applies to the
# ESP32 (found by USB vid/pid only, flashed by the standard Espressif tool).
# ---------------------------------------------------------------------------

_ESP32_UPLOAD_DIR = "/tmp/sorter-esp32-firmware"
_esp32_jobs: Dict[str, "Esp32FlashJob"] = {}
_esp32_jobs_order: List[str] = []
_esp32_jobs_lock = threading.Lock()
_esp32_flash_lock = threading.Lock()
_MAX_ESP32_JOBS_KEPT = 10


class Esp32FlashRequest(BaseModel):
    asset_url: str
    asset_name: Optional[str] = None


class Esp32FlashJob:
    def __init__(self, request: Esp32FlashRequest):
        self.id = uuid.uuid4().hex[:12]
        self.request = request
        self.created_ts = time.time()
        self.phase = "queued"
        self.progress: Optional[float] = None
        self.status = "running"  # running | done | failed | cancelled
        self.error: Optional[str] = None
        self.log: List[str] = []
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()

    def setPhase(self, phase: str, message: Optional[str] = None) -> None:
        with self._lock:
            self.phase = phase
            self.progress = None
            if message:
                self.log.append(message)

    def setProgress(self, progress: float) -> None:
        with self._lock:
            self.progress = max(0.0, min(1.0, progress))

    def addLog(self, message: str) -> None:
        with self._lock:
            self.log.append(message)

    def finish(self, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            self.status = status
            self.error = error
            if error:
                self.log.append(error)

    def toPayload(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.id,
                "created_ts": self.created_ts,
                "phase": self.phase,
                "progress": self.progress,
                "status": self.status,
                "error": self.error,
                "log": list(self.log[-40:]),
                "asset_name": self.request.asset_name,
            }


def _runEsp32FlashJob(job: Esp32FlashJob) -> None:
    try:
        os.makedirs(_ESP32_UPLOAD_DIR, exist_ok=True)
        bin_path = os.path.join(_ESP32_UPLOAD_DIR, f"{job.id}.bin")
        job.setPhase("downloading", f"Downloading {job.request.asset_name or job.request.asset_url}...")
        with requests.get(job.request.asset_url, stream=True, timeout=30) as res:
            res.raise_for_status()
            total = int(res.headers.get("Content-Length") or 0)
            written = 0
            with open(bin_path, "wb") as f:
                for chunk in res.iter_content(chunk_size=64 * 1024):
                    if job.cancel_event.is_set():
                        raise esp32_flash.FlashCancelled()
                    f.write(chunk)
                    written += len(chunk)
                    if total:
                        job.setProgress(written / total)

        job.setPhase("waiting_port", "Waiting for the ESP32 on USB...")
        port = esp32_flash.wait_for_esp32_port(job.cancel_event)
        job.addLog(f"ESP32 found on {port}.")

        job.setPhase("flashing", f"Flashing {os.path.basename(bin_path)} via esptool...")
        esp32_flash.flash_esp32(shared_state.gc_ref, bin_path, port, job.cancel_event, job.setProgress)

        job.setPhase("done", "Flash complete. The ESP32 has rebooted into the new firmware.")
        job.finish("done")
    except esp32_flash.FlashCancelled:
        job.finish("cancelled", error="Cancelled.")
    except esp32_flash.FlashError as exc:
        job.finish("failed", error=str(exc))
    except Exception as exc:
        job.finish("failed", error=f"Unexpected error: {exc}")
    finally:
        _esp32_flash_lock.release()


def _registerEsp32Job(job: Esp32FlashJob) -> None:
    with _esp32_jobs_lock:
        _esp32_jobs[job.id] = job
        _esp32_jobs_order.append(job.id)
        while len(_esp32_jobs_order) > _MAX_ESP32_JOBS_KEPT:
            dropped = _esp32_jobs_order.pop(0)
            _esp32_jobs.pop(dropped, None)


@router.post("/api/hardware-config/waveshare-ble/esp32-flash")
def start_esp32_flash(request: Esp32FlashRequest) -> Dict[str, Any]:
    if not _esp32_flash_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An ESP32 flash is already in progress.")
    try:
        job = Esp32FlashJob(request)
        _registerEsp32Job(job)
        thread = threading.Thread(
            target=_runEsp32FlashJob, args=(job,), daemon=True, name=f"esp32-flash-{job.id}"
        )
        thread.start()
    except Exception:
        _esp32_flash_lock.release()
        raise
    return {"job_id": job.id, "job": job.toPayload()}


@router.get("/api/hardware-config/waveshare-ble/esp32-flash/{job_id}")
def get_esp32_flash_job(job_id: str) -> Dict[str, Any]:
    with _esp32_jobs_lock:
        job = _esp32_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown flash job '{job_id}'")
    return job.toPayload()


@router.post("/api/hardware-config/waveshare-ble/esp32-flash/{job_id}/cancel")
def cancel_esp32_flash_job(job_id: str) -> Dict[str, Any]:
    with _esp32_jobs_lock:
        job = _esp32_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown flash job '{job_id}'")
    if job.status != "running":
        return {"ok": False, "message": f"Job is already {job.status}.", "job": job.toPayload()}
    job.cancel_event.set()
    return {"ok": True, "message": "Cancel requested.", "job": job.toPayload()}
