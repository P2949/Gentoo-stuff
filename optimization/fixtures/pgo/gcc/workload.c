#include "fixture.h"

struct fixture_result fixture_run(enum fixture_mode mode, uint64_t iterations,
                                  uint64_t seed) {
    struct fixture_result result = {
        .checksum = seed,
        .hot_visits = 0,
        .cold_visits = 0,
    };

    for (uint64_t index = 0; index != iterations; ++index) {
        const uint32_t selector = (uint32_t)(index ^ (index >> 32));
        const int takes_cold_path =
            mode == FIXTURE_MODE_COLD || (selector & 63U) == 0U ||
            (mode == FIXTURE_MODE_MIXED && (selector & 7U) == 0U);

        result.checksum =
            fixture_plugin_step(result.checksum + index, selector, mode);
        result.checksum = fixture_plugin_table(result.checksum, selector);
        result.cold_visits += (uint64_t)takes_cold_path;
        result.hot_visits += (uint64_t)!takes_cold_path;
    }

    result.checksum ^= result.hot_visits * UINT64_C(0x9e3779b185ebca87);
    result.checksum ^= result.cold_visits * UINT64_C(0xd6e8feb86659fd93);
    return result;
}
