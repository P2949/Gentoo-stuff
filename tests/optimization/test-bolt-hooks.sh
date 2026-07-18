#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
CAPTURE=${ROOT}/scripts/optimization/bolt/capture-input.sh
REGISTER=${ROOT}/scripts/optimization/bolt/register-output.sh
DEPLOY=${ROOT}/scripts/optimization/bolt/deploy-output.sh
WORK=$(mktemp -d -t gentoo-bolt-hooks.XXXXXX)
cleanup() {
    if [[ ${BOLT_HOOK_TEST_KEEP_WORK:-0} == 1 ]]; then
        printf 'INFO: preserved BOLT hook fixture work tree: %s\n' "${WORK}" >&2
    else
        rm -rf -- "${WORK}"
    fi
}
trap cleanup EXIT HUP INT TERM

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

record_required_subtest() {
    local status=$1 name=$2 detail=$3
    [[ -n ${GENTOO_OPT_SUBTEST_RESULTS:-} ]] || return 0
    printf '%s\trequired\t%s\t%s\n' "${status}" "${name}" "${detail}" \
        >>"${GENTOO_OPT_SUBTEST_RESULTS}"
}

for command in python3 readelf objcopy cc flock; do
    command -v "${command}" >/dev/null 2>&1 || fail "missing required command: ${command}"
done

ED=${WORK}/ed
CACHE=${WORK}/cache
SOURCE=${WORK}/source
mkdir -p -- "${ED}/usr/bin" "${ED}/usr/lib64" "${ED}/usr/share/fixture" "${CACHE}" "${SOURCE}"
printf '%s\n' 'non-ELF package payload' >"${ED}/usr/share/fixture/data.txt"
FINGERPRINT=$(printf 'capture-deploy-fixture' | sha256sum | awk '{print $1}')
MAIN_EXPECTED_ELIGIBLE_COUNT=4

make_inventory_proof() {
    local proof=$1 fingerprint=$2 count=$3 candidates_json=$4
    local evidence=${proof}.inventory.json
    python3 - "${proof}" "${evidence}" "${fingerprint}" "${count}" "${candidates_json}" <<'PY'
import hashlib, json, pathlib, sys
proof, evidence, fingerprint, count, candidates = sys.argv[1:]
candidate_documents=json.loads(candidates)
evidence_path = pathlib.Path(evidence).resolve()
cpv="app-test/bolt-fixture-1"
entry_sha=hashlib.sha256((cpv+fingerprint).encode()).hexdigest()
inventory={"schema_version":2,"record_type":"frozen-inventory","generation_id":"fixture-generation-v1",
"inventory_id":"fixture-inventory-v1","packages":[{"cpv":cpv,"entry_sha256":entry_sha}],
"owned_paths":sorted([{"owner_cpv":cpv,"path":"/"+path} for item in candidate_documents for path in item["paths"]],key=lambda x:(x["owner_cpv"],x["path"])),
"owned_directories":[]}
evidence_path.write_text(json.dumps(inventory,sort_keys=True,separators=(",",":"))+"\n")
record = {
    "path": str(evidence_path),
    "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
    "size": evidence_path.stat().st_size,
}
document = {
    "schema": "gentoo-optimization-bolt-inventory-proof-v1",
    "generation_id": "fixture-generation-v1",
    "inventory_id": "fixture-inventory-v1",
    "cpv": cpv,
    "inventory_entry_sha256": entry_sha,
    "package_fingerprint": fingerprint,
    "expected_eligible_count": int(count),
    "inventory_evidence": record,
    "candidates": candidate_documents,
}
pathlib.Path(proof).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
PY
}

MAIN_INVENTORY_PROOF=${WORK}/main-inventory-proof.json
MAIN_CANDIDATES=$(python3 - <<'PY'
import hashlib, json
items = []
for canonical, paths, elf_type, role in (
    ("usr/bin/fixed", ["usr/bin/fixed", "usr/bin/fixed-hardlink"], "EXEC", "fixed-executable"),
    ("usr/bin/pie", ["usr/bin/pie"], "DYN", "pie-executable"),
    ("usr/bin/static-pie", ["usr/bin/static-pie"], "DYN", "pie-executable"),
    ("usr/lib64/libfixture.so.1", ["usr/lib64/libfixture.so.1"], "DYN", "shared-object"),
):
    items.append({
        "artifact_id": hashlib.sha256(canonical.encode()).hexdigest(),
        "canonical_path": canonical, "paths": paths, "hardlink_count": len(paths),
        "elf_class": "ELF64", "elf_data": "2's complement, little endian",
        "elf_type": elf_type, "machine": "Advanced Micro Devices X86-64", "elf_role": role,
    })
print(json.dumps(items))
PY
)
make_inventory_proof "${MAIN_INVENTORY_PROOF}" "${FINGERPRINT}" \
    "${MAIN_EXPECTED_ELIGIBLE_COUNT}" "${MAIN_CANDIDATES}"
DEPLOY_QUALITY_ARGS=(--inventory-proof "${MAIN_INVENTORY_PROOF}" --fixture-quality-mode)

printf '%s\n' \
    '#include <stdio.h>' \
    'int helper(int value) { return value + 7; }' \
    'int main(void) { printf("%d\n", helper(35)); return 0; }' \
    >"${SOURCE}/main.c"
printf '%s\n' \
    'int exported_fixture(int value) { return value * 3; }' \
    >"${SOURCE}/library.c"
ORIGIN_LITERAL=\$ORIGIN

cc -O2 -g -fno-omit-frame-pointer -no-pie \
    -Wl,--build-id=sha1,--emit-relocs,-z,ibt,-z,shstk,-z,relro,-z,now,-z,noexecstack,--enable-new-dtags,-rpath,"${ORIGIN_LITERAL}/../lib64" \
    "${SOURCE}/main.c" -o "${ED}/usr/bin/fixed"
cc -O2 -g -fno-omit-frame-pointer -fPIE -pie \
    -Wl,--build-id=sha1,--emit-relocs,-z,ibt,-z,shstk,-z,relro,-z,now,-z,noexecstack \
    "${SOURCE}/main.c" -o "${ED}/usr/bin/pie"
cc -O2 -g -fno-omit-frame-pointer -static-pie \
    -Wl,--build-id=sha1,--emit-relocs,-z,ibt,-z,shstk,-z,relro,-z,now,-z,noexecstack \
    "${SOURCE}/main.c" -o "${ED}/usr/bin/static-pie"
cc -O2 -g -fno-omit-frame-pointer -fPIC -shared \
    -Wl,--build-id=sha1,--emit-relocs,-soname,libfixture.so.1,-z,ibt,-z,shstk,-z,relro,-z,now,-z,noexecstack,--disable-new-dtags,-rpath,"${ORIGIN_LITERAL}" \
    "${SOURCE}/library.c" -o "${ED}/usr/lib64/libfixture.so.1"
ln -- "${ED}/usr/bin/fixed" "${ED}/usr/bin/fixed-hardlink"
ln -s -- fixed-hardlink "${ED}/usr/bin/fixed-symlink"
chmod 4755 -- "${ED}/usr/bin/fixed"

XATTR_SUPPORTED=false
if setfattr -n user.gentoo-bolt-test -v preserved -- "${ED}/usr/bin/fixed" 2>/dev/null; then
    XATTR_SUPPORTED=true
else
    record_required_subtest SKIP metadata.user-xattr \
        'user xattrs unavailable on fixture filesystem'
    printf 'INFO: user xattrs unavailable; recorded required subtest SKIP\n'
fi

CAPABILITY_SUPPORTED=false
if command -v setcap >/dev/null 2>&1 && command -v getcap >/dev/null 2>&1 && \
        setcap cap_net_bind_service=ep "${ED}/usr/bin/fixed" 2>/dev/null; then
    CAPABILITY_SUPPORTED=true
else
    record_required_subtest SKIP metadata.file-capability \
        'file-capability setup unavailable to the current user or filesystem'
    printf 'INFO: file capabilities unavailable; recorded required subtest SKIP\n'
fi

# Hermetic writer-lock contract: framework (implicit in test mode), then exact
# project, generation, and fingerprint.  Content mismatch and a busy project
# must fail before the fingerprint inode is created; while waiting on a held
# fingerprint lock, both generation locks remain held exclusively.
PROJECT_LOCK_FIXTURE=${WORK}/project.lock
GENERATION_LOCK_FIXTURE=${WORK}/generation.lock
python3 - "${MAIN_INVENTORY_PROOF}" "${PROJECT_LOCK_FIXTURE}" "${GENERATION_LOCK_FIXTURE}" <<'PY'
import json,pathlib,sys
proof=json.load(open(sys.argv[1])); inv=proof["inventory_evidence"]
payload=json.dumps({"generation_id":proof["generation_id"],"inventory_id":proof["inventory_id"],"inventory_sha256":inv["sha256"]},indent=2,sort_keys=True)+"\n"
for path in sys.argv[2:]: pathlib.Path(path).write_text(payload)
PY
chmod 0600 "${PROJECT_LOCK_FIXTURE}" "${GENERATION_LOCK_FIXTURE}"
cp "${PROJECT_LOCK_FIXTURE}" "${WORK}/project.lock.good"
printf '%s\n' mismatch >"${PROJECT_LOCK_FIXTURE}"
if "${CAPTURE}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
        --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        --inventory-proof "${MAIN_INVENTORY_PROOF}" \
        --test-project-lock "${PROJECT_LOCK_FIXTURE}" \
        --test-generation-lock "${GENERATION_LOCK_FIXTURE}" \
        >"${WORK}/lock-content.out" 2>"${WORK}/lock-content.err"; then
    fail 'transaction accepted project lock content for another generation'
fi
grep -Fq 'does not match the exact proof generation' "${WORK}/lock-content.err" || \
    fail 'generation-lock content rejection lacked an exact reason'
[[ ! -e ${CACHE}/locks/${FINGERPRINT}.lock ]] || \
    fail 'fingerprint lock was created before generation content validation'
cp "${WORK}/project.lock.good" "${PROJECT_LOCK_FIXTURE}"
chmod 0640 "${PROJECT_LOCK_FIXTURE}"
if "${CAPTURE}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
        --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        --inventory-proof "${MAIN_INVENTORY_PROOF}" \
        --test-project-lock "${PROJECT_LOCK_FIXTURE}" \
        --test-generation-lock "${GENERATION_LOCK_FIXTURE}" \
        >"${WORK}/fixture-lock-mode.out" 2>"${WORK}/fixture-lock-mode.err"; then
    fail 'fixture transaction accepted a group-readable project lock'
fi
grep -Fq 'owner-private mode 0600' "${WORK}/fixture-lock-mode.err" || \
    fail 'fixture lock-mode rejection lacked its exact reason'
chmod 0600 "${PROJECT_LOCK_FIXTURE}"
(
    exec 8<"${PROJECT_LOCK_FIXTURE}"
    flock -x 8
    : >"${WORK}/project-busy-ready"
    sleep 30
) &
PROJECT_BUSY_PID=$!
for _ in {1..100}; do [[ -e ${WORK}/project-busy-ready ]] && break; sleep 0.01; done
if "${CAPTURE}" --test-mode --lock-timeout-seconds 0.2 --ed "${ED}" \
        --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        --inventory-proof "${MAIN_INVENTORY_PROOF}" \
        --test-project-lock "${PROJECT_LOCK_FIXTURE}" \
        --test-generation-lock "${GENERATION_LOCK_FIXTURE}" \
        >"${WORK}/project-busy.out" 2>"${WORK}/project-busy.err"; then
    fail 'transaction ignored a busy project writer lock'
fi
kill "${PROJECT_BUSY_PID}" 2>/dev/null || true; wait "${PROJECT_BUSY_PID}" 2>/dev/null || true
[[ ! -e ${CACHE}/locks/${FINGERPRINT}.lock ]] || \
    fail 'fingerprint lock was created before busy project lock acquisition'

mkdir -p "${CACHE}/locks"; chmod 0700 "${CACHE}/locks"
exec 7>"${CACHE}/locks/${FINGERPRINT}.lock"; chmod 0600 "${CACHE}/locks/${FINGERPRINT}.lock"
flock -x 7
"${CAPTURE}" --test-mode --lock-timeout-seconds 5 --ed "${ED}" \
    --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
    --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
    --inventory-proof "${MAIN_INVENTORY_PROOF}" \
    --test-project-lock "${PROJECT_LOCK_FIXTURE}" \
    --test-generation-lock "${GENERATION_LOCK_FIXTURE}" \
    >"${WORK}/ordered-lock.out" 2>"${WORK}/ordered-lock.err" &
ORDERED_LOCK_PID=$!
PROJECT_HELD=false
for _ in {1..200}; do
    if ! flock -n "${PROJECT_LOCK_FIXTURE}" -c true 2>/dev/null; then PROJECT_HELD=true; break; fi
    sleep 0.01
done
[[ ${PROJECT_HELD} == true ]] || fail 'transaction did not hold project lock while waiting on fingerprint'
if flock -n "${GENERATION_LOCK_FIXTURE}" -c true 2>/dev/null; then
    fail 'transaction did not hold generation lock while waiting on fingerprint'
fi
flock -u 7; exec 7>&-
wait "${ORDERED_LOCK_PID}" || fail 'ordered generation/fingerprint transaction failed'

BEFORE_TREE=${WORK}/before-tree
find "${ED}" -xdev -printf '%P\t%y\t%m\t%U\t%G\t%s\t%i\t%l\n' | sort >"${BEFORE_TREE}"
"${CAPTURE}" --test-mode --ed "${ED}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
    --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
    --inventory-proof "${MAIN_INVENTORY_PROOF}" \
    >"${WORK}/capture.out"
MANIFEST=${CACHE}/inputs/${FINGERPRINT}/manifest.json
[[ -s ${MANIFEST} ]] || fail 'capture manifest was not published'
LOCK_FILE=${CACHE}/locks/${FINGERPRINT}.lock
[[ -f ${LOCK_FILE} && ! -L ${LOCK_FILE} ]] || fail 'fingerprint lock is absent or a symlink'
[[ $(stat -c '%u' "${LOCK_FILE}") == $(id -u) ]] || fail 'fingerprint lock has the wrong owner'
[[ $(stat -c '%a' "${LOCK_FILE}") == 600 ]] || fail 'fingerprint lock is not mode 0600'
find "${ED}" -xdev -printf '%P\t%y\t%m\t%U\t%G\t%s\t%i\t%l\n' | sort >"${WORK}/after-tree"
cmp -s -- "${BEFORE_TREE}" "${WORK}/after-tree" || fail 'capture mutated ED metadata or topology'

python3 - "${MANIFEST}" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["schema"] == "gentoo-optimization-bolt-capture-v3"
assert manifest["elf_total"] == 4
assert manifest["eligible_total"] == 4
assert manifest["ineligible_total"] == 0
assert len(manifest["artifacts"]) == 4
assert [item["elf_class"] for item in manifest["artifacts"]] == ["ELF64"] * 4
assert {item["elf_type"] for item in manifest["artifacts"]} == {"EXEC", "DYN"}
assert all(item["machine"] == "Advanced Micro Devices X86-64" for item in manifest["artifacts"])
assert all(item["has_symtab"] and item["symbol_count"] for item in manifest["artifacts"])
assert all(item["defined_function_symbols"] > 0 for item in manifest["artifacts"])
assert all(item["text_relocation_sections"] for item in manifest["artifacts"])
assert all(item["build_id"] and item["text_sha256"] for item in manifest["artifacts"])
assert {item["elf_role"] for item in manifest["artifacts"]} == {
    "fixed-executable", "pie-executable", "shared-object"
}
assert all(item["elf_data"] == "2's complement, little endian" for item in manifest["artifacts"])
assert all(item["elf_ident_version"] == "1 (current)" for item in manifest["artifacts"])
assert all(item["elf_header_version"] == "0x1" for item in manifest["artifacts"])
assert all(item["gnu_stack_policy"] == "non-executable" for item in manifest["artifacts"])
assert all(item["has_gnu_relro"] and item["bind_now"] for item in manifest["artifacts"])
assert all(item["cet_properties"] for item in manifest["artifacts"])
fixed = next(item for item in manifest["artifacts"] if item["elf_role"] == "fixed-executable")
pie = next(item for item in manifest["artifacts"] if item["canonical_path"] == "usr/bin/pie")
static_pie = next(item for item in manifest["artifacts"] if item["canonical_path"] == "usr/bin/static-pie")
dso = next(item for item in manifest["artifacts"] if item["elf_role"] == "shared-object")
assert fixed["interpreter"] and pie["interpreter"]
assert static_pie["elf_role"] == "pie-executable" and static_pie["interpreter"] is None
assert any(item["tag"] == "FLAGS_1" and "PIE" in item["value"].split() for item in static_pie["dynamic_flags"])
assert dso["interpreter"] is None and dso["soname"] == "libfixture.so.1"
assert not any(item["tag"] == "FLAGS_1" and "PIE" in item["value"].split() for item in dso["dynamic_flags"])
assert fixed["runpath"] == ["$ORIGIN/../lib64"] and fixed["rpath"] == []
assert dso["rpath"] == ["$ORIGIN"] and dso["runpath"] == []
assert all(item["needed"] for item in (fixed, pie))
assert any(symbol["name"].startswith("exported_fixture") for symbol in dso["exported_dynamic_symbols"])
assert all(item["readiness_failures"] == [] for item in manifest["artifacts"])
assert all("terminal_reasons" not in item for item in manifest["artifacts"])
hardlinks = [item for item in manifest["artifacts"] if item["hardlink_count"] == 2]
assert len(hardlinks) == 1
assert hardlinks[0]["paths"] == ["usr/bin/fixed", "usr/bin/fixed-hardlink"]
assert manifest["ed_identity"]["symlinks"] == [
    {"gid": manifest["ed_identity"]["symlinks"][0]["gid"], "path": "usr/bin/fixed-symlink", "target": "fixed-hardlink", "uid": manifest["ed_identity"]["symlinks"][0]["uid"]}
]
assert manifest["inventory_proof"]["document"]["generation_id"] == "fixture-generation-v1"
assert hardlinks[0]["metadata"]["mode"] == "4755"
PY

# An interrupted caller may retry capture with the same fingerprint. Adoption
# is permitted only after a complete fresh capture is byte-identical.
CAPTURE_HASH=$(sha256sum "${MANIFEST}" | awk '{print $1}')
"${CAPTURE}" --test-mode --ed "${ED}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
    --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
    --inventory-proof "${MAIN_INVENTORY_PROOF}" \
    >"${WORK}/capture-retry.out"
[[ $(sha256sum "${MANIFEST}" | awk '{print $1}') == "${CAPTURE_HASH}" ]] || \
    fail 'byte-identical capture retry changed the authoritative manifest'
cp -- "${ED}/usr/bin/pie" "${WORK}/pie.pre-mismatch"
printf 'mismatch\n' >>"${ED}/usr/bin/pie"
if "${CAPTURE}" --test-mode --ed "${ED}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        --inventory-proof "${MAIN_INVENTORY_PROOF}" \
        >"${WORK}/capture-mismatch.out" 2>"${WORK}/capture-mismatch.err"; then
    fail 'capture retry adopted mismatching object contents'
fi
grep -Fq 'quarantined candidate' "${WORK}/capture-mismatch.err" || \
    fail 'mismatching capture retry lacked quarantine evidence'
find "${CACHE}/quarantine/capture-mismatch" -mindepth 1 -maxdepth 1 -type d -print -quit | \
    grep -q . || fail 'mismatching fresh capture was not quarantined'
cp -- "${WORK}/pie.pre-mismatch" "${ED}/usr/bin/pie"

COUNT_MISMATCH_PROOF=${WORK}/count-mismatch-proof.json
make_inventory_proof "${COUNT_MISMATCH_PROOF}" "${FINGERPRINT}" 3 \
    "$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])[:3]))' "${MAIN_CANDIDATES}")"
if "${CAPTURE}" --test-mode --ed "${ED}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count 3 --inventory-proof "${COUNT_MISMATCH_PROOF}" >"${WORK}/eligible-count-mismatch.out" \
        2>"${WORK}/eligible-count-mismatch.err"; then
    fail 'capture accepted an eligible count different from the frozen inventory'
fi
grep -Fq 'expected=3, actual=4' "${WORK}/eligible-count-mismatch.err" || \
    fail 'eligible-count mismatch lacked exact expected/actual evidence'
WRONG_CANDIDATE_FINGERPRINT=$(printf wrong-candidate | sha256sum | awk '{print $1}')
WRONG_CANDIDATE_PROOF=${WORK}/wrong-candidate-proof.json
WRONG_CANDIDATES=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d[0]["elf_role"]="shared-object"; print(json.dumps(d))' "${MAIN_CANDIDATES}")
make_inventory_proof "${WRONG_CANDIDATE_PROOF}" "${WRONG_CANDIDATE_FINGERPRINT}" \
    "${MAIN_EXPECTED_ELIGIBLE_COUNT}" "${WRONG_CANDIDATES}"
if "${CAPTURE}" --test-mode --ed "${ED}" --cache-root "${WORK}/wrong-candidate-cache" \
        --fingerprint "${WRONG_CANDIDATE_FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        --inventory-proof "${WRONG_CANDIDATE_PROOF}" \
        >"${WORK}/wrong-candidate.out" 2>"${WORK}/wrong-candidate.err"; then
    fail 'capture accepted inventory proof for a different candidate role/path set'
fi
grep -Fq 'candidate paths/artifact facts differ' "${WORK}/wrong-candidate.err" || \
    fail 'candidate fact mismatch lacked an exact rejection'
for inventory_tamper in cpv entry_sha generation_id; do
    TAMPER_FINGERPRINT=$(printf 'inventory-%s' "${inventory_tamper}" | sha256sum | awk '{print $1}')
    TAMPER_PROOF=${WORK}/inventory-${inventory_tamper}-proof.json
    make_inventory_proof "${TAMPER_PROOF}" "${TAMPER_FINGERPRINT}" \
        "${MAIN_EXPECTED_ELIGIBLE_COUNT}" "${MAIN_CANDIDATES}"
    python3 - "${TAMPER_PROOF}" "${inventory_tamper}" <<'PY'
import json,sys
p,kind=sys.argv[1:]; d=json.load(open(p))
if kind=="cpv": d["cpv"]="app-test/not-in-inventory-1"
elif kind=="entry_sha": d["inventory_entry_sha256"]="0"*64
else: d["generation_id"]="self-asserted-generation"
open(p,"w").write(json.dumps(d,sort_keys=True)+"\n")
PY
    if "${CAPTURE}" --test-mode --ed "${ED}" --cache-root "${WORK}/inventory-${inventory_tamper}-cache" \
            --fingerprint "${TAMPER_FINGERPRINT}" \
            --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
            --inventory-proof "${TAMPER_PROOF}" \
            >"${WORK}/inventory-${inventory_tamper}.out" 2>"${WORK}/inventory-${inventory_tamper}.err"; then
        fail "capture accepted self-asserted frozen inventory binding: ${inventory_tamper}"
    fi
done
while IFS= read -r cached_object; do
    [[ $(stat -c '%a' "${CACHE}/inputs/${FINGERPRINT}/${cached_object}") == 600 ]] || \
        fail "captured object is not private: ${cached_object}"
done < <(python3 - "${MANIFEST}" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"]:
    if item["cache_object"]:
        print(item["cache_object"])
PY
)

# The transaction fixture uses an explicit hermetic BOLT stand-in. It emits
# the same structured GNU BOLT note and nonempty origin-code section required
# from production output; a note-only objcopy forgery is tested and rejected
# below. Tool identity and every invocation input are still exact and hashed.
FAKE_BOLT=${WORK}/llvm-bolt-fixture
printf '%s\n' \
    '#!/usr/bin/python3' \
    'import os, pathlib, shlex, shutil, struct, subprocess, sys, tempfile' \
    'if sys.argv[1:] == ["--version"]:' \
    '    print("LLVM (fixture):\n  LLVM version 22.1.8\n  BOLT revision fixture")' \
    '    raise SystemExit(0)' \
    'tool = os.path.realpath(sys.argv[0])' \
    'argv = [tool, *sys.argv[1:]]' \
    'source = pathlib.Path(sys.argv[1])' \
    'output = pathlib.Path(sys.argv[sys.argv.index("-o") + 1])' \
    'shutil.copyfile(source, output)' \
    'with tempfile.TemporaryDirectory() as temporary:' \
    '    root = pathlib.Path(temporary)' \
    '    text = root / "text"' \
    '    rewrite = root / "rewrite"' \
    '    subprocess.run(["objcopy", "--dump-section", f".text={text}", str(source), str(rewrite)], check=True)' \
    '    description = f"BOLT revision: fixture, command line: {shlex.join(argv)}".encode()' \
    '    name = b"GNU\0"' \
    '    note = struct.pack("<III", len(name), len(description), 4) + name + description' \
    '    note += b"\0" * ((-len(description)) % 4)' \
    '    note_path = root / "note"' \
    '    note_path.write_bytes(note)' \
    '    subprocess.run(["objcopy", "--add-section", f".bolt.org.text={text}", "--set-section-flags", ".bolt.org.text=code,readonly", "--add-section", f".note.bolt_info={note_path}", "--set-section-flags", ".note.bolt_info=readonly", str(output)], check=True)' \
    >"${FAKE_BOLT}"
chmod 0755 -- "${FAKE_BOLT}"
FDATA=${WORK}/merged.fdata
printf '1 main 100\n' >"${FDATA}"
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

make_quality_proofs() {
    local artifact_id=$1 input=$2 workload=$3 profile=$4 fdata_quality=$5
    python3 - "${artifact_id}" "${input}" "${FDATA}" "${FAKE_BOLT}" \
        "${workload}" "${profile}" "${fdata_quality}" "${FINGERPRINT}" <<'PY'
import hashlib, json, pathlib, sys
artifact_id, source, fdata, tool, workload_path, profile_path, fdata_path, fingerprint = sys.argv[1:]
def identity(value):
    path = pathlib.Path(value).resolve(strict=True)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
source_path = pathlib.Path(source)
import subprocess, re
notes = subprocess.run(["readelf", "-nW", source], check=True, text=True, capture_output=True).stdout
build_id = re.search(r"Build ID:\s*([0-9a-fA-F]+)", notes).group(1).lower()
with pathlib.Path(source).open("rb") as stream: pass
tmp = pathlib.Path(str(workload_path) + ".text")
rewrite = pathlib.Path(str(workload_path) + ".rewrite")
subprocess.run(["objcopy", "--dump-section", f".text={tmp}", source, str(rewrite)], check=True)
text_hash = hashlib.sha256(tmp.read_bytes()).hexdigest()
tmp.unlink(); rewrite.unlink(missing_ok=True)
binding = {
    "generation_id": "fixture-generation-v1", "inventory_id": "fixture-inventory-v1",
    "package_fingerprint": fingerprint, "artifact_id": artifact_id,
    "input_build_id": build_id, "input_text_sha256": text_hash,
    "input_file_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
}
workload = {"schema":"gentoo-optimization-bolt-workload-proof-v1", **binding,
    "workload_id":"hermetic-runtime-fixture", "workload_definition":identity(source),
    "workload_log":identity(source), "command_record":None, "started_at_utc":"2026-07-13T00:00:00Z",
    "completed_at_utc":"2026-07-13T00:00:01Z", "exit_status":0, "repetitions":1,
    "functional_passed":True, "fixture_only":True}
pathlib.Path(workload_path).write_text(json.dumps(workload,sort_keys=True)+"\n")
workload_identity = identity(workload_path)
fdata_identity = identity(fdata)
profile = {"schema":"gentoo-optimization-bolt-profile-quality-proof-v1", **binding,
    "profile_tools":[identity(tool)], "events":["cycles:u","branches:u"],
    "profile_files":[identity(source)], "command_records":[], "lbr_captured":True,
    "sample_count":1, "branch_entry_count":1, "ignored_samples":0,
    "mismatching_samples":0, "out_of_range_samples":0,
    "thresholds":{"minimum_samples":1,"minimum_branch_entries":1,"maximum_ignored_samples":0,
        "maximum_mismatch_ratio":0.0,"maximum_out_of_range_ratio":0.0},
    "workload_contributors":[workload_identity], "fdata":[fdata_identity], "fixture_only":True}
pathlib.Path(profile_path).write_text(json.dumps(profile,sort_keys=True)+"\n")
profile_identity = identity(profile_path)
quality = {"schema":"gentoo-optimization-bolt-fdata-quality-proof-v1", **binding,
    "merge_tool":identity(tool), "fdata":[fdata_identity], "profile_contributors":[profile_identity],
    "total_functions":1,"total_samples":1,"command_record":None,"fixture_only":True}
pathlib.Path(fdata_path).write_text(json.dumps(quality,sort_keys=True)+"\n")
PY
}

make_command_record() {
    local input=$1 output=$2 stdout=$3 stderr=$4 record=$5 workload=$6 profile=$7 fdata_quality=$8
    python3 - "${FAKE_BOLT}" "${input}" "${output}" "${FDATA}" \
        "${workload}" "${profile}" "${fdata_quality}" "${stdout}" "${stderr}" \
        "${record}" "${POLICY_REVISION}" "${BOLT_OPTIONS[@]}" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

tool, source, output, fdata, workload, profile, fdata_quality, stdout, stderr, record, policy, *options = sys.argv[1:]

def identity(value):
    path = pathlib.Path(value).resolve(strict=True)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
def bundle(value):
    record = identity(value)
    return {"identity": record, "document": json.loads(pathlib.Path(value).read_text())}

tool_record = identity(tool)
tool_record["version"] = subprocess.run([tool, "--version"], check=True, text=True, capture_output=True).stdout.strip()
input_record = identity(source)
output_record = identity(output)
fdata_records = [identity(fdata)]
argv = [str(pathlib.Path(tool).resolve()), str(pathlib.Path(source).resolve()), "-o", str(pathlib.Path(output).resolve()), f"-data={pathlib.Path(fdata).resolve()}", *options]
document = {
    "schema": "gentoo-optimization-bolt-command-v2",
    "argv": argv,
    "exit_status": 0,
    "started_at_utc": "2026-07-13T00:00:00Z",
    "completed_at_utc": "2026-07-13T00:00:01Z",
    "tool": tool_record,
    "input": input_record,
    "output": output_record,
    "option_policy_revision": policy,
    "options": options,
    "fdata": fdata_records,
    "workload_evidence": [bundle(workload)],
    "profile_evidence": [bundle(profile)],
    "fdata_quality_evidence": [bundle(fdata_quality)],
    "stdout": identity(stdout),
    "stderr": identity(stderr),
}
pathlib.Path(record).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

registration_arguments() {
    local record=$1 command_output=$2 workload=$3 profile=$4 fdata_quality=$5 option
    REGISTER_ARGUMENTS=(
        --llvm-bolt "${FAKE_BOLT}"
        --option-policy-revision "${POLICY_REVISION}"
        --fdata "${FDATA}"
        --workload-evidence "${workload}"
        --profile-evidence "${profile}"
        --fdata-quality-evidence "${fdata_quality}"
        --command-record "${record}"
        --command-output-path "${command_output}"
        --fixture-quality-mode
    )
    for option in "${BOLT_OPTIONS[@]}"; do
        REGISTER_ARGUMENTS+=(--bolt-option="${option}")
    done
}

while IFS=$'\t' read -r artifact_id object_file; do
    prepared=${WORK}/${artifact_id}.bolt
    command_output=${prepared}.partial
    stdout=${WORK}/${artifact_id}.bolt.stdout
    stderr=${WORK}/${artifact_id}.bolt.stderr
    command_record=${WORK}/${artifact_id}.bolt.command.json
    workload=${WORK}/${artifact_id}.workload.json
    profile=${WORK}/${artifact_id}.profile.json
    fdata_quality=${WORK}/${artifact_id}.fdata-quality.json
    make_quality_proofs "${artifact_id}" "${CACHE}/inputs/${FINGERPRINT}/${object_file}" \
        "${workload}" "${profile}" "${fdata_quality}"
    "${FAKE_BOLT}" "${CACHE}/inputs/${FINGERPRINT}/${object_file}" -o "${command_output}" \
        "-data=${FDATA}" "${BOLT_OPTIONS[@]}" >"${stdout}" 2>"${stderr}"
    make_command_record "${CACHE}/inputs/${FINGERPRINT}/${object_file}" "${command_output}" \
        "${stdout}" "${stderr}" "${command_record}" "${workload}" "${profile}" "${fdata_quality}"
    mv -- "${command_output}" "${prepared}"
    registration_arguments "${command_record}" "${command_output}" "${workload}" "${profile}" "${fdata_quality}"
    "${REGISTER}" --test-mode --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --artifact-id "${artifact_id}" \
        --input "${CACHE}/inputs/${FINGERPRINT}/${object_file}" --output "${prepared}" \
        "${REGISTER_ARGUMENTS[@]}" \
        >"${WORK}/register-${artifact_id}.out"
done < <(python3 - "${MANIFEST}" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"]:
    if item["eligible"]:
        print(item["artifact_id"], item["cache_object"], sep="\t")
PY
)

# Concurrent registration for the same fingerprint is serialized. The
# test-only hold occurs after flock acquisition, making the overlap and the
# lost-update regression deterministic.
CONCURRENT_CACHE=${WORK}/concurrent-cache
mkdir -p -- "${CONCURRENT_CACHE}/inputs"
cp -a -- "${CACHE}/inputs/${FINGERPRINT}" "${CONCURRENT_CACHE}/inputs/"
CONCURRENT_PIDS=()
while IFS=$'\t' read -r artifact_id object_file; do
    registration_arguments "${WORK}/${artifact_id}.bolt.command.json" "${WORK}/${artifact_id}.bolt.partial" \
        "${WORK}/${artifact_id}.workload.json" "${WORK}/${artifact_id}.profile.json" \
        "${WORK}/${artifact_id}.fdata-quality.json"
    "${REGISTER}" --test-mode --test-lock-hold-seconds 0.2 \
        --lock-timeout-seconds 5 --cache-root "${CONCURRENT_CACHE}" \
        --fingerprint "${FINGERPRINT}" --artifact-id "${artifact_id}" \
        --input "${CACHE}/inputs/${FINGERPRINT}/${object_file}" \
        --output "${WORK}/${artifact_id}.bolt" \
        "${REGISTER_ARGUMENTS[@]}" \
        >"${WORK}/concurrent-register-${artifact_id}.out" \
        2>"${WORK}/concurrent-register-${artifact_id}.err" &
    CONCURRENT_PIDS+=("$!")
done < <(python3 - "${MANIFEST}" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"]:
    if item["eligible"]:
        print(item["artifact_id"], item["cache_object"], sep="\t")
PY
)
for child in "${CONCURRENT_PIDS[@]}"; do
    wait "${child}" || fail "concurrent output registration failed: pid=${child}"
done

# Production uses the installer's exact root-owned publication lock in shared
# mode before creating/acquiring any per-fingerprint lock.  When the live
# framework is present, directly exercise that context and prove an installer
# exclusive lock cannot overlap it.
FRAMEWORK_LOCK=/run/gentoo-optimization/framework-install.lock
if ((EUID == 0)) && [[ -f ${FRAMEWORK_LOCK} && ! -L ${FRAMEWORK_LOCK} ]]; then
    FRAMEWORK_SHARED_READY=${WORK}/framework-shared-ready
    python3 - "${ROOT}/scripts/optimization/bolt/artifact_tool.py" \
        "${FRAMEWORK_SHARED_READY}" <<'PY' &
import importlib.util,pathlib,sys,time
spec=importlib.util.spec_from_file_location("bolt_artifact_tool_fixture",sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
with module.framework_publication_lock(False,5.0):
    pathlib.Path(sys.argv[2]).write_text("ready\n")
    time.sleep(2)
PY
    FRAMEWORK_SHARED_PID=$!
    for _ in {1..200}; do [[ -e ${FRAMEWORK_SHARED_READY} ]] && break; sleep 0.01; done
    [[ -e ${FRAMEWORK_SHARED_READY} ]] || fail 'production framework shared-lock fixture did not start'
    if flock -n "${FRAMEWORK_LOCK}" -c true; then
        fail 'installer exclusive lock overlapped a production BOLT shared transaction'
    fi
    wait "${FRAMEWORK_SHARED_PID}" || fail 'production framework shared-lock fixture failed'
    record_required_subtest PASS framework-lock.shared-exclusive \
        'live root framework shared lock excluded an installer exclusive lock'
else
    record_required_subtest SKIP framework-lock.shared-exclusive \
        'root-owned live framework lock unavailable'
    printf 'INFO: live framework lock unavailable; recorded required subtest SKIP\n'
fi
python3 - "${CONCURRENT_CACHE}/outputs/${FINGERPRINT}/manifest.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["schema"] == "gentoo-optimization-bolt-output-v3"
assert manifest["expected_eligible_count"] == 4
assert manifest["inventory_proof"]["document"]["expected_eligible_count"] == 4
assert len(manifest["outputs"]) == 4
assert len({item["artifact_id"] for item in manifest["outputs"]}) == 4
assert all(item["option_policy_revision"] == "gentoo-system-wide-bolt-v1-cdsort-20260712" for item in manifest["outputs"])
assert all(item["llvm_bolt"]["version"].startswith("LLVM (fixture):") for item in manifest["outputs"])
assert all(item["fdata"] and item["workload_evidence"] and item["profile_evidence"] for item in manifest["outputs"])
assert all(item["fdata_quality_evidence"] for item in manifest["outputs"])
assert all(item["command"]["exit_status"] == 0 for item in manifest["outputs"])
PY

# A held per-fingerprint lock bounds and rejects a conflicting deployment.
LOCK_READY=${WORK}/lock-ready
(
    exec 9>"${LOCK_FILE}"
    flock -x 9
    : >"${LOCK_READY}"
    sleep 30
) &
LOCK_HOLDER=$!
for _ in {1..100}; do
    [[ -e ${LOCK_READY} ]] && break
    sleep 0.01
done
[[ -e ${LOCK_READY} ]] || fail 'lock-holder fixture did not acquire the fingerprint lock'
if "${DEPLOY}" --test-mode --lock-timeout-seconds 0.2 --ed "${ED}" \
        --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        "${DEPLOY_QUALITY_ARGS[@]}" \
        >"${WORK}/lock-timeout.out" 2>"${WORK}/lock-timeout.err"; then
    fail 'deployment ignored a held per-fingerprint lock'
fi
grep -Fq 'timed out after 0.2s waiting for fingerprint lock' "${WORK}/lock-timeout.err" || \
    fail 'fingerprint lock timeout lacked a bounded reason'
kill "${LOCK_HOLDER}" 2>/dev/null || true
wait "${LOCK_HOLDER}" 2>/dev/null || true

cp -- "${MANIFEST}" "${WORK}/manifest.good"
FIRST_ID=$(python3 - "${MANIFEST}" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"][0]["artifact_id"])
PY
)
FIRST_OBJECT=$(python3 - "${MANIFEST}" "${FIRST_ID}" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(next(item["cache_object"] for item in manifest["artifacts"] if item["artifact_id"] == sys.argv[2]))
PY
)
OTHER_OBJECT=$(python3 - "${MANIFEST}" "${FIRST_ID}" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(next(item["cache_object"] for item in manifest["artifacts"] if item["artifact_id"] != sys.argv[2]))
PY
)
registration_arguments "${WORK}/${FIRST_ID}.bolt.command.json" "${WORK}/${FIRST_ID}.bolt.partial" \
    "${WORK}/${FIRST_ID}.workload.json" "${WORK}/${FIRST_ID}.profile.json" \
    "${WORK}/${FIRST_ID}.fdata-quality.json"
NO_FIXTURE_QUALITY_ARGUMENTS=()
for argument in "${REGISTER_ARGUMENTS[@]}"; do
    [[ ${argument} == --fixture-quality-mode ]] || NO_FIXTURE_QUALITY_ARGUMENTS+=("${argument}")
done
mkdir -p "${WORK}/quality-reject-cache/inputs"
cp -a "${CACHE}/inputs/${FINGERPRINT}" "${WORK}/quality-reject-cache/inputs/"
if "${REGISTER}" --test-mode --cache-root "${WORK}/quality-reject-cache" \
        --fingerprint "${FINGERPRINT}" --artifact-id "${FIRST_ID}" \
        --input "${CACHE}/inputs/${FINGERPRINT}/${FIRST_OBJECT}" \
        --output "${WORK}/${FIRST_ID}.bolt" "${NO_FIXTURE_QUALITY_ARGUMENTS[@]}" \
        >"${WORK}/synthetic-production-quality.out" 2>"${WORK}/synthetic-production-quality.err"; then
    fail 'registration accepted synthetic fixture quality without its bounded fixture gate'
fi
grep -Fq 'synthetic fixture-only quality evidence' "${WORK}/synthetic-production-quality.err" || \
    fail 'synthetic quality rejection lacked an exact reason'
BAD_LBR_PROFILE=${WORK}/bad-lbr-profile.json
python3 - "${WORK}/${FIRST_ID}.profile.json" "${BAD_LBR_PROFILE}" <<'PY'
import json,sys
document=json.load(open(sys.argv[1])); document["lbr_captured"]=False
open(sys.argv[2],"w").write(json.dumps(document,sort_keys=True)+"\n")
PY
BAD_LBR_ARGUMENTS=()
skip_next=false
for argument in "${REGISTER_ARGUMENTS[@]}"; do
    if [[ ${skip_next} == true ]]; then
        BAD_LBR_ARGUMENTS+=("${BAD_LBR_PROFILE}")
        skip_next=false
    elif [[ ${argument} == --profile-evidence ]]; then
        BAD_LBR_ARGUMENTS+=("${argument}")
        skip_next=true
    else
        BAD_LBR_ARGUMENTS+=("${argument}")
    fi
done
if "${REGISTER}" --test-mode --cache-root "${WORK}/quality-reject-cache" \
        --fingerprint "${FINGERPRINT}" --artifact-id "${FIRST_ID}" \
        --input "${CACHE}/inputs/${FINGERPRINT}/${FIRST_OBJECT}" \
        --output "${WORK}/${FIRST_ID}.bolt" "${BAD_LBR_ARGUMENTS[@]}" \
        >"${WORK}/bad-lbr.out" 2>"${WORK}/bad-lbr.err"; then
    fail 'registration accepted a quality proof with no LBR capture'
fi
grep -Fq 'lacks LBR capture' "${WORK}/bad-lbr.err" || \
    fail 'missing-LBR quality rejection lacked an exact reason'
FALSE_PRODUCTION_WORKLOAD=${WORK}/false-production-workload.json
python3 - "${WORK}/${FIRST_ID}.workload.json" "${FALSE_PRODUCTION_WORKLOAD}" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d["fixture_only"]=False
open(sys.argv[2],"w").write(json.dumps(d,sort_keys=True)+"\n")
PY
FALSE_PRODUCTION_ARGUMENTS=()
skip_next=false
for argument in "${REGISTER_ARGUMENTS[@]}"; do
    if [[ ${skip_next} == true ]]; then
        FALSE_PRODUCTION_ARGUMENTS+=("${FALSE_PRODUCTION_WORKLOAD}"); skip_next=false
    elif [[ ${argument} == --workload-evidence ]]; then
        FALSE_PRODUCTION_ARGUMENTS+=("${argument}"); skip_next=true
    else
        FALSE_PRODUCTION_ARGUMENTS+=("${argument}")
    fi
done
if "${REGISTER}" --test-mode --fixture-quality-mode \
        --cache-root "${WORK}/quality-reject-cache" --fingerprint "${FINGERPRINT}" \
        --artifact-id "${FIRST_ID}" --input "${CACHE}/inputs/${FINGERPRINT}/${FIRST_OBJECT}" \
        --output "${WORK}/${FIRST_ID}.bolt" "${FALSE_PRODUCTION_ARGUMENTS[@]}" \
        >"${WORK}/false-production.out" 2>"${WORK}/false-production.err"; then
    fail 'registration accepted caller-authored production quality without command proof'
fi
grep -Fq 'lacks an exact sanitized command record' "${WORK}/false-production.err" || \
    fail 'fabricated production quality rejection lacked an exact reason'

# A structurally valid GNU BOLT note alone is not proof that llvm-bolt
# transformed the object. Removing the origin code section must fail closed.
NOTE_ONLY=${WORK}/note-only.bolt
cp -- "${WORK}/${FIRST_ID}.bolt" "${NOTE_ONLY}"
objcopy --remove-section .bolt.org.text "${NOTE_ONLY}"
if "${REGISTER}" --test-mode --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --artifact-id "${FIRST_ID}" \
        --input "${CACHE}/inputs/${FINGERPRINT}/${FIRST_OBJECT}" \
        --output "${NOTE_ONLY}" "${REGISTER_ARGUMENTS[@]}" \
        >"${WORK}/note-only.out" 2>"${WORK}/note-only.err"; then
    fail 'registration accepted synthetic note-only BOLT output'
fi
grep -Fq 'lacks nonempty .bolt.org.text transformation evidence' "${WORK}/note-only.err" || \
    fail 'synthetic note-only rejection lacked an exact reason'

# Arbitrary standalone roots are never accepted without the explicit hermetic
# flag; production registration is pinned to the reviewed root-owned cache.
if "${REGISTER}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --artifact-id "${FIRST_ID}" \
        --input "${CACHE}/inputs/${FINGERPRINT}/${FIRST_OBJECT}" \
        --output "${WORK}/${FIRST_ID}.bolt" \
        "${REGISTER_ARGUMENTS[@]}" \
        >"${WORK}/production-scope.out" 2>"${WORK}/production-scope.err"; then
    fail 'arbitrary cache root was accepted without --test-mode'
fi
grep -Eq 'framework installer lock|production BOLT cache operations must run as root|production cache root must be exactly' \
    "${WORK}/production-scope.err" || fail 'production cache rejection lacked an exact reason'
PORTAGE_SCOPE_FINGERPRINT=$(printf 'portage-scope' | sha256sum | awk '{print $1}')
if env -u ED -u D -u PORTAGE_BUILDDIR \
        "${CAPTURE}" --ed "${NO_ELF_ED:-${ED}}" --cache-root "${CACHE}" \
        --fingerprint "${PORTAGE_SCOPE_FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        >"${WORK}/portage-scope.out" 2>"${WORK}/portage-scope.err"; then
    fail 'standalone ED was accepted without --test-mode/active Portage state'
fi
grep -Eq 'production BOLT hooks must run as root|production BOLT hook requires active Portage ED' \
    "${WORK}/portage-scope.err" || fail 'active Portage ED rejection lacked an exact reason'

LOCK_SYMLINK_CACHE=${WORK}/lock-symlink-cache
mkdir -p -- "${LOCK_SYMLINK_CACHE}/inputs" "${LOCK_SYMLINK_CACHE}/locks"
cp -a -- "${CACHE}/inputs/${FINGERPRINT}" "${LOCK_SYMLINK_CACHE}/inputs/"
ln -s -- "${WORK}/outside-lock" "${LOCK_SYMLINK_CACHE}/locks/${FINGERPRINT}.lock"
if "${REGISTER}" --test-mode --cache-root "${LOCK_SYMLINK_CACHE}" \
        --fingerprint "${FINGERPRINT}" --artifact-id "${FIRST_ID}" \
        --input "${LOCK_SYMLINK_CACHE}/inputs/${FINGERPRINT}/${FIRST_OBJECT}" \
        --output "${WORK}/${FIRST_ID}.bolt" \
        "${REGISTER_ARGUMENTS[@]}" \
        >"${WORK}/lock-symlink.out" 2>"${WORK}/lock-symlink.err"; then
    fail 'symlink fingerprint lock was accepted'
fi
grep -Fq 'cannot open non-symlink fingerprint lock' "${WORK}/lock-symlink.err" || \
    fail 'symlink fingerprint lock rejection lacked an exact reason'

if "${REGISTER}" --test-mode --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --artifact-id "${FIRST_ID}" \
        --input "${CACHE}/inputs/${FINGERPRINT}/${OTHER_OBJECT}" \
        --output "${WORK}/${FIRST_ID}.bolt" \
        "${REGISTER_ARGUMENTS[@]}" \
        >"${WORK}/wrong-input.out" 2>"${WORK}/wrong-input.err"; then
    fail 'output registration accepted the wrong exact BOLT input'
fi
grep -Fq 'exact BOLT input full-file hash differs' "${WORK}/wrong-input.err" || \
    fail 'wrong exact-input rejection lacked an exact reason'

# Registration rejects a symlink even when its target is a valid BOLT output.
ln -s -- "${WORK}/${FIRST_ID}.bolt" "${WORK}/prepared-output-symlink"
if "${REGISTER}" --test-mode --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --artifact-id "${FIRST_ID}" \
        --input "${CACHE}/inputs/${FINGERPRINT}/${FIRST_OBJECT}" \
        --output "${WORK}/prepared-output-symlink" \
        "${REGISTER_ARGUMENTS[@]}" \
        >"${WORK}/symlink-output.out" 2>"${WORK}/symlink-output.err"; then
    fail 'output registration accepted a symlink'
fi
grep -Fq 'symlink component' "${WORK}/symlink-output.err" || \
    fail 'output symlink rejection lacked an exact reason'

# Every captured ABI and hardening axis is independently enforced against the
# live pre-deploy object, rather than merely recorded for later reporting.
ABI_AXES=(
    elf_data elf_ident_version elf_header_version elf_osabi elf_abi_version elf_flags interpreter
    needed soname rpath runpath exported_dynamic_symbols symbol_version_names
    symbol_version_files symbol_version_mappings elf_role cet_properties
    gnu_stack_policy has_gnu_relro bind_now dynamic_flags has_textrel
    has_writable_executable_load tls_segments
)
for axis in "${ABI_AXES[@]}"; do
    cp -- "${WORK}/manifest.good" "${MANIFEST}"
    python3 - "${MANIFEST}" "${axis}" <<'PY'
import json
import sys
path, axis = sys.argv[1:]
document = json.load(open(path, encoding="utf-8"))
value = document["artifacts"][0][axis]
if isinstance(value, bool):
    value = not value
elif isinstance(value, list):
    value = [*value, {"name": "tampered"}] if axis == "exported_dynamic_symbols" else [*value, "tampered"]
elif value is None:
    value = "tampered"
else:
    value = f"{value}-tampered"
document["artifacts"][0][axis] = value
with open(path, "w", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
    if "${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
            --fingerprint "${FINGERPRINT}" \
            --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
            "${DEPLOY_QUALITY_ARGS[@]}" \
            >"${WORK}/abi-${axis}.out" 2>"${WORK}/abi-${axis}.err"; then
        fail "deployment accepted tampered captured ABI/security axis: ${axis}"
    fi
    grep -Eq "ABI/security identity mismatch for ${axis}|captured BOLT candidate paths/artifact facts differ|complete current ELF set/classification differs" \
        "${WORK}/abi-${axis}.err" || \
        fail "tampered ABI/security axis lacked an exact rejection: ${axis}"
done
cp -- "${WORK}/manifest.good" "${MANIFEST}"

# A full rescan covers non-ELF payload and complete topology before any
# mutation, not only the eligible paths that happen to have BOLT outputs.
printf '%s\n' tampered >>"${ED}/usr/share/fixture/data.txt"
sha256sum -- "${ED}/usr/bin/fixed" "${ED}/usr/bin/pie" \
    "${ED}/usr/bin/static-pie" \
    "${ED}/usr/lib64/libfixture.so.1" >"${WORK}/pre-full-rescan-reject-hashes"
if "${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
        --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        "${DEPLOY_QUALITY_ARGS[@]}" >"${WORK}/full-rescan.out" 2>"${WORK}/full-rescan.err"; then
    fail 'deployment ignored a changed non-ELF package payload'
fi
grep -Fq 'complete ED topology/file/ELF classification differs' "${WORK}/full-rescan.err" || \
    fail 'full ED rescan rejection lacked an exact reason'
sha256sum -- "${ED}/usr/bin/fixed" "${ED}/usr/bin/pie" \
    "${ED}/usr/bin/static-pie" \
    "${ED}/usr/lib64/libfixture.so.1" >"${WORK}/post-full-rescan-reject-hashes"
cmp -s "${WORK}/pre-full-rescan-reject-hashes" "${WORK}/post-full-rescan-reject-hashes" || \
    fail 'full-rescan rejection mutated eligible ELF bytes'
printf '%s\n' 'non-ELF package payload' >"${ED}/usr/share/fixture/data.txt"

OUTPUT_MANIFEST=${CACHE}/outputs/${FINGERPRINT}/manifest.json
cp -- "${OUTPUT_MANIFEST}" "${WORK}/output-manifest.good"
for axis in "${ABI_AXES[@]}"; do
    cp -- "${WORK}/output-manifest.good" "${OUTPUT_MANIFEST}"
    python3 - "${OUTPUT_MANIFEST}" "${axis}" <<'PY'
import json
import sys
path, axis = sys.argv[1:]
document = json.load(open(path, encoding="utf-8"))
value = document["outputs"][0]["abi_security_identity"][axis]
if isinstance(value, bool):
    value = not value
elif isinstance(value, list):
    value = [*value, {"name": "tampered"}] if axis == "exported_dynamic_symbols" else [*value, "tampered"]
elif value is None:
    value = "tampered"
else:
    value = f"{value}-tampered"
document["outputs"][0]["abi_security_identity"][axis] = value
with open(path, "w", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
    if "${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
            --fingerprint "${FINGERPRINT}" \
            --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
            "${DEPLOY_QUALITY_ARGS[@]}" \
            >"${WORK}/output-abi-${axis}.out" 2>"${WORK}/output-abi-${axis}.err"; then
        fail "deployment accepted tampered output ABI/security record: ${axis}"
    fi
    grep -Fq 'recorded ABI/security identity mismatch' "${WORK}/output-abi-${axis}.err" || \
        fail "tampered output ABI/security record lacked an exact rejection: ${axis}"
done
cp -- "${WORK}/output-manifest.good" "${OUTPUT_MANIFEST}"

# Tool, policy, option, profile, workload, fdata, command, and BOLT structural
# bindings are each independently fail-closed before any ED mutation.
PROVENANCE_CASES=(
    llvm_bolt_hash llvm_bolt_version policy options fdata workload profile
    command_record command_document bolt_note bolt_origin
)
for provenance_case in "${PROVENANCE_CASES[@]}"; do
    cp -- "${WORK}/output-manifest.good" "${OUTPUT_MANIFEST}"
    python3 - "${OUTPUT_MANIFEST}" "${provenance_case}" <<'PY'
import json
import sys
path, case = sys.argv[1:]
document = json.load(open(path, encoding="utf-8"))
record = document["outputs"][0]
if case == "llvm_bolt_hash": record["llvm_bolt"]["sha256"] = "0" * 64
elif case == "llvm_bolt_version": record["llvm_bolt"]["version"] += " tampered"
elif case == "policy": record["option_policy_revision"] += "-tampered"
elif case == "options": record["options"] = [*record["options"], "-tampered"]
elif case == "fdata": record["fdata"][0]["sha256"] = "0" * 64
elif case == "workload": record["workload_evidence"][0]["sha256"] = "0" * 64
elif case == "profile": record["profile_evidence"][0]["sha256"] = "0" * 64
elif case == "command_record": record["command_record"]["sha256"] = "0" * 64
elif case == "command_document": record["command"]["exit_status"] = 1
elif case == "bolt_note": record["bolt_info_sha256"] = "0" * 64
elif case == "bolt_origin": record["bolt_origin_sections"] = []
with open(path, "w", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
    if "${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
            --fingerprint "${FINGERPRINT}" \
            --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
            "${DEPLOY_QUALITY_ARGS[@]}" \
            >"${WORK}/provenance-${provenance_case}.out" \
            2>"${WORK}/provenance-${provenance_case}.err"; then
        fail "deployment accepted tampered BOLT provenance: ${provenance_case}"
    fi
    case ${provenance_case} in
        llvm_bolt_hash|llvm_bolt_version) expected_reason='llvm-bolt identity is stale or mismatched' ;;
        policy) expected_reason='unreviewed BOLT option-policy revision' ;;
        options) expected_reason='unreviewed BOLT option list' ;;
        fdata) expected_reason='BOLT fdata file identity mismatch' ;;
        workload) expected_reason='malformed BOLT workload proof' ;;
        profile) expected_reason='malformed BOLT profile proof' ;;
        command_record) expected_reason='command-record identity is stale or mismatched' ;;
        command_document) expected_reason='embedded command record differs' ;;
        bolt_note|bolt_origin) expected_reason='BOLT transformation identity mismatch' ;;
    esac
    grep -Fq "${expected_reason}" "${WORK}/provenance-${provenance_case}.err" || \
        fail "tampered provenance lacked its exact rejection: ${provenance_case}"
done
cp -- "${WORK}/output-manifest.good" "${OUTPUT_MANIFEST}"

if "${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
        --fingerprint "${FINGERPRINT}" --expected-eligible-count 2 \
        "${DEPLOY_QUALITY_ARGS[@]}" \
        >"${WORK}/deploy-count-mismatch.out" 2>"${WORK}/deploy-count-mismatch.err"; then
    fail 'deployment accepted an expected eligible count different from capture'
fi
grep -Eq 'differs from captured frozen count|inventory proof expected eligible count mismatch' \
    "${WORK}/deploy-count-mismatch.err" || \
    fail 'deployment count mismatch lacked an exact reason'

# A current-input GNU build-ID mismatch must fail before deployment.
python3 - "${MANIFEST}" <<'PY'
import json
import sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["artifacts"][0]["build_id"] = "00" * 20
with open(path, "w", encoding="utf-8") as stream:
    json.dump(data, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
if "${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
        --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        "${DEPLOY_QUALITY_ARGS[@]}" \
        >"${WORK}/build-id-mismatch.out" 2>"${WORK}/build-id-mismatch.err"; then
    fail 'deployment accepted a GNU build-ID mismatch'
fi
grep -Eq 'GNU build ID mismatch|complete current ELF set/classification differs' \
    "${WORK}/build-id-mismatch.err" || \
    fail 'build-ID mismatch rejection lacked an exact reason'
cp -- "${WORK}/manifest.good" "${MANIFEST}"

# An independently mismatching .text identity must also fail closed.
python3 - "${MANIFEST}" <<'PY'
import json
import sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["artifacts"][0]["text_sha256"] = "00" * 32
with open(path, "w", encoding="utf-8") as stream:
    json.dump(data, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
if "${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
        --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        "${DEPLOY_QUALITY_ARGS[@]}" \
        >"${WORK}/text-mismatch.out" 2>"${WORK}/text-mismatch.err"; then
    fail 'deployment accepted a .text hash mismatch'
fi
grep -Eq '[.]text hash mismatch|complete current ELF set/classification differs' \
    "${WORK}/text-mismatch.err" || \
    fail '.text mismatch rejection lacked an exact reason'
cp -- "${WORK}/manifest.good" "${MANIFEST}"

# Force a post-rename verifier failure through a deterministic readelf proxy.
# The deployer must roll every group back to exact bytes and topology.
REAL_READELF=$(command -v readelf)
READELF_PROXY=${WORK}/readelf-post-rename-failure
# The single-quoted lines intentionally become a separate proxy script.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'last=${!#}' \
    'if [[ ${1-} == -SW && ${last} == "${FAIL_PATH}" ]]; then' \
    '    "${REAL_READELF}" "$@" | sed "/[.]note[.]bolt_info/d"' \
    'else' \
    '    exec "${REAL_READELF}" "$@"' \
    'fi' \
    >"${READELF_PROXY}"
chmod 0755 -- "${READELF_PROXY}"
sha256sum -- "${ED}/usr/bin/fixed" "${ED}/usr/bin/pie" \
    "${ED}/usr/bin/static-pie" \
    "${ED}/usr/lib64/libfixture.so.1" >"${WORK}/pre-rollback-hashes"
if FAIL_PATH=${ED}/usr/bin/fixed REAL_READELF=${REAL_READELF} \
        "${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
        --fingerprint "${FINGERPRINT}" \
        --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
        --readelf "${READELF_PROXY}" \
        "${DEPLOY_QUALITY_ARGS[@]}" \
        >"${WORK}/post-rename-failure.out" 2>"${WORK}/post-rename-failure.err"; then
    fail 'forced post-rename verifier failure unexpectedly succeeded'
fi
grep -Fq 'deployed file lacks .note.bolt_info' "${WORK}/post-rename-failure.err" || \
    fail 'post-rename failure did not reach final verification'
sha256sum -- "${ED}/usr/bin/fixed" "${ED}/usr/bin/pie" \
    "${ED}/usr/bin/static-pie" \
    "${ED}/usr/lib64/libfixture.so.1" >"${WORK}/post-rollback-hashes"
cmp -s -- "${WORK}/pre-rollback-hashes" "${WORK}/post-rollback-hashes" || \
    fail 'post-rename failure did not restore exact input bytes'
[[ $(stat -c '%d:%i' "${ED}/usr/bin/fixed") == $(stat -c '%d:%i' "${ED}/usr/bin/fixed-hardlink") ]] || \
    fail 'post-rename rollback did not restore hardlink topology'
[[ $(stat -c '%a' "${ED}/usr/bin/fixed") == 4755 ]] || \
    fail 'post-rename rollback did not restore setuid mode'
if [[ ${XATTR_SUPPORTED} == true ]]; then
    [[ $(getfattr --only-values -n user.gentoo-bolt-test "${ED}/usr/bin/fixed" 2>/dev/null) == preserved ]] || \
        fail 'post-rename rollback did not restore user xattr metadata'
fi
if [[ ${CAPABILITY_SUPPORTED} == true ]]; then
    getcap "${ED}/usr/bin/fixed" | grep -Fq 'cap_net_bind_service=ep' || \
        fail 'post-rename rollback did not restore file capabilities'
fi
if readelf -SW "${ED}/usr/bin/fixed" | grep -Fq '.note.bolt_info'; then
    fail 'post-rename rollback left the replacement in ED'
fi

"${DEPLOY}" --test-mode --ed "${ED}" --cache-root "${CACHE}" \
    --fingerprint "${FINGERPRINT}" \
    --expected-eligible-count "${MAIN_EXPECTED_ELIGIBLE_COUNT}" \
    "${DEPLOY_QUALITY_ARGS[@]}" \
    >"${WORK}/deploy.out"

readelf -SW "${ED}/usr/bin/fixed" | grep -Fq '.note.bolt_info' || fail 'fixed executable lacks BOLT note'
readelf -SW "${ED}/usr/bin/pie" | grep -Fq '.note.bolt_info' || fail 'PIE lacks BOLT note'
readelf -SW "${ED}/usr/bin/static-pie" | grep -Fq '.note.bolt_info' || fail 'static PIE lacks BOLT note'
readelf -SW "${ED}/usr/lib64/libfixture.so.1" | grep -Fq '.note.bolt_info' || fail 'DSO lacks BOLT note'
[[ $("${ED}/usr/bin/fixed") == 42 ]] || fail 'deployed executable failed runtime smoke test'
[[ $("${ED}/usr/bin/static-pie") == 42 ]] || fail 'deployed static PIE failed runtime smoke test'
[[ $(stat -c '%d:%i' "${ED}/usr/bin/fixed") == $(stat -c '%d:%i' "${ED}/usr/bin/fixed-hardlink") ]] || \
    fail 'deployment did not preserve the hardlink group'
[[ $(readlink -- "${ED}/usr/bin/fixed-symlink") == fixed-hardlink ]] || \
    fail 'deployment changed symlink topology'
[[ $(stat -c '%a' "${ED}/usr/bin/fixed") == 4755 ]] || fail 'deployment lost setuid mode'
[[ -s ${CACHE}/diagnostics/${FINGERPRINT}/pre-deploy/${FIRST_ID}.elf ]] || \
    fail 'deployment did not preserve its diagnostic input'
[[ $(stat -c '%a' "${CACHE}/diagnostics/${FINGERPRINT}/pre-deploy/${FIRST_ID}.elf") == 600 ]] || \
    fail 'diagnostic input is not private'
if [[ ${XATTR_SUPPORTED} == true ]]; then
    [[ $(getfattr --only-values -n user.gentoo-bolt-test "${ED}/usr/bin/fixed" 2>/dev/null) == preserved ]] || \
        fail 'deployment lost user xattr metadata'
    record_required_subtest PASS metadata.user-xattr \
        'deployment and rollback preserved the user xattr on the hardlink inode group'
fi
if [[ ${CAPABILITY_SUPPORTED} == true ]]; then
    getcap "${ED}/usr/bin/fixed" | grep -Fq 'cap_net_bind_service=ep' || \
        fail 'deployment lost file capabilities'
    record_required_subtest PASS metadata.file-capability \
        'deployment and rollback preserved the file capability on the hardlink inode group'
fi

# A package containing no ELF is a successful empty capture, while deployment
# of that identity is explicitly rejected.
NO_ELF_ED=${WORK}/no-elf-ed
NO_ELF_CACHE=${WORK}/no-elf-cache
NO_ELF_FINGERPRINT=$(printf 'no-elf' | sha256sum | awk '{print $1}')
mkdir -p -- "${NO_ELF_ED}/usr/share/fixture" "${NO_ELF_CACHE}"
printf 'data only\n' >"${NO_ELF_ED}/usr/share/fixture/data.txt"
ZERO_PROOF=${WORK}/zero-proof.json
make_inventory_proof "${ZERO_PROOF}" "${NO_ELF_FINGERPRINT}" 0 '[]'
"${CAPTURE}" --test-mode --ed "${NO_ELF_ED}" --cache-root "${NO_ELF_CACHE}" \
    --fingerprint "${NO_ELF_FINGERPRINT}" --expected-eligible-count 0 \
    --inventory-proof "${ZERO_PROOF}" >/dev/null
python3 - "${NO_ELF_CACHE}/inputs/${NO_ELF_FINGERPRINT}/manifest.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["elf_total"] == 0
assert manifest["eligible_total"] == 0
assert manifest["artifacts"] == []
assert manifest["expected_eligible_count"] == 0
assert manifest["inventory_proof"]["document"]["expected_eligible_count"] == 0
PY

if "${CAPTURE}" --test-mode --ed "${NO_ELF_ED}" --cache-root "${WORK}/missing-zero-proof-cache" \
        --fingerprint "$(printf missing-zero-proof | sha256sum | awk '{print $1}')" \
        --expected-eligible-count 0 >"${WORK}/missing-zero-proof.out" 2>"${WORK}/missing-zero-proof.err"; then
    fail 'zero expected eligible count was accepted without frozen-inventory proof'
fi
grep -Fq 'requires --inventory-proof' "${WORK}/missing-zero-proof.err" || \
    fail 'missing zero-count proof rejection lacked an exact reason'
SELF_STRIPPED_FINGERPRINT=$(printf self-stripped | sha256sum | awk '{print $1}')
SELF_STRIPPED_PROOF=${WORK}/self-stripped-proof.json
make_inventory_proof "${SELF_STRIPPED_PROOF}" "${SELF_STRIPPED_FINGERPRINT}" 1 \
    "$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])[:1]))' "${MAIN_CANDIDATES}")"
if "${CAPTURE}" --test-mode --ed "${NO_ELF_ED}" --cache-root "${WORK}/self-stripped-cache" \
        --fingerprint "${SELF_STRIPPED_FINGERPRINT}" \
        --expected-eligible-count 1 --inventory-proof "${SELF_STRIPPED_PROOF}" \
        >"${WORK}/self-stripped.out" 2>"${WORK}/self-stripped.err"; then
    fail 'capture accepted self-stripped zero output when inventory expected one candidate'
fi
grep -Fq 'expected=1, actual=0' "${WORK}/self-stripped.err" || \
    fail 'self-stripped zero rejection lacked exact expected/actual evidence'

# Even hermetic cache roots must be owned by the caller and not writable by
# group or world.
INSECURE_CACHE=${WORK}/insecure-cache
INSECURE_FINGERPRINT=$(printf 'insecure-cache' | sha256sum | awk '{print $1}')
mkdir -p -- "${INSECURE_CACHE}"
chmod 0770 -- "${INSECURE_CACHE}"
if "${CAPTURE}" --test-mode --ed "${NO_ELF_ED}" --cache-root "${INSECURE_CACHE}" \
        --fingerprint "${INSECURE_FINGERPRINT}" \
        --expected-eligible-count 0 \
        >"${WORK}/insecure-cache.out" 2>"${WORK}/insecure-cache.err"; then
    fail 'group-writable cache root was accepted'
fi
grep -Fq 'cache root is group/world-writable' "${WORK}/insecure-cache.err" || \
    fail 'insecure cache rejection lacked an exact reason'

# Root arguments containing symlink components are refused instead of being
# silently resolved to a different tree.
ln -s -- "${NO_ELF_ED}" "${WORK}/no-elf-ed-symlink"
SYMLINK_ROOT_FINGERPRINT=$(printf 'symlink-root' | sha256sum | awk '{print $1}')
if "${CAPTURE}" --test-mode --ed "${WORK}/no-elf-ed-symlink" --cache-root "${WORK}/symlink-root-cache" \
        --fingerprint "${SYMLINK_ROOT_FINGERPRINT}" \
        --expected-eligible-count 0 \
        >"${WORK}/symlink-root.out" 2>"${WORK}/symlink-root.err"; then
    fail 'capture accepted an ED root containing a symlink component'
fi
grep -Fq 'symlink component' "${WORK}/symlink-root.err" || \
    fail 'symlink-root rejection lacked an exact reason'

# Function-section links can produce .rela.text.<function>. Accept relocation
# sections that target executable sections through sh_info, not just the
# literal .rela.text spelling.
FUNCTION_ED=${WORK}/function-sections-ed
FUNCTION_CACHE=${WORK}/function-sections-cache
FUNCTION_FINGERPRINT=$(printf 'function-sections' | sha256sum | awk '{print $1}')
mkdir -p -- "${FUNCTION_ED}/usr/bin" "${FUNCTION_CACHE}"
cc -O0 -g -ffunction-sections -fno-pie -no-pie \
    '-Wl,--build-id=sha1,--emit-relocs,--unique=.text.*' \
    "${SOURCE}/main.c" -o "${FUNCTION_ED}/usr/bin/function-sections"
readelf -SW "${FUNCTION_ED}/usr/bin/function-sections" | \
    grep -Fq '.rela.text.main' || fail 'toolchain did not emit the function relocation fixture'
FUNCTION_CANDIDATES=$(python3 - <<'PY'
import hashlib,json
p="usr/bin/function-sections"
print(json.dumps([{"artifact_id":hashlib.sha256(p.encode()).hexdigest(),"canonical_path":p,"paths":[p],"hardlink_count":1,"elf_class":"ELF64","elf_data":"2's complement, little endian","elf_type":"EXEC","machine":"Advanced Micro Devices X86-64","elf_role":"fixed-executable"}]))
PY
)
FUNCTION_PROOF=${WORK}/function-inventory-proof.json
make_inventory_proof "${FUNCTION_PROOF}" "${FUNCTION_FINGERPRINT}" 1 "${FUNCTION_CANDIDATES}"
"${CAPTURE}" --test-mode --ed "${FUNCTION_ED}" --cache-root "${FUNCTION_CACHE}" \
    --fingerprint "${FUNCTION_FINGERPRINT}" --expected-eligible-count 1 \
    --inventory-proof "${FUNCTION_PROOF}" >/dev/null
python3 - "${FUNCTION_CACHE}/inputs/${FUNCTION_FINGERPRINT}/manifest.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["eligible_total"] == 1
artifact = manifest["artifacts"][0]
assert artifact["eligible"] is True
assert ".rela.text.main" in artifact["executable_relocation_sections"]
assert "no-text-relocations" not in artifact["readiness_failures"]
PY

# Every external ELF tool has a bounded process-group deadline. These fake
# tools and descendants ignore TERM so the KILL path, locale pinning, and
# partial cleanup are all exercised directly.
HUNG_TOOL=${WORK}/hung-elf-tool
# The single-quoted lines intentionally become a separate fake tool.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'trap "" TERM' \
    'printf "%s %s\\n" "${LC_ALL-}" "${LANG-}" >"${HUNG_LOCALE_FILE}"' \
    'bash -c '\''trap "" TERM; while :; do sleep 1; done'\'' &' \
    'child=$!' \
    'printf "%s %s\\n" "$$" "${child}" >"${HUNG_PID_FILE}"' \
    'wait "${child}"' \
    >"${HUNG_TOOL}"
chmod 0755 -- "${HUNG_TOOL}"

assert_hung_group_gone() {
    local pid_file=$1 pid
    [[ -s ${pid_file} ]] || fail "hung tool did not record its process group: ${pid_file}"
    for pid in $(<"${pid_file}"); do
        for _ in {1..100}; do
            [[ ! -e /proc/${pid} ]] && break
            sleep 0.01
        done
        [[ ! -e /proc/${pid} ]] || fail "hung tool process survived TERM/KILL: ${pid}"
    done
}

HUNG_ED=${WORK}/hung-ed
mkdir -p -- "${HUNG_ED}/usr/bin"
cp -- "${CACHE}/inputs/${FINGERPRINT}/${FIRST_OBJECT}" "${HUNG_ED}/usr/bin/hung"
for tool_kind in readelf objcopy; do
    HUNG_CACHE=${WORK}/hung-${tool_kind}-cache
    HUNG_FINGERPRINT=$(printf 'hung-%s' "${tool_kind}" | sha256sum | awk '{print $1}')
    HUNG_PID_FILE=${WORK}/hung-${tool_kind}.pids
    HUNG_LOCALE_FILE=${WORK}/hung-${tool_kind}.locale
    HUNG_CANDIDATES=$(python3 - <<'PY'
import hashlib,json
p="usr/bin/hung"
print(json.dumps([{"artifact_id":hashlib.sha256(p.encode()).hexdigest(),"canonical_path":p,"paths":[p],"hardlink_count":1,"elf_class":"ELF64","elf_data":"2's complement, little endian","elf_type":"EXEC","machine":"Advanced Micro Devices X86-64","elf_role":"fixed-executable"}]))
PY
)
    HUNG_PROOF=${WORK}/hung-${tool_kind}-proof.json
    make_inventory_proof "${HUNG_PROOF}" "${HUNG_FINGERPRINT}" 1 "${HUNG_CANDIDATES}"
    mkdir -p -- "${HUNG_CACHE}"
    tool_arguments=(--readelf readelf --objcopy objcopy)
    if [[ ${tool_kind} == readelf ]]; then
        tool_arguments=(--readelf "${HUNG_TOOL}" --objcopy objcopy)
    else
        tool_arguments=(--readelf readelf --objcopy "${HUNG_TOOL}")
    fi
    if HUNG_PID_FILE=${HUNG_PID_FILE} HUNG_LOCALE_FILE=${HUNG_LOCALE_FILE} \
            "${CAPTURE}" --test-mode --tool-timeout-seconds 0.2 \
            --tool-kill-after-seconds 0.2 --ed "${HUNG_ED}" \
            --cache-root "${HUNG_CACHE}" --fingerprint "${HUNG_FINGERPRINT}" \
            --expected-eligible-count 1 --inventory-proof "${HUNG_PROOF}" \
            "${tool_arguments[@]}" \
            >"${WORK}/hung-${tool_kind}.out" 2>"${WORK}/hung-${tool_kind}.err"; then
        fail "hung ${tool_kind} command unexpectedly succeeded"
    fi
    grep -Fq 'tool timed out after 0.2s' "${WORK}/hung-${tool_kind}.err" || \
        fail "hung ${tool_kind} rejection lacked a bounded timeout reason"
    [[ $(<"${HUNG_LOCALE_FILE}") == 'C C' ]] || \
        fail "${tool_kind} did not run with LC_ALL/LANG=C"
    assert_hung_group_gone "${HUNG_PID_FILE}"
    [[ ! -e ${HUNG_CACHE}/inputs/${HUNG_FINGERPRINT} ]] || \
        fail "hung ${tool_kind} published a final capture"
    if find "${HUNG_CACHE}" -name ".${HUNG_FINGERPRINT}.partial.*" -print -quit | grep -q .; then
        fail "hung ${tool_kind} left a reusable partial artifact"
    fi
done

# A mixed ELF64/ELF32 package records both and excludes the 32-bit input with
# a reason. If the host compiler lacks multilib, emit a reason-bearing skip.
MIXED_ED=${WORK}/mixed-ed
MIXED_CACHE=${WORK}/mixed-cache
MIXED_FINGERPRINT=$(printf 'mixed-abi' | sha256sum | awk '{print $1}')
mkdir -p -- "${MIXED_ED}/usr/bin" "${MIXED_CACHE}"
cp -- "${CACHE}/inputs/${FINGERPRINT}/$(python3 - "${MANIFEST}" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"]:
    if item["elf_class"] == "ELF64":
        print(item["cache_object"])
        break
PY
)" "${MIXED_ED}/usr/bin/elf64"
# The assembler register names are literal C source, not shell variables.
# shellcheck disable=SC2016
printf '%s\n' 'void _start(void) { __asm__ volatile("mov $1, %eax; xor %ebx, %ebx; int $0x80"); }' \
    >"${SOURCE}/elf32.c"
if cc -m32 -nostdlib -fno-pie -no-pie -Wl,-e,_start,--build-id=sha1,--emit-relocs \
        "${SOURCE}/elf32.c" -o "${MIXED_ED}/usr/bin/elf32" 2>"${WORK}/elf32-build.err"; then
    MIXED_CANDIDATES=$(python3 - <<'PY'
import hashlib,json
p="usr/bin/elf64"
print(json.dumps([{"artifact_id":hashlib.sha256(p.encode()).hexdigest(),"canonical_path":p,"paths":[p],"hardlink_count":1,"elf_class":"ELF64","elf_data":"2's complement, little endian","elf_type":"EXEC","machine":"Advanced Micro Devices X86-64","elf_role":"fixed-executable"}]))
PY
)
    MIXED_PROOF=${WORK}/mixed-inventory-proof.json
    make_inventory_proof "${MIXED_PROOF}" "${MIXED_FINGERPRINT}" 1 "${MIXED_CANDIDATES}"
    "${CAPTURE}" --test-mode --ed "${MIXED_ED}" --cache-root "${MIXED_CACHE}" \
        --fingerprint "${MIXED_FINGERPRINT}" --expected-eligible-count 1 \
        --inventory-proof "${MIXED_PROOF}" >/dev/null
    python3 - "${MIXED_CACHE}/inputs/${MIXED_FINGERPRINT}/manifest.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["elf_total"] == 2
classes = {item["elf_class"]: item for item in manifest["artifacts"]}
assert classes["ELF64"]["eligible"] is True
assert classes["ELF32"]["eligible"] is False
assert "unsupported-elf-class" in classes["ELF32"]["readiness_failures"]
assert "terminal_reasons" not in classes["ELF32"]
PY
    record_required_subtest PASS abi.elf32-mixed \
        'mixed ELF64 and ELF32 capture classified the 32-bit artifact as ineligible'
else
    record_required_subtest SKIP abi.elf32-mixed \
        'compiler cannot link the hermetic ELF32 mixed-ABI fixture'
    printf 'INFO: ELF32 toolchain unavailable; recorded required subtest SKIP\n'
fi

printf 'PASS: BOLT capture/deployment classification, identity, topology, and metadata fixture\n'
