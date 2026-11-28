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
        spread = watch._landingSpread([h.landed for h in hands], 4)
    finally:
        store.loadZones = store_load
    assert spread["n"] == 5
    assert spread["from_drop_middle_deg"]["min"] < -5
    assert spread["from_drop_middle_deg"]["max"] > 25
    assert spread["outside_drop_zone"] == 1
    assert spread["piece_length_deg"]["p50"] > 0


def test_the_watch_report_gives_the_landing_spread_for_c2_too() -> None:
    import math
    import zones.store as store

    zones = ChannelZones(
        channel=2,
        frame=(1000, 1000),
        ring=Ring((500.0, 500.0), 100.0, 450.0, 400.0, None),
        drop=Span(160.0, 220.0),
        exit=Span(60.0, 100.0),
    )
    w = watch.PieceWatch(perception_service=None)
    for angle in (170.0, 190.0, 210.0):
        x = 500.0 + 300.0 * math.cos(math.radians(angle))
        y = 500.0 + 300.0 * math.sin(math.radians(angle))
        w._first_seen_c2.append(watch.Sighting(ts=1.0, u=x / 1000.0, v=y / 1000.0, size=0.05))
    hand = watch.Sighting(ts=1.0, u=0.5, v=0.2, size=0.05)
    w._handoffs[2].append(watch.Handoff(2, hand, hand, 1.5))
    zones3 = ChannelZones(channel=3, frame=(1000, 1000), ring=zones.ring, drop=Span(160.0, 220.0), exit=Span(60.0, 100.0))
    original = store.loadZones
    store.loadZones = lambda: {2: zones, 3: zones3}
    try:
        report = w.report()
    finally:
        store.loadZones = original
    spread = report["channels"]["2"]["landing_from_c1"]
    assert spread["n"] == 3
    assert spread["outside_drop_zone"] == 0
    assert abs(spread["from_drop_middle_deg"]["p50"]) < 1.0
    assert report["channels"]["2"]["landing_on_next_channel"]["n"] == 1


def test_staging_stops_the_platter_once_the_drop_zone_has_been_clear_a_moment() -> None:
    ch = _channel(_Phase.STAGING)
    stops = []
    ch.stopStepper = lambda: stops.append(1)  # type: ignore[method-assign]
    ch._stopWhenDropClear(False, 1.0)
    ch._stopWhenDropClear(True, 1.1)
    assert not stops
    ch._stopWhenDropClear(True, 1.1 + two_piece._STAGE_EARLY_STOP_CLEAR_S - 0.01)
    assert not stops
    ch._stopWhenDropClear(True, 1.1 + two_piece._STAGE_EARLY_STOP_CLEAR_S + 0.01)
    assert len(stops) == 1


def test_a_piece_in_the_drop_zone_again_restarts_the_early_stop_wait() -> None:
    ch = _channel(_Phase.STAGING)
    stops = []
    ch.stopStepper = lambda: stops.append(1)  # type: ignore[method-assign]
    ch._stopWhenDropClear(True, 1.0)
    ch._stopWhenDropClear(False, 1.05)
    ch._stopWhenDropClear(True, 1.1)
    ch._stopWhenDropClear(True, 1.15)
    assert not stops


def test_ready_is_noted_once_for_the_pieces_that_left_the_drop_zone() -> None:
    ch, tp = _channel(), _piece(zone=two_piece._ZONE_PRECISE)
    ch._pieces[1] = tp
    ch._traceReady()
    assert "ready_at" not in tp.trace
    ch._traceZones()
    ch._traceReady()
    first = tp.trace["ready_at"]
    ch._traceReady()
    assert tp.trace["ready_at"] == first
