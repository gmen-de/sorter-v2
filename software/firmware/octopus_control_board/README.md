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

**New: `STEPPER_DRV.READ_TEMPERATURE`** (table 2, slot 3, opcode `0x23`,
Octopus only; other boards NACK it): the driver's heatsink temperature from
the NTC (100k, B3950) in that stepper channel's thermistor input, as `int16`
tenths of a degree C. `-32768` means nothing is plugged in, `32767` a shorted
input. The backend's `driver_temp_control.py` polls it for the Octopus
diagnostic page, the driver fan, and the over-temperature pause.

Each thermistor input follows its driver's motor socket, in socket order:

| Motor socket | Stepper channel | Thermistor input | Pin |
|---|---|---|---|
| Motor3 | ch3 carousel | TB | PF3 |
| Motor4 | ch4 c_channel_2_rotor | TH0 | PF4 |
| Motor5 | ch0 chute_stepper | TH1 | PF5 |
| Motor6 | ch1 c_channel_1_rotor | TH2 | PF6 |
| Motor7 | ch2 c_channel_3_rotor | TH3 | PF7 |

## Pin mapping

5 stepper channels on Motor3-Motor7 sockets (pin names from Marlin's/
Klipper's own open-source pin files for this board — BTT publishes no bare
pinout doc). Deliberately skips Motor2 (the dual-output MOTOR2_1/MOTOR2_2
socket sharing one driver) and frees up Motor0/Motor1 for the chute encoder
below:

| Channel | Socket | STEP | DIR | EN | UART (single-wire) | DIAG/endstop-shared |
|---|---|---|---|---|---|---|
| chute_stepper | Motor5 | PC13 | PF0 | PF1 | PE4 | PG13 |
| c_channel_1_rotor | Motor6 | PE2 | PE3 | PD4 | PE1 | PG14 |
| c_channel_3_rotor | Motor7 | PE6 | PA14 | PE0 | PD3 | PG15 |
| carousel | Motor3 | PG4 | PC1 | PA0 | PC7 | PG11 |
| c_channel_2_rotor | Motor4 | PF9 | PF10 | PG2 | PF2 | PG12 |

Chute magnet-ring Hall sensors (2× DRV5013), on Motor0's and Motor1's
now-unused endstop-shared pins: **PG6** (Motor0), **PG9** (Motor1).

LED PWM outputs, repurposed from hotend heater MOSFETs **HE0 → PA2**,
**HE1 → PA3** and **HE2 → PB10** (three independent lighting zones; HE3/PB11
is unused/available for a future zone). PA2/PA3 run on TIM5, not their
default TIM2: PA2 and PB10 would otherwise share TIM2_CH3 and HE2 stay dark.

No spare digital input is currently declared — Motor2 (unused) is the only
socket left over, and its endstop pin hasn't been identified.

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

**Confirmed booting and enumerating as a real USB-CDC device on actual
hardware (2026-11-14)** — `COM9`, `VID_0483&PID_5740`, Windows. Getting
there from "compiles but never enumerates" needed three fixes, all
already applied:
- **The actual root cause: `-DUSBCON -DUSBD_USE_CDC` were never set.**
  Arduino_Core_STM32 only maps the `Serial` object to real USB-CDC
  (`SerialUSB`) when *both* flags are defined
  (`cores/arduino/WSerial.h`); without them, `Serial.begin()` silently
  initializes a plain hardware UART instead and the USB device
  peripheral is never touched at all — compiles fine, links fine, zero
  USB activity on the bus, not even an unrecognized-device entry.
  Confirmed by the firmware's flash usage jumping 36KB -> 45KB once the
  USB device stack was actually linked in. Now set in
  `octopus_f446.json`'s `extra_flags`, along with `USBD_VID=0x0483`/
  `USBD_PID=0x5740` (ST's own VID, the framework's default PID) and
  `HAL_PCD_MODULE_ENABLED`.
- **Clock source was HSI, not HSE.** `boards/variants/octopus_f446/
  generic_clock.c` (cloned from the upstream generic/Nucleo variant)
  configured `SystemClock_Config()` off the internal 16MHz HSI
  oscillator. This board's F446 variant actually has a real 12MHz HSE
  crystal (confirmed: BTT's own FAQ says F446 Octopus takes 12MHz, vs.
  8MHz on the F429/F407 variants). HSI's ~1% tolerance isn't accurate
  enough for USB full-speed's required 48MHz clock (needs ~0.25%) — on
  its own this wouldn't necessarily block enumeration outright, but it's
  a real correctness bug fixed alongside the USBCON one. Switched to
  HSE with PLLM=6/PLLSAIM=6/PLLSAIN=96 recomputed for the real 12MHz
  input (SYSCLK still 180MHz, CLK48 still exactly 48MHz — see the file's
  own comments for the arithmetic). PLLSAI's M divider is confirmed
  independent of the main PLL's on F446 specifically (verified in the
  HAL source, `stm32f4xx_hal_rcc_ex.c`'s `#if defined(STM32F446xx)`
  branch), so this didn't need to match the main PLLM.
- **`HSE_VALUE` was never defined**, so it silently defaulted to 25MHz
  (`stm32f4xx_ll_rcc.h`'s `#if !defined(HSE_VALUE)` fallback) regardless
  of which oscillator was actually selected — this would have left
  `HAL_InitTick()`/`millis()`/`micros()` systematically off even after
  fixing the oscillator source. Now `-DHSE_VALUE=12000000` in
  `extra_flags`, matching the real crystal.

Diagnosed on real hardware by disassembling the linked `firmware.elf`
(`arm-none-eabi-objdump`) to confirm a failed `HAL_RCC_OscConfig()` would
hang silently forever (`Error_Handler()` gets inlined to a bare `b.n`
self-branch, no LED, no symptom) — then flashing a temporary one-line
diagnostic build that blinks PA13 (Marlin's `LED_PIN` for this board) as
the very first thing in `setup()`, to confirm clock init was actually
succeeding before looking elsewhere. That ruled out the clock hanging
and pointed at the `Serial`/USB wiring instead.

Earlier note, now superseded by the above —
**confirmed compiling and linking cleanly (2026-11-06)** against
`platform = ststm32@20.0.0` / `framework-arduinoststm32@3.0.0` (RAM 2.4%,
Flash 6.9% of a 512KB/128KB F446ZE). Getting there needed two non-obvious
fixes, both already applied in this project and worth knowing about if you
ever touch the board/platform config:
- A custom variant folder (`boards/variants/octopus_f446/`) trimmed from
  the upstream framework's shared `STM32F4xx/F446Z(C-E)(H-J-T)` variant,
  containing only the generic-board files (that shared folder also has a
  NUCLEO-specific variant file).
- `-DARDUINO_GENERIC_F446ZETX` in `boards/octopus_f446.json`'s
  `extra_flags` — the generic variant's `digitalPin[]`/`analogInputPin[]`
  tables and `SystemClock_Config()` are gated behind
  `#if defined(ARDUINO_GENERIC_F446Z...)` macros tied to upstream "Generic
  Fxxx" board IDs, which a custom board like this one never defines on its
  own; without it those symbols silently don't exist and the final link
  fails with "undefined reference" errors that look unrelated to the real
  cause.

This only proves the C++ compiles and links — it says nothing about
whether it runs correctly on real silicon. See "Known limitations" below.

**No SWD header is broken out on the Octopus Pro** (confirmed via a
stm32duino core maintainer's own forum report on that board) — the
standard board shares the same PCB family, so the same is assumed true
here, but this wasn't independently confirmed for the non-Pro board. Plan
around flashing via DFU or BTT's SD-card bootloader, not a debug probe,
and verify this assumption against the real board/schematic.

**DFU over USB-C (the only supported method — `pio run -t upload`)**:
enter the STM32 ROM DFU bootloader (BOOT0 jumper, reported at header "J75"
on this board, plus a reset-button press — a DFU "leave" after flashing is
a software jump to the app, not a hardware reset, so BOOT0 is only
re-sampled on an actual reset/power-on), then flash directly. Needs the
WinUSB driver bound to the `0483:df11` DFU device via
[Zadig](https://zadig.akeo.ie/) on Windows (the default Windows driver
gives `LIBUSB_ERROR_NOT_SUPPORTED`). The app is built to run starting at
`0x08000000` (`build.flash_offset`/`upload.offset_address` both `0x0`/
`0x08000000` in `octopus_f446.json`) — DFU writes it directly there, no
bootloader involved, so a plain power cycle (BOOT0 removed) boots straight
into the app every time. **SD-card flashing is no longer supported**: see
the bootloader-destruction bug below for why, and don't try to revive it
by moving `flash_offset` back to `0x8000` without first reflashing BTT's
own bootloader `.hex` (not available from BTT as a separate public
download as far as this project found).

- **Critical bug #1, found and fixed 2026-11-14 — DFU was flashing 0x8000
  bytes too low, hanging on every single DFU flash regardless of firmware
  content.** At the time, `build.flash_offset: "0x8000"` in
  `octopus_f446.json` reached the *linker* correctly (via
  `LD_FLASH_OFFSET`, confirmed on the ELF: `.isr_vector` at `0x08008000`,
  entry point `0x0801094d` — code was built assuming it runs starting at
  `0x08008000`, leaving the low 32KB for BTT's own SD-card bootloader).
  But the ststm32 platform's DFU upload step (`builder/main.py`) reads a
  *different*, unrelated key, `upload.offset_address`, which this
  board.json never set — so it silently defaulted to `0x08000000`. Every
  DFU flash wrote the image starting 0x8000 bytes below where its own
  vector table's absolute addresses assumed it would be, so the reset
  vector pointed to flash that, after the shift, was empty/erased — a
  guaranteed silent hang (no LED, no USB, nothing) independent of what the
  firmware actually did. This cost an entire debugging session that
  (wrongly, in hindsight) suspected `ChuteEncoder.cpp`/linker placement of
  a C++ static, because DFU-flashed builds never worked no matter what was
  bisected away, while SD-card-flashed builds (which go through BTT's own
  bootloader, writing to the correct address itself) worked fine. Fixed at
  the time by adding `"offset_address": "0x08008000"` to
  `octopus_f446.json`'s `upload` section, matching Marlin's own
  `ini/stm32f4.ini` pairing of `board_build.offset = 0x8000` with
  `board_upload.offset_address = 0x08008000` for this board family.
- **Critical bug #2, found and fixed the same day — bug #1 had already
  destroyed BTT's own bootloader, so fixing the offset alone wasn't
  enough.** Every DFU flash made *before* bug #1 was fixed wrote (with the
  wrong, defaulted `0x08000000` offset) directly into the first 32KB of
  flash — exactly where BTT's factory SD-card bootloader lives. That
  erase+write happened regardless of whether the resulting app ran
  correctly, so by the time bug #1 was fixed, BTT's bootloader was already
  gone. Symptom: DFU flashing worked fine (dfu-util's "leave" is a
  software jump straight into the app, bypassing the bootloader
  entirely — confirmed via `pio run -t upload`, COM9 enumerating right
  after), but a genuine power cycle went completely silent (no DFU device,
  no COM9, nothing on the bus at all) — because Main Flash boot
  (BOOT0 low) fetches the vector table from `0x08000000`, which now had no
  valid bootloader left to jump onward to the app at `0x08008000`. Since
  this project already committed to DFU-only (see above) and was never
  going to rely on BTT's bootloader again, the fix was to stop reserving
  the first 32KB at all: `build.flash_offset` and `upload.offset_address`
  both moved to `0x0`/`0x08000000`, so the app's own vector table now sits
  at the chip's native boot address and every power cycle boots directly
  into it with no intermediary. Confirmed via `arm-none-eabi-objdump -h`
  (`.isr_vector` LMA back at `0x08000000`) and, on real hardware, COM9
  enumerating after a full cold power cycle (BOOT0 jumper removed) — not
  just after a DFU "leave".

## Known limitations — not yet verified on real hardware

USB-CDC enumeration and clock init are now confirmed working on real
hardware (see above) — everything below this point is still unverified;
this firmware was written from the RP2040 original's exact logic plus
Marlin's/Klipper's published pin tables for this board family, following
the same "write it carefully, flag what's unverified" approach as this
repo's ESP32 BLE servo bridge firmware. Note: the community forum report
below was specifically about the **Octopus Pro**, not the standard board
this firmware now targets — likely still relevant (same PCB family, same
STM32duino core) but not confirmed against the standard board itself.
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
  equivalent; `CMDH_reboot_bootloader` just does a plain
  `NVIC_SystemReset()`, which (now that the app lives at `0x08000000`, see
  the flashing section above) boots straight back into this same app, not
  into any bootloader or "waiting for a new file" mode. If the host
  tooling (e.g. a future flash script) expects the UF2-style "board
  disappears and reappears as a mass storage device" behavior, it will need
  updating — this board doesn't do that; a new firmware image always needs
  a manual DFU session (BOOT0 + reset).
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
