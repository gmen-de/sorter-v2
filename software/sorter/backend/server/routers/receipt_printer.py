"""The receipt printer's settings page: its settings, the serial ports to pick
from, its live status, a test print and free text. receipt_printer.py does
the work."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import local_state
import receipt_printer

router = APIRouter()


class PrinterSettingsPayload(BaseModel):
    enabled: bool | None = None
    port: str | None = None
    baudrate: int | None = None
    bytesize: int | None = None
    parity: str | None = None
    stopbits: int | None = None
    flow_control: str | None = None
    columns: int | None = None
    codepage: str | None = None
    cut: str | None = None
    feed_lines: int | None = None
    header: str | None = None
    footer: str | None = None
    bin_inventory_on_empty: bool | None = None
    bin_inventory_prefix: str | None = None
    # Not in machine.toml: the running number lives in local_state.
    bin_inventory_next_number: int | None = None


class PrintTextPayload(BaseModel):
    text: str


@router.get("/api/receipt-printer")
def get_printer() -> dict[str, Any]:
    try:
        ports = receipt_printer.listPorts()
    except Exception:
        ports = []
    return {
        "settings": receipt_printer.getSettings(),
        "bin_inventory_next_number": local_state.get_bin_inventory_next_number(),
        "ports": ports,
        "options": {
            "baudrates": list(receipt_printer.BAUDRATES),
            "parities": list(receipt_printer.PARITIES),
            "flow_controls": list(receipt_printer.FLOW_CONTROLS),
            "cut_modes": list(receipt_printer.CUT_MODES),
            "codepages": [
                {"value": key, "label": label} for key, (_, _, label) in receipt_printer.CODEPAGES.items()
            ],
        },
    }


@router.post("/api/receipt-printer/settings")
def save_settings(payload: PrinterSettingsPayload) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    next_number = updates.pop("bin_inventory_next_number", None)
    if next_number is not None and not 1 <= next_number <= 999999:
        raise HTTPException(400, "The next inventory number must be between 1 and 999999.")
    try:
        settings = receipt_printer.setSettings(updates)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if next_number is not None:
        local_state.set_bin_inventory_next_number(next_number)
    return {"settings": settings, "bin_inventory_next_number": local_state.get_bin_inventory_next_number()}


@router.get("/api/receipt-printer/status")
def get_status() -> dict[str, Any]:
    try:
        return receipt_printer.readStatus()
    except receipt_printer.PrinterError as exc:
        return {"reachable": False, "reason": "port", "error": str(exc)}


@router.post("/api/receipt-printer/test")
def test_print() -> dict[str, Any]:
    try:
        receipt_printer.printTest()
    except receipt_printer.PrinterError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True}


@router.post("/api/receipt-printer/print")
def print_text(payload: PrintTextPayload) -> dict[str, Any]:
    try:
        receipt_printer.printText(payload.text)
    except receipt_printer.PrinterError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True}
