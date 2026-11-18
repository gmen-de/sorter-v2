/*
 * Octopus Control Board Firmware
 * Copyright (C) 2026
 *
 * Arduino/PlatformIO port of software/firmware/sorter_interface_firmware/
 * sorter_interface_firmware.cpp (RP2040 pico-sdk) onto a BIGTREETECH Octopus
 * Pro (STM32, single core). Reproduces the same host-facing wire protocol
 * (COBS+CRC32 framed commands over USB-CDC) so the Python backend
 * (software/sorter/backend/hardware/sorter_interface.py, hardware/bus.py)
 * needs zero changes. See the firmware README for the full protocol spec,
 * the new table-5 chute-encoder addition, and known unverified-on-hardware
 * caveats.
 *
 * Differences from the RP2040 original, all covered in the README:
 *  - Single core: the RP2040's core1 (step generation + motion update,
 *    driven by self-rescheduling hardware alarms) is replaced by two
 *    STM32duino HardwareTimer-driven periodic interrupts at the same rates
 *    (10kHz step tick, 1kHz motion update).
 *  - TMC2209 UART: each motor socket is a single dedicated GPIO (true
 *    single-wire half-duplex), not a shared 2-wire bus — see TMC_UART.*.
 *  - Servo/PCA9685 (command table 4) is dropped entirely: out of scope,
 *    migrated separately to the ESP32 BLE bridge.
 *  - New command table 5 (CHUTE_ENCODER): the chute's magnet-ring position
 *    sensing, which has no RP2040-board equivalent. See ChuteEncoder.*.
 */

#include <Arduino.h>
#include <HardwareTimer.h>
#include <array>
#include <cmath>
#include <utility>

#include "ChuteEncoder.h"
#include "Stepper.h"
#include "TMC2209.h"
#include "TMC_UART.h"
#include "message.h"

#ifndef INIT_DEVICE_NAME
#define INIT_DEVICE_NAME "DISTRIBUTION MB"
#endif
#ifndef INIT_DEVICE_ADDRESS
#define INIT_DEVICE_ADDRESS 0x00
#endif
#ifndef FIRMWARE_GIT_VERSION
#define FIRMWARE_GIT_VERSION "unknown"
#endif
#ifndef FIRMWARE_GIT_COMMIT
#define FIRMWARE_GIT_COMMIT "unknown"
#endif
#ifndef FIRMWARE_BUILD_TIME_UTC
#define FIRMWARE_BUILD_TIME_UTC "unknown"
#endif
#ifndef FIRMWARE_VARIANT
#define FIRMWARE_VARIANT "octopus by g-men"
#endif

char DEVICE_NAME[16] = INIT_DEVICE_NAME;
uint8_t DEVICE_ADDRESS = INIT_DEVICE_ADDRESS;

#include "hwcfg_octopus.h"

// ---------------------------------------------------------------------------
// Hardware objects
// ---------------------------------------------------------------------------

template <size_t... I>
static std::array<TMC_UART_Bus, STEPPER_COUNT> make_tmc_bus_array(std::index_sequence<I...>) {
    return {TMC_UART_Bus(TMC_UART_PINS[I])...};
}
static auto tmc_buses = make_tmc_bus_array(std::make_index_sequence<STEPPER_COUNT>{});

template <size_t... I>
static std::array<TMC2209, STEPPER_COUNT> make_tmc_array(std::index_sequence<I...>) {
    return {TMC2209(&tmc_buses[I], TMC_UART_ADDRESSES[I])...};
}
static auto tmc_drivers = make_tmc_array(std::make_index_sequence<STEPPER_COUNT>{});

template <size_t... I>
static std::array<Stepper, STEPPER_COUNT> make_stepper_array(std::index_sequence<I...>) {
    return {Stepper((int)STEPPER_STEP_PINS[I], (int)STEPPER_DIR_PINS[I])...};
}
static auto steppers = make_stepper_array(std::make_index_sequence<STEPPER_COUNT>{});

static ChuteEncoder chute_encoder(CHUTE_ENCODER_PIN_A, CHUTE_ENCODER_PIN_B);

// Tracks whether each stepper's hardware nEN pin has been pulled low.
static bool stepper_hw_enabled[STEPPER_COUNT] = {};
// Tracks whether each stepper's TMC chopper (CHOPCONF.TOFF) is on — see the
// RP2040 original's comment on ensure_stepper_hw_enabled() for why this is a
// separate gate from the nEN pin.
static bool stepper_drv_current_on[STEPPER_COUNT];
// Each channel's DIAG GPIO as the firmware uses it: the board's
// STEPPER_DIAG_PINS, or what SET_DIAG_PIN set since the last INIT.
static int stepper_diag_pins[STEPPER_COUNT];

static void init_diag_pin(int pin) {
    if (pin < 0) return;
    pinMode(pin, INPUT_PULLDOWN); // TMC2209 drives DIAG high on stall
}

static void ensure_stepper_hw_enabled(int i) {
    if (!stepper_hw_enabled[i]) {
        digitalWrite(STEPPER_nEN_PINS[i], LOW);
        stepper_hw_enabled[i] = true;
    }
    if (!stepper_drv_current_on[i]) {
        tmc_drivers[i].enableDriver(true);
        stepper_drv_current_on[i] = true;
    }
}

// ---------------------------------------------------------------------------
// Chute auto-home: on every (re-)init, drive the chute to the magnet ring's
// index gap on its own, with no host involvement needed - the host only ever
// needs to send absolute-angle moves afterward. Mirrors
// subsystems/distribution/chute.py's _homeEncoder() (same speed, same
// "stop fully, then zero" two-phase sequence), just running autonomously on
// the ISR-driven motion timers instead of a host-side poll loop.
// ---------------------------------------------------------------------------

static const int CHUTE_STEPPER_CHANNEL = 0; // STEPPER_NAMES[0] == "chute_stepper", see hwcfg_octopus.h
static const int32_t CHUTE_AUTO_HOME_SPEED = -1000; // matches chute.py's HOME_SPEED_MICROSTEPS_PER_SEC
static const uint8_t CHUTE_AUTO_HOME_IRUN = 16, CHUTE_AUTO_HOME_IHOLD = 4, CHUTE_AUTO_HOME_IHOLD_DELAY = 8;
static const uint32_t CHUTE_AUTO_HOME_TIMEOUT_MS = 15000; // matches chute.py's HOME_TIMEOUT_MS
// The encoder's index gap is only an electrical reference, not the
// mechanically "correct" zero. Re-derived 2026-11-15 against the physical
// 0 deg marker (yellow tape on the chute plate, true zero = that mark at
// 12 o'clock, level with the motor) via webcam self-check after driving
// the closed loop to the visually-confirmed-correct count - supersedes
// the older ~9 deg/197-usteps figure, which was referenced to a different
// physical mark (a drilled bolt hole) and never matched this tape mark.
static const int32_t CHUTE_HOME_OFFSET_USTEPS = 215; // 198 + 17 usteps forward, fine-tuned against the UI's live position readout 2026-11-16

static bool chute_auto_homing_active = false;  // still seeking the index gap
static bool chute_auto_homing_stopping = false; // index seen, braking to a full stop
static bool chute_auto_homing_correcting = false; // stopped at the gap, moving the fixed offset to the real zero
static bool chute_auto_homed = false;
static bool chute_auto_homing_failed = false;
static uint32_t chute_auto_home_start_ms = 0;

static void start_chute_auto_home() {
    chute_auto_homed = false;
    chute_auto_homing_failed = false;
    chute_auto_homing_stopping = false;
    chute_auto_homing_correcting = false;
    chute_encoder.clearIndexSynced();
    chute_encoder.resetIntervalTracking();
    tmc_drivers[CHUTE_STEPPER_CHANNEL].setCurrent(CHUTE_AUTO_HOME_IRUN, CHUTE_AUTO_HOME_IHOLD, CHUTE_AUTO_HOME_IHOLD_DELAY);
    ensure_stepper_hw_enabled(CHUTE_STEPPER_CHANNEL);
    steppers[CHUTE_STEPPER_CHANNEL].moveAtSpeed(CHUTE_AUTO_HOME_SPEED);
    chute_auto_home_start_ms = millis();
    chute_auto_homing_active = true;
}

static void update_chute_auto_home() {
    if (chute_auto_homing_correcting) {
        if (steppers[CHUTE_STEPPER_CHANNEL].isStopped()) {
            steppers[CHUTE_STEPPER_CHANNEL].setPosition(0);
            chute_auto_homing_correcting = false;
            chute_auto_homed = true;
        }
        return;
    }
    if (chute_auto_homing_stopping) {
        if (steppers[CHUTE_STEPPER_CHANNEL].isStopped()) {
            steppers[CHUTE_STEPPER_CHANNEL].moveSteps(CHUTE_HOME_OFFSET_USTEPS);
            chute_auto_homing_stopping = false;
            chute_auto_homing_correcting = true;
        }
        return;
    }
    if (!chute_auto_homing_active) return;

    if (chute_encoder.isIndexSynced()) {
        steppers[CHUTE_STEPPER_CHANNEL].moveAtSpeed(0); // starts braking; zeroed once isStopped()
        chute_auto_homing_active = false;
        chute_auto_homing_stopping = true;
    } else if (millis() - chute_auto_home_start_ms > CHUTE_AUTO_HOME_TIMEOUT_MS) {
        steppers[CHUTE_STEPPER_CHANNEL].moveAtSpeed(0);
        chute_auto_homing_active = false;
        chute_auto_homing_failed = true;
    }
}

// ---------------------------------------------------------------------------
// Chute closed-loop move: drives to a target magnet-ring encoder count
// (not a raw microstep target) and verifies/corrects against the real
// encoder reading afterward, so the final resting position does not depend
// on gear backlash or lost steps - only on the encoder's own ~2 deg
// (1-count) resolution. Always takes the shortest way round (<=180 deg);
// the open-loop move runs at whatever speed/current the host already
// configured (no artificial slowdown), with bounded small corrections
// after it lands.
// ---------------------------------------------------------------------------

// Re-derived 2026-11-15/16 from a linear regression over 7 hand-positioned
// reference points (0/30/90/150/210/270/330 deg, aligned to the fixed hex
// arms) checked against the real encoder count: slope implies 179.4
// counts/rev (residuals < 1 count), rounding cleanly to 180 - not the
// 176 (44 magnets x 4) assumed from the original design doc. Likely 45
// magnets, not 44; CHUTE_HOME_OFFSET_USTEPS is unaffected (it's defined
// by the physical index-stop + a fixed microstep move, not by this
// constant) but every move's target-count-from-degrees math depends on it.
static const int32_t CHUTE_COUNTS_PER_REV = 180;
static const int32_t CHUTE_USTEPS_PER_REV = 7878;   // bench-calibrated 2026-11-14 (5-revolution average)
static const int CHUTE_MOVE_MAX_CORRECTIONS = 8;     // bounded retries before giving up

static int32_t chute_move_target_count = 0;
static int chute_move_corrections = 0;
static bool chute_move_active = false;
static bool chute_move_failed = false;
static bool chute_move_settling = false;
static uint32_t chute_move_settle_until_ms = 0;
static const uint32_t CHUTE_MOVE_SETTLE_MS = 15; // let a borderline Hall edge finish switching before trusting the count

// Signed count delta, wrapped into (-CHUTE_COUNTS_PER_REV/2, +CHUTE_COUNTS_PER_REV/2]
// - the shortest way around the ring, so no move ever needs more than half a
// revolution (<=180 deg).
static int32_t chute_shortest_count_delta(int32_t raw_delta) {
    int32_t m = raw_delta % CHUTE_COUNTS_PER_REV;
    if (m < 0) m += CHUTE_COUNTS_PER_REV;
    if (m > CHUTE_COUNTS_PER_REV / 2) m -= CHUTE_COUNTS_PER_REV;
    return m;
}

static int32_t chute_usteps_for_count_delta(int32_t delta_counts) {
    // Bench-confirmed 2026-11-14: positive microsteps correlate with a
    // DEcreasing encoder count, not increasing - negate here, once, so
    // every caller can keep thinking in plain "more counts = more usteps".
    return -(int32_t)lround((double)delta_counts * CHUTE_USTEPS_PER_REV / (double)CHUTE_COUNTS_PER_REV);
}

static void start_chute_move_to_count(int32_t target_count) {
    chute_move_target_count = target_count;
    chute_move_corrections = 0;
    chute_move_failed = false;
    chute_move_settling = false;
    ensure_stepper_hw_enabled(CHUTE_STEPPER_CHANNEL);

    int32_t delta = chute_shortest_count_delta(target_count - chute_encoder.getCount());
    int32_t usteps = chute_usteps_for_count_delta(delta);
    if (usteps == 0) {
        chute_move_active = false;
        return;
    }
    steppers[CHUTE_STEPPER_CHANNEL].moveSteps(usteps);
    chute_move_active = true;
}

static void update_chute_move() {
    if (!chute_move_active) return;
    if (!steppers[CHUTE_STEPPER_CHANNEL].isStopped()) {
        chute_move_settling = false;
        return;
    }
    if (!chute_move_settling) {
        chute_move_settling = true;
        chute_move_settle_until_ms = millis() + CHUTE_MOVE_SETTLE_MS;
        return;
    }
    if (millis() < chute_move_settle_until_ms) return;
    chute_move_settling = false;

    int32_t delta = chute_shortest_count_delta(chute_move_target_count - chute_encoder.getCount());
    if (delta == 0) {
        chute_move_active = false;
        return;
    }
    if (chute_move_corrections >= CHUTE_MOVE_MAX_CORRECTIONS) {
        chute_move_active = false;
        chute_move_failed = true;
        return;
    }
    chute_move_corrections++;
    int32_t usteps = chute_usteps_for_count_delta(delta);
    if (usteps == 0) {
        // Residual is sub-1-count: as close as the encoder can ever confirm.
        chute_move_active = false;
        return;
    }
    steppers[CHUTE_STEPPER_CHANNEL].moveSteps(usteps);
    // stays active; re-checked once this new move stops
}

// ---------------------------------------------------------------------------
// Chute step-based move: drives the stepper's own absolute position
// (GET_POSITION/SET_POSITION) to a target microstep count, pure open-loop -
// no encoder involved at all. Homing (Hall-sensor index + offset) already
// zeroes this same position counter at the true physical zero, and the
// motor's own step counting has turned out to be far more precise than the
// 180-count/rev magnet-ring encoder's resolution (laser-measured
// 2026-11-16: exactly 7680 usteps/rev, i.e. a clean 1.5 deg - 32 usteps -
// grid). So for ordinary moves this is the more precise path; the encoder
// stays available for homing and periodic drift/step-loss checks, matching
// the sensor's original intended role rather than being the per-move
// position source.
// ---------------------------------------------------------------------------

static const int32_t CHUTE_STEP_USTEPS_PER_REV = 7680; // laser-measured 2026-11-16

static bool chute_step_move_active = false;

static int32_t chute_shortest_usteps_delta(int32_t raw_delta) {
    int32_t m = raw_delta % CHUTE_STEP_USTEPS_PER_REV;
    if (m < 0) m += CHUTE_STEP_USTEPS_PER_REV;
    if (m > CHUTE_STEP_USTEPS_PER_REV / 2) m -= CHUTE_STEP_USTEPS_PER_REV;
    return m;
}

static void start_chute_move_to_step_position(int32_t target_position) {
    ensure_stepper_hw_enabled(CHUTE_STEPPER_CHANNEL);
    int32_t delta = chute_shortest_usteps_delta(target_position - steppers[CHUTE_STEPPER_CHANNEL].getPosition());
    if (delta == 0) {
        chute_step_move_active = false;
        return;
    }
    steppers[CHUTE_STEPPER_CHANNEL].moveSteps(delta);
    chute_step_move_active = true;
}

static void update_chute_step_move() {
    if (!chute_step_move_active) return;
    if (!steppers[CHUTE_STEPPER_CHANNEL].isStopped()) return;
    chute_step_move_active = false;
}

// ---------------------------------------------------------------------------
// Configuration/observability/version JSON (same shape the backend already
// parses from the RP2040 boards — see sorter_interface.py's detect flow)
// ---------------------------------------------------------------------------

static int append_stepper_names_json(char* buf, size_t buf_size) {
    if (buf_size == 0) return -1;
    int written = snprintf(buf, buf_size, "[");
    if (written < 0 || (size_t)written >= buf_size) return -1;
    for (int i = 0; i < STEPPER_COUNT; i++) {
        int n = snprintf(buf + written, buf_size - written, "%s\"%s\"", i == 0 ? "" : ",", STEPPER_NAMES[i]);
        if (n < 0 || (size_t)(written + n) >= buf_size) return -1;
        written += n;
    }
    int n = snprintf(buf + written, buf_size - written, "]");
    if (n < 0 || (size_t)(written + n) >= buf_size) return -1;
    return written + n;
}

static int append_stepper_diag_pins_json(char* buf, size_t buf_size) {
    if (buf_size == 0) return -1;
    int written = snprintf(buf, buf_size, "[");
    if (written < 0 || (size_t)written >= buf_size) return -1;
    for (int i = 0; i < STEPPER_COUNT; i++) {
        int n = snprintf(buf + written, buf_size - written, "%s%d", i == 0 ? "" : ",", stepper_diag_pins[i]);
        if (n < 0 || (size_t)(written + n) >= buf_size) return -1;
        written += n;
    }
    int n = snprintf(buf + written, buf_size - written, "]");
    if (n < 0 || (size_t)(written + n) >= buf_size) return -1;
    return written + n;
}

int dump_observability(char* buf, size_t buf_size) {
    if (buf_size == 0) return 0;
    char diag_pins_buf[128];
    int diag_pins_len = append_stepper_diag_pins_json(diag_pins_buf, sizeof(diag_pins_buf));

    char led_gpios_buf[64];
    int led_len = snprintf(led_gpios_buf, sizeof(led_gpios_buf), "[");
    for (int i = 0; i < LED_OUTPUT_COUNT && led_len > 0; i++) {
        led_len += snprintf(led_gpios_buf + led_len, sizeof(led_gpios_buf) - led_len, "%s%u", i == 0 ? "" : ",",
                             (unsigned)digital_output_pins[i]);
    }
    snprintf(led_gpios_buf + led_len, sizeof(led_gpios_buf) - led_len, "]");

    // Field names kept short deliberately: MAX_PAYLOAD_SIZE is 246 bytes and
    // this string was silently overflowing it (always falling back to "{}",
    // see the n_bytes check below) once chute_step_move_active was added -
    // bench-confirmed 2026-11-16, not a motion/ISR timing issue.
    int n_bytes = snprintf(buf, buf_size,
                            "{\"hw\":\"%s\",\"diag_pins\":%s,\"led_gpios\":%s,"
                            "\"chute_homed\":%s,\"chute_homing_active\":%s,\"chute_homing_failed\":%s,"
                            "\"chute_move_active\":%s,\"chute_move_failed\":%s,\"chute_step_active\":%s}",
                            HW_ID, diag_pins_len > 0 ? diag_pins_buf : "[]", led_gpios_buf,
                            chute_auto_homed ? "true" : "false",
                            (chute_auto_homing_active || chute_auto_homing_stopping || chute_auto_homing_correcting) ? "true" : "false",
                            chute_auto_homing_failed ? "true" : "false",
                            chute_move_active ? "true" : "false",
                            chute_move_failed ? "true" : "false",
                            chute_step_move_active ? "true" : "false");
    if (n_bytes >= 0 && (size_t)n_bytes < buf_size) return n_bytes;
    if (buf_size >= 3) {
        buf[0] = '{'; buf[1] = '}'; buf[2] = '\0';
        return 2;
    }
    buf[0] = '\0';
    return 0;
}

int dump_configuration(char* buf, size_t buf_size) {
    if (buf_size == 0) return 0;
    char names_buf[256];
    int names_len = append_stepper_names_json(names_buf, sizeof(names_buf));

    if (names_len > 0) {
        int n_bytes = snprintf(buf, buf_size,
                                "{\"device_name\":\"%s\",\"stepper_count\":%d,"
                                "\"stepper_names\":%s,"
                                "\"digital_input_count\":%d,\"digital_output_count\":%d,\"servo_count\":0}",
                                DEVICE_NAME, STEPPER_COUNT, names_buf, DIGITAL_INPUT_COUNT, DIGITAL_OUTPUT_COUNT);
        if (n_bytes >= 0 && (size_t)n_bytes < buf_size) return n_bytes;
    }
    int n_bytes = snprintf(buf, buf_size,
                            "{\"device_name\":\"%s\",\"hw\":\"%s\",\"stepper_count\":%d,"
                            "\"digital_input_count\":%d,\"digital_output_count\":%d,\"servo_count\":0}",
                            DEVICE_NAME, HW_ID, STEPPER_COUNT, DIGITAL_INPUT_COUNT, DIGITAL_OUTPUT_COUNT);
    if (n_bytes >= 0 && (size_t)n_bytes < buf_size) return n_bytes;
    if (buf_size >= 3) {
        buf[0] = '{'; buf[1] = '}'; buf[2] = '\0';
        return 2;
    }
    buf[0] = '\0';
    return 0;
}

int dump_version(char* buf, size_t buf_size) {
    if (buf_size == 0) return 0;
    int n_bytes = snprintf(buf, buf_size, "{\"firmware_version\":\"%s\",\"variant\":\"%s\",\"commit\":\"%s\",\"build_time_utc\":\"%s\"}",
                            FIRMWARE_GIT_VERSION, FIRMWARE_VARIANT, FIRMWARE_GIT_COMMIT, FIRMWARE_BUILD_TIME_UTC);
    if (n_bytes >= 0 && (size_t)n_bytes < buf_size) return n_bytes;
    if (buf_size >= 3) {
        buf[0] = '{'; buf[1] = '}'; buf[2] = '\0';
        return 2;
    }
    buf[0] = '\0';
    return 0;
}

// ---------------------------------------------------------------------------
// Hardware init
// ---------------------------------------------------------------------------

void initialize_hardware() {
    for (int i = 0; i < STEPPER_COUNT; i++) {
        tmc_buses[i].setupComm(TMC_UART_BAUDRATE);
    }
    for (int i = 0; i < STEPPER_COUNT; i++) {
        steppers[i].initialize();
        steppers[i].setAcceleration(20000);
        steppers[i].setSpeedLimits(16, 4000);
        tmc_drivers[i].initialize();
        tmc_drivers[i].enableDriver(true);
        stepper_drv_current_on[i] = true;
        tmc_drivers[i].setCurrent(0, 0, 0);
        tmc_drivers[i].setMicrosteps(MICROSTEP_8);
        tmc_drivers[i].enableStealthChop(true);
    }
    // nEN pins start HIGH (disabled) until first move or explicit enable.
    for (int i = 0; i < STEPPER_COUNT; i++) {
        pinMode(STEPPER_nEN_PINS[i], OUTPUT);
        digitalWrite(STEPPER_nEN_PINS[i], HIGH);
        stepper_hw_enabled[i] = false;
    }
    for (int i = 0; i < STEPPER_COUNT; i++) {
        stepper_diag_pins[i] = STEPPER_DIAG_PINS[i];
        init_diag_pin(stepper_diag_pins[i]);
        steppers[i].setStallPin(stepper_diag_pins[i]);
        steppers[i].enableStallDetection(false);
    }
    for (int i = 0; i < DIGITAL_INPUT_COUNT; i++) {
        pinMode(digital_input_pins[i], INPUT_PULLUP);
    }
    // STM32duino's digitalWrite()/analogWrite() both reconfigure the pin's
    // alternate function as needed internally, unlike the RP2040 original's
    // raw pico-sdk GPIO/PWM blocks — no "is this pad currently PWM-routed"
    // bookkeeping is needed here.
    analogWriteResolution(16);
    for (int i = 0; i < DIGITAL_OUTPUT_COUNT; i++) {
        pinMode(digital_output_pins[i], OUTPUT);
        digitalWrite(digital_output_pins[i], LOW);
    }
    if (FAN0_OUTPUT_CHANNEL >= 0 && FAN0_OUTPUT_CHANNEL < DIGITAL_OUTPUT_COUNT) {
        digitalWrite(digital_output_pins[FAN0_OUTPUT_CHANNEL], HIGH);
    }
    // Fans default to full speed at boot (2026-11-16, explicit ask) - still
    // individually overridable afterward via DIGITAL_IO.WRITE/WRITE_PWM.
    for (int i = 0; i < FAN_OUTPUT_COUNT; i++) {
        analogWrite(digital_output_pins[FAN_OUTPUT_FIRST_CHANNEL + i], 65535);
    }
}

// ---------------------------------------------------------------------------
// Command handlers — table 0 (base)
// ---------------------------------------------------------------------------

void CMDH_init(const BusMessage* msg, BusMessage* resp) {
    // Deliberately does NOT home the chute (2026-11-17, safety fix): this
    // runs the instant the backend talks to the board at all, which is not
    // the same thing as a deliberate "home now" — the chute must only ever
    // spin on an explicit CHUTE_ENCODER.START_HOMING (which the backend
    // already sends on its own schedule, see chute.py's home()) or lazily
    // from a move request that no longer trusts its zero (see
    // CMDH_chute_move_to_count/_step_position below). See setup()'s own
    // comment for the incident this guards against.
    (void)msg;
    initialize_hardware();
    resp->payload_length = dump_configuration((char*)resp->payload, MAX_PAYLOAD_SIZE);
}

void CMDH_ping(const BusMessage* msg, BusMessage* resp) {
    memcpy(resp->payload, msg->payload, msg->payload_length);
    resp->payload_length = msg->payload_length;
}

void CMDH_reboot_bootloader(const BusMessage* msg, BusMessage* resp) {
    (void)msg;
    resp->payload_length = 0;
    // No RP2040-UF2-bootloader equivalent on this board: BTT's own SD-card
    // bootloader re-checks the SD card for a new firmware.bin on every power
    // cycle, so a plain system reset is the closest available approximation.
    // See the README flashing section — this does NOT put the board into a
    // special "waiting for a file" mode the way REBOOT_BOOTLOADER did on the
    // RP2040 boards.
    NVIC_SystemReset();
}

void CMDH_get_observability(const BusMessage* msg, BusMessage* resp) {
    (void)msg;
    resp->payload_length = dump_observability((char*)resp->payload, MAX_PAYLOAD_SIZE);
}

void CMDH_get_version(const BusMessage* msg, BusMessage* resp) {
    (void)msg;
    resp->payload_length = dump_version((char*)resp->payload, MAX_PAYLOAD_SIZE);
}

const struct CommandTable baseCmdTable = {
    .prefix = NULL,
    .commands = {{
        {"INIT", "", "s", 0, NULL, CMDH_init},
        {"PING", "", "", 255, NULL, CMDH_ping},
        {"REBOOT_BOOTLOADER", "", "", 0, NULL, CMDH_reboot_bootloader},
        {"GET_OBSERVABILITY", "", "s", 0, NULL, CMDH_get_observability},
        {"GET_VERSION", "", "s", 0, NULL, CMDH_get_version},
    }}};

// ---------------------------------------------------------------------------
// Command handlers — table 1 (STEPPER) / table 2 (STEPPER_DRV)
// ---------------------------------------------------------------------------

bool VAL_stepper_channel(uint8_t channel) { return channel < STEPPER_COUNT; }

void CMDH_stepper_move_steps(const BusMessage* msg, BusMessage* resp) {
    int32_t distance;
    memcpy(&distance, msg->payload, sizeof(distance));
    ensure_stepper_hw_enabled(msg->channel);
    bool result = steppers[msg->channel].moveSteps(distance);
    resp->payload[0] = result ? 1 : 0;
    resp->payload_length = 1;
}

void CMDH_stepper_move_at_speed(const BusMessage* msg, BusMessage* resp) {
    int32_t speed;
    memcpy(&speed, msg->payload, sizeof(speed));
    ensure_stepper_hw_enabled(msg->channel);
    bool result = steppers[msg->channel].moveAtSpeed(speed);
    resp->payload[0] = result ? 1 : 0;
    resp->payload_length = 1;
}

void CMDH_stepper_set_speed_limits(const BusMessage* msg, BusMessage* resp) {
    uint32_t min_speed, max_speed;
    memcpy(&min_speed, msg->payload, sizeof(min_speed));
    memcpy(&max_speed, msg->payload + sizeof(min_speed), sizeof(max_speed));
    steppers[msg->channel].setSpeedLimits(min_speed, max_speed);
    resp->payload_length = 0;
}

void CMDH_stepper_set_acceleration(const BusMessage* msg, BusMessage* resp) {
    uint32_t acceleration;
    memcpy(&acceleration, msg->payload, sizeof(acceleration));
    steppers[msg->channel].setAcceleration(acceleration);
    resp->payload_length = 0;
}

void CMDH_stepper_is_stopped(const BusMessage* msg, BusMessage* resp) {
    resp->payload[0] = steppers[msg->channel].isStopped() ? 1 : 0;
    resp->payload_length = 1;
}

void CMDH_stepper_get_position(const BusMessage* msg, BusMessage* resp) {
    int32_t position = steppers[msg->channel].getPosition();
    memcpy(resp->payload, &position, sizeof(position));
    resp->payload_length = sizeof(position);
}

void CMDH_stepper_set_position(const BusMessage* msg, BusMessage* resp) {
    int32_t position;
    memcpy(&position, msg->payload, sizeof(position));
    steppers[msg->channel].setPosition(position);
    resp->payload_length = 0;
}

void CMDH_stepper_home(const BusMessage* msg, BusMessage* resp) {
    int32_t home_speed;
    memcpy(&home_speed, msg->payload, sizeof(home_speed));
    uint8_t home_pin_channel = msg->payload[4];
    bool home_pin_polarity = msg->payload[5] != 0;
    if (home_pin_channel >= DIGITAL_INPUT_COUNT) {
        resp->command = msg->command | 0x80;
        resp->payload_length = snprintf((char*)resp->payload, MAX_PAYLOAD_SIZE, "Invalid home pin channel %u", home_pin_channel);
        return;
    }
    int home_pin = digital_input_pins[home_pin_channel];
    ensure_stepper_hw_enabled(msg->channel);
    steppers[msg->channel].home(home_speed, home_pin, home_pin_polarity);
    resp->payload_length = 0;
}

void CMDH_stepper_jitter(const BusMessage* msg, BusMessage* resp) {
    int32_t amplitude, cycles, speed, accel;
    memcpy(&amplitude, msg->payload, sizeof(amplitude));
    memcpy(&cycles, msg->payload + 4, sizeof(cycles));
    memcpy(&speed, msg->payload + 8, sizeof(speed));
    memcpy(&accel, msg->payload + 12, sizeof(accel));
    ensure_stepper_hw_enabled(msg->channel);
    bool result = steppers[msg->channel].jitter(amplitude, cycles, speed, accel);
    resp->payload[0] = result ? 1 : 0;
    resp->payload_length = 1;
}

void CMDH_stepper_is_jittering(const BusMessage* msg, BusMessage* resp) {
    resp->payload[0] = steppers[msg->channel].isJittering() ? 1 : 0;
    resp->payload_length = 1;
}

void CMDH_stepper_enable_stall_detection(const BusMessage* msg, BusMessage* resp) {
    bool enable = msg->payload[0] != 0;
    if (enable && stepper_diag_pins[msg->channel] < 0) {
        resp->command = msg->command | 0x80;
        resp->payload_length = snprintf((char*)resp->payload, MAX_PAYLOAD_SIZE, "No DIAG pin for channel %u", msg->channel);
        return;
    }
    steppers[msg->channel].enableStallDetection(enable);
    resp->payload_length = 0;
}

void CMDH_stepper_get_stall_status(const BusMessage* msg, BusMessage* resp) {
    (void)msg; // Channel is ignored: report every channel on this board at once.
    uint8_t mask = 0;
    for (int i = 0; i < STEPPER_COUNT; i++) {
        if (steppers[i].wasStalled()) mask |= (uint8_t)(1u << i);
    }
    resp->payload[0] = mask;
    resp->payload_length = 1;
}

void CMDH_stepper_clear_stall(const BusMessage* msg, BusMessage* resp) {
    steppers[msg->channel].clearStall();
    resp->payload_length = 0;
}

// A DIAG wire may not land on a pin the board drives or reads for anything
// else. Note: unlike the RP2040 original there is no meaningful numeric
// upper bound to check (STM32 PinName values aren't small sequential ints),
// so this only checks for collisions, not a pin-number range.
static bool gpio_in_use(int pin) {
    for (int i = 0; i < STEPPER_COUNT; i++) {
        if (pin == (int)STEPPER_STEP_PINS[i] || pin == (int)STEPPER_DIR_PINS[i] || pin == (int)STEPPER_nEN_PINS[i]) return true;
        if (pin == (int)TMC_UART_PINS[i]) return true;
    }
    for (int i = 0; i < DIGITAL_INPUT_COUNT; i++) {
        if (pin == (int)digital_input_pins[i]) return true;
    }
    for (int i = 0; i < DIGITAL_OUTPUT_COUNT; i++) {
        if (pin == (int)digital_output_pins[i]) return true;
    }
    return pin == (int)CHUTE_ENCODER_PIN_A || pin == (int)CHUTE_ENCODER_PIN_B;
}

void CMDH_stepper_set_diag_pin(const BusMessage* msg, BusMessage* resp) {
    // Payload is a signed byte (wire-protocol legacy from the RP2040 boards,
    // where GPIO numbers are small ints). On this board DIAG is fixed per
    // socket via BTT's own jumpers (see STEPPER_DIAG_PINS in hwcfg), so this
    // command has little practical use here — and if a future need for it
    // arises, verify the target PinName value actually fits an int8_t on
    // whatever Arduino_Core_STM32 version is in use before relying on it.
    int pin = (int8_t)msg->payload[0];
    if (pin != -1 && (pin < 0 || gpio_in_use(pin))) {
        resp->command = msg->command | 0x80;
        resp->payload_length = snprintf((char*)resp->payload, MAX_PAYLOAD_SIZE, "GPIO %d is not free for a DIAG wire", pin);
        return;
    }
    steppers[msg->channel].enableStallDetection(false);
    init_diag_pin(pin);
    stepper_diag_pins[msg->channel] = pin;
    steppers[msg->channel].setStallPin(pin);
    resp->payload_length = 0;
}

const struct CommandTable stepperCmdTable = {
    .prefix = "STEPPER",
    .commands = {{
        {"MOVE_STEPS", "i", "?", 4, VAL_stepper_channel, CMDH_stepper_move_steps},
        {"MOVE_AT_SPEED", "i", "?", 4, VAL_stepper_channel, CMDH_stepper_move_at_speed},
        {"SET_SPEED_LIMITS", "II", "", 8, VAL_stepper_channel, CMDH_stepper_set_speed_limits},
        {"SET_ACCELERATION", "I", "", 4, VAL_stepper_channel, CMDH_stepper_set_acceleration},
        {"IS_STOPPED", "", "B", 0, VAL_stepper_channel, CMDH_stepper_is_stopped},
        {"GET_POSITION", "", "i", 0, VAL_stepper_channel, CMDH_stepper_get_position},
        {"SET_POSITION", "i", "", 4, VAL_stepper_channel, CMDH_stepper_set_position},
        {"HOME", "iB?", "", 6, VAL_stepper_channel, CMDH_stepper_home},
        {"JITTER", "iiii", "?", 16, VAL_stepper_channel, CMDH_stepper_jitter},
        {"IS_JITTERING", "", "B", 0, VAL_stepper_channel, CMDH_stepper_is_jittering},
        {"ENABLE_STALL_DETECTION", "?", "", 1, VAL_stepper_channel, CMDH_stepper_enable_stall_detection},
        {"GET_STALL_STATUS", "", "B", 0, VAL_stepper_channel, CMDH_stepper_get_stall_status},
        {"CLEAR_STALL", "", "", 0, VAL_stepper_channel, CMDH_stepper_clear_stall},
        {"SET_DIAG_PIN", "b", "", 1, VAL_stepper_channel, CMDH_stepper_set_diag_pin},
    }}};

void CMDH_stepper_drv_set_enabled(const BusMessage* msg, BusMessage* resp) {
    bool enabled = msg->payload[0] != 0;
    if (enabled) {
        ensure_stepper_hw_enabled(msg->channel);
    } else {
        tmc_drivers[msg->channel].enableDriver(false);
        stepper_drv_current_on[msg->channel] = false;
    }
    resp->payload_length = 0;
}

void CMDH_stepper_drv_set_microsteps(const BusMessage* msg, BusMessage* resp) {
    uint16_t arg_microsteps;
    memcpy(&arg_microsteps, msg->payload, sizeof(arg_microsteps));
    TMC2209_Microstep microsteps;
    switch (arg_microsteps) {
        case 256: microsteps = MICROSTEP_256; break;
        case 128: microsteps = MICROSTEP_128; break;
        case 64: microsteps = MICROSTEP_64; break;
        case 32: microsteps = MICROSTEP_32; break;
        case 16: microsteps = MICROSTEP_16; break;
        case 8: microsteps = MICROSTEP_8; break;
        case 4: microsteps = MICROSTEP_4; break;
        case 2: microsteps = MICROSTEP_2; break;
        case 1: microsteps = MICROSTEP_FULL; break;
        default:
            resp->command = msg->command | 0x80;
            resp->payload_length = snprintf((char*)resp->payload, MAX_PAYLOAD_SIZE, "Invalid microstep value %u", arg_microsteps);
            return;
    }
    tmc_drivers[msg->channel].setMicrosteps(microsteps);
    resp->payload_length = 0;
}

void CMDH_stepper_drv_set_current(const BusMessage* msg, BusMessage* resp) {
    uint8_t run_current = msg->payload[0];
    uint8_t hold_current = msg->payload[1];
    uint8_t hold_delay = msg->payload[2];
    tmc_drivers[msg->channel].setCurrent(run_current, hold_current, hold_delay);
    resp->payload_length = 0;
}

void CMDH_stepper_drv_read_register(const BusMessage* msg, BusMessage* resp) {
    uint8_t reg = msg->payload[0];
    uint32_t value;
    int result = tmc_drivers[msg->channel].readRegister(reg, &value);
    if (result != 0) {
        resp->command = msg->command | 0x80;
        resp->payload_length = snprintf((char*)resp->payload, MAX_PAYLOAD_SIZE, "Failed to read register %d", reg);
        return;
    }
    memcpy(resp->payload, &value, sizeof(value));
    resp->payload_length = sizeof(value);
}

void CMDH_stepper_drv_write_register(const BusMessage* msg, BusMessage* resp) {
    uint8_t reg = msg->payload[0];
    uint32_t value;
    memcpy(&value, msg->payload + 1, sizeof(value));
    tmc_drivers[msg->channel].writeRegister(reg, value);
    resp->payload_length = 0;
}

const struct CommandTable stepperDrvCmdTable = {
    .prefix = "STEPPER_DRV",
    .commands = {{
        {"SET_ENABLED", "?", "", 1, VAL_stepper_channel, CMDH_stepper_drv_set_enabled},
        {"SET_MICROSTEPS", "H", "", 2, VAL_stepper_channel, CMDH_stepper_drv_set_microsteps},
        {"SET_CURRENT", "BBB", "", 3, VAL_stepper_channel, CMDH_stepper_drv_set_current},
        {NULL, NULL, NULL, 0, NULL, NULL},
        {NULL, NULL, NULL, 0, NULL, NULL},
        {NULL, NULL, NULL, 0, NULL, NULL},
        {NULL, NULL, NULL, 0, NULL, NULL},
        {NULL, NULL, NULL, 0, NULL, NULL},
        {"READ_REGISTER", "B", "I", 1, VAL_stepper_channel, CMDH_stepper_drv_read_register},
        {"WRITE_REGISTER", "BI", "", 5, VAL_stepper_channel, CMDH_stepper_drv_write_register},
    }}};

// ---------------------------------------------------------------------------
// Command handlers — table 3 (DIGITAL_IO)
// ---------------------------------------------------------------------------

bool VAL_digital_out_channel(uint8_t channel) { return channel < DIGITAL_OUTPUT_COUNT; }
bool VAL_digital_in_channel(uint8_t channel) { return channel < DIGITAL_INPUT_COUNT; }

void CMDH_digital_read(const BusMessage* msg, BusMessage* resp) {
    bool value = digitalRead(digital_input_pins[msg->channel]);
    resp->payload[0] = value ? 1 : 0;
    resp->payload_length = 1;
}

void CMDH_digital_write(const BusMessage* msg, BusMessage* resp) {
    bool value = msg->payload[0] != 0;
    digitalWrite(digital_output_pins[msg->channel], value ? HIGH : LOW);
    resp->payload_length = 0;
}

void CMDH_digital_write_pwm(const BusMessage* msg, BusMessage* resp) {
    uint16_t duty;
    memcpy(&duty, msg->payload, sizeof(duty));
    analogWrite(digital_output_pins[msg->channel], duty);
    resp->payload_length = 0;
}

const struct CommandTable digitalIoCmdTable = {
    .prefix = "DIGITAL_IO",
    .commands = {{
        {"READ", "", "?", 0, VAL_digital_in_channel, CMDH_digital_read},
        {"WRITE", "?", "", 1, VAL_digital_out_channel, CMDH_digital_write},
        {"WRITE_PWM", "H", "", 2, VAL_digital_out_channel, CMDH_digital_write_pwm},
    }}};

// ---------------------------------------------------------------------------
// Command handlers — table 5 (CHUTE_ENCODER) — new, not part of the
// original RP2040 protocol. See ChuteEncoder.h for the "not yet verified
// against real hardware" caveat.
// ---------------------------------------------------------------------------

bool VAL_chute_encoder_channel(uint8_t channel) { return channel == 0; }

void CMDH_chute_encoder_get_count(const BusMessage* msg, BusMessage* resp) {
    (void)msg;
    int32_t count = chute_encoder.getCount();
    memcpy(resp->payload, &count, sizeof(count));
    resp->payload_length = sizeof(count);
}

void CMDH_chute_encoder_set_count(const BusMessage* msg, BusMessage* resp) {
    int32_t count;
    memcpy(&count, msg->payload, sizeof(count));
    chute_encoder.setCount(count);
    resp->payload_length = 0;
}

void CMDH_chute_encoder_is_index_synced(const BusMessage* msg, BusMessage* resp) {
    (void)msg;
    resp->payload[0] = chute_encoder.isIndexSynced() ? 1 : 0;
    resp->payload_length = 1;
}

void CMDH_chute_encoder_get_count_at_index(const BusMessage* msg, BusMessage* resp) {
    (void)msg;
    int32_t count = chute_encoder.getCountAtIndex();
    memcpy(resp->payload, &count, sizeof(count));
    resp->payload_length = sizeof(count);
}

void CMDH_chute_encoder_clear_index_synced(const BusMessage* msg, BusMessage* resp) {
    (void)msg;
    chute_encoder.clearIndexSynced();
    resp->payload_length = 0;
}

void CMDH_chute_encoder_get_raw_state(const BusMessage* msg, BusMessage* resp) {
    (void)msg;
    resp->payload[0] = chute_encoder.getRawState();
    resp->payload_length = 1;
}

// SAFETY (2026-11-17): a move request while the chute does not trust its
// zero (never homed since boot/reset, or a stall invalidated it — see
// markUnhomed() on the Python side) must not blindly drive to a
// numerically-computed-but-physically-meaningless position. Starts homing
// instead and refuses this one move; the caller (chute.py) re-homes on its
// own schedule and will not normally hit this except right after a reset
// that hasn't been through CHUTE_ENCODER.START_HOMING yet.
static bool refuseMoveIfNotHomed(BusMessage* resp, const char* name) {
    if (chute_auto_homed) return false;
    if (!chute_auto_homing_active && !chute_auto_homing_stopping && !chute_auto_homing_correcting) {
        start_chute_auto_home();
    }
    resp->command |= 0x80;
    resp->payload_length =
        snprintf(reinterpret_cast<char*>(resp->payload), 246, "%s: not homed, homing started — retry shortly", name);
    return true;
}

void CMDH_chute_move_to_count(const BusMessage* msg, BusMessage* resp) {
    if (refuseMoveIfNotHomed(resp, "MOVE_TO_COUNT")) return;
    int32_t target_count;
    memcpy(&target_count, msg->payload, sizeof(target_count));
    start_chute_move_to_count(target_count);
    resp->payload[0] = 1;
    resp->payload_length = 1;
}

void CMDH_chute_move_to_step_position(const BusMessage* msg, BusMessage* resp) {
    if (refuseMoveIfNotHomed(resp, "MOVE_TO_STEP_POS")) return;
    int32_t target_position;
    memcpy(&target_position, msg->payload, sizeof(target_position));
    start_chute_move_to_step_position(target_position);
    resp->payload[0] = 1;
    resp->payload_length = 1;
}

void CMDH_chute_start_homing(const BusMessage* msg, BusMessage* resp) {
    // A re-home that touches only the chute stepper - unlike the generic
    // INIT command, this does NOT call initialize_hardware() (which would
    // zero every stepper's current, re-cycle all 5 TMC2209 drivers, and
    // reset every LED/fan output to LOW - audible as a relay-like click
    // from the drivers re-enabling). Bench-confirmed 2026-11-16: the debug
    // UI's homing button was using INIT and clobbering unrelated state.
    (void)msg;
    start_chute_auto_home();
    resp->payload_length = 0;
}

const struct CommandTable chuteEncoderCmdTable = {
    .prefix = "CHUTE_ENCODER",
    .commands = {{
        {"GET_COUNT", "", "i", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_get_count},
        {"SET_COUNT", "i", "", 4, VAL_chute_encoder_channel, CMDH_chute_encoder_set_count},
        {"IS_INDEX_SYNCED", "", "?", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_is_index_synced},
        {"GET_COUNT_AT_INDEX", "", "i", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_get_count_at_index},
        {"CLEAR_INDEX_SYNCED", "", "", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_clear_index_synced},
        {"GET_RAW_STATE", "", "B", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_get_raw_state},
        {"MOVE_TO_COUNT", "i", "?", 4, VAL_chute_encoder_channel, CMDH_chute_move_to_count},
        {"MOVE_TO_STEP_POS", "i", "?", 4, VAL_chute_encoder_channel, CMDH_chute_move_to_step_position},
        {"START_HOMING", "", "", 0, VAL_chute_encoder_channel, CMDH_chute_start_homing},
    }}};

// Table 4 (SERVO) is intentionally absent (nullptr) — servos are out of
// scope for this board, migrated to the ESP32 BLE bridge. Opcodes 0x40-0x4F
// NACK "Invalid command", identical to every other undefined slot.
const MasterCommandTable command_tables = {
    {&baseCmdTable, &stepperCmdTable, &stepperDrvCmdTable, &digitalIoCmdTable, nullptr, &chuteEncoderCmdTable}};

// ---------------------------------------------------------------------------
// Timers (replace the RP2040 original's core1 self-rescheduling hardware
// alarms with genuine periodic STM32 timer interrupts — there is no second
// core to dedicate to real-time motion on this chip).
// ---------------------------------------------------------------------------

HardwareTimer* stepgen_timer = new HardwareTimer(TIM2);
HardwareTimer* motion_timer = new HardwareTimer(TIM3);

void stepgen_isr() {
    for (int i = 0; i < STEPPER_COUNT; i++) steppers[i].stepgen_tick();
}

void motion_update_isr() {
    for (int i = 0; i < STEPPER_COUNT; i++) steppers[i].motion_update_tick();
}

// ---------------------------------------------------------------------------
// Message transport — USB-CDC (Serial). Bring-up milestone: confirm this
// actually enumerates on the real board before relying on it (see README).
// ---------------------------------------------------------------------------

static void transmit(const char* data, int length) {
    Serial.write((const uint8_t*)data, length);
}

static BusMessageProcessor* msg_processor;

void setup() {
    Serial.begin(576000); // Cosmetic for USB-CDC, matches the backend's configured baud

    // Discard stale bytes from the USB-CDC RX queue before trusting it.
    // Confirmed on real hardware (2026-11-17): right after a DFU "leave"
    // (a software jump into the app, not a hardware reset — see README's
    // flashing section), the very first host command gets corrupted by up
    // to ~192 bytes (one full CDC_ReceiveQueue, 3 full-speed packets) of
    // leftover data that was never actually sent by the host. The USB
    // peripheral and its packet queues survive the DFU-to-app jump
    // untouched, so this is PMA/queue state left over from the DFU
    // session, not anything this firmware wrote. A FIXED window, not one
    // that extends itself on every byte seen: a low-rate trickle of real
    // noise (observed separately, far rarer, still unexplained) must never
    // be able to keep re-arming this and block setup() forever.
    {
        uint32_t drain_until_ms = millis() + 150;
        while (millis() < drain_until_ms) {
            if (Serial.available()) {
                Serial.read();
            }
        }
    }

    initialize_hardware();
    chute_encoder.initialize();

    stepgen_timer->setOverflow(STEP_TICK_RATE_HZ, HERTZ_FORMAT);
    stepgen_timer->attachInterrupt(stepgen_isr);
    stepgen_timer->resume();

    motion_timer->setOverflow(STEP_MOTION_UPDATE_RATE_HZ, HERTZ_FORMAT);
    motion_timer->attachInterrupt(motion_update_isr);
    motion_timer->resume();

    // SAFETY (2026-11-17): the chute used to auto-home unconditionally right
    // here, on every power-on or reset — including a bare DFU reflash, with
    // no host involved at all. That is exactly backward when someone has
    // physical hands near the machine for exactly that reason: flash the
    // ESP32 BLE bridge, then this board, and its own reset would have spun
    // the chute stepper immediately, with real risk of tearing out the
    // ESP32's USB cable mid-rotation. The chute now only ever homes on an
    // explicit CHUTE_ENCODER.START_HOMING command (chute.py's home(), sent
    // deliberately once the backend is actually in control) or lazily from
    // a move request that no longer trusts its zero — see
    // CMDH_chute_move_to_count/_step_position. Boot and INIT leave it
    // un-homed and motionless until one of those actually happens.

    static BusMessageProcessor processor(DEVICE_ADDRESS, command_tables, transmit);
    msg_processor = &processor;
}

void loop() {
    update_chute_auto_home();
    update_chute_move();
    update_chute_step_move();
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        msg_processor->processIncomingData(c);
        msg_processor->processQueuedMessage();
    }
}
