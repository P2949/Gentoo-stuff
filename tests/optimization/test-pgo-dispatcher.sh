#!/usr/bin/env bash
# The fixture intentionally writes literal ${...} expressions into fake tools
# and isolates every environment mutation inside a case subshell.
# shellcheck disable=SC1090,SC1091,SC2016,SC2030,SC2031,SC2034,SC2329
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
BASHRC=${ROOT}/portage/bashrc
TMP=$(mktemp -d "${TMPDIR:-/tmp}/gentoo-opt-dispatcher.XXXXXX")
trap 'rm -rf -- "${TMP}"' EXIT HUP INT TERM

# Lifecycle-phase tests must exercise the same embedded candidate binding as an
# installed Portage policy.  An unbound checkout policy is deliberately
# rejected once a live framework exists, so using BASHRC directly would make
# the fatal-path cases pass for the wrong reason and the no-op cases fail
# before reaching their phase dispatcher.  Keep this candidate entirely below
# the fixture's private trust anchor so its result is independent of the live
# framework-current state.
FIXTURE_FRAMEWORK_ID=$(printf 'c%.0s' {1..64})
FIXTURE_FRAMEWORK_BASE=${TMP}/framework-root
FIXTURE_FRAMEWORK_TARGET=${FIXTURE_FRAMEWORK_BASE}/framework-${FIXTURE_FRAMEWORK_ID}
BOUND_BASHRC=${FIXTURE_FRAMEWORK_TARGET}/portage/bashrc

render_candidate_bound_bashrc() {
    local line found=0
    while IFS= read -r line || [[ -n ${line} ]]; do
        if [[ ${line} == '# GENTOO_OPT_FRAMEWORK_BINDING_PLACEHOLDER' ]]; then
            found=$((found + 1))
            printf 'gentoo_opt_embedded_framework_target=%q\n' "${FIXTURE_FRAMEWORK_TARGET}"
            printf 'gentoo_opt_embedded_framework_base=%q\n' "${FIXTURE_FRAMEWORK_BASE}"
            printf 'gentoo_opt_embedded_framework_trust_anchor=%q\n' "${TMP}"
            printf 'gentoo_opt_embedded_framework_expected_uid=%q\n' "${EUID}"
        else
            printf '%s\n' "${line}"
        fi
    done <"${BASHRC}"
    ((found == 1))
}

mkdir -p -- "${FIXTURE_FRAMEWORK_TARGET}/portage"
render_candidate_bound_bashrc >"${BOUND_BASHRC}"
chmod 0755 -- "${FIXTURE_FRAMEWORK_BASE}" "${FIXTURE_FRAMEWORK_TARGET}" \
    "${FIXTURE_FRAMEWORK_TARGET}/portage"
chmod 0644 -- "${BOUND_BASHRC}"

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

assert_stage_readiness_absent() {
    local variable value flag
    for variable in CFLAGS CXXFLAGS FCFLAGS FFLAGS; do
        value=${!variable-}
        for flag in -gline-tables-only -g1 -fdebug-info-for-profiling \
            -funique-internal-linkage-names -fpseudo-probe-for-profiling \
            -ffunction-sections -fdata-sections \
            -fno-reorder-blocks-and-partition; do
            [[ $(count_token "${value}" "${flag}") == 0 ]] || return 1
        done
        [[ ${value} != *--emit-relocs* && ${value} != *--build-id* ]] || return 1
    done
    value=${LDFLAGS-}
    [[ ${value} != *--emit-relocs* && ${value} != *--build-id* ]] || return 1
    value=${RUSTFLAGS-}
    [[ ${value} != *debuginfo=1* && ${value} != *--emit-relocs* &&
        ${value} != *--build-id* ]] || return 1
}

mkdir -p "${TMP}/bin" "${TMP}/profiles/raw-clang" "${TMP}/profiles/raw-gcc" \
    "${TMP}/profiles/raw-rust" "${TMP}/wrappers" "${TMP}/real-wrapper"

for compiler in clang clang++ gcc g++ rustc go; do
    compiler_path=${TMP}/bin/${compiler}
    case ${compiler} in
        clang|clang++) body='printf "%s\n" "clang version 22.1.8"' ;;
        gcc) body='printf "%s\n" "gcc (Gentoo fake) 15.1.0"' ;;
        g++) body='printf "%s\n" "g++ (Gentoo fake) 15.1.0"' ;;
        rustc) body='printf "%s\n" "rustc 1.90.0"' ;;
        go) body='if [[ ${1-} == tool ]]; then [[ ${2-} == pprof && ${3-} == -top && -s ${4-} ]]; else [[ ${1-} == version ]] && printf "%s\n" "go version go1.27 linux/amd64"; fi' ;;
    esac
    printf '#!/usr/bin/env bash\nset -euo pipefail\n%s\n' "${body}" > "${compiler_path}"
    chmod +x "${compiler_path}"
done
ln -s -- clang++ "${TMP}/bin/c++"
printf '%s\n' '#!/usr/bin/env bash' \
    'printf invoked > "${CACHE_WRAPPER_MARKER}"' 'exit 88' \
    > "${TMP}/real-wrapper/ccache"
chmod +x "${TMP}/real-wrapper/ccache"
ln -s -- "${TMP}/real-wrapper/ccache" "${TMP}/wrappers/clang"
ln -s -- "${TMP}/real-wrapper/ccache" "${TMP}/wrappers/clang++"

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

printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    '[[ $# == 5 && $1 == verify && $2 == --manifest && $4 == --metadata ]]' \
    '[[ $5 == "$3.metadata.json" ]]' \
    '/usr/bin/jq -e '\''(.compiler.path | type) == "string" and (.compiler.sha256 | test("^[0-9a-f]{64}$"))'\'' "$5" >/dev/null' \
    'backend=$(sed -n "s/^backend=//p" "$3")' \
    'profile=$(sed -n "s/^profile_path=//p" "$3")' \
    'case ${backend} in' \
    '  clang-ir|rust) grep -Fxq IR "${profile}" ;;' \
    '  clang-sample) grep -Fxq SAMPLE "${profile}" ;;' \
    '  gcc) [[ -d ${profile} ]] ;;' \
    '  go) grep -Fxq GO "${profile}" ;;' \
    '  *) exit 1 ;;' \
    'esac' \
    'printf "%s\\n" "$*" >> "${VALIDATOR_LOG}"' \
    > "${TMP}/bin/profile-validator"
chmod +x "${TMP}/bin/profile-validator"
printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    '[[ $# == 3 && $1 == fingerprint && $2 == --input ]]' \
    'grep -Fxq IDENTITY_OK "$3"' \
    'printf "b%.0s" {1..64}' 'printf "\n"' > "${TMP}/bin/profile-key-tool"
chmod +x "${TMP}/bin/profile-key-tool"
export GENTOO_OPT_DISPATCHER_TEST_MODE=1
export GENTOO_OPT_PROFILE_VALIDATOR="${TMP}/bin/profile-validator"
export VALIDATOR_LOG="${TMP}/profile-validator.log"

FINGERPRINT=$(printf 'a%.0s' {1..64})
export GENTOO_OPT_BOLT_EXPECTED_ELIGIBLE_COUNT=1
printf '%s\n' '{"fixture":"inventory-proof"}' > "${TMP}/bolt-inventory-proof.json"
export GENTOO_OPT_BOLT_INVENTORY_PROOF="${TMP}/bolt-inventory-proof.json"
unset CFLAGS CXXFLAGS FCFLAGS FFLAGS LDFLAGS RUSTFLAGS GOFLAGS FEATURES \
    GENTOO_OPT_FRAMEWORK_TARGET

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
    write_metadata_file "${output}.metadata.json" "${family}"
}

write_metadata_file() {
    local output=$1 family=$2 compiler digest
    case ${family} in
        clang) compiler=${TMP}/bin/clang ;;
        gcc) compiler=${TMP}/bin/gcc ;;
        rust) compiler=${TMP}/bin/rustc ;;
        go) compiler=${TMP}/bin/go ;;
        *) return 1 ;;
    esac
    digest=$(sha256sum -- "${compiler}")
    digest=${digest%% *}
    /usr/bin/jq -n --arg path "${compiler}" --arg sha256 "${digest}" \
        '{compiler: {path: $path, sha256: $sha256}}' > "${output}"
    chmod 0600 -- "${output}"
}

select_manifest() {
    export GENTOO_OPT_PROFILE_MANIFEST=$1
    export GENTOO_OPT_PROFILE_METADATA=$1.metadata.json
}

case_off_is_noop() (
    CFLAGS='c-before'; CXXFLAGS='cxx-before'; LDFLAGS='ld-before'
    FCFLAGS='fc-before'; FFLAGS='ff-before'; FEATURES='ccache sandbox'
    source "${BASHRC}" || return 1
    [[ ${CFLAGS} == c-before && ${CXXFLAGS} == cxx-before &&
        ${LDFLAGS} == ld-before && ${FCFLAGS} == fc-before &&
        ${FFLAGS} == ff-before && ${FEATURES} == 'ccache sandbox' ]]
    assert_stage_readiness_absent
)

case_framework_activation_journal_fails_closed() (
    local journal=${TMP}/framework-activation.pending
    : >"${journal}"
    CFLAGS=before CXXFLAGS=before LDFLAGS=before FCFLAGS=before FFLAGS=before
    GENTOO_OPT_DISPATCHER_TEST_MODE=1 \
        GENTOO_OPT_TEST_FRAMEWORK_ACTIVATION_JOURNAL=${journal} \
        source "${BASHRC}" >/dev/null 2>&1 && return 1
    rm -f -- "${journal}"
    GENTOO_OPT_DISPATCHER_TEST_MODE=1 \
        GENTOO_OPT_TEST_FRAMEWORK_ACTIVATION_JOURNAL=${journal} \
        GENTOO_OPT_MODE=off source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${CFLAGS} == before && ${CXXFLAGS} == before && ${LDFLAGS} == before &&
        ${FCFLAGS} == before && ${FFLAGS} == before ]]
)

case_profile_transaction_journal_authorization_is_fail_closed() (
    local root=${TMP}/profile-transaction journal authorization helper scanner child
    local framework_lock project_lock generation_lock generation inventory inventory_sha run
    local artifacts profiles evidence scan_output marker receipt stale_token
    root+=-${BASHPID}
    mkdir -m 0700 -- "${root}"
    mkdir -m 0700 -- "${root}/run" "${root}/state" "${root}/artifacts" \
        "${root}/profile-artifacts" "${root}/evidence-output" \
        "${root}/generation-state"
    framework_lock=${root}/run/framework-install.lock
    project_lock=${root}/run/project.lock
    generation_lock=${root}/run/generation.lock
    journal=${root}/state/phase-2-production-profile-locks.pending
    helper=${root}/production-profile-lock-transaction.py
    scanner=${root}/authorization-token-scan.py
    generation=dispatcher-transaction-test
    inventory=dispatcher-transaction-inventory
    inventory_sha=$(printf dispatcher-inventory | sha256sum); inventory_sha=${inventory_sha%% *}
    run=dispatcher-transaction-run
    authorization=${root}/generation-state/${generation}/phase2-sample-gate-${run}/transaction.authorization
    artifacts=${root}/artifacts
    profiles=${root}/profile-artifacts
    evidence=${root}/evidence-output
    scan_output=${authorization%/*}/coordinator-token-scan.tsv
    marker=${artifacts}/dispatcher-authorized
    receipt=${root}/state/phase-2-production-profile-locks-${generation}.receipt.json
    child=${root}/authorized-child.sh
    for lock in "${framework_lock}" "${project_lock}" "${generation_lock}"; do
        : >"${lock}"
        chmod 0600 -- "${lock}"
    done
    cp -- "${ROOT}/scripts/optimization/pgo/production-profile-lock-transaction.py" "${helper}"
    cp -- "${ROOT}/scripts/optimization/pgo/authorization-token-scan.py" "${scanner}"
    chmod 0700 -- "${helper}" "${scanner}"

    cat >"${child}" <<EOF
#!/bin/bash
set -euo pipefail
token=\${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN}
authorization=\${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION}
unset GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION
source_with() {
    local supplied_token=\$1 supplied_authorization=\$2
    GENTOO_OPT_DISPATCHER_TEST_MODE=1 \
    GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_ROOT=${root@Q} \
    GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_FRAMEWORK_LOCK=${framework_lock@Q} \
    GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_PROJECT_LOCK=${project_lock@Q} \
    GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_GENERATION_LOCK=${generation_lock@Q} \
    GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_JOURNAL=${journal@Q} \
    GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_HELPER=${helper@Q} \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN=\${supplied_token} \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION=\${supplied_authorization} \
    GENTOO_OPT_MODE=off source ${BOUND_BASHRC@Q} >/dev/null 2>&1
}
( source_with "\$(printf '0%.0s' {1..64})" "\${authorization}" ) && exit 31
( source_with "\${token}" ${TMP@Q}/outside.authorization ) && exit 32
source_with "\${token}" "\${authorization}"
[[ -z \${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN-} &&
   -z \${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION-} &&
   \${GENTOO_OPT_FRAMEWORK_TARGET-} == ${FIXTURE_FRAMEWORK_TARGET@Q} ]]
/usr/bin/env | /usr/bin/grep -q '^GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_' && exit 33
printf 'authorized\n' >${marker@Q}
EOF
    chmod 0700 -- "${child}"

    /usr/bin/env -i HOME="${HOME}" USER="${USER:-fixture}" LOGNAME="${LOGNAME:-fixture}" \
        SHELL=/bin/bash PATH=/usr/bin:/bin LANG=C LC_ALL=C TZ=UTC \
        PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I "${helper}" run \
        --test-mode --test-root "${root}" \
        --test-framework-lock "${framework_lock}" \
        --test-project-lock "${project_lock}" \
        --test-generation-lock "${generation_lock}" \
        --test-journal "${journal}" --lock-timeout-seconds 2 \
        --generation-id "${generation}" --inventory-id "${inventory}" \
        --inventory-sha256 "${inventory_sha}" --gate-run-id "${run}" \
        --child-timeout-seconds 20 --kill-after-seconds 1 \
        --token-scanner "${scanner}" \
        --token-scan-root "${artifacts}" --token-scan-root "${profiles}" \
        --token-scan-root "${authorization%/*}" \
        --token-scan-root "${evidence}" --token-scan-output "${scan_output}" \
        --evidence-output-root "${evidence}" -- "${child}" >/dev/null || return 1
    [[ $(<"${marker}") == authorized && -f ${receipt} && ! -e ${journal} &&
        ! -s ${framework_lock} && ! -s ${project_lock} && ! -s ${generation_lock} ]] || \
        return 1
    /usr/bin/jq -e '.status == "passed" and .child_exit_status == 0' \
        "${receipt}" >/dev/null || return 1

    stale_token=$(printf 'b%.0s' {1..64})
    (
        GENTOO_OPT_DISPATCHER_TEST_MODE=1 \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_ROOT=${root} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_FRAMEWORK_LOCK=${framework_lock} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_PROJECT_LOCK=${project_lock} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_GENERATION_LOCK=${generation_lock} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_JOURNAL=${journal} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_HELPER=${helper} \
        GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN=${stale_token} \
        GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION=${authorization} \
        GENTOO_OPT_MODE=off source "${BOUND_BASHRC}" >/dev/null 2>&1
    ) && return 1
    : >"${journal}.partial"
    (
        GENTOO_OPT_DISPATCHER_TEST_MODE=1 \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_ROOT=${root} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_FRAMEWORK_LOCK=${framework_lock} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_PROJECT_LOCK=${project_lock} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_GENERATION_LOCK=${generation_lock} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_JOURNAL=${journal} \
        GENTOO_OPT_TEST_PRODUCTION_PROFILE_TRANSACTION_HELPER=${helper} \
        GENTOO_OPT_MODE=off source "${BOUND_BASHRC}" >/dev/null 2>&1
    ) && return 1
    return 0
)

case_repository_ordinary_flags_are_stage_clean() (
    LDFLAGS='' RUSTFLAGS='' FEATURES=''
    # make.conf is shell assignment syntax; source the repository policy to
    # test the effective ordinary C, C++, Fortran, linker, and Rust lanes.
    source "${ROOT}/portage/make.conf"
    GENTOO_OPT_MODE=off source "${BASHRC}" >/dev/null 2>&1 || return 1
    assert_stage_readiness_absent
    [[ -z ${BOLT_READY_FLAGS+x} && -z ${PROFILE_MAPPING_FLAGS+x} &&
        -z ${SECTION_FLAGS+x} && -z ${BOLT_READY_LD_FLAGS+x} ]]
)

case_profile_map_stage_is_exact() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang CXX=clang++ ABI=amd64
    export GENTOO_OPT_MODE=off GENTOO_OPT_COMPILER_FAMILY=clang
    export GENTOO_OPT_PROFILE_MAP_READY=1 GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
    RUSTFLAGS='rust'; GOFLAGS='go'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    for flag in -gline-tables-only -fdebug-info-for-profiling \
        -funique-internal-linkage-names -fpseudo-probe-for-profiling; do
        [[ $(count_token "${CFLAGS}" "${flag}") == 1 ]] || return 1
        [[ $(count_token "${CXXFLAGS}" "${flag}") == 1 ]] || return 1
    done
    [[ $(count_token "${LDFLAGS}" -Wl,--build-id=sha1) == 1 ]]
    [[ ${LDFLAGS} != *--emit-relocs* && ${CFLAGS} != *-mllvm=* ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff &&
        ${RUSTFLAGS} == rust && ${GOFLAGS} == go ]]
)

case_stage_build_id_policy_fails_closed() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang CXX=clang++ ABI=amd64
    export GENTOO_OPT_MODE=off GENTOO_OPT_COMPILER_FAMILY=clang
    export GENTOO_OPT_PROFILE_MAP_READY=1 GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='-Wl,--build-id'
    source "${BASHRC}" >/dev/null 2>&1 && return 1

    CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='-Wl,--build-id=sha1 -Wl,--build-id=sha1'
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
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

case_mixed_c_cxx_tuple_rejected() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang CXX=g++ ABI=amd64
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
    select_manifest "${TMP}/profiles/absent.manifest"
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
    select_manifest "${TMP}/profiles/ir.manifest"
    CFLAGS='-O2'; CXXFLAGS='-O2'; LDFLAGS='-Wl,--as-needed'
    FCFLAGS='fortran-c'; FFLAGS='fortran-f'; FEATURES='ccache sandbox'
    SANDBOX_WRITE='/existing/write'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    local_flag="-fprofile-use=${GENTOO_OPT_PROFILE_PATH}"
    [[ $(count_token "${CFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${CXXFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${LDFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${FEATURES}" -ccache) == 1 && ${CCACHE_DISABLE} == 1 ]]
    [[ ${FCFLAGS} == fortran-c && ${FFLAGS} == fortran-f ]]
    [[ ${SANDBOX_WRITE} == /existing/write ]]
    assert_stage_readiness_absent
    grep -Fxq "verify --manifest ${GENTOO_OPT_PROFILE_MANIFEST} --metadata ${GENTOO_OPT_PROFILE_METADATA}" \
        "${VALIDATOR_LOG}"
)

case_sample_use_and_format_separation() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang LLVM_PROFDATA=llvm-profdata ABI=amd64
    export GENTOO_OPT_MODE=clang-sample-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/sample.prof"
    select_manifest "${TMP}/profiles/sample.manifest"
    CFLAGS='-O2'; CXXFLAGS='-O2'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${CFLAGS} == *"-fprofile-sample-use=${GENTOO_OPT_PROFILE_PATH}"* ]]
    [[ ${CFLAGS} != *'-fprofile-use='* && ${LDFLAGS} == ld ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
    assert_stage_readiness_absent

    # A correctly hashed sample payload cannot pass through the IR validator.
    sed 's/backend=clang-sample/backend=clang-ir/' \
        "${TMP}/profiles/sample.manifest" > "${TMP}/profiles/sample-as-ir.manifest"
    write_metadata_file "${TMP}/profiles/sample-as-ir.manifest.metadata.json" clang
    export GENTOO_OPT_MODE=clang-ir-use
    select_manifest "${TMP}/profiles/sample-as-ir.manifest"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_manifest_mismatch_rejected() (
    sed 's/abi=amd64/abi=x86/' "${TMP}/profiles/ir.manifest" > "${TMP}/profiles/ir-x86.manifest"
    write_metadata_file "${TMP}/profiles/ir-x86.manifest.metadata.json" clang
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang LLVM_PROFDATA=llvm-profdata ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/ir.profdata"
    select_manifest "${TMP}/profiles/ir-x86.manifest"
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_exact_profile_compiler_identity_is_bound() (
    cp -- "${TMP}/profiles/ir.manifest" "${TMP}/profiles/ir-other-compiler.manifest"
    other_hash=$(sha256sum -- "${TMP}/bin/clang++")
    other_hash=${other_hash%% *}
    /usr/bin/jq -n --arg path "${TMP}/bin/clang++" --arg sha256 "${other_hash}" \
        '{compiler: {path: $path, sha256: $sha256}}' \
        > "${TMP}/profiles/ir-other-compiler.manifest.metadata.json"
    chmod 0600 -- "${TMP}/profiles/ir-other-compiler.manifest.metadata.json"
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang LLVM_PROFDATA=llvm-profdata ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/ir.profdata"
    select_manifest "${TMP}/profiles/ir-other-compiler.manifest"
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
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    local_flag="-fprofile-instr-generate=${GENTOO_OPT_PROFILE_PATH}/%m-%p.profraw"
    [[ $(count_token "${CFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${CXXFLAGS}" "${local_flag}") == 1 ]]
    [[ $(count_token "${LDFLAGS}" "${local_flag}") == 1 ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
    [[ ${SANDBOX_WRITE} == "/existing/write:${GENTOO_OPT_PROFILE_PATH}" ]]
    assert_stage_readiness_absent
)

case_compiler_masquerades_are_bypassed() (
    export PATH="${TMP}/wrappers:${TMP}/bin:/usr/bin:/bin" CC=clang CXX=clang++ ABI=amd64
    export CACHE_WRAPPER_MARKER="${TMP}/cache-wrapper-invoked"
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    FEATURES='ccache distcc icecream sandbox'; RUSTC_WRAPPER=/untrusted/sccache
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${CC} == "${TMP}/bin/clang" && ${CXX} == "${TMP}/bin/clang++" ]]
    [[ ${CCACHE_DISABLE} == 1 && ${SCCACHE_DISABLE} == 1 && -z ${RUSTC_WRAPPER} ]]
    [[ $(count_token "${FEATURES}" -ccache) == 1 ]]
    [[ $(count_token "${FEATURES}" -distcc) == 1 ]]
    [[ $(count_token "${FEATURES}" -icecream) == 1 ]]
    [[ ! -e ${CACHE_WRAPPER_MARKER} ]]
)

printf 'gcda\n' > "${TMP}/profiles/raw-gcc/unit.gcda"

case_gcc_use_isolated_correction() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=gcc CXX=g++ GCOV_TOOL=gcov-tool ABI=amd64
    GENTOO_OPT_MODE=off source "${BASHRC}" || return 1
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
    write_metadata_file "${TMP}/profiles/gcc.manifest.metadata.json" gcc
    export GENTOO_OPT_MODE=gcc-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-gcc"
    select_manifest "${TMP}/profiles/gcc.manifest"
    CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${CFLAGS} == *-fprofile-correction* && ${CXXFLAGS} == *-fprofile-correction* ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
    assert_stage_readiness_absent
)

case_rust_target_isolation() (
    export PATH="${TMP}/bin:/usr/bin:/bin" RUSTC=rustc ABI=amd64
    export GENTOO_OPT_MODE=rust-generate GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-rust"
    export GENTOO_OPT_RUST_TARGET=x86_64-unknown-linux-gnu
    RUSTFLAGS='-Copt-level=3'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${RUSTFLAGS} == *"-Cprofile-generate=${GENTOO_OPT_PROFILE_PATH}"* ]]
    [[ ${CARGO_BUILD_TARGET} == x86_64-unknown-linux-gnu ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
    assert_stage_readiness_absent
)

printf '%s\n' GO > "${TMP}/profiles/cpu.pprof"
write_manifest_file "${TMP}/profiles/go.manifest" go go "${TMP}/profiles/cpu.pprof"

case_go_use_only_goflags() (
    export PATH="${TMP}/bin:/usr/bin:/bin" GO=go ABI=amd64
    go --version >/dev/null 2>&1 && return 1
    export GENTOO_OPT_MODE=go-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_GO_MAIN_COUNT=1 GENTOO_OPT_GO_BINARY=fixture
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/cpu.pprof"
    select_manifest "${TMP}/profiles/go.manifest"
    GOFLAGS='-trimpath'; CFLAGS='c'; CXXFLAGS='cxx'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${GOFLAGS} == *"-pgo=${GENTOO_OPT_PROFILE_PATH}"* ]]
    [[ ${CFLAGS} == c && ${CXXFLAGS} == cxx && ${FCFLAGS} == fc && ${FFLAGS} == ff ]]
    assert_stage_readiness_absent
)

case_rust_and_go_bolt_layering() (
    export PATH="${TMP}/bin:/usr/bin:/bin" RUSTC=rustc ABI=amd64
    export GENTOO_OPT_MODE=rust-generate GENTOO_OPT_BOLT_STAGE=capture
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-rust"
    export GENTOO_OPT_RUST_TARGET=x86_64-unknown-linux-gnu
    RUSTFLAGS='-Copt-level=3'; CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${RUSTFLAGS} == *'-Clink-arg=-Wl,--emit-relocs'* ]]
    [[ ${RUSTFLAGS} == *'-Clink-arg=-Wl,--build-id=sha1'* ]]
    [[ ${CFLAGS} == c && ${CXXFLAGS} == cxx && ${LDFLAGS} == ld ]]

    export GO=go GENTOO_OPT_MODE=go-use
    export GENTOO_OPT_GO_MAIN_COUNT=1 GENTOO_OPT_GO_BINARY=fixture
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/cpu.pprof"
    select_manifest "${TMP}/profiles/go.manifest"
    unset RUSTC GENTOO_OPT_RUST_TARGET CARGO_BUILD_TARGET
    GOFLAGS='-trimpath'; CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${GOFLAGS} == *"-pgo=${GENTOO_OPT_PROFILE_PATH}"* ]]
    [[ ${CFLAGS} == c && ${CXXFLAGS} == cxx && ${LDFLAGS} == ld ]]
)

case_rust_bolt_readiness_requires_target() (
    export PATH="${TMP}/bin:/usr/bin:/bin" RUSTC=rustc ABI=amd64
    export GENTOO_OPT_MODE=bolt-capture GENTOO_OPT_COMPILER_FAMILY=rust
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
    unset GENTOO_OPT_RUST_TARGET CARGO_BUILD_TARGET
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    export GENTOO_OPT_RUST_TARGET=x86_64-unknown-linux-gnu
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${RUSTC} == "${TMP}/bin/rustc" ]]
    [[ ${CARGO_BUILD_TARGET} == x86_64-unknown-linux-gnu ]]
)

case_go_multi_main_is_rejected() (
    export PATH="${TMP}/bin:/usr/bin:/bin" GO=go ABI=amd64
    export GENTOO_OPT_MODE=go-use GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/cpu.pprof"
    select_manifest "${TMP}/profiles/go.manifest"
    export GENTOO_OPT_GO_MAIN_COUNT=2 GENTOO_OPT_GO_BINARY=fixture
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    unset GENTOO_OPT_GO_MAIN_COUNT
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_bolt_layer_and_gcc_guard() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_BOLT_STAGE=capture
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ $(count_token "${CFLAGS}" -gline-tables-only) == 1 ]]
    [[ $(count_token "${CXXFLAGS}" -gline-tables-only) == 1 ]]
    [[ $(count_token "${LDFLAGS}" -Wl,--emit-relocs) == 1 ]]
    [[ $(count_token "${LDFLAGS}" -Wl,--build-id=sha1) == 1 ]]
    [[ ${CFLAGS} != *-fdebug-info-for-profiling* &&
        ${CFLAGS} != *-funique-internal-linkage-names* &&
        ${CFLAGS} != *-fpseudo-probe-for-profiling* ]]
    [[ ${FCFLAGS} == fc && ${FFLAGS} == ff ]]

    export CC=gcc CXX=g++ GENTOO_OPT_MODE=gcc-generate
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-gcc"
    unset GENTOO_OPT_BOLT_GCC_READY
    source "${BASHRC}" >/dev/null 2>&1 && return 1
    return 0
)

case_c_bolt_capture_and_deploy_sets_are_exact() (
    local stage
    for stage in capture deploy; do
        (
            export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang CXX=clang++ ABI=amd64
            export GENTOO_OPT_MODE="bolt-${stage}" GENTOO_OPT_COMPILER_FAMILY=clang
            export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
            export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
            unset GENTOO_OPT_BOLT_GCC_READY GENTOO_OPT_PROFILE_MAP_READY
            CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
            RUSTFLAGS='rust'; GOFLAGS='go'
            source "${BASHRC}" >/dev/null 2>&1 || return 1
            [[ $(count_token "${CFLAGS}" -gline-tables-only) == 1 ]]
            [[ $(count_token "${CXXFLAGS}" -gline-tables-only) == 1 ]]
            [[ $(count_token "${LDFLAGS}" -Wl,--emit-relocs) == 1 ]]
            [[ $(count_token "${LDFLAGS}" -Wl,--build-id=sha1) == 1 ]]
            [[ ${CFLAGS} != *-fdebug-info-for-profiling* &&
                ${CFLAGS} != *-funique-internal-linkage-names* &&
                ${CFLAGS} != *-fpseudo-probe-for-profiling* &&
                ${CFLAGS} != *-ffunction-sections* &&
                ${CFLAGS} != *-fdata-sections* ]]
            [[ ${FCFLAGS} == fc && ${FFLAGS} == ff &&
                ${RUSTFLAGS} == rust && ${GOFLAGS} == go ]]
        ) || return 1

        (
            export PATH="${TMP}/bin:/usr/bin:/bin" CC=gcc CXX=g++ ABI=amd64
            export GENTOO_OPT_MODE="bolt-${stage}" GENTOO_OPT_COMPILER_FAMILY=gcc
            export GENTOO_OPT_BOLT_GCC_READY=1 GENTOO_OPT_ABI=amd64
            export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
            export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
            unset GENTOO_OPT_PROFILE_MAP_READY
            CFLAGS='c'; CXXFLAGS='cxx'; LDFLAGS='ld'; FCFLAGS='fc'; FFLAGS='ff'
            RUSTFLAGS='rust'; GOFLAGS='go'
            source "${BASHRC}" >/dev/null 2>&1 || return 1
            [[ $(count_token "${CFLAGS}" -g1) == 1 ]]
            [[ $(count_token "${CXXFLAGS}" -g1) == 1 ]]
            [[ $(count_token "${CFLAGS}" -fno-reorder-blocks-and-partition) == 1 ]]
            [[ $(count_token "${CXXFLAGS}" -fno-reorder-blocks-and-partition) == 1 ]]
            [[ $(count_token "${LDFLAGS}" -Wl,--emit-relocs) == 1 ]]
            [[ $(count_token "${LDFLAGS}" -Wl,--build-id=sha1) == 1 ]]
            [[ ${CFLAGS} != *-gline-tables-only* &&
                ${CFLAGS} != *-fdebug-info-for-profiling* &&
                ${CFLAGS} != *-funique-internal-linkage-names* &&
                ${CFLAGS} != *-fpseudo-probe-for-profiling* ]]
            [[ ${FCFLAGS} == fc && ${FFLAGS} == ff &&
                ${RUSTFLAGS} == rust && ${GOFLAGS} == go ]]
        ) || return 1
    done
)

case_readiness_markers_fail_closed() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=gcc CXX=g++ ABI=amd64
    export GENTOO_OPT_MODE=gcc-generate GENTOO_OPT_ABI=amd64
    export GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-gcc"
    GENTOO_OPT_BOLT_STAGE=off GENTOO_OPT_BOLT_GCC_READY=1 \
        source "${BASHRC}" >/dev/null 2>&1 && return 1
    GENTOO_OPT_BOLT_GCC_READY=2 source "${BASHRC}" >/dev/null 2>&1 && return 1
    GENTOO_OPT_PROFILE_MAP_READY=enabled source "${BASHRC}" >/dev/null 2>&1 && return 1

    export CC=clang CXX=clang++ GENTOO_OPT_MODE=bolt-capture
    export GENTOO_OPT_COMPILER_FAMILY=clang
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
    GENTOO_OPT_BOLT_GCC_READY=1 source "${BASHRC}" >/dev/null 2>&1 && return 1
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

case_bolt_inventory_proof_is_required() (
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=bolt-capture GENTOO_OPT_COMPILER_FAMILY=clang
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/bolt-cache"
    unset GENTOO_OPT_BOLT_INVENTORY_PROOF
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
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${GENTOO_OPT_ACTIVE_FINGERPRINT} == "${FINGERPRINT}" ]]
)

case_fingerprint_identity_tool_is_bounded() (
    printf '%s\n' IDENTITY_OK > "${TMP}/identity.json"
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=clang-ir-generate GENTOO_OPT_ABI=amd64
    unset GENTOO_OPT_FINGERPRINT GENTOO_OPT_FINGERPRINT_FILE
    export GENTOO_OPT_IDENTITY_INPUT="${TMP}/identity.json"
    export GENTOO_OPT_PROFILE_KEY_TOOL="${TMP}/bin/profile-key-tool"
    export GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${GENTOO_OPT_ACTIVE_FINGERPRINT} == $(printf 'b%.0s' {1..64}) ]]
)

case_production_trust_rejects_replaceable_paths() (
    GENTOO_OPT_MODE=off source "${BASHRC}" >/dev/null 2>&1 || return 1
    unset GENTOO_OPT_DISPATCHER_TEST_MODE GENTOO_OPT_PORTAGE_FIXTURE_MODE
    gentoo_opt_trusted_root_executable /usr/bin/jq || return 1
    gentoo_opt_trusted_root_executable "${TMP}/bin/clang" && return 1
    ln -s -- /usr "${TMP}/trusted-link"
    gentoo_opt_trusted_root_executable "${TMP}/trusted-link/bin/jq" && return 1
    return 0
)

case_production_tools_are_exact_and_trusted() (
    GENTOO_OPT_MODE=off source "${BASHRC}" >/dev/null 2>&1 || return 1
    unset GENTOO_OPT_DISPATCHER_TEST_MODE GENTOO_OPT_PORTAGE_FIXTURE_MODE
    GENTOO_OPT_PROFILE_KEY_TOOL="${TMP}/bin/profile-key-tool"
    gentoo_opt_profile_key_tool >/dev/null 2>&1 && return 1
    GENTOO_OPT_PROFILE_VALIDATOR="${TMP}/bin/profile-validator"
    GENTOO_OPT_PROFILE_METADATA="${TMP}/profiles/ir.manifest.metadata.json"
    GENTOO_OPT_PROFILE_MANIFEST="${TMP}/profiles/ir.manifest"
    gentoo_opt_validate_profile_format >/dev/null 2>&1 && return 1
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang CXX=clang++
    gentoo_opt_detect_compiler_family clang >/dev/null 2>&1 && return 1
    return 0
)

case_production_namespaces_fail_closed() (
    GENTOO_OPT_MODE=off source "${BASHRC}" >/dev/null 2>&1 || return 1
    unset GENTOO_OPT_DISPATCHER_TEST_MODE GENTOO_OPT_PORTAGE_FIXTURE_MODE
    gentoo_opt_require_immutable_input "${TMP}/fingerprint.env" \
        /var/lib/gentoo-optimization/generations file fingerprint >/dev/null 2>&1 && return 1
    gentoo_opt_require_immutable_input "${TMP}/profiles/ir.profdata" \
        /var/cache/gentoo-optimization/pgo file profile >/dev/null 2>&1 && return 1
    gentoo_opt_require_generation_spool "${TMP}/profiles/raw-clang" >/dev/null 2>&1 && return 1
    gentoo_opt_require_bolt_cache_root "${TMP}/bolt-cache" >/dev/null 2>&1 && return 1
    GENTOO_OPT_MODE=clang-ir-generate
    GENTOO_OPT_ACTIVE_MODE=clang-ir-generate
    GENTOO_OPT_ACTIVE_BOLT_STAGE=off
    GENTOO_OPT_ACTIVE_BACKEND=clang-ir
    GENTOO_OPT_ACTIVE_ABI=amd64
    GENTOO_OPT_ACTIVE_COMPILER_FAMILY=clang
    GENTOO_OPT_ACTIVE_COMPILER=/usr/bin/jq
    GENTOO_OPT_ACTIVE_FINGERPRINT=${FINGERPRINT}
    GENTOO_OPT_PROFILE_PATH="${TMP}/profiles/raw-clang"
    GENTOO_OPT_LOG_FILE="${TMP}/arbitrary.log"
    gentoo_opt_log_selection >/dev/null 2>&1 && return 1
    return 0
)

case_production_inline_fingerprint_is_rejected() (
    GENTOO_OPT_MODE=off source "${BASHRC}" >/dev/null 2>&1 || return 1
    unset GENTOO_OPT_DISPATCHER_TEST_MODE GENTOO_OPT_PORTAGE_FIXTURE_MODE
    GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    unset GENTOO_OPT_FINGERPRINT_FILE GENTOO_OPT_IDENTITY_INPUT
    gentoo_opt_load_fingerprint >/dev/null 2>&1 && return 1
    return 0
)

case_portage_fixture_override_is_exactly_bounded() (
    GENTOO_OPT_MODE=off source "${BASHRC}" >/dev/null 2>&1 || return 1
    unset GENTOO_OPT_DISPATCHER_TEST_MODE GENTOO_OPT_PORTAGE_FIXTURE_MODE
    CATEGORY=app-test
    PN=phase2-pgo-use-fixture
    EBUILD_PHASE=compile
    EBUILD=/var/tmp/gentoo-phase2-pgo-portage.ABC123/app-test/phase2-pgo-use-fixture/phase2-pgo-use-fixture-1.ebuild
    gentoo_opt_fixture_override_allowed && return 1
    GENTOO_OPT_PORTAGE_FIXTURE_MODE=1
    gentoo_opt_fixture_override_allowed || return 1
    EBUILD=/var/tmp/unreviewed/app-test/phase2-pgo-use-fixture/phase2-pgo-use-fixture-1.ebuild
    gentoo_opt_fixture_override_allowed && return 1
    PN=unreviewed
    EBUILD=/var/tmp/gentoo-phase2-pgo-portage.ABC123/app-test/phase2-pgo-use-fixture/phase2-pgo-use-fixture-1.ebuild
    gentoo_opt_fixture_override_allowed && return 1
    return 0
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
    mkdir -p "${TMP}/ed" "${TMP}/bolt-cache" "${TMP}/portage/cat/pkg"
    : > "${TMP}/portage/cat/pkg/.installed"
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
    export PORTAGE_TMPDIR="${TMP}" PORTAGE_BUILDDIR="${TMP}/portage/cat/pkg"
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    post_src_install
    mapfile -t arguments < "${BOLT_WRAPPER_EVIDENCE}"
    [[ ${arguments[*]} == "--ed ${ED} --cache-root ${GENTOO_OPT_BOLT_CACHE_ROOT} --fingerprint ${FINGERPRINT} --expected-eligible-count 1 --inventory-proof ${GENTOO_OPT_BOLT_INVENTORY_PROOF} --readelf /usr/bin/readelf --objcopy /usr/bin/objcopy" ]]
    [[ -f ${PORTAGE_BUILDDIR}/.installed ]]
)

case_existing_post_install_hook_is_chained() (
    post_src_install() {
        printf '%s\n' 'previous hook ran' >> "${TMP}/previous-hook.log"
    }
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    # An accidental second source must not capture our wrapper as its own
    # predecessor and recurse.
    source "${BASHRC}" >/dev/null 2>&1 || return 1
    post_src_install
    [[ $(<"${TMP}/previous-hook.log") == 'previous hook ran' ]]
)

case_portage_phase_cannot_swallow_bolt_failure() (
    mkdir -p "${TMP}/fatal-ed" "${TMP}/fatal-cache" "${TMP}/portage/cat/fatal"
    : > "${TMP}/portage/cat/fatal/.installed"
    printf '%s\n' '#!/usr/bin/env bash' 'exit 41' > "${TMP}/bin/failing-capture-wrapper"
    chmod +x "${TMP}/bin/failing-capture-wrapper"
    export PATH="${TMP}/bin:/usr/bin:/bin" CC=clang ABI=amd64
    export GENTOO_OPT_MODE=bolt-capture GENTOO_OPT_COMPILER_FAMILY=clang
    export GENTOO_OPT_ABI=amd64 GENTOO_OPT_FINGERPRINT=${FINGERPRINT}
    export GENTOO_OPT_BOLT_CACHE_ROOT="${TMP}/fatal-cache"
    export GENTOO_OPT_BOLT_CAPTURE_TOOL="${TMP}/bin/failing-capture-wrapper"
    export ED="${TMP}/fatal-ed"
    export PORTAGE_TMPDIR="${TMP}" PORTAGE_BUILDDIR="${TMP}/portage/cat/fatal"
    source "${BASHRC}" >/dev/null 2>&1 || return 1
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
    [[ ${status} -eq 97 && ! -e ${PORTAGE_BUILDDIR}/.installed ]]
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
    source "${BOUND_BASHRC}" >/dev/null 2>&1 || return 1
    [[ ${CFLAGS} == depend-c && ${CXXFLAGS} == depend-cxx && ${LDFLAGS} == depend-ld ]]
    [[ ${RUSTFLAGS} == depend-rust && ${GOFLAGS} == depend-go ]]
    [[ ${FEATURES} == 'ccache sandbox' && ${SANDBOX_WRITE} == /depend/write ]]
    [[ -z ${GENTOO_OPT_ACTIVE_FINGERPRINT-} && -z ${GENTOO_OPT_ACTIVE_BACKEND-} ]]
    [[ ${GENTOO_OPT_FRAMEWORK_TARGET-} == "${FIXTURE_FRAMEWORK_TARGET}" ]]
)

case_depend_phase_invalid_mode_is_fatal() (
    set +e
    (
        die() { exit 95; }
        EBUILD_PHASE=depend PATH=/dev/null GENTOO_OPT_MODE=invalid source "${BOUND_BASHRC}"
        exit 0
    ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 95 ]]
)

case_depend_phase_invalid_readiness_is_fatal() (
    local status
    set +e
    (
        die() { exit 94; }
        EBUILD_PHASE=depend PATH=/dev/null GENTOO_OPT_MODE=off \
            GENTOO_OPT_PROFILE_MAP_READY=invalid source "${BOUND_BASHRC}"
        exit 0
    ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 94 ]] || return 1

    set +e
    (
        die() { exit 93; }
        EBUILD_PHASE=depend PATH=/dev/null GENTOO_OPT_MODE=gcc-generate \
            GENTOO_OPT_BOLT_GCC_READY=1 source "${BOUND_BASHRC}"
        exit 0
    ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 93 ]]
)

case_nonbuild_phase_matrix_is_noop() (
    local phase
    for phase in depend clean cleanrm nofetch pretend prerm postrm preinst postinst config info; do
        (
            export EBUILD_PHASE=${phase} PATH=/dev/null
            GENTOO_OPT_MODE=clang-ir-use
            GENTOO_OPT_BOLT_STAGE=deploy
            GENTOO_OPT_FINGERPRINT_FILE=/missing/fingerprint
            GENTOO_OPT_PROFILE_PATH=/missing/profile
            GENTOO_OPT_BOLT_CACHE_ROOT=/missing/cache
            CFLAGS='phase-c'; CXXFLAGS='phase-cxx'; LDFLAGS='phase-ld'
            RUSTFLAGS='phase-rust'; GOFLAGS='phase-go'; FEATURES='ccache sandbox'
            SANDBOX_WRITE='/phase/write'
            source "${BOUND_BASHRC}" >/dev/null 2>&1 || return 1
            [[ ${CFLAGS} == phase-c && ${CXXFLAGS} == phase-cxx && ${LDFLAGS} == phase-ld ]]
            [[ ${RUSTFLAGS} == phase-rust && ${GOFLAGS} == phase-go ]]
            [[ ${FEATURES} == 'ccache sandbox' && ${SANDBOX_WRITE} == /phase/write ]]
            [[ -z ${GENTOO_OPT_ACTIVE_FINGERPRINT-} && -z ${GENTOO_OPT_ACTIVE_BACKEND-} ]]
            [[ ${GENTOO_OPT_FRAMEWORK_TARGET-} == "${FIXTURE_FRAMEWORK_TARGET}" ]]
            ! declare -F post_src_install >/dev/null
        ) || return 1
    done
)

run_case 'off/unset leaves all flags unchanged' case_off_is_noop
run_case 'durable framework activation journal blocks Portage' case_framework_activation_journal_fails_closed
run_case 'durable profile transaction journal requires exact coordinator authorization' case_profile_transaction_journal_authorization_is_fail_closed
run_case 'ordinary repository policy contains no stage readiness' case_repository_ordinary_flags_are_stage_clean
run_case 'profile-map readiness owns its complete exact stage set' case_profile_map_stage_is_exact
run_case 'stage build-ID policy rejects conflicts and duplicates' case_stage_build_id_policy_fails_closed
run_case 'legacy marker paths fail closed' case_legacy_rejected
run_case 'unknown modes fail closed' case_unknown_mode_rejected
run_case 'Clang mode rejects a GCC compiler' case_compiler_lane_rejected
run_case 'mixed C and C++ compiler families are rejected' case_mixed_c_cxx_tuple_rejected
run_case 'requested ABI cannot cross the Portage ABI' case_abi_separation
run_case 'missing use profile fails closed' case_missing_profile_rejected
run_case 'Clang IR use validates and appends exactly once' case_ir_use_and_exact_once
run_case 'IR and sample profiles remain format/flag separated' case_sample_use_and_format_separation
run_case 'profile manifest ABI mismatch fails closed' case_manifest_mismatch_rejected
run_case 'profile use binds the exact compiler path and hash' case_exact_profile_compiler_identity_is_bound
run_case 'Clang generation appends exactly once' case_clang_generate_exact_once
run_case 'compiler cache and distribution masquerades are bypassed' case_compiler_masquerades_are_bypassed
run_case 'GCC correction remains isolated from Fortran' case_gcc_use_isolated_correction
run_case 'Rust instrumentation requires target isolation' case_rust_target_isolation
run_case 'Go PGO changes GOFLAGS only' case_go_use_only_goflags
run_case 'generic Go PGO rejects multi-main or unclassified packages' case_go_multi_main_is_rejected
run_case 'Rust and Go BOLT stages remain language-lane specific' case_rust_and_go_bolt_layering
run_case 'Rust BOLT readiness requires an explicit target' case_rust_bolt_readiness_requires_target
run_case 'BOLT readiness layers and guards the GCC lane' case_bolt_layer_and_gcc_guard
run_case 'C/C++ BOLT capture and deploy sets are exact' case_c_bolt_capture_and_deploy_sets_are_exact
run_case 'stage readiness markers fail closed outside their lane' case_readiness_markers_fail_closed
run_case 'BOLT stages require an exact sandbox cache scope' case_bolt_cache_scope_is_required
run_case 'BOLT stages require an exact inventory proof path' case_bolt_inventory_proof_is_required
run_case 'strict fingerprint.env loading works' case_fingerprint_file_strict
run_case 'fixture fingerprint computation uses only its bounded key tool' case_fingerprint_identity_tool_is_bounded
run_case 'root is rejected as an identity file path' case_root_path_is_not_safe_identity
run_case 'root trust rejects user-writable and symlinked ancestors' case_production_trust_rejects_replaceable_paths
run_case 'production compiler and identity tools are exact trusted executables' case_production_tools_are_exact_and_trusted
run_case 'production identity/profile/spool/log/BOLT namespaces fail closed' case_production_namespaces_fail_closed
run_case 'production cannot inject an inline fingerprint' case_production_inline_fingerprint_is_rejected
run_case 'real Portage fixture overrides are atom/path bounded' case_portage_fixture_override_is_exactly_bounded
run_case 'post_src_install invokes the exact BOLT wrapper interface' case_bolt_post_install_wrapper
run_case 'post_src_install chains an existing Portage hook' case_existing_post_install_hook_is_chained
run_case 'Portage cannot swallow a BOLT transaction failure' case_portage_phase_cannot_swallow_bolt_failure
run_case 'Portage cannot swallow dispatcher failure while sourcing' case_portage_source_dispatch_failure_is_fatal
run_case 'depend phase performs no external command or flag/path mutation' case_depend_phase_is_external_command_free
run_case 'depend phase rejects invalid modes through Portage fatal path' case_depend_phase_invalid_mode_is_fatal
run_case 'depend phase rejects invalid readiness through Portage fatal path' case_depend_phase_invalid_readiness_is_fatal
run_case 'all non-build lifecycle phases leave policy and hooks inactive' case_nonbuild_phase_matrix_is_noop

printf 'SUMMARY: pass=%d fail=%d total=%d\n' "${PASS}" "${FAIL}" "$((PASS + FAIL))"
((FAIL == 0))
