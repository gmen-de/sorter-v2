import pytest

import machine_toml
import receipt_printer as rp


@pytest.fixture
def settings():
    s = dict(rp.DEFAULT_SETTINGS)
    s.update(port="/dev/ttyUSB0", columns=20)
    return s


def test_receipt_bytes_init_codepage_text_cut(settings):
    data = rp.Receipt(settings).text("Grün €").finish()
    assert data.startswith(b"\x1b@\x1bt\x13")  # ESC @, ESC t 19 (PC858)
    assert "Grün €\n".encode("cp858") in data
    assert data.endswith(b"\x1bd\x04\x1dV\x01")  # feed 4, partial cut


def test_receipt_wraps_and_aligns_rows(settings):
    r = rp.Receipt(settings)
    r.text("a" * 25)
    r.row("Port", "ttyUSB0")
    body = bytes(r.data)
    assert b"a" * 20 + b"\n" + b"a" * 5 + b"\n" in body
    assert b"Port" + b" " * 9 + b"ttyUSB0\n" in body


def test_cut_none_and_full(settings):
    settings.update(cut="none", feed_lines=0)
    assert rp.Receipt(settings).finish().endswith(b"\x1bM\x00\x1b2")  # back to font A, no feed, no cut
    settings["cut"] = "full"
    assert rp.Receipt(settings).finish().endswith(b"\x1dV\x00")


def test_header_and_footer(settings):
    settings.update(header="My Shop", footer="Danke!")
    data = rp.Receipt(settings).header().finish()
    assert b"My Shop\n" in data and b"Danke!\n" in data


def test_parse_status_online_and_problems():
    ok = rp._parseStatus({1: 0x16, 2: 0x12, 3: 0x12, 4: 0x12})
    assert ok["reachable"] and ok["online"] and not ok["paper_out"] and not ok["cover_open"]

    bad = rp._parseStatus({1: 0x1E, 2: 0x16 | 0x20, 3: 0x12 | 0x08, 4: 0x12 | 0x0C | 0x60})
    assert not bad["online"]
    assert bad["cover_open"] and bad["paper_out"] and bad["paper_near_end"] and bad["cutter_error"]


def test_parse_status_no_or_garbage_reply():
    assert rp._parseStatus({1: None}) == {"reachable": False}
    assert rp._parseStatus({1: 0x41}) == {"reachable": False}


def test_settings_validation(monkeypatch, tmp_path):
    monkeypatch.setenv(machine_toml.ENV_VAR, str(tmp_path / "machine.toml"))
    with pytest.raises(ValueError):
        rp.setSettings({"enabled": True})  # no port yet
    with pytest.raises(ValueError):
        rp.setSettings({"baudrate": 12345})
    saved = rp.setSettings({"port": " /dev/serial/by-id/usb-FTDI ", "enabled": True, "codepage": "pc850"})
    assert saved["port"] == "/dev/serial/by-id/usb-FTDI"
    assert rp.getSettings()["codepage"] == "pc850"


def test_print_receipt_only_when_enabled(monkeypatch, settings):
    sent = []
    monkeypatch.setattr(rp, "_send", lambda s, data: sent.append(data))
    assert rp.printReceipt(lambda r: r.text("x"), settings) is False
    settings["enabled"] = True
    assert rp.printReceipt(lambda r: r.text("x"), settings) is True
    assert len(sent) == 1


ITEMS = [
    {"part_id": "3070", "color_name": "Trans-Red", "count": 3},
    {"part_id": "3069", "color_name": "White", "count": 4},
    {"part_id": "3070", "color_name": "Trans-Green", "count": 1},
    {"part_id": "3070", "color_name": "Trans-Dark Blue", "count": 1},
]


def test_group_bin_items_most_pieces_first():
    assert rp.groupBinItems(ITEMS) == [
        ("3070", 5, [("Trans-Red", 3), ("Trans-Dark Blue", 1), ("Trans-Green", 1)]),
        ("3069", 4, [("White", 4)]),
    ]


def test_bin_inventory_layout(settings):
    import datetime

    settings["columns"] = 42
    data = rp.buildBinInventory(
        settings,
        number="ZIP0007",
        label=rp.binLabel(0, 0, 0),
        items=ITEMS,
        part_names={"3070": "Tile 1 x 1"},
        date=datetime.date(2026, 11, 26),
    )
    big = b"\x1bE\x01\x1d!\x22ZIP0007\n"  # bold, 3x3
    row = b"\x1bE\x01L1 - S1 - B1" + b" " * 20 + b"26.11.2026\n\x1bE\x00"  # bold
    part = b"\x1bM\x00\x1b2\n\x1bE\x015x 3070 Tile 1 x 1\n\x1bE\x00" + b"-" * 42 + b"\n\x1bM\x01\x1b3" + bytes([rp.FONT_B_LINE_SPACING])
    colors = b"3x Trans-Red\n1x Trans-Dark Blue\n1x Trans-Green\n"
    assert big in data and row in data and part in data and colors in data
    assert data.index(b"5x 3070") < data.index(b"4x 3069\n")
    assert rp.inventoryNumber("", 12) == "0012"


def test_print_bin_inventory_counts_only_on_success(monkeypatch, settings):
    import local_state
    import piece_records

    counter = {"n": 7}
    monkeypatch.setattr(local_state, "get_bin_inventory_next_number", lambda: counter["n"])
    monkeypatch.setattr(local_state, "set_bin_inventory_next_number", lambda n: counter.update(n=n))
    monkeypatch.setattr(piece_records, "partNames", lambda ids: {})
    settings.update(enabled=True, bin_inventory_on_empty=True)

    def fail(s, data):
        raise rp.PrinterError("off")

    monkeypatch.setattr(rp, "_send", fail)
    with pytest.raises(rp.PrinterError):
        rp.printBinInventory(0, 0, 0, ITEMS, settings)
    assert counter["n"] == 7

    monkeypatch.setattr(rp, "_send", lambda s, data: None)
    assert rp.printBinInventory(0, 0, 0, ITEMS, settings) == "ZIP0007"
    assert counter["n"] == 8
    assert rp.printBinInventory(0, 0, 0, [], settings) is None
    settings["bin_inventory_on_empty"] = False
    assert rp.printBinInventory(0, 0, 0, ITEMS, settings) is None


def test_empty_bin_stays_full_when_inventory_fails(monkeypatch):
    from fastapi import HTTPException

    from server.routers import bins

    cleared = []
    monkeypatch.setattr(bins, "_current_bin_categories", lambda: [[[[]]]])
    monkeypatch.setattr(bins, "clear_current_session_bins", lambda **kw: cleared.append(kw) or {"cleared_bins": 1})
    monkeypatch.setattr(bins, "_clear_runtime_bin_contents", lambda **kw: cleared.append(kw))
    monkeypatch.setattr(bins, "get_bin_contents", lambda: {"bins": [dict(layer_index=0, section_index=0, bin_index=0, items=ITEMS)]})
    monkeypatch.setattr(rp, "getSettings", lambda: dict(rp.DEFAULT_SETTINGS, enabled=True, bin_inventory_on_empty=True, port="x"))

    def fail(*a, **kw):
        raise rp.PrinterError("Printer off.")

    monkeypatch.setattr(rp, "printBinInventory", fail)
    with pytest.raises(HTTPException) as err:
        bins.clear_bin_contents(scope="bin", layer_index=0, section_index=0, bin_index=0)
    assert err.value.status_code == 502 and not cleared

    monkeypatch.setattr(rp, "printBinInventory", lambda *a, **kw: "ZIP0001")
    result = bins.clear_bin_contents(scope="bin", layer_index=0, section_index=0, bin_index=0)
    assert result["inventory_number"] == "ZIP0001" and "ZIP0001" in result["message"] and cleared
