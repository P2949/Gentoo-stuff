# Source-pinned compatibility bridge for the GCC 17 PGO lane.
# The moving 9999 ebuild is intentionally not used for this candidate.

EAPI=8

TOOLCHAIN_HAS_TESTS=1
PATCH_GCC_VER="17.0.0"
MUSL_GCC_VER="17.0.0"
PYTHON_COMPAT=( python3_{11..14} )

inherit toolchain

# Pin the pre-GLIBCXX_3.4.37 source revision explicitly.  git-r3 accepts
# hashes through EGIT_COMMIT; keeping this after inherit avoids the live
# ebuild's master-branch assignment while retaining the normal toolchain
# eclass and Gentoo patch flow.
EGIT_COMMIT="336f25a0ad83cc74b6a18137ffd177eefa4a81b0"
EGIT_BRANCH=""

if [[ ${CATEGORY} != cross-* ]] ; then
	RDEPEND="elibc_glibc? ( sys-libs/glibc[cet(-)?] )"
	DEPEND="${RDEPEND}"
fi

src_prepare() {
	local p upstreamed_patches=()
	for p in "${upstreamed_patches[@]}"; do
		rm -v "${WORKDIR}/patch/${p}" || die
	done
	toolchain_src_prepare
	eapply "${FILESDIR}"/${PN}-13-fix-cross-fixincludes.patch
	[[ ${CHOST} == m68k-* ]] && eapply "${FILESDIR}"/${PN}-15-m68k-workaround.patch
	eapply_user
}
