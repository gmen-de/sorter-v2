"""While the chute is not ready for a placed head, the channel already carries
that head to a stand-off short of the fall-off, keeping the drop-zone piece in
the drop zone; only the last bit of the eject then waits for the chute."""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

from defs.known_object import PieceStage
from subsystems.classification_channel.two_piece import flow as two_piece
from subsystems.classification_channel.two_piece.constants import C4_TRAVEL_SIGN
from subsystems.classification_channel.two_piece.flow import (
    TwoPieceClassificationChannel,
    _TrackedPiece,
)


class _Worker:
    def __init__(self) -> None:
        self.ctx = SimpleNamespace(known_object=None)


def _channel(monkeypatch, standoff: float = 10.0, precise: float = 72.0):
    monkeypatch.setattr(two_piece, "_overlapStandoffDeg", lambda: standoff)
    ch = TwoPieceClassificationChannel.__new__(TwoPieceClassificationChannel)
    ch.logger = logging.getLogger("test_c4_overlap_chute")
    ch.ctx = SimpleNamespace(config=SimpleNamespace(discharge_speed_usteps_per_s=6000, discharge_max_move_output_deg=270.0))
    ch._pieces = {}
    ch._preciseSpanDeg = lambda: precise
    moves: list[float] = []
    ch.startOutputMove = lambda deg, speed: moves.append(round(deg, 2)) or True
    return ch, moves


def _piece(ch, track_id: int, *, zone: int, gap: float, placed: bool = False) -> _TrackedPiece:
    tp = _TrackedPiece(track_id, _Worker(), time.monotonic())
    tp.zone, tp.gap_to_exit = zone, gap
    if placed:
        tp.placed = True
        tp.worker.ctx.known_object = SimpleNamespace(stage=PieceStage.distributing)
    ch._pieces[track_id] = tp
    return tp


def _head_and_drop(ch, head_gap=72.0, drop_gap=167.0):
    head = _piece(ch, 1, zone=two_piece._ZONE_PRECISE, gap=head_gap, placed=True)
    drop = _piece(ch, 2, zone=two_piece._ZONE_DROP, gap=drop_gap)
    return head, drop


def test_a_placed_head_is_carried_to_the_standoff(monkeypatch) -> None:
    ch, moves = _channel(monkeypatch)
    head, drop = _head_and_drop(ch)
    ch._maybePreAdvance(head, drop)
    assert moves == [round(C4_TRAVEL_SIGN * 62.0, 2)]


def test_only_once_per_head(monkeypatch) -> None:
    ch, moves = _channel(monkeypatch)
    head, drop = _head_and_drop(ch)
    ch._maybePreAdvance(head, drop)
    ch._maybePreAdvance(head, drop)
    assert len(moves) == 1


def test_the_drop_zone_piece_stays_in_the_drop_zone(monkeypatch) -> None:
    ch, moves = _channel(monkeypatch)
    head, drop = _head_and_drop(ch, drop_gap=100.0)
    ch._maybePreAdvance(head, drop)
    assert moves == [round(C4_TRAVEL_SIGN * (100.0 - 72.0 - two_piece._PRE_ADVANCE_DROP_MARGIN_DEG), 2)]


def test_off_when_the_setting_is_off(monkeypatch) -> None:
    ch, moves = _channel(monkeypatch, standoff=0.0)
    head, drop = _head_and_drop(ch)
    ch._maybePreAdvance(head, drop)
    assert moves == []


def test_not_for_a_head_the_chute_is_not_aiming_for(monkeypatch) -> None:
    ch, moves = _channel(monkeypatch)
    head = _piece(ch, 1, zone=two_piece._ZONE_PRECISE, gap=72.0)
    drop = _piece(ch, 2, zone=two_piece._ZONE_DROP, gap=167.0)
    ch._maybePreAdvance(head, drop)
    assert moves == []


def test_nothing_when_already_at_the_standoff(monkeypatch) -> None:
    ch, moves = _channel(monkeypatch)
    head, drop = _head_and_drop(ch, head_gap=11.0)
    ch._maybePreAdvance(head, drop)
    assert moves == []
