#include "fixture.h"

static const uint64_t fixture_constants[] = {
    UINT64_C(0x243f6a8885a308d3), UINT64_C(0x13198a2e03707344),
    UINT64_C(0xa4093822299f31d0), UINT64_C(0x082efa98ec4e6c89),
    UINT64_C(0x452821e638d01377), UINT64_C(0xbe5466cf34e90c6c),
    UINT64_C(0xc0ac29b7c97c50dd), UINT64_C(0x3f84d5b5b5470917),
};

uint64_t fixture_plugin_table(uint64_t value, uint32_t selector) {
    const uint64_t constant = fixture_constants[selector & 7U];
    value ^= constant;
    value *= (constant | UINT64_C(1));
    return value ^ (value >> ((selector & 15U) + 1U));
}
