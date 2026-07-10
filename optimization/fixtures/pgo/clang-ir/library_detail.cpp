#include "library_detail.hpp"

#include <bit>

namespace clang_ir_pgo_fixture {

std::uint64_t rotate_mix(std::uint64_t value, unsigned shift) {
    return std::rotl(value ^ 0x9e3779b97f4a7c15ULL, static_cast<int>(shift & 63U));
}

std::uint64_t branch_mix(std::uint64_t value, unsigned mode) {
    switch (mode & 3U) {
    case 0:
        return rotate_mix(value + 0x243f6a8885a308d3ULL, 7U);
    case 1:
        return rotate_mix(value ^ 0x13198a2e03707344ULL, 17U);
    case 2:
        return rotate_mix(value * 0xbf58476d1ce4e5b9ULL, 29U);
    default:
        return rotate_mix(value + (value >> 11U), 43U);
    }
}

} // namespace clang_ir_pgo_fixture
