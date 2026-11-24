import logging

import pytest

import driver_temp_control as dtc
from hardware.sorter_interface import DIGITAL_OUTPUT_DUTY_MAX


SETTINGS = dict(dtc.DEFAULT_SETTINGS)


class FakeStats:
    def __init__(self):
        self.incident = None

    def activeIncident(self):
        return self.incident

    def setActiveIncident(self, incident):
        self.incident = incident

    def clearActiveIncident(self, kind=None):
        if self.incident and (kind is None or self.incident.get("kind") == kind):
            self.incident = None


class FakeGc:
    def __init__(self):
        self.logger = logging.getLogger("test")
        self.runtime_stats = FakeStats()


class FakeStepper:
    def __init__(self, channel, reading):
        self.channel = channel
        self.name = f"stepper{channel}"
        self.reading = reading

    def read_driver_temperature(self):
        if isinstance(self.reading, Exception):
            raise self.reading
        return self.reading


class FakeOutput:
    def __init__(self):
        self.duty = None

    def setDuty(self, duty):
        self.duty = duty


class FakeBoard:
    def __init__(self, readings, hw="octopus_v1_1"):
        self.steppers = [FakeStepper(i, r) for i, r in enumerate(readings)]
        self.digital_outputs = [FakeOutput() for _ in range(9)]
        self.hw = hw

    def get_observability_info(self):
        return {"hw": self.hw}


class FakeIrl:
    def __init__(self, board):
        self.interfaces = {dtc.BOARD_KEY: board}


@pytest.fixture
def control(monkeypatch):
    monkeypatch.setattr(dtc, "getSettings", lambda: dict(SETTINGS))
    pauses = []
    c = dtc.DriverTempControl(FakeGc())
    monkeypatch.setattr(c, "_requestPauseIfRunning", lambda: pauses.append(1))
    c.pauses = pauses
    return c


def test_fan_duty_ramp_and_hysteresis():
    assert dtc.fanDutyPercent(30.0, False, SETTINGS) == 0
    assert dtc.fanDutyPercent(40.0, False, SETTINGS) == 30
    assert dtc.fanDutyPercent(50.0, False, SETTINGS) == 65
    assert dtc.fanDutyPercent(60.0, False, SETTINGS) == 100
    # Running fan stays on until 3 degrees below the start temperature.
    assert dtc.fanDutyPercent(38.0, True, SETTINGS) == 30
    assert dtc.fanDutyPercent(36.9, True, SETTINGS) == 0


def test_poll_reads_drives_fan_and_skips_missing_sensors(control):
    board = FakeBoard([("ok", 45.0), ("no_sensor", None), ("ok", 50.0), ("shorted", None), ("ok", 30.0)])
    control.poll(FakeIrl(board))
    snap = control.snapshot(history_s=60)
    assert snap["available"]
    assert [d["state"] for d in snap["drivers"]] == ["ok", "no_sensor", "ok", "shorted", "ok"]
    assert [d["input"] for d in snap["drivers"]] == ["TH1", "TH2", "TH3", "TB", "TH0"]
    assert snap["history"]["temps_c"] == [[45.0, None, 50.0, None, 30.0]]
    fan0 = board.digital_outputs[3]
    assert fan0.duty == round(65 * DIGITAL_OUTPUT_DUTY_MAX / 100)
    assert control.pauses == []
    assert control._gc.runtime_stats.incident is None


def test_overtemp_pauses_holds_and_clears_below_resume(control):
    board = FakeBoard([("ok", 82.0), ("ok", 40.0)])
    irl = FakeIrl(board)
    stats = control._gc.runtime_stats
    control.poll(irl)
    assert control.pauses
    assert stats.incident["kind"] == dtc.DRIVER_OVERTEMP_INCIDENT_KIND
    assert stats.incident["steppers"] == ["stepper0"]
    assert board.digital_outputs[3].duty == DIGITAL_OUTPUT_DUTY_MAX
    # Cooler but still above resume_below_c: the hold stays.
    board.steppers[0].reading = ("ok", 75.0)
    control.poll(irl)
    assert stats.incident is not None
    board.steppers[0].reading = ("ok", 65.0)
    control.poll(irl)
    assert stats.incident is None


def test_overtemp_never_replaces_another_incident(control):
    stats = control._gc.runtime_stats
    stats.incident = {"kind": "stepper_stall"}
    control.poll(FakeIrl(FakeBoard([("ok", 90.0)])))
    assert stats.incident == {"kind": "stepper_stall"}
    assert control.pauses


def test_old_firmware_is_left_alone(control):
    from hardware.bus import MCUBusError

    board = FakeBoard([MCUBusError("Error response received, command: 0xa3, payload: b'Invalid command 35'")])
    control.poll(FakeIrl(board))
    assert not control.snapshot()["available"]
    assert control._unsupported_until > 0


def test_other_boards_are_ignored(control):
    board = FakeBoard([("ok", 90.0)], hw="basically_v1_2")
    control.poll(FakeIrl(board))
    assert not control.snapshot()["available"]
    assert control.pauses == []


def test_settings_validation():
    bad = dict(SETTINGS, fan_index=5)
    assert "CPU" in dtc.validateSettings(bad)
    assert dtc.validateSettings(dict(SETTINGS, resume_below_c=90.0)) is not None
    assert dtc.validateSettings(dict(SETTINGS, fan_start_c=70.0)) is not None
    assert dtc.validateSettings(SETTINGS) is None
