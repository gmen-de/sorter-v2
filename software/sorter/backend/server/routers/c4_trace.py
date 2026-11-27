"""What the classification channel did between a piece's classification and
the chute being aimed for it, piece by piece."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from subsystems.classification_channel.two_piece import aim_trace

router = APIRouter()


@router.get("/api/c4/aim-trace")
def get_aim_trace(limit: int = 50) -> Dict[str, Any]:
    """The last pieces' traces, oldest first: for each, when it was classified,
    when its aim was asked for, when the chute started moving, when the aim
    was done and when it was ejected (wall-clock seconds), the piece's gap to
    the exit in channel degrees and its zone at each, and the stretches
    between them in ms. `summary` has their median, 90th percentile and
    maximum."""
    traces = aim_trace.recent(limit)
    return {"summary": aim_trace.summary(traces), "traces": traces}


@router.delete("/api/c4/aim-trace")
def clear_aim_trace() -> Dict[str, Any]:
    aim_trace.clear()
    return {"ok": True}
