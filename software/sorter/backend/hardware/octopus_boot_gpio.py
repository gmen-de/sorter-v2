"""Software-triggered BOOT0/RESET for the Octopus control board, over two
Orange Pi GPIO lines wired to the board's J75 BOOT0 jumper and its EXP2
reset header. Lets a firmware flash enter DFU mode on its own, instead of
needing someone to set a jumper and press a reset button by hand.

Wiring (Orange Pi 5 Pro, 40-pin header) — verified 2026-11-18 by actually
driving the Octopus from "STM Virtual COM Port" into "STM Device in DFU
Mode" and back, watched on `lsusb`:
  - header pin 9  (GND)             -> Octopus EXP2 pin 9 (GND)
  - header pin 11 (sysfs GPIO 138)  -> Octopus J75 BOOT0 signal pin
  - header pin 13 (sysfs GPIO 139)  -> Octopus EXP2 pin 8 (RESET)
138/139 = RK3588 GPIO bank 4 (gpiochip128 = 4*32), offsets 10/11 (B2/B3).

Uses the legacy /sys/class/gpio interface, not libgpiod (not installed on
this machine) — fine for a couple of pins toggled a few times per flash,
not a hot path. Entirely optional: a flash that can't use this (wiring not
present, sysfs unavailable) falls back to the existing manual BOOT0/reset
instructions — see server/routers/firmware.py.
"""

from __future__ import annotations

import os
import time

BOOT0_GPIO = 138
RESET_GPIO = 139
SYSFS_GPIO_DIR = "/sys/class/gpio"
RESET_PULSE_S = 0.3
BOOT_SETTLE_S = 1.5


def _export(gpio: int) -> None:
    path = f"{SYSFS_GPIO_DIR}/gpio{gpio}"
    if os.path.isdir(path):
        return
    with open(f"{SYSFS_GPIO_DIR}/export", "w") as f:
        f.write(str(gpio))
    deadline = time.monotonic() + 2.0
    while not os.path.isdir(path):
        if time.monotonic() > deadline:
            raise RuntimeError(f"GPIO {gpio} did not appear in sysfs after export")
        time.sleep(0.02)


def _unexport(gpio: int) -> None:
    try:
        with open(f"{SYSFS_GPIO_DIR}/unexport", "w") as f:
            f.write(str(gpio))
    except OSError:
        pass


def _set_direction(gpio: int, direction: str) -> None:
    with open(f"{SYSFS_GPIO_DIR}/gpio{gpio}/direction", "w") as f:
        f.write(direction)


def _set_value(gpio: int, value: int) -> None:
    with open(f"{SYSFS_GPIO_DIR}/gpio{gpio}/value", "w") as f:
        f.write(str(value))


def available() -> bool:
    """Whether the sysfs GPIO interface exists at all — not whether the
    BOOT0/RESET wiring is actually connected, which there's no way to tell
    short of the DFU-entry attempt itself."""
    return os.path.isdir(SYSFS_GPIO_DIR)


def enter_dfu() -> None:
    """BOOT0 high, then one reset pulse. The board enumerates as a DFU
    device (0483:df11) within ~1-2s if the wiring is connected; the caller
    should still wait for it rather than assume `BOOT_SETTLE_S` is enough."""
    _export(BOOT0_GPIO)
    _export(RESET_GPIO)
    _set_direction(BOOT0_GPIO, "out")
    _set_value(BOOT0_GPIO, 1)
    _set_direction(RESET_GPIO, "out")
    _set_value(RESET_GPIO, 0)
    time.sleep(RESET_PULSE_S)
    _set_direction(RESET_GPIO, "in")  # release reset back to its pull-up
    time.sleep(BOOT_SETTLE_S)


def release_boot0() -> None:
    """BOOT0 low and both lines unexported. Called after a successful flash:
    a DFU "leave" is a software jump to the app (not a hardware reset), so
    the board is already running the new app by the time this runs — this
    just makes sure a future power-cycle/reset doesn't land back in DFU."""
    try:
        _set_value(BOOT0_GPIO, 0)
    except OSError:
        pass
    _unexport(BOOT0_GPIO)
    _unexport(RESET_GPIO)


def pulse_reset() -> None:
    """One reset pulse with BOOT0 left as-is. Used for the recovery path:
    forcing a reboot without touching BOOT0's current state."""
    _export(RESET_GPIO)
    _set_direction(RESET_GPIO, "out")
    _set_value(RESET_GPIO, 0)
    time.sleep(RESET_PULSE_S)
    _set_direction(RESET_GPIO, "in")
    _unexport(RESET_GPIO)
