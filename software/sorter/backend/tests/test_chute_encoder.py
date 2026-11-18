"""Tests for the chute's configurable position sensor (endstop vs. the
Octopus board's magnet-ring encoder). Updated 2026-11-16 for the
bench-calibrated, firmware-driven homing/positioning (start_homing(),
move_to_step_pos()) added this session - see
software/firmware/octopus_control_board/src/main.cpp and
subsystems/distribution/chute.py's _homeEncoder()/_moveToAngleEncoder().
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hardware_settings.chute import (
    DEFAULT_CHUTE_POSITION_SENSOR,
    DEFAULT_ENCODER_COUNTS_PER_REVOLUTION,
    boardDefaults,
    readChuteSettings,
    saveChuteSettings,
)
from machine_platform.control_board import (
    BASICALLY_V1_2_DISTRIBUTION_PROFILE,
    OCTOPUS_DISTRIBUTION_PROFILE,
)
from subsystems.distribution.chute import GEAR_RATIO, Chute
from subsystems.distribution.position_check import checkPositionEncoder


class _NullLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


def _fake_gc() -> SimpleNamespace:
    return SimpleNamespace(logger=_NullLogger(), disable_chute=False)


class _FakeStepper:
    """Just enough of StepperMotor's surface for Chute's homing dispatch and
    for moveToAngle()/moveToAngleBlocking(). Simplified unit model: 1
    "microstep" == 1 motor-degree, so position_degrees == position exactly
    (chute.py already does the real GEAR_RATIO scaling itself; this fake
    just needs to track whatever degree amount it was told to move)."""

    def __init__(self):
        self.enabled = False
        self.position = 0.0
        self.stopped = True  # the homing poll loop exits immediately
        self.move_at_speed_calls: list[int] = []
        self.move_degrees_calls: list[float] = []

    def move_at_speed(self, speed: int, **kwargs) -> bool:
        self.move_at_speed_calls.append(speed)
        return True

    def set_speed_limits(self, *a, **k) -> None:
        pass

    def estimateMoveDegreesMs(self, delta_deg: float, max_speed: int = 5000) -> int:
        return int(abs(delta_deg))

    def move_degrees(self, delta_deg: float, **kwargs) -> bool:
        self.move_degrees_calls.append(delta_deg)
        self.position += delta_deg
        return True

    def move_degrees_blocking(self, delta_deg: float, timeout_ms: int = 5000) -> bool:
        return self.move_degrees(delta_deg)

    def microsteps_for_degrees(self, degrees: float) -> int:
        return int(round(degrees))

    @property
    def position_degrees(self) -> float:
        return float(self.position)


class _FakeChuteEncoder:
    """Mirrors the real ChuteEncoder's firmware-driven behavior: start_homing()
    and move_to_step_pos() are both fire-and-forget commands whose real
    effect (zeroing the stepper's position, or driving it to an absolute
    microstep target) the firmware carries out on its own - so the fake
    applies that same side effect directly to the fake stepper, the same
    way the real board eventually would, instead of the test polling a
    separate "is it done yet" signal across several fake ticks."""

    def __init__(
        self, *, count: int = 0, count_at_index: int = 0, synced: bool = True,
        stepper: "_FakeStepper | None" = None, homing_outcome: str = "succeeds",
    ):
        # homing_outcome: "succeeds" (default), "times_out" (firmware never
        # reports homed or failed - e.g. no index event ever seen), or
        # "fails" (firmware latches chute_homing_failed).
        self._count = count
        self._count_at_index = count_at_index
        self._synced = synced
        self._stepper = stepper
        self._homing_outcome = homing_outcome
        self.clear_index_synced_calls = 0
        self.start_homing_calls = 0
        self.move_to_step_pos_calls: list[int] = []
        self._homed = False
        self._homing_failed = False

    def get_count(self) -> int:
        return self._count

    def set_count(self, count: int) -> None:
        self._count = count

    def is_index_synced(self) -> bool:
        return self._synced

    def get_count_at_index(self) -> int:
        return self._count_at_index

    def clear_index_synced(self) -> None:
        self.clear_index_synced_calls += 1

    def start_homing(self) -> None:
        self.start_homing_calls += 1
        if self._homing_outcome == "times_out":
            self._homed = False
            self._homing_failed = False
            return
        if self._homing_outcome == "fails":
            self._homed = False
            self._homing_failed = True
            return
        if self._stepper is not None:
            self._stepper.position = 0.0
        self._homed = True
        self._homing_failed = False

    def get_homing_status(self) -> dict:
        return {"homed": self._homed, "active": False, "failed": self._homing_failed}

    def move_to_step_pos(self, target_usteps: int) -> None:
        self.move_to_step_pos_calls.append(target_usteps)
        if self._stepper is not None:
            # Absolute command, same units the real firmware/stepper share:
            # CHUTE_ENCODER_USTEPS_PER_REV microsteps == one full GEAR_RATIO
            # motor revolution. The fake stepper's "position" is plain motor
            # degrees (see _FakeStepper's docstring), so convert accordingly.
            from subsystems.distribution.chute import CHUTE_ENCODER_USTEPS_PER_REV, GEAR_RATIO

            self._stepper.position = target_usteps * (GEAR_RATIO * 360.0 / CHUTE_ENCODER_USTEPS_PER_REV)


def _empty_layout() -> SimpleNamespace:
    return SimpleNamespace(layers=[])


class BoardProfileDetectionTests(unittest.TestCase):
    def test_octopus_profile_defaults_to_encoder_sensor(self) -> None:
        self.assertEqual("encoder", OCTOPUS_DISTRIBUTION_PROFILE.chute_position_sensor)
        self.assertEqual({}, OCTOPUS_DISTRIBUTION_PROFILE.input_aliases)

    def test_older_boards_default_to_endstop_sensor(self) -> None:
        self.assertEqual("endstop", BASICALLY_V1_2_DISTRIBUTION_PROFILE.chute_position_sensor)


class ChuteConstructionTests(unittest.TestCase):
    def test_requires_exactly_one_of_home_pin_or_encoder(self) -> None:
        with self.assertRaises(ValueError):
            Chute(_fake_gc(), _FakeStepper(), None, _empty_layout())
        with self.assertRaises(ValueError):
            Chute(
                _fake_gc(), _FakeStepper(), object(), _empty_layout(),
                chute_encoder=_FakeChuteEncoder(),
            )

    def test_encoder_mode_reports_itself(self) -> None:
        c = Chute(
            _fake_gc(), _FakeStepper(), None, _empty_layout(),
            chute_encoder=_FakeChuteEncoder(),
        )
        self.assertEqual("encoder", c.chute_position_sensor)

    def test_endstop_mode_reports_itself(self) -> None:
        c = Chute(_fake_gc(), _FakeStepper(), object(), _empty_layout())
        self.assertEqual("endstop", c.chute_position_sensor)


class ChuteEncoderHomingTests(unittest.TestCase):
    def _chute(self, encoder: _FakeChuteEncoder, stepper: _FakeStepper) -> Chute:
        return Chute(
            _fake_gc(), stepper, None, _empty_layout(),
            chute_encoder=encoder,
            # Both zero so _backoffToFirstBin() is a no-op (see module docstring).
            first_section_offset_deg=0.0,
            section_width_deg=0.0,
        )

    def test_home_dispatches_to_encoder_and_zeroes_position(self) -> None:
        stepper = _FakeStepper()
        stepper.position = 12345  # pre-existing open-loop drift
        encoder = _FakeChuteEncoder(count=42, count_at_index=40, stepper=stepper)
        chute = self._chute(encoder, stepper)

        self.assertTrue(chute.home())
        self.assertTrue(chute.homed)
        self.assertEqual(0, stepper.position)
        self.assertEqual(42, chute._encoder_zero_count)
        self.assertEqual(1, encoder.start_homing_calls)

    def test_home_times_out_when_firmware_never_reports_homed(self) -> None:
        stepper = _FakeStepper()
        encoder = _FakeChuteEncoder(stepper=stepper, homing_outcome="times_out")
        chute = self._chute(encoder, stepper)

        # Patch the timeout low so the test doesn't actually wait 15s.
        import subsystems.distribution.chute as chute_module

        original_timeout = chute_module.HOME_TIMEOUT_MS
        chute_module.HOME_TIMEOUT_MS = 10
        try:
            self.assertFalse(chute.home())
        finally:
            chute_module.HOME_TIMEOUT_MS = original_timeout
        self.assertFalse(chute.homed)

    def test_home_fails_when_firmware_reports_homing_failed(self) -> None:
        stepper = _FakeStepper()
        encoder = _FakeChuteEncoder(stepper=stepper, homing_outcome="fails")
        chute = self._chute(encoder, stepper)

        self.assertFalse(chute.home())
        self.assertFalse(chute.homed)

    def test_encoder_properties_raise_in_endstop_mode(self) -> None:
        chute = Chute(_fake_gc(), _FakeStepper(), object(), _empty_layout())
        with self.assertRaises(RuntimeError):
            _ = chute.encoder_count
        with self.assertRaises(RuntimeError):
            _ = chute.encoder_synced

    def test_endstop_properties_raise_in_encoder_mode(self) -> None:
        chute = self._chute(_FakeChuteEncoder(), _FakeStepper())
        with self.assertRaises(RuntimeError):
            _ = chute.raw_endstop_active


class ChuteShortestPathTests(unittest.TestCase):
    """The reason for switching from endstop to encoder in the first place:
    once the chute can cross 0/360, moves should take the shorter way
    around, not just be unbounded.

    Since this session's firmware rework, the shortest-path WRAP itself is
    computed on the board (ChuteEncoder.move_to_step_pos(), bench-confirmed
    there - see software/firmware/octopus_control_board/src/main.cpp's
    chute_shortest_usteps_delta()), not in chute.py any more. What's left
    to verify at this layer is that moveToAngle() sends the correct
    ABSOLUTE target (in the stepper's own microstep frame) for a given
    chute-degree target, and that current_angle correctly reads back
    wherever the (fake, here instantly "arrived") stepper ends up."""

    def _chute(self, *, encoder: bool) -> tuple[Chute, _FakeStepper]:
        stepper = _FakeStepper()
        if encoder:
            chute = Chute(
                _fake_gc(), stepper, None, _empty_layout(),
                chute_encoder=_FakeChuteEncoder(stepper=stepper),
            )
        else:
            chute = Chute(_fake_gc(), stepper, object(), _empty_layout())
        return chute, stepper

    def test_encoder_mode_targets_the_absolute_microstep_position(self) -> None:
        from subsystems.distribution.chute import CHUTE_ENCODER_USTEPS_PER_REV

        chute, stepper = self._chute(encoder=True)
        stepper.position = 10.0 * GEAR_RATIO  # current_angle == 10°
        chute.moveToAngle(350.0)
        target_usteps = chute.chute_encoder.move_to_step_pos_calls[-1]
        self.assertEqual(round(350.0 / 360.0 * CHUTE_ENCODER_USTEPS_PER_REV), target_usteps)
        self.assertAlmostEqual(350.0, chute.current_angle, places=3)

    def test_endstop_mode_keeps_the_original_direct_path(self) -> None:
        chute, stepper = self._chute(encoder=False)
        stepper.position = 10.0 * GEAR_RATIO
        chute.moveToAngle(350.0)
        delta_deg = stepper.move_degrees_calls[-1] / GEAR_RATIO
        # Unchanged behavior: the chute can't cross zero, so it must still
        # take the long way around (+340°), exactly as before this change.
        self.assertAlmostEqual(340.0, delta_deg, places=3)

    def test_encoder_mode_also_works_approaching_from_the_other_side(self) -> None:
        from subsystems.distribution.chute import CHUTE_ENCODER_USTEPS_PER_REV

        chute, stepper = self._chute(encoder=True)
        stepper.position = 350.0 * GEAR_RATIO  # current_angle == 350°
        chute.moveToAngle(10.0)
        target_usteps = chute.chute_encoder.move_to_step_pos_calls[-1]
        self.assertEqual(round(10.0 / 360.0 * CHUTE_ENCODER_USTEPS_PER_REV), target_usteps)
        self.assertAlmostEqual(10.0, chute.current_angle, places=3)


class ChuteReachabilityTests(unittest.TestCase):
    def _chute(self, *, encoder: bool) -> Chute:
        kwargs = dict(
            first_section_offset_deg=355.0,
            section_width_deg=2.0,
            num_sections=1,
        )
        if encoder:
            return Chute(
                _fake_gc(), _FakeStepper(), None, _empty_layout(),
                chute_encoder=_FakeChuteEncoder(), **kwargs,
            )
        return Chute(_fake_gc(), _FakeStepper(), object(), _empty_layout(), **kwargs)

    def test_endstop_mode_has_a_dead_wedge_past_350(self) -> None:
        chute = self._chute(encoder=False)
        self.assertIsNone(chute.angleForVirtualBin(0, 0, 1))

    def test_encoder_mode_has_no_dead_wedge(self) -> None:
        chute = self._chute(encoder=True)
        angle = chute.angleForVirtualBin(0, 0, 1)
        self.assertIsNotNone(angle)
        self.assertAlmostEqual(356.0, angle, places=3)


class ChuteEncoderPositionCheckDriftTests(unittest.TestCase):
    def test_zero_drift_survives_a_full_revolution(self) -> None:
        stepper = _FakeStepper()
        encoder = _FakeChuteEncoder(count=0, count_at_index=0, stepper=stepper)
        chute = Chute(
            _fake_gc(), stepper, None, _empty_layout(),
            chute_encoder=encoder,
            first_section_offset_deg=0.0,
            section_width_deg=0.0,
        )
        self.assertTrue(chute.home())  # sets stepper.position = 0, zero_count = 0

        # Simulate one full, drift-free revolution: both the stepper's own
        # open-loop count and the encoder's running count advance by exactly
        # one revolution's worth. Before the fix this surfaces, comparing
        # against the 360-normalized current_angle (now ~0 again) instead
        # of this raw accumulation would have reported ~360° of bogus drift.
        stepper.position += 360.0 * GEAR_RATIO
        encoder.set_count(encoder.get_count() + chute.encoder_counts_per_revolution)

        check = checkPositionEncoder(chute)
        self.assertTrue(check.ok)
        self.assertAlmostEqual(0.0, check.drift_deg, places=3)


class ChuteSettingsPersistenceTests(unittest.TestCase):
    """Mirrors tests/test_endstop_config.py's ChuteConfigPersistenceTests
    pattern: point MACHINE_SPECIFIC_PARAMS_PATH at a throwaway temp file so
    saveChuteSettings() (which opens machine_toml.edit() before validating)
    never touches the real machine.toml."""

    def setUp(self) -> None:
        self._old_machine_params = os.environ.get("MACHINE_SPECIFIC_PARAMS_PATH")
        self._tmpdir = tempfile.TemporaryDirectory()
        self.machine_params_path = Path(self._tmpdir.name) / "machine_params.toml"
        self.machine_params_path.write_text("", encoding="utf-8")
        os.environ["MACHINE_SPECIFIC_PARAMS_PATH"] = str(self.machine_params_path)

    def tearDown(self) -> None:
        if self._old_machine_params is None:
            os.environ.pop("MACHINE_SPECIFIC_PARAMS_PATH", None)
        else:
            os.environ["MACHINE_SPECIFIC_PARAMS_PATH"] = self._old_machine_params
        self._tmpdir.cleanup()

    def test_defaults(self) -> None:
        settings = readChuteSettings({})
        self.assertEqual(DEFAULT_CHUTE_POSITION_SENSOR, settings.chute_position_sensor)
        self.assertEqual(DEFAULT_ENCODER_COUNTS_PER_REVOLUTION, settings.encoder_counts_per_revolution)

    def test_board_default_is_threaded_through(self) -> None:
        defaults = boardDefaults(
            OCTOPUS_DISTRIBUTION_PROFILE.input_aliases,
            OCTOPUS_DISTRIBUTION_PROFILE.chute_home_active_high,
            OCTOPUS_DISTRIBUTION_PROFILE.chute_position_sensor,
        )
        self.assertEqual("encoder", defaults["chute_position_sensor"])
        self.assertEqual("encoder", readChuteSettings({}, **defaults).chute_position_sensor)

    def test_machine_toml_overrides_the_board_default(self) -> None:
        defaults = boardDefaults(
            OCTOPUS_DISTRIBUTION_PROFILE.input_aliases,
            OCTOPUS_DISTRIBUTION_PROFILE.chute_home_active_high,
            OCTOPUS_DISTRIBUTION_PROFILE.chute_position_sensor,
        )
        saved = {"chute": {"chute_position_sensor": "endstop"}}
        self.assertEqual("endstop", readChuteSettings(saved, **defaults).chute_position_sensor)

    def test_invalid_sensor_value_is_rejected_and_falls_back(self) -> None:
        saved = {"chute": {"chute_position_sensor": "laser"}}
        self.assertEqual(DEFAULT_CHUTE_POSITION_SENSOR, readChuteSettings(saved).chute_position_sensor)

    def test_save_rejects_invalid_sensor_value(self) -> None:
        with self.assertRaises(ValueError):
            saveChuteSettings({"chute_position_sensor": "laser"})


if __name__ == "__main__":
    unittest.main()
