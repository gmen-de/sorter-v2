"""Books what the sorter sorts into StudSphere, the owner's own parts
database (a separate PHP app, its api/sorter.php).

With an API key made in StudSphere, the sorter creates its locations there:
a location named after the machine, with BIN-Storage (one location per bin,
"L1-S1-B1") and ZIP-Storage below it. Then:

- each piece that lands in a bin is added to that bin's location;
- a bin emptied with an inventory receipt moves its parts into a new
  location under ZIP-Storage named after the receipt (ZIP0004);
- a bin emptied without a receipt has its stock in StudSphere set to zero.

Everything goes through an outbox (a table in the local state database)
that a background thread drains, so sorting never waits on the network and
nothing is lost while StudSphere is unreachable. Operations are numbered;
StudSphere remembers the last number it applied for this sorter's stream,
so a batch re-sent after a timeout counts once. Turning the connection on,
changing the address or key, or a "resync" starts a new stream: the outbox
is emptied and every bin's current contents go first, as "set" operations,
so StudSphere starts from what the sorter actually holds.

The enqueue calls run inside bin_contents' write lock, in the same order as
the bin changes themselves, so an add can never overtake the clear of its bin.
StudSphere knows parts by its own ids; it maps the classifier's BrickLink
part and color ids itself and reports the ones it doesn't know, which the
settings page lists. A piece it didn't know is not booked then; a resync books
it once StudSphere does know it.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from typing import Any

import requests

import db
import local_state

log = logging.getLogger(__name__)

BATCH_SIZE = 200
IDLE_POLL_S = 2.0
MAX_BACKOFF_S = 60.0
REQUEST_TIMEOUT_S = 45.0  # StudSphere may ask Rebrickable for part numbers it doesn't know
UNMAPPED_KEPT = 50
HINTED_KEPT = 50
CONDITIONS = ("used", "new")


def binName(layer_index: int, section_index: int, bin_index: int) -> str:
    return f"L{layer_index + 1}-S{section_index + 1}-B{bin_index + 1}"


def _createTables(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS studsphere_outbox ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "op TEXT NOT NULL, "
        "created_at REAL NOT NULL"
        ")"
    )


def _connection():
    return db.connect(_createTables)


def apiUrl(base_url: str, action: str) -> str:
    return f"{base_url.rstrip('/')}/api/sorter.php?action={action}"


def normalizeUrl(url: str) -> str:
    url = url.strip()
    if url and not url.lower().startswith(("http://", "https://")):
        raise ValueError("The StudSphere address must start with http:// or https://.")
    return url.rstrip("/") + "/" if url else ""


# --- Outbox ------------------------------------------------------------------


def _enqueue(ops: list[dict[str, Any]]) -> None:
    now = time.time()
    with _connection() as conn:
        conn.executemany(
            "INSERT INTO studsphere_outbox(op, created_at) VALUES(?, ?)",
            [(json.dumps(op, separators=(",", ":")), now) for op in ops],
        )
        conn.commit()


def _clearOutbox() -> None:
    with _connection() as conn:
        conn.execute("DELETE FROM studsphere_outbox")
        conn.commit()


def _pendingOps(limit: int) -> list[dict[str, Any]]:
    with _connection() as conn:
        rows = conn.execute("SELECT seq, op FROM studsphere_outbox ORDER BY seq LIMIT ?", (limit,)).fetchall()
    return [dict(json.loads(row[1]), seq=int(row[0])) for row in rows]


def _dropThrough(seq: int) -> None:
    with _connection() as conn:
        conn.execute("DELETE FROM studsphere_outbox WHERE seq <= ?", (seq,))
        conn.commit()


def pendingCount() -> int:
    with _connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM studsphere_outbox").fetchone()[0])


# --- Other numbers for a part ------------------------------------------------


def alternates(part: str, color: str | None) -> list[str]:
    """The other numbers Hive's catalog has for a BrickLink part, from the
    cache the classification already filled: the number of the part it takes
    its dimensions from (a Rebrickable-style number, often where BrickLink's and
    Rebrickable's differ) and the LDraw id. StudSphere tries them only when
    its own ways of matching find nothing, and says so when one was used,
    because they're a hint and not a mapping."""
    try:
        from hive_metadata_cache import get_cached_part_metadata

        payload = None
        for color_key in (color, None):  # this color's entry, else any color's
            payload, _price, _at = get_cached_part_metadata(part, color_key)
            if payload:
                break
    except Exception:
        return []
    dimensions = payload.get("dimensions") if isinstance(payload, dict) else None
    if not isinstance(dimensions, dict):
        return []
    found: list[str] = []
    for key in ("physical_parent_part_num", "ldraw_id"):
        value = dimensions.get(key)
        if isinstance(value, str) and value.strip() and value != part and value not in found:
            found.append(value.strip())
    return found


def _withAlt(op: dict[str, Any], part: str, color: str | None) -> dict[str, Any]:
    alt = alternates(part, color)
    if alt:
        op["alt"] = alt
    return op


# --- Hooks from bin_contents (called inside its write lock) -------------------


def onPieceDistributed(piece: dict[str, Any], layer_index: int, section_index: int, bin_index: int) -> None:
    syncer = getStudSphereSyncer()
    if not syncer.active:
        return
    part, color = piece.get("part_id"), piece.get("color_id")
    if not part or color in (None, ""):
        return  # unidentified: nothing StudSphere could book
    try:
        _enqueue(
            [
                _withAlt(
                    {
                        "type": "add",
                        "bin": binName(layer_index, section_index, bin_index),
                        "part": str(part),
                        "color": str(color),
                        "qty": 1,
                    },
                    str(part),
                    str(color),
                )
            ]
        )
    except Exception as exc:
        log.warning("StudSphere: couldn't queue a piece: %s", exc)
    syncer.wake()


def onBinsCleared(bins: list[tuple[int, int, int]], zip_name: str | None = None) -> None:
    """zip_name: the inventory receipt's number, when one printed (only ever
    for a single bin); without it the bins' stock just goes to zero."""
    syncer = getStudSphereSyncer()
    if not syncer.active or not bins:
        return
    ops = [
        {"type": "to_zip", "bin": binName(*b), "zip": zip_name} if zip_name else {"type": "clear", "bin": binName(*b)}
        for b in bins
    ]
    try:
        _enqueue(ops)
    except Exception as exc:
        log.warning("StudSphere: couldn't queue emptying %d bins: %s", len(bins), exc)
    syncer.wake()


# --- Snapshot of the bins, for a new stream ----------------------------------


def _layoutBinNames() -> list[str]:
    from server.routers.bins import _current_bin_categories

    names = []
    for li, layer in enumerate(_current_bin_categories()):
        for si, section in enumerate(layer):
            for bi, _ in enumerate(section):
                names.append(binName(li, si, bi))
    return names


def _snapshotSetOps() -> list[dict[str, Any]]:
    """A "set" for every bin in the layout, empty ones too, so stock left in
    StudSphere from before goes away."""
    from bin_contents import get_current_bin_contents_snapshot

    items_by_bin: dict[str, list[dict[str, Any]]] = {name: [] for name in _layoutBinNames()}
    for entry in get_current_bin_contents_snapshot().get("bins") or []:
        name = binName(int(entry["layer_index"]), int(entry["section_index"]), int(entry["bin_index"]))
        items = items_by_bin.setdefault(name, [])
        for item in entry.get("items") or []:
            if item.get("part_id") and item.get("color_id") not in (None, ""):
                items.append(
                    _withAlt(
                        {"part": str(item["part_id"]), "color": str(item["color_id"]), "qty": int(item.get("count") or 0)},
                        str(item["part_id"]),
                        str(item["color_id"]),
                    )
                )
    return [{"type": "set", "bin": name, "items": items} for name, items in items_by_bin.items()]


# --- The worker --------------------------------------------------------------


class StudSphereError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class StudSphereSyncer:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self.active = False
        self._set_up = False
        self._backoff_s = 0.0
        self._last_error: str | None = None
        self._last_sync_at: float | None = None
        self._location: str | None = None
        self._unmapped: dict[str, dict[str, str]] = {}
        self._hinted: dict[str, str] = {}

    # Lifecycle

    def start(self) -> None:
        config = local_state.get_studsphere_config()
        self.active = bool(config.get("enabled") and config.get("url") and config.get("api_key"))
        if not self.active or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._set_up = False
        self._thread = threading.Thread(target=self._loop, name="studsphere-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.active = False
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=REQUEST_TIMEOUT_S + 5)
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def resync(self) -> None:
        """A new stream: empty the outbox and queue every bin's contents.
        Holds bin_contents' write lock so no piece slips in between."""
        from bin_contents import _bin_state_write_lock

        self.stop()
        with _bin_state_write_lock:
            config = local_state.get_studsphere_config()
            config["stream_id"] = uuid.uuid4().hex
            local_state.set_studsphere_config(config)
            _clearOutbox()
            if config.get("enabled") and config.get("url") and config.get("api_key"):
                _enqueue(_snapshotSetOps())
            with self._lock:
                self._unmapped.clear()
                self._hinted.clear()
                self._last_error = None
            self.start()

    def disable(self) -> None:
        self.stop()
        _clearOutbox()

    # Status

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "set_up": self._set_up,
                "location": self._location,
                "pending": pendingCount(),
                "last_sync_at": self._last_sync_at,
                "last_error": self._last_error,
                "unmapped": list(self._unmapped.values()),
                "hinted": [{"part": part, "as": other} for part, other in self._hinted.items()],
            }

    # Talking to StudSphere

    def _request(self, config: dict[str, Any], method: str, action: str, body: dict | None = None) -> dict[str, Any]:
        try:
            resp = requests.request(
                method,
                apiUrl(config["url"], action),
                json=body,
                headers={"Authorization": f"Bearer {config['api_key']}", "X-Api-Key": config["api_key"]},
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise StudSphereError(f"StudSphere isn't reachable: {exc}") from exc
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise StudSphereError(
                f"StudSphere answered HTTP {resp.status_code} without JSON: is the address right?", resp.status_code
            )
        if resp.status_code >= 400 or not data.get("ok"):
            raise StudSphereError(str(data.get("error") or f"HTTP {resp.status_code}"), resp.status_code)
        return data

    def hello(self, config: dict[str, Any]) -> dict[str, Any]:
        return self._request(config, "GET", "hello")

    def _setup(self, config: dict[str, Any]) -> None:
        from toml_config import getMachineNickname

        if not config.get("stream_id"):
            raise StudSphereError("No stream yet: resync.")
        data = self._request(
            config,
            "POST",
            "setup",
            {
                "machine_id": local_state.get_or_create_machine_id(),
                "sorter_name": getMachineNickname() or socket.gethostname() or "Sorter",
                "stream_id": config["stream_id"],
                "bins": _layoutBinNames(),
            },
        )
        _dropThrough(int(data.get("last_seq") or 0))
        with self._lock:
            self._location = data.get("location")
        self._set_up = True

    def _sendBatch(self, config: dict[str, Any]) -> bool:
        """Sends the oldest queued operations; returns whether any were left."""
        ops = _pendingOps(BATCH_SIZE)
        if not ops:
            return False
        data = self._request(
            config,
            "POST",
            "ops",
            {"stream_id": config["stream_id"], "condition": config.get("condition") or "used", "ops": ops},
        )
        _dropThrough(int(data.get("last_seq") or 0))
        with self._lock:
            self._last_sync_at = time.time()
            for pair in data.get("unmapped") or []:
                key = f"{pair.get('part')}|{pair.get('color')}"
                self._unmapped.pop(key, None)
                self._unmapped[key] = {
                    "part": str(pair.get("part")),
                    "color": str(pair.get("color")),
                    # What StudSphere doesn't know: "part", "color" or "both".
                    "missing": str(pair.get("missing") or "both"),
                }
            while len(self._unmapped) > UNMAPPED_KEPT:
                self._unmapped.pop(next(iter(self._unmapped)))
            for pair in data.get("hinted") or []:
                self._hinted.pop(str(pair.get("part")), None)
                self._hinted[str(pair.get("part"))] = str(pair.get("as"))
            while len(self._hinted) > HINTED_KEPT:
                self._hinted.pop(next(iter(self._hinted)))
        return len(ops) == BATCH_SIZE

    def _loop(self) -> None:
        while not self._stop.is_set():
            more = False
            try:
                config = local_state.get_studsphere_config()
                if not self._set_up:
                    self._setup(config)
                more = self._sendBatch(config)
                self._backoff_s = 0.0
                with self._lock:
                    self._last_error = None
            except StudSphereError as exc:
                if exc.status == 409:
                    self._set_up = False  # stream changed or key moved: set up again
                self._fail(str(exc), long=exc.status in (401, 403, 409))
            except Exception as exc:
                self._fail(f"StudSphere sync failed: {exc}")
            if self._backoff_s:
                self._stop.wait(self._backoff_s)
            elif not more:
                self._wake.wait(IDLE_POLL_S)
                self._wake.clear()

    def _fail(self, message: str, *, long: bool = False) -> None:
        with self._lock:
            if message != self._last_error:
                log.warning("StudSphere: %s", message)
            self._last_error = message
        self._backoff_s = MAX_BACKOFF_S if long else min(MAX_BACKOFF_S, max(2.0, self._backoff_s * 2))


_syncer: StudSphereSyncer | None = None
_syncer_lock = threading.Lock()


def getStudSphereSyncer() -> StudSphereSyncer:
    global _syncer
    with _syncer_lock:
        if _syncer is None:
            _syncer = StudSphereSyncer()
        return _syncer
