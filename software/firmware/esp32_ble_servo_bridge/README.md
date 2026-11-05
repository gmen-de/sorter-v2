# ESP32 BLE Servo Bridge

Custom firmware for the [Waveshare "Servo Driver with ESP32"](https://www.waveshare.com/wiki/Servo_Driver_with_ESP32)
board, **replacing Waveshare's own firmware**. It drives Waveshare SC15 (and
other SCSCL-protocol) bus servos over the board's servo UART, and controls
them over Bluetooth Low Energy using a protocol this project defines itself
— Waveshare does not document a BLE protocol for this board (their firmware
only offers WiFi/HTTP, USB-serial, and ESP-NOW).

All servo intelligence lives on the ESP32: open/closed/blocked detection,
automatic ID assignment, calibration, and factory reset. The host (the
sorter backend) only ever sends short gate-level commands and reads back
gate-level state — see [`software/sorter/backend/hardware/waveshare_ble_bridge.py`](../../sorter/backend/hardware/waveshare_ble_bridge.py)
for the Python client.

## Hardware

- Board: Waveshare "Servo Driver with ESP32" (ESP32-WROOM-32).
- Servos: Waveshare SC15 (or other SC-series/SCSCL-protocol bus servos),
  connected to the board's servo bus header, one per logical bin-layer gate.
- Servo bus: UART1, GPIO18 (RX) / GPIO19 (TX), 1 Mbps, half-duplex single
  wire (handled by the board's own bus circuitry — these are Waveshare's own
  pin defaults for this board, taken from their stock `ServoDriver.ino`, not
  something invented here).

This firmware does **not** use the board's OLED or RGB LED — deliberately
left out to keep the first version small and easy to reason about without
being able to test on real hardware while writing it. Add them later if
useful.

## Gate numbering and auto ID assignment

Gate *N* is always servo ID `N + 10` (gate 1 = ID 11, gate 2 = ID 12, …).
IDs 1-10 are never used for gates; ID 1 is the factory-default ID every new
SC-series servo boots with, so it doubles as "not yet learned".

Onboarding a fresh set of servos:

1. Connect **one** new (factory-default) servo to the bus.
2. Send `LEARN`. The board finds the servo at ID 1, assigns it the next free
   gate ID (11, then 12, then 13, …), calibrates its open/closed limits, and
   replies `LEARNED <gate> OK`.
3. Disconnect it (or leave it — it now has a unique ID) and connect the next
   new servo. Repeat.

To redo this from scratch (e.g. after re-wiring, or a servo was swapped),
send `RESETALL` first: every currently-reachable servo (IDs 1 and 11-60) is
reset to factory defaults — ID back to 1, angle limits back to 0-1023 — so
the one-at-a-time `LEARN` flow above can start over. After `RESETALL`,
every previously-learned servo answers to ID 1, so connect them one at a
time again rather than all at once, same as onboarding new ones.

## BLE protocol

The device advertises as `SORTER-SERVO-BLE` using the well-known **Nordic
UART Service** UUIDs, so any generic BLE serial terminal app (e.g. "Serial
Bluetooth Terminal", nRF Connect) can talk to it for debugging without any
custom tooling:

| | UUID |
|---|---|
| Service | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX (write — host → board) | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` |
| TX (notify — board → host) | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` |

Commands and responses are newline-terminated ASCII lines (notifications are
chunked to 20 bytes and may arrive as several BLE packets per line — the
Python client reassembles them on `\n`).

| Command | Response | Meaning |
|---|---|---|
| `OPEN <gate>` | `<gate> OPEN` / `<gate> BLOCKED` / `<gate> ERR UNCALIBRATED` / `<gate> ERR OFFLINE` | Move the gate open, report whether it got there. |
| `CLOSE <gate>` | `<gate> CLOSED` / `<gate> BLOCKED` / … | Same, closing. |
| `STATUS <gate>` | `<gate> OPEN` / `<gate> CLOSED` / `<gate> UNKNOWN` / `<gate> ERR …` | Read current state without moving. |
| `LEARN` | `LEARNED <gate> OK` / `LEARNED <gate> NOCAL` / `LEARN NONE` / `LEARN ERR …` | Assign the next gate ID to a servo at factory ID 1, then calibrate it. |
| `CALIBRATE <gate>` | `CALIBRATED <gate>` / `CALIBRATE ERR <gate>` | Re-run calibration on an already-numbered servo. |
| `RESETALL` | `RESETALL OK <count>` | Factory-reset every reachable servo (see above). |
| `LIST` | `GATES <g1> <g2> …` | Which gate IDs currently answer on the bus. |
| `PING` | `PONG` | Liveness check. |
| anything else | `ERR UNKNOWN_CMD` | |

### Unsolicited message: `READY GATES <g1> <g2> …`

Sent once per connection, with no command needed, as soon as the host
subscribes to notifications on the TX characteristic (`onSubscribe`, not
`onConnect` — a notification sent before the client's subscribe write would
be silently dropped). Lets the host (the sorter's Orange Pi) learn how many
servos are reachable and which gate numbers they are right after connecting,
without having to send `LIST` itself. `GATES` with no numbers after it means
none answered.

Calibration (`LEARN`, `CALIBRATE`) is a direct C++ port of
`calibrate_servo()` in [`waveshare_servo.py`](../../sorter/backend/hardware/waveshare_servo.py)
— same step size, settle times, and stall-load threshold — so a servo
calibrates the same way whether it's on this BLE bridge or the wired
USB-serial path. It steps the servo outward until it stalls, so it blocks
for several seconds up to roughly a minute; see "Known limitations" below.

## Build and flash

Uses [PlatformIO](https://platformio.org/).

```sh
cd software/firmware/esp32_ble_servo_bridge
pio run                 # build
pio run -t upload       # flash over the board's USB-C port
pio device monitor       # watch Serial logs (115200 baud)
```

The vendored `lib/SCServo/` is Waveshare's own Arduino library for
SC/ST-series bus servos (downloaded from the resources linked on their wiki
page above), used here unmodified for the low-level servo register
read/write/ping primitives. No separate license file shipped with it from
Waveshare; treated the same as the rest of that board's open hardware
example ecosystem.

## Known limitations — not yet verified on real hardware

This firmware was written from Waveshare's published pinout and their own
stock `ServoDriver.ino` sketch, and from the already-tested calibration
algorithm in the Python backend, but **has not been compiled or flashed to
real hardware yet**. Things to check once a board is available:

- `calibrateServo()` blocks the Arduino loop task for a long time (multiple
  seconds per step, up to ~30-70s total). NimBLE's own stack normally runs
  independently of the Arduino `loop()` task, so the BLE link should survive
  this, but it hasn't been confirmed — if `LEARN`/`CALIBRATE` disconnects
  the client, move calibration onto its own FreeRTOS task
  (`xTaskCreatePinnedToCore`, same pattern Waveshare's own stock firmware
  uses in `BOARD_DEV.h`).
- NimBLE-Arduino is pinned to the 1.x line in `platformio.ini` because this
  code uses that version's API (`getValue()` returning `std::string`,
  `createCharacteristic()` returning a raw pointer). If `pio run` resolves a
  2.x release anyway, check its migration notes before adjusting the pin.
- The 20-byte BLE notify chunk size is conservative (works at the default,
  un-negotiated ATT MTU). If responses feel slow, negotiate a larger MTU and
  raise `BLE_NOTIFY_CHUNK` to match.
- `RESETALL` assumes servos are addressed by their *current* ID before
  being reset to ID 1; if several already share an ID (e.g. two still at
  factory default 1), only one will answer per step — same constraint as
  the one-at-a-time `LEARN` flow.
