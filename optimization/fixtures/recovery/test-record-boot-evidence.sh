#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../../.." && pwd -P)
HOOK=${REPOSITORY_ROOT}/scripts/optimization/recovery/record-boot-evidence.sh
FIXTURE=$(mktemp -d)
trap 'rm -rf -- "${FIXTURE}"' EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_contains() {
    local needle=$1 file=$2
    grep -Fq -- "${needle}" "${file}" || fail "expected '${needle}' in ${file}"
}

assert_count() {
    local expected=$1 directory=$2 pattern=$3 actual
    actual=$(find "${directory}" -maxdepth 1 -type f -name "${pattern}" | wc -l)
    [[ ${actual} -eq ${expected} ]] || fail "expected ${expected} ${pattern} files, got ${actual}"
}

ROOT=${FIXTURE}/root
STATE=${ROOT}/var/lib/gentoo-optimization
TOOLS=${ROOT}/fixture-tools
ESP=${ROOT}/efi
MARKERS=${STATE}/recovery
EVIDENCE=${STATE}/reports/recovery/boot-evidence
KERNEL=${ESP}/EFI/Gentoo/vmlinuz-fixture.efi
INITRAMFS=${ESP}/EFI/Gentoo/initramfs-fixture.img

mkdir -p -- \
    "${TOOLS}" "${MARKERS}" "${ESP}/EFI/Gentoo" \
    "${ROOT}/proc/sys/kernel/random" "${ROOT}/proc/self" \
    "${ROOT}/etc" "${ROOT}/sys/class/net/eth0"
printf 'fixture kernel\n' >"${KERNEL}"
printf 'fixture initramfs\n' >"${INITRAMFS}"
printf '11111111-2222-3333-4444-555555555555\n' >"${ROOT}/proc/sys/kernel/random/boot_id"
printf 'root=/dev/mapper/gentoo-root quiet test-fixture=1\n' >"${ROOT}/proc/cmdline"
printf '31 22 0:28 / / rw,relatime - xfs /dev/mapper/gentoo-root rw\n' >"${ROOT}/proc/self/mountinfo"
printf '/dev/mapper/gentoo-root / xfs rw,relatime 0 0\n/dev/nvme0n1p1 /efi vfat rw 0 0\n' >"${ROOT}/proc/mounts"
printf 'nameserver 192.0.2.53\n' >"${ROOT}/etc/resolv.conf"
kernel_sha=$(sha256sum -- "${KERNEL}")
kernel_sha=${kernel_sha%% *}
initramfs_sha=$(sha256sum -- "${INITRAMFS}")
initramfs_sha=${initramfs_sha%% *}

cat >"${TOOLS}/uname" <<'EOF'
#!/usr/bin/env bash
if [[ ${1-} == -r ]]; then
    printf '7.2.0-fixture\n'
else
    printf 'Linux fixture 7.2.0-fixture #1 SMP PREEMPT_DYNAMIC x86_64 GNU/Linux\n'
fi
EOF

cat >"${TOOLS}/efibootmgr" <<'EOF'
#!/usr/bin/env bash
printf 'BootCurrent: 0004\n'
printf 'BootOrder: 0004,0200\n'
printf 'Boot0004* Gentoo validation HD(1,GPT,fixture)/\\EFI\\Gentoo\\vmlinuz-fixture.efi\n'
EOF

cat >"${TOOLS}/findmnt" <<EOF
#!/usr/bin/env bash
if [[ " \$* " == *' --noheadings '* ]]; then
    printf '/dev/mapper/gentoo-root\\n'
elif [[ " \$* " == *' ${ESP} '* ]]; then
    printf 'SOURCE TARGET FSTYPE OPTIONS\\n/dev/nvme0n1p1 ${ESP} vfat rw\\n'
else
    printf 'SOURCE TARGET FSTYPE OPTIONS\\n/dev/mapper/gentoo-root ${ROOT} xfs rw,relatime\\n'
fi
EOF

cat >"${TOOLS}/rc-status" <<'EOF'
#!/usr/bin/env bash
if [[ ${1-} == --crashed ]]; then
    [[ -z ${FIXTURE_CRASHED_SERVICE:-} ]] || printf '%s\n' "${FIXTURE_CRASHED_SERVICE}"
else
    printf 'Runlevel: default\n networking [ started ]\n local [ started ]\n'
fi
EOF

cat >"${TOOLS}/emerge" <<'EOF'
#!/usr/bin/env bash
printf 'Portage 3.0 fixture\n'
printf 'ROOT=%s\n' "${ROOT-}"
printf 'PORTAGE_CONFIGROOT=%s\n' "${PORTAGE_CONFIGROOT-}"
EOF

cat >"${TOOLS}/portageq" <<'EOF'
#!/usr/bin/env bash
printf '/\n'
EOF

cat >"${TOOLS}/python3" <<'EOF'
#!/usr/bin/env bash
printf '3.14.0\n/usr/lib/python3.14/site-packages/portage/__init__.py\n'
EOF

cat >"${TOOLS}/sh" <<'EOF'
#!/usr/bin/env bash
printf 'shell-smoke-ok\n'
EOF

cat >"${TOOLS}/ip" <<'EOF'
#!/usr/bin/env bash
if [[ ${FIXTURE_FAIL_IP:-0} == 1 ]]; then
    printf 'fixture network probe failure\n' >&2
    exit 23
fi
printf 'fixture ip output:'
printf ' <%s>' "$@"
printf '\n'
EOF

cat >"${TOOLS}/cc" <<'EOF'
#!/usr/bin/env bash
if [[ ${1-} == --version ]]; then
    printf 'fixture cc 1.0\n'
    exit 0
fi
output=
while (($#)); do
    if [[ $1 == -o ]]; then output=$2; shift 2; else shift; fi
done
[[ -n ${output} ]]
printf 'fixture C object\n' >"${output}"
EOF
cp -- "${TOOLS}/cc" "${TOOLS}/c++"
chmod 0755 -- "${TOOLS}"/*

write_marker() {
    local path=$1 bootnum=$2 release=$3 root_source=$4 expected_kernel_sha=$5
    cat >"${path}" <<EOF
version=1
status=pending
attempt_id=fixture-attempt
expected_bootnum=${bootnum}
expected_kernel_release=${release}
expected_root_source=${root_source}
expected_kernel_path=/efi/EFI/Gentoo/vmlinuz-fixture.efi
expected_kernel_sha256=${expected_kernel_sha}
expected_initramfs_path=/efi/EFI/Gentoo/initramfs-fixture.img
expected_initramfs_sha256=${initramfs_sha}
EOF
    chmod 0600 -- "${path}"
}

common=(
    --fixture-mode
    --root "${ROOT}"
    --state-root "${STATE}"
    --esp-root "${ESP}"
    --tool-root "${TOOLS}"
)

SUCCESS_MARKER=${MARKERS}/boot-validation.pending
write_marker "${SUCCESS_MARKER}" 0004 7.2.0-fixture /dev/mapper/gentoo-root "${kernel_sha}"
"${HOOK}" "${common[@]}"

[[ -f ${SUCCESS_MARKER} ]] || fail 'completed marker was deleted'
assert_contains 'status=completed' "${SUCCESS_MARKER}"
assert_contains 'result_status=pass' "${SUCCESS_MARKER}"
assert_contains 'completed_boot_id=11111111-2222-3333-4444-555555555555' "${SUCCESS_MARKER}"
success_evidence=$(sed -n 's/^evidence_path=//p' "${SUCCESS_MARKER}")
[[ -f ${success_evidence} ]] || fail "evidence path was not published: ${success_evidence}"
assert_contains 'esp_root='"${ESP}" "${success_evidence}"
assert_contains 'BootCurrent: 0004' "${success_evidence}"
assert_contains 'boot_current_status=match' "${success_evidence}"
assert_contains 'kernel_release_status=match' "${success_evidence}"
assert_contains 'root_source_status=match' "${success_evidence}"
assert_contains 'kernel_hash_status=match' "${success_evidence}"
assert_contains 'initramfs_hash_status=match' "${success_evidence}"
assert_contains '===== rc_status_crashed =====' "${success_evidence}"
assert_contains '===== portage_emerge_info =====' "${success_evidence}"
assert_contains '===== python_portage_smoke =====' "${success_evidence}"
assert_contains '===== shell_smoke =====' "${success_evidence}"
assert_contains '===== c_compiler_smoke =====' "${success_evidence}"
assert_contains '===== cxx_compiler_smoke =====' "${success_evidence}"
assert_contains '===== network_route_v6 =====' "${success_evidence}"
assert_contains 'result_status=pass' "${success_evidence}"
assert_contains 'failure_count=0' "${success_evidence}"
if grep -Eq 'esp_root=.*/boot$|/boot/EFI/' "${success_evidence}"; then
    fail 'boot evidence incorrectly referenced /boot instead of /efi'
fi
assert_count 1 "${EVIDENCE}" '*.log'

# A completed marker is idempotent and never rewrites or deletes its evidence.
"${HOOK}" "${common[@]}"
assert_count 1 "${EVIDENCE}" '*.log'
[[ -f ${success_evidence} ]] || fail 'repeat invocation removed existing evidence'

# Mismatches are evidence failures, but startup-facing execution still succeeds
# and the marker is atomically completed with its failed result.
FAILURE_MARKER=${MARKERS}/failure.pending
write_marker "${FAILURE_MARKER}" 0005 7.2.1-wrong /dev/wrong-root "$(printf '%064d' 0)"
"${HOOK}" "${common[@]}" --marker recovery/failure.pending
assert_contains 'status=completed' "${FAILURE_MARKER}"
assert_contains 'result_status=failed' "${FAILURE_MARKER}"
failure_evidence=$(sed -n 's/^evidence_path=//p' "${FAILURE_MARKER}")
[[ -f ${failure_evidence} ]] || fail 'failed validation evidence was not retained'
assert_contains 'validation:boot_current_mismatch:' "${failure_evidence}"
assert_contains 'validation:kernel_release_mismatch:' "${failure_evidence}"
assert_contains 'validation:root_source_mismatch:' "${failure_evidence}"
assert_contains 'validation:kernel_sha256_mismatch:' "${failure_evidence}"
assert_contains 'result_status=failed' "${failure_evidence}"

# Probe failures are retained with their stderr and exit status as well.
PROBE_MARKER=${MARKERS}/probe-failure.pending
write_marker "${PROBE_MARKER}" 0004 7.2.0-fixture /dev/mapper/gentoo-root "${kernel_sha}"
FIXTURE_FAIL_IP=1 "${HOOK}" "${common[@]}" --marker recovery/probe-failure.pending
assert_contains 'status=completed' "${PROBE_MARKER}"
assert_contains 'result_status=failed' "${PROBE_MARKER}"
probe_evidence=$(sed -n 's/^evidence_path=//p' "${PROBE_MARKER}")
[[ -f ${probe_evidence} ]] || fail 'probe failure evidence was not retained'
assert_contains 'exit_status=23' "${probe_evidence}"
assert_contains 'fixture network probe failure' "${probe_evidence}"
assert_contains 'probe:network_link_failed:exit status 23' "${probe_evidence}"
assert_contains 'probe_failure_count=4' "${probe_evidence}"

# OpenRC prints plain service names (not "[ crashed ]") in --crashed mode.
# A nonempty service list must therefore fail validation.
CRASHED_MARKER=${MARKERS}/crashed-service.pending
write_marker "${CRASHED_MARKER}" 0004 7.2.0-fixture /dev/mapper/gentoo-root "${kernel_sha}"
FIXTURE_CRASHED_SERVICE=thermald "${HOOK}" "${common[@]}" --marker recovery/crashed-service.pending
assert_contains 'status=completed' "${CRASHED_MARKER}"
assert_contains 'result_status=failed' "${CRASHED_MARKER}"
crashed_evidence=$(sed -n 's/^evidence_path=//p' "${CRASHED_MARKER}")
[[ -f ${crashed_evidence} ]] || fail 'crashed-service evidence was not retained'
assert_contains 'thermald' "${crashed_evidence}"
assert_contains 'validation:openrc_crashed_services:' "${crashed_evidence}"

# An unsafe marker is ignored and cannot cause evidence writes.
UNSAFE_MARKER=${MARKERS}/unsafe.pending
write_marker "${UNSAFE_MARKER}" 0004 7.2.0-fixture /dev/mapper/gentoo-root "${kernel_sha}"
chmod 0666 -- "${UNSAFE_MARKER}"
before=$(find "${EVIDENCE}" -maxdepth 1 -type f -name '*.log' | wc -l)
"${HOOK}" "${common[@]}" --marker recovery/unsafe.pending
after=$(find "${EVIDENCE}" -maxdepth 1 -type f -name '*.log' | wc -l)
[[ ${before} -eq ${after} ]] || fail 'unsafe marker produced evidence'
assert_contains 'status=pending' "${UNSAFE_MARKER}"

if find "${STATE}" -type f \( -name '.boot-evidence.*' -o -name '*.completed.*' \) -print -quit | grep -q .; then
    fail 'atomic staging file was left behind'
fi

printf 'PASS: boot evidence fixture suite\n'
