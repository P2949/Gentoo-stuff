#include "sample_math.hpp"

#include <charconv>
#include <cstdint>
#include <cstdio>
#include <string_view>

namespace {

std::uint64_t parse_iterations(const char *argument) {
    std::uint64_t result = 0;
    const std::string_view input(argument);
    const auto parsed = std::from_chars(input.data(), input.data() + input.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != input.data() + input.size() || result == 0) {
        return 0;
    }
    return result;
}

} // namespace

int main(int argc, char **argv) {
    const std::uint64_t iterations = argc == 2 ? parse_iterations(argv[1]) : 0;
    if (iterations == 0) {
        std::fputs("usage: sample-workload ITERATIONS\n", stderr);
        return 2;
    }

    std::uint64_t state = UINT64_C(0x123456789abcdef0);
    for (std::uint64_t index = 0; index != iterations; ++index) {
        state = sample_mix(state + index, static_cast<unsigned>(index));
    }
    std::printf("iterations=%llu checksum=%016llx\n",
                static_cast<unsigned long long>(iterations),
                static_cast<unsigned long long>(state));
    return 0;
}
