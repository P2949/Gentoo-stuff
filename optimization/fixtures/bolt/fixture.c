#include "fixture.h"

BOLT_NOINLINE static uint64_t hot_even(uint64_t value) {
    value ^= value << 13;
    value ^= value >> 7;
    value ^= value << 17;
    return value * UINT64_C(0x9e3779b185ebca87);
}

BOLT_NOINLINE static uint64_t hot_odd(uint64_t value) {
    value += UINT64_C(0xd1b54a32d192ed03);
    value = (value << 19) | (value >> 45);
    return value ^ (value * UINT64_C(0x94d049bb133111eb));
}

BOLT_NOINLINE static uint64_t warm_path(uint64_t value, unsigned selector) {
    switch (selector & 3U) {
    case 0:
        return value + (value >> 11);
    case 1:
        return value ^ (value << 9);
    case 2:
        return value - (value >> 5);
    default:
        return value + UINT64_C(0x2545f4914f6cdd1d);
    }
}

BOLT_NOINLINE static uint64_t cold_path(uint64_t value) {
    for (unsigned index = 0; index < 7U; ++index) {
        value = hot_even(value + index);
    }
    return value;
}

uint64_t bolt_fixture_run(uint64_t iterations, unsigned mode) {
    uint64_t state = UINT64_C(0x6a09e667f3bcc909) ^ mode;

    for (uint64_t index = 0; index < iterations; ++index) {
        if (((index + mode) & 1U) == 0U) {
            state = hot_even(state + index);
        } else {
            state = hot_odd(state ^ index);
        }
        if ((index & 15U) == (mode & 15U)) {
            state = warm_path(state, (unsigned)index + mode);
        }
        if ((index & UINT64_C(0xfffff)) == UINT64_C(0xabcde)) {
            state ^= cold_path(state);
        }
    }
    return state;
}
