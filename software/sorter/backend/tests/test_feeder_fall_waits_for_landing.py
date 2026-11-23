"""A fall off C3 counts once the classification channel has the piece: until
then the exit only makes its small pulse, so a lead the camera lost on the lip
is not turned off in one long move together with the piece behind it."""

from __future__ import annotations

from types import SimpleNamespace

from subsystems.feeder.pulse_perception.config import PulsePerceptionConfig
from subsystems.feeder.pulse_perception.flow import PulsePerceptionFeeding
from subsystems.feeder.pulse_perception.landing_confirm import LandingConfirm


def _feeder(landing: LandingConfirm) -> PulsePerceptionFeeding:
    feeder = PulsePerceptionFeeding.__new__(PulsePerceptionFeeding)
    feeder._landing = {3: landing}
    feeder._moved_until = {}
    return feeder


def _state(*gaps: float) -> SimpleNamespace:
    return SimpleNamespace(ts=1.0, pieces=tuple(SimpleNamespace(com_forward_to_exit_deg=g) for g in gaps))


def _fellUnseen() -> LandingConfirm:
    landing = LandingConfirm()
    landing.fell(10.0, next_has_piece=False)
    return landing


def test_a_fall_stays_open_until_the_next_channel_has_a_piece() -> None:
    landing = _fellUnseen()
    assert landing.open
    landing.observe(next_has_piece=False)
    assert landing.open
    landing.observe(next_has_piece=True)
    assert not landing.open


def test_a_piece_already_on_the_next_channel_is_the_one_that_fell() -> None:
    landing = LandingConfirm()
    landing.fell(10.0, next_has_piece=True)
    assert not landing.open


def test_after_an_unseen_fall_the_exit_only_makes_its_small_pulse() -> None:
    cfg = PulsePerceptionConfig(exit_pulse_output_deg=2.0, exit_move_max_deg=30.0)
    assert _feeder(LandingConfirm())._exitMoveDeg(3, _state(8.0), cfg) == 30.0
    assert _feeder(_fellUnseen())._exitMoveDeg(3, _state(8.0), cfg) == 2.0


def test_the_long_move_is_back_once_the_piece_has_landed() -> None:
    cfg = PulsePerceptionConfig(exit_pulse_output_deg=2.0, exit_move_max_deg=30.0)
    landing = _fellUnseen()
    feeder = _feeder(landing)
    landing.observe(next_has_piece=True)
    assert feeder._exitMoveDeg(3, _state(8.0), cfg) == 30.0


def test_switched_off_the_camera_is_taken_at_its_word() -> None:
    cfg = PulsePerceptionConfig(exit_pulse_output_deg=2.0, exit_move_max_deg=30.0, ch3_fall_waits_for_landing=False)
    assert _feeder(_fellUnseen())._exitMoveDeg(3, _state(8.0), cfg) == 30.0


def test_c2_is_left_as_it_was() -> None:
    cfg = PulsePerceptionConfig(exit_pulse_output_deg=2.0, exit_move_max_deg=30.0)
    assert _feeder(_fellUnseen())._exitMoveDeg(2, _state(8.0), cfg) == 30.0
