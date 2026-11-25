"""The receipt printer: an Epson TM-T88IV (or any ESC/POS printer) on a serial
port, usually an FTDI USB-RS232 adapter.

Settings live in machine.toml under [receipt_printer]. The port is opened for
each job and closed again, so a printer that gets unplugged or switched off
costs nothing until the next print, and nothing holds the port in between.

The serial settings have to match the printer's DIP switches. The TM-T88IV
prints its current ones on a self-test page: hold FEED while switching it on.

Flow control is RTS/CTS: the printer drives its busy signal on DTR and RTS
alike, and with the usual null-modem cable that arrives on the adapter's CTS.
DTR/DSR isn't offered because pyserial only does it on Windows; on Linux it
would silently mean no flow control at all.

Status comes from the real-time command DLE EOT n, which the printer answers
with one byte even while it is offline (cover open, paper out).
"""

from __future__ import annotations

import datetime
import os
import textwrap
import threading
from typing import Any

SETTINGS_SECTION = "receipt_printer"

# ESC t n: the code page number the printer knows, and the Python codec that
# encodes text for it. PC858 is PC850 with the euro sign; both carry German
# umlauts.
CODEPAGES: dict[str, tuple[int, str, str]] = {
    "pc437": (0, "cp437", "PC437 (USA, standard Europe)"),
    "pc850": (2, "cp850", "PC850 (Multilingual)"),
    "pc858": (19, "cp858", "PC858 (Multilingual with euro)"),
    "pc860": (3, "cp860", "PC860 (Portuguese)"),
    "pc863": (4, "cp863", "PC863 (Canadian-French)"),
    "pc865": (5, "cp865", "PC865 (Nordic)"),
    "pc852": (18, "cp852", "PC852 (Latin 2)"),
    "pc866": (17, "cp866", "PC866 (Cyrillic)"),
    "wpc1252": (16, "cp1252", "WPC1252 (Windows Latin 1)"),
}
BAUDRATES = (2400, 4800, 9600, 19200, 38400, 57600, 115200)
PARITIES = ("none", "even", "odd")
FLOW_CONTROLS = ("rtscts", "xonxoff", "none")
CUT_MODES = ("partial", "full", "none")

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "port": "",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "none",
    "stopbits": 1,
    "flow_control": "rtscts",
    "columns": 42,
    "codepage": "pc858",
    "cut": "partial",
    "feed_lines": 4,
    "header": "",
    "footer": "",
    "bin_inventory_on_empty": False,
    "bin_inventory_prefix": "ZIP",
}

# Font B is 9x17 dots against font A's 12x24, so a line holds a third more.
# Line spacing for the compact font B lines, in the TM-T88IV's vertical motion
# unit of 1/360 inch (half a dot): 42 is 21 dots for a 17-dot font, against the
# default 60 made for font A. Below about 36 the lines touch.
FONT_B_LINE_SPACING = 42

WRITE_TIMEOUT_S = 10.0
STATUS_TIMEOUT_S = 0.5

ESC = b"\x1b"
GS = b"\x1d"
DLE = b"\x10"


class PrinterError(Exception):
    """A print or status request that didn't reach the printer; the message
    says why in words a person can act on."""


def getSettings() -> dict[str, Any]:
    import machine_toml

    section = machine_toml.read().get(SETTINGS_SECTION)
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(section, dict):
        settings.update({k: v for k, v in section.items() if k in DEFAULT_SETTINGS})
    if settings["flow_control"] not in FLOW_CONTROLS:
        settings["flow_control"] = DEFAULT_SETTINGS["flow_control"]
    return settings


def validateSettings(settings: dict[str, Any]) -> str | None:
    if settings["baudrate"] not in BAUDRATES:
        return f"Baud rate must be one of {', '.join(map(str, BAUDRATES))}."
    if settings["bytesize"] not in (7, 8):
        return "Data bits must be 7 or 8."
    if settings["parity"] not in PARITIES:
        return "Parity must be none, even or odd."
    if settings["stopbits"] not in (1, 2):
        return "Stop bits must be 1 or 2."
    if settings["flow_control"] not in FLOW_CONTROLS:
        return "Unknown flow control."
    if settings["codepage"] not in CODEPAGES:
        return "Unknown code page."
    if settings["cut"] not in CUT_MODES:
        return "Cut must be partial, full or none."
    if not 16 <= settings["columns"] <= 80:
        return "Characters per line must be between 16 and 80."
    if not 0 <= settings["feed_lines"] <= 20:
        return "Feed before cut must be between 0 and 20 lines."
    prefix = settings["bin_inventory_prefix"]
    if len(prefix) > 3 or not prefix.isprintable():
        return "The inventory number prefix is at most 3 characters."
    if settings["enabled"] and not settings["port"]:
        return "Choose a port before turning the printer on."
    return None


def setSettings(updates: dict[str, Any]) -> dict[str, Any]:
    """Merges updates over the current settings; raises ValueError when the
    result doesn't make sense."""
    import machine_toml

    merged = getSettings()
    for key, value in updates.items():
        if key in DEFAULT_SETTINGS and value is not None:
            kind = type(DEFAULT_SETTINGS[key])
            merged[key] = value.strip() if key in ("port", "bin_inventory_prefix") else kind(value)
    error = validateSettings(merged)
    if error:
        raise ValueError(error)
    with machine_toml.edit() as config:
        config[SETTINGS_SECTION] = merged
    return merged


# --- Ports -------------------------------------------------------------------


def _byIdLinks() -> dict[str, str]:
    """/dev/ttyUSB0 -> /dev/serial/by-id/usb-FTDI_..., so a saved port stays
    the same adapter when the ttyUSB numbers move after a reboot."""
    base = "/dev/serial/by-id"
    links: dict[str, str] = {}
    try:
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            links[os.path.realpath(path)] = path
    except OSError:
        pass
    return links


def listPorts() -> list[dict[str, Any]]:
    import serial.tools.list_ports

    from hardware.bus import MCUBus

    board_ids = set(MCUBus.KNOWN_BOARD_USB_IDS)
    links = _byIdLinks()
    ports = []
    for port in serial.tools.list_ports.comports():
        device = port.device
        stable = links.get(os.path.realpath(device), device)
        ports.append(
            {
                "path": stable,
                "device": device,
                "description": port.description if port.description != "n/a" else None,
                "manufacturer": port.manufacturer,
                "serial_number": port.serial_number,
                "vid_pid": f"{port.vid:04x}:{port.pid:04x}" if port.vid is not None else None,
                "is_ftdi": port.vid == 0x0403,
                "is_control_board": (port.vid, port.pid) in board_ids,
            }
        )
    ports.sort(key=lambda p: (p["is_control_board"], not p["is_ftdi"], p["path"]))
    return ports


# --- ESC/POS -----------------------------------------------------------------


def _encode(text: str, settings: dict[str, Any]) -> bytes:
    codec = CODEPAGES[settings["codepage"]][1]
    return text.encode(codec, errors="replace")


def _wrap(text: str, columns: int) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines() or [""]:
        lines.extend(textwrap.wrap(raw, columns, replace_whitespace=False, drop_whitespace=True) or [""])
    return lines


class Receipt:
    """Builds one job's bytes: init, code page, then text with a little
    formatting, then the feed and cut."""

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self.columns_a = int(settings["columns"])
        self.columns = self.columns_a
        self.data = bytearray(ESC + b"@")
        self.data += ESC + b"t" + bytes([CODEPAGES[settings["codepage"]][0]])

    def font(self, which: str) -> "Receipt":
        """Font A, the normal one, at the printer's own line spacing; or the
        small font B on tight lines, a third more characters per line."""
        if which == "b":
            self.data += ESC + b"M" + bytes([1]) + ESC + b"3" + bytes([FONT_B_LINE_SPACING])
            self.columns = self.columns_a * 4 // 3
        else:
            self.data += ESC + b"M" + bytes([0]) + ESC + b"2"
            self.columns = self.columns_a
        return self

    def align(self, where: str) -> "Receipt":
        self.data += ESC + b"a" + bytes([{"left": 0, "center": 1, "right": 2}[where]])
        return self

    def bold(self, on: bool) -> "Receipt":
        self.data += ESC + b"E" + bytes([1 if on else 0])
        return self

    def size(self, width: int = 1, height: int = 1) -> "Receipt":
        self.data += GS + b"!" + bytes([((width - 1) << 4) | (height - 1)])
        return self

    def text(self, text: str, columns: int | None = None) -> "Receipt":
        for line in _wrap(text, columns or self.columns):
            self.data += _encode(line, self.settings) + b"\n"
        return self

    def row(self, left: str, right: str) -> "Receipt":
        """Left and right on one line, the right side flush to the edge."""
        room = max(1, self.columns - len(right) - 1)
        left = left[:room]
        return self.text(left + " " * (self.columns - len(left) - len(right)) + right)

    def rule(self, char: str = "-") -> "Receipt":
        return self.text(char * self.columns)

    def blank(self, lines: int = 1) -> "Receipt":
        self.data += b"\n" * lines
        return self

    def header(self) -> "Receipt":
        if self.settings["header"].strip():
            self.align("center").bold(True).text(self.settings["header"]).bold(False).align("left").blank()
        return self

    def finish(self) -> bytes:
        self.font("a")
        if self.settings["footer"].strip():
            self.blank().align("center").text(self.settings["footer"]).align("left")
        feed = int(self.settings["feed_lines"])
        if feed:
            self.data += ESC + b"d" + bytes([feed])
        if self.settings["cut"] == "full":
            self.data += GS + b"V\x00"
        elif self.settings["cut"] == "partial":
            self.data += GS + b"V\x01"
        return bytes(self.data)


# --- Talking to the printer --------------------------------------------------

_lock = threading.Lock()


def _open(settings: dict[str, Any]):
    import serial

    if not settings["port"]:
        raise PrinterError("No port chosen for the printer.")
    flow = settings["flow_control"]
    try:
        return serial.Serial(
            port=settings["port"],
            baudrate=int(settings["baudrate"]),
            bytesize=int(settings["bytesize"]),
            parity={"none": serial.PARITY_NONE, "even": serial.PARITY_EVEN, "odd": serial.PARITY_ODD}[
                settings["parity"]
            ],
            stopbits=serial.STOPBITS_TWO if int(settings["stopbits"]) == 2 else serial.STOPBITS_ONE,
            xonxoff=flow == "xonxoff",
            rtscts=flow == "rtscts",
            timeout=STATUS_TIMEOUT_S,
            write_timeout=WRITE_TIMEOUT_S,
        )
    except serial.SerialException as exc:
        text = str(exc)
        if "Permission denied" in text:
            raise PrinterError(
                f"No permission to open {settings['port']}: the backend's user needs to be in the dialout group."
            ) from exc
        if "No such file" in text or "could not open port" in text:
            raise PrinterError(f"{settings['port']} isn't there: is the USB adapter plugged in?") from exc
        raise PrinterError(f"Could not open {settings['port']}: {text}") from exc


def _send(settings: dict[str, Any], data: bytes) -> None:
    import serial

    with _lock:
        port = _open(settings)
        try:
            port.write(data)
            port.flush()
        except serial.SerialTimeoutException as exc:
            hint = (
                "CTS never went ready"
                if settings["flow_control"] == "rtscts"
                else "the port didn't take the data"
            )
            raise PrinterError(
                f"The printer didn't take the job within {WRITE_TIMEOUT_S:.0f} s ({hint}). "
                "Is it on and online, and does the cable carry the handshake lines?"
            ) from exc
        except serial.SerialException as exc:
            raise PrinterError(f"Writing to the printer failed: {exc}") from exc
        finally:
            port.close()


def _parseStatus(replies: dict[int, int | None]) -> dict[str, Any]:
    """DLE EOT 1..4, as the TM-T88IV documents them. Each reply byte has bit 4
    set and bit 7 clear; anything else isn't a status byte."""

    def valid(n: int) -> int | None:
        b = replies.get(n)
        return b if b is not None and b & 0x12 == 0x12 and not b & 0x81 else None

    printer, offline, error, paper = valid(1), valid(2), valid(3), valid(4)
    if printer is None:
        return {"reachable": False}
    return {
        "reachable": True,
        "online": not printer & 0x08,
        "cover_open": bool(offline is not None and offline & 0x04),
        "feed_button": bool(offline is not None and offline & 0x08),
        "paper_out": bool(
            (offline is not None and offline & 0x20) or (paper is not None and paper & 0x60)
        ),
        "paper_near_end": bool(paper is not None and paper & 0x0C),
        "cutter_error": bool(error is not None and error & 0x08),
        "unrecoverable_error": bool(error is not None and error & 0x20),
        "auto_recoverable_error": bool(error is not None and error & 0x40),
    }


def readStatus(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    import serial

    settings = settings or getSettings()
    with _lock:
        port = _open(settings)
        try:
            port.reset_input_buffer()
            replies: dict[int, int | None] = {}
            for n in (1, 2, 3, 4):
                port.write(DLE + b"\x04" + bytes([n]))
                port.flush()
                reply = port.read(1)
                replies[n] = reply[0] if reply else None
        except serial.SerialTimeoutException:
            return {"reachable": False, "reason": "handshake"}
        except serial.SerialException as exc:
            raise PrinterError(f"Talking to the printer failed: {exc}") from exc
        finally:
            port.close()
    status = _parseStatus(replies)
    if not status["reachable"]:
        status["reason"] = "no_reply"
    return status


def printTest(settings: dict[str, Any] | None = None) -> None:
    settings = settings or getSettings()
    label = CODEPAGES[settings["codepage"]][2]
    r = Receipt(settings).header()
    r.align("center").size(2, 2).text("TEST", columns=r.columns // 2).size().align("left").blank()
    r.row("Printed", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    r.row("Port", os.path.basename(settings["port"]) or "-")
    r.row(
        "Serial",
        f"{settings['baudrate']} {settings['bytesize']}"
        f"{settings['parity'][0].upper()}{settings['stopbits']} {settings['flow_control']}",
    )
    r.row("Code page", label.split(" ")[0])
    r.rule()
    r.text("Umlauts: ÄÖÜ äöü ß  Euro: €")
    r.bold(True).text("Bold text").bold(False)
    r.size(1, 2).text("Double height").size()
    r.rule()
    r.text("".join(str(i % 10) for i in range(1, r.columns + 1)))
    r.text(f"The line above should end at the right edge ({r.columns} characters).")
    _send(settings, r.finish())


def printText(text: str, settings: dict[str, Any] | None = None) -> None:
    settings = settings or getSettings()
    if not text.strip():
        raise PrinterError("Nothing to print.")
    _send(settings, Receipt(settings).header().text(text).finish())


def printReceipt(build, settings: dict[str, Any] | None = None) -> bool:
    """For the rest of the backend: build(receipt) fills a Receipt, and it
    goes out only when the printer is turned on. Returns whether it printed."""
    settings = settings or getSettings()
    if not settings["enabled"]:
        return False
    receipt = Receipt(settings).header()
    build(receipt)
    _send(settings, receipt.finish())
    return True


# --- Bin inventory -----------------------------------------------------------


def binLabel(layer_index: int, section_index: int, bin_index: int) -> str:
    return f"L{layer_index + 1} - S{section_index + 1} - B{bin_index + 1}"


def inventoryNumber(prefix: str, number: int) -> str:
    return f"{prefix}{number:04d}"


def groupBinItems(items: list[dict[str, Any]]) -> list[tuple[str, int, list[tuple[str, int]]]]:
    """(part, total, [(color, count)]) for each part type in a bin, most pieces
    first, the colors within a part likewise."""
    parts: dict[str, dict[str, int]] = {}
    for item in items:
        part = str(item.get("part_id") or "?")
        color = str(item.get("color_name") or "Unknown color")
        colors = parts.setdefault(part, {})
        colors[color] = colors.get(color, 0) + int(item.get("count") or 0)
    grouped = [
        (part, sum(colors.values()), sorted(colors.items(), key=lambda kv: (-kv[1], kv[0])))
        for part, colors in parts.items()
    ]
    return sorted(grouped, key=lambda g: (-g[1], g[0]))


def buildBinInventory(
    settings: dict[str, Any],
    *,
    number: str,
    label: str,
    items: list[dict[str, Any]],
    part_names: dict[str, str],
    date: datetime.date,
) -> bytes:
    """The number big, bold and centred; the bin and the date; then each part
    type in normal type with a rule under it, and its colors in small type."""
    r = Receipt(settings).header()
    big = 3
    r.align("center").bold(True).size(big, big).text(number, columns=r.columns // big)
    r.size().bold(False).align("left").blank()
    r.bold(True).row(label, date.strftime("%d.%m.%Y")).bold(False)
    for part, total, colors in groupBinItems(items):
        r.font("a").blank()
        r.bold(True).text(f"{total}x {part} {part_names.get(part, '')}".rstrip()).bold(False)
        r.rule()
        r.font("b")
        for color, count in colors:
            r.text(f"{count}x {color}")
    return r.finish()


_inventory_lock = threading.Lock()


def printBinInventory(
    layer_index: int,
    section_index: int,
    bin_index: int,
    items: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
) -> str | None:
    """Prints a bin's inventory before it gets emptied, when the printer and
    the inventory print are both on and the bin holds anything. Returns the
    number it printed, or None when it printed nothing. Raises PrinterError
    when the printer didn't take it; the number is only used up on success."""
    import local_state
    import piece_records

    settings = settings or getSettings()
    if not (settings["enabled"] and settings["bin_inventory_on_empty"]) or not items:
        return None
    names = piece_records.partNames([str(i.get("part_id") or "") for i in items])
    with _inventory_lock:
        sequence = local_state.get_bin_inventory_next_number()
        number = inventoryNumber(settings["bin_inventory_prefix"], sequence)
        data = buildBinInventory(
            settings,
            number=number,
            label=binLabel(layer_index, section_index, bin_index),
            items=items,
            part_names=names,
            date=datetime.date.today(),
        )
        _send(settings, data)
        local_state.set_bin_inventory_next_number(sequence + 1)
    return number
