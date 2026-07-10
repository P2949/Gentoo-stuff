#ifndef GENTOO_OPTIMIZATION_GCC_PGO_FIXTURE_H
#define GENTOO_OPTIMIZATION_GCC_PGO_FIXTURE_H

#include <stdint.h>

enum fixture_mode {
    FIXTURE_MODE_HOT = 0,
    FIXTURE_MODE_MIXED = 1,
    FIXTURE_MODE_COLD = 2,
};

struct fixture_result {
    uint64_t checksum;
    uint64_t hot_visits;
    uint64_t cold_visits;
};

uint64_t fixture_plugin_step(uint64_t value, uint32_t selector,
                             enum fixture_mode mode);
uint64_t fixture_plugin_table(uint64_t value, uint32_t selector);
struct fixture_result fixture_run(enum fixture_mode mode, uint64_t iterations,
                                  uint64_t seed);

#endif
