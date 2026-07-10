#include "api.hpp"
#include "workload.hpp"

#include <charconv>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string_view>

namespace {

unsigned parse_unsigned(const char *text, const char *name) {
    unsigned value = 0;
    const std::string_view input{text};
    const auto [end, error] = std::from_chars(input.begin(), input.end(), value);
    if (error != std::errc{} || end != input.end()) {
        std::cerr << "invalid " << name << ": " << input << '\n';
        std::exit(2);
    }
    return value;
}

} // namespace

int main(int argc, char **argv) {
    const unsigned mode = argc > 1 ? parse_unsigned(argv[1], "mode") : 0U;
    const unsigned rounds = argc > 2 ? parse_unsigned(argv[2], "rounds") : 50000U;
    const std::uint64_t seed = 0x6a09e667f3bcc909ULL ^
                               (static_cast<std::uint64_t>(mode) << 32U);
    const std::uint64_t checksum =
        clang_ir_pgo_fixture::run_workload(seed, rounds, mode);

    std::cout << clang_ir_pgo_library_identity() << " mode=" << mode
              << " rounds=" << rounds << " checksum=" << checksum << '\n';
    return checksum == 0 ? 1 : 0;
}
