// BIGTREETECH Octopus (standard, non-Pro) V1.0/V1.1 (STM32F446ZET6 default;
// see README for the F429ZGT6/F407ZET6 alternates) — replaces the
// "basically" RP2040 distribution board. Chosen over the Octopus Pro
// because Marlin's own shared pin header (pins_BTT_OCTOPUS_V1_common.h)
// confirms every pin this firmware uses — Motor sockets, TMC UART, DIAG,
// heater, fan — is identical between the standard and Pro boards; the
// Pro's extras (dedicated probe port, 60V stepper rail option, optional
// H723/Cortex-M7 chip) are all irrelevant here and the standard board is
// actually simpler on the chip front (always Cortex-M4, never H723).
// Servos are NOT part of this board (migrated separately to the Waveshare
// SC15 / ESP32 BLE bridge) — no I2C/PCA9685 section here.
//
// Pin table taken from Marlin's/Klipper's own open-source pin definitions for
// this board (the only reliable public source — BTT does not publish a bare
// pinout doc). Motor0-Motor4 sockets are used for the 5 stepper channels;
// Motor5/Motor6's endstop-shared pins carry the new chute magnet-ring Hall
// sensors instead of steppers; two of the hotend heater MOSFET pins (HE1,
// HE3) are repurposed as 24V LED/COB PWM outputs.

const char* const HW_ID = "octopus_v1_1";

const uint8_t STEPPER_COUNT = 5;
const uint32_t STEPPER_STEP_PINS[] = {PF13, PG0, PF11, PG4, PF9};
const uint32_t STEPPER_DIR_PINS[]  = {PF12, PG1, PG3, PC1, PF10};
const uint32_t STEPPER_nEN_PINS[]  = {PF14, PF15, PG5, PA0, PG2};

#ifdef FIRMWARE_ROLE_DISTRIBUTION
const char* const STEPPER_NAMES[] = {
    "chute_stepper",       // ch0 — Motor0 — STEP=PF13 DIR=PF12 EN=PF14 UART=PC4 DIAG=PG6
    "c_channel_1_rotor",   // ch1 — Motor1 — STEP=PG0  DIR=PG1  EN=PF15 UART=PD11 DIAG=PG9
    "c_channel_3_rotor",   // ch2 — Motor2 — STEP=PF11 DIR=PG3  EN=PG5  UART=PC6 DIAG=PG10
    "carousel",            // ch3 — Motor3 — STEP=PG4  DIR=PC1  EN=PA0  UART=PC7 DIAG=PG11
    "c_channel_2_rotor"    // ch4 — Motor4 — STEP=PF9  DIR=PF10 EN=PG2  UART=PF2 DIAG=PG12
};
#else
const char* const STEPPER_NAMES[] = {
    "carousel",
    "third_c_channel_rotor",
    "second_c_channel_rotor",
    "first_c_channel_rotor",
    "fifth_stepper"
};
#endif

// Each motor socket on this board is its own single-wire half-duplex UART
// link with exactly one driver on it (unlike the RP2040 board's shared
// 2-wire bus with up to 4 addressed drivers) — so every channel uses TMC
// UART address 0 (the default for MS1=MS2=low) and gets its own
// TMC_UART_Bus instance on its own pin. See TMC_UART.h/.cpp.
const uint32_t TMC_UART_PINS[] = {PC4, PD11, PC6, PC7, PF2};
const uint8_t TMC_UART_ADDRESSES[] = {0, 0, 0, 0, 0};
const long TMC_UART_BAUDRATE = 100000; // software bit-banged; see TMC_UART.h

// StallGuard DIAG. On this board DIAG is electrically the same pin as that
// motor socket's endstop header signal (BTT ties them together with a
// jumper) — confirm each socket's DIAG jumper is populated before relying
// on stall detection.
const int STEPPER_DIAG_PINS[] = {PG6, PG9, PG10, PG11, PG12};

// No stepper on this board currently homes against a generic endstop (the
// chute no longer needs one — see the magnet-ring encoder below, and no
// other channel's homing input was identified when this board was
// planned). One spare input is still declared on Motor7's otherwise-unused
// endstop pin (PG15), reserved/unassigned, so DIGITAL_IO channel 0 exists
// for future use instead of leaving DIGITAL_INPUT_COUNT at zero.
const uint8_t DIGITAL_INPUT_COUNT = 1;
const uint32_t digital_input_pins[] = {PG15}; // spare/unassigned

// LED/24V PWM outputs, repurposed from hotend heater MOSFETs HE1 (PA3) and
// HE3 (PB11). On the Pro board HE0/HE2 swap pins between revisions (which
// is why this firmware avoided them); the standard board has no such
// revision swap, but HE1/HE3 work identically here too, so there's no
// reason to change the choice.
const uint8_t DIGITAL_OUTPUT_COUNT = 2;
const uint32_t digital_output_pins[] = {PA3, PB11};
const int FAN0_OUTPUT_CHANNEL = -1;
const uint8_t LED_OUTPUT_COUNT = 2;

// New feature, not present on the RP2040 boards: the chute's continuous-
// rotation (>350°, slip-ring powered) position sensing. Two DRV5013
// Hall-latch sensors read a 71-magnet ring (1 index gap) mounted on the
// chute's own flange. See ChuteEncoder.h/.cpp and the table-5 protocol
// addition documented in the firmware README. Wired to two otherwise-
// unused motor sockets' endstop-shared pins (Motor5, Motor6).
const uint32_t CHUTE_ENCODER_PIN_A = PG13;
const uint32_t CHUTE_ENCODER_PIN_B = PG14;
