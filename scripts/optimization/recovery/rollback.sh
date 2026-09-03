#!/usr/bin/env bash
# Recover packages and optimization configuration for the system-wide
# optimization project.
#
# Live mutation is intentionally fail-closed.  Use --dry-run to inspect an
# action, or --fixture-mode with a non-/ root and tools below --tool-root for
# non-root fixture tests.
#
# Firmware boot entries, EFI variables, kernels, initramfs images, and kernel
# configuration are outside this program's authority.  This program never
# reads, writes, creates, selects, copies, builds, or configures them.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly PROGRAM=${0##*/}
readonly VERSION=3
readonly PROHIBITED_BOOT_MESSAGE='boot-entry, EFI-variable, kernel, and initramfs operations are permanently outside project authority'

DRY_RUN=0
FIXTURE_MODE=0
PORTAGE_ROOT=${RECOVERY_PORTAGE_ROOT:-/}
STATE_ROOT=${RECOVERY_STATE_ROOT:-}
CACHE_ROOT=${RECOVERY_CACHE_ROOT:-}
PKGDIR=${RECOVERY_PKGDIR:-}
TOOL_ROOT=${RECOVERY_TOOL_ROOT:-}
LOG_DIR=${RECOVERY_LOG_DIR:-}
CRITICAL_FILE=${RECOVERY_CRITICAL_FILE:-}
COMMAND=
LOG_FILE=
LOG_READY=0
LOCK_HELD=0
LOCK_FD=
SIMULATED_KILL_SWITCH=0
RESOLVED_PKGDIR=
PACKAGES_INDEX=
ATOM_CPV=
VALIDATED_ARCHIVE=
CHECK_FAILURES=0
declare -a EXTRA_ASSIGNMENTS=()
declare -a RECOVERY_GCC_ATOMS=(sys-devel/gcc dev-lang/fpc dev-build/make sys-fs/zfs)

usage() {
    cat <<EOF
Usage: ${PROGRAM} [global options] COMMAND [=category/package-version ...]

Commands:
  check                 Read-only preflight of rollback prerequisites.
  disable               Disable active PGO/BOLT package.env assignments and
                        install the optimization-off kill switch.
  restore ATOM...       Restore one or more exact atoms from protected PKGDIR.
  restore-critical      Restore all exact CPVs in protected PKGDIR (or those
                        listed by --critical-file).
  preserved-rebuild     Rebuild @preserved-rebuild with optimization disabled.
  all                   Run disable, restore-critical, and preserved-rebuild.

Global options (must precede COMMAND):
  --dry-run                      Log mutations without performing them.
  --check                        Alias for the check command.
  --root DIR                     Portage ROOT/PORTAGE_CONFIGROOT (default /).
  --state-root DIR               Persistent state root.
  --cache-root DIR               Cache/recovery root.
  --pkgdir DIR                   Protected binary package directory; default
                                 CACHE_ROOT/binpkgs/critical-current.
  --tool-root DIR                Resolve emerge from this directory (required
                                 for non-root fixtures).
  --fixture-mode                 Permit non-root mutation only below a non-/
                                 Portage root, using tools below --tool-root.
  --log-dir DIR                  Recovery log directory.
  --assignment RELPATH           Additional package.env file to disable.
  --critical-file FILE           Exact atoms to restore instead of every CPV in
                                 the protected Packages index.
  -h, --help                     Show this help.

Restore operations use emerge --usepkgonly with remote binhosts, source fetch,
and source fallback disabled.  All actions emit a timestamp-named log.

Boot-entry, EFI-variable, kernel, and initramfs actions are prohibited.  Their
historical commands and options fail closed and never resolve or invoke a tool.
EOF
}

timestamp() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

log() {
    local level=$1
    shift
    printf '%s [%s] %s\n' "$(timestamp)" "${level}" "$*"
}

die() {
    log ERROR "$*" >&2
    exit 1
}

is_exact_cpv() {
    local cpv=$1 package_version
    local category_re='[A-Za-z0-9_][A-Za-z0-9+_.-]*'
    local package_re='[A-Za-z0-9_][A-Za-z0-9+_-]*'
    local version_re='[0-9]+([.][0-9]+)*[a-z]?(_(alpha|beta|pre|rc|p)[0-9]*)*'
    local version_revision_re="${version_re}(-r[0-9]+)?"
    [[ ${cpv} =~ ^${category_re}/${package_re}-${version_revision_re}$ ]] || return 1
    package_version=${cpv#*/}
    [[ ! ${package_version} =~ ^${package_re}-${version_revision_re}-${version_revision_re}$ ]]
}

warn() {
    log WARN "$*" >&2
}

need_option_value() {
    (($# >= 2)) || die "option $1 requires a value"
}

while (($#)); do
    case $1 in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --execute|--regenerate-initramfs|--overwrite-initramfs|--overwrite-known-good|--legacy-managed-default|--esp-root|--recovery-manifest|--known-good-bootnum|--known-good-kernel|--known-good-kernel-image|--known-good-initramfs|--kernel-sha256|--initramfs-sha256|--initramfs-output|--preserve-kernel|--recovery-tag|--efi-label|--efi-disk|--efi-part|--cmdline-file|--microcode)
            die "${PROHIBITED_BOOT_MESSAGE}: $1"
            ;;
        --check)
            [[ -z ${COMMAND} ]] || die "--check cannot be combined with a command"
            COMMAND=check
            shift
            ;;
        --fixture-mode)
            FIXTURE_MODE=1
            shift
            ;;
        --root)
            need_option_value "$@"
            PORTAGE_ROOT=$2
            shift 2
            ;;
        --state-root)
            need_option_value "$@"
            STATE_ROOT=$2
            shift 2
            ;;
        --cache-root)
            need_option_value "$@"
            CACHE_ROOT=$2
            shift 2
            ;;
        --pkgdir)
            need_option_value "$@"
            PKGDIR=$2
            shift 2
            ;;
        --tool-root)
            need_option_value "$@"
            TOOL_ROOT=$2
            shift 2
            ;;
        --log-dir)
            need_option_value "$@"
            LOG_DIR=$2
            shift 2
            ;;
        --assignment)
            need_option_value "$@"
            EXTRA_ASSIGNMENTS+=("$2")
            shift 2
            ;;
        --critical-file)
            need_option_value "$@"
            CRITICAL_FILE=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        initramfs|bootnext|preserve-boot|rescue-entry)
            die "${PROHIBITED_BOOT_MESSAGE}: $1"
            ;;
        check|disable|restore|restore-critical|preserved-rebuild|all)
            [[ -z ${COMMAND} ]] || die "multiple commands supplied"
            COMMAND=$1
            shift
            break
            ;;
        --)
            shift
            break
            ;;
        *)
            die "unknown option or command: $1"
            ;;
    esac
done

declare -a COMMAND_ARGS=("$@")
[[ -n ${COMMAND} ]] || die "a command is required (try --help)"
if [[ ${COMMAND} != restore && ${#COMMAND_ARGS[@]} -ne 0 ]]; then
    die "${COMMAND} does not accept positional arguments"
fi
if [[ ${COMMAND} == restore && ${#COMMAND_ARGS[@]} -eq 0 ]]; then
    die "restore requires at least one exact atom"
fi

normalize_absolute() {
    local value=$1
    local label=$2
    [[ ${value} == /* ]] || die "${label} must be an absolute path: ${value}"
    if [[ ${value} != / ]]; then
        value=${value%/}
    fi
    printf '%s' "${value}"
}

root_path() {
    local relative=${1#/}
    if [[ ${PORTAGE_ROOT} == / ]]; then
        printf '/%s' "${relative}"
    else
        printf '%s/%s' "${PORTAGE_ROOT}" "${relative}"
    fi
}

path_is_within() {
    local child parent
    child=$(readlink -m -- "$1") || return 1
    parent=$(readlink -m -- "$2") || return 1
    [[ ${child} == "${parent}" || ${child} == "${parent}/"* ]]
}

PORTAGE_ROOT=$(normalize_absolute "${PORTAGE_ROOT}" "Portage root")
if [[ -z ${STATE_ROOT} ]]; then
    STATE_ROOT=$(root_path /var/lib/gentoo-optimization)
fi
if [[ -z ${CACHE_ROOT} ]]; then
    CACHE_ROOT=$(root_path /var/cache/gentoo-optimization)
fi
STATE_ROOT=$(normalize_absolute "${STATE_ROOT}" "state root")
CACHE_ROOT=$(normalize_absolute "${CACHE_ROOT}" "cache root")
if [[ -n ${TOOL_ROOT} ]]; then
    TOOL_ROOT=$(normalize_absolute "${TOOL_ROOT}" "tool root")
fi
if [[ -z ${PKGDIR} ]]; then
    PKGDIR=${CACHE_ROOT}/binpkgs/critical-current
fi
PKGDIR=$(normalize_absolute "${PKGDIR}" "PKGDIR")
if [[ -z ${LOG_DIR} ]]; then
    LOG_DIR=${STATE_ROOT}/reports/recovery
fi
LOG_DIR=$(normalize_absolute "${LOG_DIR}" "log directory")
if [[ -n ${CRITICAL_FILE} ]]; then
    CRITICAL_FILE=$(normalize_absolute "${CRITICAL_FILE}" "critical atom file")
fi

is_mutating_command() {
    case ${COMMAND} in
        disable|restore|restore-critical|preserved-rebuild|all)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

require_mutation_authority() {
    ((DRY_RUN)) && return 0
    is_mutating_command || return 0
    if ((EUID == 0)); then
        return 0
    fi
    ((FIXTURE_MODE)) || die "live mutations require root; use --dry-run for inspection"
    [[ ${PORTAGE_ROOT} != / ]] || die "fixture mutation refuses Portage root /"
    [[ -n ${TOOL_ROOT} ]] || die "non-root fixture mutation requires --tool-root"
    path_is_within "${TOOL_ROOT}" "${PORTAGE_ROOT}" || die "fixture tool root must be below the fixture Portage root"
}

init_log() {
    local requested=${LOG_DIR}
    local stamp
    stamp=$(date -u '+%Y%m%dT%H%M%SZ')
    if ! mkdir -p -- "${requested}" 2>/dev/null || [[ ! -w ${requested} ]]; then
        if is_mutating_command && ((DRY_RUN == 0)); then
            die "cannot create persistent recovery log directory: ${requested}"
        fi
        requested=${TMPDIR:-/tmp}/gentoo-optimization-recovery-${EUID}
        mkdir -p -- "${requested}" || die "cannot create fallback log directory: ${requested}"
        warn "persistent log directory is not writable; using ${requested}"
    fi
    LOG_FILE=${requested}/rollback-${stamp}-${$}.log
    : >"${LOG_FILE}" || die "cannot create recovery log: ${LOG_FILE}"
    chmod 0600 "${LOG_FILE}" || die "cannot protect recovery log: ${LOG_FILE}"
    exec > >(tee -a -- "${LOG_FILE}") 2>&1
    LOG_READY=1
    log INFO "${PROGRAM} v${VERSION}: command=${COMMAND} dry_run=${DRY_RUN}"
    log INFO "log=${LOG_FILE}"
    log INFO "portage_root=${PORTAGE_ROOT} state_root=${STATE_ROOT} cache_root=${CACHE_ROOT} pkgdir=${PKGDIR}"
}

release_lock() {
    if ((LOCK_HELD)); then
        flock -u "${LOCK_FD}" || true
        LOCK_HELD=0
    fi
}

on_exit() {
    local rc=$?
    trap - EXIT
    release_lock
    if ((LOG_READY)); then
        if ((rc == 0)); then
            log INFO "completed successfully"
        else
            log ERROR "failed with exit status ${rc}"
        fi
    fi
    exit "${rc}"
}
trap on_exit EXIT

require_mutation_authority
init_log

acquire_lock() {
    ((DRY_RUN)) && return 0
    is_mutating_command || return 0
    local lock_dir=${STATE_ROOT}/locks
    mkdir -p -- "${lock_dir}" || die "cannot create recovery lock directory"
    exec {LOCK_FD}>"${lock_dir}/rollback.lock"
    flock -n "${LOCK_FD}" || die "another recovery action holds ${lock_dir}/rollback.lock"
    LOCK_HELD=1
}
acquire_lock

resolve_tool() {
    local name=$1
    local result
    if [[ -n ${TOOL_ROOT} ]]; then
        result=${TOOL_ROOT}/${name}
        [[ -x ${result} ]] || die "required fixture tool is not executable: ${result}"
    else
        result=$(command -v -- "${name}") || die "required tool not found: ${name}"
    fi
    printf '%s' "${result}"
}

format_command() {
    local arg
    printf 'RUN'
    for arg in "$@"; do
        printf ' %q' "${arg}"
    done
    printf '\n'
}

run_mutation() {
    format_command "$@"
    if ((DRY_RUN)); then
        return 0
    fi
    "$@"
}

assert_secure_path() {
    local path=$1
    local kind=$2
    local mode owner numeric
    if [[ ${kind} == directory ]]; then
        [[ -d ${path} ]] || die "required protected directory is absent: ${path}"
    else
        [[ -f ${path} ]] || die "required protected file is absent: ${path}"
    fi
    mode=$(stat -Lc '%a' -- "${path}") || die "cannot stat protected ${kind}: ${path}"
    owner=$(stat -Lc '%u' -- "${path}") || die "cannot read owner of protected ${kind}: ${path}"
    numeric=$((8#${mode}))
    (( (numeric & 8#022) == 0 )) || die "protected ${kind} is group/world writable: ${path} (mode ${mode})"
    if [[ ${PORTAGE_ROOT} == / ]] && ((owner != 0)); then
        die "live protected ${kind} is not root-owned: ${path}"
    fi
}

resolve_pkgdir() {
    if [[ -L ${PKGDIR} && ${PORTAGE_ROOT} == / ]]; then
        local link_owner
        link_owner=$(stat -c '%u' -- "${PKGDIR}") || die "cannot stat PKGDIR symlink: ${PKGDIR}"
        ((link_owner == 0)) || die "live PKGDIR symlink is not root-owned: ${PKGDIR}"
    fi
    RESOLVED_PKGDIR=$(readlink -f -- "${PKGDIR}") || die "protected PKGDIR does not resolve: ${PKGDIR}"
    assert_secure_path "${RESOLVED_PKGDIR}" directory
    PACKAGES_INDEX=${RESOLVED_PKGDIR}/Packages
    assert_secure_path "${PACKAGES_INDEX}" file
    [[ -r ${PACKAGES_INDEX} ]] || die "protected Packages index is not readable: ${PACKAGES_INDEX}"
    log INFO "protected PKGDIR resolved to ${RESOLVED_PKGDIR}"
}

atom_to_cpv() {
    local atom=$1
    local body
    [[ ${atom} == =* ]] || die "restore atom must be exact and begin with '=': ${atom}"
    [[ ${atom} != *'['* && ${atom} != *']'* && ${atom} != *'*'* && ${atom} != *'?'* ]] || die "restore atom may not contain USE/wildcard syntax: ${atom}"
    [[ ${atom} != *[[:space:]]* ]] || die "restore atom contains whitespace: ${atom}"
    body=${atom#=}
    body=${body%%::*}
    body=${body%%:*}
    is_exact_cpv "${body}" || die "invalid exact Gentoo CPV in restore atom: ${atom}"
    ATOM_CPV=${body}
}

validate_archive_for_cpv() {
    local cpv=$1
    local record rel_path expected_size expected_md5 archive actual_size actual_md5
    local -a records=()
    mapfile -t records < <(
        awk -v wanted="${cpv}" '
            BEGIN { RS=""; FS="\n" }
            {
                cpv=""; path=""; size=""; md5=""
                for (i=1; i<=NF; i++) {
                    if ($i ~ /^CPV: /) cpv=substr($i, 6)
                    else if ($i ~ /^PATH: /) path=substr($i, 7)
                    else if ($i ~ /^SIZE: /) size=substr($i, 7)
                    else if ($i ~ /^MD5: /) md5=substr($i, 6)
                }
                if (cpv == wanted) print path "|" size "|" md5
            }
        ' "${PACKAGES_INDEX}"
    )
    ((${#records[@]} == 1)) || die "expected exactly one protected binpkg record for ${cpv}; found ${#records[@]}"
    record=${records[0]}
    IFS='|' read -r rel_path expected_size expected_md5 <<<"${record}"
    [[ -n ${rel_path} ]] || die "protected Packages record has no PATH for ${cpv}"
    [[ ${rel_path} != /* && /${rel_path}/ != *'/../'* ]] || die "unsafe archive PATH for ${cpv}: ${rel_path}"
    archive=$(readlink -f -- "${RESOLVED_PKGDIR}/${rel_path}") || die "protected archive is absent for ${cpv}: ${rel_path}"
    path_is_within "${archive}" "${RESOLVED_PKGDIR}" || die "protected archive escapes PKGDIR for ${cpv}: ${archive}"
    assert_secure_path "${archive}" file
    [[ -r ${archive} ]] || die "protected archive is not readable for ${cpv}: ${archive}"
    if [[ -n ${expected_size} ]]; then
        [[ ${expected_size} =~ ^[0-9]+$ ]] || die "invalid SIZE in protected Packages record for ${cpv}"
        actual_size=$(stat -Lc '%s' -- "${archive}") || die "cannot size archive for ${cpv}"
        [[ ${actual_size} == "${expected_size}" ]] || die "archive SIZE mismatch for ${cpv}: expected ${expected_size}, got ${actual_size}"
    else
        warn "protected Packages record has no SIZE for ${cpv}"
    fi
    if [[ -n ${expected_md5} ]]; then
        [[ ${expected_md5} =~ ^[0-9a-fA-F]{32}$ ]] || die "invalid MD5 in protected Packages record for ${cpv}"
        actual_md5=$(md5sum -- "${archive}") || die "cannot hash archive for ${cpv}"
        actual_md5=${actual_md5%% *}
        [[ ${actual_md5,,} == "${expected_md5,,}" ]] || die "archive MD5 mismatch for ${cpv}"
    else
        warn "protected Packages record has no MD5 for ${cpv}"
    fi
    VALIDATED_ARCHIVE=${archive}
}

collect_protected_atoms() {
    local line
    local -n destination=$1
    destination=()
    if [[ -n ${CRITICAL_FILE} ]]; then
        assert_secure_path "${CRITICAL_FILE}" file
        while IFS= read -r line || [[ -n ${line} ]]; do
            line=${line%%#*}
            line=${line#"${line%%[![:space:]]*}"}
            line=${line%"${line##*[![:space:]]}"}
            [[ -n ${line} ]] || continue
            atom_to_cpv "${line}"
            destination+=("${line}")
        done <"${CRITICAL_FILE}"
    else
        mapfile -t destination < <(awk '/^CPV: / { print "=" substr($0, 6) }' "${PACKAGES_INDEX}" | LC_ALL=C sort -u)
    fi
    ((${#destination[@]} > 0)) || die "protected critical package set is empty"
}

optimization_env_content() {
    cat <<'EOF'
# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.
RECOVERY_OPTIMIZATION_DISABLED="1"
RECOVERY_COMPILER_LANE=""
PGO_INSTRUMENT="0"
PGO_DISABLE_USE="1"
PGO_USE_IF_AVAILABLE="0"
PGO_GENERATE="0"
PGO_USE="0"
BOLT_CAPTURE="0"
BOLT_DEPLOY="0"
BOLT_ENABLE="0"
AUTOFDO_ENABLE="0"
PROPELLER_ENABLE="0"
POLLY_FLAGS=""
AGGRO_OPT_FLAGS=""
OPT_FLAGS=""
CLEAN_FLAGS=""
BOLT_READY_FLAGS=""
BOLT_READY_LD_FLAGS=""
PROFILE_MAPPING_FLAGS=""
SECTION_FLAGS=""
SECTION_LD_FLAGS=""
VISIBILITY_FLAGS=""
CXX_VISIBILITY_FLAGS=""
OPT_REMARK_FLAGS=""
VTABLE_OPT_FLAGS=""
GLOBAL_PERF_FLAGS=""
LTO_FLAGS=""
LTO_UNI_FLAGS=""
UARG_FLAGS=""
RUNTIME_LINK_FLAGS=""
LD_OPT_FLAGS=""
LD_CLEAN_FLAGS=""
FORCED_LIBS=""
COMMON_FLAGS=""
CFLAGS=""
CXXFLAGS=""
FCFLAGS=""
FFLAGS=""
LDFLAGS=""
RUSTFLAGS=""
RUSTFLAGS_BOOTSTRAP=""
GOFLAGS=""
EOF
}

recovery_clang_libcxx_env_content() {
    cat <<'EOF'
# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.
# Conservative Clang recovery baseline. Keep the installed libc++ ABI and
# compiler-rt/libunwind/lld runtime policy while dropping all project PGO,
# BOLT, LTO, Polly, OpenMP, visibility, section-splitting, and remark flags.
RECOVERY_COMPILER_LANE="clang-libcxx"
COMMON_FLAGS="-O2 -pipe"
CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS} -stdlib=libc++"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"
LDFLAGS="-fuse-ld=lld -rtlib=compiler-rt -unwindlib=libunwind -stdlib=libc++"
CC="clang"
CXX="clang++"
CPP="clang-cpp"
LD="ld.lld"
AR="llvm-ar"
NM="llvm-nm"
RANLIB="llvm-ranlib"
LIB_FLAGS="-stdlib=libc++"
RUNTIME_LINK_FLAGS="-fuse-ld=lld -rtlib=compiler-rt -unwindlib=libunwind -stdlib=libc++"
GCCLIB_FLAGS=""
EOF
}

recovery_gcc_env_content() {
    cat <<'EOF'
# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.
# Conservative GCC recovery baseline. Use GCC/libstdc++-compatible tools and
# flags only; no Clang runtime, PGO, BOLT, LTO, or project optimization axes.
RECOVERY_COMPILER_LANE="gcc-libstdcxx"
COMMON_FLAGS="-O2 -pipe"
CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS}"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"
LDFLAGS=""
CC="gcc"
CXX="g++"
CPP="gcc -E"
LD="ld.bfd"
AR="gcc-ar"
NM="gcc-nm"
RANLIB="gcc-ranlib"
LIB_FLAGS=""
RUNTIME_LINK_FLAGS=""
GCCLIB_FLAGS=""
EOF
}

optimization_assignment_content() {
    local atom
    cat <<'EOF'
# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.
*/* recovery/optimization-off.conf recovery/clang-libcxx.conf
# Every package assigned to gcc.conf by the live policy is repeated below so
# it cannot inherit the global Clang recovery lane after the final 99-* file.
EOF
    for atom in "${RECOVERY_GCC_ATOMS[@]}"; do
        printf '%s recovery/gcc.conf\n' "${atom}"
    done
}

collect_recovery_gcc_atoms() {
    local package_env_dir=$1
    local path atom
    local -A seen=()
    local -a discovered=()
    for atom in "${RECOVERY_GCC_ATOMS[@]}"; do
        seen["${atom}"]=1
    done
    while IFS= read -r -d '' path; do
        [[ ${path##*/} != 99-recovery-optimization-off ]] || continue
        while IFS= read -r atom; do
            [[ -n ${atom} ]] || continue
            [[ ${atom} != *[[:space:]]* && ${atom} == */* ]] || die "unsafe GCC package.env selector discovered: ${atom}"
            seen["${atom}"]=1
        done < <(
            awk '
                /^[[:space:]]*#/ || NF < 2 { next }
                {
                    for (i=2; i<=NF; i++) {
                        if ($i == "gcc.conf" || $i ~ /\/gcc[.]conf$/) {
                            print $1
                            break
                        }
                    }
                }
            ' "${path}"
        )
    done < <(find "${package_env_dir}" -type f -print0)
    mapfile -t discovered < <(printf '%s\n' "${!seen[@]}" | LC_ALL=C sort)
    RECOVERY_GCC_ATOMS=("${discovered[@]}")
    ((${#RECOVERY_GCC_ATOMS[@]} > 0)) || die "recovery GCC assignment set is empty"
}

atomic_install_content() {
    local path=$1
    local mode=$2
    local producer=$3
    local permit_managed_upgrade=${4:-0}
    local directory temp
    directory=${path%/*}
    if ((DRY_RUN)); then
        log INFO "would atomically install ${path} (mode ${mode})"
        return 0
    fi
    mkdir -p -- "${directory}" || die "cannot create ${directory}"
    temp=$(mktemp "${directory}/.${PROGRAM}.XXXXXX") || die "cannot create temporary file in ${directory}"
    "${producer}" >"${temp}" || {
        rm -f -- "${temp}"
        die "cannot generate ${path}"
    }
    chmod "${mode}" "${temp}" || {
        rm -f -- "${temp}"
        die "cannot set mode on temporary ${path}"
    }
    if [[ -e ${path} ]]; then
        if cmp -s -- "${temp}" "${path}"; then
            rm -f -- "${temp}"
            return 0
        fi
        if ((permit_managed_upgrade)) && head -n 1 -- "${path}" | grep -Fxq '# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.'; then
            mv -T -- "${temp}" "${path}" || {
                rm -f -- "${temp}"
                die "cannot atomically upgrade managed file: ${path}"
            }
            log INFO "atomically upgraded prior managed recovery file: ${path}"
            return 0
        fi
        rm -f -- "${temp}"
        die "refusing to overwrite non-matching unmanaged file: ${path}"
    fi
    mv -T -- "${temp}" "${path}" || {
        rm -f -- "${temp}"
        die "cannot atomically install ${path}"
    }
}

is_optimization_assignment() {
    local path=$1
    local base=${path##*/}
    [[ ${base} == 99-recovery-optimization-off ]] && return 1
    case ${base} in
        50-global-pgo|50-pgo-generated|51-bolt-capture-generated|52-bolt-deploy-generated)
            return 0
            ;;
    esac
    if [[ ${base,,} =~ (pgo|bolt|autofdo|propeller).*(generated|active|deploy|capture)|(generated).*(pgo|bolt|autofdo|propeller) ]]; then
        return 0
    fi
    awk '
        /^[[:space:]]*#/ { next }
        {
            line=tolower($0)
            if (line ~ /(^|[[:space:]_\/.:-])(pgo|bolt|autofdo|propeller)([[:space:]_\/.:-]|$)/ ||
                line ~ /optimization\/[[:alnum:]_.\/-]+\.conf/) found=1
        }
        END { exit(found ? 0 : 1) }
    ' "${path}"
}

collect_optimization_assignments() {
    local package_env_dir=$1
    # Bash nameref intentionally receives the caller's array variable name.
    # shellcheck disable=SC2178
    local -n destination=$2
    local path relative
    local -A seen=()
    destination=()
    [[ -d ${package_env_dir} ]] || die "Portage package.env directory is absent: ${package_env_dir}"
    while IFS= read -r -d '' path; do
        if is_optimization_assignment "${path}"; then
            destination+=("${path}")
            seen["${path}"]=1
        fi
    done < <(find "${package_env_dir}" -type f -print0)
    for relative in "${EXTRA_ASSIGNMENTS[@]}"; do
        [[ ${relative} != /* && /${relative}/ != *'/../'* ]] || die "--assignment must be a safe path relative to package.env: ${relative}"
        path=${package_env_dir}/${relative}
        [[ -f ${path} ]] || die "requested package.env assignment is absent: ${path}"
        if [[ -z ${seen[${path}]:-} ]]; then
            destination+=("${path}")
            seen["${path}"]=1
        fi
    done
}

kill_switch_is_active() {
    local config_root
    config_root=$(root_path /etc/portage)
    [[ -f ${STATE_ROOT}/recovery/optimization.disabled ]] || return 1
    [[ -f ${config_root}/env/recovery/optimization-off.conf ]] || return 1
    [[ -f ${config_root}/env/recovery/clang-libcxx.conf ]] || return 1
    [[ -f ${config_root}/env/recovery/gcc.conf ]] || return 1
    [[ -f ${config_root}/package.env/99-recovery-optimization-off ]] || return 1
    grep -Fxq '*/* recovery/optimization-off.conf recovery/clang-libcxx.conf' "${config_root}/package.env/99-recovery-optimization-off" &&
        grep -Fxq 'sys-devel/gcc recovery/gcc.conf' "${config_root}/package.env/99-recovery-optimization-off"
}

disable_optimization() {
    local config_root package_env_dir quarantine txn path relative target manifest marker marker_tmp
    local -a assignments=()
    config_root=$(root_path /etc/portage)
    package_env_dir=${config_root}/package.env
    collect_optimization_assignments "${package_env_dir}" assignments
    txn=$(date -u '+%Y%m%dT%H%M%SZ')-${$}
    quarantine=${config_root}/package.env.recovery-disabled/${txn}
    manifest=${STATE_ROOT}/recovery/disabled-${txn}.manifest
    marker=${STATE_ROOT}/recovery/optimization.disabled

    collect_recovery_gcc_atoms "${package_env_dir}"
    atomic_install_content "${config_root}/env/recovery/optimization-off.conf" 0644 optimization_env_content 1
    atomic_install_content "${config_root}/env/recovery/clang-libcxx.conf" 0644 recovery_clang_libcxx_env_content 1
    atomic_install_content "${config_root}/env/recovery/gcc.conf" 0644 recovery_gcc_env_content 1
    atomic_install_content "${package_env_dir}/99-recovery-optimization-off" 0644 optimization_assignment_content 1

    if ((DRY_RUN)); then
        log INFO "would atomically create kill-switch marker ${marker}"
        for path in "${assignments[@]}"; do
            log INFO "would quarantine optimization assignment ${path}"
        done
        SIMULATED_KILL_SWITCH=1
        return 0
    fi

    mkdir -p -- "${STATE_ROOT}/recovery" "${quarantine}" || die "cannot create recovery transaction directories"
    marker_tmp=$(mktemp "${STATE_ROOT}/recovery/.optimization.disabled.XXXXXX") || die "cannot stage kill-switch marker"
    {
        printf 'version=%s\n' "${VERSION}"
        printf 'disabled_at=%s\n' "$(timestamp)"
        printf 'transaction=%s\n' "${txn}"
        printf 'portage_root=%s\n' "${PORTAGE_ROOT}"
    } >"${marker_tmp}"
    chmod 0644 "${marker_tmp}"
    if [[ -e ${marker} ]]; then
        rm -f -- "${marker_tmp}"
    else
        mv -T -- "${marker_tmp}" "${marker}" || die "cannot atomically activate kill-switch marker"
    fi

    : >"${manifest}.tmp"
    chmod 0600 "${manifest}.tmp"
    for path in "${assignments[@]}"; do
        relative=${path#"${package_env_dir}/"}
        target=${quarantine}/${relative}
        mkdir -p -- "${target%/*}" || die "cannot create quarantine path for ${relative}"
        printf '%s\t%s\t%s\n' "$(sha256sum -- "${path}" | awk '{print $1}')" "${path}" "${target}" >>"${manifest}.tmp"
        if ! mv -T -- "${path}" "${target}"; then
            die "failed to atomically quarantine ${path}; kill switch remains active"
        fi
        log INFO "quarantined optimization assignment ${path} -> ${target}"
    done
    mv -T -- "${manifest}.tmp" "${manifest}" || die "cannot publish disable transaction manifest"
    kill_switch_is_active || die "optimization kill switch failed post-install verification"
    collect_optimization_assignments "${package_env_dir}" assignments
    ((${#assignments[@]} == 0)) || die "active optimization assignments remain after disable"
    log INFO "optimization is disabled; transaction manifest=${manifest}"
}

require_kill_switch() {
    if ((DRY_RUN && SIMULATED_KILL_SWITCH)); then
        return 0
    fi
    kill_switch_is_active || die "optimization kill switch is not active; run '${PROGRAM} disable' first"
}

restore_atoms() {
    local emerge atom
    local -a atoms=("$@")
    resolve_pkgdir
    for atom in "${atoms[@]}"; do
        atom_to_cpv "${atom}"
        validate_archive_for_cpv "${ATOM_CPV}"
        log INFO "validated protected archive for ${atom}: ${VALIDATED_ARCHIVE}"
    done
    emerge=$(resolve_tool emerge)
    run_mutation env \
        "ROOT=${PORTAGE_ROOT}" \
        "PORTAGE_CONFIGROOT=${PORTAGE_ROOT}" \
        "PKGDIR=${RESOLVED_PKGDIR}" \
        'PORTAGE_BINHOST=' \
        'GENTOO_MIRRORS=' \
        'FETCHCOMMAND=/bin/false' \
        'RESUMECOMMAND=/bin/false' \
        'PGO_INSTRUMENT=0' \
        'PGO_DISABLE_USE=1' \
        'PGO_USE_IF_AVAILABLE=0' \
        'BOLT_CAPTURE=0' \
        'BOLT_DEPLOY=0' \
        "${emerge}" \
        --ignore-default-opts \
        --ask=n \
        --autounmask=n \
        --buildpkg=n \
        --getbinpkg=n \
        --usepkgonly \
        --binpkg-changed-deps=n \
        --binpkg-respect-use=n \
        --oneshot \
        --verbose \
        "${atoms[@]}"
}

restore_critical() {
    local -a atoms=()
    resolve_pkgdir
    collect_protected_atoms atoms
    log INFO "critical restore set contains ${#atoms[@]} exact CPVs"
    restore_atoms "${atoms[@]}"
}

run_preserved_rebuild() {
    local emerge
    require_kill_switch
    resolve_pkgdir
    emerge=$(resolve_tool emerge)
    run_mutation env \
        "ROOT=${PORTAGE_ROOT}" \
        "PORTAGE_CONFIGROOT=${PORTAGE_ROOT}" \
        "PKGDIR=${RESOLVED_PKGDIR}" \
        'PORTAGE_BINHOST=' \
        'GENTOO_MIRRORS=' \
        'FETCHCOMMAND=/bin/false' \
        'RESUMECOMMAND=/bin/false' \
        'RECOVERY_OPTIMIZATION_DISABLED=1' \
        'PGO_INSTRUMENT=0' \
        'PGO_DISABLE_USE=1' \
        'PGO_USE_IF_AVAILABLE=0' \
        'BOLT_CAPTURE=0' \
        'BOLT_DEPLOY=0' \
        'RUSTFLAGS=' \
        'GOFLAGS=' \
        "${emerge}" \
        --ignore-default-opts \
        --ask=n \
        --autounmask=n \
        --buildpkg=n \
        --getbinpkg=n \
        --usepkgonly \
        --binpkg-changed-deps=n \
        --binpkg-respect-use=n \
        --oneshot \
        --verbose \
        @preserved-rebuild
}

check_assignment_state() {
    local config_root package_env_dir
    local -a assignments=()
    config_root=$(root_path /etc/portage)
    package_env_dir=${config_root}/package.env
    collect_optimization_assignments "${package_env_dir}" assignments
    if ((${#assignments[@]})); then
        log INFO "${#assignments[@]} optimization assignment file(s) would be disabled:"
        printf '  %s\n' "${assignments[@]}"
    else
        log INFO "no active legacy/generated optimization assignments detected"
    fi
    if kill_switch_is_active; then
        log INFO "optimization kill switch is active"
    else
        log INFO "optimization kill switch is not active (expected before rollback)"
    fi
}

check_protected_set() {
    local atom
    local -a atoms=()
    resolve_pkgdir
    collect_protected_atoms atoms
    for atom in "${atoms[@]}"; do
        atom_to_cpv "${atom}"
        validate_archive_for_cpv "${ATOM_CPV}"
    done
    log INFO "validated ${#atoms[@]} exact protected binpkg record(s)"
    resolve_tool emerge >/dev/null
}

check_item() {
    local label=$1
    shift
    log INFO "CHECK ${label}"
    if ("$@"); then
        log INFO "PASS ${label}"
    else
        warn "FAIL ${label}"
        CHECK_FAILURES=$((CHECK_FAILURES + 1))
    fi
}

run_checks() {
    check_item package-env check_assignment_state
    check_item protected-binpkgs check_protected_set
    if ((CHECK_FAILURES)); then
        die "rollback preflight failed ${CHECK_FAILURES} check group(s)"
    fi
    log INFO "rollback preflight passed"
}

case ${COMMAND} in
    check)
        run_checks
        ;;
    disable)
        disable_optimization
        ;;
    restore)
        restore_atoms "${COMMAND_ARGS[@]}"
        ;;
    restore-critical)
        restore_critical
        ;;
    preserved-rebuild)
        run_preserved_rebuild
        ;;
    all)
        disable_optimization
        restore_critical
        run_preserved_rebuild
        ;;
    *)
        die "internal error: unhandled command ${COMMAND}"
        ;;
esac
