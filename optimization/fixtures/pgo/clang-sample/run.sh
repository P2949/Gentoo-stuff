#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../../../.." && pwd -P)
OUTPUT_ROOT=${1:-}
ITERATIONS=${CLANG_SAMPLE_ITERATIONS:-50000000}
LLVM_ROOT=${LLVM_ROOT:-/usr/lib/llvm/22/bin}
CLANGXX=${CLANGXX:-${LLVM_ROOT}/clang++}
PROFGEN=${PROFGEN:-${LLVM_ROOT}/llvm-profgen}
PROFDATA=${PROFDATA:-${LLVM_ROOT}/llvm-profdata}
READELF=${READELF:-${LLVM_ROOT}/llvm-readelf}
OBJCOPY=${OBJCOPY:-${LLVM_ROOT}/llvm-objcopy}
PERF=${PERF:-/usr/bin/perf}
RG=${RG:-/usr/bin/rg}
PYTHON=${PYTHON:-/usr/bin/python3}
READLINK_TOOL=${READLINK_TOOL:-/usr/bin/readlink}
SHA256SUM=${SHA256SUM:-/usr/bin/sha256sum}
CUT=${CUT:-/usr/bin/cut}
DATE=${DATE:-/usr/bin/date}
HOSTNAME_TOOL=${HOSTNAME_TOOL:-/usr/bin/hostname}
PROFILE_IDENTITY=${PROFILE_IDENTITY:-${REPOSITORY_ROOT}/scripts/optimization/pgo/profile-identity.py}
PROFILE_VALIDATOR=${PROFILE_VALIDATOR:-${REPOSITORY_ROOT}/scripts/optimization/pgo/validate-profile.py}

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

[[ -n ${OUTPUT_ROOT} && ${OUTPUT_ROOT} == /* && ${OUTPUT_ROOT} != / ]] || \
    fail 'provide one new absolute output directory'
[[ ! ${OUTPUT_ROOT} =~ [[:space:]=] ]] || \
    fail 'output directory may not contain whitespace or equals signs'
case ${OUTPUT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *) fail 'output directory must be below /tmp or /var/tmp/gentoo-optimization' ;;
esac
[[ ! -e ${OUTPUT_ROOT} ]] || fail "output already exists: ${OUTPUT_ROOT}"
[[ ${ITERATIONS} =~ ^[1-9][0-9]*$ ]] || fail 'CLANG_SAMPLE_ITERATIONS must be positive'

for tool in \
    "${CLANGXX}" "${PROFGEN}" "${PROFDATA}" "${READELF}" "${OBJCOPY}" \
    "${PERF}" "${RG}" "${PYTHON}" "${READLINK_TOOL}" "${SHA256SUM}" \
    "${CUT}" "${DATE}" "${HOSTNAME_TOOL}" "${PROFILE_IDENTITY}" \
    "${PROFILE_VALIDATOR}"; do
    [[ -x ${tool} ]] || fail "required tool is not executable: ${tool}"
done

mkdir -p -- "${OUTPUT_ROOT}"
OUTPUT_ROOT=$(cd -- "${OUTPUT_ROOT}" && pwd -P)
case ${OUTPUT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *) fail "canonical output directory escaped the allowed roots: ${OUTPUT_ROOT}" ;;
esac
exec > >(tee "${OUTPUT_ROOT}/run.log") 2>&1
set -x

CLANG_REAL=$("${READLINK_TOOL}" -f -- "${CLANGXX}")
PROFGEN_REAL=$("${READLINK_TOOL}" -f -- "${PROFGEN}")
PROFDATA_REAL=$("${READLINK_TOOL}" -f -- "${PROFDATA}")
READELF_REAL=$("${READLINK_TOOL}" -f -- "${READELF}")
OBJCOPY_REAL=$("${READLINK_TOOL}" -f -- "${OBJCOPY}")
for tool in \
    "${CLANG_REAL}" "${PROFGEN_REAL}" "${PROFDATA_REAL}" \
    "${READELF_REAL}" "${OBJCOPY_REAL}"; do
    [[ ${tool} == /* && -f ${tool} && -x ${tool} && ! -L ${tool} ]] || \
        fail "LLVM tool does not resolve to a canonical executable regular file: ${tool}"
done
PRODUCTION_HOST=$("${HOSTNAME_TOOL}")
[[ ${PRODUCTION_HOST} =~ ^[A-Za-z0-9+_.@-]+$ ]] || \
    fail "hostname is not a safe producer identity: ${PRODUCTION_HOST}"
PRODUCTION_DATE=$(TZ=UTC "${DATE}" -u +%F)
SOURCE_IDENTITY_SHA256=$(
    "${SHA256SUM}" -- \
        "${SCRIPT_DIR}/run.sh" \
        "${SCRIPT_DIR}/sample_main.cpp" \
        "${SCRIPT_DIR}/sample_math.cpp" |
        "${CUT}" -d ' ' -f 1 |
        "${SHA256SUM}" |
        "${CUT}" -d ' ' -f 1
)

COMMON_FLAGS=(
    -O3
    -flto=thin
    -gline-tables-only
    -fdebug-info-for-profiling
    -funique-internal-linkage-names
    -fpseudo-probe-for-profiling
    -fno-omit-frame-pointer
    -ffunction-sections
    -fdata-sections
)
LINK_FLAGS=(
    -fuse-ld=lld
    "-Wl,--build-id=sha1"
    "-Wl,--emit-relocs"
)

"${CLANGXX}" "${COMMON_FLAGS[@]}" -c "${SCRIPT_DIR}/sample_math.cpp" \
    -o "${OUTPUT_ROOT}/sample_math.o"
"${CLANGXX}" "${COMMON_FLAGS[@]}" -c "${SCRIPT_DIR}/sample_main.cpp" \
    -o "${OUTPUT_ROOT}/sample_main.o"
"${CLANGXX}" "${COMMON_FLAGS[@]}" "${LINK_FLAGS[@]}" -pie \
    "${OUTPUT_ROOT}/sample_main.o" "${OUTPUT_ROOT}/sample_math.o" \
    -o "${OUTPUT_ROOT}/sample-train"

"${READELF}" --notes --sections --relocs "${OUTPUT_ROOT}/sample-train" \
    >"${OUTPUT_ROOT}/sample-train.readelf"
"${PERF}" record -q -e cycles:u -j any,u \
    -o "${OUTPUT_ROOT}/perf.data" -- \
    "${OUTPUT_ROOT}/sample-train" "${ITERATIONS}" \
    >"${OUTPUT_ROOT}/training.stdout"
"${PERF}" evlist -v -i "${OUTPUT_ROOT}/perf.data" \
    >"${OUTPUT_ROOT}/perf-evlist.log"
"${RG}" -q 'sample_type:.*BRANCH_STACK' "${OUTPUT_ROOT}/perf-evlist.log" || \
    fail 'perf.data does not declare branch-stack samples'
"${RG}" -q 'branch_sample_type:.*USER.*ANY' "${OUTPUT_ROOT}/perf-evlist.log" || \
    fail 'perf.data does not declare the requested user/any branch filter'

env -i HOME=/nonexistent LANG=C LANGUAGE=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
    "${PYTHON}" - \
    "${OUTPUT_ROOT}/fingerprint-input.json" \
    "${CLANG_REAL}" \
    "${SOURCE_IDENTITY_SHA256}" <<'PY'
import json
import sys
from pathlib import Path

output, compiler, source_identity = sys.argv[1:]
manifest = {
    "schema_version": 2,
    "category": "dev-util",
    "pf": "clang-sample-capability-fixture-2",
    "slot": "0",
    "subslot": "0",
    "repository": "gentoo-optimization-fixture",
    "ebuild_sha256": source_identity,
    "eapi": "8",
    "chost": "x86_64-pc-linux-gnu",
    "abi": "amd64",
    "compiler": {
        "path": compiler,
        "family": "clang",
        "major": 22,
        "profile_format": "llvm-sample-v22",
    },
    "use_flags": [],
    "cflags": "-O3 -flto=thin -gline-tables-only -fdebug-info-for-profiling -funique-internal-linkage-names -fpseudo-probe-for-profiling -fno-omit-frame-pointer -ffunction-sections -fdata-sections",
    "cxxflags": "-O3 -flto=thin -gline-tables-only -fdebug-info-for-profiling -funique-internal-linkage-names -fpseudo-probe-for-profiling -fno-omit-frame-pointer -ffunction-sections -fdata-sections",
    "ldflags": "-fuse-ld=lld -Wl,--build-id=sha1 -Wl,--emit-relocs",
    "rustflags": "",
    "goflags": "",
    "features": ["fixture"],
    "package_env_files": [],
    "extra_econf": "",
    "extra_emeson": "",
    "extra_ecmake": "",
    "kernel_module": False,
    "kernel_release": None,
    "rust_target_triple": None,
    "rustc_llvm_version": None,
}
Path(output).write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
PY

FINGERPRINT=$(env -i HOME=/nonexistent LANG=C LANGUAGE=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
    "${PROFILE_IDENTITY}" fingerprint \
    --input "${OUTPUT_ROOT}/fingerprint-input.json" \
    --metadata-out "${OUTPUT_ROOT}/fingerprint-metadata.json")
[[ ${FINGERPRINT} =~ ^[0-9a-f]{64}$ ]] || fail 'fingerprint tool returned an invalid key'

PROFILE_SHA256=$(env -i HOME=/nonexistent LANG=C LANGUAGE=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
    "${PROFILE_IDENTITY}" sample-convert \
    --llvm-profgen "${PROFGEN_REAL}" \
    --llvm-profdata "${PROFDATA_REAL}" \
    --readelf "${READELF_REAL}" \
    --objcopy "${OBJCOPY_REAL}" \
    --binary "${OUTPUT_ROOT}/sample-train" \
    --perf-data "${OUTPUT_ROOT}/perf.data" \
    --profile-out "${OUTPUT_ROOT}/sample.prof" \
    --metadata-out "${OUTPUT_ROOT}/sample-metadata.json" \
    --conversion-log-out "${OUTPUT_ROOT}/llvm-profgen-conversion-log.json" \
    --cpv dev-util/clang-sample-capability-fixture-2 \
    --fingerprint "${FINGERPRINT}" \
    --abi amd64 \
    --clang-major 22 \
    --optimization-generation-id capability-clang-sample-v2 \
    --workload-revision clang-sample-fixture-v2 \
    --source-identity-sha256 "${SOURCE_IDENTITY_SHA256}" \
    --production-host "${PRODUCTION_HOST}" \
    --production-date "${PRODUCTION_DATE}")
[[ ${PROFILE_SHA256} == "$("${SHA256SUM}" "${OUTPUT_ROOT}/sample.prof" | "${CUT}" -d ' ' -f 1)" ]] || \
    fail 'sample producer returned a mismatching profile SHA-256'

"${PROFDATA}" show --sample --all-functions --counts \
    "${OUTPUT_ROOT}/sample.prof" >"${OUTPUT_ROOT}/sample-profile.show"

CLANG_SHA256=$("${SHA256SUM}" "${CLANG_REAL}" | "${CUT}" -d ' ' -f 1)
PROFDATA_SHA256=$("${SHA256SUM}" "${PROFDATA_REAL}" | "${CUT}" -d ' ' -f 1)
env -i HOME=/nonexistent LANG=C LANGUAGE=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
    "${PROFILE_VALIDATOR}" produce \
    --backend clang-sample \
    --profile "${OUTPUT_ROOT}/sample.prof" \
    --fingerprint "${FINGERPRINT}" \
    --abi amd64 \
    --compiler-family clang \
    --compiler "${CLANG_REAL}" \
    --compiler-sha256 "${CLANG_SHA256}" \
    --compiler-major 22 \
    --profile-tool "${PROFDATA_REAL}" \
    --profile-tool-sha256 "${PROFDATA_SHA256}" \
    --profile-tool-major 22 \
    --sample-metadata "${OUTPUT_ROOT}/sample-metadata.json" \
    --manifest-out "${OUTPUT_ROOT}/profile.manifest" \
    --metadata-out "${OUTPUT_ROOT}/profile.manifest.metadata.json" \
    >"${OUTPUT_ROOT}/profile-produce.stdout" \
    2>"${OUTPUT_ROOT}/profile-produce.stderr"
env -i HOME=/nonexistent LANG=C LANGUAGE=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
    "${PROFILE_VALIDATOR}" verify \
    --manifest "${OUTPUT_ROOT}/profile.manifest" \
    --metadata "${OUTPUT_ROOT}/profile.manifest.metadata.json" \
    >"${OUTPUT_ROOT}/profile-verify.stdout" \
    2>"${OUTPUT_ROOT}/profile-verify.stderr"

[[ $("${RG}" -c '^profile_path=' "${OUTPUT_ROOT}/profile.manifest") -eq 1 ]] || \
    fail 'dispatcher manifest does not contain exactly one profile_path'
VALIDATED_PROFILE=$("${RG}" '^profile_path=' "${OUTPUT_ROOT}/profile.manifest" | \
    "${CUT}" -d = -f 2-)
[[ ${VALIDATED_PROFILE} == "${OUTPUT_ROOT}/sample.prof" ]] || \
    fail 'dispatcher manifest does not select the exact produced sample profile'

"${CLANGXX}" "${COMMON_FLAGS[@]}" \
    -fprofile-sample-use="${VALIDATED_PROFILE}" \
    -fsample-profile-use-profi \
    -c "${SCRIPT_DIR}/sample_math.cpp" -o "${OUTPUT_ROOT}/sample_math.use.o" \
    2>"${OUTPUT_ROOT}/sample-use-compile.log"
"${CLANGXX}" "${COMMON_FLAGS[@]}" \
    -fprofile-sample-use="${VALIDATED_PROFILE}" \
    -fsample-profile-use-profi \
    -c "${SCRIPT_DIR}/sample_main.cpp" -o "${OUTPUT_ROOT}/sample_main.use.o" \
    2>>"${OUTPUT_ROOT}/sample-use-compile.log"
"${CLANGXX}" "${COMMON_FLAGS[@]}" "${LINK_FLAGS[@]}" -pie \
    "${OUTPUT_ROOT}/sample_main.use.o" "${OUTPUT_ROOT}/sample_math.use.o" \
    -o "${OUTPUT_ROOT}/sample-use"

"${OUTPUT_ROOT}/sample-use" "${ITERATIONS}" >"${OUTPUT_ROOT}/sample-use.stdout"
cmp -- "${OUTPUT_ROOT}/training.stdout" "${OUTPUT_ROOT}/sample-use.stdout"

if "${CLANGXX}" "${COMMON_FLAGS[@]}" \
    -fprofile-use="${VALIDATED_PROFILE}" \
    -c "${SCRIPT_DIR}/sample_math.cpp" \
    -o "${OUTPUT_ROOT}/must-not-build.o" \
    >"${OUTPUT_ROOT}/ir-consumer-negative.stdout" \
    2>"${OUTPUT_ROOT}/ir-consumer-negative.stderr"; then
    fail 'Clang incorrectly accepted the sample profile through -fprofile-use'
fi
[[ -s ${OUTPUT_ROOT}/ir-consumer-negative.stderr ]] || \
    fail 'wrong-consumer negative test produced no diagnostic'

"${CLANGXX}" "${COMMON_FLAGS[@]}" \
    -fprofile-sample-use="${VALIDATED_PROFILE}" \
    -fsample-profile-use-profi \
    -### -c "${SCRIPT_DIR}/sample_math.cpp" \
    -o "${OUTPUT_ROOT}/command-proof.o" \
    2>"${OUTPUT_ROOT}/sample-use-command.log"

profile_functions=$("${RG}" -c '^Function: ' "${OUTPUT_ROOT}/sample-profile.show")
profgen_warning_lines=$(env -i HOME=/nonexistent LANG=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
    "${PYTHON}" - "${OUTPUT_ROOT}/llvm-profgen-conversion-log.json" <<'PY'
import json
import sys
from pathlib import Path

record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(sum(line.lstrip().startswith("warning:") for line in record["stderr"].splitlines()))
PY
)
[[ -n ${profile_functions} && ${profile_functions} -gt 0 ]] || \
    fail 'sample profile contains no functions'
"${RG}" -q '^[1-9][0-9]*, [0-9]+, [1-9][0-9]* sampled lines$' \
    "${OUTPUT_ROOT}/sample-profile.show" || \
    fail 'sample profile has no nonzero top-level samples'
"${RG}" -q -- '-fprofile-sample-use=' "${OUTPUT_ROOT}/sample-use-command.log" || \
    fail 'Clang command proof omits the sample-profile consumer'
if "${RG}" -q -- '-fprofile-(instr-)?use=|-fprofile-generate' \
    "${OUTPUT_ROOT}/sample-use-command.log"; then
    fail 'Clang command proof contains an instrumentation-profile consumer/generator'
fi
"${RG}" -qi 'invalid|malformed|instrumentation profile|bad magic|unrecognized' \
    "${OUTPUT_ROOT}/ir-consumer-negative.stderr" || \
    fail 'wrong-consumer diagnostic does not prove profile-format rejection'

{
    printf 'result=PASS\n'
    printf 'iterations=%s\n' "${ITERATIONS}"
    printf 'sample_profile=%s\n' "${OUTPUT_ROOT}/sample.prof"
    printf 'sample_profile_sha256=%s\n' "${PROFILE_SHA256}"
    printf 'package_fingerprint=%s\n' "${FINGERPRINT}"
    printf 'producer_metadata=%s\n' "${OUTPUT_ROOT}/sample-metadata.json"
    printf 'conversion_log=%s\n' "${OUTPUT_ROOT}/llvm-profgen-conversion-log.json"
    printf 'dispatcher_manifest=%s\n' "${OUTPUT_ROOT}/profile.manifest"
    printf 'dispatcher_verification=PASS\n'
    printf 'sample_profile_functions=%s\n' "${profile_functions}"
    printf 'perf_sample_type=BRANCH_STACK\n'
    printf 'perf_branch_sample_type=USER_ANY\n'
    printf 'llvm_profgen_warning_lines=%s\n' "${profgen_warning_lines:-0}"
    printf 'training_output=%s\n' "$(<"${OUTPUT_ROOT}/training.stdout")"
    printf 'sample_use_output=%s\n' "$(<"${OUTPUT_ROOT}/sample-use.stdout")"
    printf 'wrong_consumer_exit=nonzero\n'
    printf 'wrong_consumer_diagnostic=present\n'
    printf 'consumer_flag=-fprofile-sample-use\n'
    printf 'instrumentation_consumer_flag=absent\n'
    printf 'fixture_runner_sha256=%s\n' "$("${SHA256SUM}" "${SCRIPT_DIR}/run.sh" | "${CUT}" -d ' ' -f 1)"
} >"${OUTPUT_ROOT}/validation-summary.log"

"${SHA256SUM}" -- \
    "${OUTPUT_ROOT}/sample-train" \
    "${OUTPUT_ROOT}/perf.data" \
    "${OUTPUT_ROOT}/perf-evlist.log" \
    "${OUTPUT_ROOT}/llvm-profgen-conversion-log.json" \
    "${OUTPUT_ROOT}/sample.prof" \
    "${OUTPUT_ROOT}/sample-metadata.json" \
    "${OUTPUT_ROOT}/profile.manifest" \
    "${OUTPUT_ROOT}/profile.manifest.metadata.json" \
    "${OUTPUT_ROOT}/sample-use" \
    "${OUTPUT_ROOT}/validation-summary.log" \
    >"${OUTPUT_ROOT}/evidence-sha256.log"

set +x
printf 'PASS: Clang sample-PGO fixture (%s functions)\n' "${profile_functions}"
