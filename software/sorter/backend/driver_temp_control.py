"""Stepper driver temperatures (Octopus board): read, keep, cool, protect.

Each stepper driver has an NTC thermistor (100k, B3950) on its heatsink,
plugged into the thermistor input that goes with its driver's motor socket
(see STEPPER_THERMISTOR_PINS in the firmware's hwcfg_octopus.h): Motor3 TB,
Motor4 TH0, Motor5 TH1, Motor6 TH2, Motor7 TH3. The firmware turns the reading into tenths of
a degree; this loop polls every driver every POLL_INTERVAL_S and

- keeps the newest reading per driver and a history (HISTORY_S at
  HISTORY_STEP_S) for the Octopus diagnostic page,
- drives one fan header from the hottest driver: off below fan_start_c, then
  a ramp from fan_min_duty_percent up to full at fan_full_c (it turns off
  again only FAN_OFF_HYSTERESIS_C below fan_start_c, so it doesn't chatter),
- and at shutdown_c pauses the machine and holds it with a resume-blocking
  `driver_overtemp` incident, which clears itself once every driver is back
  below resume_below_c.

The settings live in machine.toml's [driver_temperature]. A driver without a
sensor plugged in reads "no_sensor" and is simply left out; a board whose
firmware has no READ_TEMPERATURE yet is retried every UNSUPPORTED_RETRY_S.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Optional

from global_config import GlobalConfig

DRIVER_OVERTEMP_INCIDENT_KIND = "driver_overtemp"

POLL_INTERVAL_S = 2.0
HISTORY_STEP_S = 10.0
HISTORY_S = 24 * 3600
UNSUPPORTED_RETRY_S = 60.0
FAN_OFF_HYSTERESIS_C = 3.0
# A duty change smaller than this isn't written (no bus traffic for noise).
FAN_DUTY_STEP_PERCENT = 5

# Thermistor input per stepper channel, as printed on the board: ch0 chute
# (Motor5), ch1 c_channel_1 (Motor6), ch2 c_channel_3 (Motor7), ch3 carousel
# (Motor3), ch4 c_channel_2 (Motor4).
THERMISTOR_INPUT_LABELS = ("TH1", "TH2", "TH3", "TB", "TH0")

BOARD_KEY = "DISTRIBUTION MB"
SETTINGS_SECTION = "driver_temperature"

DEFAULT_SETTINGS: dict[str, Any] = {
    # Fan header (FAN0-FAN5) cooling the drivers; -1 for none.
    "fan_index": 0,
    "fan_start_c": 40.0,
    "fan_full_c": 60.0,
    "fan_min_duty_percent": 30,
    "shutdown_c": 80.0,
    "resume_below_c": 70.0,
}


def getSettings() -> dict[str, Any]:
    import machine_toml

    section = machine_toml.read().get(SETTINGS_SECTION)
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(section, dict):
        settings.update({k: v for k, v in section.items() if k in DEFAULT_SETTINGS})
    return settings


def validateSettings(settings: dict[str, Any]) -> Optional[str]:
    from server.routers.fans import FAN_COUNT
    import cpu_fan_control

    fan_index = settings["fan_index"]
    if fan_index != -1 and not 0 <= fan_index < FAN_COUNT:
        return f"fan_index must be -1 (none) or 0-{FAN_COUNT - 1}"
    if fan_index == cpu_fan_control.AUTO_FAN_INDEX:
        return f"FAN{fan_index} already cools the Orange Pi's CPU"
    if not 0 <= settings["fan_min_duty_percent"] <= 100:
        return "fan_min_duty_percent must be 0-100"
    if not settings["fan_start_c"] < settings["fan_full_c"]:
        return "fan_start_c must be below fan_full_c"
    if not settings["resume_below_c"] < settings["shutdown_c"]:
        return "resume_below_c must be below shutdown_c"
    if not settings["fan_full_c"] <= settings["shutdown_c"]:
        return "fan_full_c must not be above shutdown_c"
    return None


def setSettings(updates: dict[str, Any]) -> dict[str, Any]:
    """Merges updates over the current settings; raises ValueError when the
    result doesn't make sense."""
    import machine_toml

    merged = getSettings()
    for key, value in updates.items():
        if key in DEFAULT_SETTINGS and value is not None:
            merged[key] = type(DEFAULT_SETTINGS[key])(value)
    error = validateSettings(merged)
    if error is not None:
        raise ValueError(error)
    with machine_toml.edit() as config:
        config[SETTINGS_SECTION] = merged
    return merged


def fanDutyPercent(hottest_c: float, fan_on: bool, settings: dict[str, Any]) -> int:
    start = settings["fan_start_c"]
    full = settings["fan_full_c"]
    if hottest_c < (start - FAN_OFF_HYSTERESIS_C if fan_on else start):
        return 0
    if hottest_c >= full:
        return 100
    share = max(0.0, (hottest_c - start) / (full - start))
    low = settings["fan_min_duty_percent"]
    return round(low + (100 - low) * share)


class DriverTempControl:
    def __init__(self, gc: GlobalConfig):
        self._gc = gc
        self._lock = threading.Lock()
        self._readings: list[dict[str, Any]] = []
        self._history: deque[tuple[float, list[Optional[float]]]] = deque(
            maxlen=int(HISTORY_S / HISTORY_STEP_S)
        )
        self._last_history_at = 0.0
        self._unsupported_until = 0.0
        self._fan_duty: Optional[int] = None
        self._fan_index_driven: Optional[int] = None
        self._overtemp: list[str] = []

    # -- reading -----------------------------------------------------------

    def snapshot(self, history_s: float = 0.0) -> dict[str, Any]:
        with self._lock:
            readings = [dict(r) for r in self._readings]
            cutoff = time.time() - max(0.0, history_s)
            rows = [row for row in self._history if row[0] >= cutoff] if history_s > 0 else []
            return {
                "available": bool(readings),
                "drivers": readings,
                "fan": {"index": self._fan_index_driven, "duty_percent": self._fan_duty},
                "overtemp": list(self._overtemp),
                "history": {
                    "step_s": HISTORY_STEP_S,
                    "t": [row[0] for row in rows],
                    "temps_c": [row[1] for row in rows],
                },
            }

    @property
    def fan_index(self) -> Optional[int]:
        return self._fan_index_driven

    def run(self) -> None:
        import server.shared_state as shared_state

        while True:
            try:
                self.poll(shared_state.getActiveIRL())
            except Exception as e:
                self._gc.logger.warning(f"Driver temperature poll error: {e}")
            time.sleep(POLL_INTERVAL_S)

    def _board(self, irl) -> Any:
        if irl is None:
            return None
        interfaces = getattr(irl, "interfaces", None)
        board = interfaces.get(BOARD_KEY) if isinstance(interfaces, dict) else None
        if board is None:
            return None
        try:
            hw = board.get_observability_info().get("hw", "")
        except Exception:
            return None
        return board if str(hw).startswith("octopus") else None

    def _read(self, board) -> list[dict[str, Any]]:
        readings: list[dict[str, Any]] = []
        for stepper in board.steppers:
            channel = stepper.channel
            entry: dict[str, Any] = {
                "channel": channel,
                "stepper": stepper.name,
                "input": THERMISTOR_INPUT_LABELS[channel] if channel < len(THERMISTOR_INPUT_LABELS) else None,
                "state": "error",
                "temp_c": None,
            }
            try:
                entry["state"], entry["temp_c"] = stepper.read_driver_temperature()
            except Exception as e:
                # The board answered with a NACK ("Invalid command"): its
                # firmware predates READ_TEMPERATURE.
                if "Error response received" in str(e):
                    raise _Unsupported() from e
                self._gc.logger.debug(f"Driver temperature read failed on ch{channel}: {e}")
            readings.append(entry)
        return readings

    def poll(self, irl) -> None:
        now = time.monotonic()
        board = self._board(irl)
        if board is None or now < self._unsupported_until:
            with self._lock:
                self._readings = []
            return
        try:
            readings = self._read(board)
        except _Unsupported:
            self._unsupported_until = now + UNSUPPORTED_RETRY_S
            self._gc.logger.info(
                "Driver temperatures: this board's firmware has no READ_TEMPERATURE yet "
                f"(needs firmware v0.8.7 or newer); retrying in {UNSUPPORTED_RETRY_S:.0f}s."
            )
            return

        settings = getSettings()
        temps = [r["temp_c"] for r in readings if r["state"] == "ok"]
        wall = time.time()
        with self._lock:
            self._readings = readings
            if wall - self._last_history_at >= HISTORY_STEP_S:
                self._history.append((wall, [r["temp_c"] if r["state"] == "ok" else None for r in readings]))
                self._last_history_at = wall
        if not temps:
            self._syncIncident([], settings)
            return
        self._driveFan(irl, max(temps), settings)
        hot = [r["stepper"] for r in readings if r["state"] == "ok" and r["temp_c"] >= settings["shutdown_c"]]
        still_warm = [
            r["stepper"] for r in readings if r["state"] == "ok" and r["temp_c"] >= settings["resume_below_c"]
        ]
        self._syncIncident(hot if hot else (still_warm if self._overtemp else []), settings, readings)

    # -- fan ---------------------------------------------------------------

    def _driveFan(self, irl, hottest_c: float, settings: dict[str, Any]) -> None:
        fan_index = int(settings["fan_index"])
        if fan_index < 0:
            self._fan_index_driven = None
            self._fan_duty = None
            return
        if fan_index != self._fan_index_driven:
            self._fan_index_driven = fan_index
            self._fan_duty = None
        duty = fanDutyPercent(hottest_c, bool(self._fan_duty), settings)
        if self._fan_duty is not None and duty != 0 and duty != 100 and abs(duty - self._fan_duty) < FAN_DUTY_STEP_PERCENT:
            return
        if duty == self._fan_duty:
            return
        from server.routers.fans import fanOutputs
        from hardware.sorter_interface import DIGITAL_OUTPUT_DUTY_MAX

        outputs = fanOutputs(irl)
        if outputs is None:
            return
        outputs[fan_index].setDuty(round(duty * DIGITAL_OUTPUT_DUTY_MAX / 100))
        if (self._fan_duty or 0) == 0 or duty == 0:
            self._gc.logger.info(f"Driver fan FAN{fan_index} at {duty}% (hottest driver {hottest_c:.1f}°C)")
        self._fan_duty = duty

    # -- protection --------------------------------------------------------

    def _syncIncident(
        self, overtemp: list[str], settings: dict[str, Any], readings: Optional[list[dict[str, Any]]] = None
    ) -> None:
        rising = sorted(set(overtemp) - set(self._overtemp))
        self._overtemp = sorted(overtemp)
        rs = self._gc.runtime_stats
        active = rs.activeIncident()
        active_kind = active.get("kind") if isinstance(active, dict) else None
        if overtemp:
            if rising:
                self._gc.logger.error(
                    f"Driver over temperature on {rising} (limit {settings['shutdown_c']}°C): pausing the machine."
                )
            self._requestPauseIfRunning()
            if active is None or active_kind == DRIVER_OVERTEMP_INCIDENT_KIND:
                temps = {
                    r["stepper"]: r["temp_c"] for r in (readings or []) if r["stepper"] in overtemp
                }
                detail = ", ".join(
                    f"{name} {temps[name]:.0f}°C" if temps.get(name) is not None else name for name in self._overtemp
                )
                rs.setActiveIncident(
                    {
                        "kind": DRIVER_OVERTEMP_INCIDENT_KIND,
                        "channel": self._overtemp[0],
                        "steppers": list(self._overtemp),
                        "status": "needs_manual_fix",
                        "operator_message": (
                            f"Stepper driver over temperature: {detail}. The machine is paused until every "
                            f"driver is below {settings['resume_below_c']:.0f}°C again; check its fan and heatsink."
                        ),
                    }
                )
        elif active_kind == DRIVER_OVERTEMP_INCIDENT_KIND:
            self._gc.logger.info("Driver temperatures back to normal: clearing the over-temperature hold.")
            rs.clearActiveIncident(kind=DRIVER_OVERTEMP_INCIDENT_KIND)

    def _requestPauseIfRunning(self) -> None:
        try:
            import server.shared_state as shared_state

            controller = shared_state.controller_ref
            if controller is None:
                return
            if getattr(getattr(controller, "state", None), "value", None) != "running":
                return
            q = shared_state.command_queue
            if q is None:
                return
            from defs.events import PauseCommandData, PauseCommandEvent

            q.put(PauseCommandEvent(tag="pause", data=PauseCommandData()))
        except Exception:
            pass


class _Unsupported(Exception):
    pass


_instance: Optional[DriverTempControl] = None
_start_lock = threading.Lock()


def start(gc: GlobalConfig) -> DriverTempControl:
    global _instance
    with _start_lock:
        if _instance is None:
            _instance = DriverTempControl(gc)
            threading.Thread(target=_instance.run, daemon=True, name="driver-temp-control").start()
        return _instance


def getActive() -> Optional[DriverTempControl]:
    with _start_lock:
        return _instance
