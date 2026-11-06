/*
 * Octopus Control Board Firmware - Chute Magnet-Ring Position Encoder
 * Copyright (C) 2026
 *
 * New feature (no RP2040 equivalent): the chute now rotates continuously
 * past 350 degrees via a slip ring, so its old mechanical limit switch is
 * gone. Position is instead inferred from two DRV5013 Hall-latch sensors
 * reading a 71-magnet ring (alternating N/S, 1 index gap = 72 nominal
 * positions per revolution) mounted on the chute's own flange.
 *
 * Each sensor is a bistable latch: it flips to one logic level crossing a
 * north pole and back crossing a south pole, so as the ring turns each
 * sensor toggles roughly once per magnet. Two sensors mounted at a fixed
 * mechanical offset from each other behave like a 2-phase incremental
 * encoder, decoded here with the standard 4x quadrature state-transition
 * table. The one missing magnet (index gap) produces one noticeably longer
 * interval between transitions than its neighbours once per revolution,
 * used here as a re-sync reference after power loss.
 *
 * IMPORTANT — not yet verified against real hardware: the exact counts-
 * per-revolution figure and the gap-detection threshold are nominal
 * estimates, not bench-calibrated values (no magnet ring + sensor pair has
 * been tested yet, per the mechanical design conversation this firmware is
 * built against). This class reports a raw signed quadrature count and an
 * index-sync event with the count captured at that event; translating that
 * into a calibrated absolute angle is left to whoever first bench-tests it
 * against the real ring — don't assume getCount()'s units are degrees.
 *
 * Exposed to the host over a new command table (table index 5, unused by
 * the original wire protocol — see the firmware README for the opcode
 * list), not through the generic DIGITAL_IO table: the raw Hall-latch
 * levels are not very useful to a host too far away (in USB-polling terms)
 * to decode transitions reliably in real time, so this keeps the standing
 * "all sensor intelligence lives on the MCU" design already used for the
 * servo BLE bridge.
 */

#ifndef CHUTE_ENCODER_H
#define CHUTE_ENCODER_H

#include <atomic>
#include <stdint.h>

class ChuteEncoder {
public:
    ChuteEncoder(uint32_t pin_a, uint32_t pin_b);

    // Sets pin modes and attaches the pin-change interrupts. Only one
    // ChuteEncoder instance may exist (the ISRs are plain static functions
    // that reach back into it through a single static instance pointer).
    void initialize();

    // Signed running count of quadrature transitions since boot or the last
    // setCount(). Direction sign depends on which sensor leads — verify
    // against the real ring's mounting orientation during bench test.
    int32_t getCount() { return _count.load(); }
    void setCount(int32_t count) { _count.store(count); }

    // True once an index-gap event (one unusually long inter-transition
    // interval, i.e. the missing 72nd magnet) has been seen since boot or
    // the last clearIndexSynced().
    bool isIndexSynced() { return _index_synced.load(); }
    // getCount()'s value at the moment the most recently seen index-gap
    // event was detected. Only meaningful when isIndexSynced() is true.
    int32_t getCountAtIndex() { return _count_at_index.load(); }
    void clearIndexSynced() { _index_synced.store(false); }

private:
    void handleEdge();
    static void isrA();
    static void isrB();

    uint32_t _pin_a, _pin_b;

    std::atomic<int32_t> _count{0};
    std::atomic<uint8_t> _last_state{0};
    std::atomic<uint32_t> _last_edge_us{0};
    // Exponential moving average of the interval between transitions, used
    // only to notice "this gap was unusually long" — not a calibrated speed
    // measurement.
    std::atomic<uint32_t> _avg_interval_us{0};
    std::atomic<bool> _index_synced{false};
    std::atomic<int32_t> _count_at_index{0};

    static ChuteEncoder* _instance;
};

#endif // CHUTE_ENCODER_H
