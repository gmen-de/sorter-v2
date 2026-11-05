import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import types
import unittest
from unittest.mock import patch


serial_stub = types.ModuleType("serial")
serial_tools_stub = types.ModuleType("serial.tools")
serial_list_ports_stub = types.ModuleType("serial.tools.list_ports")
serial_tools_stub.list_ports = serial_list_ports_stub
serial_stub.tools = serial_tools_stub
sys.modules.setdefault("serial", serial_stub)
sys.modules.setdefault("serial.tools", serial_tools_stub)
sys.modules.setdefault("serial.tools.list_ports", serial_list_ports_stub)

global_config_stub = types.ModuleType("global_config")
global_config_stub.GlobalConfig = object
sys.modules.setdefault("global_config", global_config_stub)

irl_stub = types.ModuleType("irl")
parse_user_toml_stub = types.ModuleType("irl.parse_user_toml")
parse_user_toml_stub.ServoChannelConfig = object
parse_user_toml_stub.WaveshareServoConfig = object
parse_user_toml_stub.WaveshareBleServoConfig = object
irl_stub.parse_user_toml = parse_user_toml_stub
sys.modules.setdefault("irl", irl_stub)
sys.modules.setdefault("irl.parse_user_toml", parse_user_toml_stub)

bleak_stub = types.ModuleType("bleak")
bleak_stub.BleakClient = object
sys.modules.setdefault("bleak", bleak_stub)

machine_platform_stub = types.ModuleType("machine_platform")
control_board_stub = types.ModuleType("machine_platform.control_board")
control_board_stub.ControlBoard = object
machine_platform_stub.control_board = control_board_stub
sys.modules.setdefault("machine_platform", machine_platform_stub)
sys.modules.setdefault("machine_platform.control_board", control_board_stub)

module_path = Path(__file__).resolve().parents[1] / "machine_platform" / "servo_controller.py"
spec = importlib.util.spec_from_file_location("servo_controller_under_test", module_path)
assert spec is not None and spec.loader is not None
servo_controller = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = servo_controller
spec.loader.exec_module(servo_controller)

LayerServoAssignment = servo_controller.LayerServoAssignment
WaveshareServoController = servo_controller.WaveshareServoController
WaveshareBleServoController = servo_controller.WaveshareBleServoController


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return

    def warning(self, *_args, **_kwargs) -> None:
        return


class WaveshareServoControllerTests(unittest.TestCase):
    def test_missing_trailing_assignments_are_treated_as_unassigned(self) -> None:
        controller = WaveshareServoController(
            SimpleNamespace(logger=_Logger()),
            port=None,
            assignments=[
                LayerServoAssignment(id=2, invert=True),
                LayerServoAssignment(id=None, invert=False),
            ],
            mcu_ports=[],
        )
        layout = SimpleNamespace(
            layers=[
                SimpleNamespace(enabled=True),
                SimpleNamespace(enabled=False),
                SimpleNamespace(enabled=False),
                SimpleNamespace(enabled=False),
            ]
        )

        assignments = controller._iter_layer_assignments(layout)

        self.assertEqual(4, len(assignments))
        self.assertEqual(2, assignments[0][1].id)
        self.assertIsNone(assignments[1][1].id)
        self.assertIsNone(assignments[2][1].id)
        self.assertIsNone(assignments[3][1].id)


class WaveshareBleServoControllerTests(unittest.TestCase):
    def test_missing_assignments_default_to_sequential_gates(self) -> None:
        controller = WaveshareBleServoController(
            SimpleNamespace(logger=_Logger()),
            device="AA:BB:CC:DD:EE:FF",
            assignments=[LayerServoAssignment(id=5, invert=True)],
        )
        layout = SimpleNamespace(
            layers=[
                SimpleNamespace(enabled=True),
                SimpleNamespace(enabled=True),
                SimpleNamespace(enabled=True),
            ]
        )

        assignments = controller._iter_layer_assignments(layout)

        self.assertEqual(3, len(assignments))
        self.assertEqual(5, assignments[0][1].id)
        # No explicit config for layers 1 and 2 -> gate = layer index + 1,
        # matching the order servos are LEARNed onto the bus in.
        self.assertEqual(2, assignments[1][1].id)
        self.assertEqual(3, assignments[2][1].id)

    def test_configured_gate_missing_from_post_connect_scan_is_flagged(self) -> None:
        class _FakeBridge:
            known_gates = [1, 2]  # gate 3 never answered the post-connect scan

            def connect(self) -> None:
                return

        controller = WaveshareBleServoController(
            SimpleNamespace(logger=_Logger()),
            device="AA:BB:CC:DD:EE:FF",
            assignments=[],  # defaults to gates 1, 2, 3 for 3 layers
        )
        layout = SimpleNamespace(
            layers=[SimpleNamespace(enabled=True) for _ in range(3)]
        )

        with patch("hardware.waveshare_ble_bridge.get_waveshare_ble_bridge", return_value=_FakeBridge()):
            servos = controller.create_layer_servos(layout)

        self.assertEqual(3, len(servos))
        missing_gate_issues = [issue for issue in controller.issues if issue.get("servo_id") == 3]
        self.assertEqual(1, len(missing_gate_issues))
        self.assertEqual(0, len([i for i in controller.issues if i.get("servo_id") in (1, 2)]))

    def test_missing_device_marks_enabled_layers_offline(self) -> None:
        controller = WaveshareBleServoController(
            SimpleNamespace(logger=_Logger()),
            device="",
            assignments=[],
        )
        layout = SimpleNamespace(layers=[SimpleNamespace(enabled=True)])

        servos = controller.create_layer_servos(layout)

        self.assertEqual(1, len(servos))
        self.assertFalse(servos[0].available)
        self.assertEqual(1, len(controller.issues))


if __name__ == "__main__":
    unittest.main()
