#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly ROOT
TEMPLATE=${ROOT}/optimization/fixtures/portage/phase2-portage-fixture-1.ebuild.in
PROXY_TEMPLATE=${ROOT}/optimization/fixtures/portage/capture-proxy.sh.in
CAPTURE_TOOL=${ROOT}/scripts/optimization/bolt/capture-input.sh
FRAMEWORK_INSTALLER=/var/lib/gentoo-optimization/bootstrap/install-framework.sh
REGISTER_TOOL=/usr/local/libexec/gentoo-optimization/bolt/register-output.sh
DEPLOY_TOOL=/usr/local/libexec/gentoo-optimization/bolt/deploy-output.sh
LLVM_BOLT=/usr/lib/llvm/22/bin/llvm-bolt

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

if ((EUID != 0)); then
    printf 'SKIP: real Portage phase integration requires root\n'
    exit 77
fi

for command in b2sum chgrp date ebuild mv portageq python3 readelf sed setfacl sha256sum sha512sum stat; do
    command -v "${command}" >/dev/null 2>&1 || fail "missing required command: ${command}"
done
[[ -f ${TEMPLATE} && -f ${PROXY_TEMPLATE} && -x ${CAPTURE_TOOL} ]] || \
    fail 'fixture template, proxy template, or capture tool is absent'
[[ -x ${FRAMEWORK_INSTALLER} ]] || fail 'root-owned framework installer is absent'
"${FRAMEWORK_INSTALLER}" --source-root "${ROOT}" --check >/dev/null || \
    fail 'live root-owned framework does not match the reviewed repository source'
[[ -x ${REGISTER_TOOL} ]] || fail 'installed production BOLT output registrar is absent'
[[ -x ${LLVM_BOLT} ]] || fail 'package-managed llvm-bolt 22 is absent'

WORK=$(mktemp -d /var/tmp/gentoo-phase2-portage-fixture.XXXXXX)
SUCCESS_FINGERPRINT=''
CACHE_ROOT=/var/cache/gentoo-optimization/bolt
cleanup() {
    local status=$?
    if ((status != 0)); then
        printf 'Portage fixture logs from %s:\n' "${WORK}" >&2
        for log in "${WORK}"/*.log; do
            [[ -f ${log} ]] || continue
            printf '\n--- %s ---\n' "${log}" >&2
            tail -n 120 "${log}" >&2 || :
        done
    fi
    if [[ ${SUCCESS_FINGERPRINT} =~ ^[0-9a-f]{64}$ ]]; then
        rm -rf -- "${CACHE_ROOT}/inputs/${SUCCESS_FINGERPRINT}" \
            "${CACHE_ROOT}/perf/${SUCCESS_FINGERPRINT}" \
            "${CACHE_ROOT}/fdata/${SUCCESS_FINGERPRINT}" \
            "${CACHE_ROOT}/outputs/${SUCCESS_FINGERPRINT}" \
            "${CACHE_ROOT}/diagnostics/${SUCCESS_FINGERPRINT}"
        rm -f -- "${CACHE_ROOT}/locks/${SUCCESS_FINGERPRINT}.lock"
        if [[ -d ${CACHE_ROOT}/quarantine/capture-mismatch ]]; then
            find "${CACHE_ROOT}/quarantine/capture-mismatch" -mindepth 1 -maxdepth 1 \
                -name "${SUCCESS_FINGERPRINT}.*" -exec rm -rf -- {} +
        fi
    fi
    # Restore the production cache trust boundary after exposing the fixture
    # proof to the Portage user during the test.
    chown root:root -- "${CACHE_ROOT}/diagnostics" 2>/dev/null || :
    chmod 0700 -- "${CACHE_ROOT}/diagnostics" 2>/dev/null || :
    setfacl -b -- "${CACHE_ROOT}" "${CACHE_ROOT}/diagnostics" 2>/dev/null || :
    rm -rf -- "${WORK}"
    return "${status}"
}
trap cleanup EXIT HUP INT TERM
PACKAGE_ROOT=${WORK}/app-test/phase2-portage-fixture
EBUILD=${PACKAGE_ROOT}/phase2-portage-fixture-1.ebuild
FAIL_SWITCH=${WORK}/force-capture-failure
PROXY_MODE_SWITCH=${WORK}/use-capture-proxy
OFF_SWITCH=${WORK}/optimization-off
DEPLOY_SWITCH=${WORK}/optimization-deploy
CAPTURE_PROXY=${WORK}/capture-proxy.sh
DEPLOY_PROXY=${WORK}/deploy-proxy.sh
SUCCESS_FINGERPRINT=$(printf '%s' "${WORK}:success" | sha256sum | awk '{print $1}')
INVENTORY_PROOF_ROOT=${WORK}/inventory-proof
INVENTORY_EVIDENCE=${INVENTORY_PROOF_ROOT}/inventory.json
INVENTORY_PROOF=${INVENTORY_PROOF_ROOT}/proof.json
FIXTURE_PROJECT_LOCK=${WORK}/fixture-project.lock
FIXTURE_GENERATION_LOCK=${WORK}/fixture-generation.lock
mkdir -p -- "${INVENTORY_PROOF_ROOT}"
chmod 0755 -- "${INVENTORY_PROOF_ROOT}"
python3 - "${INVENTORY_EVIDENCE}" "${INVENTORY_PROOF}" "${SUCCESS_FINGERPRINT}" <<'PY'
import hashlib,json,pathlib,sys
evidence=pathlib.Path(sys.argv[1]).resolve()
canonical="usr/bin/phase2-portage-fixture"
cpv="app-test/phase2-portage-fixture-1"
entry_sha=hashlib.sha256((cpv+sys.argv[3]).encode()).hexdigest()
inventory={"schema_version":2,"record_type":"frozen-inventory","generation_id":"phase2-portage-fixture-generation-v1",
"inventory_id":"phase2-portage-fixture-inventory-v1","packages":[{"cpv":cpv,"entry_sha256":entry_sha}],
"owned_paths":[{"owner_cpv":cpv,"path":"/"+canonical}],"owned_directories":[]}
evidence.write_text(json.dumps(inventory,sort_keys=True,separators=(",",":"))+"\n")
record={"path":str(evidence),"sha256":hashlib.sha256(evidence.read_bytes()).hexdigest(),"size":evidence.stat().st_size}
candidate={"artifact_id":hashlib.sha256(canonical.encode()).hexdigest(),"canonical_path":canonical,
"paths":[canonical],"hardlink_count":1,"elf_class":"ELF64","elf_data":"2's complement, little endian",
"elf_type":"DYN","machine":"Advanced Micro Devices X86-64","elf_role":"pie-executable"}
document={"schema":"gentoo-optimization-bolt-inventory-proof-v1","generation_id":"phase2-portage-fixture-generation-v1",
"inventory_id":"phase2-portage-fixture-inventory-v1","package_fingerprint":sys.argv[3],
"cpv":cpv,"inventory_entry_sha256":entry_sha,
"expected_eligible_count":1,"inventory_evidence":record,"candidates":[candidate]}
pathlib.Path(sys.argv[2]).write_text(json.dumps(document,sort_keys=True)+"\n")
PY
chgrp portage -- "${INVENTORY_EVIDENCE}" "${INVENTORY_PROOF}"
chmod 0640 -- "${INVENTORY_EVIDENCE}" "${INVENTORY_PROOF}"
python3 - "${INVENTORY_PROOF}" "${FIXTURE_PROJECT_LOCK}" "${FIXTURE_GENERATION_LOCK}" <<'PY'
import json,pathlib,sys
p=json.load(open(sys.argv[1])); payload=json.dumps({"generation_id":p["generation_id"],"inventory_id":p["inventory_id"],"inventory_sha256":p["inventory_evidence"]["sha256"]},indent=2,sort_keys=True)+"\n"
for path in sys.argv[2:]: pathlib.Path(path).write_text(payload)
PY
chmod 0600 "${FIXTURE_PROJECT_LOCK}" "${FIXTURE_GENERATION_LOCK}"
mkdir -p -- "${PACKAGE_ROOT}" "${WORK}/metadata" "${WORK}/profiles"
chmod 0755 -- "${WORK}" "${WORK}/app-test" "${PACKAGE_ROOT}" \
    "${WORK}/metadata" "${WORK}/profiles"
printf '%s\n' 'masters = gentoo' > "${WORK}/metadata/layout.conf"
printf '%s\n' phase2-portage-fixture > "${WORK}/profiles/repo_name"
printf '%s\n' app-test > "${WORK}/profiles/categories"

escape_sed() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

sed \
    -e "s|@CACHE_ROOT@|$(escape_sed "${CACHE_ROOT}")|g" \
    -e "s|@CAPTURE_PROXY@|$(escape_sed "${CAPTURE_PROXY}")|g" \
    -e "s|@PROXY_MODE_SWITCH@|$(escape_sed "${PROXY_MODE_SWITCH}")|g" \
    -e "s|@OFF_SWITCH@|$(escape_sed "${OFF_SWITCH}")|g" \
    -e "s|@DEPLOY_SWITCH@|$(escape_sed "${DEPLOY_SWITCH}")|g" \
    -e "s|@DEPLOY_PROXY@|$(escape_sed "${DEPLOY_PROXY}")|g" \
    -e "s|@INVENTORY_PROOF@|$(escape_sed "${INVENTORY_PROOF}")|g" \
    -e "s|@SUCCESS_FINGERPRINT@|${SUCCESS_FINGERPRINT}|g" \
    "${TEMPLATE}" > "${EBUILD}"
CAPTURE_COMMAND="${CAPTURE_TOOL} --test-mode --test-project-lock ${FIXTURE_PROJECT_LOCK} --test-generation-lock ${FIXTURE_GENERATION_LOCK}"
sed \
    -e "s|@CAPTURE_TOOL@|$(escape_sed "${CAPTURE_COMMAND}")|g" \
    -e "s|@FAIL_SWITCH@|$(escape_sed "${FAIL_SWITCH}")|g" \
    "${PROXY_TEMPLATE}" > "${CAPTURE_PROXY}"
chmod 0755 -- "${CAPTURE_PROXY}"
printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    "exec '${DEPLOY_TOOL}' --test-mode --fixture-quality-mode --test-project-lock '${FIXTURE_PROJECT_LOCK}' --test-generation-lock '${FIXTURE_GENERATION_LOCK}' \"\$@\"" >"${DEPLOY_PROXY}"
chmod 0755 -- "${DEPLOY_PROXY}"
chmod 0644 -- "${EBUILD}" "${WORK}/metadata/layout.conf" "${WORK}/profiles/repo_name"
printf 'EBUILD %s %s BLAKE2B %s SHA512 %s\n' \
    "$(basename -- "${EBUILD}")" "$(stat -c %s "${EBUILD}")" \
    "$(b2sum "${EBUILD}" | awk '{print $1}')" \
    "$(sha512sum "${EBUILD}" | awk '{print $1}')" \
    > "${PACKAGE_ROOT}/Manifest"
chmod 0644 -- "${WORK}/profiles/categories" "${PACKAGE_ROOT}/Manifest"

PORTAGE_TMPDIR=$(portageq envvar PORTAGE_TMPDIR)
[[ ${PORTAGE_TMPDIR} == /* ]] || fail 'Portage returned an unsafe PORTAGE_TMPDIR'
BUILD_ROOT=${PORTAGE_TMPDIR%/}/portage/app-test/phase2-portage-fixture-1

# The installed ebuild CLI cannot invoke its internal `depend` action directly
# (it requires Portage's returnproc plumbing), so dependency sourcing remains
# covered by the exact phase emulator in test-pgo-dispatcher.sh. Exercise the
# real cleanup and build/install processes here.
ebuild "${EBUILD}" clean >"${WORK}/initial-clean.log" 2>&1

# Successful install stops at Portage staging; it does not merge into /usr/VDB.
ebuild "${EBUILD}" install >"${WORK}/install-success.log" 2>&1
MANIFEST=${CACHE_ROOT}/inputs/${SUCCESS_FINGERPRINT}/manifest.json
[[ -s ${MANIFEST} ]] || fail 'real Portage install did not publish the capture manifest'
python3 - "${MANIFEST}" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["eligible_total"] == 1
artifact = manifest["artifacts"][0]
assert artifact["eligible"] is True
assert artifact["has_symtab"] is True
assert artifact["text_relocation_sections"]
assert artifact["build_id"] and artifact["text_sha256"]
PY
cp -- "${MANIFEST}" "${WORK}/first-capture-manifest.json"
[[ ! -e /var/db/pkg/app-test/phase2-portage-fixture-1 ]] || \
    fail 'disposable ebuild unexpectedly entered the installed VDB'
[[ -f ${BUILD_ROOT}/.installed ]] || fail 'successful Portage install lacks its .installed marker'
[[ $(<"${BUILD_ROOT}/temp/previous-hook.log") == previous-hook-passed ]] || \
    fail 'pre-existing post_src_install hook did not run before capture'

# Force the wrapper to fail after Portage creates .installed. The fatal hook
# must remove that marker so a retry cannot bypass src_install/post_src_install.
ebuild "${EBUILD}" clean >"${WORK}/pre-failure-clean.log" 2>&1
rm -rf -- "${CACHE_ROOT}/inputs/${SUCCESS_FINGERPRINT}"
: > "${PROXY_MODE_SWITCH}"
: > "${FAIL_SWITCH}"
if ebuild "${EBUILD}" install >"${WORK}/install-failure.log" 2>&1; then
    fail 'real Portage install accepted a failing BOLT capture wrapper'
fi
[[ ! -e ${BUILD_ROOT}/.installed ]] || \
    fail 'failed post_src_install left Portage .installed behind'

rm -f -- "${FAIL_SWITCH}"
ebuild "${EBUILD}" install >"${WORK}/install-retry.log" 2>&1
[[ -s ${MANIFEST} ]] || fail 'successful retry did not recapture its exact input'
python3 - "${WORK}/first-capture-manifest.json" "${MANIFEST}" <<'PY'
import json
import sys

before = json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"][0]
after = json.load(open(sys.argv[2], encoding="utf-8"))["artifacts"][0]
for key in ("file_sha256", "build_id", "text_sha256"):
    assert before[key] == after[key], key
PY
[[ -f ${BUILD_ROOT}/.installed ]] || fail 'successful retry did not complete install phase'
grep -Fq 'Completed installing app-test/phase2-portage-fixture-1' \
    "${WORK}/install-retry.log" || fail 'retry appears to have skipped src_install'

# Produce one real prepared output from the immutable captured object. This is
# deliberately a hook-integration profile, not profile-quality evidence: the
# minimal valid fdata record only proves that the production registrar and
# pre-strip deployment path accept an exact llvm-bolt transformation.
IFS=$'\t' read -r ARTIFACT_ID OBJECT_FILE < <(python3 - "${MANIFEST}" <<'PY'
import json
import sys

eligible = [
    item
    for item in json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"]
    if item["eligible"]
]
assert len(eligible) == 1
print(eligible[0]["artifact_id"], eligible[0]["cache_object"], sep="\t")
PY
)
[[ ${ARTIFACT_ID} =~ ^[0-9a-f]{64}$ ]] || fail 'capture returned an invalid artifact ID'
CAPTURED_OBJECT=${CACHE_ROOT}/inputs/${SUCCESS_FINGERPRINT}/${OBJECT_FILE}
[[ -s ${CAPTURED_OBJECT} ]] || fail 'exact captured BOLT input object is absent'

EVIDENCE_ROOT=${CACHE_ROOT}/diagnostics/${SUCCESS_FINGERPRINT}/hook-integration
FDATA_ROOT=${CACHE_ROOT}/fdata/${SUCCESS_FINGERPRINT}
mkdir -p -- "${EVIDENCE_ROOT}" "${FDATA_ROOT}"
chmod 0700 -- "${CACHE_ROOT}/diagnostics/${SUCCESS_FINGERPRINT}" \
    "${EVIDENCE_ROOT}" "${FDATA_ROOT}"
FDATA=${FDATA_ROOT}/minimal-hook-integration.fdata
WORKLOAD_EVIDENCE=${EVIDENCE_ROOT}/workload-evidence.json
PROFILE_EVIDENCE=${EVIDENCE_ROOT}/profile-evidence.json
FDATA_QUALITY_EVIDENCE=${EVIDENCE_ROOT}/fdata-quality-evidence.json
BOLT_STDOUT=${EVIDENCE_ROOT}/llvm-bolt.stdout
BOLT_STDERR=${EVIDENCE_ROOT}/llvm-bolt.stderr
BOLT_COMMAND_RECORD=${EVIDENCE_ROOT}/llvm-bolt-command.json
BOLT_COMMAND_OUTPUT=${EVIDENCE_ROOT}/phase2-portage-fixture.bolt.partial
BOLT_PREPARED=${EVIDENCE_ROOT}/phase2-portage-fixture.bolt
REGISTER_STDOUT=${EVIDENCE_ROOT}/register-output.stdout
REGISTER_STDERR=${EVIDENCE_ROOT}/register-output.stderr
printf '%s\n' '1 main 0 1 main 1 0 100' > "${FDATA}"
python3 - "${CAPTURED_OBJECT}" "${ARTIFACT_ID}" "${SUCCESS_FINGERPRINT}" \
    "${FDATA}" "${LLVM_BOLT}" "${WORKLOAD_EVIDENCE}" "${PROFILE_EVIDENCE}" \
    "${FDATA_QUALITY_EVIDENCE}" <<'PY'
import hashlib,json,pathlib,re,subprocess,sys,tempfile
source,artifact_id,fingerprint,fdata,tool,workload_path,profile_path,quality_path=sys.argv[1:]
def identity(value):
 p=pathlib.Path(value).resolve(strict=True); return {"path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"size":p.stat().st_size}
notes=subprocess.run(["/usr/bin/readelf","-nW",source],check=True,text=True,capture_output=True,env={"LC_ALL":"C","LANG":"C","PATH":"/usr/bin:/bin"}).stdout
build_id=re.search(r"Build ID:\s*([0-9a-fA-F]+)",notes).group(1).lower()
with tempfile.TemporaryDirectory() as td:
 text=pathlib.Path(td)/"text"; rewrite=pathlib.Path(td)/"rewrite"
 subprocess.run(["/usr/bin/objcopy","--dump-section",f".text={text}",source,str(rewrite)],check=True)
 text_hash=hashlib.sha256(text.read_bytes()).hexdigest()
binding={"generation_id":"phase2-portage-fixture-generation-v1","inventory_id":"phase2-portage-fixture-inventory-v1",
"package_fingerprint":fingerprint,"artifact_id":artifact_id,"input_build_id":build_id,
"input_text_sha256":text_hash,"input_file_sha256":hashlib.sha256(pathlib.Path(source).read_bytes()).hexdigest()}
workload={"schema":"gentoo-optimization-bolt-workload-proof-v1",**binding,"workload_id":"portage-hook-integration",
"workload_definition":identity(source),"workload_log":identity(source),
"command_record":None,
"started_at_utc":"2026-07-13T00:00:00Z","completed_at_utc":"2026-07-13T00:00:01Z","exit_status":0,
"repetitions":1,"functional_passed":True,"fixture_only":True}
pathlib.Path(workload_path).write_text(json.dumps(workload,sort_keys=True)+"\n")
fd=identity(fdata); wi=identity(workload_path)
profile={"schema":"gentoo-optimization-bolt-profile-quality-proof-v1",**binding,"profile_tools":[identity(tool)],
"events":["fixture-synthetic-branch"],"profile_files":[identity(source)],"command_records":[],"lbr_captured":True,"sample_count":1,"branch_entry_count":1,
"ignored_samples":0,"mismatching_samples":0,"out_of_range_samples":0,
"thresholds":{"minimum_samples":1,"minimum_branch_entries":1,"maximum_ignored_samples":0,
"maximum_mismatch_ratio":0.0,"maximum_out_of_range_ratio":0.0},"workload_contributors":[wi],"fdata":[fd],"fixture_only":True}
pathlib.Path(profile_path).write_text(json.dumps(profile,sort_keys=True)+"\n"); pi=identity(profile_path)
quality={"schema":"gentoo-optimization-bolt-fdata-quality-proof-v1",**binding,"merge_tool":identity(tool),
"fdata":[fd],"profile_contributors":[pi],"total_functions":1,"total_samples":1,"command_record":None,"fixture_only":True}
pathlib.Path(quality_path).write_text(json.dumps(quality,sort_keys=True)+"\n")
PY
chmod 0600 -- "${FDATA}" "${WORKLOAD_EVIDENCE}" "${PROFILE_EVIDENCE}" "${FDATA_QUALITY_EVIDENCE}"

POLICY_REVISION=gentoo-system-wide-bolt-v1-cdsort-20260712
BOLT_OPTIONS=(
    -reorder-blocks=ext-tsp
    -reorder-functions=cdsort
    -split-functions
    -split-all-cold
    -split-eh
    -icf=safe
    -update-debug-sections
    -dyno-stats
)
rm -f -- "${BOLT_COMMAND_OUTPUT}" "${BOLT_PREPARED}"
BOLT_STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"${LLVM_BOLT}" "${CAPTURED_OBJECT}" -o "${BOLT_COMMAND_OUTPUT}" \
    "-data=${FDATA}" "${BOLT_OPTIONS[@]}" >"${BOLT_STDOUT}" 2>"${BOLT_STDERR}" || \
    fail 'genuine llvm-bolt rejected the exact captured object or minimal integration fdata'
BOLT_COMPLETED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
[[ -s ${BOLT_COMMAND_OUTPUT} ]] || fail 'genuine llvm-bolt published no output'
chmod 0600 -- "${BOLT_COMMAND_OUTPUT}" "${BOLT_STDOUT}" "${BOLT_STDERR}"
mv -- "${BOLT_COMMAND_OUTPUT}" "${BOLT_PREPARED}"

python3 - "${LLVM_BOLT}" "${CAPTURED_OBJECT}" "${BOLT_PREPARED}" \
    "${BOLT_COMMAND_OUTPUT}" "${FDATA}" "${WORKLOAD_EVIDENCE}" \
    "${PROFILE_EVIDENCE}" "${BOLT_STDOUT}" "${BOLT_STDERR}" \
    "${FDATA_QUALITY_EVIDENCE}" \
    "${BOLT_COMMAND_RECORD}" "${BOLT_STARTED}" "${BOLT_COMPLETED}" \
    "${POLICY_REVISION}" "${BOLT_OPTIONS[@]}" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

(
    tool,
    source,
    prepared,
    command_output,
    fdata,
    workload,
    profile,
    stdout,
    stderr,
    fdata_quality,
    record,
    started,
    completed,
    policy,
    *options,
) = sys.argv[1:]


def identity(value: str, *, recorded_path: str | None = None) -> dict[str, object]:
    path = pathlib.Path(value).resolve(strict=True)
    return {
        "path": recorded_path or str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
    }
def bundle(value: str) -> dict[str, object]:
    return {"identity": identity(value), "document": json.loads(pathlib.Path(value).read_text())}


tool_path = pathlib.Path(tool).resolve(strict=True)
source_path = pathlib.Path(source).resolve(strict=True)
fdata_path = pathlib.Path(fdata).resolve(strict=True)
command_output_path = str(pathlib.Path(command_output).resolve(strict=False))
tool_record = identity(str(tool_path))
tool_record["version"] = subprocess.run(
    [str(tool_path), "--version"],
    check=True,
    text=True,
    capture_output=True,
    env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
).stdout.strip()
document = {
    "schema": "gentoo-optimization-bolt-command-v2",
    "argv": [
        str(tool_path),
        str(source_path),
        "-o",
        command_output_path,
        f"-data={fdata_path}",
        *options,
    ],
    "exit_status": 0,
    "started_at_utc": started,
    "completed_at_utc": completed,
    "tool": tool_record,
    "input": identity(str(source_path)),
    "output": identity(prepared, recorded_path=command_output_path),
    "option_policy_revision": policy,
    "options": options,
    "fdata": [identity(str(fdata_path))],
    "workload_evidence": [bundle(workload)],
    "profile_evidence": [bundle(profile)],
    "fdata_quality_evidence": [bundle(fdata_quality)],
    "stdout": identity(stdout),
    "stderr": identity(stderr),
}
pathlib.Path(record).write_text(
    json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
chmod 0600 -- "${BOLT_COMMAND_RECORD}"

REGISTER_ARGUMENTS=(
    --cache-root "${CACHE_ROOT}"
    --fingerprint "${SUCCESS_FINGERPRINT}"
    --artifact-id "${ARTIFACT_ID}"
    --input "${CAPTURED_OBJECT}"
    --output "${BOLT_PREPARED}"
    --llvm-bolt "${LLVM_BOLT}"
    --option-policy-revision "${POLICY_REVISION}"
    --fdata "${FDATA}"
    --workload-evidence "${WORKLOAD_EVIDENCE}"
    --profile-evidence "${PROFILE_EVIDENCE}"
    --fdata-quality-evidence "${FDATA_QUALITY_EVIDENCE}"
    --command-record "${BOLT_COMMAND_RECORD}"
    --command-output-path "${BOLT_COMMAND_OUTPUT}"
    --readelf /usr/bin/readelf
    --objcopy /usr/bin/objcopy
    --test-mode
    --fixture-quality-mode
    --test-project-lock "${FIXTURE_PROJECT_LOCK}"
    --test-generation-lock "${FIXTURE_GENERATION_LOCK}"
)
for option in "${BOLT_OPTIONS[@]}"; do
    REGISTER_ARGUMENTS+=(--bolt-option="${option}")
done
"${REGISTER_TOOL}" "${REGISTER_ARGUMENTS[@]}" \
    >"${REGISTER_STDOUT}" 2>"${REGISTER_STDERR}" || \
    { cat -- "${REGISTER_STDERR}" >&2; fail 'production BOLT output registration rejected the exact real-tool provenance'; }
chmod 0600 -- "${REGISTER_STDOUT}" "${REGISTER_STDERR}"

OUTPUT_MANIFEST=${CACHE_ROOT}/outputs/${SUCCESS_FINGERPRINT}/manifest.json
[[ -s ${OUTPUT_MANIFEST} ]] || fail 'production output registrar published no manifest'
OUTPUT_OBJECT=$(python3 - "${MANIFEST}" "${OUTPUT_MANIFEST}" <<'PY'
import json
import sys

capture = json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"][0]
output_manifest = json.load(open(sys.argv[2], encoding="utf-8"))
assert output_manifest["expected_eligible_count"] == 1
assert len(output_manifest["outputs"]) == 1
output = output_manifest["outputs"][0]
abi_keys = {
    "elf_class",
    "elf_data",
    "elf_ident_version",
    "elf_header_version",
    "elf_osabi",
    "elf_abi_version",
    "elf_flags",
    "elf_type",
    "machine",
    "elf_role",
    "interpreter",
    "needed",
    "soname",
    "rpath",
    "runpath",
    "exported_dynamic_symbols",
    "symbol_version_names",
    "symbol_version_files",
    "symbol_version_mappings",
    "cet_properties",
    "gnu_stack_policy",
    "has_gnu_relro",
    "bind_now",
    "dynamic_flags",
    "has_textrel",
    "has_writable_executable_load",
    "tls_segments",
}
assert output["artifact_id"] == capture["artifact_id"]
assert set(output["source_abi_security_identity"]) == abi_keys
assert set(output["abi_security_identity"]) == abi_keys
assert output["source_abi_security_identity"] == output["abi_security_identity"]
assert output["source_abi_security_identity"] == {
    key: capture[key] for key in output["source_abi_security_identity"]
}
assert output["option_policy_revision"] == "gentoo-system-wide-bolt-v1-cdsort-20260712"
assert output["command"]["exit_status"] == 0
print(output["output_object"])
PY
)
REGISTERED_OUTPUT=${CACHE_ROOT}/outputs/${SUCCESS_FINGERPRINT}/${OUTPUT_OBJECT}
[[ -s ${REGISTERED_OUTPUT} ]] || fail 'registered real BOLT output object is absent'

# A genuine bolt-deploy Portage install must see the original object in the
# package hook and replace it only in the lexically-last install-QA hook.
: > "${DEPLOY_SWITCH}"
ebuild "${EBUILD}" clean >"${WORK}/pre-deploy-clean.log" 2>&1
ebuild "${EBUILD}" install >"${WORK}/install-deploy.log" 2>&1
STAGED_EXECUTABLE=${BUILD_ROOT}/image/usr/bin/phase2-portage-fixture
[[ -x ${STAGED_EXECUTABLE} ]] || fail 'bolt-deploy install did not stage its executable'
[[ -f ${BUILD_ROOT}/.installed ]] || fail 'successful bolt-deploy lacks its .installed marker'
[[ $(<"${BUILD_ROOT}/temp/previous-hook.log") == previous-hook-passed ]] || \
    fail 'package post_src_install did not run before BOLT deployment'
readelf -SW "${STAGED_EXECUTABLE}" | grep -F '.note.bolt_info' >/dev/null || \
    fail 'deployed executable lacks .note.bolt_info'
readelf -SW "${STAGED_EXECUTABLE}" | grep -F '.bolt.org.text' >/dev/null || \
    fail 'deployed executable lacks .bolt.org.text'
[[ $(sha256sum "${STAGED_EXECUTABLE}" | awk '{print $1}') == \
    $(sha256sum "${REGISTERED_OUTPUT}" | awk '{print $1}') ]] || \
    fail 'deployed executable differs from the exact registered BOLT object'
[[ $("${STAGED_EXECUTABLE}") == 42 ]] || fail 'deployed BOLT executable failed runtime smoke test'

# Remove the sole registered output and prove the fatal deploy path clears the
# marker that would otherwise let Portage skip the next install attempt.
ebuild "${EBUILD}" clean >"${WORK}/pre-deploy-failure-clean.log" 2>&1
REGISTERED_OUTPUT_SAVED=${REGISTERED_OUTPUT}.saved
mv -- "${REGISTERED_OUTPUT}" "${REGISTERED_OUTPUT_SAVED}"
if ebuild "${EBUILD}" install >"${WORK}/install-deploy-failure.log" 2>&1; then
    fail 'real Portage install accepted a missing registered BOLT output'
fi
[[ ! -e ${BUILD_ROOT}/.installed ]] || \
    fail 'failed bolt-deploy left Portage .installed behind'
mv -- "${REGISTERED_OUTPUT_SAVED}" "${REGISTERED_OUTPUT}"
ebuild "${EBUILD}" install >"${WORK}/install-deploy-retry.log" 2>&1
[[ -f ${BUILD_ROOT}/.installed ]] || fail 'bolt-deploy retry did not complete install phase'
[[ $("${STAGED_EXECUTABLE}") == 42 ]] || fail 'bolt-deploy retry failed runtime smoke test'

# A clean off-mode build must run the same package hook yet publish no capture.
ebuild "${EBUILD}" clean >"${WORK}/pre-off-clean.log" 2>&1
rm -rf -- "${CACHE_ROOT}/inputs/${SUCCESS_FINGERPRINT}"
rm -f -- "${PROXY_MODE_SWITCH}" "${DEPLOY_SWITCH}"
: > "${OFF_SWITCH}"
ebuild "${EBUILD}" install >"${WORK}/install-off.log" 2>&1
[[ ! -e ${MANIFEST} ]] || fail 'off-mode real Portage build unexpectedly published a capture'
[[ -f ${BUILD_ROOT}/.installed ]] || fail 'off-mode disposable install did not complete'

ebuild "${EBUILD}" clean >"${WORK}/final-clean.log" 2>&1
printf 'PASS: real Portage capture/deploy hooks, exact BOLT provenance, fatal markers, retries, and off mode\n'
