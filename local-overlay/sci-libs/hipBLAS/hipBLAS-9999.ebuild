# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

ROCM_VERSION=${PV}

inherit cmake fortran-2 git-r3 rocm

DESCRIPTION="ROCm BLAS marshalling library"
HOMEPAGE="https://github.com/ROCm/rocm-libraries/tree/develop/projects/hipblas"
EGIT_REPO_URI="https://github.com/ROCm/rocm-libraries.git"
EGIT_BRANCH="develop"
S="${WORKDIR}/${P}/projects/hipblas"

REQUIRED_USE="${ROCM_REQUIRED_USE}"

LICENSE="MIT"
SLOT="0/9999"
SLOT_NOLIVE="0/7.2"
IUSE="rocsolver"

RDEPEND="
	sci-libs/rocBLAS:${SLOT}
	rocsolver? ( sci-libs/rocSOLVER:${SLOT} )
"
DEPEND="
	dev-util/hip:${SLOT_NOLIVE}
	sci-libs/hipBLAS-common:${SLOT}
	${RDEPEND}
"

src_configure() {
	rocm_use_clang

	local mycmakeargs=(
		-DBUILD_CLIENTS_TESTS=OFF
		-DBUILD_CLIENTS_BENCHMARKS=OFF
		-DROCM_SYMLINK_LIBS=OFF
		-DBUILD_WITH_SOLVER=$(usex rocsolver ON OFF)
	)

	cmake_src_configure
}
