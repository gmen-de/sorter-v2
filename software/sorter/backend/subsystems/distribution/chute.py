import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from global_config import GlobalConfig
from hardware_settings.chute import (
    DEFAULT_ENCODER_COUNTS_PER_REVOLUTION,
    DEFAULT_FIRST_SECTION_OFFSET_DEG,
    DEFAULT_NUM_SECTIONS,
    DEFAULT_OPERATING_SPEED_MICROSTEPS_PER_SEC,
    DEFAULT_POSITION_CHECK,
    DEFAULT_POSITION_CHECK_EVERY,
    DEFAULT_SECTION_WIDTH_DEG,
)
from irl.bin_layout import DistributionLayout

if TYPE_CHECKING:
    from hardware.sorter_interface import ChuteEncoder, StepperMotor, DigitalInputPin
    from .position_check import PositionCheck

GEAR_RATIO = 120 / 25  # 25T motor gear -> 25T idle gear -> 120T chute gear
CHUTE_MAX_ANGLE = 350

# Chute aiming geometry.
#
# The chute rotates within [0, 360) and hits a HARD STOP at either end — it
# can neither cross zero nor pass 360. So every reachable bin angle must stay
# inside that arc, and the home switch must sit such that the trailing margin
# of the last section still fits before 360.
#
# The geometry is described by three calibrated invariants, all of which are
# bin-count INDEPENDENT (this is what lets one calibration serve 1/2/3/5-bin
# layers alike):
#   - num_sections (N): how many bin sections tile the circle. The section
#     pitch — first-bin to first-bin across adjacent sections — is 360 / N.
#   - section_width_deg (W): the usable angular arc one section's bins occupy.
#     The physical pillar between sections is therefore (360/N - W).
#   - first_section_offset_deg (theta0): home-zero to the START edge of
#     section 0's usable arc. From home the chute turns clockwise this far to
#     reach the leading edge of the first section.
#
# Within a section of K bins, bins are equal-width slots and we aim at each
# slot's MIDPOINT:
#     bin_center = theta0 + section*(360/N) + (i + 0.5)*(W / K)
# The (i + 0.5) term is what centers a 1-bin layer and splits 2/3/5-bin
# layers evenly from the same W. See the agent-notes task
# "chute-aiming-calibration" for the full derivation and the calibration
# routine that measures theta0 and W. The defaults are in hardware_settings/chute.py.

HOME_SPEED_MICROSTEPS_PER_SEC = -1000
# The chute's moves start and end at this speed; they cruise at the operating speed.
MIN_SPEED_MICROSTEPS_PER_SEC = 16
HOME_TIMEOUT_MS = 15000


@dataclass
class BinAddress:
    layer_index: int
    section_index: int
    bin_index: int


class Chute:
    def __init__(
        self,
        gc: GlobalConfig,
        stepper: "StepperMotor",
        home_pin: "DigitalInputPin | None",
        layout: DistributionLayout,
        num_sections: int = DEFAULT_NUM_SECTIONS,
        section_width_deg: float = DEFAULT_SECTION_WIDTH_DEG,
        first_section_offset_deg: float = DEFAULT_FIRST_SECTION_OFFSET_DEG,
        endstop_active_high: bool = True,
        operating_speed_microsteps_per_second: int = DEFAULT_OPERATING_SPEED_MICROSTEPS_PER_SEC,
        position_check: bool = DEFAULT_POSITION_CHECK,
        position_check_every: int = DEFAULT_POSITION_CHECK_EVERY,
        chute_encoder: "ChuteEncoder | None" = None,
        encoder_counts_per_revolution: int = DEFAULT_ENCODER_COUNTS_PER_REVOLUTION,
    ):
        if (home_pin is None) == (chute_encoder is None):
            raise ValueError("Chute needs exactly one of home_pin or chute_encoder.")
        self.gc = gc
        self.logger = gc.logger
        self.stepper = stepper
        self.home_pin = home_pin
        self.chute_encoder = chute_encoder
        self.encoder_counts_per_revolution = max(1, int(encoder_counts_per_revolution))
        # Encoder count recorded exactly when the stepper's own position was
        # last zeroed (see _homeEncoder()) — the reference point for
        # position_check.checkPositionEncoder()'s drift comparison.
        self._encoder_zero_count: int | None = None
        self.layout = layout
        self.num_sections = max(1, int(num_sections))
        self.section_width_deg = float(section_width_deg)
        self.first_section_offset_deg = float(first_section_offset_deg)
        self.endstop_active_high = endstop_active_high
        self.operating_speed_microsteps_per_second = operating_speed_microsteps_per_second
        self._homed: bool = False
        # The position check (position_check.py): whether it runs, every how
        # many pieces, how many have gone since, and whether one is running.
        self.position_check = position_check
        self.position_check_every = max(1, int(position_check_every))
        self.pieces_since_check = 0
        self.checking = False

    @property
    def chute_position_sensor(self) -> str:
        return "endstop" if self.home_pin is not None else "encoder"

    @property
    def homed(self) -> bool:
        return self._homed

    def markUnhomed(self, reason: str = "") -> None:
        """Drop the home reference. A stall means lost steps -> the recorded position
        is no longer trustworthy, so force a re-home before the next precise move."""
        if self._homed:
            self.logger.warning(
                f"Chute: home reference invalidated{f' ({reason})' if reason else ''}; re-home required."
            )
        self._homed = False

    @property
    def section_pitch_deg(self) -> float:
        return 360.0 / self.num_sections

    @property
    def pillar_width_deg(self) -> float:
        # Derived for display / legacy callers: the dead arc between two
        # adjacent sections' usable regions.
        return self.section_pitch_deg - self.section_width_deg

    @property
    def usable_deg_per_section(self) -> float:
        return self.section_width_deg

    @property
    def first_bin_center(self) -> float:
        # Legacy alias (chute_stress, old settings page): the angle to the
        # center of bin 0 in section 0, using that section's real bin count.
        # Falls back to the bare section offset when the layout is empty.
        angle = self.angleForVirtualBin(
            0, 0, self._binsInFirstSection(), unclamped=True
        )
        return angle if angle is not None else self.first_section_offset_deg

    def setAimingCalibration(
        self,
        num_sections: int,
        section_width_deg: float,
        first_section_offset_deg: float,
        endstop_active_high: bool | None = None,
    ) -> None:
        self.num_sections = max(1, int(num_sections))
        self.section_width_deg = float(section_width_deg)
        self.first_section_offset_deg = float(first_section_offset_deg)
        if endstop_active_high is not None:
            self.endstop_active_high = endstop_active_high

    def setPositionCheck(self, enabled: bool, every: int) -> None:
        self.position_check = bool(enabled)
        self.position_check_every = max(1, int(every))

    def positionCheckDue(self) -> bool:
        return (
            self.position_check
            and self._homed
            and not self.checking
            and not self.gc.disable_chute
            and self.pieces_since_check >= self.position_check_every
        )

    def checkPosition(self) -> "PositionCheck":
        """Say how far the chute had drifted since it was last homed/checked.
        In endstop mode this finds the switch again and puts the chute back on
        its true position (blocks for about two seconds, so a caller on the
        main loop runs it on a thread); in encoder mode it is a non-blocking
        comparison against the magnet-ring encoder's own running count — no
        motion needed. See position_check.py."""
        from .position_check import checkPosition, checkPositionEncoder, recordCheck

        self.checking = True
        try:
            check = checkPositionEncoder(self) if self.chute_encoder is not None else checkPosition(self)
        finally:
            self.checking = False
            self.pieces_since_check = 0
        if not check.ok:
            self.logger.error(f"Chute: position check failed: {check.error}; the chute needs homing.")
        elif check.beyond_range:
            self.logger.error(
                f"Chute: position check found the switch at once: off by {check.drift_deg:g}° or more. "
                "The chute is back on its true position."
            )
        elif check.lost_steps:
            self.logger.warning(
                f"Chute: position check found {check.drift_deg:+.2f}° ({check.drift_steps:+d} µsteps) of drift "
                "since the last check: it lost steps. The chute is back on its true position."
            )
        else:
            self.logger.info(
                f"Chute: position check: {check.drift_deg:+.2f}° ({check.drift_steps:+d} µsteps), "
                f"read to {check.precision_deg}°, in {check.duration_s} s."
            )
        try:
            recordCheck(check)
        except Exception as e:
            self.logger.warning(f"Chute: could not record the position check: {e}")
        return check

    def setOperatingSpeed(self, operating_speed_microsteps_per_second: int) -> None:
        self.operating_speed_microsteps_per_second = max(1, int(operating_speed_microsteps_per_second))

    @property
    def raw_endstop_active(self) -> bool:
        if self.home_pin is None:
            raise RuntimeError("Chute: raw_endstop_active is not available in encoder mode.")
        return bool(self.home_pin.value)

    @property
    def endstop_triggered(self) -> bool:
        raw_value = self.raw_endstop_active
        return raw_value if self.endstop_active_high else not raw_value

    @property
    def encoder_count(self) -> int:
        if self.chute_encoder is None:
            raise RuntimeError("Chute: encoder_count is not available in endstop mode.")
        return self.chute_encoder.get_count()

    @property
    def encoder_synced(self) -> bool:
        if self.chute_encoder is None:
            raise RuntimeError("Chute: encoder_synced is not available in endstop mode.")
        return self.chute_encoder.is_index_synced()

    @property
    def current_angle(self) -> float:
        # Normalized to [0, 360): once encoder mode allows a move to wrap
        # past 0/360 (see _deltaToTarget()), the stepper's own raw open-loop
        # degrees can go negative or past 360, and this public angle must
        # still read as the sane physical value. Python's `%` on a positive
        # divisor already returns [0, 360) regardless of the dividend's
        # sign, so this needs no extra sign-handling. In endstop mode this
        # is a no-op: every move there is already clamped into [0, 360], so
        # the raw and normalized values always agreed anyway.
        return (self.stepper.position_degrees / GEAR_RATIO) % 360.0

    def _binsInSection(self, layer_index: int, section_index: int) -> int:
        try:
            return max(1, len(self.layout.layers[layer_index].sections[section_index].bins))
        except (IndexError, AttributeError):
            return 1

    def _binsInFirstSection(self) -> int:
        for layer in self.layout.layers:
            if layer.sections:
                return max(1, len(layer.sections[0].bins))
        return 1

    def angleForVirtualBin(
        self,
        section_index: int,
        bin_index: int,
        bins_in_section: int,
        num_sections: int | None = None,
        unclamped: bool = False,
    ) -> float | None:
        # Canonical aiming formula. Used both by getAngleForBin (real layout)
        # and by the calibration UI (arbitrary, customizable layouts) so there
        # is a single source of truth for where the chute points.
        n = max(1, int(num_sections)) if num_sections else self.num_sections
        k = max(1, int(bins_in_section))
        slot = self.section_width_deg / k
        angle = (
            self.first_section_offset_deg
            + section_index * (360.0 / n)
            + (bin_index + 0.5) * slot
        )
        if unclamped:
            return angle
        norm = angle % 360.0
        if self.chute_position_sensor == "endstop" and norm > CHUTE_MAX_ANGLE:
            # The chute travels [0, CHUTE_MAX_ANGLE] and can never cross the
            # home stop (the switch's own hammer is the reason), so wrap the
            # bin onto the circle: it is reachable iff its wrapped position
            # lands inside the travel window. The arc (CHUTE_MAX_ANGLE, 360)
            # just before home is the only dead wedge. Wrapping is what lets
            # a section that home cut through still serve the bins sitting
            # just clockwise of home — reached the long way round, never
            # across the stop. In encoder mode there is no such obstruction
            # (that's the whole point of the slip-ring/encoder swap), so
            # every bin is reachable there — no dead wedge at all.
            return None
        return norm

    def getAngleForBin(self, address: BinAddress) -> float | None:
        num_bins = self._binsInSection(address.layer_index, address.section_index)
        return self.angleForVirtualBin(
            address.section_index, address.bin_index, num_bins
        )

    def _setSpeed(self, speed: int) -> None:
        # The board keeps the last speed limits any caller sent (start-up's, a
        # jog's, a sweep's), so every chute move sets its own.
        self.stepper.set_speed_limits(MIN_SPEED_MICROSTEPS_PER_SEC, speed)

    def _deltaToTarget(self, target: float) -> tuple[float, float]:
        """The (delta_stepper_deg, clamped_target_deg) to reach `target`
        chute-degrees from here.

        Endstop mode: exactly the chute's original behavior — the chute
        physically cannot cross 0/360 (the switch is in the way), so the
        target is clamped into [0, 360] and the delta is the direct,
        possibly-long-way distance.

        Encoder mode: the chute can now turn freely either way (that's the
        whole point of the slip-ring/encoder swap), so this picks whichever
        direction is the SHORTER way around the circle — less travel, less
        time, not just "no limit". `current` is already 360-normalized
        (see current_angle); wrapping the raw difference into (-180, 180]
        gives the shortest signed delta in the same sign convention the
        direct-delta formula already used (positive target - current =
        positive delta), so there's no new direction ambiguity here beyond
        what endstop mode already has.
        """
        current = self.current_angle
        if self.chute_position_sensor == "encoder":
            target = target % 360.0
            delta_deg = ((target - current + 180.0) % 360.0) - 180.0
        else:
            target = max(0.0, min(360.0, target))
            delta_deg = target - current
        return delta_deg * GEAR_RATIO, target

    def moveToAngle(self, target: float, max_speed: int | None = None) -> int | None:
        """Start a move at the operating speed, or at `max_speed` when given; the
        estimated duration in ms, or None if the board refused it."""
        delta_stepper_angle, target = self._deltaToTarget(target)
        current = self.current_angle
        speed = int(max_speed or self.operating_speed_microsteps_per_second)
        estimated_ms = self.stepper.estimateMoveDegreesMs(delta_stepper_angle, max_speed=speed)

        if self.gc.disable_chute:
            self.logger.info(
                f"Chute: [DISABLED] would move from {current:.1f}° to {target:.1f}° (delta_stepper_deg={delta_stepper_angle:.2f}, est_ms={estimated_ms})"
            )
            return estimated_ms

        self._setSpeed(speed)
        self.logger.info(
            f"Chute: moving from {current:.1f}° to {target:.1f}° at {speed} µsteps/s (delta_stepper_deg={delta_stepper_angle:.2f}, est_ms={estimated_ms})"
        )
        if not self.stepper.move_degrees(delta_stepper_angle):
            return None
        return estimated_ms

    def isBinReachable(self, address: BinAddress) -> bool:
        return self.getAngleForBin(address) is not None

    def moveToBin(self, address: BinAddress) -> int | None:
        target = self.getAngleForBin(address)
        if target is None:
            self.logger.error(f"Chute: bin {address} is unreachable")
            return None
        self.logger.info(
            f"Chute: moveToBin layer={address.layer_index} section={address.section_index} bin={address.bin_index} -> {target:.2f}°"
        )
        estimated_ms = self.moveToAngle(target)
        if estimated_ms is not None:
            self.pieces_since_check += 1
        return estimated_ms

    def moveToAngleBlocking(self, target: float, timeout_buffer_ms: int = 0) -> int | None:
        """Move and wait; the estimated duration in ms, or None if it was refused or did not finish."""
        delta_stepper_angle, target = self._deltaToTarget(target)
        current = self.current_angle
        speed = int(self.operating_speed_microsteps_per_second)
        estimated_ms = self.stepper.estimateMoveDegreesMs(delta_stepper_angle, max_speed=speed)
        timeout_ms = max(1, estimated_ms + timeout_buffer_ms)

        if self.gc.disable_chute:
            self.logger.info(
                f"Chute: [DISABLED] would move from {current:.1f}° to {target:.1f}° (delta_stepper_deg={delta_stepper_angle:.2f}, est_ms={estimated_ms}, timeout_ms={timeout_ms})"
            )
            return estimated_ms

        self._setSpeed(speed)
        self.logger.info(
            f"Chute: moving(blocking) from {current:.1f}° to {target:.1f}° at {speed} µsteps/s (delta_stepper_deg={delta_stepper_angle:.2f}, est_ms={estimated_ms}, timeout_ms={timeout_ms})"
        )
        if not self.stepper.move_degrees_blocking(delta_stepper_angle, timeout_ms=timeout_ms):
            return None
        return estimated_ms

    def _backoffToFirstBin(self) -> bool:
        backoff_angle = self.angleForVirtualBin(
            0, 0, self._binsInFirstSection(), unclamped=True
        )
        if backoff_angle is None or backoff_angle <= 0.0:
            return True
        try:
            if self.moveToAngleBlocking(backoff_angle, timeout_buffer_ms=1500) is None:
                self.logger.error("Chute: backoff to bin 1 did not finish")
                return False
            self.logger.info(f"Chute: backed off to bin 1 ({backoff_angle:.2f}°)")
            return True
        except Exception as exc:
            self.logger.error(f"Chute: backoff to bin 1 failed: {exc}")
            return False

    def home(self) -> bool:
        if self.chute_encoder is not None:
            return self._homeEncoder()
        return self._homeEndstop()

    def _homeEncoder(self) -> bool:
        """Home against the magnet-ring encoder's index gap instead of a
        mechanical switch: rotate until the encoder reports an index-sync
        event, then zero the stepper's own position right there — the same
        "zero the counter at a known fixed physical landmark" convention the
        endstop case uses (see _homeEndstop()), just with the index gap as
        the landmark instead of the switch. _backoffToFirstBin() then moves
        from that zero to bin 0's leading edge exactly as it already does
        for the endstop case, so the two modes end up at the same place.

        Known approximation, not yet bench-verified: the stepper is stopped
        on Python's next poll after is_index_synced() flips true, not at the
        exact instant the firmware's ISR saw the index event, so there is a
        small poll-latency-proportional position error here that the
        endstop case does not have (its zero is set inside the firmware's
        own step-tick ISR). get_count_at_index() could correct for this
        using the encoder's own ISR-precise running count, but doing that
        safely needs the encoder's count-to-direction sign convention
        confirmed against real hardware first — deliberately not attempted
        here; see ChuteEncoder.h's own "not bench-calibrated" caveat.
        """
        self.logger.info("Chute: homing via magnet-ring encoder")
        assert self.chute_encoder is not None
        # Raw, unbounded degrees for this log only (not the 360-normalized
        # current_angle) -- during a long homing rotation a wrapped value
        # would make "moved so far" look like it went backwards.
        pos_before = self.stepper.position_degrees / GEAR_RATIO
        self.chute_encoder.clear_index_synced()
        self.stepper.move_at_speed(HOME_SPEED_MICROSTEPS_PER_SEC)
        start = time.monotonic()
        while not self.chute_encoder.is_index_synced():
            if (time.monotonic() - start) * 1000 > HOME_TIMEOUT_MS:
                self.stepper.move_at_speed(0)
                moved = self.stepper.position_degrees / GEAR_RATIO - pos_before
                self.logger.error(
                    f"Chute: encoder homing timed out after {HOME_TIMEOUT_MS}ms without an "
                    f"index-sync event (moved={moved:.2f}° so far)."
                )
                return False
            time.sleep(0.01)
        self.stepper.move_at_speed(0)
        stop_start = time.monotonic()
        while not self.stepper.stopped:
            if (time.monotonic() - stop_start) * 1000 > HOME_TIMEOUT_MS:
                self.logger.error("Chute: encoder homing: stepper did not stop after the index-sync event.")
                return False
            time.sleep(0.01)
        self.stepper.position = 0
        self._encoder_zero_count = self.chute_encoder.get_count()
        if not self._backoffToFirstBin():
            return False
        self.logger.info(
            "Chute: encoder-homed successfully "
            f"(count_at_index={self.chute_encoder.get_count_at_index()}, zero_count={self._encoder_zero_count})"
        )
        self._homed = True
        self.pieces_since_check = 0
        return True

    def _homeEndstop(self) -> bool:
        self.logger.info("Chute: homing via sensor")
        pos_before = self.current_angle
        raw_before = self.raw_endstop_active
        self.stepper.home(
            HOME_SPEED_MICROSTEPS_PER_SEC,
            self.home_pin,
            home_pin_active_high=self.endstop_active_high,
        )
        start = time.monotonic()
        polls = 0
        while not self.stepper.stopped:
            polls += 1
            if (time.monotonic() - start) * 1000 > HOME_TIMEOUT_MS:
                self.logger.error(
                    f"Chute: homing timed out after {HOME_TIMEOUT_MS}ms "
                    f"(polls={polls}, moved={self.current_angle - pos_before:.2f}°, "
                    f"endstop raw={self.raw_endstop_active} triggered={self.endstop_triggered})"
                )
                return False
            time.sleep(0.01)
        elapsed_ms = (time.monotonic() - start) * 1000
        if not self.endstop_triggered:
            self.logger.warning(
                "Chute: homing stopped before the endstop triggered "
                f"(elapsed={elapsed_ms:.0f}ms, polls={polls}, "
                f"moved={self.current_angle - pos_before:.2f}° from {pos_before:.2f}°, "
                f"endstop raw {raw_before}->{self.raw_endstop_active} "
                f"(active_high={self.endstop_active_high}, triggered={self.endstop_triggered})). "
                "polls<=1 / moved~0 means the firmware reported stopped before motion began (race); "
                "large moved means it travelled without the switch ever firing."
            )
            return False
        if not self._backoffToFirstBin():
            return False
        self.logger.info("Chute: homed successfully")
        self._homed = True
        self.pieces_since_check = 0
        return True
