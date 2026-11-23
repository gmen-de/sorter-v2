"""An advance is sized from the newest picture. One taken while the channel was
still turning often misses the lead piece, and the move sized for a piece
further back carries the real lead over the lip. So the advance waits for a
picture taken after the last move ended."""

from __future__ import annotations

from types import SimpleNamespace

from perception.cascade import Action
from subsystems.feeder.pulse_perception.config import PulsePerceptionConfig
from subsystems.feeder.pulse_perception.flow import PulsePerceptionFeeding


def _advance(picture_at: float, moved_until: float) -> list[float]:
    feeder = PulsePerceptionFeeding.__new__(PulsePerceptionFeeding)
    moves: list[float] = []
    feeder._busy = lambda stepper: False
    feeder._shake_owed = {}
    feeder._moved_until = {3: moved_until}
    feeder._classification_ready = lambda cfg: True
    feeder._move = lambda label, channel, stepper, deg, pause, cfg, enforce_min=True: moves.append(deg) or True
    state = SimpleNamespace(ts=picture_at, in_drop=True, advance_clearance_deg=20.0)
    feeder._apply_action("ch3", 3, Action.ADVANCE, SimpleNamespace(_name="s"), state, PulsePerceptionConfig())
    return moves


def test_no_advance_on_a_picture_taken_while_the_channel_was_turning() -> None:
    assert _advance(picture_at=9.9, moved_until=10.0) == []


def test_the_advance_goes_on_a_picture_taken_after_it_stopped() -> None:
    assert _advance(picture_at=10.05, moved_until=10.0) == [20.0]
