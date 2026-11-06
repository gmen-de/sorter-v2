/*
 * Octopus Control Board Firmware - TMC UART Bus Implementation
 * Copyright (C) 2026 Jose I Romero
 *
 * See TMC_UART.h for why this is a software (bit-banged) single-wire UART
 * instead of the RP2040 original's hardware-UART-plus-electrical-tie design.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
*/

#include "TMC_UART.h"
#include <Arduino.h>

TMC_UART_Bus::TMC_UART_Bus(uint32_t pin) : _pin(pin), _bit_period_us(10), _timeout_us(2500) {
}

bool TMC_UART_Bus::setupComm(long baudrate) {
    _bit_period_us = 1000000UL / (uint32_t)baudrate;
    _timeout_us = 250000000UL / (uint32_t)baudrate; // 250 bit periods, same budget as the original
    pinMode(_pin, OUTPUT);
    digitalWrite(_pin, HIGH); // idle high
    return true;
}

/*! \brief Bit-bang one byte out, LSB first, with start/stop bits (8N1). */
void TMC_UART_Bus::txByte(uint8_t b) {
    noInterrupts();
    digitalWrite(_pin, LOW); // start bit
    delayMicroseconds(_bit_period_us);
    for (uint8_t i = 0; i < 8; i++) {
        digitalWrite(_pin, (b & (1 << i)) ? HIGH : LOW);
        delayMicroseconds(_bit_period_us);
    }
    digitalWrite(_pin, HIGH); // stop bit
    delayMicroseconds(_bit_period_us);
    interrupts();
}

/*! \brief Bit-bang one byte in, LSB first, 8N1.
 *
 *  Waits for the start bit with interrupts enabled (the wait can take up to
 *  `timeout_us`, and must not stall the stepper motion-tick timer ISRs for
 *  that long). Once a start bit is seen, the actual byte sampling runs with
 *  interrupts disabled for accurate bit timing — bounded to about one byte
 *  time (~10 bit periods), not the whole wait.
 */
bool TMC_UART_Bus::rxByte(uint8_t* out, uint32_t timeout_us) {
    uint32_t wait_start = micros();
    while (digitalRead(_pin) == HIGH) {
        if ((uint32_t)(micros() - wait_start) > timeout_us) return false;
    }
    noInterrupts();
    delayMicroseconds(_bit_period_us + _bit_period_us / 2); // skip start bit, land mid data-bit-0
    uint8_t value = 0;
    for (uint8_t i = 0; i < 8; i++) {
        if (digitalRead(_pin) == HIGH) value |= (uint8_t)(1u << i);
        delayMicroseconds(_bit_period_us);
    }
    interrupts();
    *out = value;
    return true;
}

/*! \brief Write a 32-bit value to a register on the TMC device. Fire-and-forget: the
 *  TMC2209 never replies to a write. */
void TMC_UART_Bus::writeRegister(uint8_t address, uint8_t reg, uint32_t value) {
    uint8_t frame[8];
    frame[0] = 0x55; // Sync byte
    frame[1] = address;
    frame[2] = reg | 0x80; // Set MSB for write
    frame[3] = (uint8_t)((value >> 24) & 0xFF);
    frame[4] = (uint8_t)((value >> 16) & 0xFF);
    frame[5] = (uint8_t)((value >> 8) & 0xFF);
    frame[6] = (uint8_t)(value & 0xFF);
    frame[7] = calcCRC(frame, 7);

    pinMode(_pin, OUTPUT);
    for (int i = 0; i < 8; i++) txByte(frame[i]);
    digitalWrite(_pin, HIGH); // leave idle high
}

/*! \brief Read a 32-bit register value from the TMC device.
 *  \return 0 on success, -1 on timeout/short read, -2 on CRC mismatch. */
int TMC_UART_Bus::readRegister(uint8_t address, uint8_t reg, uint32_t* value) {
    uint8_t cmd[4];
    cmd[0] = 0x55; // Sync byte
    cmd[1] = address;
    cmd[2] = reg & 0x7F; // Clear MSB for read
    cmd[3] = calcCRC(cmd, 3);

    pinMode(_pin, OUTPUT);
    for (int i = 0; i < 4; i++) txByte(cmd[i]);

    // Switch the wire from drive to listen. Unlike the RP2040 board (separate
    // TX/RX GPIOs electrically tied together off-chip, so every TX self-echoes
    // on RX and must be flushed), this is the same single pin just changing
    // direction — we only ever see what the driver actually sends back.
    pinMode(_pin, INPUT_PULLUP);

    uint8_t resp[8];
    for (int i = 0; i < 8; i++) {
        if (!rxByte(&resp[i], _timeout_us)) {
            pinMode(_pin, OUTPUT);
            digitalWrite(_pin, HIGH);
            return -1; // Timeout or incomplete response
        }
    }
    pinMode(_pin, OUTPUT);
    digitalWrite(_pin, HIGH); // back to idle-high drive for the next write

    uint8_t crc = calcCRC(resp, 7);
    if (crc != resp[7]) {
        return -2; // CRC error
    }
    *value = ((uint32_t)resp[3] << 24) | ((uint32_t)resp[4] << 16) | ((uint32_t)resp[5] << 8) | resp[6];
    return 0;
}

/*! \brief TMC UART CRC-8, polynomial x^8+x^2+x^1+x^0 (poly byte 0x07), LSB-first.
 *  Identical to the RP2040 original — pure bit math, no platform dependency. */
uint8_t TMC_UART_Bus::calcCRC(const uint8_t* data, size_t length) {
    uint8_t crc = 0;
    for (size_t i = 0; i < length; i++) {
        uint8_t cur_byte = data[i];
        for (uint8_t j = 0; j < 8; j++) {
            if ((crc >> 7) ^ (cur_byte & 0x01)) {
                crc = (uint8_t)((crc << 1) ^ 0x07);
            } else {
                crc = (uint8_t)(crc << 1);
            }
            cur_byte >>= 1;
        }
    }
    return crc;
}
