// Re-emit the libc++ inline member that the established source-highlight ABI
// exported.  This is scoped to the exact de-instrumentation transaction and
// keeps the installed SONAME ABI stable without changing the guard.
#include <string>

template void std::__1::basic_string<
    char,
    std::__1::char_traits<char>,
    std::__1::allocator<char>
>::__init_copy_ctor_external(const char *, unsigned long);
