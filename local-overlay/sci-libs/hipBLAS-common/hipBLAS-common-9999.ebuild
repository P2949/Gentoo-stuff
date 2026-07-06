# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit cmake git-r3

DESCRIPTION="Common files shared by hipBLAS and hipBLASLt"
HOMEPAGE="https://github.com/ROCm/rocm-libraries/tree/develop/projects/hipblas-common"
EGIT_REPO_URI="https://github.com/ROCm/rocm-libraries.git"
EGIT_BRANCH="develop"
S="${WORKDIR}/${P}/projects/hipblas-common"

LICENSE="MIT"
SLOT="0/9999"

BDEPEND="dev-build/rocm-cmake:0/7.2"
