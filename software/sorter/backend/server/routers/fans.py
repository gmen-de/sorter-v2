"""Octopus fan headers (FAN0-FAN5): generic PWM digital outputs on channels
3-8 of that board's DIGITAL_OUTPUT_COUNT=9 layout (see hwcfg_octopus.h) -
fixed hardware wiring, not a flexible per-channel assignment like the LED
outputs. No other board family has these.

Duty read-back is this process's own last-written value, not a live read
from the board (there is no GET_DUTY command) - the firmware's own boot
default (full speed) is invisible here until this process writes a duty
at least once.
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


def _fanOutputs() -> Any | None:
    irl = shared_state.getActiveIRL()
    if irl is None:
        return None
    interfaces = getattr(irl, "interfaces", {})
    board = interfaces.get("DISTRIBUTION MB") if isinstance(interfaces, dict) else None
    outputs = getattr(board, "digital_outputs", None) if board is not None else None
    if not outputs or len(outputs) < FAN_FIRST_CHANNEL + FAN_COUNT:
        return None
    return outputs


@router.get("/api/fans")
def get_fans() -> dict[str, Any]:
    outputs = _fanOutputs()
    if outputs is None:
        return {"available": False, "fans": []}
    return {
        "available": True,
        "fans": [
            {
                "index": i,
                "duty_percent": round(outputs[FAN_FIRST_CHANNEL + i].duty * 100 / DIGITAL_OUTPUT_DUTY_MAX),
            }
            for i in range(FAN_COUNT)
        ],
    }


@router.post("/api/fans")
def set_fan(payload: SetFanPayload) -> dict[str, Any]:
    if not 0 <= payload.index < FAN_COUNT:
        raise HTTPException(status_code=400, detail=f"Fan index must be 0-{FAN_COUNT - 1}")
    if not 0 <= payload.duty_percent <= 100:
        raise HTTPException(status_code=400, detail="duty_percent must be 0-100")
    outputs = _fanOutputs()
    if outputs is None:
        raise HTTPException(status_code=503, detail="Hardware not initialized, or this board has no fan headers.")
    outputs[FAN_FIRST_CHANNEL + payload.index].setDuty(round(payload.duty_percent * DIGITAL_OUTPUT_DUTY_MAX / 100))
    return {"ok": True, "index": payload.index, "duty_percent": payload.duty_percent}
