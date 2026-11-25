"""The receipt printer's settings page: its settings, the serial ports to pick
from, its live status, a test print and free text. receipt_printer.py does
the work."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    try:
        return {"settings": receipt_printer.setSettings(payload.model_dump(exclude_none=True))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
