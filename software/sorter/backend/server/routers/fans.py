"""Octopus fan headers (FAN0-FAN5): generic PWM digital outputs on channels
3-8 of that board's DIGITAL_OUTPUT_COUNT=9 layout (see hwcfg_octopus.h) -
fixed hardware wiring, not a flexible per-channel assignment like the LED
outputs. No other board family has these.

Duty read-back is this process's own last-written value, not a live read
from the board (there is no GET_DUTY command) - the firmware's own boot
default (full speed) is invisible here until this process writes a duty
at least once.

FAN5 (index 5) is reserved for cpu_fan_control's automatic CPU cooling loop
(see that module), and the fan driver_temp_control cools the stepper drivers
with (machine.toml [driver_temperature].fan_index) is that loop's - neither
duty is settable here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hardware.sorter_interface import DIGITAL_OUTPUT_DUTY_MAX
from server import shared_state

router = APIRouter()

FAN_FIRST_CHANNEL = 3
FAN_COUNT = 6


class SetFanPayload(BaseModel):
    index: int
    duty_percent: int


def fanOutputs(irl: Any | None = None) -> Any | None:
    """The board's FAN_COUNT fan-output objects, indexed 0..FAN_COUNT-1, or
    None if hardware isn't initialized or this board has no fan headers."""
    if irl is None:
        irl = shared_state.getActiveIRL()
    if irl is None:
        return None
    interfaces = getattr(irl, "interfaces", {})
    board = interfaces.get("DISTRIBUTION MB") if isinstance(interfaces, dict) else None
    outputs = getattr(board, "digital_outputs", None) if board is not None else None
    if not outputs or len(outputs) < FAN_FIRST_CHANNEL + FAN_COUNT:
        return None
    return outputs[FAN_FIRST_CHANNEL : FAN_FIRST_CHANNEL + FAN_COUNT]


def _driverFanIndex() -> int | None:
    import driver_temp_control

    if driver_temp_control.getActive() is None:
        return None
    index = int(driver_temp_control.getSettings()["fan_index"])
    return index if index >= 0 else None


def _cpuInfo() -> dict[str, Any]:
    import cpu_fan_control as fan_control

    control = fan_control.getActive()
    return {
        "temp_c": fan_control.readCpuTempC(),
        "throttle_c": fan_control.SOC_THROTTLE_C,
        "fan_off_below_c": fan_control.FAN_OFF_BELOW_C,
        "fan_on_at_or_above_c": fan_control.FAN_ON_AT_OR_ABOVE_C,
        "fan_on": control.fan_on if control is not None else None,
        "auto_fan_index": fan_control.AUTO_FAN_INDEX,
        "min_switch_interval_s": fan_control.MIN_SWITCH_INTERVAL_S,
    }


@router.get("/api/fans")
def get_fans() -> dict[str, Any]:
    import cpu_fan_control as fan_control

    outputs = fanOutputs()
    if outputs is None:
        return {"available": False, "fans": [], "cpu": _cpuInfo()}
    driver_fan = _driverFanIndex()
    return {
        "available": True,
        "fans": [
            {
                "index": i,
                "duty_percent": round(outputs[i].duty * 100 / DIGITAL_OUTPUT_DUTY_MAX),
                "auto": i == fan_control.AUTO_FAN_INDEX or i == driver_fan,
                "auto_source": (
                    "cpu" if i == fan_control.AUTO_FAN_INDEX else "drivers" if i == driver_fan else None
                ),
            }
            for i in range(FAN_COUNT)
        ],
        "cpu": _cpuInfo(),
    }


@router.post("/api/fans")
def set_fan(payload: SetFanPayload) -> dict[str, Any]:
    import cpu_fan_control as fan_control

    if not 0 <= payload.index < FAN_COUNT:
        raise HTTPException(status_code=400, detail=f"Fan index must be 0-{FAN_COUNT - 1}")
    if payload.index == fan_control.AUTO_FAN_INDEX:
        raise HTTPException(
            status_code=400,
            detail=f"FAN{fan_control.AUTO_FAN_INDEX} is controlled automatically by CPU temperature.",
        )
    if payload.index == _driverFanIndex():
        raise HTTPException(
            status_code=400,
            detail=f"FAN{payload.index} is controlled automatically by the stepper driver temperatures.",
        )
    if not 0 <= payload.duty_percent <= 100:
        raise HTTPException(status_code=400, detail="duty_percent must be 0-100")
    outputs = fanOutputs()
    if outputs is None:
        raise HTTPException(status_code=503, detail="Hardware not initialized, or this board has no fan headers.")
    outputs[payload.index].setDuty(round(payload.duty_percent * DIGITAL_OUTPUT_DUTY_MAX / 100))
    return {"ok": True, "index": payload.index, "duty_percent": payload.duty_percent}
