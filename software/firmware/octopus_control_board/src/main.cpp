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
#define FIRMWARE_VARIANT "octopus"
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

    int n_bytes = snprintf(buf, buf_size, "{\"hw\":\"%s\",\"diag_pins\":%s,\"led_gpios\":%s}", HW_ID,
                            diag_pins_len > 0 ? diag_pins_buf : "[]", led_gpios_buf);
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
}

// ---------------------------------------------------------------------------
// Command handlers — table 0 (base)
// ---------------------------------------------------------------------------

void CMDH_init(const BusMessage* msg, BusMessage* resp) {
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

const struct CommandTable chuteEncoderCmdTable = {
    .prefix = "CHUTE_ENCODER",
    .commands = {{
        {"GET_COUNT", "", "i", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_get_count},
        {"SET_COUNT", "i", "", 4, VAL_chute_encoder_channel, CMDH_chute_encoder_set_count},
        {"IS_INDEX_SYNCED", "", "?", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_is_index_synced},
        {"GET_COUNT_AT_INDEX", "", "i", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_get_count_at_index},
        {"CLEAR_INDEX_SYNCED", "", "", 0, VAL_chute_encoder_channel, CMDH_chute_encoder_clear_index_synced},
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
    initialize_hardware();
    chute_encoder.initialize();

    stepgen_timer->setOverflow(STEP_TICK_RATE_HZ, HERTZ_FORMAT);
    stepgen_timer->attachInterrupt(stepgen_isr);
    stepgen_timer->resume();

    motion_timer->setOverflow(STEP_MOTION_UPDATE_RATE_HZ, HERTZ_FORMAT);
    motion_timer->attachInterrupt(motion_update_isr);
    motion_timer->resume();

    static BusMessageProcessor processor(DEVICE_ADDRESS, command_tables, transmit);
    msg_processor = &processor;
}

void loop() {
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        msg_processor->processIncomingData(c);
        msg_processor->processQueuedMessage();
    }
}
