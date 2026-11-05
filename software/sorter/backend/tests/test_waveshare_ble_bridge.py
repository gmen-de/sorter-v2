import sys
import types
import unittest

bleak_stub = types.ModuleType("bleak")
bleak_stub.BleakClient = object
sys.modules.setdefault("bleak", bleak_stub)

from hardware.waveshare_ble_bridge import BleGateServo, WaveshareBleBridge, _firmware_word


class _FakeBridge:
    def __init__(self):
        self.calls: list[str] = []
        self.open_response = "1 OPEN"
        self.close_response = "1 CLOSED"
        self.status_response = "1 OPEN"
        self.calibrate_response = "CALIBRATED 1"

    def open_gate(self, gate: int, **_kwargs) -> str:
        self.calls.append(f"OPEN {gate}")
        return self.open_response

    def close_gate(self, gate: int, **_kwargs) -> str:
        self.calls.append(f"CLOSE {gate}")
        return self.close_response

    def status_gate(self, gate: int, **_kwargs) -> str:
        self.calls.append(f"STATUS {gate}")
        return self.status_response

    def calibrate_gate(self, gate: int, **_kwargs) -> str:
        self.calls.append(f"CALIBRATE {gate}")
        return self.calibrate_response


class FirmwareWordTests(unittest.TestCase):
    def test_parses_second_token(self) -> None:
        self.assertEqual("OPEN", _firmware_word("1 OPEN"))

    def test_err_response(self) -> None:
        self.assertEqual("ERR", _firmware_word("1 ERR UNCALIBRATED"))

    def test_empty_response(self) -> None:
        self.assertEqual("", _firmware_word(""))


class BleGateServoTests(unittest.TestCase):
    def test_open_sends_open_command_without_invert(self) -> None:
        bridge = _FakeBridge()
        servo = BleGateServo(bridge, gate=1, invert=False)

        self.assertTrue(servo.open())
        self.assertEqual(["OPEN 1"], bridge.calls)

    def test_open_sends_close_command_when_inverted(self) -> None:
        bridge = _FakeBridge()
        bridge.close_response = "1 CLOSED"
        servo = BleGateServo(bridge, gate=1, invert=True)

        # Physically inverted: "logical open" asks the firmware to CLOSE,
        # and a firmware CLOSED answer means logically open.
        self.assertTrue(servo.open())
        self.assertEqual(["CLOSE 1"], bridge.calls)

    def test_close_blocked_is_reported_as_blocked_regardless_of_invert(self) -> None:
        bridge = _FakeBridge()
        # invert=True means close() asks the firmware to OPEN (see the
        # non-inverted test above) - that's the call whose response decides
        # blocked/not, not close_gate's.
        bridge.open_response = "1 BLOCKED"
        servo = BleGateServo(bridge, gate=1, invert=True)

        self.assertFalse(servo.close())
        self.assertEqual(["OPEN 1"], bridge.calls)
        self.assertEqual("blocked", servo._last_state)

    def test_is_open_reads_status(self) -> None:
        bridge = _FakeBridge()
        bridge.status_response = "1 OPEN"
        servo = BleGateServo(bridge, gate=1, invert=False)

        self.assertTrue(servo.isOpen())
        self.assertFalse(servo.isClosed())

    def test_is_open_inverted_swaps_meaning(self) -> None:
        bridge = _FakeBridge()
        bridge.status_response = "1 CLOSED"
        servo = BleGateServo(bridge, gate=1, invert=True)

        self.assertTrue(servo.isOpen())

    def test_feedback_reports_error_on_exception(self) -> None:
        class _FailingBridge(_FakeBridge):
            def status_gate(self, gate: int, **_kwargs) -> str:
                raise TimeoutError("no response")

        servo = BleGateServo(_FailingBridge(), gate=1, invert=False)
        feedback = servo.feedback()

        self.assertFalse(feedback["available"])
        self.assertIn("error", feedback)

    def test_recalibrate_raises_on_failure(self) -> None:
        bridge = _FakeBridge()
        bridge.calibrate_response = "CALIBRATE ERR 1"
        servo = BleGateServo(bridge, gate=1, invert=False)

        with self.assertRaises(RuntimeError):
            servo.recalibrate()


class ReadyInventoryTests(unittest.TestCase):
    def test_ready_push_is_parsed_and_not_queued_as_a_response(self) -> None:
        bridge = WaveshareBleBridge("AA:BB:CC:DD:EE:FF")

        bridge._on_notify(0, bytearray(b"READY GATES 11 12 13\n"))

        self.assertEqual([11, 12, 13], bridge.known_gates)
        self.assertTrue(bridge._inventory_event.is_set())
        self.assertTrue(bridge._responses.empty())

    def test_ready_push_with_no_gates(self) -> None:
        bridge = WaveshareBleBridge("AA:BB:CC:DD:EE:FF")

        bridge._on_notify(0, bytearray(b"READY GATES\n"))

        self.assertEqual([], bridge.known_gates)
        self.assertTrue(bridge._inventory_event.is_set())

    def test_plain_gates_line_is_queued_as_a_list_response(self) -> None:
        # The LIST command's own response ("GATES ...", no READY prefix)
        # must still reach command()/list_gates() through the normal queue.
        bridge = WaveshareBleBridge("AA:BB:CC:DD:EE:FF")

        bridge._on_notify(0, bytearray(b"GATES 11 12\n"))

        self.assertEqual("GATES 11 12", bridge._responses.get_nowait())
        self.assertFalse(bridge._inventory_event.is_set())

    def test_wait_for_inventory_times_out_without_a_push(self) -> None:
        bridge = WaveshareBleBridge("AA:BB:CC:DD:EE:FF")

        self.assertIsNone(bridge.wait_for_inventory(timeout=0.05))


if __name__ == "__main__":
    unittest.main()
