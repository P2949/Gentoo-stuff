#include "fixture.h"

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>

static uint64_t parse_iterations(const char *text) {
    char *end = NULL;
    if (*text == '\0') {
        fprintf(stderr, "empty numeric argument\n");
        exit(2);
    }
    for (const char *cursor = text; *cursor != '\0'; ++cursor) {
        if (*cursor < '0' || *cursor > '9') {
            fprintf(stderr, "numeric argument must contain decimal digits only: %s\n", text);
            exit(2);
        }
    }
    errno = 0;
    const unsigned long long value = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value == 0ULL || value > UINT64_MAX) {
        fprintf(stderr, "invalid iteration count: %s\n", text);
        exit(2);
    }
    return (uint64_t)value;
}

int main(int argc, char **argv) {
    if (argc > 3) {
        fprintf(stderr, "usage: %s [positive-iterations] [positive-unsigned-mode]\n", argv[0]);
        return 2;
    }
    const uint64_t iterations = argc > 1 ? parse_iterations(argv[1]) : UINT64_C(1000000);
    const uint64_t parsed_mode = argc > 2 ? parse_iterations(argv[2]) : UINT64_C(1);
    if (parsed_mode > UINT_MAX) {
        fprintf(stderr, "mode is outside the unsigned range: %" PRIu64 "\n", parsed_mode);
        return 2;
    }
    const unsigned mode = (unsigned)parsed_mode;
    const uint64_t result = bolt_fixture_run(iterations, mode);
    printf("iterations=%" PRIu64 " mode=%u result=%016" PRIx64 "\n", iterations, mode, result);
    return 0;
}
