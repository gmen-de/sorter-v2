"""Flashing for the esp32_ble_servo_bridge board, over USB-serial via esptool.

Separate from `firmware_flash.py` on purpose: that module's whole shape
(RPI-RP2 drag-and-drop vs. DFU, re-identifying the board afterward over the
sorter's own MCU bus protocol) is specific to the Octopus/RP2040 control
boards. The ESP32 speaks no such protocol — it only ever runs the BLE gate
bridge firmware — so it is found purely by USB vendor/product id (the same
`_ESP32_USB_IDS` the safety interlock in `hardware/bus.py` watches for) and
flashed with the standard Espressif tool.

Plugging an ESP32 in over USB to flash it is exactly the condition that
interlock exists for: `hardware.bus.startEsp32SafetyWatch()` latches the
stepper lock within a few seconds of the port appearing, before this module
ever touches it, and that lock does not clear until the backend restarts —
by design, so a flash (or the reset it causes) can never catch a chute mid
home. See the long comment on `lockSteppersForSafety` in `hardware/bus.py`.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from global_config import GlobalConfig
from hardware.bus import _ESP32_USB_IDS

PORT_WAIT_TIMEOUT_S = 30.0
FLASH_TIMEOUT_S = 120.0
FLASH_BAUD = 460800
# A merged single image (bootloader + partition table + boot_app0 + app),
# produced by `esptool merge_bin` in CI — see .github/workflows/firmware-release.yml.
# One file, one offset, nothing board-specific to get wrong on this end.
FLASH_OFFSET = "0x0"

ProgressFn = Callable[[float], None]


class FlashCancelled(Exception):
    pass


class FlashError(Exception):
    pass


def find_esp32_port() -> Optional[str]:
    import serial.tools.list_ports

    for port in serial.tools.list_ports.comports():
        if (port.vid, port.pid) in _ESP32_USB_IDS:
            return port.device
    return None


def wait_for_esp32_port(cancel: threading.Event, timeout_s: float = PORT_WAIT_TIMEOUT_S) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise FlashCancelled()
        port = find_esp32_port()
        if port:
            return port
        time.sleep(0.5)
    raise FlashError(
        f"No ESP32 (BLE gate bridge) found on USB within {timeout_s:.0f}s. "
        "Plug it in over USB-C and retry."
    )


def flash_esp32(
    gc: GlobalConfig,
    bin_path: str,
    port: str,
    cancel: threading.Event,
    on_progress: ProgressFn,
) -> None:
    """Runs `esptool write_flash`. Most ESP32 dev boards auto-reset into the
    bootloader via the USB-serial adapter's DTR/RTS lines, which esptool
    drives itself — no BOOT0-jumper dance like the Octopus. Not yet
    confirmed on the actual Waveshare "Servo Driver with ESP32" board's auto-
    reset circuit (see the firmware's own README "known limitations"); if
    esptool times out waiting for sync, the board likely needs its BOOT
    button held by hand during the reset esptool performs at the start of
    the command.
    """
    if cancel.is_set():
        raise FlashCancelled()
    on_progress(0.0)
    cmd = [
        sys.executable,
        "-m",
        "esptool",
        "--chip",
        "esp32",
        "--port",
        port,
        "--baud",
        str(FLASH_BAUD),
        "write_flash",
        FLASH_OFFSET,
        bin_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FLASH_TIMEOUT_S)
    except FileNotFoundError:
        raise FlashError("esptool is not installed on this machine.")
    except subprocess.TimeoutExpired:
        raise FlashError(f"esptool did not finish within {FLASH_TIMEOUT_S:.0f}s.")
    if proc.returncode != 0:
        raise FlashError(
            f"esptool failed: {(proc.stderr or proc.stdout).strip() or 'unknown error'}"
        )
    gc.logger.info(f"esp32 flash: {bin_path} -> {port} done")
    on_progress(1.0)
