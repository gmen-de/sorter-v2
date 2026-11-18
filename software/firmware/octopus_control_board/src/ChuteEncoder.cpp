/*
 * Octopus Control Board Firmware - Chute Magnet-Ring Position Encoder
 * Copyright (C) 2026
 *
 * See ChuteEncoder.h for the design and the "not yet verified against real
 * hardware" caveat.
 */

#include "ChuteEncoder.h"
#include <Arduino.h>

ChuteEncoder* ChuteEncoder::_instance = nullptr;

// Standard 4x quadrature decode table: index = (old_state << 2) | new_state,
// where each 2-bit state is (sensor_a << 1 | sensor_b). Entries that aren't a
// valid single-step quadrature transition (e.g. both sensors flipping at
// once, or no change) decode to 0 and are ignored — on a real encoder these
// only happen from read glitches/bounce, not genuine motion.
static const int8_t QUAD_TABLE[16] = {
    0,  -1, +1, 0,
    +1, 0,  0,  -1,
    -1, 0,  0,  +1,
    0,  +1, -1, 0,
};

// An interval this many times the running average marks the index gap (the
// one missing magnet out of 72 nominal positions). Bench-measured on real
// hardware (2026-11-14): normal spacing vs. the genuine gap sits at a
// consistent ~2.0-2.2x ratio, reproduced at both 1000 and 3000 usteps/s -
// 1.8x sits safely between the two with margin on both sides.
static const uint32_t INDEX_GAP_RATIO_NUM = 9;
static const uint32_t INDEX_GAP_RATIO_DEN = 5; // i.e. 1.8x

// Both Hall sensors see the same physical gap, offset by their fixed 6-
// transition mechanical spacing (bench-measured) - without a lockout, the
// second sensor's own long interval latches as a second, spurious index
// event a few transitions later. 20 is safely above that 6 and safely
// below the ~170-transition spacing to the next revolution's real gap.
static const uint32_t MIN_TRANSITIONS_BETWEEN_INDEX_EVENTS = 20;

ChuteEncoder::ChuteEncoder(uint32_t pin_a, uint32_t pin_b) : _pin_a(pin_a), _pin_b(pin_b) {
}

void ChuteEncoder::initialize() {
    _instance = this;
    pinMode(_pin_a, INPUT_PULLUP);
    pinMode(_pin_b, INPUT_PULLUP);
    uint8_t a = digitalRead(_pin_a) ? 1 : 0;
    uint8_t b = digitalRead(_pin_b) ? 1 : 0;
    _last_state.store((uint8_t)((a << 1) | b));
    _last_edge_us.store(micros());
    attachInterrupt(digitalPinToInterrupt(_pin_a), isrA, CHANGE);
    attachInterrupt(digitalPinToInterrupt(_pin_b), isrB, CHANGE);
}

void ChuteEncoder::resetIntervalTracking() {
    _avg_interval_us.store(0);
    _transitions_since_index = 0;
    // See the long comment on this method in ChuteEncoder.h: without this,
    // the first transition after an idle period bootstraps the average from
    // an interval that includes however long the ring sat still, not the
    // real magnet-to-magnet spacing.
    _last_edge_us.store(micros());
}

void ChuteEncoder::isrA() {
    if (_instance != nullptr) _instance->handleEdge(true);
}

void ChuteEncoder::isrB() {
    if (_instance != nullptr) _instance->handleEdge(false);
}

void ChuteEncoder::handleEdge(bool from_sensor_a) {
    uint8_t a = digitalRead(_pin_a) ? 1 : 0;
    uint8_t b = digitalRead(_pin_b) ? 1 : 0;
    uint8_t new_state = (uint8_t)((a << 1) | b);
    uint8_t old_state = _last_state.load();
    if (new_state == old_state) return; // Debounce/glitch: no real transition
    _last_state.store(new_state);

    int8_t delta = QUAD_TABLE[(old_state << 2) | new_state];
    if (delta == 0) return; // Not a valid single-step transition

    _count.fetch_add(delta);

    uint32_t now = micros();
    uint32_t last = _last_edge_us.load();
    uint32_t interval = now - last; // wraps safely (unsigned)
    _last_edge_us.store(now);

    uint32_t avg = _avg_interval_us.load();
    bool is_outlier = avg > 0 && interval > (avg * INDEX_GAP_RATIO_NUM) / INDEX_GAP_RATIO_DEN;
    if (is_outlier && from_sensor_a && _transitions_since_index >= MIN_TRANSITIONS_BETWEEN_INDEX_EVENTS) {
        // Unusually long gap since the last transition from our one
        // designated reference sensor (A): the missing 72nd magnet. Latch
        // the event; don't let this abnormal interval drag the running
        // average up.
        _index_synced.store(true);
        _count_at_index.store(_count.load());
        _transitions_since_index = 0;
    } else if (is_outlier) {
        // Either sensor B's own delayed detection of the same physical gap
        // (wrong sensor - ignored so the zero point doesn't depend on travel
        // direction), or still within the lockout window. Don't latch it and
        // don't let it pollute the average either.
        _transitions_since_index++;
    } else {
        // Exponential moving average, weight 1/8 on the new sample.
        _avg_interval_us.store(avg == 0 ? interval : avg - (avg >> 3) + (interval >> 3));
        _transitions_since_index++;
    }
}
