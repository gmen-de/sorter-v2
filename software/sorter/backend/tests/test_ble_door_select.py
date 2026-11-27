import sys
import types
import unittest

bleak_stub = types.ModuleType("bleak")
bleak_stub.BleakClient = object
bleak_stub.BleakScanner = object
sys.modules.setdefault("bleak", bleak_stub)

from hardware.waveshare_ble_bridge import BleGateServo, GateSelectResult, parse_select_response
from subsystems.distribution.positioning import Positioning


class _Bridge:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[str] = []
        self.supports_select = None

    def select_gate(self, gate: int, **_kwargs) -> str:
        self.calls.append(f"SELECT {gate}")
        return self.response


class ParseTests(unittest.TestCase):
    def test_ok(self) -> None:
        self.assertEqual(GateSelectResult(), parse_select_response("SELECT 3 OK", 3))

    def test_blocked_lists_the_gates(self) -> None:
        self.assertEqual((2, 5), parse_select_response("SELECT 3 BLOCKED 2 5", 3).blocked)

    def test_error_word(self) -> None:
        self.assertEqual("OFFLINE", parse_select_response("SELECT 3 ERR OFFLINE", 3).error)

    def test_an_older_firmware_is_none(self) -> None:
        self.assertIsNone(parse_select_response("ERR UNKNOWN_CMD", 3))

    def test_an_answer_for_another_gate_is_refused(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_select_response("SELECT 4 OK", 3)


class ServoTests(unittest.TestCase):
    def test_select_sends_one_command(self) -> None:
        bridge = _Bridge("SELECT 3 OK")
        self.assertEqual(GateSelectResult(), BleGateServo(bridge, gate=3).select_exclusive())
        self.assertEqual(["SELECT 3"], bridge.calls)

    def test_an_older_firmware_is_asked_once(self) -> None:
        bridge = _Bridge("ERR UNKNOWN_CMD")
        servo = BleGateServo(bridge, gate=3)
        self.assertIsNone(servo.select_exclusive())
        self.assertIsNone(servo.select_exclusive())
        self.assertEqual(["SELECT 3"], bridge.calls)

    def test_an_inverted_servo_is_left_to_the_per_gate_path(self) -> None:
        bridge = _Bridge("SELECT 3 OK")
        self.assertIsNone(BleGateServo(bridge, gate=3, invert=True).select_exclusive())
        self.assertEqual([], bridge.calls)


class _Logger:
    def warning(self, *_a, **_k) -> None: ...
    def info(self, *_a, **_k) -> None: ...


def _positioning(servos) -> Positioning:
    p = Positioning.__new__(Positioning)
    p.irl = types.SimpleNamespace(servos=servos)
    p.logger = _Logger()
    p.marked: list[tuple[int, str]] = []
    p._markLayerUnavailable = lambda layer, reason: p.marked.append((layer, reason))
    return p


class DoorSelectTests(unittest.TestCase):
    def test_every_servo_on_the_board_is_set_by_the_one_command(self) -> None:
        bridge = _Bridge("SELECT 2 OK")
        servos = [BleGateServo(bridge, gate=g) for g in (1, 2, 3)]
        p = _positioning(servos)
        self.assertEqual({0, 1, 2}, p._selectDoorOnBoard(1, servos[1]))
        self.assertEqual(["SELECT 2"], bridge.calls)
        self.assertEqual([], p.marked)

    def test_a_servo_that_did_not_open_is_marked_but_the_door_goes_on(self) -> None:
        bridge = _Bridge("SELECT 2 BLOCKED 3")
        servos = [BleGateServo(bridge, gate=g) for g in (1, 2, 3)]
        p = _positioning(servos)
        self.assertEqual({0, 1, 2}, p._selectDoorOnBoard(1, servos[1]))
        self.assertEqual([2], [layer for layer, _ in p.marked])

    def test_a_target_that_did_not_close_fails_the_door(self) -> None:
        bridge = _Bridge("SELECT 2 BLOCKED 2")
        servos = [BleGateServo(bridge, gate=g) for g in (1, 2)]
        p = _positioning(servos)
        self.assertIs(False, p._selectDoorOnBoard(1, servos[1]))

    def test_an_inverted_servo_stays_on_the_per_gate_path(self) -> None:
        bridge = _Bridge("SELECT 2 OK")
        servos = [BleGateServo(bridge, gate=1, invert=True), BleGateServo(bridge, gate=2)]
        p = _positioning(servos)
        self.assertEqual({1}, p._selectDoorOnBoard(1, servos[1]))

    def test_an_older_firmware_falls_back_to_one_door_at_a_time(self) -> None:
        bridge = _Bridge("ERR UNKNOWN_CMD")
        servos = [BleGateServo(bridge, gate=1), BleGateServo(bridge, gate=2)]
        self.assertIsNone(_positioning(servos)._selectDoorOnBoard(1, servos[1]))


if __name__ == "__main__":
    unittest.main()
