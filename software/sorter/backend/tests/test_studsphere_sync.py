"""studsphere_sync against a fake StudSphere that keeps the same books the
real api/sorter.php does: a stream id, the last applied seq, and per-bin and
per-bag stock."""

import json
from collections import Counter

import pytest

import local_state
import studsphere_sync as ss
from bin_contents import clear_current_session_bins, record_piece_distribution, start_new_sorting_session

KNOWN = {("3070", "17"), ("3070", "1"), ("3069", "1")}


class FakeStudSphere:
    def __init__(self):
        self.stream_id = None
        self.last_seq = 0
        self.bins: dict[str, Counter] = {}
        self.zips: dict[str, Counter] = {}
        self.machine_id = None
        self.fail_next = 0
        self.requests = 0

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        self.requests += 1
        assert headers["Authorization"] == "Bearer ssk_test"
        if self.fail_next:
            self.fail_next -= 1
            raise ss.requests.ConnectionError("down")
        action = url.split("action=")[1]
        if action == "setup":
            if self.machine_id not in (None, json["machine_id"]):
                return Resp(409, {"ok": False, "error": "This API key belongs to another sorter."})
            self.machine_id = json["machine_id"]
            if json["stream_id"] != self.stream_id:
                self.stream_id, self.last_seq = json["stream_id"], 0
            for name in json["bins"]:
                self.bins.setdefault(name, Counter())
            return Resp(200, {"ok": True, "location": "Test sorter", "last_seq": self.last_seq})
        if action == "ops":
            if json["stream_id"] != self.stream_id:
                return Resp(409, {"ok": False, "error": "Stream changed: set up again."})
            unmapped = []
            for op in json["ops"]:
                if op["seq"] <= self.last_seq:
                    continue
                bin_stock = self.bins.setdefault(op["bin"], Counter())
                if op["type"] == "add":
                    if (op["part"], op["color"]) in KNOWN:
                        bin_stock[(op["part"], op["color"])] += op["qty"]
                    else:
                        unmapped.append({"part": op["part"], "color": op["color"]})
                elif op["type"] == "set":
                    bin_stock.clear()
                    for item in op["items"]:
                        if (item["part"], item["color"]) in KNOWN:
                            bin_stock[(item["part"], item["color"])] += item["qty"]
                elif op["type"] == "clear":
                    bin_stock.clear()
                elif op["type"] == "to_zip":
                    self.zips.setdefault(op["zip"], Counter()).update(bin_stock)
                    bin_stock.clear()
                self.last_seq = op["seq"]
            return Resp(200, {"ok": True, "last_seq": self.last_seq, "unmapped": unmapped})
        return Resp(404, {"ok": False, "error": "Unknown action."})


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "local_state.sqlite"))
    fake = FakeStudSphere()
    monkeypatch.setattr(ss.requests, "request", fake)
    monkeypatch.setattr(ss, "_layoutBinNames", lambda: ["L1-S1-B1", "L1-S1-B2"])
    syncer = ss.StudSphereSyncer()
    # No background thread in tests: drive the worker's steps by hand.
    monkeypatch.setattr(
        syncer,
        "start",
        lambda: setattr(
            syncer,
            "active",
            bool(local_state.get_studsphere_config().get("enabled")),
        ),
    )
    monkeypatch.setattr(ss, "getStudSphereSyncer", lambda: syncer)
    start_new_sorting_session(reason="test")
    return fake, syncer


def piece(uuid, bin_index, part="3070", color="17"):
    return {
        "uuid": uuid,
        "destination_bin": [0, 0, bin_index],
        "distributed_at": 10.0,
        "part_id": part,
        "color_id": color,
        "color_name": "x",
        "classification_status": "classified",
    }


def drain(syncer):
    config = local_state.get_studsphere_config()
    if not syncer._set_up:
        syncer._setup(config)
    while syncer._sendBatch(config):
        pass


def turn_on(syncer):
    local_state.set_studsphere_config({"enabled": True, "url": "http://ss/", "api_key": "ssk_test"})
    syncer.resync()


def test_nothing_queued_while_off(env):
    fake, syncer = env
    record_piece_distribution(piece("a", 0))
    assert ss.pendingCount() == 0


def test_resync_sends_current_contents_then_live_pieces(env):
    fake, syncer = env
    record_piece_distribution(piece("a", 0))  # before connecting
    record_piece_distribution(piece("b", 0, color="1"))
    turn_on(syncer)
    record_piece_distribution(piece("c", 1))  # live
    record_piece_distribution(piece("d", 1, part="99999"))  # unknown to StudSphere
    record_piece_distribution(piece("e", 1, part=None))  # unidentified: not sent at all
    drain(syncer)

    assert fake.bins["L1-S1-B1"] == Counter({("3070", "17"): 1, ("3070", "1"): 1})
    assert fake.bins["L1-S1-B2"] == Counter({("3070", "17"): 1})
    assert ss.pendingCount() == 0
    assert syncer.status()["unmapped"] == [{"part": "99999", "color": "17"}]
    assert syncer.status()["location"] == "Test sorter"


def test_emptied_bin_with_receipt_moves_into_its_bag(env):
    fake, syncer = env
    turn_on(syncer)
    record_piece_distribution(piece("a", 0))
    record_piece_distribution(piece("b", 0))
    clear_current_session_bins(scope="bin", layer_index=0, section_index=0, bin_index=0, zip_name="ZIP0004")
    record_piece_distribution(piece("c", 0))  # after emptying: in the bin again
    drain(syncer)

    assert fake.zips["ZIP0004"] == Counter({("3070", "17"): 2})
    assert fake.bins["L1-S1-B1"] == Counter({("3070", "17"): 1})


def test_emptied_without_receipt_goes_to_zero_and_layer_clears_have_no_bag(env):
    fake, syncer = env
    turn_on(syncer)
    record_piece_distribution(piece("a", 0))
    record_piece_distribution(piece("b", 1))
    clear_current_session_bins(scope="layer", layer_index=0, zip_name="ZIP0009")
    drain(syncer)

    assert fake.zips == {}
    assert fake.bins["L1-S1-B1"] == Counter() and fake.bins["L1-S1-B2"] == Counter()


def test_resent_batch_counts_once(env):
    fake, syncer = env
    turn_on(syncer)
    record_piece_distribution(piece("a", 0))
    drain(syncer)
    # StudSphere applied it, but the answer got lost: the sorter sends again.
    config = local_state.get_studsphere_config()
    record_piece_distribution(piece("b", 0))
    pending = ss._pendingOps(10)
    fake("POST", "http://ss/api/sorter.php?action=ops", json={"stream_id": config["stream_id"], "ops": pending},
         headers={"Authorization": "Bearer ssk_test"})
    drain(syncer)  # the same ops again
    assert fake.bins["L1-S1-B1"] == Counter({("3070", "17"): 2})


def test_unreachable_studsphere_keeps_the_outbox(env):
    fake, syncer = env
    turn_on(syncer)
    record_piece_distribution(piece("a", 0))
    fake.fail_next = 1
    with pytest.raises(ss.StudSphereError):
        drain(syncer)
    assert ss.pendingCount() > 0
    drain(syncer)
    assert ss.pendingCount() == 0
    assert fake.bins["L1-S1-B1"] == Counter({("3070", "17"): 1})


def test_turning_off_empties_the_outbox(env):
    fake, syncer = env
    turn_on(syncer)
    record_piece_distribution(piece("a", 0))
    syncer.disable()
    assert ss.pendingCount() == 0
    record_piece_distribution(piece("b", 0))
    assert ss.pendingCount() == 0


def test_api_key_is_encrypted_at_rest(env):
    local_state.set_studsphere_config({"api_key": "ssk_secret"})
    raw = json.dumps(local_state._read_state("studsphere"))
    assert "ssk_secret" not in raw
    assert local_state.get_studsphere_config()["api_key"] == "ssk_secret"


def test_normalize_url():
    assert ss.normalizeUrl(" https://studsphere.example/app ") == "https://studsphere.example/app/"
    assert ss.apiUrl("https://s.example/app/", "ops") == "https://s.example/app/api/sorter.php?action=ops"
    with pytest.raises(ValueError):
        ss.normalizeUrl("studsphere.example")
