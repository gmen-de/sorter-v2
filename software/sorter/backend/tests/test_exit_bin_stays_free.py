"""A new category is never assigned to the configured exit bin."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from subsystems.distribution import positioning as pos
from subsystems.distribution.positioning import Positioning


def _bin():
    return SimpleNamespace(category_ids=[], not_in_inventory=False)


def _positioning(bins: int = 3) -> Positioning:
    section = SimpleNamespace(enabled=True, bins=[_bin() for _ in range(bins)])
    layer = SimpleNamespace(enabled=True, sections=[section], max_pieces_per_bin=None)
    p = Positioning.__new__(Positioning)
    p.layout = SimpleNamespace(layers=[layer])
    p.chute = SimpleNamespace(isBinReachable=lambda address: True)
    p.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    p._isLayerUsable = lambda index: True
    return p


def _assign(p: Positioning, category: str, exit_bin):
    with (
        patch.object(pos, "_configuredExitBin", return_value=exit_bin),
        patch.object(pos, "_persistBinCategories", lambda layout: None),
        patch("bin_contents.get_current_bin_piece_counts", return_value={}),
    ):
        return p._findOrAssignBinForCategory(category)[0]


def test_exit_bin_is_skipped_when_assigning() -> None:
    p = _positioning(3)
    exit_bin = {"layer_index": 0, "section_index": 0, "bin_index": 0}
    assert (_assign(p, "a", exit_bin).bin_index, _assign(p, "b", exit_bin).bin_index) == (1, 2)
    assert p.layout.layers[0].sections[0].bins[0].category_ids == []
    # Every other bin taken: the next category finds no bin, it does not take the exit.
    assert _assign(p, "c", exit_bin) is None


def test_without_an_exit_bin_the_first_free_bin_is_used() -> None:
    p = _positioning(3)
    assert _assign(p, "a", None).bin_index == 0
