import math
import time
import queue
from typing import Optional
from states.base_state import BaseState
from subsystems.shared_variables import SharedVariables
from .states import DistributionState
from irl.config import IRLInterface
from global_config import GlobalConfig
from utils.event import knownObjectToEvent
from defs.known_object import PieceStage


# Physics-derived settle wait, replacing a flat 2000ms for every layer -
# free-fall distance to the layer's own gate grows with depth, so a piece
# landing on layer 1 (topmost, hit first) needs far less settle time than one
# landing on layer 8. Derived from the sorter's 4-8 layer gen4_core stack
# (160mm pitch) - see the project memory `chute-fallzeiten-wartezeit` for the
# full derivation (LEGO Tile 1x1 quarter-circle and Brick 2x10, air
# resistance negligible, pure free fall + a fixed gate/funnel slide time).
GRAVITY_M_S2 = 9.81
FALL_DISTANCE_BASE_M = 0.115  # fall distance to layer 1's gate midpoint
FALL_DISTANCE_PER_LAYER_M = 0.16  # additional fall distance per layer deeper (= layer pitch)
CHUTE_SLIDE_S = 0.35  # gate + funnel slide time, layer-independent (measured)
SETTLE_RESERVE_S = 0.1
# Used only if a piece's target layer is somehow unknown - the layer-1 value
# this formula was tuned against would be too short to be safe as a
# catch-all, so this keeps the old flat constant as a conservative fallback.
FALLBACK_SETTLE_MS = 2000
# An operator's per-layer adjustment (storage-layers settings) must never
# bring the total settle wait below this - otherwise the chute could start
# repositioning before a piece has actually fallen clear.
MIN_SETTLE_MS = 100


def chuteSettleMsForLayer(layer_index: Optional[int]) -> float:
    """Milliseconds to wait after a drop before the piece has settled
    (fallen + slid clear) on the given layer (0 = topmost, hit first)."""
    if layer_index is None or layer_index < 0:
        return FALLBACK_SETTLE_MS
    n = layer_index + 1
    fall_distance_m = FALL_DISTANCE_BASE_M + FALL_DISTANCE_PER_LAYER_M * (n - 1)
    fall_time_s = math.sqrt(2 * fall_distance_m / GRAVITY_M_S2)
    return (fall_time_s + CHUTE_SLIDE_S + SETTLE_RESERVE_S) * 1000.0


def chuteSettleMsForPiece(layer_index: Optional[int], adjust_ms: int = 0) -> float:
    """``chuteSettleMsForLayer`` plus the operator's per-layer adjustment
    (storage-layers settings), floored at ``MIN_SETTLE_MS``. What ``Sending``
    actually waits; the plain per-layer function above is also used on its
    own where only the physics-computed base value is wanted (e.g. the
    storage-layers settings page, to show "computed" vs. "adjusted")."""
    base_ms = chuteSettleMsForLayer(layer_index)
    if layer_index is None or layer_index < 0:
        return base_ms  # no per-layer adjustment to apply without a known layer
    return max(MIN_SETTLE_MS, base_ms + adjust_ms)


MISSING_DROP_PIECE_GRACE_MS = 1500


class Sending(BaseState):
    def __init__(
        self,
        irl: IRLInterface,
        gc: GlobalConfig,
        shared: SharedVariables,
        event_queue: queue.Queue,
        *,
        post_distribute_cooldown_s: float = 0.0,
    ):
        super().__init__(irl, gc)
        self.shared = shared
        self.event_queue = event_queue
        self._cooldown_s = max(0.0, float(post_distribute_cooldown_s))
        self.piece = None
        self.start_time: float = 0.0
        self._occupancy_state: str | None = None
        self._committed: bool = False
        self._settle_ms: float = FALLBACK_SETTLE_MS

    def _setOccupancyState(self, state_name: str) -> None:
        if self._occupancy_state == state_name:
            return
        prev_state = self._occupancy_state
        self._occupancy_state = state_name
        self.gc.runtime_stats.observeStateTransition(
            "distribution.occupancy",
            prev_state,
            state_name,
        )

    def step(self) -> Optional[DistributionState]:
        now = time.time()
        if self.piece is None and not self._committed:
            if self.start_time <= 0.0:
                self.start_time = now
            transport = self.shared.transport
            self.piece = (
                transport.getPieceForDistributionDrop()
                if transport is not None
                else None
            )
            if self.piece is None:
                elapsed_ms = (now - self.start_time) * 1000
                self._setOccupancyState("sending.wait_drop_piece")
                if elapsed_ms >= MISSING_DROP_PIECE_GRACE_MS:
                    self.logger.warning(
                        "Sending: no distribution-drop piece available after "
                        f"{elapsed_ms:.0f}ms; reopening distribution gate"
                    )
                    self.gc.runtime_stats.observeBlockedReason(
                        "distribution",
                        "sending_missing_drop_piece",
                    )
                    self.shared.set_distribution_gate(True, reason=None)
                    return DistributionState.IDLE
                return None
            self._settle_ms = chuteSettleMsForPiece(
                getattr(self.piece, "intended_layer_index", None),
                self._settleAdjustMsForLayer(getattr(self.piece, "intended_layer_index", None)),
            )

        elapsed_ms = (now - self.start_time) * 1000
        settle_ms = self._settle_ms
        self._setOccupancyState("sending.wait_chute_settle")
        if elapsed_ms < settle_ms:
            return None

        # Commit the piece once (stats, event, recorder) — must not repeat
        # even if we decide to hold the gate for additional cooldown below.
        if not self._committed:
            self.logger.info(f"Sending: settle complete ({elapsed_ms:.0f}ms)")
            self._setOccupancyState("sending.commit_piece")
            if self.piece and self._alreadyCommitted(self.piece):
                # The drop slot still holds the piece committed on an earlier
                # cycle: READY released without a new drop (the gate fallback).
                # Recording it again would double-count it in the run history
                # and set progress.
                self.logger.warning(
                    f"Sending: piece {self.piece.uuid[:8]} was already committed; not recording it again"
                )
            elif self.piece:
                self.piece.stage = PieceStage.distributed
                self.piece.distributed_at = time.time()
                self.piece.updated_at = time.time()
                self.event_queue.put(knownObjectToEvent(self.piece))
                self.gc.run_recorder.recordPiece(self.piece)
                tracker = getattr(self.gc, 'set_progress_tracker', None)
                if tracker is not None:
                    tracker.record(
                        self.piece.part_id,
                        self.piece.color_id,
                        self.piece.category_id,
                    )
                    try:
                        from server.set_progress_sync import getSetProgressSyncWorker

                        getSetProgressSyncWorker().notify()
                    except Exception:
                        pass
            self._committed = True

        if not self._shouldReopenGate():
            self._setOccupancyState("sending.wait_piece_exit")
            return None

        self.shared.set_distribution_gate(True, reason=None)
        return DistributionState.IDLE

    def _settleAdjustMsForLayer(self, layer_index: Optional[int]) -> int:
        if layer_index is None or layer_index < 0:
            return 0
        try:
            layers = self.irl.distribution_layout.layers
            if 0 <= layer_index < len(layers):
                adjust = getattr(layers[layer_index], "settle_adjust_ms", 0)
                return int(adjust) if isinstance(adjust, int) and not isinstance(adjust, bool) else 0
        except Exception:
            pass
        return 0

    @staticmethod
    def _alreadyCommitted(piece) -> bool:
        return piece.stage == PieceStage.distributed or piece.distributed_at is not None

    def _shouldReopenGate(self) -> bool:
        elapsed_since_drop = time.time() - self.start_time
        required_s = (self._settle_ms / 1000.0) + self._cooldown_s
        if elapsed_since_drop < required_s:
            return False
        return True

    def cleanup(self) -> None:
        super().cleanup()
        self.piece = None
        self.start_time = 0.0
        self._committed = False
        self._settle_ms = FALLBACK_SETTLE_MS
