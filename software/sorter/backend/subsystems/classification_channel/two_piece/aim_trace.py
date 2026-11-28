"""When each piece was classified, when its chute aim was asked for and when it
was done, and where the piece stood on the classification channel at each.

The channel notes the moments (flow.py); this keeps the last pieces' traces in
memory for /api/c4/aim-trace, so a slow cycle can be read off instead of
guessed at. All times are wall-clock seconds; the angles are the piece's gap to
the exit in channel degrees (smaller = nearer the fall-off)."""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

_KEPT = 300
_lock = threading.Lock()
_done: deque[dict[str, Any]] = deque(maxlen=_KEPT)

# (label, from, to): the stretches a cycle's time goes into.
STRETCHES = (
    ("classified_to_aim_requested", "classified_at", "aim_requested_at"),
    ("aim_requested_to_chute_moving", "aim_requested_at", "chute_move_started_at"),
    ("chute_moving_to_positioned", "chute_move_started_at", "positioned_at"),
    ("aim_requested_to_aim_ready", "aim_requested_at", "aim_ready_at"),
    ("aim_ready_to_ejected", "aim_ready_at", "ejected_at"),
    # Which of the two waits before the aim request is the long one: the piece
    # still in the drop zone (the platter), or distribution's slot (the piece
    # before it not yet ejected).
    ("classified_to_left_drop", "classified_at", "left_drop_at"),
    ("left_drop_to_aim_requested", "left_drop_at", "aim_requested_at"),
    ("prev_ejected_to_aim_requested", "prev_ejected_at", "aim_requested_at"),
    ("aim_ready_to_at_exit", "aim_ready_at", "at_exit_at"),
    ("at_exit_to_ejected", "at_exit_at", "ejected_at"),
)


def finish(trace: dict[str, Any]) -> None:
    for label, a, b in STRETCHES:
        if trace.get(a) is not None and trace.get(b) is not None:
            trace[f"{label}_ms"] = round((trace[b] - trace[a]) * 1000.0)
    with _lock:
        _done.append(dict(trace))


def recent(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        items = list(_done)
    return items[-max(1, int(limit)):]


def summary(traces: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"pieces": len(traces)}
    for label, _a, _b in STRETCHES:
        values = sorted(t[f"{label}_ms"] for t in traces if f"{label}_ms" in t)
        if values:
            out[label + "_ms"] = {
                "n": len(values),
                "p50": values[len(values) // 2],
                "p90": values[min(len(values) - 1, int(len(values) * 0.9))],
                "max": values[-1],
            }
    return out


def clear() -> None:
    with _lock:
        _done.clear()
