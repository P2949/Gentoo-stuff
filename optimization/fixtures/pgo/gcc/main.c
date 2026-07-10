#include "fixture.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int parse_mode(const char *argument, enum fixture_mode *mode) {
    if (strcmp(argument, "hot") == 0) {
        *mode = FIXTURE_MODE_HOT;
        return 1;
    }
    if (strcmp(argument, "mixed") == 0) {
        *mode = FIXTURE_MODE_MIXED;
        return 1;
    }
    if (strcmp(argument, "cold") == 0) {
        *mode = FIXTURE_MODE_COLD;
        return 1;
    }
    return 0;
}

static int parse_u64(const char *argument, uint64_t *value) {
    char *end = NULL;
    errno = 0;
    const unsigned long long parsed = strtoull(argument, &end, 0);
    if (errno != 0 || end == argument || *end != '\0' || parsed == 0) {
        return 0;
    }
    *value = (uint64_t)parsed;
    return 1;
}

int main(int argc, char **argv) {
    enum fixture_mode mode;
    uint64_t iterations;
    uint64_t seed = UINT64_C(0x123456789abcdef0);

    if ((argc != 3 && argc != 4) || !parse_mode(argv[1], &mode) ||
        !parse_u64(argv[2], &iterations) ||
        (argc == 4 && !parse_u64(argv[3], &seed))) {
        fprintf(stderr, "usage: %s {hot|mixed|cold} ITERATIONS [SEED]\n",
                argv[0]);
        return 2;
    }

    const struct fixture_result result = fixture_run(mode, iterations, seed);
    printf("mode=%s iterations=%" PRIu64 " checksum=%016" PRIx64
           " hot=%" PRIu64 " cold=%" PRIu64 "\n",
           argv[1], iterations, result.checksum, result.hot_visits,
           result.cold_visits);
    return 0;
}
