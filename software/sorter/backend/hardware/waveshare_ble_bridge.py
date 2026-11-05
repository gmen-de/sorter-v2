"""BLE client for the esp32_ble_servo_bridge firmware.

Talks the line-based gate protocol documented in
software/firmware/esp32_ble_servo_bridge/README.md, over the Nordic UART
Service characteristics that firmware exposes. All servo intelligence
(open/closed/blocked detection, automatic ID assignment, calibration,
factory reset) lives on the ESP32 board; this module is a thin synchronous
RPC client (`WaveshareBleBridge`) plus a ServoMotor-compatible wrapper
(`BleGateServo`) so a gate drops into the same layer-servo machinery as
`hardware.sorter_interface.ServoMotor` and
`hardware.waveshare_servo.WaveshareServoMotor`.

Gate numbering is fixed by the firmware: gate N is servo ID N+10. This
module only ever talks in gate numbers.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Any, Dict

from bleak import BleakClient

logger = logging.getLogger(__name__)

_CHAR_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # host -> board
_CHAR_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # board -> host (notify)

_DEFAULT_TIMEOUT_S = 5.0
_CONNECT_TIMEOUT_S = 15.0
_LEARN_TIMEOUT_S = 90.0  # calibration steps outward until stall; can take a while
_INVENTORY_TIMEOUT_S = 5.0  # how long to wait for the post-connect READY GATES push


def _parse_gates(line: str) -> list[int]:
    """Parses the gate numbers out of a `[READY] GATES <id> <id> ...` line."""
    parts = line.split()
    if "GATES" not in parts:
        return []
    gates: list[int] = []
    for token in parts[parts.index("GATES") + 1 :]:
        try:
            gates.append(int(token))
        except ValueError:
            continue
    return gates


class WaveshareBleBridge:
    """Synchronous facade over a BLE connection to one ESP32 gate-servo bridge.

    Runs its own asyncio event loop on a background thread, since the rest
    of this codebase (and bleak) is not async. ``command()`` blocks the
    calling thread until a response line arrives or ``timeout`` elapses, and
    serializes access so only one command is in flight on the link at a
    time (mirrors `hardware.waveshare_bus_service.WaveshareBusService`'s
    locking for the wired bus).
    """

    def __init__(self, address: str, *, timeout: float = _DEFAULT_TIMEOUT_S):
        self._address = address
        self._timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: BleakClient | None = None
        self._call_lock = threading.RLock()
        self._line_buffer = ""
        self._responses: "queue.Queue[str]" = queue.Queue()
        self._connected = threading.Event()
        self._known_gates: list[int] = []
        self._inventory_event = threading.Event()

    @property
    def address(self) -> str:
        return self._address

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # -- lifecycle ------------------------------------------------------------

    def connect(self) -> None:
        """Connect, then wait briefly for the firmware's unsolicited
        `READY GATES ...` inventory push (sent as soon as it sees us
        subscribe) and log what it reports. Does not raise if that push
        doesn't arrive in time — callers that need it can still check
        `known_gates` / call `wait_for_inventory()` themselves."""
        with self._call_lock:
            if self._connected.is_set():
                return
            self._known_gates = []
            self._inventory_event.clear()
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name=f"waveshare-ble-{self._address}", daemon=True
            )
            self._thread.start()
            future = asyncio.run_coroutine_threadsafe(self._async_connect(), self._loop)
            future.result(timeout=_CONNECT_TIMEOUT_S)

        gates = self.wait_for_inventory(timeout=_INVENTORY_TIMEOUT_S)
        if gates is None:
            logger.warning(
                "waveshare ble bridge %s connected but sent no inventory within %ss.",
                self._address,
                _INVENTORY_TIMEOUT_S,
            )
        else:
            logger.info(
                "waveshare ble bridge %s connected: %d gate(s) reachable (%s)",
                self._address,
                len(gates),
                gates,
            )

    def close(self) -> None:
        with self._call_lock:
            if self._loop is None:
                return
            try:
                future = asyncio.run_coroutine_threadsafe(self._async_disconnect(), self._loop)
                future.result(timeout=_CONNECT_TIMEOUT_S)
            except Exception as exc:
                logger.warning("waveshare ble disconnect on %s failed: %s", self._address, exc)
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            self._loop = None
            self._thread = None
            self._connected.clear()

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _async_connect(self) -> None:
        client = BleakClient(self._address)
        await client.connect(timeout=_CONNECT_TIMEOUT_S)
        await client.start_notify(_CHAR_TX_UUID, self._on_notify)
        self._client = client
        self._connected.set()

    async def _async_disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    def _on_notify(self, _handle: int, data: bytearray) -> None:
        self._line_buffer += data.decode("ascii", errors="replace")
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith("READY"):
                self._handle_ready(line)
            else:
                self._responses.put(line)

    def _handle_ready(self, line: str) -> None:
        """Handle the unsolicited `READY GATES <id> <id> ...` push. Never
        goes through `_responses`: nothing is waiting on it with `command()`,
        and `LIST`'s own `GATES ...` response (no `READY` prefix) still does."""
        self._known_gates = _parse_gates(line)
        self._inventory_event.set()

    @property
    def known_gates(self) -> list[int]:
        """Gate numbers the board last reported as reachable (from the
        post-connect `READY` push, or a later `refresh_known_gates()` /
        `wait_for_inventory()`)."""
        return list(self._known_gates)

    def wait_for_inventory(self, timeout: float = _INVENTORY_TIMEOUT_S) -> list[int] | None:
        """Block for the next `READY GATES ...` push. Returns None on timeout."""
        if self._inventory_event.wait(timeout):
            return list(self._known_gates)
        return None

    def refresh_known_gates(self, *, timeout: float = 5.0) -> list[int]:
        """Actively ask (`LIST`) instead of waiting for the next unsolicited
        push. Used by the setup UI's "rescan" action."""
        gates = _parse_gates(self.list_gates(timeout=timeout))
        self._known_gates = gates
        self._inventory_event.set()
        return gates

    # -- raw command/response ---------------------------------------------------

    def command(self, line: str, *, timeout: float | None = None) -> str:
        """Send one command line, block for the matching response line.

        Reconnects once and retries on a dropped link, the same soft-recovery
        spirit as the wired bus service.
        """
        effective_timeout = timeout if timeout is not None else self._timeout
        with self._call_lock:
            if not self._connected.is_set():
                self.connect()
            try:
                return self._send_and_wait(line, effective_timeout)
            except Exception as exc:
                logger.warning("waveshare ble command %r failed on %s: %s", line, self._address, exc)
                self.close()
                self.connect()
                return self._send_and_wait(line, effective_timeout)

    def _send_and_wait(self, line: str, timeout: float) -> str:
        assert self._loop is not None
        # Drain stale responses left over from a previous timed-out call.
        while not self._responses.empty():
            try:
                self._responses.get_nowait()
            except queue.Empty:
                break
        future = asyncio.run_coroutine_threadsafe(self._async_write(line), self._loop)
        future.result(timeout=min(timeout, _CONNECT_TIMEOUT_S))
        try:
            return self._responses.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"No response to {line!r} from {self._address} within {timeout}s.")

    async def _async_write(self, line: str) -> None:
        assert self._client is not None
        await self._client.write_gatt_char(_CHAR_RX_UUID, (line + "\n").encode("ascii"), response=False)

    # -- gate-level commands ------------------------------------------------

    def open_gate(self, gate: int, *, timeout: float = 2.0) -> str:
        return self.command(f"OPEN {gate}", timeout=timeout)

    def close_gate(self, gate: int, *, timeout: float = 2.0) -> str:
        return self.command(f"CLOSE {gate}", timeout=timeout)

    def status_gate(self, gate: int, *, timeout: float = 2.0) -> str:
        return self.command(f"STATUS {gate}", timeout=timeout)

    def learn(self, *, timeout: float = _LEARN_TIMEOUT_S) -> str:
        return self.command("LEARN", timeout=timeout)

    def calibrate_gate(self, gate: int, *, timeout: float = _LEARN_TIMEOUT_S) -> str:
        return self.command(f"CALIBRATE {gate}", timeout=timeout)

    def reset_all(self, *, timeout: float = 10.0) -> str:
        return self.command("RESETALL", timeout=timeout)

    def list_gates(self, *, timeout: float = 5.0) -> str:
        return self.command("LIST", timeout=timeout)

    def ping(self, *, timeout: float = 2.0) -> bool:
        try:
            return self.command("PING", timeout=timeout) == "PONG"
        except TimeoutError:
            return False


class WaveshareBleBridgeRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._bridges: dict[str, WaveshareBleBridge] = {}

    def get_bridge(self, address: str) -> WaveshareBleBridge:
        normalized = address.strip()
        if not normalized:
            raise ValueError("Waveshare BLE bridge requires a non-empty device address.")
        with self._lock:
            bridge = self._bridges.get(normalized)
            if bridge is None:
                bridge = WaveshareBleBridge(normalized)
                self._bridges[normalized] = bridge
            return bridge

    def close_all(self) -> None:
        with self._lock:
            bridges = list(self._bridges.values())
            self._bridges.clear()
        for bridge in bridges:
            bridge.close()


_REGISTRY = WaveshareBleBridgeRegistry()


def get_waveshare_ble_bridge(address: str) -> WaveshareBleBridge:
    return _REGISTRY.get_bridge(address)


def close_all_waveshare_ble_bridges() -> None:
    _REGISTRY.close_all()


# ---------------------------------------------------------------------------
# BleGateServo — drop-in layer servo backed by the BLE gate protocol
# ---------------------------------------------------------------------------

_FIRMWARE_STATE = {"OPEN": "open", "CLOSED": "closed", "BLOCKED": "blocked", "UNKNOWN": "unknown"}


def _firmware_word(response: str) -> str:
    parts = response.split()
    return parts[1] if len(parts) >= 2 else parts[0] if parts else ""


class BleGateServo:
    """A layer gate controlled over `WaveshareBleBridge`.

    Presents the same interface as `hardware.sorter_interface.ServoMotor` /
    `hardware.waveshare_servo.WaveshareServoMotor` used by the distribution
    system, but there is no raw angle/position concept here — the firmware
    owns calibration and reports only open/closed/blocked.
    """

    def __init__(self, bridge: WaveshareBleBridge, gate: int, invert: bool = False):
        self._bridge = bridge
        self._gate = gate
        self._invert = invert
        self._name = f"waveshare_ble_gate_{gate}"
        self._last_state = "unknown"

    def _logical_state(self, firmware_word: str) -> str:
        if firmware_word == "BLOCKED":
            return "blocked"
        if firmware_word == "ERR":
            return "error"
        base = _FIRMWARE_STATE.get(firmware_word, "unknown")
        if self._invert and base in ("open", "closed"):
            base = "closed" if base == "open" else "open"
        return base

    # -- ServoMotor-compatible interface ------------------------------------

    @property
    def available(self) -> bool:
        return True

    @property
    def channel(self) -> int:
        return self._gate

    @property
    def enabled(self) -> bool:
        return True

    @enabled.setter
    def enabled(self, _value: bool) -> None:
        return  # torque is managed by the firmware around each move

    @property
    def stopped(self) -> bool:
        return True  # open()/close() block until the move is done

    def set_name(self, name: str) -> None:
        self._name = name

    def set_invert(self, invert: bool) -> None:
        self._invert = invert

    def set_speed_limits(self, _min_speed: int, _max_speed: int) -> None:
        pass  # the firmware uses a fixed move time, same as the wired SC backend

    def set_acceleration(self, _acceleration: int) -> None:
        pass

    def set_duty_limits(self, _min_duty_us: int, _max_duty_us: int) -> None:
        pass

    def set_preset_angles(self, _open_angle: int, _closed_angle: int) -> None:
        pass  # open/closed positions are learned and stored on the servo itself

    def open(self, _open_angle: int | None = None) -> bool:
        response = self._bridge.open_gate(self._gate) if not self._invert else self._bridge.close_gate(self._gate)
        self._last_state = self._logical_state(_firmware_word(response))
        return self._last_state == "open"

    def close(self, _closed_angle: int | None = None) -> bool:
        response = self._bridge.close_gate(self._gate) if not self._invert else self._bridge.open_gate(self._gate)
        self._last_state = self._logical_state(_firmware_word(response))
        return self._last_state == "closed"

    def stop(self) -> None:
        return  # no continuous motion to interrupt — moves are already blocking

    def toggle(self) -> None:
        if self.isOpen():
            self.close()
        else:
            self.open()

    def isOpen(self) -> bool:
        return self._refresh_state() == "open"

    def isClosed(self) -> bool:
        return self._refresh_state() == "closed"

    def isBlocked(self) -> bool:
        return self._refresh_state() == "blocked"

    def _query_state(self) -> str:
        """Like `_refresh_state`, but lets a bridge/connection error propagate
        instead of silently falling back to the last known state. Used by
        `feedback()`, which needs to tell "offline" apart from "unknown"."""
        response = self._bridge.status_gate(self._gate)
        self._last_state = self._logical_state(_firmware_word(response))
        return self._last_state

    def _refresh_state(self) -> str:
        try:
            return self._query_state()
        except Exception:
            return self._last_state

    def recalibrate(self) -> None:
        response = self._bridge.calibrate_gate(self._gate)
        if not response.startswith("CALIBRATED"):
            raise RuntimeError(f"Calibration of gate {self._gate} failed: {response}")

    def feedback(self) -> Dict[str, Any]:
        try:
            state = self._query_state()
            return {
                "channel": self._gate,
                "available": True,
                "is_open": state == "open",
                "is_blocked": state == "blocked",
                "state": state,
                "name": self._name,
                "invert": self._invert,
            }
        except Exception as exc:
            return {
                "channel": self._gate,
                "available": False,
                "error": str(exc),
                "name": self._name,
                "invert": self._invert,
            }
