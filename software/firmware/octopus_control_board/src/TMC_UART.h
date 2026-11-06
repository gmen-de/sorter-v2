/*
 * Octopus Control Board Firmware - TMC UART Bus Header
 * Copyright (C) 2026 Jose I Romero
 *
 * Reimplementation of software/firmware/sorter_interface_firmware/TMC_UART.*
 * for the standard Octopus board. The RP2040 board wired TMC2209 drivers onto a shared
 * 2-wire UART bus (separate TX/RX GPIOs tied together off-chip, up to 4
 * addressed drivers per bus). The Octopus (and Octopus Pro, which shares the
 * same motor-socket pinout) instead gives each motor socket
 * a single dedicated GPIO (true single-wire half-duplex) with exactly one
 * driver per wire — so this bus is a software (bit-banged) single-wire UART,
 * one instance per stepper channel, always talking to TMC UART address 0
 * (the default when a driver's MS1/MS2 straps are both low, which is the
 * case when it's alone on its own wire).
 *
 * Deliberately run at 100000 baud (10us/bit) rather than the original
 * 400000 baud: this link is entirely internal to this firmware (the TMC's
 * UART baud is invisible to the host wire protocol), and the slower rate
 * leaves far more timing margin for Arduino digitalRead/digitalWrite
 * overhead on a bit-banged link than 400000 baud (2.5us/bit) would.
 *
 * The on-the-wire TMC UART datagram format (sync byte, address, register,
 * big-endian data, CRC-8 poly 0x07) and the TMC2209 register-level API in
 * TMC2209.cpp/.h are unchanged from the original — only this transport
 * layer differs.
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

#ifndef TMC_UART_H
#define TMC_UART_H

#include <stdint.h>
#include <stddef.h>

class TMC_UART_Bus {
public:
    explicit TMC_UART_Bus(uint32_t pin);

    bool setupComm(long baudrate = 100000);

    void writeRegister(uint8_t address, uint8_t reg, uint32_t data);
    int readRegister(uint8_t address, uint8_t reg, uint32_t* data);

private:
    uint32_t _pin;
    uint32_t _bit_period_us;
    uint32_t _timeout_us;

    void txByte(uint8_t b);
    bool rxByte(uint8_t* out, uint32_t deadline_us);
    uint8_t calcCRC(const uint8_t* data, size_t length);
};

#endif // TMC_UART_H
