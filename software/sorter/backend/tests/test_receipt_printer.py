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
    assert rp.Receipt(settings).finish().endswith(b"\x1bt\x13")
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
