#pragma once

#include <cstdint>

namespace clang_ir_pgo_fixture {

std::uint64_t rotate_mix(std::uint64_t value, unsigned shift);
std::uint64_t branch_mix(std::uint64_t value, unsigned mode);

} // namespace clang_ir_pgo_fixture
