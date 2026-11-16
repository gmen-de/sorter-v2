"""The Waveshare SC servo bus over a Bluetooth bridge.

An ESP32 on the rotating part (see ``software/firmware/sc15_bt_bridge``) owns
the real half-duplex bus. This module is the host side: it keeps one RFCOMM
link to the bridge and presents it as a ``ScServoBus``, so ``WaveshareServoMotor``
and the bus service work on it unchanged.

Two things cross the link:

* ``XFER`` - one servo instruction packet out, the status packet back. The
  bridge runs the bus transaction locally, so the Bluetooth latency is paid
  once per command and the 1 Mbit/s timing never touches the radio.
* ``TELEMETRY`` - the bridge reads position, speed, load, voltage and
  temperature of the servos it was told to watch and streams them. The motor
  uses these to see that a move has arrived instead of waiting a fixed time.

Frames are COBS-encoded with a CRC16, like the control boards' protocol.

Port strings (``servo.port`` in machine.toml):

* ``bt:AA:BB:CC:DD:EE:FF`` or ``bt:AA:BB:CC:DD:EE:FF/2`` - RFCOMM to that
  address (channel 1 unless given). Needs a paired device and Linux/BlueZ.
* ``btserial:/dev/rfcomm0`` (or ``btserial:COM7``) - the same link through a
  serial device the OS created for the paired bridge.
"""

from __future__ import annotations

import logging
import queue
import select
import socket
import struct
import threading
import time
from typing import Callable

from hardware.waveshare_servo import ScServoBus, decode_state

logger = logging.getLogger(__name__)

PROTO_VERSION = 1

# Host -> bridge
T_XFER = 0x01
T_HELLO = 0x02
T_SET_POLL = 0x03
T_HEARTBEAT = 0x05
T_CONFIG = 0x06
# Bridge -> host
R_XFER = 0x81
R_HELLO = 0x82
R_SET_POLL = 0x83
R_TELEMETRY = 0x84
R_CONFIG = 0x86
R_ERROR = 0x8F

XFER_OK = 0
XFER_TIMEOUT = 1
XFER_BAD = 2

MAX_POLL_IDS = 16
DEFAULT_POLL_PERIOD_MS = 40
# How long the bridge waits for a servo's answer on the real bus.
BUS_TIMEOUT_MS = 20

_TELEMETRY_ENTRY = struct.Struct("<BBHHHBB")  # id, flags, pos, speed, load, volt, temp
_FLAG_VALID = 0x01


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    out = bytearray([0])
    code_index = 0
    code = 1
    for byte in data:
        if byte == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(byte)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index = len(out)
                out.append(0)
                code = 1
    out[code_index] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise ValueError("zero byte inside a COBS block")
        i += 1
        end = i + code - 1
        if end > n:
            raise ValueError("COBS block runs past the end")
        out += data[i:end]
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


def encode_frame(frame_type: int, seq: int, body: bytes = b"") -> bytes:
    """type, seq, body, CRC16 (little-endian), COBS, 0x00 delimiter."""
    raw = bytes([frame_type, seq]) + body
    raw += struct.pack("<H", crc16(raw))
    return cobs_encode(raw) + b"\x00"


class FrameDecoder:
    """Cuts a byte stream at the 0x00 delimiters and yields valid frames as
    (type, seq, body). Damaged frames are counted and dropped."""

    MAX_FRAME = 1024

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.bad_frames = 0

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        frames: list[tuple[int, int, bytes]] = []
        for byte in data:
            if byte != 0:
                if len(self._buffer) < self.MAX_FRAME:
                    self._buffer.append(byte)
                else:
                    self._buffer.clear()
                    self.bad_frames += 1
                continue
            if not self._buffer:
                continue
            chunk = bytes(self._buffer)
            self._buffer.clear()
            try:
                raw = cobs_decode(chunk)
            except ValueError:
                self.bad_frames += 1
                continue
            if len(raw) < 4 or struct.unpack("<H", raw[-2:])[0] != crc16(raw[:-2]):
                self.bad_frames += 1
                continue
            frames.append((raw[0], raw[1], raw[2:-2]))
        return frames


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class SocketTransport:
    """A connected stream socket (RFCOMM in production, a socketpair in tests)."""

    def __init__(self, sock: socket.socket | None = None):
        self._sock = sock

    def connect(self) -> None:
        if self._sock is None:
            raise ConnectionError("no socket")

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise ConnectionError("link is closed")
        try:
            sock.sendall(data)
        except OSError as exc:
            raise ConnectionError(str(exc)) from exc

    def recv(self, size: int, timeout: float) -> bytes:
        sock = self._sock
        if sock is None:
            raise ConnectionError("link is closed")
        try:
            ready, _, _ = select.select([sock], [], [], timeout)
            if not ready:
                return b""
            data = sock.recv(size)
        except (OSError, ValueError) as exc:
            raise ConnectionError(str(exc)) from exc
        if not data:
            raise ConnectionError("bridge closed the link")
        return data


class RfcommTransport(SocketTransport):
    def __init__(self, address: str, channel: int = 1, connect_timeout: float = 5.0):
        super().__init__(None)
        self._address = address
        self._channel = channel
        self._connect_timeout = connect_timeout

    def connect(self) -> None:
        self.close()
        af_bluetooth = getattr(socket, "AF_BLUETOOTH", None)
        proto = getattr(socket, "BTPROTO_RFCOMM", None)
        if af_bluetooth is None or proto is None:
            raise ConnectionError("this Python has no RFCOMM sockets; use btserial:<device> instead")
        sock = socket.socket(af_bluetooth, socket.SOCK_STREAM, proto)
        try:
            sock.settimeout(self._connect_timeout)
            sock.connect((self._address, self._channel))
            sock.settimeout(None)
        except OSError as exc:
            sock.close()
            raise ConnectionError(f"cannot reach {self._address} channel {self._channel}: {exc}") from exc
        self._sock = sock


class SerialTransport:
    """The bridge as an OS serial device (rfcomm bind, or a Windows COM port)."""

    def __init__(self, device: str):
        self._device = device
        self._serial = None

    def connect(self) -> None:
        import serial

        self.close()
        try:
            self._serial = serial.Serial(self._device, baudrate=115200, timeout=0.2, write_timeout=2.0)
        except (OSError, serial.SerialException) as exc:
            raise ConnectionError(f"cannot open {self._device}: {exc}") from exc

    def close(self) -> None:
        ser, self._serial = self._serial, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def send(self, data: bytes) -> None:
        ser = self._serial
        if ser is None:
            raise ConnectionError("link is closed")
        try:
            ser.write(data)
        except Exception as exc:
            raise ConnectionError(str(exc)) from exc

    def recv(self, size: int, timeout: float) -> bytes:
        ser = self._serial
        if ser is None:
            raise ConnectionError("link is closed")
        try:
            ser.timeout = timeout
            data = ser.read(max(1, min(size, ser.in_waiting or 1)))
        except Exception as exc:
            raise ConnectionError(str(exc)) from exc
        return data


# ---------------------------------------------------------------------------
# The link
# ---------------------------------------------------------------------------


class BtLink:
    """One connection to the bridge, shared by everything that talks to it.

    It reconnects on its own in the background; requests made while it is
    down fail at once with ``ConnectionError`` instead of blocking the caller.
    """

    def __init__(
        self,
        transport,
        name: str = "bridge",
        *,
        heartbeat_s: float = 0.5,
        reconnect_s: float = 1.0,
        request_timeout_s: float = 0.6,
        retries: int = 1,
    ):
        self._transport = transport
        self.name = name
        self._heartbeat_s = heartbeat_s
        self._reconnect_s = reconnect_s
        self._request_timeout_s = request_timeout_s
        self._retries = retries

        self._conn_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._up = False
        self._generation = 0
        self._pending: dict[int, queue.Queue] = {}
        self._seq = 0
        self._consecutive_timeouts = 0
        self._samples: dict[int, dict] = {}
        self._poll_ids: list[int] = []
        self._poll_period_ms = DEFAULT_POLL_PERIOD_MS
        self.hello: dict | None = None
        self.bad_frames = 0
        self.reconnects = 0
        self._closed = False
        self._keeper: threading.Thread | None = None

    # -- connection ---------------------------------------------------------

    @property
    def up(self) -> bool:
        return self._up

    def connect(self) -> None:
        """Connect now (blocking) and keep the link alive from then on."""
        self._closed = False
        self._connect_once()
        if self._keeper is None or not self._keeper.is_alive():
            self._keeper = threading.Thread(target=self._keep_alive, name=f"bt-link-{self.name}", daemon=True)
            self._keeper.start()

    def close(self) -> None:
        self._closed = True
        self._mark_down("closed")

    def _connect_once(self) -> None:
        with self._conn_lock:
            if self._up:
                return
            try:
                self._transport.connect()
            except ConnectionError:
                self._transport.close()
                raise
            with self._state_lock:
                self._generation += 1
                generation = self._generation
                self._up = True
                self._consecutive_timeouts = 0
            threading.Thread(
                target=self._read_loop, args=(generation, FrameDecoder()), name=f"bt-rx-{self.name}", daemon=True
            ).start()
            try:
                hello = self._request(T_HELLO, b"", expect=R_HELLO)
                self.hello = self._parse_hello(hello)
                if self.hello["proto"] != PROTO_VERSION:
                    raise ConnectionError(
                        f"bridge speaks protocol {self.hello['proto']}, host expects {PROTO_VERSION}"
                    )
                if self._poll_ids:
                    self._request(T_SET_POLL, self._poll_body(), expect=R_SET_POLL)
            except Exception:
                self._mark_down("handshake failed")
                raise
            self.reconnects += 1
            logger.info(f"Bluetooth bridge {self.name} connected: {self.hello}")

    def _keep_alive(self) -> None:
        while not self._closed:
            if self._up:
                try:
                    self._send(T_HEARTBEAT, 0, b"")
                except ConnectionError:
                    pass
                time.sleep(self._heartbeat_s)
            else:
                try:
                    self._connect_once()
                except Exception as exc:
                    logger.debug(f"Bluetooth bridge {self.name}: reconnect failed: {exc}")
                    time.sleep(self._reconnect_s)

    def _mark_down(self, reason: str) -> None:
        with self._state_lock:
            was_up = self._up
            self._up = False
            self._generation += 1
            pending = list(self._pending.values())
            self._pending.clear()
        self._transport.close()
        for waiter in pending:
            waiter.put(None)
        if was_up and not self._closed:
            logger.warning(f"Bluetooth bridge {self.name} link down: {reason}")

    # -- receiving ----------------------------------------------------------

    def _read_loop(self, generation: int, decoder: FrameDecoder) -> None:
        while self._up and generation == self._generation:
            try:
                data = self._transport.recv(512, 0.2)
            except ConnectionError as exc:
                if generation == self._generation:
                    self._mark_down(str(exc))
                return
            if not data:
                continue
            for frame_type, seq, body in decoder.feed(data):
                self._dispatch(frame_type, seq, body)
            self.bad_frames = max(self.bad_frames, decoder.bad_frames)

    def _dispatch(self, frame_type: int, seq: int, body: bytes) -> None:
        if frame_type == R_TELEMETRY:
            self._on_telemetry(body)
            return
        with self._state_lock:
            waiter = self._pending.pop(seq, None)
        if waiter is not None:
            waiter.put((frame_type, body))

    def _on_telemetry(self, body: bytes) -> None:
        if len(body) < 5:
            return
        device_ms, count = struct.unpack_from("<IB", body, 0)
        now = time.monotonic()
        offset = 5
        for _ in range(count):
            if offset + _TELEMETRY_ENTRY.size > len(body):
                break
            servo_id, flags, pos, speed, load_word, volt, temp = _TELEMETRY_ENTRY.unpack_from(body, offset)
            offset += _TELEMETRY_ENTRY.size
            if not flags & _FLAG_VALID:
                continue
            sample = decode_state(
                bytes([pos >> 8, pos & 0xFF, speed >> 8, speed & 0xFF, load_word >> 8, load_word & 0xFF, volt, temp]),
                now,
            )
            sample["device_ms"] = device_ms
            self._samples[servo_id] = sample

    # -- sending ------------------------------------------------------------

    def _send(self, frame_type: int, seq: int, body: bytes) -> None:
        if not self._up:
            raise ConnectionError(f"bridge {self.name} is not connected")
        try:
            with self._send_lock:
                self._transport.send(encode_frame(frame_type, seq, body))
        except ConnectionError as exc:
            self._mark_down(str(exc))
            raise

    def _next_seq(self) -> int:
        with self._state_lock:
            self._seq = self._seq % 255 + 1
            return self._seq

    def _request(self, frame_type: int, body: bytes, expect: int, timeout: float | None = None) -> bytes:
        timeout = self._request_timeout_s if timeout is None else timeout
        seq = self._next_seq()
        waiter: queue.Queue = queue.Queue()
        with self._state_lock:
            self._pending[seq] = waiter
        try:
            for _ in range(self._retries + 1):
                self._send(frame_type, seq, body)  # the same seq: the bridge answers a repeat from memory
                try:
                    answer = waiter.get(timeout=timeout)
                except queue.Empty:
                    continue
                if answer is None:
                    raise ConnectionError(f"bridge {self.name} link went down")
                rtype, rbody = answer
                if rtype == R_ERROR:
                    raise ConnectionError(f"bridge {self.name} refused the request (code {rbody[:1].hex()})")
                if rtype != expect:
                    raise ConnectionError(f"bridge {self.name} answered {rtype:#x}, expected {expect:#x}")
                self._consecutive_timeouts = 0
                return rbody
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= 3:
                self._mark_down("no answer")
            raise ConnectionError(f"bridge {self.name} did not answer")
        finally:
            with self._state_lock:
                self._pending.pop(seq, None)

    # -- public API ---------------------------------------------------------

    def xfer(self, packet: bytes, bus_timeout_ms: int = BUS_TIMEOUT_MS) -> bytes | None:
        """Run one instruction packet on the bridge's bus; the status packet,
        or None when the servo did not answer."""
        body = self._request(T_XFER, bytes([bus_timeout_ms]) + packet, expect=R_XFER)
        if len(body) < 1 or body[0] != XFER_OK:
            return None
        return bytes(body[1:]) or None

    def set_poll(self, servo_ids: list[int], period_ms: int = DEFAULT_POLL_PERIOD_MS) -> None:
        """The servos whose state the bridge should stream, and how often."""
        ids = sorted(set(int(i) for i in servo_ids))[:MAX_POLL_IDS]
        self._poll_ids = ids
        self._poll_period_ms = int(period_ms)
        if self._up:
            self._request(T_SET_POLL, self._poll_body(), expect=R_SET_POLL)

    def _poll_body(self) -> bytes:
        return struct.pack("<HB", self._poll_period_ms, len(self._poll_ids)) + bytes(self._poll_ids)

    def watch(self, servo_id: int) -> None:
        """Add a servo to the stream without waiting for the answer."""
        if servo_id in self._poll_ids or len(self._poll_ids) >= MAX_POLL_IDS:
            return
        self._poll_ids = sorted(self._poll_ids + [servo_id])
        try:
            self._send(T_SET_POLL, 0, self._poll_body())
        except ConnectionError:
            pass  # sent again with the handshake after a reconnect

    def configure(self, watchdog_ms: int = 1500, release_on_loss: bool = False) -> None:
        """What the bridge does when it hears nothing from the host."""
        self._request(T_CONFIG, struct.pack("<HB", watchdog_ms, 1 if release_on_loss else 0), expect=R_CONFIG)

    def latest(self, servo_id: int, max_age: float) -> dict | None:
        """The newest streamed sample of a servo if it is no older than
        ``max_age`` seconds. The first ask for a servo puts it on the stream."""
        if servo_id not in self._poll_ids:
            self.watch(servo_id)
            return None
        sample = self._samples.get(servo_id)
        if sample is None or not self._up:
            return None
        if time.monotonic() - sample["t"] > max_age:
            return None
        return dict(sample)

    @staticmethod
    def _parse_hello(body: bytes) -> dict:
        if len(body) < 9:
            raise ConnectionError("bridge sent a short hello")
        proto, major, minor, uptime_ms, poll_count, flags = struct.unpack_from("<BBBIBB", body, 0)
        return {
            "proto": proto,
            "firmware": f"{major}.{minor}",
            "uptime_ms": uptime_ms,
            "poll_count": poll_count,
            "flags": flags,
        }


# ---------------------------------------------------------------------------
# Registry and the bus
# ---------------------------------------------------------------------------

_LINKS: dict[str, BtLink] = {}
_LINKS_LOCK = threading.Lock()


def is_bridge_port(port: str) -> bool:
    return port.startswith("bt:") or port.startswith("btserial:")


def _make_transport(port: str):
    if port.startswith("btserial:"):
        return SerialTransport(port[len("btserial:"):])
    spec = port[len("bt:"):]
    channel = 1
    if "/" in spec:
        spec, channel_text = spec.rsplit("/", 1)
        channel = int(channel_text)
    return RfcommTransport(spec, channel)


def get_link(port: str, factory: Callable[[str], BtLink] | None = None) -> BtLink:
    """The one link for a port string, created on first use. A link lives for
    the whole process: a bus service closing its bus after an operation must
    not drop the radio connection."""
    with _LINKS_LOCK:
        link = _LINKS.get(port)
        if link is None:
            link = factory(port) if factory else BtLink(_make_transport(port), name=port)
            _LINKS[port] = link
        return link


def close_all_links() -> None:
    with _LINKS_LOCK:
        links = list(_LINKS.values())
        _LINKS.clear()
    for link in links:
        link.close()


class BtScServoBus(ScServoBus):
    """``ScServoBus`` whose transport is the Bluetooth bridge."""

    def __init__(self, port: str, baudrate: int = 1_000_000, timeout: float = 0.05):
        # No serial port here: the bridge owns the real one, at its own speed.
        self._lock = threading.Lock()
        self._port = port
        self._link = get_link(port)
        if not self._link.up:
            self._link.connect()

    def close(self) -> None:
        pass  # the link is shared and stays up

    def _exchange(self, pkt: bytes) -> bytes | None:
        return self._link.xfer(pkt)

    def latest(self, servo_id: int, max_age: float = 0.25) -> dict | None:
        return self._link.latest(servo_id, max_age)

    def set_poll(self, servo_ids: list[int], period_ms: int = DEFAULT_POLL_PERIOD_MS) -> None:
        self._link.set_poll(servo_ids, period_ms)

    def link_info(self) -> dict:
        return {
            "port": self._port,
            "up": self._link.up,
            "hello": self._link.hello,
            "reconnects": self._link.reconnects,
            "bad_frames": self._link.bad_frames,
        }
