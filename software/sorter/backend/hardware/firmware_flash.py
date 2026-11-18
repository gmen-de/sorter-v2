from __future__ import annotations

import glob
import json
import os
import platform
import struct
import subprocess
import threading
import time
from typing import Callable, Optional

from global_config import GlobalConfig
from hardware.bus import BaseCommandCode, MCUBus, MCUDevice

UF2_MAGIC_START_0 = 0x0A324655
UF2_MAGIC_START_1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_BLOCK_SIZE = 512
UF2_FAMILY_RP2040 = 0xE48BFF56

DFU_USB_ID = "0483:df11"
DFU_WAIT_TIMEOUT_S = 120.0  # the user has to physically set BOOT0 + press reset
DFU_FLASH_TIMEOUT_S = 60.0

LINUX_MOUNT_POINT = "/mnt/rpi-rp2-flash"
BOOTLOADER_MOUNT_TIMEOUT_S = 30.0
BOOTLOADER_UNMOUNT_TIMEOUT_S = 20.0
SERIAL_REAPPEAR_TIMEOUT_S = 15.0
# How much longer the serial wait holds its clock while the RPI-RP2 drive is
# still there: a board behind a busy USB hub can take most of a minute to leave
# the bootloader after the copy.
BOOTLOADER_LINGER_TIMEOUT_S = 60.0
COPY_CHUNK_SIZE = 64 * 1024
# The bus default (100ms) is tuned for the hot control loop, where a late reply
# is better dropped than waited on. Flashing runs inside the same busy backend
# process, and the flash thread can lose the GIL to vision work for longer than
# that — long enough to read a frame back in pieces and report a perfectly
# healthy board as "blank or wedged". Identification happens a handful of times
# per flash, so buy the headroom.
IDENTIFY_READ_TIMEOUT_S = 1.0

ProgressFn = Callable[[float], None]


class FlashCancelled(Exception):
    pass


class FlashError(Exception):
    pass


def validateUf2(data: bytes) -> str | None:
    if len(data) < UF2_BLOCK_SIZE:
        return "file is smaller than one UF2 block (512 bytes)"
    if len(data) % UF2_BLOCK_SIZE != 0:
        return f"file size {len(data)} is not a multiple of the 512-byte UF2 block size"
    magic0, magic1 = struct.unpack_from("<II", data, 0)
    if magic0 != UF2_MAGIC_START_0 or magic1 != UF2_MAGIC_START_1:
        return "missing UF2 magic — this is not a UF2 file"
    (magic_end,) = struct.unpack_from("<I", data, UF2_BLOCK_SIZE - 4)
    if magic_end != UF2_MAGIC_END:
        return "corrupt first UF2 block (bad end magic)"
    flags, _, _, _, _, family = struct.unpack_from("<IIIIII", data, 8)
    family_present = bool(flags & 0x00002000)
    if family_present and family != UF2_FAMILY_RP2040:
        return f"UF2 family id {family:#010x} is not RP2040 — wrong target chip"
    return None


def isOctopusVariant(info: dict | None) -> bool:
    """Whether a probed/identified board's own reported firmware variant is
    the Octopus port (see software/firmware/octopus_control_board) — its
    FIRMWARE_VARIANT build flag, not a filename convention. This drives the
    automatic choice between UF2 drag-and-drop and DFU flashing: the same
    thing that boots differently on different hardware should not need the
    person flashing it to know which recovery procedure applies."""
    if not info:
        return False
    version = info.get("version") or {}
    variant = (version.get("variant") or "") if isinstance(version, dict) else ""
    return "octopus" in variant.lower()


def dfuDevicePresent() -> bool:
    """Whether a board is currently sitting in the STM32 ROM DFU bootloader
    (BOOT0 jumper + reset — see the Octopus firmware's own README; there is
    no UF2-style mass-storage equivalent on this board family)."""
    if platform.system() == "Darwin":
        return False
    try:
        result = subprocess.run(
            ["lsusb", "-d", DFU_USB_ID], capture_output=True, text=True, timeout=5
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def waitForDfuDevice(
    gc: GlobalConfig, cancel: threading.Event, timeout_s: float = DFU_WAIT_TIMEOUT_S
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise FlashCancelled()
        if dfuDevicePresent():
            return
        time.sleep(0.5)
    raise FlashError(
        f"No DFU device ({DFU_USB_ID}) appeared within {timeout_s:.0f}s. "
        "Set the BOOT0 jumper, press reset, then retry."
    )


def flashViaDfu(
    gc: GlobalConfig,
    bin_path: str,
    cancel: threading.Event,
    on_progress: ProgressFn,
) -> None:
    """dfu-util gives no clean machine-parseable progress stream, so this
    reports start/done rather than a smooth bar — same as the Octopus
    firmware's own README documents for a manual flash."""
    if cancel.is_set():
        raise FlashCancelled()
    on_progress(0.0)
    try:
        proc = subprocess.run(
            ["dfu-util", "-a", "0", "-s", "0x08000000:leave", "-D", bin_path],
            capture_output=True,
            text=True,
            timeout=DFU_FLASH_TIMEOUT_S,
        )
    except FileNotFoundError:
        raise FlashError("dfu-util is not installed on this machine.")
    except subprocess.TimeoutExpired:
        raise FlashError(f"dfu-util did not finish within {DFU_FLASH_TIMEOUT_S:.0f}s.")
    if proc.returncode != 0:
        raise FlashError(
            f"dfu-util failed: {(proc.stderr or proc.stdout).strip() or 'unknown error'}"
        )
    on_progress(1.0)


def identifyBoard(gc: GlobalConfig, port: str) -> dict | None:
    bus: MCUBus | None = None
    try:
        bus = MCUBus(port=port, timeout=IDENTIFY_READ_TIMEOUT_S)
        dev = MCUDevice(bus, 0)
        info = dev.detect()
        try:
            info["version"] = dev.get_version()
        except Exception as exc:
            gc.logger.info(f"Board on {port} did not answer GET_VERSION: {exc}")
            info["version"] = None
        return info
    except Exception as exc:
        gc.logger.info(f"No responsive board on {port}: {exc}")
        return None
    finally:
        if bus is not None:
            bus.close()


def rebootToBootloader(gc: GlobalConfig, port: str) -> None:
    bus = MCUBus(port=port)
    try:
        bus.send_command_no_response(0x00, BaseCommandCode.REBOOT_BOOTLOADER, 0, b"")
        time.sleep(0.1)
    finally:
        bus.close()


def findBootloaderBlockdev() -> Optional[str]:
    if platform.system() == "Darwin":
        return None
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,LABEL", "-J"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        data = json.loads(result.stdout)
        for dev in data.get("blockdevices", []):
            for child in dev.get("children", [dev]):
                if child.get("label") == "RPI-RP2":
                    return f"/dev/{child['name']}"
    except Exception:
        pass
    return None


def findBootloaderMount() -> Optional[str]:
    if platform.system() == "Darwin":
        path = "/Volumes/RPI-RP2"
        return path if os.path.isdir(path) else None
    for pattern in ["/media/*/RPI-RP2", "/run/media/*/RPI-RP2", "/mnt/RPI-RP2"]:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    if os.path.ismount(LINUX_MOUNT_POINT):
        return LINUX_MOUNT_POINT
    return None


def bootloaderPresent() -> bool:
    return findBootloaderMount() is not None or findBootloaderBlockdev() is not None


def anyBootloaderPresent() -> bool:
    """RPI-RP2 (UF2) or DFU (Octopus) — whichever recovery bootloader a
    connected-but-blank-or-wedged board happens to be sitting in."""
    return bootloaderPresent() or dfuDevicePresent()


def waitForBootloaderMount(
    gc: GlobalConfig,
    cancel: threading.Event,
    timeout_s: float = BOOTLOADER_MOUNT_TIMEOUT_S,
) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise FlashCancelled()
        path = findBootloaderMount()
        if path:
            return path
        if platform.system() != "Darwin":
            dev = findBootloaderBlockdev()
            if dev:
                os.makedirs(LINUX_MOUNT_POINT, exist_ok=True)
                try:
                    subprocess.run(
                        ["mount", dev, LINUX_MOUNT_POINT], check=True, timeout=10
                    )
                    return LINUX_MOUNT_POINT
                except Exception as exc:
                    gc.logger.warning(f"Mounting {dev} failed, will retry: {exc}")
        time.sleep(0.5)
    raise FlashError(
        f"RPI-RP2 bootloader drive did not appear within {timeout_s:.0f}s. "
        "The board may not have rebooted — retry, or hold BOOTSEL while "
        "replugging and use a recovery flash."
    )


def copyUf2ToMount(
    gc: GlobalConfig,
    uf2_path: str,
    mount: str,
    cancel: threading.Event,
    on_progress: ProgressFn,
) -> None:
    total = os.path.getsize(uf2_path)
    dest = os.path.join(mount, os.path.basename(uf2_path))
    written = 0
    with open(uf2_path, "rb") as src, open(dest, "wb") as dst:
        while True:
            if cancel.is_set():
                raise FlashCancelled()
            chunk = src.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            dst.write(chunk)
            written += len(chunk)
            on_progress(written / total if total else 1.0)
        dst.flush()
        os.fsync(dst.fileno())
    # The bootloader consumes the file and reboots; sync makes sure the page
    # cache actually reaches the fake FAT device before we start waiting.
    if platform.system() != "Darwin":
        try:
            subprocess.run(["sync"], timeout=15)
        except Exception:
            pass


def waitForBootloaderGone(
    gc: GlobalConfig,
    cancel: threading.Event,
    timeout_s: float = BOOTLOADER_UNMOUNT_TIMEOUT_S,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise FlashCancelled()
        if findBootloaderBlockdev() is None and findBootloaderMount() is None:
            _unmountStaleMountpoint()
            return
        time.sleep(0.5)
    # Not fatal: some kernels keep the stale mountpoint entry around after the
    # device vanished. Clean up and let verification decide success.
    _unmountStaleMountpoint()
    gc.logger.warning(
        f"RPI-RP2 still visible after {timeout_s:.0f}s; proceeding to verification anyway"
    )


def _unmountStaleMountpoint() -> None:
    if platform.system() != "Darwin" and os.path.ismount(LINUX_MOUNT_POINT):
        try:
            subprocess.run(["umount", LINUX_MOUNT_POINT], check=False, timeout=10)
        except Exception:
            pass


def waitForSerialBoard(
    gc: GlobalConfig,
    cancel: threading.Event,
    timeout_s: float = SERIAL_REAPPEAR_TIMEOUT_S,
    linger_s: float = BOOTLOADER_LINGER_TIMEOUT_S,
) -> dict | None:
    """The board's info once it answers on serial, or None after timeout_s.
    While the RPI-RP2 drive is still visible the board has not started its
    reboot, so the clock starts only once the drive is gone (or after
    linger_s)."""
    started = time.monotonic()
    deadline = started + timeout_s
    while True:
        if cancel.is_set():
            raise FlashCancelled()
        for port in MCUBus.enumerate_buses():
            info = identifyBoard(gc, port)
            if info is not None:
                info["port"] = port
                return info
        now = time.monotonic()
        if now - started < linger_s and findBootloaderBlockdev() is not None:
            deadline = now + timeout_s
        if now >= deadline:
            return None
        time.sleep(1.0)
