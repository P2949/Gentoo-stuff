#!/usr/bin/env bash
# The fixture intentionally writes literal ${...} expressions into fake tools
# and isolates every environment mutation inside a case subshell.
# shellcheck disable=SC1090,SC2016,SC2030,SC2031,SC2329
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
BASHRC=${ROOT}/portage/bashrc
TMP=$(mktemp -d "${TMPDIR:-/tmp}/gentoo-opt-dispatcher.XXXXXX")
trap 'rm -rf -- "${TMP}"' EXIT HUP INT TERM

PASS=0
FAIL=0

run_case() {
    local name=$1
    shift
    if ( "$@" ); then
        printf 'PASS: %s\n' "${name}"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s\n' "${name}" >&2
        FAIL=$((FAIL + 1))
    fi
}

count_token() {
    local value=$1 token=$2 count=0 word
    for word in ${value}; do
        [[ ${word} == "${token}" ]] && count=$((count + 1))
    done
    printf '%s\n' "${count}"
}

mkdir -p "${TMP}/bin" "${TMP}/profiles/raw-clang" "${TMP}/profiles/raw-gcc" \
    "${TMP}/profiles/raw-rust"

for compiler in clang gcc rustc go; do
    compiler_path=${TMP}/bin/${compiler}
    case ${compiler} in
        clang) body='printf "%s\n" "clang version 22.1.8"' ;;
        gcc) body='printf "%s\n" "gcc (Gentoo fake) 15.1.0"' ;;
        rustc) body='printf "%s\n" "rustc 1.90.0"' ;;
        go) body='if [[ ${1-} == tool ]]; then [[ ${2-} == pprof && ${3-} == -top && -s ${4-} ]]; else [[ ${1-} == version ]] && printf "%s\n" "go version go1.27 linux/amd64"; fi' ;;
    esac
    printf '#!/usr/bin/env bash\nset -euo pipefail\n%s\n' "${body}" > "${compiler_path}"
    chmod +x "${compiler_path}"
done

printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    '[[ ${1-} == show ]]' \
    'if [[ ${2-} == --sample ]]; then' \
    '    grep -qx SAMPLE "${3}"' \
    'else' \
    '    grep -qx IR "${2}"' \
    'fi' > "${TMP}/bin/llvm-profdata"
printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    '[[ ${1-} == overlap && -d ${2-} && ${2-} == ${3-} ]]' \
    > "${TMP}/bin/gcov-tool"
chmod +x "${TMP}/bin/llvm-profdata" "${TMP}/bin/gcov-tool"

FINGERPRINT=$(printf 'a%.0s' {1..64})

write_manifest_file() {
    local output=$1 backend=$2 family=$3 profile=$4 abi=${5:-amd64}
    local digest
    digest=$(sha256sum -- "${profile}")
    digest=${digest%% *}
    printf '%s\n' \
        'schema=gentoo-optimization-profile-v1' \
        "backend=${backend}" \
        "fingerprint=${FINGERPRINT}" \
        "abi=${abi}" \
        "compiler_family=${family}" \
        "profile_path=${profile}" \
        "profile_sha256=${digest}" \
        'validation_status=passed' > "${output}"
}

case_off_is_noop() (
    CFLAGS='c-before'; CXXFLAGS='cxx-before'; LDFLAGS='ld-before'
    FCFLAGS='fc-before'; FFLAGS='ff-before'; FEATURES='ccache sandbox'
    source "${BASHRC}"
    [[ ${CFLAGS} == c-before && ${CXXFLAGS} == cxx-before &&
        ${LDFLAGS} == ld-before && ${FCFLAGS} == fc-before &&
        ${FFLAGS} == ff-before && ${FEATURES} == 'ccache sandbox' ]]
)

case_legacy_rejected() (
    PGO_INSTRUMENT=1 source "${BASHRC}" >/dev/null 2>&1 && return 1
    PGO_USE_IF_AVAILABLE=1 source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_unknown_mode_rejected() (
    GENTOO_OPT_MODE=guess-profile source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_compiler_lane_rejected() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=gcc ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_abi_separation() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=x86
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_missing_profile_rejected() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/absent.profdata"
    export GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/absent.manifest"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

printf '%s\n' IR > "${TMP}/profiles/ir.profdata"
printf '%s\n' SAMPLE > "${TMP}/profiles/sample.prof"
write_manifest_file "${TMP}/profiles/ir.manifest" clang-ir clang "${TMP}/profiles/ir.profdata"
write_manifest_file "${TMP}/profiles/sample.manifest" clang-sample clang "${TMP}/profiles/sample.prof"

case_ir_use_and_exact_once() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang LLVM_PROFDATA=llvm-profdata ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/ir.profdata"
    export GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/ir.manifest"
    CFLAGS='-O2'; CXXFLAGS='-O2'; LDFLAGS='-Wl,--as-needed'
    FCFLAGS='fortran-c'; FFLAGS='fortran-f'; FEATURES='ccache sandbox'
    SANDBOX_WRITE='/existing/write'
    source "${BASHRC}" >/dev/null 2>&1
    source "${BASHRC}" >/dev/null 2>&1
    local_flag="-fprofile-use=${GENTOO_OPT_PROFILE_PATH}"
    [[ $(count_token "${CFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${CXXFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${LDFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${FEATURES}" -ccache) == 1 && ${CCACHE_DISABLE} == 1 ]]
    [[ ${FCFLAGS} == fortran-c && ${FFLAGS} == fortran-f ]]
    [[ ${SANDBOX_WRITE} == /existing/write ]]
)

case_sample_use_and_format_separation() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang LLVM_PROFDATA=llvm-profdata ABI=amd64
    export GENTOO_OPT_MODE=clang-sample-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/sample.prof"
    export GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/sample.manifest"
    CFLAGS='-O2'; CXXFLAGS='-O2'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${CFLAGS} == *"-fprofile-sample-use=${GENTOO_OPT_PROFILE_PATH}"* ]]
    [[ ${CFLAGS} != *'-fprofile-use='* && ${LDFLAGS} == ld ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]

    # A correctly hashed sample payload cannot pass through the IR validator.
    sed 's/backend=clang-sample/backend=clang-ir/' \
        "${TMP}/profiles/sample.manifest" > "${TMP}/profiles/sample-as-ir.manifest"
    export GENTOO_OPT_MODE=clang-ir-use
    export GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/sample-as-ir.manifest"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_manifest_mismatch_rejected() (
    sed 's/abi=amd64/abi=x86/' "${TMP}/profiles/ir.manifest" > "${TMP}/profiles/ir-x86.manifest"
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang LLVM_PROFDATA=llvm-profdata ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/ir.profdata"
    export GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/ir-x86.manifest"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_clang_generate_exact_once() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'; FEATURES='ccache'
    SANDBOX_WRITE='/existing/write'
    source "${BASHRC}" >/dev/null 2>&1
    source "${BASHRC}" >/dev/null 2>&1
    local_flag="-fprofile-instr-generate=${GENTOO_OPT_PROFILE_PATH}/%m-%p.profraw"
    [[ $(count_token "${CFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${CXXFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${LDFLAGS}" "${local_flag}") == 1 ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
    [[ ${SANDBOX_WRITE} == "/existing/write:${GENTOO_OPT_PROFILE_PATH}" ]]
)

printf 'gcda\n' > "${TMP}/profiles/raw-gcc/unit.gcda"

case_gcc_use_isolated_correction() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=gcc GCOV_TOOL=gcov-tool ABI=amd64
    GENTOO_OPT_MODE=off source "${BASHRC}"
    digest=$(gentoo_opt_hash_profile_directory "${TMP}/profiles/raw-gcc")
    printf '%s\n' \
        'schema=gentoo-optimization-profile-v1' \
        'backend=gcc' \
        "fingerprint=${FINGERPRINT}" \
        'abi=amd64' \
        'compiler_family=gcc' \
        "profile_path=${TMP}/profiles/raw-gcc" \
        "profile_sha256=${digest}" \
        'validation_status=passed' > "${TMP}/profiles/gcc.manifest"
    export GENTOO_OPT_MODE=gcc-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-gcc"
    export GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/gcc.manifest"
    CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${CFLAGS} == *-fprofile-correction* && ${CXXFLAGS} == *-fprofile-correction* ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
)

case_rust_target_isolation() (
    export PATH="${TMP}/bin:/usr/bin:/bin" RUSTC=rustc ABI=amd64
    export GENTOO_OPT_MODE=rust-generate GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-rust"
    export GENTOO_OPT_RUST_TARGET=x86_64-unknown-linux-gnu
    RUSTFLAGS='-Copt-level=3'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${RUSTFLAGS} == *"-Cprofile-generate=${GENTOO_OPT_PROFILE_PATH}"* ]]
    [[ ${CARGO_BUILD_TARGET} == x86_64-unknown-linux-gnu ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
)

printf '%s\n' GO > "${TMP}/profiles/cpu.pprof"
write_manifest_file "${TMP}/profiles/go.manifest" go go "${TMP}/profiles/cpu.pprof"

case_go_use_only_goflags() (
    export PATH="${TMP}/bin:/usr/bin:/bin" GO=go ABI=amd64
    go --version >/dev/null 2>&1 && return 1
    export GENTOO_OPT_MODE=go-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/cpu.pprof"
    export GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/go.manifest"
    GOFLAGS='-trimpath'; CFLAGS='c'; CXXFLAGS='cxx'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${GOFLAGS} == *"-pgo=${GENTOO_OPT_PROFILE_PATH}"* ]]
    [[ ${CFLAGS} == c && ${CXXFLAGS} == cxx && ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
)

case_rust_and_go_bolt_layering() (
    export PATH="${TMP}/bin:/usr/bin:/bin" RUSTC=rustc ABI=amd64
    export GENTOO_OPT_MODE=rust-generate GENTOO_OPT_BOLT_STAGE=capture
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-rust"
    export GENTOO_OPT_RUST_TARGET=x86_64-unknown-linux-gnu
    RUSTFLAGS='-Copt-level=3'; CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${RUSTFLAGS} == *'-Clink-arg=-Wl,--emit-relocs'* ]]
    [[ ${RUSTFLAGS} == *'-Clink-arg=-Wl,--build-id=sha1'* ]]
    [[ ${CFLAGS} == c && ${CXXFLAGS} == cxx && ${LDFLAGS} == ld ]]

    export GO=go GENTOO_OPT_MODE=go-use
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/cpu.pprof"
    export GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/go.manifest"
    unset RUSTC GENTOO_OPT_RUST_TARGET CARGO_BUILD_TARGET
    GOFLAGS='-trimpath'; CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${GOFLAGS} == *"-pgo=${GENTOO_OPT_PROFILE_PATH}"* ]]
    [[ ${CFLAGS} == c && ${CXXFLAGS} == cxx && ${LDFLAGS} == ld ]]
)

case_bolt_layer_and_gcc_guard() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_BOLT_STAGE=capture
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${CFLAGS} == *-g1* && ${LDFLAGS} == *-Wl,--emit-relocs* ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]

    export CC=gcc GENTOO_OPT_MODE=gcc-generate
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-gcc"
    unset GENTOO_OPT_BOLT_GCC_READY
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_bolt_cache_scope_is_required() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_BOLT_STAGE=capture
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    unset GENTOO_OPT_BOLT_CACHE_ROOT
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/invalid:cache"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_fingerprint_file_strict() (
    printf 'fingerprint=%s\n' "${FINGERPRINT}" > "${TMP}/fingerprint.env"
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_ABI=amd64
    unset GENTOO_OPT_FINGERPRINT
    export GENTOO_OPT_FINGERPRINT_FILE="${TMP}/fingerprint.env"
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${GENTOO_OPT_ACTIVE_FINGERPRINT} == "${FINGERPRINT}" ]]
)

case_root_path_is_not_safe_identity() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT_FILE=/
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_bolt_post_install_wrapper() (
    mkdir -p "${TMP}/ed" "${TMP}/bolt-cache"
    printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
        'printf "%s\n" "$@" > "${BOLT_WRAPPER_EVIDENCE}"' \
        > "${TMP}/bin/capture-wrapper"
    chmod +x "${TMP}/bin/capture-wrapper"
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=bolt-capture GENTOO_OPT_COMPILER_FAMILY=clang
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
    export GENTOO_OPT_BOLT_CAPTURE_TOOL="${TMP}/bin/capture-wrapper"
    export BOLT_WRAPPER_EVIDENCE="${TMP}/wrapper.args" ED="${TMP}/ed"
    source "${BASHRC}" >/dev/null 2>&1
    post_src_install
    mapfile -t arguments < "${BOLT_WRAPPER_EVIDENCE}"
    [[ ${arguments[*]} == "--ed ${ED} --cache-root ${GENTOO_OPT_BOLT_CACHE_ROOT} --fingerprint ${FINGERPRINT}" ]]
)

case_existing_post_install_hook_is_chained() (
    post_src_install() {
        printf '%s\n' 'previous hook ran' >> "${TMP}/previous-hook.log"
    }
    source "${BASHRC}" >/dev/null 2>&1
    # An accidental second source must not capture our wrapper as its own
    # predecessor and recurse.
    source "${BASHRC}" >/dev/null 2>&1
    post_src_install
    [[ $(<"${TMP}/previous-hook.log") == 'previous hook ran' ]]
)

case_portage_phase_cannot_swallow_bolt_failure() (
    mkdir -p "${TMP}/fatal-ed" "${TMP}/fatal-cache"
    printf '%s\n' '#!/usr/bin/env bash' 'exit 41' > "${TMP}/bin/failing-capture-wrapper"
    chmod +x "${TMP}/bin/failing-capture-wrapper"
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=bolt-capture GENTOO_OPT_COMPILER_FAMILY=clang
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/fatal-cache"
    export GENTOO_OPT_BOLT_CAPTURE_TOOL="${TMP}/bin/failing-capture-wrapper"
    export ED="${TMP}/fatal-ed"
    source "${BASHRC}" >/dev/null 2>&1
    set +e
    (
        die() { exit 97; }
        __emulated_portage_phase() {
            declare -F "$1" >/dev/null && "$1"
            # Real Portage's trailing false if can otherwise turn a failed hook
            # into a successful function return.
            if ((0)); then :; fi
        }
        __emulated_portage_phase post_src_install
    ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 97 ]]
)

case_portage_source_dispatch_failure_is_fatal() (
    set +e
    (
        die() { exit 96; }
        GENTOO_OPT_MODE=unknown-mode source "${BASHRC}"
        exit 0
    ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 96 ]]
)

case_depend_phase_is_external_command_free() (
    export EBUILD_PHASE=depend PATH=/dev/null
    GENTOO_OPT_MODE=clang-ir-use
    GENTOO_OPT_BOLT_STAGE=capture
    GENTOO_OPT_FINGERPRINT_FILE=/does/not/exist
    GENTOO_OPT_PROFILE_PATH=/does/not/exist
    GENTOO_OPT_BOLT_CACHE_ROOT=/does/not/exist
    CFLAGS='depend-c'; CXXFLAGS='depend-cxx'; LDFLAGS='depend-ld'
    RUSTFLAGS='depend-rust'; GOFLAGS='depend-go'; FEATURES='ccache sandbox'
    SANDBOX_WRITE='/depend/write'
    source "${BASHRC}" >/dev/null 2>&1
    [[ ${CFLAGS} == depend-c && ${CXXFLAGS} == depend-cxx && ${LDFLAGS} == depend-ld ]]
    [[ ${RUSTFLAGS} == depend-rust && ${GOFLAGS} == depend-go ]]
    [[ ${FEATURES} == 'ccache sandbox' && ${SANDBOX_WRITE} == /depend/write ]]
    [[ -z ${GENTOO_OPT_ACTIVE_FINGERPRINT-} && -z ${GENTOO_OPT_ACTIVE_BACKEND-} ]]
)

case_depend_phase_invalid_mode_is_fatal() (
    set +e
    (
        die() { exit 95; }
        EBUILD_PHASE=depend PATH=/dev/null GENTOO_OPT_MODE=invalid source "${BASHRC}"
        exit 0
    ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 95 ]]
)

run_case 'off/unset leaves all flags unchanged' case_off_is_noop
run_case 'legacy marker paths fail closed' case_legacy_rejected
run_case 'unknown modes fail closed' case_unknown_mode_rejected
run_case 'Clang mode rejects a GCC compiler' case_compiler_lane_rejected
run_case 'requested ABI cannot cross the Portage ABI' case_abi_separation
run_case 'missing use profile fails closed' case_missing_profile_rejected
run_case 'Clang IR use validates and appends exactly once' case_ir_use_and_exact_once
run_case 'IR and sample profiles remain format/flag separated' case_sample_use_and_format_separation
run_case 'profile manifest ABI mismatch fails closed' case_manifest_mismatch_rejected
run_case 'Clang generation appends exactly once' case_clang_generate_exact_once
run_case 'GCC correction remains isolated from Fortran' case_gcc_use_isolated_correction
run_case 'Rust instrumentation requires target isolation' case_rust_target_isolation
run_case 'Go PGO changes GOFLAGS only' case_go_use_only_goflags
run_case 'Rust and Go BOLT stages remain language-lane specific' case_rust_and_go_bolt_layering
run_case 'BOLT readiness layers and guards the GCC lane' case_bolt_layer_and_gcc_guard
run_case 'BOLT stages require an exact sandbox cache scope' case_bolt_cache_scope_is_required
run_case 'strict fingerprint.env loading works' case_fingerprint_file_strict
run_case 'root is rejected as an identity file path' case_root_path_is_not_safe_identity
run_case 'post_src_install invokes the exact BOLT wrapper interface' case_bolt_post_install_wrapper
run_case 'post_src_install chains an existing Portage hook' case_existing_post_install_hook_is_chained
run_case 'Portage cannot swallow a BOLT transaction failure' case_portage_phase_cannot_swallow_bolt_failure
run_case 'Portage cannot swallow dispatcher failure while sourcing' case_portage_source_dispatch_failure_is_fatal
run_case 'depend phase performs no external command or flag/path mutation' case_depend_phase_is_external_command_free
run_case 'depend phase rejects invalid modes through Portage fatal path' case_depend_phase_invalid_mode_is_fatal

printf 'SUMMARY: pass=%d fail=%d total=%d\n' "${PASS}" "${FAIL}" "$((PASS + FAIL))"
((FAIL == 0))
