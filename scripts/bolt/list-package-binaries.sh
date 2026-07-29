#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <category/package>" >&2
    exit 2
fi

pkg="$1"

list_package_paths() {
    if command -v qlist >/dev/null 2>&1; then
        qlist -e "${pkg}"
        return
    fi

    if ! command -v portageq >/dev/null 2>&1; then
        echo "qlist or portageq is required" >&2
        exit 1
    fi

    portageq match / "${pkg}" | while read -r cpv; do
        contents="/var/db/pkg/${cpv}/CONTENTS"
        [[ -f "${contents}" ]] || continue
        awk '$1 == "obj" { print $2 }' "${contents}"
    done
}

list_package_paths | while read -r path; do
    if [[ -f "${path}" && -x "${path}" ]]; then
        if file "${path}" | grep -q 'ELF'; then
            echo "${path}"
        fi
    fi
done
