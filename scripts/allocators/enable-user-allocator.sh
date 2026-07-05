#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <jemalloc|mimalloc|tcmalloc>" >&2
    exit 2
fi

allocator="$1"

case "${allocator}" in
    jemalloc) lib="${JEMALLOC_LIB:-/usr/lib64/libjemalloc.so}" ;;
    mimalloc) lib="${MIMALLOC_LIB:-/usr/lib64/libmimalloc.so}" ;;
    tcmalloc) lib="${TCMALLOC_LIB:-/usr/lib64/libtcmalloc.so}" ;;
    *) echo "unknown allocator: ${allocator}" >&2; exit 2 ;;
esac

if [[ ! -e "${lib}" ]]; then
    echo "allocator library not found: ${lib}" >&2
    exit 1
fi

mkdir -p "${HOME}/.config/environment.d"

cat > "${HOME}/.config/environment.d/99-global-allocator.conf" <<EOF
LD_PRELOAD=${lib}
EOF

echo "enabled user-session allocator preload: ${lib}"
echo "log out and back in for environment.d consumers to inherit it"
