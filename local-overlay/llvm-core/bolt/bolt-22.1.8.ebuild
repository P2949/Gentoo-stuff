# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit cmake llvm.org toolchain-funcs

DESCRIPTION="Binary Optimization and Layout Tool from LLVM"
HOMEPAGE="https://github.com/llvm/llvm-project/tree/main/bolt"

LICENSE="Apache-2.0-with-LLVM-exceptions UoI-NCSA"
SLOT="${LLVM_MAJOR}/${LLVM_SOABI}"
KEYWORDS="amd64"

RDEPEND="
	~llvm-core/llvm-${PV}:${LLVM_MAJOR}=
	~llvm-runtimes/libcxx-${PV}
	~llvm-runtimes/libcxxabi-${PV}
	~llvm-runtimes/libunwind-${PV}
	app-arch/zstd:=
	sys-libs/zlib:=
"
DEPEND="${RDEPEND}"
BDEPEND="
	~llvm-core/clang-${PV}:${LLVM_MAJOR}
	~llvm-core/lld-${PV}:${LLVM_MAJOR}
	app-misc/pax-utils
"

# BOLT calls private target helpers that are intentionally absent from the
# installed monolithic libLLVM ABI.  Build its exact static LLVM closure from
# the same source release, then install only the requested BOLT tools.
LLVM_COMPONENTS=( llvm bolt cmake third-party )
llvm.org_set_globals

CMAKE_BUILD_TYPE=Release

src_configure() {
	local llvm_root="${BROOT}/usr/lib/llvm/${LLVM_MAJOR}"
	local -x CC="${llvm_root}/bin/clang"
	local -x CXX="${llvm_root}/bin/clang++"
	local -x CFLAGS="-O2 -pipe"
	local -x CXXFLAGS="-O2 -pipe -stdlib=libc++"
	local -x LDFLAGS="-fuse-ld=lld -rtlib=compiler-rt -unwindlib=libunwind -stdlib=libc++ -Wl,--as-needed"

	local mycmakeargs=(
		-DCMAKE_INSTALL_PREFIX="${EPREFIX}/usr/lib/llvm/${LLVM_MAJOR}"
		-DCMAKE_INSTALL_LIBDIR="$(get_libdir)"
		-DCMAKE_SKIP_RPATH=ON
		-DBOLT_ENABLE_RUNTIME=OFF
		-DBUILD_SHARED_LIBS=OFF
		-DLLVM_BUILD_BENCHMARKS=OFF
		-DLLVM_BUILD_DOCS=OFF
		-DLLVM_BUILD_EXAMPLES=OFF
		-DLLVM_BUILD_LLVM_DYLIB=OFF
		-DLLVM_BUILD_TESTS=OFF
		-DLLVM_BUILD_TOOLS=OFF
		-DLLVM_ENABLE_BINDINGS=OFF
		-DLLVM_ENABLE_CURL=OFF
		-DLLVM_ENABLE_EH=ON
		-DLLVM_ENABLE_FFI=OFF
		-DLLVM_ENABLE_HTTPLIB=OFF
		-DLLVM_ENABLE_LIBEDIT=OFF
		-DLLVM_ENABLE_LIBPFM=OFF
		-DLLVM_ENABLE_LIBXML2=OFF
		-DLLVM_ENABLE_PROJECTS=bolt
		-DLLVM_ENABLE_RTTI=ON
		-DLLVM_ENABLE_ZLIB=FORCE_ON
		-DLLVM_ENABLE_ZSTD=FORCE_ON
		-DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=
		-DLLVM_INCLUDE_BENCHMARKS=OFF
		-DLLVM_INCLUDE_DOCS=OFF
		-DLLVM_INCLUDE_EXAMPLES=OFF
		-DLLVM_INCLUDE_TESTS=OFF
		-DLLVM_LINK_LLVM_DYLIB=OFF
		-DLLVM_PARALLEL_LINK_JOBS=2
		-DLLVM_TARGETS_TO_BUILD=X86
	)

	cmake_src_configure
}

src_compile() {
	cmake_build llvm-bolt merge-fdata
}

src_install() {
	local build_bin="${BUILD_DIR}/bin"
	local tool

	for tool in llvm-bolt perf2bolt merge-fdata; do
		[[ -x ${build_bin}/${tool} ]] || die "missing built tool: ${tool}"
	done
	"${build_bin}/llvm-bolt" --version >/dev/null 2>&1 || die
	"${build_bin}/perf2bolt" --help >/dev/null 2>&1 || die
	"${build_bin}/merge-fdata" --help >/dev/null 2>&1 || die

	exeinto "/usr/lib/llvm/${LLVM_MAJOR}/bin"
	doexe "${build_bin}/llvm-bolt" "${build_bin}/merge-fdata"
	dosym llvm-bolt "/usr/lib/llvm/${LLVM_MAJOR}/bin/perf2bolt"

	for tool in llvm-bolt perf2bolt merge-fdata; do
		dosym "../lib/llvm/${LLVM_MAJOR}/bin/${tool}" "/usr/bin/${tool}"
	done

	local installed_bin="${ED}/usr/lib/llvm/${LLVM_MAJOR}/bin"
	local rpaths needed
	rpaths=$(scanelf -qyRF '%F;%r' "${installed_bin}") || die
	[[ -z ${rpaths} ]] || die "BOLT tools contain RPATH/RUNPATH: ${rpaths}"
	needed=$(scanelf -BF '%F;%n' \
		"${installed_bin}/llvm-bolt" "${installed_bin}/merge-fdata") || die
	[[ ${needed} != *libLLVM* ]] || die "BOLT tools unexpectedly depend on libLLVM"
}
