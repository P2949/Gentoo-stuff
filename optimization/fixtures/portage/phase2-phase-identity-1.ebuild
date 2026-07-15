# shellcheck shell=bash disable=SC2034
EAPI=8

DESCRIPTION="Disposable Portage phase identity fixture"
HOMEPAGE="https://invalid.example/"
LICENSE="CC0-1.0"
SLOT="0"
KEYWORDS="amd64"

S="${WORKDIR}/${P}"

record_phase_identity() {
	local label=$1
	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
		"${label}" "${EUID}" "$(id -g)" "$(id -G)" \
		"${EBUILD_PHASE-}" "${PORTAGE_BUILD_USER-}" \
		"${PORTAGE_BUILD_GROUP-}" "${FEATURES-}" \
		"${GENTOO_OPT_FRAMEWORK_TARGET-}" \
		>> "${T}/phase-identity.tsv" || die
}

src_unpack() {
	record_phase_identity src_unpack
	mkdir -p -- "${S}" || die
	printf '%s\n' phase-identity > "${S}/payload" || die
}

src_compile() {
	record_phase_identity src_compile
}

pre_src_install() {
	record_phase_identity pre_src_install
}

src_install() {
	record_phase_identity src_install
	insinto /usr/share/phase2-phase-identity
	doins "${S}/payload"
}

post_src_install() {
	record_phase_identity post_src_install
}
