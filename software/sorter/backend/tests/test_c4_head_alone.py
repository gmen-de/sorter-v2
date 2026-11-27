"""The head is ejected alone after [c4] head_alone_after_s without a next piece,
and at once (after a short settle) when a piece straddling the drop zone's edge
keeps the feeder shut while no drop piece exists to stage."""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

from defs.known_object import PieceStage
from subsystems.classification_channel.two_piece import flow as two_piece
from subsystems.classification_channel.two_piece.flow import (
    TwoPieceClassificationChannel,
    _TrackedPiece,
)


class _Worker:
    def __init__(self) -> None:
        self.ctx = SimpleNamespace(known_object=SimpleNamespace(stage=PieceStage.distributing))


def _channel(monkeypatch, cfg: dict, in_drop: bool, waited: float):
    monkeypatch.setattr(two_piece, "_c4Config", lambda: cfg)
    ch = TwoPieceClassificationChannel.__new__(TwoPieceClassificationChannel)
    ch.logger = logging.getLogger("test_c4_head_alone")
    ch.shared = SimpleNamespace(distribution_ready=True)
    ch._pieces = {}
    head = _TrackedPiece(1, _Worker(), time.monotonic())
    head.zone, head.gap_to_exit, head.placed = two_piece._ZONE_PRECISE, 40.0, True
    ch._pieces[1] = head
    ch._ready_since = None
    ch._phase_started_at = time.monotonic() - waited
    ch._last_in_drop = in_drop
    entered = []
    ch._enterPhase = lambda phase: entered.append(phase)
    return ch, entered


def test_straddler_ejects_after_the_short_settle(monkeypatch) -> None:
    ch, entered = _channel(monkeypatch, {"eject_past_straddler": True}, in_drop=True, waited=1.5)
    ch._maybeEjectHeadAlone()
    assert entered == [two_piece._Phase.EJECTING]


def test_straddler_rule_is_off_by_default(monkeypatch) -> None:
    ch, entered = _channel(monkeypatch, {}, in_drop=True, waited=1.5)
    ch._maybeEjectHeadAlone()
    assert entered == []


def test_straddler_waits_for_the_settle(monkeypatch) -> None:
    ch, entered = _channel(monkeypatch, {"eject_past_straddler": True}, in_drop=True, waited=0.5)
    ch._maybeEjectHeadAlone()
    assert entered == []


def test_straddler_rule_can_be_switched_off(monkeypatch) -> None:
    ch, entered = _channel(monkeypatch, {"eject_past_straddler": False}, in_drop=True, waited=1.5)
    ch._maybeEjectHeadAlone()
    assert entered == []


def test_head_alone_wait_is_configurable(monkeypatch) -> None:
    ch, entered = _channel(monkeypatch, {"head_alone_after_s": 3.0}, in_drop=False, waited=3.5)
    ch._maybeEjectHeadAlone()
    assert entered == [two_piece._Phase.EJECTING]
    ch, entered = _channel(monkeypatch, {"head_alone_after_s": 3.0}, in_drop=False, waited=2.0)
    ch._maybeEjectHeadAlone()
    assert entered == []


def test_default_wait_is_unchanged(monkeypatch) -> None:
    ch, entered = _channel(monkeypatch, {}, in_drop=False, waited=5.0)
    ch._maybeEjectHeadAlone()
    assert entered == []
