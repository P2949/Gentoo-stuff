/*
 * Deterministic user-space branch workload for perf/LBR capability checks.
 *
 * The indirect call on every iteration gives perf a stable stream of taken
 * branches without depending on files, clocks, random devices, or networking.
 */
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef uint64_t (*mix_fn)(uint64_t, uint64_t);

static __attribute__((noinline)) uint64_t
mix0(uint64_t value, uint64_t accumulator)
{
	return (accumulator + value) ^ (value << 7);
}

static __attribute__((noinline)) uint64_t
mix1(uint64_t value, uint64_t accumulator)
{
	return (accumulator ^ value) + (value >> 3);
}

static __attribute__((noinline)) uint64_t
mix2(uint64_t value, uint64_t accumulator)
{
	return (accumulator - value) ^ (value << 13);
}

static __attribute__((noinline)) uint64_t
mix3(uint64_t value, uint64_t accumulator)
{
	return (accumulator + (value ^ (value >> 17))) * UINT64_C(0x9e3779b1);
}

int
main(int argc, char **argv)
{
	static mix_fn const functions[] = {mix0, mix1, mix2, mix3};
	uint64_t iterations = UINT64_C(100000000);
	uint64_t state = UINT64_C(0x4d595df4d0f33173);
	uint64_t accumulator = UINT64_C(0x243f6a8885a308d3);

	if (argc > 2) {
		fprintf(stderr, "usage: %s [iterations]\n", argv[0]);
		return EXIT_FAILURE;
	}
	if (argc == 2) {
		char *end = NULL;
		errno = 0;
		iterations = strtoull(argv[1], &end, 10);
		if (errno != 0 || end == argv[1] || *end != '\0' || iterations == 0) {
			fprintf(stderr, "invalid iteration count: %s\n", argv[1]);
			return EXIT_FAILURE;
		}
	}

	for (uint64_t index = 0; index < iterations; ++index) {
		state ^= state << 13;
		state ^= state >> 7;
		state ^= state << 17;
		accumulator = functions[state & 3](state, accumulator);
	}

	printf("iterations=%" PRIu64 " checksum=%016" PRIx64 "\n",
	       iterations, accumulator);
	return EXIT_SUCCESS;
}
