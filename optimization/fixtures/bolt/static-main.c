#include "fixture.h"

#include <stddef.h>
#include <stdint.h>

static long raw_write(int descriptor, const void *buffer, size_t size) {
    register long syscall_number __asm__("rax") = 1;
    register long argument1 __asm__("rdi") = descriptor;
    register const void *argument2 __asm__("rsi") = buffer;
    register size_t argument3 __asm__("rdx") = size;
    __asm__ volatile(
        "syscall"
        : "+a"(syscall_number)
        : "r"(argument1), "r"(argument2), "r"(argument3)
        : "rcx", "r11", "memory");
    return syscall_number;
}

__attribute__((noreturn)) static void raw_exit(int status) {
    register long syscall_number __asm__("rax") = 60;
    register long argument1 __asm__("rdi") = status;
    __asm__ volatile(
        "syscall"
        : "+a"(syscall_number)
        : "r"(argument1)
        : "rcx", "r11", "memory");
    __builtin_unreachable();
}

static uint64_t parse_positive_decimal(const char *text) {
    uint64_t value = 0;
    if (*text == '\0') {
        raw_exit(2);
    }
    for (; *text != '\0'; ++text) {
        if (*text < '0' || *text > '9') {
            raw_exit(2);
        }
        const uint64_t digit = (uint64_t)(*text - '0');
        if (value > (UINT64_MAX - digit) / UINT64_C(10)) {
            raw_exit(2);
        }
        value = value * UINT64_C(10) + digit;
    }
    if (value == 0) {
        raw_exit(2);
    }
    return value;
}

static char *append_text(char *output, const char *text) {
    while (*text != '\0') {
        *output++ = *text++;
    }
    return output;
}

static char *append_decimal(char *output, uint64_t value) {
    char reversed[20];
    size_t count = 0;
    do {
        reversed[count++] = (char)('0' + value % UINT64_C(10));
        value /= UINT64_C(10);
    } while (value != 0);
    while (count != 0) {
        *output++ = reversed[--count];
    }
    return output;
}

static char *append_hexadecimal(char *output, uint64_t value) {
    static const char digits[] = "0123456789abcdef";
    for (unsigned shift = 64; shift != 0; shift -= 4) {
        *output++ = digits[(value >> (shift - 4)) & UINT64_C(0xf)];
    }
    return output;
}

__attribute__((used, noinline)) static int static_fixture_main(
    uint64_t argc, char **argv
) {
    if (argc > UINT64_C(3)) {
        return 2;
    }
    const uint64_t iterations = argc > 1
        ? parse_positive_decimal(argv[1])
        : UINT64_C(1000000);
    const uint64_t parsed_mode = argc > 2
        ? parse_positive_decimal(argv[2])
        : UINT64_C(1);
    if (parsed_mode > UINT32_MAX) {
        return 2;
    }
    const unsigned mode = (unsigned)parsed_mode;
    const uint64_t result = bolt_fixture_run(iterations, mode);
    char output[128];
    char *cursor = append_text(output, "iterations=");
    cursor = append_decimal(cursor, iterations);
    cursor = append_text(cursor, " mode=");
    cursor = append_decimal(cursor, mode);
    cursor = append_text(cursor, " result=");
    cursor = append_hexadecimal(cursor, result);
    *cursor++ = '\n';
    return raw_write(1, output, (size_t)(cursor - output)) < 0 ? 1 : 0;
}

__attribute__((naked, noreturn, visibility("default"))) void _start(void) {
    __asm__ volatile(
        "xorl %ebp, %ebp\n"
        "movq (%rsp), %rdi\n"
        "leaq 8(%rsp), %rsi\n"
        "andq $-16, %rsp\n"
        "callq static_fixture_main\n"
        "movl %eax, %edi\n"
        "movl $60, %eax\n"
        "syscall\n"
        "hlt\n");
}
