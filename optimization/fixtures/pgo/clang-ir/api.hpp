#pragma once

#include <cstdint>

#if defined(__GNUC__)
#define CLANG_IR_PGO_EXPORT __attribute__((visibility("default")))
#else
#define CLANG_IR_PGO_EXPORT
#endif

extern "C" {

CLANG_IR_PGO_EXPORT std::uint64_t
clang_ir_pgo_library_mix(std::uint64_t seed, unsigned rounds, unsigned mode);

CLANG_IR_PGO_EXPORT const char *clang_ir_pgo_library_identity();

}
