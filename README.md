# Gentoo-stuff

Aggressive Gentoo Portage config for a Clang/LLVM/libc++/LTO/Polly/OpenMP
experiment.

The default is intentionally risky and bleeding edge. Packages should start at
the most aggressive tier, then be demoted one axis at a time through
`portage/package.env` only when a real build failure, runtime bug, or
miscompilation proves the need.

See `portage/env/README` for the fallback ladder and local-only policy notes.
