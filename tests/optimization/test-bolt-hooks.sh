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

for command in python3 readelf objcopy cc; do
    command -v "${command}" >/dev/null 2>&1 || fail "missing required command: ${command}"
done

ED=${WORK}/ed
CACHE=${WORK}/cache
SOURCE=${WORK}/source
mkdir -p -- "${ED}/usr/bin" "${ED}/usr/lib64" "${CACHE}" "${SOURCE}"
FINGERPRINT=$(printf 'capture-deploy-fixture' | sha256sum | awk '{print $1}')

printf '%s\n' \
    '#include <stdio.h>' \
    'int helper(int value) { return value + 7; }' \
    'int main(void) { printf("%d\n", helper(35)); return 0; }' \
    >"${SOURCE}/main.c"
printf '%s\n' \
    'int exported_fixture(int value) { return value * 3; }' \
    >"${SOURCE}/library.c"

cc -O2 -g -fno-omit-frame-pointer -no-pie \
    -Wl,--build-id=sha1,--emit-relocs \
    "${SOURCE}/main.c" -o "${ED}/usr/bin/fixed"
cc -O2 -g -fno-omit-frame-pointer -fPIE -pie \
    -Wl,--build-id=sha1,--emit-relocs \
    "${SOURCE}/main.c" -o "${ED}/usr/bin/pie"
cc -O2 -g -fno-omit-frame-pointer -fPIC -shared \
    -Wl,--build-id=sha1,--emit-relocs,-soname,libfixture.so.1 \
    "${SOURCE}/library.c" -o "${ED}/usr/lib64/libfixture.so.1"
ln -- "${ED}/usr/bin/fixed" "${ED}/usr/bin/fixed-hardlink"
ln -s -- fixed-hardlink "${ED}/usr/bin/fixed-symlink"
chmod 4755 -- "${ED}/usr/bin/fixed"

XATTR_SUPPORTED=false
if setfattr -n user.gentoo-bolt-test -v preserved -- "${ED}/usr/bin/fixed" 2>/dev/null; then
    XATTR_SUPPORTED=true
else
    printf 'SKIP: user xattrs unavailable on fixture filesystem\n'
fi

CAPABILITY_SUPPORTED=false
if command -v setcap >/dev/null 2>&1 && command -v getcap >/dev/null 2>&1 && \
        setcap cap_net_bind_service=ep "${ED}/usr/bin/fixed" 2>/dev/null; then
    CAPABILITY_SUPPORTED=true
else
    printf 'SKIP: file-capability setup unavailable to the current user/filesystem\n'
fi

BEFORE_TREE=${WORK}/before-tree
find "${ED}" -xdev -printf '%P\t%y\t%m\t%U\t%G\t%s\t%i\t%l\n' | sort >"${BEFORE_TREE}"
"${CAPTURE}" --ed "${ED}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
    >"${WORK}/capture.out"
MANIFEST=${CACHE}/inputs/${FINGERPRINT}/manifest.json
[[ -s ${MANIFEST} ]] || fail 'capture manifest was not published'
find "${ED}" -xdev -printf '%P\t%y\t%m\t%U\t%G\t%s\t%i\t%l\n' | sort >"${WORK}/after-tree"
cmp -s -- "${BEFORE_TREE}" "${WORK}/after-tree" || fail 'capture mutated ED metadata or topology'

python3 - "${MANIFEST}" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["schema"] == "gentoo-optimization-bolt-capture-v1"
assert manifest["elf_total"] == 3
assert manifest["eligible_total"] == 3
assert manifest["ineligible_total"] == 0
assert len(manifest["artifacts"]) == 3
assert [item["elf_class"] for item in manifest["artifacts"]] == ["ELF64"] * 3
assert {item["elf_type"] for item in manifest["artifacts"]} == {"EXEC", "DYN"}
assert all(item["machine"] == "Advanced Micro Devices X86-64" for item in manifest["artifacts"])
assert all(item["has_symtab"] and item["symbol_count"] for item in manifest["artifacts"])
assert all(item["text_relocation_sections"] for item in manifest["artifacts"])
assert all(item["build_id"] and item["text_sha256"] for item in manifest["artifacts"])
hardlinks = [item for item in manifest["artifacts"] if item["hardlink_count"] == 2]
assert len(hardlinks) == 1
assert hardlinks[0]["paths"] == ["usr/bin/fixed", "usr/bin/fixed-hardlink"]
assert manifest["symlinks"] == [
    {"gid": manifest["symlinks"][0]["gid"], "path": "usr/bin/fixed-symlink", "target": "fixed-hardlink", "uid": manifest["symlinks"][0]["uid"]}
]
assert hardlinks[0]["metadata"]["mode"] == "4755"
PY

# Create syntactically valid stand-ins for prepared BOLT outputs. The added
# note exercises the deployment invariant without requiring llvm-bolt in this
# hermetic transaction test.
printf 'fixture-bolt-note\n' >"${WORK}/bolt-note"
while IFS=$'\t' read -r artifact_id object_file; do
    prepared=${WORK}/${artifact_id}.bolt
    cp -- "${CACHE}/inputs/${FINGERPRINT}/${object_file}" "${prepared}"
    objcopy --add-section ".note.bolt_info=${WORK}/bolt-note" \
        --set-section-flags .note.bolt_info=alloc,readonly \
        "${prepared}"
    "${REGISTER}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --artifact-id "${artifact_id}" --output "${prepared}" \
        >"${WORK}/register-${artifact_id}.out"
done < <(python3 - "${MANIFEST}" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"]:
    if item["eligible"]:
        print(item["artifact_id"], item["cache_object"], sep="\t")
PY
)

cp -- "${MANIFEST}" "${WORK}/manifest.good"
FIRST_ID=$(python3 - "${MANIFEST}" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"][0]["artifact_id"])
PY
)

# Registration rejects a symlink even when its target is a valid BOLT output.
ln -s -- "${WORK}/${FIRST_ID}.bolt" "${WORK}/prepared-output-symlink"
if "${REGISTER}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        --artifact-id "${FIRST_ID}" --output "${WORK}/prepared-output-symlink" \
        >"${WORK}/symlink-output.out" 2>"${WORK}/symlink-output.err"; then
    fail 'output registration accepted a symlink'
fi
grep -Fq 'symlink component' "${WORK}/symlink-output.err" || \
    fail 'output symlink rejection lacked an exact reason'

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
if "${DEPLOY}" --ed "${ED}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        >"${WORK}/build-id-mismatch.out" 2>"${WORK}/build-id-mismatch.err"; then
    fail 'deployment accepted a GNU build-ID mismatch'
fi
grep -Fq 'GNU build ID mismatch' "${WORK}/build-id-mismatch.err" || \
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
if "${DEPLOY}" --ed "${ED}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
        >"${WORK}/text-mismatch.out" 2>"${WORK}/text-mismatch.err"; then
    fail 'deployment accepted a .text hash mismatch'
fi
grep -Fq '.text hash mismatch' "${WORK}/text-mismatch.err" || \
    fail '.text mismatch rejection lacked an exact reason'
cp -- "${WORK}/manifest.good" "${MANIFEST}"

# Force a post-rename verifier failure through a deterministic readelf proxy.
# The deployer must roll every group back to exact bytes and topology.
REAL_READELF=$(command -v readelf)
READELF_COUNT=${WORK}/readelf-count
READELF_PROXY=${WORK}/readelf-post-rename-failure
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'count=0' \
    '[[ ! -s ${READELF_COUNT} ]] || count=$(<"${READELF_COUNT}")' \
    '((count += 1))' \
    'printf "%s\\n" "${count}" >"${READELF_COUNT}"' \
    'if (( count >= 26 )) && [[ ${1-} == -SW ]]; then' \
    '    "${REAL_READELF}" "$@" | sed "/[.]note[.]bolt_info/d"' \
    'else' \
    '    exec "${REAL_READELF}" "$@"' \
    'fi' \
    >"${READELF_PROXY}"
chmod 0755 -- "${READELF_PROXY}"
sha256sum -- "${ED}/usr/bin/fixed" "${ED}/usr/bin/pie" \
    "${ED}/usr/lib64/libfixture.so.1" >"${WORK}/pre-rollback-hashes"
if READELF_COUNT=${READELF_COUNT} REAL_READELF=${REAL_READELF} \
        "${DEPLOY}" --ed "${ED}" --cache-root "${CACHE}" \
        --fingerprint "${FINGERPRINT}" --readelf "${READELF_PROXY}" \
        >"${WORK}/post-rename-failure.out" 2>"${WORK}/post-rename-failure.err"; then
    fail 'forced post-rename verifier failure unexpectedly succeeded'
fi
grep -Fq 'deployed file lacks .note.bolt_info' "${WORK}/post-rename-failure.err" || \
    fail 'post-rename failure did not reach final verification'
sha256sum -- "${ED}/usr/bin/fixed" "${ED}/usr/bin/pie" \
    "${ED}/usr/lib64/libfixture.so.1" >"${WORK}/post-rollback-hashes"
cmp -s -- "${WORK}/pre-rollback-hashes" "${WORK}/post-rollback-hashes" || \
    fail 'post-rename failure did not restore exact input bytes'
[[ $(stat -c '%d:%i' "${ED}/usr/bin/fixed") == $(stat -c '%d:%i' "${ED}/usr/bin/fixed-hardlink") ]] || \
    fail 'post-rename rollback did not restore hardlink topology'
if readelf -SW "${ED}/usr/bin/fixed" | grep -Fq '.note.bolt_info'; then
    fail 'post-rename rollback left the replacement in ED'
fi

"${DEPLOY}" --ed "${ED}" --cache-root "${CACHE}" --fingerprint "${FINGERPRINT}" \
    >"${WORK}/deploy.out"

readelf -SW "${ED}/usr/bin/fixed" | grep -Fq '.note.bolt_info' || fail 'fixed executable lacks BOLT note'
readelf -SW "${ED}/usr/bin/pie" | grep -Fq '.note.bolt_info' || fail 'PIE lacks BOLT note'
readelf -SW "${ED}/usr/lib64/libfixture.so.1" | grep -Fq '.note.bolt_info' || fail 'DSO lacks BOLT note'
[[ $("${ED}/usr/bin/fixed") == 42 ]] || fail 'deployed executable failed runtime smoke test'
[[ $(stat -c '%d:%i' "${ED}/usr/bin/fixed") == $(stat -c '%d:%i' "${ED}/usr/bin/fixed-hardlink") ]] || \
    fail 'deployment did not preserve the hardlink group'
[[ $(readlink -- "${ED}/usr/bin/fixed-symlink") == fixed-hardlink ]] || \
    fail 'deployment changed symlink topology'
[[ $(stat -c '%a' "${ED}/usr/bin/fixed") == 4755 ]] || fail 'deployment lost setuid mode'
[[ -s ${CACHE}/diagnostics/${FINGERPRINT}/pre-deploy/${FIRST_ID}.elf ]] || \
    fail 'deployment did not preserve its diagnostic input'
if [[ ${XATTR_SUPPORTED} == true ]]; then
    [[ $(getfattr --only-values -n user.gentoo-bolt-test "${ED}/usr/bin/fixed" 2>/dev/null) == preserved ]] || \
        fail 'deployment lost user xattr metadata'
fi
if [[ ${CAPABILITY_SUPPORTED} == true ]]; then
    getcap "${ED}/usr/bin/fixed" | grep -Fq 'cap_net_bind_service=ep' || \
        fail 'deployment lost file capabilities'
fi

# A package containing no ELF is a successful empty capture, while deployment
# of that identity is explicitly rejected.
NO_ELF_ED=${WORK}/no-elf-ed
NO_ELF_CACHE=${WORK}/no-elf-cache
NO_ELF_FINGERPRINT=$(printf 'no-elf' | sha256sum | awk '{print $1}')
mkdir -p -- "${NO_ELF_ED}/usr/share/fixture" "${NO_ELF_CACHE}"
printf 'data only\n' >"${NO_ELF_ED}/usr/share/fixture/data.txt"
"${CAPTURE}" --ed "${NO_ELF_ED}" --cache-root "${NO_ELF_CACHE}" \
    --fingerprint "${NO_ELF_FINGERPRINT}" >/dev/null
python3 - "${NO_ELF_CACHE}/inputs/${NO_ELF_FINGERPRINT}/manifest.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["elf_total"] == 0
assert manifest["eligible_total"] == 0
assert manifest["artifacts"] == []
PY

# Root arguments containing symlink components are refused instead of being
# silently resolved to a different tree.
ln -s -- "${NO_ELF_ED}" "${WORK}/no-elf-ed-symlink"
SYMLINK_ROOT_FINGERPRINT=$(printf 'symlink-root' | sha256sum | awk '{print $1}')
if "${CAPTURE}" --ed "${WORK}/no-elf-ed-symlink" --cache-root "${WORK}/symlink-root-cache" \
        --fingerprint "${SYMLINK_ROOT_FINGERPRINT}" \
        >"${WORK}/symlink-root.out" 2>"${WORK}/symlink-root.err"; then
    fail 'capture accepted an ED root containing a symlink component'
fi
grep -Fq 'symlink component' "${WORK}/symlink-root.err" || \
    fail 'symlink-root rejection lacked an exact reason'

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
printf '%s\n' 'void _start(void) { __asm__ volatile("mov $1, %eax; xor %ebx, %ebx; int $0x80"); }' \
    >"${SOURCE}/elf32.c"
if cc -m32 -nostdlib -fno-pie -no-pie -Wl,-e,_start,--build-id=sha1,--emit-relocs \
        "${SOURCE}/elf32.c" -o "${MIXED_ED}/usr/bin/elf32" 2>"${WORK}/elf32-build.err"; then
    "${CAPTURE}" --ed "${MIXED_ED}" --cache-root "${MIXED_CACHE}" \
        --fingerprint "${MIXED_FINGERPRINT}" >/dev/null
    python3 - "${MIXED_CACHE}/inputs/${MIXED_FINGERPRINT}/manifest.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["elf_total"] == 2
classes = {item["elf_class"]: item for item in manifest["artifacts"]}
assert classes["ELF64"]["eligible"] is True
assert classes["ELF32"]["eligible"] is False
assert "unsupported-elf-class" in classes["ELF32"]["terminal_reasons"]
PY
else
    printf 'SKIP: compiler cannot link the hermetic ELF32 mixed-ABI fixture\n'
fi

printf 'PASS: BOLT capture/deployment classification, identity, topology, and metadata fixture\n'
