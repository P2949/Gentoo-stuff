#ifndef GENTOO_OPTIMIZATION_BOLT_FIXTURE_H
#define GENTOO_OPTIMIZATION_BOLT_FIXTURE_H

#include <stdint.h>

#if defined(__GNUC__) || defined(__clang__)
#define BOLT_NOINLINE __attribute__((noinline))
#define BOLT_EXPORT __attribute__((visibility("default")))
#else
#define BOLT_NOINLINE
#define BOLT_EXPORT
#endif

BOLT_EXPORT uint64_t bolt_fixture_run(uint64_t iterations, unsigned mode);

#endif
