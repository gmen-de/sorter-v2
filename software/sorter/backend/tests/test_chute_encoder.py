"""Tests for the chute's configurable position sensor (endstop vs. the
Octopus board's magnet-ring encoder). See the plan this was built from:
C:\\Users\\j.grell\\.claude\\plans\\calm-sparking-kettle.md (not committed,
local to the session that wrote this) and
software/firmware/octopus_control_board/ChuteEncoder.h for the firmware
side's own "not yet bench-calibrated" caveat that this mirrors.
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
from subsystems.distribution.chute import Chute


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
    """Just enough of StepperMotor's surface for Chute's homing dispatch."""

    def __init__(self):
        self.enabled = False
        self.position = 0
        self.stopped = True  # the homing poll loop exits immediately
        self.move_at_speed_calls: list[int] = []

    def move_at_speed(self, speed: int, **kwargs) -> bool:
        self.move_at_speed_calls.append(speed)
        return True

    @property
    def position_degrees(self) -> float:
        return float(self.position)


class _FakeChuteEncoder:
    def __init__(self, *, count: int = 0, count_at_index: int = 0, synced: bool = True):
        self._count = count
        self._count_at_index = count_at_index
        self._synced = synced
        self.clear_index_synced_calls = 0

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
        encoder = _FakeChuteEncoder(count=42, count_at_index=40, synced=True)
        chute = self._chute(encoder, stepper)

        self.assertTrue(chute.home())
        self.assertTrue(chute.homed)
        self.assertEqual(0, stepper.position)
        self.assertEqual(42, chute._encoder_zero_count)
        self.assertEqual(1, encoder.clear_index_synced_calls)
        # Homed at negative speed (same direction convention as the endstop case).
        self.assertTrue(any(s < 0 for s in stepper.move_at_speed_calls))

    def test_home_times_out_without_index_sync(self) -> None:
        stepper = _FakeStepper()
        stepper.stopped = False  # never reports stopped -> keeps "moving"
        encoder = _FakeChuteEncoder(synced=False)
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
