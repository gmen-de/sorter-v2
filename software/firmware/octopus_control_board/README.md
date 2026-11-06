# Octopus Control Board Firmware

Custom firmware for a **BIGTREETECH Octopus (standard, non-Pro) V1.0/V1.1**
3D-printer mainboard, replacing the sorter's original custom RP2040-based
"basically" distribution board
(`software/firmware/sorter_interface_firmware/`). It speaks the **exact
same host wire protocol** as that board (COBS+CRC32-framed binary commands
over USB-CDC), so the Python backend
(`software/sorter/backend/hardware/sorter_interface.py`,
`hardware/bus.py`) needs **no changes** to talk to it — this is a firmware
port, not a protocol redesign.

Built with PlatformIO, `framework = arduino` (Arduino_Core_STM32 /
stm32duino), matching the toolchain already used for this repo's ESP32 BLE
servo bridge (`software/firmware/esp32_ble_servo_bridge/`).

**Why the standard board, not the Pro (~30€ cheaper):** Marlin's own shared
pin header for this board family
(`pins_BTT_OCTOPUS_V1_common.h`, included unmodified by both the standard
and the Pro V1.0 board) confirms every pin this firmware actually uses —
all 8 motor sockets' STEP/DIR/EN, the per-socket TMC UART pins, the
DIAG/endstop pins, all 4 heater pins, all 6 fan pins — is identical between
the two. The Pro's extras (a dedicated proximity-probe port, a per-driver
60V stepper-voltage option, an optional H723/Cortex-M7 chip on some SKUs)
are all irrelevant to this project. The standard board is arguably simpler
on the one point that actually matters here: it only ever ships with an F4
Cortex-M4 chip (F446/F429/F407), never the Pro's H723 Cortex-M7 option —
see "Chip variant" below.

## Scope

In scope: 5 stepper channels (chute, 3× c-channel rotor, carousel) via
TMC2209 over a software single-wire UART, StallGuard DIAG stall detection,
2 PWM outputs repurposed from hotend MOSFETs for 24V LED/COB lighting, and
**new** chute magnet-ring position sensing (table 5, see below) — the
chute now rotates continuously past 350° via a slip ring, so its old
mechanical limit switch is gone.

Out of scope: servos (migrated separately to
`software/firmware/esp32_ble_servo_bridge/` over BLE — command table 4 in
the original protocol is simply absent here, the opcode space just NACKs
like any other undefined slot), Klipper/Marlin, SD-card G-code printing.

## Protocol

Identical framing/dispatch to the RP2040 original:
`[address][command][channel][payload_length][payload...][crc32 LE]`,
COBS-encoded, `0x00`-terminated. Command byte: bits 4-6 select one of 8
command tables, bits 0-3 select a slot within it; bit 7 marks a NACK in the
*response*. See `software/sorter/backend/hardware/bus.py`'s
`InterfaceCommandCode` for the canonical opcode list this firmware must
keep matching for tables 0-3.

**New: command table 5, `CHUTE_ENCODER`** (table index 5, i.e. opcodes
`0x50`-`0x5F`) — not present in the original protocol (table indices 5-7
were always unused `nullptr` slots there, so this is purely additive, zero
collision risk):

| idx | opcode | name | payload | response | notes |
|---|---|---|---|---|---|
| 0 | 0x50 | GET_COUNT | - | `int32` | signed running quadrature count |
| 1 | 0x51 | SET_COUNT | `int32` | - | re-baseline the count |
| 2 | 0x52 | IS_INDEX_SYNCED | - | `bool` | has the magnet-ring index gap been seen since boot/last clear |
| 3 | 0x53 | GET_COUNT_AT_INDEX | - | `int32` | count value captured at the most recent index-gap event |
| 4 | 0x54 | CLEAR_INDEX_SYNCED | - | - | clears the index-synced flag |

Channel is always `0` (there is one chute encoder). **This has no backend
consumer yet** — implementing the Python side that uses this to recalibrate
the chute's absolute position after power loss is a follow-up task once
this firmware itself has been bench-verified against the real magnet ring.

## Pin mapping

5 stepper channels on Motor0-Motor4 sockets (pin names from Marlin's/
Klipper's own open-source pin files for this board — BTT publishes no bare
pinout doc):

| Channel | Socket | STEP | DIR | EN | UART (single-wire) | DIAG/endstop-shared |
|---|---|---|---|---|---|---|
| chute_stepper | Motor0 | PF13 | PF12 | PF14 | PC4 | PG6 |
| c_channel_1_rotor | Motor1 | PG0 | PG1 | PF15 | PD11 | PG9 |
| c_channel_3_rotor | Motor2 | PF11 | PG3 | PG5 | PC6 | PG10 |
| carousel | Motor3 | PG4 | PC1 | PA0 | PC7 | PG11 |
| c_channel_2_rotor | Motor4 | PF9 | PF10 | PG2 | PF2 | PG12 |

Chute magnet-ring Hall sensors (2× DRV5013), on two otherwise-unused motor
sockets' endstop-shared pins: **PG13** (Motor5), **PG14** (Motor6).

LED PWM outputs, repurposed from hotend heater MOSFETs **HE1 → PA3** and
**HE3 → PB11**.

One spare digital input, `DIGITAL_IO` channel 0, on Motor7's endstop pin
**PG15** — reserved/unassigned, declared so the channel exists for future
use instead of leaving the board with zero generic digital inputs.

DIAG is electrically the same pin as that socket's endstop header signal
(BTT bridges them with small jumpers) — confirm each used socket's DIAG
jumper is populated before relying on StallGuard.

## Chip variant — confirm before first flash

Unlike the Pro board, the standard Octopus **never ships with the H723
(Cortex-M7) chip** — per Marlin's own board-to-env mapping, "Octopus V1.1"
only ever pairs with F446ZET6, F429ZGT6, or F407ZET6 (all Cortex-M4,
pin-compatible). Still worth a quick check of the chip marking before
flashing, since the three F4 options have different flash/RAM sizes.

This project defaults to **F446ZET6** (`boards/octopus_f446.json`,
`platformio.ini`'s `board = octopus_f446`). If your board turns out to be:
- **F429ZGT6**: clone the board json, change `mcu` to `stm32f429zgt6`,
  `extra_flags` to add `-DSTM32F429xx`, `maximum_size` to `1048576`.
- **F407ZET6**: clone the board json, change `mcu` to `stm32f407zet6`,
  `extra_flags` to add `-DSTM32F407xx`, `maximum_size` to `524288` (same
  512KB as F446, but confirm against the specific part).

No pin changes needed for any of the three — they share the same package
and this firmware's pin table is independent of which one is fitted.

## Build & flash

```sh
pio run -e octopus
```

**No SWD header is broken out on the Octopus Pro** (confirmed via a
stm32duino core maintainer's own forum report on that board) — the
standard board shares the same PCB family, so the same is assumed true
here, but this wasn't independently confirmed for the non-Pro board. Plan
around flashing via DFU or BTT's SD-card bootloader, not a debug probe,
and verify this assumption against the real board/schematic.

- **SD-card (recommended, non-destructive)**: copy the built `.bin` onto
  the board's SD card, named so BTT's own bootloader picks it up (typically
  `firmware.bin`; it renames it to `FIRMWARE.CUR` after flashing on the
  next boot). Does not disturb that bootloader.
- **DFU over USB-C** (`pio run -t upload`, as configured in
  `platformio.ini`): enter the STM32 ROM DFU bootloader (BOOT0 jumper,
  reported at header "J75" on this board) and flash directly.
  **This overwrites BTT's own SD-card bootloader** — after a raw DFU flash,
  SD-card-based updates stop working until that bootloader `.hex` is
  reflashed. Prefer the SD-card method unless you specifically need DFU.

## Known limitations — not yet verified on real hardware

Nothing below has been bench-tested; this firmware was written from the
RP2040 original's exact logic plus Marlin's/Klipper's published pin tables
for this board family, following the same "write it carefully, flag what's
unverified" approach as this repo's ESP32 BLE servo bridge firmware. Note:
the two community forum reports below were specifically about the
**Octopus Pro**, not the standard board this firmware now targets — likely
still relevant (same PCB family, same STM32duino core) but not confirmed
against the standard board itself.

- **USB-CDC (`Serial`) enumeration**: a PlatformIO community report found
  `Serial` not reliably enumerating on the Octopus Pro with some
  `ststm32`/variant combinations when using the Marlin-derived custom
  variant folder. This firmware uses the stock chip-package variant
  (not Marlin's), which should avoid that specific issue, but this is the
  single most important thing to verify first — before porting/trusting
  anything else, flash a minimal sketch and confirm `Serial` actually shows
  up as a COM/tty device.
- **Driver-socket pin buffering**: a stm32duino forum report on the
  Octopus Pro mentioned some motor-socket header pins routing through
  74HCT125 tri-state bus-buffer ICs; Marlin/Klipper drive them fine with
  plain GPIO writes, so this is expected to be transparent, but verify
  STEP/DIR/EN toggling with a meter or logic analyzer before trusting a
  move command.
- **Software-bit-banged TMC UART timing**: run at a conservative 100000
  baud (10us/bit, see `TMC_UART.h`) specifically to leave margin for
  Arduino `digitalRead`/`digitalWrite` overhead, but the actual achievable
  bit timing on real silicon hasn't been measured. If register reads/writes
  are unreliable, this is the first place to look — either tune the bit
  period, or fall back to a hardware USART in single-wire/half-duplex mode
  on whichever of these pins turns out to map to a real UART TX line.
- **Step pulse width**: `Stepper.cpp`'s `step_pulse_delay()` uses
  `delayMicroseconds(1)`, a conservative stand-in for the RP2040 original's
  cycle-counted ~200ns pulse (TMC2209's minimum is ~100ns). Safe but
  untuned; revisit if higher step rates are needed than bench-testing shows
  this comfortably achieves.
- **Timer ISR jitter**: the two `HardwareTimer` interrupts (10kHz step
  tick, 1kHz motion update) are a different mechanism from the RP2040
  original's self-rescheduling hardware alarms (see `main.cpp`'s banner
  comment) — functionally equivalent in design, but real-world jitter
  under load (e.g. while a TMC UART transaction holds interrupts disabled
  for up to ~1 byte-time, bounded to roughly 100us) hasn't been measured.
- **`REBOOT_BOOTLOADER` semantics changed**: the RP2040 original jumps into
  a persistent UF2 mass-storage bootloader mode. This board has no
  equivalent; `CMDH_reboot_bootloader` just does a plain `NVIC_SystemReset()`,
  relying on BTT's bootloader re-checking the SD card on every boot. If the
  host tooling (e.g. a future flash script) expects the UF2-style "board
  disappears and reappears as a mass storage device" behavior, it will need
  updating — this board doesn't do that.
- **Chute magnet-ring encoder decode**: the quadrature decode table, the
  index-gap detection threshold, and the whole notion of "count" in
  `ChuteEncoder.*` are nominal, not calibrated against a real magnet ring +
  DRV5013 pair (see the detailed caveat in `ChuteEncoder.h`). Bench-test
  with the actual ring before trusting direction sign or the index-gap
  threshold.
- **`SET_DIAG_PIN`'s single-byte payload**: inherited from the RP2040
  wire protocol, where GPIO numbers are small ints. Whether an arbitrary
  STM32 `PinName` value always fits in a signed byte hasn't been checked —
  low-risk here since this board's DIAG pins are fixed per socket anyway
  (see `main.cpp`'s comment on this handler).
- **Chip variant**: see the dedicated section above.
