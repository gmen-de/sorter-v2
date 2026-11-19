"""FAN5 as a dedicated CPU cooler: on/off from the Orange Pi's own SoC
temperature, independent of any sorting hardware state (this cools the
compute board, not the sorter).

The RK3588S's built-in thermal governor throttles the SoC (DVFS clock cuts)
at 85C (its second passive trip point - bench-confirmed on this machine's
kernel, `/sys/class/thermal/thermal_zone0/trip_point_1_temp` = 85000
millidegrees, matching upstream's RK3588(S) devicetree default) - but that's
far hotter than this board should be left to idle at, so FAN5's own target is
much lower: on at 55C, off again at 50C (a 5C hysteresis band) - plus a
minimum time between switches (MIN_SWITCH_INTERVAL_S) so a temperature
sitting right at a boundary can't flip the fan every poll.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from global_config import GlobalConfig

THERMAL_ZONE_PATH = "/sys/class/thermal/thermal_zone0/temp"
SOC_THROTTLE_C = 85.0
FAN_ON_AT_OR_ABOVE_C = 55.0
FAN_OFF_BELOW_C = 50.0
MIN_SWITCH_INTERVAL_S = 60.0
AUTO_FAN_INDEX = 5
POLL_INTERVAL_S = 5.0


def readCpuTempC() -> Optional[float]:
    try:
        with open(THERMAL_ZONE_PATH, "r") as handle:
            return round(int(handle.read().strip()) / 1000.0, 1)
    except Exception:
        return None


class CpuFanControl:
    def __init__(self, gc: GlobalConfig):
        self._gc = gc
        # Matches the firmware's own boot default (every fan output starts at
        # full speed, see initialize_hardware() in main.cpp) so this control
        # never writes a redundant "on" the first time it agrees with it.
        self._fan_on = True
        # 0.0 (not time.monotonic()'s current value) so the very first real
        # switch is never held back by MIN_SWITCH_INTERVAL_S.
        self._last_switch_at = 0.0

    @property
    def fan_on(self) -> bool:
        return self._fan_on

    def run(self) -> None:
        import server.shared_state as shared_state

        while True:
            try:
                self.poll(shared_state.getActiveIRL())
            except Exception as e:
                self._gc.logger.warning(f"CPU fan control poll error: {e}")
            time.sleep(POLL_INTERVAL_S)

    def poll(self, irl) -> None:
        temp_c = readCpuTempC()
        if temp_c is None:
            return
        want_on = self._fan_on
        if temp_c < FAN_OFF_BELOW_C:
            want_on = False
        elif temp_c >= FAN_ON_AT_OR_ABOVE_C:
            want_on = True
        if want_on == self._fan_on:
            return
        now = time.monotonic()
        if now - self._last_switch_at < MIN_SWITCH_INTERVAL_S:
            return  # Debounced; the wanted state is re-checked next poll.
        from server.routers.fans import fanOutputs

        output = fanOutputs(irl)
        if output is None:
            return  # Hardware not initialized yet; retried next poll.
        from hardware.sorter_interface import DIGITAL_OUTPUT_DUTY_MAX

        output[AUTO_FAN_INDEX].setDuty(DIGITAL_OUTPUT_DUTY_MAX if want_on else 0)
        self._fan_on = want_on
        self._last_switch_at = now
        self._gc.logger.info(
            f"CPU fan control: FAN{AUTO_FAN_INDEX} turned {'on' if want_on else 'off'} at {temp_c}°C "
            f"(target {FAN_ON_AT_OR_ABOVE_C}°C, SoC throttles at {SOC_THROTTLE_C}°C)"
        )


_instance: Optional[CpuFanControl] = None
_lock = threading.Lock()


def start(gc: GlobalConfig) -> Optional[CpuFanControl]:
    """Starts the control loop, unless this machine has no readable SoC
    temperature (e.g. a dev box) - best-effort, same as status_ping.py."""
    global _instance
    if readCpuTempC() is None:
        gc.logger.info("CPU fan control not started: no readable SoC temperature on this machine.")
        return None
    with _lock:
        if _instance is None:
            _instance = CpuFanControl(gc)
            threading.Thread(target=_instance.run, daemon=True, name="cpu-fan-control").start()
        return _instance


def getActive() -> Optional[CpuFanControl]:
    with _lock:
        return _instance
