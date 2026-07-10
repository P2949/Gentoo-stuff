#include "api.hpp"
#include "library_detail.hpp"

#include <cstdint>

extern "C" std::uint64_t
clang_ir_pgo_library_mix(std::uint64_t seed, unsigned rounds, unsigned mode) {
    std::uint64_t value = seed | 1ULL;
    for (unsigned iteration = 0; iteration < rounds; ++iteration) {
        value = clang_ir_pgo_fixture::branch_mix(value + iteration, mode + iteration);
        if ((iteration & 7U) == (mode & 7U)) {
            value ^= clang_ir_pgo_fixture::rotate_mix(value, iteration);
        }
    }
    return value;
}

extern "C" const char *clang_ir_pgo_library_identity() {
    return "clang-ir-pgo-dso-v1";
}
