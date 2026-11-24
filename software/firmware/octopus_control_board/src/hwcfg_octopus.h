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
// pinout doc). Motor3-Motor7 sockets are used for the 5 stepper channels
// (deliberately skipping Motor2, the dual-output MOTOR2_1/MOTOR2_2 socket
// shared by one driver, and avoiding Motor0/Motor1 below); Motor0/Motor1's
// endstop-shared pins carry the new chute magnet-ring Hall sensors instead
// of steppers; two of the hotend heater MOSFET pins (HE1, HE3) are
// repurposed as 24V LED/COB PWM outputs.

const char* const HW_ID = "octopus_v1_1";

const uint8_t STEPPER_COUNT = 5;
const uint32_t STEPPER_STEP_PINS[] = {PC13, PE2, PE6, PG4, PF9};
const uint32_t STEPPER_DIR_PINS[]  = {PF0,  PE3, PA14, PC1, PF10};
const uint32_t STEPPER_nEN_PINS[]  = {PF1,  PD4, PE0,  PA0, PG2};

#ifdef FIRMWARE_ROLE_DISTRIBUTION
const char* const STEPPER_NAMES[] = {
    "chute_stepper",       // ch0 — Motor5 — STEP=PC13 DIR=PF0  EN=PF1 UART=PE4 DIAG=PG13
    "c_channel_1_rotor",   // ch1 — Motor6 — STEP=PE2  DIR=PE3  EN=PD4 UART=PE1 DIAG=PG14
    "c_channel_3_rotor",   // ch2 — Motor7 — STEP=PE6  DIR=PA14 EN=PE0 UART=PD3 DIAG=PG15
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
const uint32_t TMC_UART_PINS[] = {PE4, PE1, PD3, PC7, PF2};
const uint8_t TMC_UART_ADDRESSES[] = {0, 0, 0, 0, 0};
const long TMC_UART_BAUDRATE = 100000; // software bit-banged; see TMC_UART.h

// StallGuard DIAG. On this board DIAG is electrically the same pin as that
// motor socket's endstop header signal (BTT ties them together with a
// jumper) — confirm each socket's DIAG jumper is populated before relying
// on stall detection.
const int STEPPER_DIAG_PINS[] = {PG13, PG14, PG15, PG11, PG12};

// No stepper on this board currently homes against a generic endstop (the
// chute no longer needs one — see the magnet-ring encoder below). Motor2
// (the dual-output socket) is unused and its endstop pin not identified, so
// no spare digital input is declared here for now.
const uint8_t DIGITAL_INPUT_COUNT = 0;
const uint32_t digital_input_pins[] = {};

// LED/24V PWM outputs, repurposed from hotend heater MOSFETs HE0 (PA2),
// HE1 (PA3) and HE2 (PB10) — three independent lighting zones. Pins taken
// from Marlin's pins_BTT_OCTOPUS_V1_common.h (HEATER_0_PIN/HEATER_1_PIN/
// HEATER_2_PIN); HE3 (PB11) is left unused/available for a future zone.
// Channels 3-8 are the board's 6 fan headers (FAN0-FAN5 silkscreen), same
// Marlin header (FAN0_PIN..FAN5_PIN = PA8, PE5, PD12, PD13, PD14, PD15) -
// the board has 6 fan headers, not 8; there is no FAN6/FAN7. Exposed
// through the same generic DIGITAL_IO.WRITE/WRITE_PWM commands as the LED
// channels, just appended to the same array - no new command table slot.
const uint8_t DIGITAL_OUTPUT_COUNT = 9;
// PA2 and PA3 are driven through their _ALT1 timer, TIM5 (CH3/CH4): their
// default, TIM2_CH3/CH4, is the very channel PB10 (HE2) and PB11 (HE3) are on,
// and STM32duino's pwm_start() routes a pin to its timer only while the
// channel isn't in PWM mode yet - so whichever of PA2/PB10 came second only
// moved the first one's duty and never lit up (HE2 stayed dark). The fans are
// on TIM1/TIM4/TIM9, the step timers TIM6/TIM7, so TIM5 is free. The ALT bits
// are masked off (PNUM_MASK) wherever a pin number is reported or compared.
const uint32_t digital_output_pins[] = {PA2_ALT1, PA3_ALT1, PB10, PA8, PE5, PD12, PD13, PD14, PD15};
const int FAN0_OUTPUT_CHANNEL = -1; // kept disabled - was already -1 before the fan channels existed; manual control only via DIGITAL_IO
const uint8_t LED_OUTPUT_COUNT = 3;
const uint8_t FAN_OUTPUT_COUNT = 6;
const uint8_t FAN_OUTPUT_FIRST_CHANNEL = 3;

// New feature, not present on the RP2040 boards: the chute's continuous-
// rotation (>350°, slip-ring powered) position sensing. Two DRV5013
// Hall-latch sensors read a 71-magnet ring (1 index gap) mounted on the
// chute's own flange. See ChuteEncoder.h/.cpp and the table-5 protocol
// addition documented in the firmware README. Wired to the two motor
// sockets freed up by moving the steppers to Motor3-Motor7 (Motor0's and
// Motor1's endstop-shared pins).
const uint32_t CHUTE_ENCODER_PIN_A = PG6;
const uint32_t CHUTE_ENCODER_PIN_B = PG9;
