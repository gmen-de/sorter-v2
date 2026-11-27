import logging
import time
from types import SimpleNamespace

from subsystems.classification_channel.two_piece import aim_trace
from subsystems.classification_channel.two_piece import flow as two_piece
from subsystems.classification_channel.two_piece.flow import TwoPieceClassificationChannel, _Phase, _TrackedPiece
from zones import watch
from zones.model import ChannelZones, Ring, Span


class _Worker:
    def __init__(self, obj=None) -> None:
        self.ctx = SimpleNamespace(known_object=obj)


def _channel(phase=_Phase.WAITING) -> TwoPieceClassificationChannel:
    ch = TwoPieceClassificationChannel.__new__(TwoPieceClassificationChannel)
    ch.logger = logging.getLogger("test_c4_aim_trace")
    ch._phase = phase
    ch._pieces = {}
    return ch


def _piece(gap=40.0, zone=two_piece._ZONE_PRECISE, obj=None) -> _TrackedPiece:
    tp = _TrackedPiece(1, _Worker(obj), time.monotonic())
    tp.gap_to_exit, tp.zone = gap, zone
    return tp


def test_a_moment_is_noted_once_with_where_the_piece_stood() -> None:
    ch, tp = _channel(), _piece(gap=41.26)
    ch._traceEvent(tp, "classified")
    first = tp.trace["classified_at"]
    tp.gap_to_exit = 10.0
    ch._traceEvent(tp, "classified")
    assert tp.trace["classified_at"] == first
    assert tp.trace["classified_gap_deg"] == 41.3
    assert tp.trace["classified_zone"] == "precise"
    assert tp.trace["classified_platter_turning"] is False


def test_the_platter_turning_is_noted() -> None:
    ch, tp = _channel(_Phase.EJECTING), _piece()
    ch._traceEvent(tp, "aim_requested")
    assert tp.trace["aim_requested_platter_turning"] is True


def test_aim_ready_is_noted_for_the_placed_piece_once_the_chute_is_aimed() -> None:
    obj = SimpleNamespace(stage=two_piece.PieceStage.distributing)
    ch, tp = _channel(), _piece(obj=obj)
    tp.placed = True
    ch._pieces[1] = tp
    ch.shared = SimpleNamespace(distribution_ready=False)
    ch._traceAimReady()
    assert "aim_ready_at" not in tp.trace
    ch.shared.distribution_ready = True
    ch._traceAimReady()
    assert "aim_ready_at" in tp.trace


def test_a_finished_trace_has_its_stretches_in_ms() -> None:
    aim_trace.clear()
    t = 1000.0
    aim_trace.finish(
        {
            "classified_at": t,
            "aim_requested_at": t + 0.2,
            "chute_move_started_at": t + 0.25,
            "positioned_at": t + 1.0,
            "aim_ready_at": t + 1.05,
            "ejected_at": t + 1.6,
        }
    )
    trace = aim_trace.recent(1)[0]
    assert trace["classified_to_aim_requested_ms"] == 200
    assert trace["chute_moving_to_positioned_ms"] == 750
    assert trace["aim_ready_to_ejected_ms"] == 550
    assert aim_trace.summary([trace])["aim_requested_to_aim_ready_ms"]["p50"] == 850


def test_landing_spread_is_read_relative_to_the_drop_zone_across_zero() -> None:
    zones = ChannelZones(
        channel=3,
        frame=(1000, 1000),
        ring=Ring((500.0, 500.0), 100.0, 450.0, 400.0, None),
        drop=Span(350.0, 10.0),  # 20 degrees wide, middle at 0
        exit=Span(150.0, 200.0),
    )
    import math

    def sighting(angle_deg: float):
        x = 500.0 + 300.0 * math.cos(math.radians(angle_deg))
        y = 500.0 + 300.0 * math.sin(math.radians(angle_deg))
        return watch.Sighting(ts=1.0, u=x / 1000.0, v=y / 1000.0, size=0.05)

    hands = [watch.Handoff(3, sighting(0), sighting(a), 1.0) for a in (352.0, 358.0, 2.0, 8.0, 30.0)]
    import zones.store as store

    store_load = store.loadZones
    store.loadZones = lambda: {4: zones}
    try:
        spread = watch._landingSpread(hands, 4)
    finally:
        store.loadZones = store_load
    assert spread["n"] == 5
    assert spread["from_drop_middle_deg"]["min"] < -5
    assert spread["from_drop_middle_deg"]["max"] > 25
    assert spread["outside_drop_zone"] == 1
    assert spread["piece_length_deg"]["p50"] > 0
