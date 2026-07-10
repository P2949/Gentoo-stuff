#pragma once

#include <cstdint>

namespace clang_ir_pgo_fixture {

std::uint64_t run_workload(std::uint64_t seed, unsigned rounds, unsigned mode);

} // namespace clang_ir_pgo_fixture
