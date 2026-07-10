#!/usr/bin/env bash

set -euo pipefail
IFS=$'\n\t'
umask 077

FIXTURE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly FIXTURE_DIR
RESULT_ROOT="${1:-/tmp/gentoo-optimization-phase-1-clang-ir-pgo}"
case ${RESULT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *)
        printf 'result directory must be below /tmp or /var/tmp/gentoo-optimization: %s\n' \
            "${RESULT_ROOT}" >&2
        exit 2
        ;;
esac
if [[ -e ${RESULT_ROOT} && ! -d ${RESULT_ROOT} ]]; then
    printf 'result path is not a directory: %s\n' "${RESULT_ROOT}" >&2
    exit 2
fi
mkdir -p -- "$RESULT_ROOT"
RESULT_ROOT="$(cd -- "$RESULT_ROOT" && pwd -P)"
readonly RESULT_ROOT
readonly OUTPUT_MARKER="${RESULT_ROOT}/.clang-ir-pgo-fixture-output"
if [[ -e ${OUTPUT_MARKER} ]]; then
    [[ -f ${OUTPUT_MARKER} && ! -L ${OUTPUT_MARKER} ]] || {
        printf 'unsafe output marker: %s\n' "${OUTPUT_MARKER}" >&2
        exit 2
    }
elif find "${RESULT_ROOT}" -mindepth 1 -print -quit | rg -q .; then
    printf 'refusing nonempty unmarked result directory: %s\n' "${RESULT_ROOT}" >&2
    exit 2
else
    printf 'clang-ir-pgo-fixture-output-v1\n' >"${OUTPUT_MARKER}"
fi
readonly BUILD_ROOT="$RESULT_ROOT/build"
readonly GENERATE_ROOT="$BUILD_ROOT/generate"
readonly USE_ROOT="$BUILD_ROOT/use"
readonly MISMATCH_ROOT="$BUILD_ROOT/mismatch"
readonly RAW_ROOT="$RESULT_ROOT/raw"
readonly TRAINING_ROOT="$RESULT_ROOT/training"
readonly RAW_PATTERN="$RAW_ROOT/%m-%p.profraw"
readonly RAW_LIST="$RESULT_ROOT/raw-profiles.list"
readonly MERGED_PROFILE="$RESULT_ROOT/merged.profdata"
readonly COMMAND_LOG="$RESULT_ROOT/commands.log"
readonly RUN_LOG="$RESULT_ROOT/run.log"
readonly PROFILE_SHOW="$RESULT_ROOT/merged-profile.txt"
readonly MISMATCH_LOG="$RESULT_ROOT/mismatch-negative.log"
readonly THINLTO_SECTIONS="$RESULT_ROOT/thinlto-object-sections.txt"
readonly FINAL_DYNAMIC="$RESULT_ROOT/final-dynamic.txt"
readonly SYSTEM_PORTAGE_FLAGS="$RESULT_ROOT/system-portage-flags.txt"
readonly SUMMARY="$RESULT_ROOT/summary.txt"
readonly EVIDENCE_HASHES="$RESULT_ROOT/evidence.sha256"

rm -rf -- "$BUILD_ROOT" "$RAW_ROOT" "$TRAINING_ROOT"
rm -f -- "$RAW_LIST" "$MERGED_PROFILE" "$COMMAND_LOG" "$RUN_LOG" \
    "$PROFILE_SHOW" "$MISMATCH_LOG" "$THINLTO_SECTIONS" "$FINAL_DYNAMIC" \
    "$SYSTEM_PORTAGE_FLAGS" "$SUMMARY" "$EVIDENCE_HASHES"
mkdir -p -- "$GENERATE_ROOT/lib" "$GENERATE_ROOT/app" \
    "$USE_ROOT/lib" "$USE_ROOT/app" "$USE_ROOT/thinlto-cache" \
    "$MISMATCH_ROOT/app" "$MISMATCH_ROOT/thinlto-cache" "$RAW_ROOT" \
    "$TRAINING_ROOT"

exec > >(tee -a "$RUN_LOG") 2>&1

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

record_command() {
    {
        printf 'COMMAND'
        printf ' %q' "$@"
        printf '\n'
    } >>"$COMMAND_LOG"
}

run() {
    record_command "$@"
    "$@"
}

resolve_tool() {
    local requested="$1"
    local resolved
    resolved="$(command -v -- "$requested")" || fail "tool not found: $requested"
    if [[ "$resolved" != /* ]]; then
        resolved="$(pwd -P)/$resolved"
    fi
    printf '%s/%s\n' "$(cd -- "$(dirname -- "$resolved")" && pwd -P)" \
        "$(basename -- "$resolved")"
}

CLANGXX="$(resolve_tool "${CLANGXX:-clang++}")"
readonly CLANGXX
# Do not canonicalise the clang++ symlink to clang-22: Clang selects C++
# driver behaviour from argv[0], and losing the "++" would omit libc++.
LLVM_BINDIR=$(dirname -- "$CLANGXX")
readonly LLVM_BINDIR
LLVM_PROFDATA="${LLVM_PROFDATA:-$LLVM_BINDIR/llvm-profdata}"
LLD="${LLD:-$LLVM_BINDIR/ld.lld}"
LLVM_PROFDATA="$(readlink -f -- "$LLVM_PROFDATA")"
LLD="$(readlink -f -- "$LLD")"
readonly LLVM_PROFDATA LLD
PORTAGEQ="$(resolve_tool portageq)"
readonly PORTAGEQ

[[ -x "$LLVM_PROFDATA" ]] || fail "paired llvm-profdata is not executable: $LLVM_PROFDATA"
[[ -x "$LLD" ]] || fail "paired ld.lld is not executable: $LLD"

readonly -a COMMON_FLAGS=(
    -std=c++20
    -O3
    -march=native
    -gline-tables-only
    -fvisibility=hidden
    -fvisibility-inlines-hidden
    -fstrict-vtable-pointers
    -flto=thin
    -ffat-lto-objects
    -funified-lto
    -fforce-emit-vtables
    -fwhole-program-vtables
    -fsplit-lto-unit
    -stdlib=libc++
    -I"$FIXTURE_DIR"
)
readonly -a LINK_FLAGS=(
    -fuse-ld=lld
    -rtlib=compiler-rt
    -unwindlib=libunwind
    -stdlib=libc++
    "-Wl,--lto-O3"
    "-Wl,--lto-CGO3"
    "-Wl,--build-id"
)
readonly -a GENERATE_FLAGS=(-fprofile-generate="$RAW_ROOT")
readonly -a USE_FLAGS=(
    -fprofile-use="$MERGED_PROFILE"
    -Wprofile-instr-out-of-date
    -Wprofile-instr-unprofiled
)

printf 'fixture_dir=%s\nresult_root=%s\nraw_pattern=%s\n' \
    "$FIXTURE_DIR" "$RESULT_ROOT" "$RAW_PATTERN"
run "$CLANGXX" --version
run "$LLD" --version
run "$LLVM_PROFDATA" --version
run "$PORTAGEQ" envvar CXXFLAGS LDFLAGS >"$SYSTEM_PORTAGE_FLAGS"
for required_system_flag in \
    -flto=thin \
    -ffat-lto-objects \
    -funified-lto \
    -fforce-emit-vtables \
    -fwhole-program-vtables \
    -fsplit-lto-unit \
    -Wl,--lto-O3 \
    -Wl,--lto-CGO3; do
    rg -Fq -- "$required_system_flag" "$SYSTEM_PORTAGE_FLAGS" ||
        fail "active Portage flags lack required ThinLTO axis: $required_system_flag"
done

# Generation DSO: two independently compiled translation units.
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${GENERATE_FLAGS[@]}" -fPIC \
    -c "$FIXTURE_DIR/library.cpp" -o "$GENERATE_ROOT/lib/library.o"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${GENERATE_FLAGS[@]}" -fPIC \
    -c "$FIXTURE_DIR/library_detail.cpp" -o "$GENERATE_ROOT/lib/library_detail.o"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${GENERATE_FLAGS[@]}" \
    "${LINK_FLAGS[@]}" -shared \
    -Wl,--thinlto-cache-dir="$GENERATE_ROOT/thinlto-cache" \
    "$GENERATE_ROOT/lib/library.o" "$GENERATE_ROOT/lib/library_detail.o" \
    -o "$GENERATE_ROOT/lib/libclang-ir-pgo-fixture.so"

# Generation executable: two more independently compiled translation units.
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${GENERATE_FLAGS[@]}" \
    -c "$FIXTURE_DIR/main.cpp" -o "$GENERATE_ROOT/app/main.o"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${GENERATE_FLAGS[@]}" \
    -c "$FIXTURE_DIR/workload.cpp" -o "$GENERATE_ROOT/app/workload.o"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${GENERATE_FLAGS[@]}" \
    "${LINK_FLAGS[@]}" -Wl,--thinlto-cache-dir="$GENERATE_ROOT/thinlto-cache" \
    "$GENERATE_ROOT/app/main.o" "$GENERATE_ROOT/app/workload.o" \
    -L"$GENERATE_ROOT/lib" -lclang-ir-pgo-fixture \
    "-Wl,-rpath,\$ORIGIN/../lib" -o "$GENERATE_ROOT/app/clang-ir-pgo-fixture"

# Six concurrent processes exercise distinct hot paths.  The absolute %m/%p
# pattern prevents module and process collisions.
training_pids=()
for mode in 0 1 2 3 4 5; do
    record_command env "LLVM_PROFILE_FILE=$RAW_PATTERN" \
        "$GENERATE_ROOT/app/clang-ir-pgo-fixture" "$mode" 30000
    LLVM_PROFILE_FILE="$RAW_PATTERN" \
        "$GENERATE_ROOT/app/clang-ir-pgo-fixture" "$mode" 30000 \
        >"$TRAINING_ROOT/mode-$mode.log" 2>&1 &
    training_pids+=("$!")
done
for pid in "${training_pids[@]}"; do
    wait "$pid"
done
for mode in 0 1 2 3 4 5; do
    cat -- "$TRAINING_ROOT/mode-$mode.log"
done

shopt -s nullglob
raw_profiles=("$RAW_ROOT"/*.profraw)
shopt -u nullglob
(( ${#raw_profiles[@]} >= 6 )) ||
    fail "expected at least six raw profiles, found ${#raw_profiles[@]}"

profile_pids=()
profile_modules=()
for raw_profile in "${raw_profiles[@]}"; do
    [[ -s "$raw_profile" ]] || fail "empty raw profile: $raw_profile"
    raw_name="${raw_profile##*/}"
    profile_pid="${raw_name##*-}"
    profile_pid="${profile_pid%.profraw}"
    [[ "$profile_pid" =~ ^[0-9]+$ ]] ||
        fail "raw profile does not end in a process id: $raw_name"
    profile_pids+=("$profile_pid")
    profile_modules+=("${raw_name%-*}")
done

mapfile -t unique_profile_pids < <(printf '%s\n' "${profile_pids[@]}" | sort -u)
mapfile -t unique_profile_modules < <(printf '%s\n' "${profile_modules[@]}" | sort -u)
(( ${#unique_profile_pids[@]} >= 6 )) ||
    fail "expected raw profiles from six processes, found ${#unique_profile_pids[@]}"
(( ${#unique_profile_modules[@]} >= 2 )) ||
    fail "expected distinct executable and DSO profile modules"

printf '%s\n' "${raw_profiles[@]}" >"$RAW_LIST"
run "$LLVM_PROFDATA" merge --failure-mode=all --input-files="$RAW_LIST" \
    -o "$MERGED_PROFILE"
run "$LLVM_PROFDATA" show --all-functions --counts "$MERGED_PROFILE" \
    >"$PROFILE_SHOW"
[[ -s "$MERGED_PROFILE" ]] || fail "llvm-profdata emitted an empty merged profile"
rg -q '^Instrumentation level: IR' "$PROFILE_SHOW" ||
    fail "merged profile is not identified as an LLVM IR instrumentation profile"
rg -q 'clang_ir_pgo_library_mix' "$PROFILE_SHOW" ||
    fail "merged profile lacks the DSO entry point"
rg -q 'run_workload' "$PROFILE_SHOW" ||
    fail "merged profile lacks the executable workload"

# Profile-use DSO and executable, retaining the same system ThinLTO axes.
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${USE_FLAGS[@]}" -fPIC \
    -c "$FIXTURE_DIR/library.cpp" -o "$USE_ROOT/lib/library.o"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${USE_FLAGS[@]}" -fPIC \
    -c "$FIXTURE_DIR/library_detail.cpp" -o "$USE_ROOT/lib/library_detail.o"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${USE_FLAGS[@]}" \
    "${LINK_FLAGS[@]}" -shared -Wl,--thinlto-cache-dir="$USE_ROOT/thinlto-cache" \
    "$USE_ROOT/lib/library.o" "$USE_ROOT/lib/library_detail.o" \
    -o "$USE_ROOT/lib/libclang-ir-pgo-fixture.so"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${USE_FLAGS[@]}" \
    -c "$FIXTURE_DIR/main.cpp" -o "$USE_ROOT/app/main.o"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${USE_FLAGS[@]}" \
    -c "$FIXTURE_DIR/workload.cpp" -o "$USE_ROOT/app/workload.o"
run "$CLANGXX" "${COMMON_FLAGS[@]}" "${USE_FLAGS[@]}" \
    "${LINK_FLAGS[@]}" -Wl,--thinlto-cache-dir="$USE_ROOT/thinlto-cache" \
    "$USE_ROOT/app/main.o" "$USE_ROOT/app/workload.o" \
    -L"$USE_ROOT/lib" -lclang-ir-pgo-fixture "-Wl,-rpath,\$ORIGIN/../lib" \
    -o "$USE_ROOT/app/clang-ir-pgo-fixture"
run "$USE_ROOT/app/clang-ir-pgo-fixture" 2 12000

run readelf -SW "$USE_ROOT/app/main.o" >"$THINLTO_SECTIONS"
rg -q '\.llvm\.lto[[:space:]]+LLVM_LTO' "$THINLTO_SECTIONS" ||
    fail "fat profile-use object lacks its embedded ThinLTO IR section"
run readelf -d "$USE_ROOT/app/clang-ir-pgo-fixture" >"$FINAL_DYNAMIC"
rg -q 'libclang-ir-pgo-fixture\.so' "$FINAL_DYNAMIC" ||
    fail "final profile-use executable is not linked to the fixture DSO"

mapfile -t thinlto_cache_files < <(
    find "$USE_ROOT/thinlto-cache" -type f -name 'llvmcache-*' -print | sort
)
(( ${#thinlto_cache_files[@]} > 0 )) ||
    fail "profile-use final links did not populate the ThinLTO cache"

# Deliberate stale-profile negative case.  With unified LTO, Clang 22 reports
# the changed function hash as a backend-plugin diagnostic during bitcode
# generation.  Promote that exact diagnostic to an error.  Success would mean
# the mismatch diagnostic was suppressed or failed to fire.
record_command "$CLANGXX" "${COMMON_FLAGS[@]}" "${USE_FLAGS[@]}" \
    -Werror=backend-plugin -DPROFILE_SCHEMA_MISMATCH=1 \
    -c "$FIXTURE_DIR/workload.cpp" -o "$MISMATCH_ROOT/app/workload.o"
set +e
"$CLANGXX" "${COMMON_FLAGS[@]}" "${USE_FLAGS[@]}" \
    -Werror=backend-plugin -DPROFILE_SCHEMA_MISMATCH=1 \
    -c "$FIXTURE_DIR/workload.cpp" -o "$MISMATCH_ROOT/app/workload.o" \
    >"$MISMATCH_LOG" 2>&1
mismatch_status=$?
set -e
(( mismatch_status != 0 )) ||
    fail "mismatched-profile compilation unexpectedly succeeded"
rg -q 'function control flow change detected \(hash mismatch\)' "$MISMATCH_LOG" ||
    fail "mismatch failed without the expected visible profile diagnostic"
rg -q '\[-Werror,-Wbackend-plugin\]' "$MISMATCH_LOG" ||
    fail "mismatch diagnostic was not promoted by backend-plugin"
if rg -q 'hash mismatch|profile data may be out of date|no profile data available' \
    "$RUN_LOG"; then
    fail "positive profile-use build emitted a stale or missing profile diagnostic"
fi

compiler_sha256="$(sha256sum "$CLANGXX" | awk '{print $1}')"
profdata_sha256="$(sha256sum "$LLVM_PROFDATA" | awk '{print $1}')"
lld_sha256="$(sha256sum "$LLD" | awk '{print $1}')"
merged_sha256="$(sha256sum "$MERGED_PROFILE" | awk '{print $1}')"
fixture_source_sha256="$(
    sha256sum "$FIXTURE_DIR"/{api.hpp,library_detail.hpp,library_detail.cpp,library.cpp,workload.hpp,workload.cpp,main.cpp} |
        sha256sum | awk '{print $1}'
)"
generate_dso_sha256="$(sha256sum "$GENERATE_ROOT/lib/libclang-ir-pgo-fixture.so" | awk '{print $1}')"
generate_exe_sha256="$(sha256sum "$GENERATE_ROOT/app/clang-ir-pgo-fixture" | awk '{print $1}')"
use_dso_sha256="$(sha256sum "$USE_ROOT/lib/libclang-ir-pgo-fixture.so" | awk '{print $1}')"
use_exe_sha256="$(sha256sum "$USE_ROOT/app/clang-ir-pgo-fixture" | awk '{print $1}')"
profile_function_count="$(sed -n 's/^Total functions: //p' "$PROFILE_SHOW" | tail -n 1)"
[[ "$profile_function_count" =~ ^[0-9]+$ ]] ||
    fail "could not read total function count from llvm-profdata output"

{
    printf 'result=PASS\n'
    printf 'clangxx=%s\nllvm_profdata=%s\nld_lld=%s\n' \
        "$CLANGXX" "$LLVM_PROFDATA" "$LLD"
    printf 'clangxx_sha256=%s\nllvm_profdata_sha256=%s\nld_lld_sha256=%s\n' \
        "$compiler_sha256" "$profdata_sha256" "$lld_sha256"
    printf 'raw_pattern=%s\nraw_profile_count=%s\nraw_process_count=%s\nraw_module_count=%s\n' \
        "$RAW_PATTERN" "${#raw_profiles[@]}" "${#unique_profile_pids[@]}" \
        "${#unique_profile_modules[@]}"
    printf 'merged_profile=%s\nmerged_profile_sha256=%s\nprofile_function_count=%s\n' \
        "$MERGED_PROFILE" "$merged_sha256" "$profile_function_count"
    printf 'profile_kind=LLVM_IR_instrumentation\n'
    printf 'fixture_source_sha256=%s\n' "$fixture_source_sha256"
    printf 'generate_dso_sha256=%s\ngenerate_executable_sha256=%s\n' \
        "$generate_dso_sha256" "$generate_exe_sha256"
    printf 'use_dso_sha256=%s\nuse_executable_sha256=%s\n' \
        "$use_dso_sha256" "$use_exe_sha256"
    printf 'thinlto_cache_entry_count=%s\n' "${#thinlto_cache_files[@]}"
    printf 'system_thinlto_flags_verified=true\n'
    printf 'mismatch_exit_status=%s\n' "$mismatch_status"
    printf 'mismatch_diagnostic=backend-plugin-hash-mismatch\n'
} | tee "$SUMMARY"

sha256sum "$COMMAND_LOG" "$PROFILE_SHOW" "$MISMATCH_LOG" \
    "$THINLTO_SECTIONS" "$FINAL_DYNAMIC" "$SYSTEM_PORTAGE_FLAGS" \
    "$SUMMARY" "$MERGED_PROFILE" \
    "$GENERATE_ROOT/lib/libclang-ir-pgo-fixture.so" \
    "$GENERATE_ROOT/app/clang-ir-pgo-fixture" \
    "$USE_ROOT/lib/libclang-ir-pgo-fixture.so" \
    "$USE_ROOT/app/clang-ir-pgo-fixture" "${raw_profiles[@]}" \
    >"$EVIDENCE_HASHES"

printf 'PASS: Clang IR-PGO generation, merge, ThinLTO use, and mismatch checks\n'
