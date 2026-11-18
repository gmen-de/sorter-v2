"""Background worker that uploads recognized pieces to the configured
Nextcloud, for later training a custom model from them.

Mirrors hive_sync.py's shape (poll, a per-minute upload budget, backoff on
failure) but is far smaller: one leg (piece_images), one target, one thread.
The one real asymmetry versus a Hive target: Hive tells this machine the
highest id/seq it already has, so hive_sync.py never needs to remember a
watermark itself (see sync_seq.py and hive_sync.py's module doc); Nextcloud
is dumb file storage with no such API, so this worker keeps its own
watermark in local_state (last_synced_seq), advanced one image at a time so
a crash mid-run only risks re-sending one file (a WebDAV PUT is idempotent),
never skips one.

Images go in one of two places under remote_root/dataset/:
  - identified pieces: <part_id>_<part name>/<color_id>_<color name>/
  - everything else (no part match, or below min_confidence): _unidentified/
    -- often the most valuable future training data, since it is exactly
    where today's recognition struggles.
Each image gets a same-named .json sidecar with the rest of its piece's
metadata, so the pair is self-describing without a separate manifest file
that concurrent uploads would have to append to safely.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

import db
import local_state
import piece_image_store

from server.hive_sync import _UploadBudget

log = logging.getLogger(__name__)

FILES_PER_PASS = 20
IDLE_POLL_S = 10.0
BACKOFF_START_S = 10.0
BACKOFF_MAX_S = 120.0
# Conservative default: most Nextcloud instances this machine talks to sit
# behind a home upload link, not a datacenter one.
BYTES_PER_MINUTE = 8 * 1024 * 1024
FILES_PER_MINUTE = 60

DEFAULT_REMOTE_ROOT = "/SorterTrainingData"

_SEGMENT_RE = re.compile(r"[^A-Za-z0-9 _.-]+")


class _AuthError(Exception):
    """Nextcloud rejected the stored app password (401)."""


def _slug(value: str | None, fallback: str) -> str:
    text = (value or "").strip() or fallback
    return _SEGMENT_RE.sub("_", text)[:80] or fallback


def _quote(segment: str) -> str:
    return urllib.parse.quote(segment, safe="")


def _fetchPieceRecords(uuids: list[str]) -> dict[str, dict[str, Any]]:
    if not uuids:
        return {}
    import piece_records

    placeholders = ",".join("?" for _ in uuids)
    with db.connect(piece_records._createTables) as conn:
        rows = conn.execute(
            "SELECT uuid, part_id, part_name, color_id, color_name, category_id, confidence, "
            "color_confidence, color_provider, mold_provider, training_color_id, "
            "training_label_source, dead FROM piece_records "
            f"WHERE uuid IN ({placeholders})",
            uuids,
        ).fetchall()
    return {r["uuid"]: dict(r) for r in rows}


class NextcloudSyncer:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._budget = _UploadBudget()
        self._budget.setLimits(BYTES_PER_MINUTE, FILES_PER_MINUTE)
        self._dirs_made: set[str] = set()
        self._backoff_s = 0.0
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._last_upload_at: float | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        config = local_state.get_nextcloud_config()
        if not config or not config.get("enabled") or not config.get("app_password"):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nextcloud-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    def reload(self) -> None:
        """Call after the Nextcloud config changes (reconnected, enabled,
        settings edited...) so a stopped/misconfigured worker can pick it
        back up, or a disabled one stops."""
        self.stop()
        self._dirs_made.clear()
        self.start()

    def status(self) -> dict[str, Any]:
        config = local_state.get_nextcloud_config() or {}
        watermark = int(config.get("last_synced_seq") or 0)
        with self._lock:
            last_error, last_upload_at = self._last_error, self._last_upload_at
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "pending": piece_image_store.countImagesAfter(watermark),
            "last_synced_seq": watermark,
            "last_upload_at": last_upload_at,
            "last_error": last_error,
            "needs_reconnect": bool(config.get("needs_reconnect")),
        }

    # --- the loop -----------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                moved = self._drainOnce()
            except Exception as exc:
                self._noteError(str(exc))
                moved = False
            if self._stop.is_set():
                return
            wait_s = 0.0 if moved else (self._backoff_s or IDLE_POLL_S)
            self._wake.wait(timeout=wait_s)
            self._wake.clear()

    def _drainOnce(self) -> bool:
        config = local_state.get_nextcloud_config()
        if not config or not config.get("enabled") or not config.get("app_password") or config.get("needs_reconnect"):
            return False
        watermark = int(config.get("last_synced_seq") or 0)
        rows = piece_image_store.listImagesAfter(watermark, FILES_PER_PASS)
        if not rows:
            return False
        records = _fetchPieceRecords(sorted({r["piece_uuid"] for r in rows}))
        moved_any = False
        for row in rows:
            if self._stop.is_set():
                break
            try:
                self._uploadOne(config, row, records.get(row["piece_uuid"]))
            except _AuthError:
                local_state.set_nextcloud_needs_reconnect(True)
                self._noteError("Nextcloud lehnt die gespeicherten Zugangsdaten ab (401) - bitte neu verbinden.")
                return moved_any
            local_state.set_nextcloud_watermark(row["sync_seq"])
            moved_any = True
            self._backoff_s = 0.0
        return moved_any

    def _uploadOne(self, config: dict[str, Any], row: dict[str, Any], record: dict[str, Any] | None) -> None:
        if row["evicted_locally"] or not row["used"] or row["excluded_from_result"]:
            return
        if record is not None and record.get("dead"):
            return
        path = piece_image_store.getImageFileById(row["id"])
        if path is None:
            return
        remote_dir = self._remoteFolder(config, record)
        if remote_dir is None:
            return

        nbytes = path.stat().st_size
        self._waitForBudget(nbytes)
        server_url = config["server_url"].rstrip("/")
        auth = (config["login_name"], config["app_password"])
        base_name = f"{row['piece_uuid']}_{row['seq']:02d}"

        self._ensureDir(server_url, auth, remote_dir)
        self._put(server_url, auth, remote_dir, f"{base_name}.jpg", path.read_bytes(), "image/jpeg")
        metadata = self._metadataFor(row, record)
        self._put(
            server_url, auth, remote_dir, f"{base_name}.json",
            json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8"), "application/json",
        )
        with self._lock:
            self._last_error = None
            self._last_upload_at = time.time()

    def _remoteFolder(self, config: dict[str, Any], record: dict[str, Any] | None) -> list[str] | None:
        root = [s for s in (config.get("remote_root") or DEFAULT_REMOTE_ROOT).split("/") if s]
        part_id = record.get("part_id") if record else None
        min_confidence = config.get("min_confidence")
        confidence = record.get("confidence") if record else None
        meets_threshold = min_confidence is None or (confidence is not None and confidence >= min_confidence)
        if part_id and meets_threshold:
            color_id = (record.get("color_id") if record else None) or "unknown"
            part_folder = f"{part_id}_{_slug(record.get('part_name'), part_id)}"
            color_folder = f"{color_id}_{_slug(record.get('color_name'), color_id)}"
            return root + ["dataset", part_folder, color_folder]
        if config.get("upload_unidentified", True):
            return root + ["dataset", "_unidentified"]
        return None

    @staticmethod
    def _metadataFor(row: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
        record = record or {}
        return {
            "piece_uuid": row["piece_uuid"],
            "seq": row["seq"],
            "source": row.get("source"),
            "channel": row.get("channel"),
            "captured_at": row.get("ts") or row.get("created_at"),
            "part_id": record.get("part_id"),
            "part_name": record.get("part_name"),
            "color_id": record.get("color_id"),
            "color_name": record.get("color_name"),
            "category_id": record.get("category_id"),
            "confidence": record.get("confidence"),
            "color_confidence": record.get("color_confidence"),
            "color_provider": record.get("color_provider"),
            "mold_provider": record.get("mold_provider"),
            "training_color_id": record.get("training_color_id"),
            "training_label_source": record.get("training_label_source"),
        }

    # --- budget / backoff / HTTP --------------------------------------

    def _waitForBudget(self, nbytes: int) -> None:
        while not self._stop.is_set():
            if self._budget.take(nbytes):
                return
            self._wake.wait(timeout=min(5.0, self._budget.waitS(nbytes)))
            self._wake.clear()

    def _noteError(self, message: str) -> None:
        with self._lock:
            self._last_error = message
        self._backoff_s = min(max(self._backoff_s, BACKOFF_START_S) * 1.5, BACKOFF_MAX_S)
        log.warning("nextcloud_sync: %s", message)

    def _ensureDir(self, server_url: str, auth: tuple[str, str], segments: list[str]) -> None:
        made_so_far: list[str] = []
        for segment in segments:
            made_so_far.append(segment)
            key = "/".join(made_so_far)
            if key in self._dirs_made:
                continue
            url = self._filesUrl(server_url, auth[0], made_so_far)
            resp = requests.request("MKCOL", url, auth=auth, timeout=15)
            if resp.status_code == 401:
                raise _AuthError()
            if resp.status_code not in (201, 405):  # created, or already exists
                resp.raise_for_status()
            self._dirs_made.add(key)

    def _put(
        self, server_url: str, auth: tuple[str, str], segments: list[str], filename: str,
        data: bytes, content_type: str,
    ) -> None:
        url = self._filesUrl(server_url, auth[0], segments) + "/" + _quote(filename)
        resp = requests.put(url, data=data, auth=auth, headers={"Content-Type": content_type}, timeout=60)
        if resp.status_code == 401:
            raise _AuthError()
        resp.raise_for_status()

    @staticmethod
    def _filesUrl(server_url: str, login_name: str, segments: list[str]) -> str:
        path = "/".join(_quote(s) for s in [login_name, *segments])
        return f"{server_url}/remote.php/dav/files/{path}"


_syncer: NextcloudSyncer | None = None
_syncer_lock = threading.Lock()


def getNextcloudSyncer() -> NextcloudSyncer:
    global _syncer
    with _syncer_lock:
        if _syncer is None:
            _syncer = NextcloudSyncer()
        return _syncer
