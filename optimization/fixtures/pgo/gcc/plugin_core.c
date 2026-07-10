#include "fixture.h"

#include <stddef.h>

static __attribute__((noinline)) uint64_t rotate_left(uint64_t value,
                                                       unsigned amount) {
    return (value << amount) | (value >> (64U - amount));
}

static __attribute__((noinline)) uint64_t hot_transform(uint64_t value,
                                                         uint32_t selector) {
    value ^= value >> 17;
    value *= UINT64_C(0xed5ad4bb7b31e995);
    value ^= (uint64_t)selector * UINT64_C(0x9e3779b185ebca87);
    return rotate_left(value, 13);
}

static __attribute__((noinline)) uint64_t cold_transform(uint64_t value,
                                                          uint32_t selector) {
    for (unsigned index = 1; index != 12; ++index) {
        value ^= rotate_left(value + selector + index, (index % 31U) + 1U);
        value *= UINT64_C(0xd6e8feb86659fd93);
    }
    return value ^ (value >> 29);
}

uint64_t fixture_plugin_step(uint64_t value, uint32_t selector,
                             enum fixture_mode mode) {
#if defined(GCC_PGO_FORCE_MISMATCH)
    /* This extra control-flow exists only in the deliberate mismatch build. */
    if ((selector & 1U) != 0U) {
        value ^= rotate_left(value, 7);
    } else {
        value += UINT64_C(0xa0761d6478bd642f);
    }
#endif

    if (mode == FIXTURE_MODE_COLD || (selector & 63U) == 0U) {
        return cold_transform(value, selector);
    }
    if (mode == FIXTURE_MODE_MIXED && (selector & 7U) == 0U) {
        return cold_transform(value ^ UINT64_C(0xe7037ed1a0b428db), selector);
    }
    return hot_transform(value, selector);
}
