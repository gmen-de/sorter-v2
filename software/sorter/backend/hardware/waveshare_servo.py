"""Waveshare SC series serial bus servo driver.

Implements the Feetech SC protocol (SC09, SC15, SC40, SC60) over half-duplex
TTL serial at 1 Mbps.  The driver provides both a low-level bus class
(`ScServoBus`) and a high-level `WaveshareServoMotor` that is a drop-in
replacement for the PCA9685-based `ServoMotor` used elsewhere in the sorter.

SC series uses **big-endian** byte order for 16-bit register values.

Auto-calibration
----------------
On first use each servo finds its physical min/max positions by stepping
towards each limit until the servo stalls (load spike + position stops
changing).  The discovered range is stored in the servo's EEPROM so the
procedure only runs once per servo (unless limits are reset to 0-1023).
"""

import logging
import struct
import threading
import time
from typing import Any, Dict

import serial

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SC Protocol constants
# ---------------------------------------------------------------------------

# Instructions
_INST_PING = 0x01
_INST_READ = 0x02
_INST_WRITE = 0x03

# Register addresses — EEPROM
_REG_MODEL_L = 3
_REG_ID = 5
_REG_MIN_ANGLE_L = 9
_REG_MAX_ANGLE_L = 11
_REG_P_COEF = 21
_REG_D_COEF = 22
_REG_I_COEF = 23

# Register addresses — SRAM
_REG_TORQUE_ENABLE = 40
_REG_GOAL_POSITION_L = 42
_REG_LOCK = 48
_REG_PRESENT_POSITION_L = 56
_REG_PRESENT_LOAD_L = 60
_REG_PRESENT_VOLTAGE = 62
_REG_PRESENT_TEMPERATURE = 63
_REG_MOVING = 66
_REG_PRESENT_CURRENT_L = 69


def _checksum(data: bytes) -> int:
    return (~sum(data)) & 0xFF


def decode_state(data: bytes, taken_at: float) -> dict:
    """Decode the 8 bytes from the present-position register on: position,
    speed and load as big-endian words, then voltage (0.1 V) and temperature."""
    position = ((data[0] << 8) | data[1]) & 0x03FF
    speed = (data[2] << 8) | data[3]
    load_word = (data[4] << 8) | data[5]
    load = -(load_word & 0x3FF) if load_word & 0x400 else load_word & 0x3FF
    return {
        "position": position,
        "speed": speed,
        "load": load,
        "voltage": data[6],
        "temperature": data[7],
        "t": taken_at,
    }


class ScServoBus:
    """Low-level half-duplex serial bus for SC servos."""

    def __init__(self, port: str, baudrate: int = 1_000_000, timeout: float = 0.05):
        self._serial = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self._lock = threading.Lock()

    def close(self):
        self._serial.close()

    # -- packet I/O ---------------------------------------------------------

    def _read_packet(self) -> bytes | None:
        first = self._serial.read(1)
        if len(first) < 1:
            return None

        while True:
            if first == b"\xFF":
                second = self._serial.read(1)
                if len(second) < 1:
                    return None
                if second == b"\xFF":
                    break
                first = second
                continue

            first = self._serial.read(1)
            if len(first) < 1:
                return None

        meta = self._serial.read(3)
        if len(meta) < 3:
            return None

        resp_length = meta[1]
        if resp_length < 2:
            return None

        tail = self._serial.read(resp_length - 1)
        if len(tail) < resp_length - 1:
            return None

        return b"\xFF\xFF" + meta + tail

    def _exchange(self, pkt: bytes) -> bytes | None:
        """Put one instruction packet on the bus and return the raw status
        packet (echo already skipped), or None when nothing came back.
        Caller holds ``self._lock``. A subclass with another transport only
        replaces this."""
        self._serial.reset_input_buffer()
        self._serial.write(pkt)
        self._serial.flush()

        packet = self._read_packet()
        if packet == pkt:
            packet = self._read_packet()
        return packet

    def _send(self, servo_id: int, instruction: int, params: bytes = b"") -> bytes | None:
        with self._lock:
            length = len(params) + 2
            pkt = bytes([0xFF, 0xFF, servo_id, length, instruction]) + params
            pkt += bytes([_checksum(pkt[2:])])

            packet = self._exchange(pkt)
            if packet is None or len(packet) < 6:
                return None

            response_id = packet[2]
            resp_length = packet[3]
            if resp_length < 2:
                return None
            error = packet[4]
            if response_id != servo_id:
                return None

            payload = packet[5:-1]
            checksum = packet[-1]
            expected_checksum = _checksum(packet[2:-1])
            if checksum != expected_checksum:
                return None
            if error != 0:
                return None

            return payload

    # -- helpers ------------------------------------------------------------

    def ping(self, servo_id: int) -> bool:
        return self._send(servo_id, _INST_PING) is not None

    def read_bytes(self, servo_id: int, address: int, count: int) -> bytes | None:
        resp = self._send(servo_id, _INST_READ, bytes([address, count]))
        if resp is None or len(resp) < count:
            return None
        return resp[:count]

    def write_bytes(self, servo_id: int, address: int, data: bytes) -> bool:
        return self._send(servo_id, _INST_WRITE, bytes([address]) + data) is not None

    def read_word(self, servo_id: int, address: int) -> int | None:
        data = self.read_bytes(servo_id, address, 2)
        if data is None:
            return None
        return struct.unpack(">H", data)[0]  # big-endian

    def write_byte(self, servo_id: int, address: int, value: int) -> bool:
        return self.write_bytes(servo_id, address, bytes([value]))

    # -- high-level servo commands ------------------------------------------

    def scan(self, start: int = 1, end: int = 20) -> list[int]:
        found = []
        for sid in range(start, end + 1):
            if self.ping(sid):
                found.append(sid)
            time.sleep(0.002)
        return found

    def set_torque(self, servo_id: int, enable: bool) -> bool:
        return self.write_byte(servo_id, _REG_TORQUE_ENABLE, 1 if enable else 0)

    def move_to(self, servo_id: int, position: int, time_ms: int = 500) -> bool:
        position = max(0, min(1023, position))
        pos_bytes = struct.pack(">H", position)
        time_bytes = struct.pack(">H", time_ms)
        speed_bytes = struct.pack(">H", 0)
        return self.write_bytes(
            servo_id, _REG_GOAL_POSITION_L,
            pos_bytes + time_bytes + speed_bytes,
        )

    def read_position(self, servo_id: int) -> int | None:
        val = self.read_word(servo_id, _REG_PRESENT_POSITION_L)
        if val is None:
            return None
        return val & 0x03FF

    def read_load(self, servo_id: int) -> int | None:
        val = self.read_word(servo_id, _REG_PRESENT_LOAD_L)
        if val is None:
            return None
        raw = val & 0x3FF
        if val & 0x400:
            return -raw
        return raw

    def is_moving(self, servo_id: int) -> bool:
        data = self.read_bytes(servo_id, _REG_MOVING, 1)
        if data is None:
            return False
        return data[0] != 0

    def read_state(self, servo_id: int) -> dict | None:
        """Position, speed, load, voltage and temperature in one bus read.

        ``t`` is the time (``time.monotonic()``) the sample was taken."""
        data = self.read_bytes(servo_id, _REG_PRESENT_POSITION_L, 8)
        if data is None:
            return None
        return decode_state(data, time.monotonic())

    def latest(self, servo_id: int, max_age: float = 0.25) -> dict | None:
        """The newest sample a bus keeps by itself, no younger than ``max_age``
        seconds. A direct serial bus keeps none; the Bluetooth bridge streams
        them."""
        return None

    def sample(self, servo_id: int, max_age: float = 0.0) -> dict | None:
        """A streamed sample when one is fresh enough, else a read."""
        if max_age > 0:
            cached = self.latest(servo_id, max_age)
            if cached is not None:
                return cached
        return self.read_state(servo_id)

    def read_angle_limits(self, servo_id: int) -> tuple[int, int] | None:
        data = self.read_bytes(servo_id, _REG_MIN_ANGLE_L, 4)
        if data is None or len(data) < 4:
            return None
        min_angle = struct.unpack(">H", data[0:2])[0]
        max_angle = struct.unpack(">H", data[2:4])[0]
        return min_angle, max_angle

    def set_angle_limits(self, servo_id: int, min_val: int, max_val: int) -> bool:
        self.write_byte(servo_id, _REG_LOCK, 0)  # unlock EEPROM
        time.sleep(0.01)
        data = struct.pack(">H", min_val) + struct.pack(">H", max_val)
        result = self.write_bytes(servo_id, _REG_MIN_ANGLE_L, data)
        time.sleep(0.01)
        self.write_byte(servo_id, _REG_LOCK, 1)  # lock EEPROM
        return result

    def set_pid(self, servo_id: int, p: int, d: int, i: int) -> bool:
        self.write_byte(servo_id, _REG_LOCK, 0)
        time.sleep(0.01)
        result = self.write_bytes(servo_id, _REG_P_COEF, bytes([p, d, i]))
        time.sleep(0.01)
        self.write_byte(servo_id, _REG_LOCK, 1)
        return result

    def set_id(self, old_id: int, new_id: int) -> bool:
        """Change a servo's ID.  Requires EEPROM unlock."""
        if new_id < 1 or new_id > 253:
            return False
        self.write_byte(old_id, _REG_LOCK, 0)
        time.sleep(0.01)
        result = self.write_byte(old_id, _REG_ID, new_id)
        time.sleep(0.01)
        self.write_byte(new_id, _REG_LOCK, 1)
        return result

    def read_servo_info(self, servo_id: int) -> dict | None:
        """Read identification and live telemetry from a servo."""
        if not self.ping(servo_id):
            return None

        model = self.read_word(servo_id, _REG_MODEL_L)
        position = self.read_position(servo_id)
        load = self.read_load(servo_id)
        limits = self.read_angle_limits(servo_id)

        temp_data = self.read_bytes(servo_id, _REG_PRESENT_VOLTAGE, 2)
        voltage: int | None = None
        temperature: int | None = None
        if temp_data is not None and len(temp_data) >= 2:
            voltage = temp_data[0]
            temperature = temp_data[1]

        current = self.read_word(servo_id, _REG_PRESENT_CURRENT_L)

        pid_data = self.read_bytes(servo_id, _REG_P_COEF, 3)
        pid = None
        if pid_data is not None and len(pid_data) >= 3:
            pid = {"p": pid_data[0], "d": pid_data[1], "i": pid_data[2]}

        model_name = None
        if model is not None:
            # Model detection based on observed firmware values (see sc_servo.rs)
            if model in (4, 9, 0x0400, 0x0504, 0x0405, 0x0900):
                model_name = "SC09"
            elif model in (15, 0x0F00, 0x050F, 0x0F05):
                model_name = "SC15"
            elif model in (40, 0x2800):
                model_name = "SC40"
            elif model in (60, 0x3C00):
                model_name = "SC60"

        return {
            "id": servo_id,
            "model": model,
            "model_name": model_name,
            "position": position,
            "load": load,
            "min_limit": limits[0] if limits else None,
            "max_limit": limits[1] if limits else None,
            "voltage": round(voltage / 10.0, 1) if voltage is not None else None,
            "temperature": temperature,
            "current": current,
            "pid": pid,
        }


# ---------------------------------------------------------------------------
# Auto-calibration
# ---------------------------------------------------------------------------

_CAL_STEP_SIZE = 30
_CAL_STEP_TIME_MS = 500
_CAL_MARGIN = 5

# ---------------------------------------------------------------------------
# Motion feedback
# ---------------------------------------------------------------------------

# A move is done when the measured position is within _ARRIVE_TOL units of the
# target on _ARRIVE_CONFIRM samples in a row; it is blocked when the position
# has not moved more than _STALL_MOVE units for _STALL_S seconds short of the
# target; it timed out when neither happened before the deadline.
_ARRIVE_TOL = 6
_ARRIVE_CONFIRM = 2
_STALL_S = 0.25
_STALL_MOVE = 2
_MIN_MOVE_S = 0.2
_MOVE_GRACE_S = 1.0
_SAMPLE_MAX_AGE_S = 0.2
_DEFAULT_MOVE_MS = 300
_MOVE_MS_RANGE = (50, 5000)
_NO_FEEDBACK_LIMIT = 5


class MotionWatch:
    """Decides from position samples whether a move has arrived, is blocked
    or has timed out. Feed it samples with ``update``; it returns None while
    the move is still going, else ``"ok"``, ``"blocked"`` or ``"timeout"``.

    A sample is only counted once (by its ``t``), so polling faster than the
    samples arrive does not make a slow servo look stalled."""

    def __init__(self, target: int, started: float, deadline: float):
        self.target = target
        self.started = started
        self.deadline = deadline
        self.position: int | None = None
        self.load = 0
        self._last_t: float | None = None
        self._arrived = 0
        self._ref_pos: int | None = None
        self._ref_t = 0.0

    def update(self, state: dict | None, now: float) -> str | None:
        if state is not None and state["t"] != self._last_t:
            self._last_t = state["t"]
            pos = state["position"]
            self.position = pos
            self.load = state["load"]
            if abs(pos - self.target) <= _ARRIVE_TOL:
                self._arrived += 1
                if self._arrived >= _ARRIVE_CONFIRM:
                    return "ok"
            else:
                self._arrived = 0
                if self._ref_pos is None or abs(pos - self._ref_pos) > _STALL_MOVE:
                    self._ref_pos = pos
                    self._ref_t = state["t"]
                elif (
                    state["t"] - self._ref_t >= _STALL_S
                    and state["t"] - self.started >= _MIN_MOVE_S
                ):
                    return "blocked"
        if now >= self.deadline:
            return "timeout"
        return None


def wait_settled(
    bus: "ScServoBus",
    servo_id: int,
    target: int,
    timeout_s: float,
    poll_s: float = 0.02,
    max_age: float = 0.0,
) -> tuple[str, int | None, int]:
    """Block until the servo has reached ``target``, is blocked short of it, or
    ``timeout_s`` is over. Returns (result, last position, last load); result
    is ``"ok"``, ``"blocked"``, ``"timeout"`` or ``"no_feedback"`` when the
    servo does not answer."""
    started = time.monotonic()
    watch = MotionWatch(target, started, started + timeout_s)
    misses = 0
    while True:
        state = bus.sample(servo_id, max_age)
        if state is None:
            misses += 1
            if misses >= _NO_FEEDBACK_LIMIT:
                return "no_feedback", watch.position, watch.load
        else:
            misses = 0
        result = watch.update(state, time.monotonic())
        if result is not None:
            return result, watch.position, watch.load
        time.sleep(poll_s)


def calibrate_servo(bus: ScServoBus, servo_id: int) -> tuple[int, int]:
    """Find the physical min/max of a servo by stepping until stall.

    Returns (safe_min, safe_max) with a small safety margin applied.
    Raises RuntimeError if calibration fails.
    """
    logger.info(f"Calibrating servo {servo_id}...")

    # Temporarily open full range
    bus.set_angle_limits(servo_id, 0, 1023)
    time.sleep(0.02)
    bus.set_torque(servo_id, True)
    time.sleep(0.02)

    current = bus.read_position(servo_id)
    if current is None:
        raise RuntimeError(f"Cannot read position of servo {servo_id}")

    def find_limit(start_pos: int, direction: int) -> int:
        """Step in `direction` (-1 for min, +1 for max) until the measured
        position stops short of the step's target."""
        best = start_pos
        target = start_pos

        while True:
            next_target = target + direction * _CAL_STEP_SIZE
            next_target = max(0, min(1023, next_target))
            if next_target == target:
                # Hit 0 or 1023 boundary
                return best
            target = next_target

            bus.move_to(servo_id, target, _CAL_STEP_TIME_MS)
            result, pos, load = wait_settled(
                bus, servo_id, target, timeout_s=_CAL_STEP_TIME_MS / 1000.0 + _MOVE_GRACE_S
            )
            if pos is not None and ((direction < 0 and pos < best) or (direction > 0 and pos > best)):
                best = pos
            if result == "ok":
                continue  # reached the step's target, issue the next step
            if result == "no_feedback":
                raise RuntimeError(f"Servo {servo_id} stopped answering during calibration")
            logger.info(f"  Servo {servo_id}: limit found at {best} ({result}, load={load})")
            return best

    cal_min = find_limit(current, -1)
    logger.info(f"  Servo {servo_id}: min = {cal_min}")

    cal_max = find_limit(cal_min, +1)
    logger.info(f"  Servo {servo_id}: max = {cal_max}")

    span = cal_max - cal_min
    if span < 20:
        raise RuntimeError(
            f"Servo {servo_id} calibration failed: range too small ({cal_min}-{cal_max}, span={span})"
        )

    margin = min(_CAL_MARGIN, span // 4)
    safe_min = cal_min + margin
    safe_max = cal_max - margin

    # Save to EEPROM so we don't need to recalibrate next time
    bus.set_angle_limits(servo_id, safe_min, safe_max)
    logger.info(f"  Servo {servo_id}: calibrated range {safe_min}-{safe_max} (saved to EEPROM)")

    # Move to center
    center = (safe_min + safe_max) // 2
    bus.move_to(servo_id, center, 500)
    wait_settled(bus, servo_id, center, timeout_s=0.5 + _MOVE_GRACE_S)
    bus.set_torque(servo_id, False)

    return safe_min, safe_max


# ---------------------------------------------------------------------------
# WaveshareServoMotor — drop-in replacement for ServoMotor
# ---------------------------------------------------------------------------

class WaveshareServoMotor:
    """A servo motor controlled via the Waveshare SC serial bus.

    Presents the same interface as `hardware.sorter_interface.ServoMotor`
    so it can be used as a drop-in replacement in the distribution system.

    open/close positions are mapped to the EEPROM angle limits:
    - open  = min limit  (gate opens, piece falls through)
    - close = max limit  (gate blocks)
    If `invert` is True these are swapped.
    """

    def __init__(self, bus: ScServoBus, servo_id: int, invert: bool = False):
        self._bus = bus
        self._servo_id = servo_id
        self._invert = invert
        self._name = f"waveshare_servo_{servo_id}"
        self._enabled = False
        self._current_position: int = 0  # raw SC position 0-1023
        self._min_limit: int = 0
        self._max_limit: int = 1023
        self._open_position: int = 0
        self._closed_position: int = 1023
        # Motion is judged from measured positions, not from elapsed time.
        self._motion_lock = threading.RLock()
        self._watch: MotionWatch | None = None
        self._release_after = False
        self._move_result = "idle"
        self._measured_position: int | None = None
        self._open_speed: int | None = None
        self._close_speed: int | None = None
        self._homing_speed: int | None = None
        self._speed_dps: int | None = None

    def initialize(self) -> None:
        """Read or auto-calibrate limits and apply good PID settings."""
        # Set PID to avoid undershooting (factory default I=0 causes issues)
        self._bus.set_pid(self._servo_id, 32, 32, 20)

        limits = self._bus.read_angle_limits(self._servo_id)
        if limits is None:
            raise RuntimeError(f"Cannot communicate with servo {self._servo_id}")

        min_lim, max_lim = limits
        needs_calibration = (min_lim == 0 and max_lim == 1023) or (max_lim - min_lim < 20)

        if needs_calibration:
            logger.info(f"Servo {self._servo_id}: limits are {min_lim}-{max_lim}, running auto-calibration")
            min_lim, max_lim = calibrate_servo(self._bus, self._servo_id)
        else:
            logger.info(f"Servo {self._servo_id}: using stored limits {min_lim}-{max_lim}")

        self._min_limit = min_lim
        self._max_limit = max_lim

        if self._invert:
            self._open_position = max_lim
            self._closed_position = min_lim
        else:
            self._open_position = min_lim
            self._closed_position = max_lim

        # Read current position
        pos = self._bus.read_position(self._servo_id)
        if pos is not None:
            self._current_position = pos

    def set_invert(self, invert: bool) -> None:
        self._invert = invert
        if self._invert:
            self._open_position = self._max_limit
            self._closed_position = self._min_limit
        else:
            self._open_position = self._min_limit
            self._closed_position = self._max_limit

    def recalibrate(self) -> tuple[int, int]:
        min_lim, max_lim = calibrate_servo(self._bus, self._servo_id)
        self._min_limit = min_lim
        self._max_limit = max_lim
        self.set_invert(self._invert)
        pos = self._bus.read_position(self._servo_id)
        if pos is not None:
            self._current_position = pos
        else:
            self._current_position = (min_lim + max_lim) // 2

    # -- ServoMotor-compatible interface ------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = bool(value)
        self._bus.set_torque(self._servo_id, self._enabled)

    def move_to(self, angle: int) -> bool:
        """Move to angle (0-180). Maps linearly to calibrated range."""
        if not self._enabled:
            self.enabled = True
        position = self._angle_to_position(angle)
        return self._start_move(position, release=False, speed_dps=self._speed_dps)

    def move_to_and_release(self, angle: int) -> bool:
        """Move to angle, then disable torque once the servo has arrived."""
        if not self._enabled:
            self.enabled = True
        position = self._angle_to_position(angle)
        accepted = self._start_move(position, release=True, speed_dps=self._speed_dps)
        self._enabled = False  # released when the move ends
        return accepted

    @property
    def position(self) -> int:
        pos = self._bus.read_position(self._servo_id)
        if pos is not None:
            self._measured_position = pos
        return pos if pos is not None else self._current_position

    def stop(self):
        with self._motion_lock:
            self._watch = None
            self._release_after = False
            self._move_result = "stopped"
        self._bus.set_torque(self._servo_id, False)
        self._enabled = False

    @property
    def stopped(self) -> bool:
        """True when no move is under way: the servo reached its target, is
        blocked short of it, or the move ran out of time (see ``move_result``).
        Each call looks at the newest measured position; it never sleeps."""
        with self._motion_lock:
            watch = self._watch
            if watch is None:
                return True
            try:
                state = self._bus.sample(self._servo_id, _SAMPLE_MAX_AGE_S)
            except Exception as exc:
                # An unreachable bus cannot confirm the move; the deadline
                # still ends it.
                logger.debug(f"Servo {self._servo_id}: no position sample: {exc}")
                state = None
            if state is not None:
                self._measured_position = state["position"]
            result = watch.update(state, time.monotonic())
            if result is None:
                return False
            self._finish_move(result, watch)
            return True

    @property
    def move_result(self) -> str:
        """How the last move ended: ``"ok"``, ``"blocked"``, ``"timeout"``,
        ``"rejected"``, ``"stopped"``, ``"moving"`` while under way, or
        ``"idle"`` before the first move."""
        return self._move_result

    def wait_stopped(self, timeout_s: float = 2.0, poll_s: float = 0.02) -> bool:
        """Block until the current move has ended; False if it still runs
        after ``timeout_s``."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        while not self.stopped:
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_s)
        return True

    @property
    def available(self) -> bool:
        return True

    def open(self, open_angle: int | None = None) -> bool:
        if not self._enabled:
            self.enabled = True
        accepted = self._start_move(self._open_position, release=True, speed_dps=self._open_speed)
        self._enabled = False  # released when the move ends
        return accepted

    def close(self, closed_angle: int | None = None) -> bool:
        if not self._enabled:
            self.enabled = True
        accepted = self._start_move(self._closed_position, release=True, speed_dps=self._close_speed)
        self._enabled = False  # released when the move ends
        return accepted

    def toggle(self) -> None:
        if self.isOpen():
            self.close()
        else:
            self.open()

    def isOpen(self) -> bool:
        # The measured position when there is one, else the last commanded.
        position = self._measured_position if self._measured_position is not None else self._current_position
        return abs(position - self._open_position) < abs(position - self._closed_position)

    def isClosed(self) -> bool:
        return not self.isOpen()

    def set_motion_speeds(
        self, open_speed: int | None, close_speed: int | None, homing_speed: int | None
    ) -> None:
        """Speeds in °/s for opening, closing and everything else; None keeps
        the default move time."""
        self._open_speed = open_speed
        self._close_speed = close_speed
        self._homing_speed = homing_speed

    def apply_open_speed(self) -> None:
        pass  # open() always uses the opening speed

    def apply_close_speed(self) -> None:
        pass  # close() always uses the closing speed

    def apply_homing_speed(self) -> None:
        self._speed_dps = self._homing_speed

    def set_speed_limits(self, min_speed: int, max_speed: int) -> None:
        pass  # SC servos take a move time, not a speed; see set_motion_speeds

    def set_acceleration(self, acceleration: int) -> None:
        pass  # not applicable for SC servos

    def set_duty_limits(self, min_duty_us: int, max_duty_us: int) -> None:
        pass  # not applicable for SC servos

    def set_name(self, name: str) -> None:
        self._name = name

    def set_preset_angles(self, open_angle: int, closed_angle: int) -> None:
        # For waveshare, open/close are determined by calibration + invert,
        # so we ignore the angle values but accept the call for compatibility.
        pass

    @property
    def angle(self) -> int:
        return self._position_to_angle(self._current_position)

    @property
    def channel(self) -> int:
        return self._servo_id

    def feedback(self) -> Dict[str, Any]:
        position = self.position
        return {
            "channel": self._servo_id,
            "position": position,
            "angle": self._position_to_angle(position),
            "open_position": self._open_position,
            "closed_position": self._closed_position,
            "min_limit": self._min_limit,
            "max_limit": self._max_limit,
            "is_open": abs(position - self._open_position) < abs(position - self._closed_position),
            "invert": self._invert,
            "move_result": self._move_result,
        }

    # -- internal -----------------------------------------------------------

    def _move_time_ms(self, target: int, speed_dps: int | None) -> int:
        """The servo's move time: from the configured speed and the distance
        (the calibrated range counts as 180°), else the default."""
        if not speed_dps:
            return _DEFAULT_MOVE_MS
        span = max(1, self._max_limit - self._min_limit)
        degrees = abs(target - self._current_position) / span * 180.0
        time_ms = int(round(degrees / speed_dps * 1000.0))
        return max(_MOVE_MS_RANGE[0], min(_MOVE_MS_RANGE[1], time_ms))

    def _start_move(self, position: int, release: bool, speed_dps: int | None) -> bool:
        with self._motion_lock:
            time_ms = self._move_time_ms(position, speed_dps)
            now = time.monotonic()
            self._watch = MotionWatch(position, now, now + time_ms / 1000.0 + _MOVE_GRACE_S)
            self._release_after = release
            self._move_result = "moving"
            self._current_position = position
            accepted = bool(self._bus.move_to(self._servo_id, position, time_ms))
            if not accepted:
                self._watch = None
                self._release_after = False
                self._move_result = "rejected"
            return accepted

    def _finish_move(self, result: str, watch: MotionWatch) -> None:
        """End the move (lock held): record how it went and, for a move that
        was to release, let go of the servo."""
        self._watch = None
        self._move_result = result
        if result != "ok":
            logger.warning(
                f"Servo {self._servo_id}: move to {watch.target} ended '{result}' at "
                f"{watch.position} (load {watch.load})"
            )
        if self._release_after:
            self._release_after = False
            try:
                self._bus.set_torque(self._servo_id, False)
            except Exception as exc:
                logger.warning(f"Servo {self._servo_id}: could not release torque: {exc}")

    def _angle_to_position(self, angle: int) -> int:
        """Map 0-180 degrees to calibrated min-max range."""
        angle = max(0, min(180, angle))
        span = self._max_limit - self._min_limit
        return self._min_limit + int(round(angle / 180.0 * span))

    def _position_to_angle(self, position: int) -> int:
        """Map calibrated position back to 0-180 degrees."""
        span = self._max_limit - self._min_limit
        if span == 0:
            return 0
        return int(round((position - self._min_limit) / span * 180.0))
