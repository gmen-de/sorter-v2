"""Chute calibration by motor steps: manual control of the chute's stepper and
teaching each bin's position as an exact step position.

A taught bin position is stored as the per-bin correction (microsteps) on top
of the aiming geometry (bin_layout_store's chute_bin_corrections), the same
value every move to that bin already applies, so nothing else changes: the
sorter, the bin wheel and the test moves all pick it up.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from bin_layout_store import chute_correction_key, get_chute_bin_corrections, set_chute_bin_correction
from server import shared_state
from server.routers.steppers import (
    _ensure_manual_motion_allowed,
    _ensure_no_blocking_fault,
)
from subsystems.distribution.chute import CHUTE_ENCODER_USTEPS_PER_REV

router = APIRouter()

MIN_SPEED_USTEPS_PER_SEC = 16
MAX_SPEED_USTEPS_PER_SEC = 20000
MAX_ACCEL_USTEPS_PER_SEC2 = 100000


class SpeedPayload(BaseModel):
    speed: int = Field(..., ge=-MAX_SPEED_USTEPS_PER_SEC, le=MAX_SPEED_USTEPS_PER_SEC)
    acceleration: Optional[int] = Field(None, gt=0, le=MAX_ACCEL_USTEPS_PER_SEC2)


class MoveStepsPayload(BaseModel):
    steps: int
    speed: int = Field(500, gt=0, le=MAX_SPEED_USTEPS_PER_SEC)
    acceleration: Optional[int] = Field(None, gt=0, le=MAX_ACCEL_USTEPS_PER_SEC2)


class MoveToPayload(BaseModel):
    usteps: int
    speed: int = Field(500, gt=0, le=MAX_SPEED_USTEPS_PER_SEC)
    acceleration: Optional[int] = Field(None, gt=0, le=MAX_ACCEL_USTEPS_PER_SEC2)


class BinRef(BaseModel):
    layer_index: int
    section_index: int
    bin_index: int


class LearnBinPayload(BinRef):
    # The step position to teach. Left out: where the chute stands right now.
    usteps: Optional[int] = None


def _chute_and_stepper() -> tuple[Any, Any]:
    _ensure_manual_motion_allowed("move the chute")
    _ensure_no_blocking_fault("move the chute")
    irl = shared_state.getActiveIRL()
    if irl is None:
        raise HTTPException(
            status_code=503,
            detail="Hardware not initialized. Initialize or home the system first.",
        )
    chute = getattr(irl, "chute", None)
    stepper = getattr(irl, "chute_stepper", None)
    if chute is None or stepper is None:
        raise HTTPException(status_code=503, detail="Chute subsystem not available.")
    return chute, stepper


def _encoder_chute() -> tuple[Any, Any, Any]:
    chute, stepper = _chute_and_stepper()
    encoder = getattr(chute, "chute_encoder", None)
    if encoder is None:
        raise HTTPException(
            status_code=409,
            detail="This needs the Octopus chute encoder; the chute runs on an endstop here.",
        )
    return chute, stepper, encoder


def _run(action: str, fn: Any) -> Any:
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Could not {action}: {exc}")


def _ustep_target(chute: Any, ref: BinRef) -> tuple[int, int]:
    """(geometry target, k): the step position the aiming geometry gives this
    bin, without any taught correction, and how many bins its section has."""
    k = chute._binsInSection(ref.layer_index, ref.section_index)
    if ref.bin_index < 0 or ref.bin_index >= k:
        raise HTTPException(status_code=400, detail="That bin does not exist in this layout.")
    angle = chute.angleForVirtualBin(ref.section_index, ref.bin_index, k)
    if angle is None:
        raise HTTPException(status_code=400, detail="Bin is unreachable (angle out of range).")
    return round(angle % 360.0 / 360.0 * CHUTE_ENCODER_USTEPS_PER_REV), k


@router.post("/api/chute-calibration/jog")
def jog(payload: SpeedPayload) -> Dict[str, Any]:
    """Run the chute at a signed speed (microsteps/s) until stopped; 0 stops."""
    _chute, stepper = _chute_and_stepper()
    if payload.speed == 0:
        return stop()

    def go() -> bool:
        stepper.set_speed_limits(MIN_SPEED_USTEPS_PER_SEC, abs(payload.speed))
        return stepper.move_at_speed(payload.speed, acceleration=payload.acceleration)

    if not _run("start the chute", go):
        raise HTTPException(status_code=409, detail="The board refused the move.")
    return {"ok": True, "speed": payload.speed}


@router.post("/api/chute-calibration/stop")
def stop() -> Dict[str, Any]:
    _chute, stepper = _chute_and_stepper()

    def go() -> None:
        # A floor above 0 so braking to a stop always catches (see _halt_stepper).
        stepper.set_speed_limits(MIN_SPEED_USTEPS_PER_SEC, 60000)
        stepper.move_at_speed(0)

    _run("stop the chute", go)
    return {"ok": True}


@router.post("/api/chute-calibration/move-steps")
def move_steps(payload: MoveStepsPayload) -> Dict[str, Any]:
    """Move the chute by a number of microsteps from where it is."""
    _chute, stepper = _chute_and_stepper()

    def go() -> bool:
        stepper.set_speed_limits(MIN_SPEED_USTEPS_PER_SEC, payload.speed)
        return stepper.move_steps(payload.steps, acceleration=payload.acceleration)

    if not _run("move the chute", go):
        raise HTTPException(status_code=409, detail="The chute is still moving; try again when it stops.")
    return {"ok": True, "steps": payload.steps}


@router.post("/api/chute-calibration/move-to")
def move_to(payload: MoveToPayload) -> Dict[str, Any]:
    """Go to an absolute step position (the chute's own counter)."""
    _chute, stepper, encoder = _encoder_chute()

    def go() -> None:
        stepper.set_speed_limits(MIN_SPEED_USTEPS_PER_SEC, payload.speed)
        if payload.acceleration is not None:
            stepper.set_acceleration(payload.acceleration)
        encoder.move_to_step_pos(payload.usteps)

    _run("move the chute", go)
    return {"ok": True, "usteps": payload.usteps}


@router.post("/api/chute-calibration/zero-counter")
def zero_counter() -> Dict[str, Any]:
    """Make wherever the chute stands now step position 0. Only while stopped:
    the counter is what every step position is measured from, so zeroing it
    moves the origin of the taught bin positions too (they are corrections on
    top of the geometry, which counts from here)."""
    chute, stepper = _chute_and_stepper()

    def go() -> None:
        if not stepper.stopped:
            raise RuntimeError("the chute is still moving")
        stepper.position = 0
        encoder = getattr(chute, "chute_encoder", None)
        if encoder is not None:
            # The position check compares drift against the count at the zero.
            chute._encoder_zero_count = encoder.get_count()

    _run("zero the counter", go)
    return {"ok": True, "usteps": 0}


@router.get("/api/chute-calibration/bins")
def list_bins(layer_index: int = 0) -> Dict[str, Any]:
    """Every bin of one layer with its geometry step position, its taught
    correction and the step position the chute actually drives to."""
    irl = shared_state.getActiveIRL()
    chute = getattr(irl, "chute", None) if irl is not None else None
    if chute is None:
        raise HTTPException(status_code=503, detail="Chute subsystem not available.")
    layers = getattr(chute.layout, "layers", [])
    if layer_index < 0 or layer_index >= len(layers):
        raise HTTPException(status_code=400, detail=f"Invalid layer index {layer_index}.")
    corrections = get_chute_bin_corrections()
    bin_count = chute.layerBinCount(layer_index)
    bins: List[Dict[str, Any]] = []
    for section_index, section in enumerate(layers[layer_index].sections):
        for bin_index in range(len(section.bins)):
            ref = BinRef(layer_index=layer_index, section_index=section_index, bin_index=bin_index)
            try:
                base, _k = _ustep_target(chute, ref)
            except HTTPException:
                continue
            key = chute_correction_key(bin_count, section_index, bin_index)
            correction = int(corrections.get(key, 0))
            bins.append(
                {
                    "section_index": section_index,
                    "bin_index": bin_index,
                    "geometry_usteps": base,
                    "correction_usteps": correction,
                    "target_usteps": base + correction,
                    "taught": key in corrections,
                }
            )
    return {
        "layer_index": layer_index,
        # What is taught here holds for every layer with this many bins.
        "bin_count": bin_count,
        "same_size_layers": [
            i for i in range(len(layers)) if i != layer_index and chute.layerBinCount(i) == bin_count
        ],
        "usteps_per_rev": CHUTE_ENCODER_USTEPS_PER_REV,
        "bins": bins,
    }


@router.post("/api/chute-calibration/learn-bin")
def learn_bin(payload: LearnBinPayload) -> Dict[str, Any]:
    """Teach a bin its step position (the given one, or where the chute stands
    now): stored as the correction from the geometry's own position."""
    chute, stepper = _chute_and_stepper()
    base, _k = _ustep_target(chute, payload)
    usteps = payload.usteps
    if usteps is None:
        usteps = int(_run("read the chute position", lambda: stepper.position))
    # The counter can sit a revolution (or more) away from where the geometry
    # counts; keep the correction the short way round.
    half = CHUTE_ENCODER_USTEPS_PER_REV // 2
    correction = (usteps - base + half) % CHUTE_ENCODER_USTEPS_PER_REV - half
    set_chute_bin_correction(
        chute.layerBinCount(payload.layer_index), payload.section_index, payload.bin_index, correction
    )
    return {
        "ok": True,
        "geometry_usteps": base,
        "correction_usteps": correction,
        "target_usteps": base + correction,
    }


@router.post("/api/chute-calibration/clear-bin")
def clear_bin(payload: BinRef) -> Dict[str, Any]:
    irl = shared_state.getActiveIRL()
    chute = getattr(irl, "chute", None) if irl is not None else None
    if chute is None:
        raise HTTPException(status_code=503, detail="Chute subsystem not available.")
    set_chute_bin_correction(
        chute.layerBinCount(payload.layer_index), payload.section_index, payload.bin_index, None
    )
    return {"ok": True}
