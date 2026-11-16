"""The Bluetooth bridge host side against a simulated bridge and SC servos.

No radio and no pyserial are needed: the link runs over a socket pair, and a
thread plays the ESP32 firmware (same frame format, same dedupe rule) in front
of simulated servos with real stops.
"""

import socket
import struct
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

serial_stub = types.ModuleType("serial")
serial_stub.Serial = object
sys.modules.setdefault("serial", serial_stub)

from hardware import bt_servo_bus as bt
from hardware.waveshare_servo import (
    MotionWatch,
    WaveshareServoMotor,
    _checksum,
    calibrate_servo,
    decode_state,
    wait_settled,
)


class SimServo:
    """An SC servo: moves linearly to its goal in the commanded time and stops
    at its physical stops."""

    def __init__(self, servo_id: int, start: int = 500, stops: tuple[int, int] = (100, 900)):
        self.id = servo_id
        self.stops = stops
        self.mem = bytearray(128)
        self._from = float(start)
        self._goal = float(start)
        self._t0 = 0.0
        self._dur = 0.0
        self.writes = 0
        self.mem[62] = 74  # 7.4 V
        self.mem[63] = 30

    def position(self) -> int:
        if self._dur <= 0:
            frac = 1.0
        else:
            frac = min(1.0, (time.monotonic() - self._t0) / self._dur)
        pos = self._from + (self._goal - self._from) * frac
        return int(round(max(self.stops[0], min(self.stops[1], pos))))

    def handle(self, instruction: int, params: bytes) -> bytes | None:
        if instruction == 0x01:
            return b""
        if instruction == 0x02:
            address, count = params[0], params[1]
            if address == 56:
                pos = self.position()
                moving = pos != int(round(self._goal))
                blob = struct.pack(">HHH", pos, 100 if moving else 0, 0) + bytes([self.mem[62], self.mem[63]])
                return blob[:count]
            return bytes(self.mem[address:address + count])
        if instruction == 0x03:
            self.writes += 1
            address, data = params[0], params[1:]
            if address == 42 and len(data) >= 4:
                goal, ms = struct.unpack(">HH", data[:4])
                self._from = float(self.position())
                self._goal = float(goal)
                self._t0 = time.monotonic()
                self._dur = ms / 1000.0
            self.mem[address:address + len(data)] = data
            return b""
        return None


def status_packet(servo_id: int, payload: bytes) -> bytes:
    head = bytes([servo_id, len(payload) + 2, 0])
    return b"\xFF\xFF" + head + payload + bytes([_checksum(head + payload)])


class FakeBridge(threading.Thread):
    """The ESP32 firmware, in Python."""

    def __init__(self, sock, servos, drop_first_xfer_answer=False, telemetry_s=0.02):
        super().__init__(daemon=True)
        self.sock = sock
        self.servos = {s.id: s for s in servos}
        self.drop_first = drop_first_xfer_answer
        self.telemetry_s = telemetry_s
        self.poll_ids: list[int] = []
        self.xfers_run = 0
        self._last = None  # (seq, crc, frame)
        self._halt = threading.Event()
        self._tx = threading.Lock()

    def send(self, frame_type, seq, body=b""):
        data = bt.encode_frame(frame_type, seq, body)
        with self._tx:
            try:
                self.sock.sendall(data)
            except OSError:
                self._halt.set()

    def stop(self):
        self._halt.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def run(self):
        decoder = bt.FrameDecoder()
        next_telemetry = time.monotonic()
        while not self._halt.is_set():
            try:
                ready = self._readable()
            except (OSError, ValueError):
                return
            if ready:
                try:
                    data = self.sock.recv(512)
                except OSError:
                    return
                if not data:
                    return
                for frame_type, seq, body in decoder.feed(data):
                    self.handle(frame_type, seq, body)
            now = time.monotonic()
            if self.poll_ids and now >= next_telemetry:
                next_telemetry = now + self.telemetry_s
                self.send_telemetry()

    def _readable(self):
        import select

        r, _, _ = select.select([self.sock], [], [], 0.005)
        return bool(r)

    def send_telemetry(self):
        entries = b""
        for servo_id in self.poll_ids:
            servo = self.servos.get(servo_id)
            if servo is None:
                entries += struct.pack("<BBHHHBB", servo_id, 0, 0, 0, 0, 0, 0)
                continue
            pos = servo.position()
            moving = pos != int(round(servo._goal))
            entries += struct.pack("<BBHHHBB", servo_id, 1, pos, 100 if moving else 0, 0, servo.mem[62], servo.mem[63])
        body = struct.pack("<IB", int(time.monotonic() * 1000) & 0xFFFFFFFF, len(self.poll_ids)) + entries
        self.send(bt.R_TELEMETRY, 0, body)

    def handle(self, frame_type, seq, body):
        if frame_type == bt.T_HELLO:
            self._last = None
            self.send(bt.R_HELLO, seq, struct.pack("<BBBIBB", bt.PROTO_VERSION, 1, 0, 1234, len(self.poll_ids), 0))
        elif frame_type == bt.T_SET_POLL:
            _, count = struct.unpack_from("<HB", body, 0)
            self.poll_ids = list(body[3:3 + count])
            if seq:
                self.send(bt.R_SET_POLL, seq, bytes([count]))
        elif frame_type == bt.T_XFER:
            crc = bt.crc16(body)
            if self._last and self._last[0] == seq and self._last[1] == crc:
                answer = self._last[2]
            else:
                self.xfers_run += 1
                answer = self.run_xfer(body[1:])
                self._last = (seq, crc, answer)
                if self.drop_first:
                    self.drop_first = False
                    return
            self.send(bt.R_XFER, seq, answer)
        elif frame_type == bt.T_CONFIG:
            self.send(bt.R_CONFIG, seq)

    def run_xfer(self, pkt: bytes) -> bytes:
        servo = self.servos.get(pkt[2])
        if servo is None:
            return bytes([bt.XFER_TIMEOUT])
        payload = servo.handle(pkt[4], pkt[5:-1])
        if payload is None:
            return bytes([bt.XFER_TIMEOUT])
        return bytes([bt.XFER_OK]) + status_packet(servo.id, payload)


class PairTransport(bt.SocketTransport):
    """Every connect makes a new socket pair and starts a new bridge on the far end."""

    def __init__(self, servos, **bridge_kwargs):
        super().__init__(None)
        self.servos = servos
        self.bridge_kwargs = bridge_kwargs
        self.bridge: FakeBridge | None = None
        self.refuse = False

    def connect(self):
        if self.refuse:
            raise ConnectionError("bridge is away")
        host, far = socket.socketpair()
        self._sock = host
        self.bridge = FakeBridge(far, self.servos, **self.bridge_kwargs)
        self.bridge.start()

    def close(self):
        super().close()
        if self.bridge is not None:
            self.bridge.stop()


def make_bus(servos, **bridge_kwargs):
    transport = PairTransport(servos, **bridge_kwargs)
    link = bt.BtLink(transport, name="test", request_timeout_s=0.15, reconnect_s=0.05, heartbeat_s=0.05)
    port = f"bt:test-{id(link)}"
    bt.get_link(port, factory=lambda _p: link)
    return bt.BtScServoBus(port), link, transport


class FramingTests(unittest.TestCase):
    def test_cobs_roundtrip_with_zeros_and_long_runs(self):
        for data in (b"", b"\x00", b"\x01\x00\x02", bytes(range(256)) * 3, b"\x00" * 300, b"\xAA" * 254, b"\xAA" * 255):
            encoded = bt.cobs_encode(data)
            self.assertNotIn(0, encoded)
            self.assertEqual(bt.cobs_decode(encoded), data)

    def test_frame_roundtrip_and_split_delivery(self):
        frame = bt.encode_frame(bt.T_XFER, 7, b"\x00\x01\x02\x00")
        decoder = bt.FrameDecoder()
        frames = []
        for byte in frame:
            frames += decoder.feed(bytes([byte]))
        self.assertEqual(frames, [(bt.T_XFER, 7, b"\x00\x01\x02\x00")])

    def test_damaged_frame_is_dropped_and_the_next_one_survives(self):
        good = bt.encode_frame(bt.T_HELLO, 1, b"abc")
        bad = bytearray(bt.encode_frame(bt.T_HELLO, 2, b"abc"))
        bad[3] ^= 0x55
        decoder = bt.FrameDecoder()
        frames = decoder.feed(bytes(bad) + good)
        self.assertEqual(frames, [(bt.T_HELLO, 1, b"abc")])
        self.assertEqual(decoder.bad_frames, 1)


class BusTests(unittest.TestCase):
    def test_ping_read_and_move_through_the_bridge(self):
        servo = SimServo(1, start=500)
        bus, link, _ = make_bus([servo])
        self.assertTrue(bus.ping(1))
        self.assertFalse(bus.ping(9))  # nobody there: the bridge reports a bus timeout
        self.assertEqual(bus.read_position(1), 500)
        self.assertTrue(bus.move_to(1, 700, 100))
        time.sleep(0.2)
        self.assertEqual(bus.read_position(1), 700)
        link.close()

    def test_a_repeated_request_runs_on_the_bus_once(self):
        servo = SimServo(1)
        bus, link, transport = make_bus([servo], drop_first_xfer_answer=True)
        before = servo.writes
        self.assertTrue(bus.move_to(1, 650, 50))  # first answer is lost, the host asks again
        self.assertEqual(servo.writes - before, 1)
        link.close()

    def test_streamed_samples_replace_reads(self):
        servo = SimServo(1, start=300)
        bus, link, _ = make_bus([servo])
        self.assertIsNone(bus.latest(1, 0.25))  # first ask puts the servo on the stream
        time.sleep(0.15)
        sample = bus.latest(1, 0.25)
        self.assertIsNotNone(sample)
        self.assertEqual(sample["position"], 300)
        self.assertEqual(sample["voltage"], 74)
        link.close()

    def test_link_loss_raises_and_the_link_comes_back(self):
        servo = SimServo(1)
        bus, link, transport = make_bus([servo])
        self.assertTrue(bus.ping(1))
        transport.refuse = True
        transport.bridge.stop()
        deadline = time.monotonic() + 2
        while link.up and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(link.up)
        with self.assertRaises(ConnectionError):
            bus.ping(1)
        transport.refuse = False
        deadline = time.monotonic() + 3
        while not link.up and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(link.up)
        self.assertTrue(bus.ping(1))
        link.close()


class MotionTests(unittest.TestCase):
    def test_watch_arrives_blocks_and_times_out(self):
        def state(pos, t):
            return decode_state(struct.pack(">HHH", pos, 0, 0) + bytes([74, 30]), t)

        watch = MotionWatch(500, started=0.0, deadline=5.0)
        self.assertIsNone(watch.update(state(300, 0.05), 0.05))
        self.assertIsNone(watch.update(state(499, 0.10), 0.10))
        self.assertEqual(watch.update(state(500, 0.15), 0.15), "ok")

        watch = MotionWatch(500, started=0.0, deadline=5.0)
        self.assertIsNone(watch.update(state(300, 0.05), 0.05))
        self.assertIsNone(watch.update(state(301, 0.20), 0.20))
        self.assertEqual(watch.update(state(301, 0.40), 0.40), "blocked")

        watch = MotionWatch(500, started=0.0, deadline=1.0)
        self.assertIsNone(watch.update(state(300, 0.05), 0.05))
        self.assertEqual(watch.update(state(320, 1.5), 1.5), "timeout")

    def test_the_same_sample_is_not_counted_twice(self):
        watch = MotionWatch(500, started=0.0, deadline=5.0)
        stuck = decode_state(struct.pack(">HHH", 301, 0, 0) + bytes([74, 30]), 0.05)
        for now in (0.06, 0.2, 0.4, 0.8):
            self.assertIsNone(watch.update(stuck, now))

    def _motor(self, servo):
        bus, link, _ = make_bus([servo])
        motor = WaveshareServoMotor(bus, servo.id)
        motor._min_limit, motor._max_limit = 150, 850
        motor._open_position, motor._closed_position = 150, 850
        return motor, bus, link

    def test_open_is_stopped_when_the_servo_arrives_and_then_releases(self):
        servo = SimServo(1, start=850)
        motor, bus, link = self._motor(servo)
        self.assertTrue(motor.open())
        self.assertFalse(motor.stopped)  # the move takes 300 ms; nothing is assumed
        self.assertTrue(motor.wait_stopped(2.0))
        self.assertEqual(motor.move_result, "ok")
        self.assertEqual(servo.mem[40], 0)  # torque released on arrival
        self.assertTrue(motor.isOpen())
        link.close()

    def test_a_blocked_flap_is_reported_not_waited_out(self):
        servo = SimServo(1, start=850, stops=(400, 900))  # something holds it at 400
        motor, bus, link = self._motor(servo)
        started = time.monotonic()
        self.assertTrue(motor.open())
        self.assertTrue(motor.wait_stopped(3.0))
        self.assertEqual(motor.move_result, "blocked")
        self.assertLess(time.monotonic() - started, 2.0)
        link.close()

    def test_configured_speed_sets_the_move_time_from_the_distance(self):
        servo = SimServo(1, start=850)
        motor, bus, link = self._motor(servo)
        motor.set_motion_speeds(open_speed=180, close_speed=None, homing_speed=None)
        motor._current_position = 850
        # the whole range (700 units = 180 degrees) at 180 degrees/s is one second
        self.assertEqual(motor._move_time_ms(150, 180), 1000)
        self.assertEqual(motor._move_time_ms(150, None), 300)
        link.close()

    def test_calibration_finds_the_stops_from_feedback(self):
        servo = SimServo(1, start=500, stops=(180, 820))
        bus, link, _ = make_bus([servo])
        with patch("hardware.waveshare_servo._CAL_STEP_TIME_MS", 100):
            lo, hi = calibrate_servo(bus, 1)
        self.assertLessEqual(abs(lo - (180 + 5)), 8)
        self.assertLessEqual(abs(hi - (820 - 5)), 8)
        link.close()

    def test_wait_settled_reports_a_silent_servo(self):
        bus, link, _ = make_bus([SimServo(1)])
        result, pos, _ = wait_settled(bus, 7, 500, timeout_s=2.0)
        self.assertEqual(result, "no_feedback")
        link.close()


if __name__ == "__main__":
    unittest.main()
