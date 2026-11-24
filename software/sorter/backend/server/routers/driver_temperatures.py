"""Stepper driver temperatures (Octopus thermistor inputs) and the settings of
their fan control and over-temperature hold - see driver_temp_control.py."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import driver_temp_control

router = APIRouter()


class DriverTemperatureSettingsPayload(BaseModel):
    fan_index: Optional[int] = None
    fan_start_c: Optional[float] = None
    fan_full_c: Optional[float] = None
    fan_min_duty_percent: Optional[int] = None
    shutdown_c: Optional[float] = None
    resume_below_c: Optional[float] = None


@router.get("/api/driver-temperatures")
def get_driver_temperatures(history_s: float = 0.0) -> dict[str, Any]:
    control = driver_temp_control.getActive()
    snapshot = (
        control.snapshot(history_s=min(history_s, driver_temp_control.HISTORY_S))
        if control is not None
        else {"available": False, "drivers": [], "fan": None, "overtemp": [], "history": None}
    )
    snapshot["settings"] = driver_temp_control.getSettings()
    return snapshot


@router.post("/api/driver-temperatures/settings")
def set_driver_temperature_settings(payload: DriverTemperatureSettingsPayload) -> dict[str, Any]:
    try:
        settings = driver_temp_control.setSettings(payload.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "settings": settings}
