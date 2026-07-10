#include "sample_math.hpp"

namespace {

[[gnu::noinline]] std::uint64_t rotate_mix(std::uint64_t value) {
    value ^= value >> 17;
    value *= UINT64_C(0xed5ad4bb);
    value ^= value >> 11;
    return (value << 13) | (value >> (64 - 13));
}

[[gnu::noinline]] std::uint64_t multiply_mix(std::uint64_t value) {
    value ^= value << 7;
    value *= UINT64_C(0x9e3779b185ebca87);
    return value ^ (value >> 29);
}

[[gnu::noinline]] std::uint64_t sparse_cold_path(std::uint64_t value) {
    for (unsigned index = 0; index != 11; ++index) {
        value = rotate_mix(value + index);
    }
    return value;
}

} // namespace

[[gnu::noinline]] std::uint64_t sample_mix(std::uint64_t value, unsigned selector) {
    if ((selector & 31U) == 0U) {
        return sparse_cold_path(value);
    }
    if ((selector & 3U) == 0U) {
        return rotate_mix(value);
    }
    return multiply_mix(value);
}
