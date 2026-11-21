"""Bench timing test: acceleration=5000, then 25 random bin moves on the
18-bin wheel (k=3), same angle math and MOVE_TO_STEP_POS approach as
chute_debug_ui.py's bin selector. Measures real elapsed time per move."""
import json
import math
import random
import struct
import time

from hardware.bus import MCUBus, MCUDevice, BaseCommandCode
from hardware.sorter_interface import StepperMotor
import logging


class GC:
    logger = logging.getLogger("bin_timing")


PORT = "/dev/ttyACM0"
CH = 0  # chute stepper channel

CHUTE_ENCODER_MOVE_TO_STEP_POS = 0x57
CHUTE_START_HOMING = 0x58
CHUTE_STEP_USTEPS_PER_REV = 7680
REHOME_EVERY = 5

FIRST_SECTION_OFFSET_DEG = 8.25
SECTION_WIDTH_DEG = 51.75
NUM_SECTIONS = 6
ROTATION_OFFSET_DEG = 34.125
K = 3  # 18 bins (3 per section)
NUM_BINS = NUM_SECTIONS * K


def angle_for_bin(bin_num: int) -> float:
    section, i = divmod(bin_num, K)
    start_s = FIRST_SECTION_OFFSET_DEG + section * (360.0 / NUM_SECTIONS)
    slot = SECTION_WIDTH_DEG / K
    a0 = (start_s + i * slot - ROTATION_OFFSET_DEG) % 360.0
    a1 = a0 + slot
    return (a0 + a1) / 2.0 % 360.0


bus = MCUBus(port=PORT)
dev = MCUDevice(bus, 0)
stepper = StepperMotor(dev, CH, GC())

stepper.set_speed_limits(16, 5000)
stepper.set_acceleration(5000)
print("speed limit set to 5000, acceleration set to 5000")

bins = [random.randrange(NUM_BINS) for _ in range(25)]
print("bin sequence:", bins)

results = []
for i, bin_num in enumerate(bins):
    angle = angle_for_bin(bin_num)
    target = round(angle / 360.0 * CHUTE_STEP_USTEPS_PER_REV)
    t0 = time.monotonic()
    dev.send_command(CHUTE_ENCODER_MOVE_TO_STEP_POS, 0, struct.pack("<i", target))
    deadline = t0 + 15
    while time.monotonic() < deadline:
        obs = json.loads(dev.send_command(BaseCommandCode.GET_OBSERVABILITY, 0, b"").payload.decode())
        if not obs.get("chute_step_active"):
            break
        time.sleep(0.02)
    elapsed = time.monotonic() - t0
    results.append((bin_num, angle, elapsed))
    print(f"{i+1:2d}/25  bin={bin_num:2d}  angle={angle:6.2f}  took={elapsed:.3f}s")
    time.sleep(1.0)

bus.close()

times = [r[2] for r in results]
print()
print(f"min={min(times):.3f}s  max={max(times):.3f}s  avg={sum(times)/len(times):.3f}s")
