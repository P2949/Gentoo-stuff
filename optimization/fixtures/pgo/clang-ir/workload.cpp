#include "workload.hpp"

#include "api.hpp"

#include <array>
#include <cstdint>

#ifndef PROFILE_SCHEMA_MISMATCH
#define PROFILE_SCHEMA_MISMATCH 0
#endif

namespace clang_ir_pgo_fixture {

std::uint64_t run_workload(std::uint64_t seed, unsigned rounds, unsigned mode) {
    std::array<std::uint64_t, 8> lanes{};
    for (std::size_t lane = 0; lane < lanes.size(); ++lane) {
        lanes[lane] = seed + lane * 0x9e3779b97f4a7c15ULL;
    }

    for (unsigned iteration = 0; iteration < rounds; ++iteration) {
        const std::size_t lane = (iteration + mode) & (lanes.size() - 1U);
        lanes[lane] = clang_ir_pgo_library_mix(
            lanes[lane] ^ iteration, 16U + (iteration & 15U), mode + iteration);

#if PROFILE_SCHEMA_MISMATCH
        // This deliberately changes the IR counter layout for the negative
        // test.  Reusing the v1 profile must produce an out-of-date-profile
        // diagnostic; the runner promotes that diagnostic to an error.
        if ((lanes[lane] & 0x1fU) == 0x0bU) {
            lanes[(lane + 3U) & (lanes.size() - 1U)] ^= 0xd6e8feb86659fd93ULL;
        }
#endif
    }

    std::uint64_t result = 0;
    for (const std::uint64_t lane : lanes) {
        result ^= lane + (result << 7U) + (result >> 3U);
    }
    return result;
}

} // namespace clang_ir_pgo_fixture
