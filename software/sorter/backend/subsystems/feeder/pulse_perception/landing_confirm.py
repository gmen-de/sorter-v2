"""A fall counts once the next channel has the piece.

A channel takes the piece it is pushing off for fallen when its camera stops
seeing it (dispense_gate.py). At the exit a piece that is still there can look
the same: its box is merged with the box of the piece behind it, the detector
misses it on the lip, or the tracker gives it a new id. The exit then saw one
piece with nothing close behind it and turned it off in one long move, with
the real lead still on the lip: both fell, a double feed.

The next channel shows what happened: a piece that fell lands there. So a fall
the next channel has not taken stays open, and while one is open the exit only
makes its small pulse and carries nothing to the lip fast. The lead that was
not seen then falls on its own, lands, and closes it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LandingConfirm:
    # When the exit took its lead for fallen, while the next channel has not
    # taken a piece since; None when no fall is open.
    open_since: float | None = None

    def fell(self, now: float, next_has_piece: bool) -> None:
        """The exit took its lead for fallen. A piece already on the next
        channel is that piece: it often lands before the fall is confirmed."""
        self.open_since = None if next_has_piece else now

    def observe(self, next_has_piece: bool) -> None:
        if next_has_piece:
            self.open_since = None

    @property
    def open(self) -> bool:
        return self.open_since is not None
