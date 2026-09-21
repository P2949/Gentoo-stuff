# Exhaustive System-Wide PGO and BOLT Implementation Plan for Gentoo

## Progress summary

- 2026-09-21: Framework check for the exact baseline maintenance policy correctly refused activation rather than accepting source drift: active target `c878d5eb723e7fa1523452d259085d92aeca62ed8e2561521e5bbf25072e0234`, reviewed input expected `91b4adf4f329c03b09ad6fbd696c5d6df561448d5899dd099247c4e3cc400048`, current Git `7e566f2a14cc24d0bf6671547f854229f2f3f96b`, status clean. The maintenance policy therefore remains candidate source until a fresh generation/source snapshot is produced; no active framework files were copied or edited out of band.

- 2026-09-21: The userspace-only baseline update was started after a clean pretend excluding all kernel/boot/initramfs/firmware atoms. It merged initial userspace packages, then was terminated at the bounded `Hyprutils.git` fetch stall. Preserved failures from the transaction: `media-libs/libde265-1.1.3` and `media-libs/libmediainfo-26.05` failed the exported-ABI guard (logs SHA-256 `fe68453154f2965fa774ef02a76fbd9cab6e29bab0dae4c69eeca8090465d5b1` and `c532930cbff29029be8067242b8c437d15b21415c720d372a9cac0d01af4c1ba`); `app-shells/bash-9999` failed closed because global PGO had no exact package fingerprint (die environment SHA-256 `5174a22a718b0b986a3e8e6ec5d40f5221979f610c13cdce38d2ccaa44f9cdb5`). A narrow exact-C PV maintenance policy was added and pushed in `295cafb`; the active framework still requires an authenticated generated-policy rotation before that policy can be consumed. No ABI guard or boot/kernel boundary was weakened.

- 2026-09-21: Added `classify-kernel-policy.py`, which derives kernel-policy exclusion from exact VDB forbidden-artifact evidence and reviewed ebuild lifecycle markers rather than package category prefixes. Ordinary `sys-kernel`/`sys-firmware` userspace transactions remain visible unless their exact evidence proves a forbidden mutation; the hard boot/kernel/EFI boundary remains unchanged. Regression fixture passes (commit `26db17f`).

- 2026-09-21: Re-synchronized the Gentoo repository successfully (OpenPGP verification passed); the optional `steam-overlay` fetch then stalled and was terminated at the established bounded fetch window without affecting the completed Gentoo sync. A fresh read-only `@world` assessment reports 86 candidate operations, including kernel lifecycle packages and the unresolved SPIR-V/graphics/Hyprland closure; no mixed transaction was executed. Read-only depclean reports 21 removable packages, with the optimization userspace set protecting the in-scope installed state. Root-owned evidence: `/var/lib/gentoo-optimization/reports/userspace-baseline-update-20260921.txt` (SHA-256 `4b30d0c33d1f14237a9d0569706ce5ee48385fb88c14708c7c003e888d556115`) and `userspace-baseline-depclean-20260921.txt` (SHA-256 `c1b105d21c32284c4dc9825d69a04826e376e386a7f81074952decabd74464b8`).

- 2026-09-21: Tightened consumer-workload planning so a reverse dependency alone is not `consumer-workload-ready`. Readiness now requires a bound consumer executable/service/test, deterministic recipe, expected provider artifacts, and a counter-emission proof reference; unbound edges remain pending. Regression coverage passes in `tests/optimization/test_consumer_workload_planner.py` (commit `8a40a2c`).

- 2026-09-21: Broadened package-level PGO applicability beyond the installed ELF-owner set: archives, native objects, bitcode, GPU/device artifacts, and explicit native markers remain in PGO classification even when no conventional ELF is owned. This preserves separate package PGO and artifact BOLT obligations; focused profile identity, coverage, carry-forward, dispatcher, and Python compilation validation passed.

- 2026-09-21: Added immutable `profile-carry-forward-v1` production with strict equality across CPV, repository/ebuild, ordered package.env content, build controls, compiler/ABI/target, flags, workload, training/merge evidence, and profile digest; changed identities require retraining. Phase-3 coverage now reports separate package-classification, ELF-classification, and BOLT-safety gates. ELF classification no longer excludes categories by prefix, and missing build IDs are recorded as `rebuild-required-for-bolt-capture`. Added the typed reverse-dependency generator combining Portage and DT_NEEDED records. Focused identity, coverage, and carry-forward tests pass; commit `1762abb` pushed.

- 2026-09-21: Regenerated a live inventory from `/var/db/pkg` against the current host, producing 1,305 packages, 684,559 owned paths, 80,056 owned directories, and one unresolved directory. This replaces the stale 1,292-CPV candidate boundary exposed by the Ayatana successor CPVs; no profile-use transaction was resumed.

- 2026-09-21: Corrected fingerprint materialization was run against retained `phase3-live-candidate-20260918-postsync-r1` lanes and failed closed on the first stale CPV (`dev-libs/libayatana-appindicator-0.5.94` absent from live VDB). This confirms the collector refuses stale candidate identities instead of silently producing package.env-empty or successor-bound fingerprints; the corrected inventory and lanes must be regenerated from the current live VDB before further profile publication.

- 2026-09-21: Classified the two Ayatana stale-dispatcher transactions and the libbsd repository mismatch as permanent non-authoritative identity incidents in `plans/phase3-profile-use-identity-incidents.json`; no historical profile-use result was retroactively blessed. Live Portage resolves `dev-libs/libbsd-0.12.1` from `codex-local` with ebuild SHA-256 `d7a76f596a8ec08836a64965a9b6de76310120f6ff5fdf0e4db290874adcc00d`, so the earlier `gentoo`-sourced dispatcher remains unresolved and requires corrected-generation remediation or retraining.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-cpp/sdbus-c++-2.3.1` using fingerprint `ea19f059dbb129b6588077d39b12e6c17b10c3240770978bd85e2e327978540d` and profile `/var/cache/gentoo-optimization/pgo/dev-cpp_sdbus-c++-2.3.1.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-sdbus-c++-profile-use-20260921.log` SHA-256 `98da9af69b6b04eca303864b8dcedd0228c78d92d037114f4dce9d69df312d4c`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Narrow retry of `dev-build/make-9999::local` with `-Wno-error=profile-instr-unprofiled` retained profile-use but still failed closed: one profile-out-of-date warning remained under `-Werror`, and `src/read.c` hit `-Wincompatible-pointer-types-discards-qualifiers`. No merge occurred. Report `/var/lib/gentoo-optimization/reports/phase3-make-profile-use-20260921-r1.log` SHA-256 `1bebf90bad8d1cdb61e1219d2c3d1850ed6498b1d9a165e6c86ef951d9f63b3e`.

- 2026-09-21: Authenticated profile-use rebuild for `dev-build/make-9999::local` was attempted with fingerprint `3e3cf8bbacaa41e4bf50c83a0cb9688b72e7812228419ba3a25ae7d007db8ec3` and profile `/var/cache/gentoo-optimization/pgo/dev-build_make-9999.profdata`, but failed closed during compile because the package uses `-Werror` and Clang reported an unprofiled `arscan.c` warning as an error. No package merge occurred. Report `/var/lib/gentoo-optimization/reports/phase3-make-profile-use-20260921.log` SHA-256 `0b4934c8c7f9b19043b16a5fb386ab371864148805358f46bfe6427b5dad2b94`. This is a preserved failed attempt; no optimization success or BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-build/icmake-9.03.01-r1` using fingerprint `2bc059d31d45afab95da6f57209bd0eeea0295fa03dd3169a1627900e337f311` and profile `/var/cache/gentoo-optimization/pgo/dev-build_icmake-9.03.01-r1.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-icmake-profile-use-20260921.log` SHA-256 `51117ea4dde7501af3295e3c6043917067de32ba02d0602438e8aef40055f5ac`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild for `dev-build/cmake-4.3.5` was attempted with fingerprint `47ca49b637517eb93f3abbcb5cbb2afc466e7032c9a8abe80f47c40d8479e9d8` and profile `/var/cache/gentoo-optimization/pgo/dev-build_cmake-4.3.5.profdata`, but failed closed during CMake configure: the profile-use C++ compiler probe reported no `std::unique_ptr`/C++11 support. No package merge occurred. Report `/var/lib/gentoo-optimization/reports/phase3-cmake-profile-use-20260921.log` SHA-256 `a9cbd29ce00de1d7c863fe81cb5ada8be904c36b0e423651981e2347f65814ee`. This is a preserved failed attempt; no optimization success or BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-build/bmake-20260508` using fingerprint `cb6a53e8f4327f4e0406f727d77a82cf5e43d633b873ca655852efffdb9c72d0` and profile `/var/cache/gentoo-optimization/pgo/dev-build_bmake-20260508.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-bmake-profile-use-20260921.log` SHA-256 `711d85a93e80660e8ac00bd3895b8b7597cf7dff90b7b54299fa842840def419`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-build/b2-5.5.3` using fingerprint `271a1571120bac3737e0191a0ee986622fce245907270b0dc5a704d194cdc905` and profile `/var/cache/gentoo-optimization/pgo/dev-build_b2-5.5.3.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-b2-profile-use-20260921.log` SHA-256 `5eb6590422d179ee9b9721d327726e1bceff792bbbebd463045882eda2b29db1`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/xxhash-0.8.3-r2` using fingerprint `c43b349b0320b559b81947abd685637bcbfe028573e25029597aebd8f2353b6e` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_xxhash-0.8.3-r2.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-xxhash-profile-use-20260921.log` SHA-256 `de7f8c0f8fac87bbbac37102938c017265de036256a6ca3abbc6c0f1067efd4b`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/snowball-stemmer-3.1.1` using fingerprint `d776d2142283ed85e1ca987c6214429892d9c8e03d0877461abb3de571d328de` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_snowball-stemmer-3.1.1.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-snowball-stemmer-profile-use-20260921.log` SHA-256 `69b7a49fa0c10ef7265edc647a93189ce0c5039642f609f54d23fd8979f69caf`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/popt-1.19-r1` using fingerprint `8e2b956b1bbbd9d56c88e15d3a05f7d25828550c6e23f40baa3a83402a09d6f3` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_popt-1.19-r1.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-popt-profile-use-20260921.log` SHA-256 `bb443a8b0f37d567aaaf0c65037c9d65316cbe19706b9c6bccc7fe231a5129a5`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/mpdecimal-4.0.1` using fingerprint `dde994db104128e69527371cdeac6909b452b252ab84f057df74e569938eb2d4` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_mpdecimal-4.0.1.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-mpdecimal-profile-use-20260921.log` SHA-256 `7ec477b7f0b04d9653d91743f575cc4403c72a0ecb9a3e5fa35152440bee0918`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/nspr-4.40` using fingerprint `fe8742954fa3f37961c5b80f02f429641b912f9cb660f1168a1dade078df85c7` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_nspr-4.40.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-nspr-profile-use-20260921.log` SHA-256 `2832da02e06879ab3ae1f1d5bc39038a2cded0dfd6a749f8b3d9566eb66cef52`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/nettle-3.10.2` using fingerprint `a2eedcd64aed04520746a92a20d8f35602afccb258c8d03b9f3406e2bc228dd1` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_nettle-3.10.2.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-nettle-profile-use-20260921.log` SHA-256 `0d9daa2a58bcfe8c0593f66ca0e8241d8e1a0df2c6623588727567938c9f1f22`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libverto-0.3.2-r1` using fingerprint `0c95a4b2ad911c18e74339ca8bd8c5fbee0869eaeb3bad84afe949fcb3f86a71` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libverto-0.3.2-r1.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libverto-profile-use-20260921.log` SHA-256 `cfa224313ca83c6b815228afa1d413812b53bc671ca2d537a65be48b2cdf9c1e`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libusb-1.0.30` using fingerprint `79930588e31ca53f0ba8ea26d2f1ca4e8ec21499f6ca357ad5a698ddbe3f0c5c` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libusb-1.0.30.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libusb-profile-use-20260921.log` SHA-256 `1c6c4df4794f97b927de64f901ed131a99f3a239fcbf274f8847e62e26a20659`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libutf8proc-2.11.3` using fingerprint `4cf677564384441170314be6ed914479f2bad265ea0b322dd962d0a67fd7c31c` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libutf8proc-2.11.3.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libutf8proc-profile-use-20260921.log` SHA-256 `dde52a38bbe7ae605088de861ddf028fa88683cc457e06f00e6960bbaa75bfa2`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libunistring-1.4.2` using fingerprint `59d88a0b6edaf2923e4469825fe4a2edd93fff251dbfb61fe86b3a632ea462b7` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libunistring-1.4.2.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libunistring-profile-use-20260921.log` SHA-256 `faf133fcdab2459c93f99741bc758fd1e1661e9a4e7cce795336baab25cc585d`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libtraceevent-1.9.0` using fingerprint `17a01274b29a5d1cbb398bd57f004d1a1b8387fbd75c8868b0dea34d686a5810` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libtraceevent-1.9.0.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libtraceevent-profile-use-20260921.log` SHA-256 `5fc8597ddf9ae318c69dcc9f5f47c1fe05f0123b01ecabbebc9db3d8a3bf2dd0`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libtomcrypt-1.18.2-r4` using fingerprint `1833c812f56b840b7a00b36f8f512b1756015c0e7b908a6bf3ff8da847c4eb70` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libtomcrypt-1.18.2-r4.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libtomcrypt-profile-use-20260921.log` SHA-256 `381f6942855038702523b7b3d59c5e51f2f172dd3e6735c6b1a4364fbe00bd99`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libtasn1-4.21.0` using fingerprint `96f67465f50258006f6f21cc640db0e160d3ccd081a1ba7650d53972379088cf` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libtasn1-4.21.0.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libtasn1-profile-use-20260921.log` SHA-256 `8bbafdf0b62dc06bf190a4204a574a2bb52613225680765039a0123d1d044c46`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libsigc++-3.8.0:3` using fingerprint `5aded3dac1baad63339eef9c8ce218af7db14ac092c6232a44e892203f55f01d` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libsigc++-3.8.0.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libsigc++-3.8.0-profile-use-20260921.log` SHA-256 `d48589ddced781eb6a6127ad9a5ec3504b0a9d27251e8ec63c1e4d556700c4e8`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libsigc++-2.12.1:2` using fingerprint `153faf0ee34411034ec1fff0c76dfbd5da543d64fd48ef6df060e79bea01c2fd` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libsigc++-2.12.1.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libsigc++-2.12.1-profile-use-20260921.log` SHA-256 `a9b3d67ace4a8ff13e2adbc139d2b236f4f8ec1ba2c67570a26e10f37021e8ae`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libpfm-9999` using fingerprint `427a74ca354ca911ea9bbb13f667438c4bad7c811f3b95eb2595fc09181f7695` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libpfm-9999.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libpfm-profile-use-20260921.log` SHA-256 `60718c594e5848adf04aadef9a26d55ff317269ee6acda22231c578eca30fdf8`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libpcre2-10.48` using fingerprint `8e513cd60f65055b035466760d4551540e2be6baeee98b464d11089e6ef79007` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libpcre2-10.48.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libpcre2-profile-use-20260921.log` SHA-256 `3e98ac02ea40b894c07de12351190fd8bc02faf8922d3cafbca0a8b04bc2cb75`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libpcre-8.45-r4` using fingerprint `9cfab29ffc82528b24a08feb4adbe602d6d763f65944f558381417fcb34ba0d3` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libpcre-8.45-r4.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libpcre-profile-use-20260921.log` SHA-256 `fb7fdd830598dc8f8a7f803c3afeeed07425e79396e4a120d76a6f7e6adeaeee`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libksba-1.8.1` using fingerprint `cebf8e9e8fc24c7c860cdefa2fc32cfc361f2239310481a618105dcdf0a7fff7` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libksba-1.8.1.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libksba-profile-use-20260921.log` SHA-256 `e582e42c9f15d94f161b076c79dee17e76e425e88fab5a7fa8dfb48cdf4dbcf8`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libgpg-error-1.61` using fingerprint `eaa3611228466dc98e1af95d66fffc18a892a446e9c2c63f908e7db97f56fc51` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libgpg-error-1.61.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libgpg-error-profile-use-20260921.log` SHA-256 `c89ae08ec47df265b5361d39f860cb09672f206c56527255cbbcb0654353472d`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild of `dev-libs/libgcrypt-1.12.4` failed during the 32-bit compile before install-QA because libgcrypt FIPS mode requires thread-local storage (`fips.c:82`) and the profile-use multilib compile lacked the required TLS support; no merge occurred. Report `/var/lib/gentoo-optimization/reports/phase3-libgcrypt-profile-use-20260921.log` SHA-256 `f9fa78820b248e21716ca802edca521f89a516f232633050d024e59b973452c2`. This failed attempt is preserved; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libev-4.33` using fingerprint `d82b43f3e2483f1221bda9ed9566ecc637ade425462a62fe8caadda1fc3f5849` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libev-4.33.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libev-profile-use-20260921.log` SHA-256 `563f6b7fcc30693fc2f8c01fbc5bf341eefcee3a37823e97fad1592ecede315b`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libedit-20240808.3.1` using fingerprint `9753cf3ba7b5dec1bd82bd60c0f0cc60efb14da2899a641b3729ddc7dc691afb` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libedit-20240808.3.1.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libedit-profile-use-20260921.log` SHA-256 `3e670f5abfc8abfc21f57eeef5c89d867396bf49e48eb5409926c623952fa443`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libdbusmenu-16.04.0-r4` using fingerprint `2477178d1c907a32614e7bdf9f8b25365e1fee49e7921b47ddcde4de1643d185` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libdbusmenu-16.04.0-r4.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libdbusmenu-profile-use-20260921.log` has SHA-256 `f745d25667da185a78bae64353a4dc0b4645c908e22b7ac2c568e0eb8b17ae82`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libbsd-0.12.1`; live Portage selected repository `codex-local` while the dispatcher generation names Gentoo, so repository provenance is recorded for follow-up authority review. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; fingerprint `0844a2aef9dce25cddbeadf43712a562311793ceda3ab26d7db145b22bbe4a69`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_libbsd-0.12.1.profdata`, report `/var/lib/gentoo-optimization/reports/phase3-libbsd-profile-use-20260921.log` SHA-256 `9a1d8efa3054c0af56c23e53db09ffbf509f69524b0ac1c107f91a9f31ba8074`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Dispatcher identity drift observed for `dev-libs/libayatana-indicator`: dispatcher/profile identity names `0.9.4`, but live Portage resolved and merged `0.9.5` as an upgrade. The transaction passed install-QA/ABI, but this attempt is not authoritative optimization evidence for either exact CPV and requires corrected generation/dispatcher regeneration. Report `/var/lib/gentoo-optimization/reports/phase3-libayatana-indicator-profile-use-20260921.log` SHA-256 `f8b1c4d77284bd76f11e21b6a674506d20065479eddbdd2989ad7f0df822b4b4`; fingerprint observed `c3d3c7202690a8367bf42d44e6666b73a9aefb220fb6cf28e9312ec90612b903`.

- 2026-09-21: Dispatcher identity drift observed for `dev-libs/libayatana-appindicator`: dispatcher/profile identity names `0.5.94`, but live Portage resolved and merged `0.6.0` as an upgrade. The transaction passed install-QA/ABI, but this attempt is not authoritative optimization evidence for either exact CPV and requires corrected generation/dispatcher regeneration before acceptance. Report `/var/lib/gentoo-optimization/reports/phase3-libayatana-appindicator-profile-use-20260921.log` SHA-256 `cbe4952015a90f97fe287d0a736481eb5f52153a881e7508f4bce1542e24ed13`; fingerprint observed `e8a1ec8a8877a075d97fef0d59611726938253be9c12bf2dd531a42970233500`.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/libaio-9999` using fingerprint `85e342fc4a02effbb888bcb7a2b5e2c271cbcfd018b1ff6acb6da851b0f7a945` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libaio-9999.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-libaio-profile-use-20260921.log` has SHA-256 `2ff7eed7f16acacda3cbcc1cc808b412373413a71e8548ebba54a97adaf1cb99`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/json-glib-1.10.8` using fingerprint `cf69a5af9f828dd8d02b4be0a653aba8165513519409b6c3b62a6c515ad614fc` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_json-glib-1.10.8.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-json-glib-profile-use-20260921.log` has SHA-256 `63b2a9187dac39601eb3c300be84e9b81c314ccacfaec28e3617596bc96644b6`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/hyphen-2.8.8-r2` using fingerprint `61250aa4885f9f50ede956cac79eabac9429a89f4f3d14ceeccb6151309febe2` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_hyphen-2.8.8-r2.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-hyphen-profile-use-20260921.log` has SHA-256 `0b8b4e31c2d3da9c6955f44fcf51117f0b744cf9ac2d527ccf003032ab9708fc`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/hidapi-0.15.0` using fingerprint `815f062d8386529b5f1ac23182d4a13f948b356c5199d9e06976b391082d5422` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_hidapi-0.15.0.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-hidapi-profile-use-20260921.log` has SHA-256 `4bd516a446986ca15b29888923028c03203a8e824a062263bc175673b5681302`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/fribidi-1.0.16` using fingerprint `72740f1dab0111b3f75a5aeba366535c5a5a7ddce631e4dfe520099ec7e31dd` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_fribidi-1.0.16.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-fribidi-profile-use-20260921.log` has SHA-256 `712360fd0cc8ae6467d2845a40bfd8f92d01c859b7f96a8edf10b6218f4f5c45`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild of `dev-libs/flatbuffers-25.12.19` reached install-QA but was correctly rejected by the fail-closed exported-ABI guard: `libflatbuffers.so.25.12.19` changed exported ABI from 212 to 207 and lost five symbols (`data`, `empty_blob`, `empty_fixed_vector`, `empty_string`, `empty_vector`). No package merge occurred. Report `/var/lib/gentoo-optimization/reports/phase3-flatbuffers-profile-use-20260921.log` has SHA-256 `faa5735d5b31691b9cb7dfb4abeadb5db27abec1ffa00503697af1971634481a`. This is preserved as a terminal failed attempt; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/expat-2.8.4` using fingerprint `8b28f8d26414e5707a3f42bc4c3f4835ea5b6dfa5b2235e866f45bba2754fb45` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_expat-2.8.4.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-expat-profile-use-20260921.log` has SHA-256 `2a3b564e7588c4b74b1245bd63d8409bcfcdec1f95fd133c0c8662813fa117e3`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/ell-9999` using fingerprint `1b6ba30bd169c8a0177c25de138c9e20e0a1d41e1c4f1b86ed994943ddb65804` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_ell-9999.profdata`. The package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-ell-profile-use-20260921.log` has SHA-256 `ceeb9ae75eadd72bfa0208d9947a0346cad91c33f24059214e4e71cc950b5159`. This is `clang-ir-use` evidence only; no BOLT claim.

- 2026-09-21: Authenticated profile-use rebuild completed for `dev-libs/elfutils-0.196` using fingerprint `b7bee581aa08c4573afbb473eb86f10226dae4c5eb71e0b58eec94fdaab9a5a9` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_elfutils-0.196.profdata`. The 32/64-bit package compiled, installed, and passed install-QA/ABI validation; report `/var/lib/gentoo-optimization/reports/phase3-elfutils-profile-use-20260921.log` has SHA-256 `e7b6f480af07cb870324b4eba6b32f344a601ecdf61d8fa1d9fa74eaa755db6a`. This is `clang-ir-use` evidence only; no BOLT claim.
- **Authenticated `dev-libs/elfutils-0.196` profile-use rebuild (2026-09-21):** completed 32-bit and 64-bit ABI builds under `clang-ir-use` with fingerprint `b7bee581aa08c4573afbb473eb86f10226dae4c5eb71e0b58eec94fdaab9a5a9`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_elfutils-0.196.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-elfutils-profile-use-20260921.log` has SHA-256 `e7b6f480af07cb870324b4eba6b32f344a601ecdf61d8fa1d9fa74eaa755db6a`.
- **Authenticated `dev-libs/double-conversion-3.4.0` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `2517eeee27dd9bc9f25c549f4c400e5b7c0b2b8463ab4377ff3a805a89da31d6`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_double-conversion-3.4.0.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-double-conversion-profile-use-20260921.log` has SHA-256 `af1428ad565aee79a925eac1a67072937ebb8aef6c0237d42c7f080eca2098bf`.
- **Authenticated `dev-libs/dbus-glib-0.114` profile-use rebuild (2026-09-21):** completed 32-bit and 64-bit ABI builds under `clang-ir-use` with fingerprint `162221d8a23d739e82a6baab1a4475a09f754e1866d67e883c2d9bc9371ee44b`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_dbus-glib-0.114.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-dbus-glib-profile-use-20260921.log` has SHA-256 `680f47e88c0b54b86577a22707ff8fe28e7693dddb0776300fe80cd519ed59bf`.
- **Authenticated `dev-libs/date-3.0.3` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `dec7bd1a9bd7b0334d240d92009c03ee5c6f637b64f5a42d8264f38ef6e3f018`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_date-3.0.3.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-date-profile-use-20260921.log` has SHA-256 `e722076d8d9c5fb1ee04f5492b326eed80c3d47bc28b8c886454efcf88fda27d`.
- **Authenticated `dev-libs/boehm-gc-8.2.12` profile-use rebuild (2026-09-21):** completed 32-bit and 64-bit ABI builds under `clang-ir-use` with fingerprint `b40cfb6fa932309a415979db3f2c2c0e9cf963d645ca589fd0989ccaf73b4a09`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_boehm-gc-8.2.12.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-boehm-gc-profile-use-20260921.log` has SHA-256 `37acc966cb3679f0b4a8a8c8e2b08097ff851641175775c059a93c25d2cffb24`.
- **Authenticated `dev-libs/ayatana-ido-0.10.4-r1` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `8b1df2811a1b587dfe25350f4ac761549f4e713adecc51b20e9db9a40be6c889`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_ayatana-ido-0.10.4-r1.profdata`, install-QA/ABI guard passed, and Portage completed the merge. Existing compiler QA warnings were preserved in the report. The preserved report `/var/lib/gentoo-optimization/reports/phase3-ayatana-ido-profile-use-20260921.log` has SHA-256 `86c6f861644411b599174ce24790873057bb453e31d045811d41753b71a40519`.
- **Authenticated `dev-libs/appstream-glib-0.8.3` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `dda5a97d289a0afd0160b510151107bc90c117fd7ed191ba30fe358dabf8122c`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_appstream-glib-0.8.3.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-appstream-glib-profile-use-20260921.log` has SHA-256 `db582f9bcf68f20dadb3810d87885776ad611d444e3e47b5ab60d6527d809aa7`.
- **Authenticated `dev-libs/appstream-1.0.6` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `e10eb8c4668ac0858cbaabf72998fec6e1f52025244b494fe4f50fe739aa0628`, profile `/var/cache/gentoo-optimization/pgo/dev-libs_appstream-1.0.6.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-appstream-profile-use-20260921.log` has SHA-256 `dd6bf7ff80887b12d27da9149fc421ed4bc44c44aae0b574b11a018c001dbd9e`.
- **Authenticated `dev-lang/vala-0.56.19` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `4bf002dcd692d1695ae0b39be6cc1bf115442bf5b156d5ae73bd708c05b86216`, profile `/var/cache/gentoo-optimization/pgo/dev-lang_vala-0.56.19.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-vala-profile-use-20260921.log` has SHA-256 `c5c9309cb86d260de4e257af609b614e387cc28e5619dbc1782e5814d0d5179d`.
- **Authenticated `dev-lang/tk-8.6.17` profile-use rebuild (2026-09-21):** completed 32-bit and 64-bit ABI builds under `clang-ir-use` with fingerprint `43cb87d0c3a9e964bb810d301e15b26a730747478db565dc37bef9531710fee0`, profile `/var/cache/gentoo-optimization/pgo/dev-lang_tk-8.6.17.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-tk-profile-use-20260921.log` has SHA-256 `d8105c0117407c5cc476b50ba7dc15cd8aad82ed062a3307b3ec7bdca99cc21d`.
- **Authenticated `dev-lang/swig-4.4.1` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `5ff7af9aeddf623bb9691ec9a2e086adcc34e4695b9bd914fa833084ce633ae1`, profile `/var/cache/gentoo-optimization/pgo/dev-lang_swig-4.4.1.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-swig-profile-use-20260921.log` has SHA-256 `b8eda9671bdd7442711c1c3df1143ed6c9d43e4aa8b653dbd9dd0f02dcfb7708`.
- **Authenticated `dev-lang/sassc-3.6.2` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `315eba06bb9b36f1b3fffda73e5947de6dd3867d466c5c6565ad85e98b0dfedd`, profile `/var/cache/gentoo-optimization/pgo/dev-lang_sassc-3.6.2.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-sassc-profile-use-20260921.log` has SHA-256 `6acfbc0dd57c10859c87eec48f0a279eb6f4f6f3418bdf706dacb18b13ceb22a`.
- **Authenticated `dev-lang/orc-0.4.42` profile-use rebuild (2026-09-21):** completed 32-bit and 64-bit ABI builds under `clang-ir-use` with fingerprint `c3c913931fc09828d50633ecf54aa47cafe001198592fe03f47c5fcf93cf6898`, profile `/var/cache/gentoo-optimization/pgo/dev-lang_orc-0.4.42.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-orc-profile-use-20260921.log` has SHA-256 `6105a4716b3c29c86ca99210f1874e34eaca765ea4b1d9b0a29270b027d202a4`.
- **Authenticated `dev-lang/duktape-2.7.0-r3` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `3c53822a52a05c75f78264e13bb29358c4c92431ca601d488d542627b3fdfaa9`, profile `/var/cache/gentoo-optimization/pgo/dev-lang_duktape-2.7.0-r3.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-duktape-profile-use-20260921.log` has SHA-256 `80ec72b7ca46264ee748cdc70632737576c771ae29dcab0757e6317bb8607373`.
- **Authenticated `dev-lang/deno-bin-2.9.6` profile-use deployment (2026-09-21):** binary package installed successfully through the authenticated `clang-ir-use` dispatcher with fingerprint `9a84c582ffa40fb2f198687d1852dd226a7c193986c40582bcd1d6f019884532`, install-QA/ABI guard passed, and Portage reported `>>> dev-lang/deno-bin-2.9.6 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-deno-bin-profile-use-20260921.log` has SHA-256 `700e2ad82e49c30d11023e69eef4faa2be035dde7535324b703aad6d60252eee`.
- **Authenticated `dev-embedded/libdisasm-0.23-r1` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `563feb9f783a04e00c12e6ccaca08ac09a887532c8959dd5547d995c48ce5247`, profile `/var/cache/gentoo-optimization/pgo/dev-embedded_libdisasm-0.23-r1.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-libdisasm-profile-use-20260921.log` has SHA-256 `38b6821af176d2617b71ec47a83d5d78c6ae94c9f3f8f47f29d4411c685f3dfb`.
- **Authenticated `dev-db/sqlite-3.53.4` profile-use rebuild (2026-09-21):** completed both `abi_x86_32` and `abi_x86_64` builds under `clang-ir-use` with fingerprint `9ca39f67cc138feddd07e9ef3362d020633bb6b82fada70d048049a440c1eadb`, profile `/var/cache/gentoo-optimization/pgo/dev-db_sqlite-3.53.4.profdata`, install-QA/ABI guard passed, and Portage reported `>>> dev-db/sqlite-3.53.4 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-sqlite-profile-use-20260921.log` has SHA-256 `dfbaefd82d81de7d159f6e0e14e2d8510c35aebb7c81ac5d48ff51fee2c4b1ab`.
- **Authenticated `dev-cpp/sdbus-c++-2.3.1` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `ea19f059dbb129b6588077d39b12e6c17b10c3240770978bd85e2e327978540d`, profile `/var/cache/gentoo-optimization/pgo/dev-cpp_sdbus-c++-2.3.1.profdata`, install-QA/ABI guard passed, and Portage completed the merge. The preserved report `/var/lib/gentoo-optimization/reports/phase3-sdbus-profile-use-20260921.log` has SHA-256 `8e71d072a6b9a6258e9c4e47fdee63d5d78e0f1957383c9b70f34ba62adf9097`.
- **Authenticated `dev-build/make-9999` profile-use attempt (2026-09-21):** source fetch/bootstrap/configuration completed, but compilation failed because Clang treated missing profile data for `loadapi.c` as an error (`error: no profile data available for file "loadapi.c"`); no package mutation occurred. The preserved report `/var/lib/gentoo-optimization/reports/phase3-make-profile-use-20260921.log` has SHA-256 `8241d8d066fae85e1e51c000b5c62234f31bde708356ffdd30b5f59b687a2a1e`.
- **Authenticated `dev-build/icmake-9.03.01-r1` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `2bc059d31d45afab95da6f57209bd0eeea0295fa03dd3169a1627900e337f311`, profile `/var/cache/gentoo-optimization/pgo/dev-build_icmake-9.03.01-r1.profdata`, install-QA/ABI guard passed, and Portage reported `>>> dev-build/icmake-9.03.01-r1 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-icmake-profile-use-20260921.log` has SHA-256 `d3eedf5d638cc311575197d23fa33b829ba29707563792a5b72bdc54c34331d9`.
- **Authenticated `dev-build/cmake-4.3.5` profile-use attempt (2026-09-21):** configuration failed before compilation because the C++ compiler feature probe reported no usable C++11 `std::unique_ptr` support under the current profile-use/toolchain flags; no package mutation occurred. The preserved report `/var/lib/gentoo-optimization/reports/phase3-cmake-profile-use-20260921.log` has SHA-256 `53333840ff69c74103f7c1147646a16261d3cd3b20e64f8fff06d4e4729617e8`.
- **Authenticated `dev-build/bmake-20260508` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `cb6a53e8f4327f4e0406f727d77a82cf5e43d633b873ca655852efffdb9c72d0`, profile `/var/cache/gentoo-optimization/pgo/dev-build_bmake-20260508.profdata`, install-QA/ABI guard passed, and Portage reported `>>> dev-build/bmake-20260508 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-bmake-profile-use-20260921.log` has SHA-256 `a1810b55fdeab358fecff69a7e9081a07c713a0cffd1c662ff95dfc568d287af`.
- **Authenticated `dev-build/b2-5.5.3` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `271a1571120bac3737e0191a0ee986622fce245907270b0dc5a704d194cdc905`, profile `/var/cache/gentoo-optimization/pgo/dev-build_b2-5.5.3.profdata`, install-QA/ABI guard passed, and Portage reported `>>> dev-build/b2-5.5.3 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-b2-profile-use-20260921.log` has SHA-256 `25eaaabb148a6f5591c6871152c546b0fcd96495932fd8da68bda64340f9a2fb`.
- **Authenticated `dev-util/desktop-file-utils-0.28-r1` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `389b4af6818997b27763aa8cd4d27c8cabc39ab85e4e19e1e8c0fa272981b914`, profile `/var/cache/gentoo-optimization/pgo/desktop-file-utils.profdata`, install-QA/ABI guard passed, and Portage reported `>>> dev-util/desktop-file-utils-0.28-r1 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-desktop-file-utils-profile-use-20260921.log` has SHA-256 `b59d7502bb81edf1f5e2fe01cedc8189133c2a957495f40226a6fc0dc4011eca`.
- **Authenticated `app-text/xmlto-0.0.28-r11` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `26951dbb3c184140dbcba372dd62f5c30fcdfa22d977635322dc37846c849992`, profile `/var/cache/gentoo-optimization/pgo/app-text_xmlto-0.0.28-r11.profdata`, install-QA/ABI guard passed, and Portage reported `>>> app-text/xmlto-0.0.28-r11 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-xmlto-profile-use-20260921.log` has SHA-256 `b2b7f68723f9fdd4420116508a6e1a97d524726d168d47bf776a4e633d794822`.
- **Authenticated `app-text/scdoc-9999` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `4ad9bdf34b8ff8db28b26d6115f483bf57768429e1fc985a5bccdccda8a6cbb7`, profile `/var/cache/gentoo-optimization/pgo/app-text_scdoc-9999.profdata`, install-QA/ABI guard passed, and Portage reported `>>> app-text/scdoc-9999 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-scdoc-profile-use-20260921.log` has SHA-256 `72d349082cf8848723485045b56c9d6f465db6d2ee60c8257c3d3c5e8c6181b6`.
- **Authenticated `app-text/libpaper-2.1.3` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `977ef7a3d7e4a5e5a88fd9efbc685f3f9e1b80653aa7f61e5661b412191aa164`, profile `/var/cache/gentoo-optimization/pgo/app-text_libpaper-2.1.3.profdata`, install-QA/ABI guard passed, and Portage reported `>>> app-text/libpaper-2.1.3 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-libpaper-profile-use-20260921.log` has SHA-256 `8e5e003deea9154f194f2eff1aa0b83448c187a6e9a5a33f9c4da15ff99676d1`.
- **Authenticated `app-text/hunspell-1.7.2-r1` profile-use attempt (2026-09-21):** compilation and staging completed under `clang-ir-use`, but install-QA rejected `usr/lib64/libhunspell-1.7.so.0.0.1` and its symlinks for exported-ABI loss (old=104, new=103; missing `std::__1::basic_stringstream` destructor); no package merge occurred. The preserved report `/var/lib/gentoo-optimization/reports/phase3-hunspell-profile-use-20260921.log` has SHA-256 `8de21e11c15fc84beea94760bf4ba2650eb47e9301bffa8ccbbcfaa1d300f0df`.
- **Authenticated `app-text/gspell-1.14.4` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `02fda35bda68db902018b4eb552742e35636eb9e1324ea11fcc1c0c98ef05d26`, profile `/var/cache/gentoo-optimization/pgo/app-text_gspell-1.14.4.profdata`, install-QA/ABI guard passed, and Portage reported `>>> app-text/gspell-1.14.4 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-gspell-profile-use-20260921.log` has SHA-256 `08283ae9da281fc357f8c9eb55f6d2a299fee57f4f19fb3678e0ec4bfa5b9feb`.
- **Authenticated `app-text/mandoc-1.14.6-r1` profile-use rebuild (2026-09-21):** merged successfully under `clang-ir-use` with fingerprint `7e6e49c1ab0575ca31865640a7ba8c2ca14ea7de12368a54d6d36e8b14d789b9`, profile `/var/cache/gentoo-optimization/pgo/app-text_mandoc-1.14.6-r1.profdata`, install-QA/ABI guard passed, and Portage reported `>>> app-text/mandoc-1.14.6-r1 merged.` The preserved report `/var/lib/gentoo-optimization/reports/phase3-mandoc-profile-use-20260921.log` has SHA-256 `4227405425819d1b34b54589c2d02afab6aa220ada018029bca34453e0e35ee8`.
- **Authenticated `app-text/lowdown-3.1.1` profile-use attempt (2026-09-21):** compile failed before merge because the package configuration generated `compats.c` with `No getprogname available` (line 3272); no package mutation occurred. The preserved report `/var/lib/gentoo-optimization/reports/phase3-lowdown-profile-use-20260921.log` has SHA-256 `e2089829a0385330530b3e16a2c4fe1bc0c575bef17af2c5c2f6513aa0e753ca`.
- **Authenticated `app-text/enchant-2.8.16` profile-use attempt (2026-09-21):** build completed under `clang-ir-use` with the active fingerprint/profile, but install-QA rejected the replacement `usr/lib64/enchant-2/enchant_hunspell.so` for exported-ABI loss (old=14, new=13; one missing libc++ symbol); no package merge occurred. The preserved report `/var/lib/gentoo-optimization/reports/phase3-enchant-profile-use-20260921.log` has SHA-256 `1f284cbfd1b3b6b8550c74a8e23ccb614f0921e8a382001b800df602f7ba6641`.
- **Authenticated dos2unix profile-use rebuild (2026-09-21):** exact `app-text/dos2unix-7.5.6` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `28fcf56760bd4e7d56e7c2e2c1e59fa97b7fc0c2dbb25deab16c67a45b36f58c`, and profile `/var/cache/gentoo-optimization/pgo/app-text_dos2unix-7.5.6.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-dos2unix-profile-use-20260921.log`; its SHA-256 is `0c0c5993f80f29afecc5ccc3c4eba215f9baabf6bcc3a52511d845ec193fe576.
- **Authenticated quoter profile-use rebuild (2026-09-21):** exact `app-shells/quoter-4.2` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `47281818d2866c000729d5840e990e95dc8e079b8d7ca4d32ffafcc611d73792`, and profile `/var/cache/gentoo-optimization/pgo/app-shells_quoter-4.2.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-quoter-profile-use-20260921.log`; its SHA-256 is `4b531bfdbbb09ef99327152ddd108892c4de79d0c5c708831496db93bac45a83.
- **Authenticated bash profile-use rebuild (2026-09-21):** after the initial `pgo*` flag conflict was preserved, exact `app-shells/bash-9999` was rebuilt successfully from the same root-owned dispatcher with `USE=-pgo`, `clang-ir-use` active, fingerprint `d9931a71be7d1ccc9713ef0e1f3e692d043a7b683f77a64f967241baea2933a2`, and profile `/var/cache/gentoo-optimization/pgo/app-shells_bash-9999.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative retry log is `/var/lib/gentoo-optimization/reports/phase3-bash-profile-use-retry-20260921.log`; its SHA-256 is `692ff53bb4c086ea9cf8decc08a7a4ae7ea49021edf82afaa2d4fbb0b708d909.
- **Bash profile-use attempt (2026-09-21):** exact `app-shells/bash-9999` entered the authenticated dispatcher with fingerprint `d9931a71be7d1ccc9713ef0e1f3e692d043a7b683f77a64f967241baea2933a2` and profile `/var/cache/gentoo-optimization/pgo/app-shells_bash-9999.profdata`, but the ebuild's forced `pgo*` USE flag added profile-generation flags and Clang rejected the combination with `-fprofile-use`. The failed attempt is preserved in `/var/lib/gentoo-optimization/reports/phase3-bash-profile-use-20260921.log` with SHA-256 `cc9952f1edf5abcaac98d118b8fc0d1bb41bc3d0e3fa049f776ded52c30295fe`; no package mutation was admitted. A retry with the package's optional pgo feature disabled is required to test the authenticated profile-use path.
- **Authenticated zsh profile-use rebuild (2026-09-21):** exact `app-shells/zsh-9999` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `04a9e29c339ac8aa945b4724d9c4684afe6fd7cb577d748d15cabb4d729392e1`, and profile `/var/cache/gentoo-optimization/pgo/app-shells_zsh-9999.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-zsh-profile-use-20260921.log`; its SHA-256 is `eec0563aa6e4245da40ea1f22eb1557c7a38e5e0abc0ce5654f39008f4e7bb9a.
- **Authenticated dash profile-use rebuild (2026-09-21):** exact `app-shells/dash-9999` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `c74ee74a6cda2dba478b091b340d0e9508eaef77cb472ef6b663970563fab524`, and profile `/var/cache/gentoo-optimization/pgo/app-shells_dash-9999.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-dash-profile-use-20260921.log`; its SHA-256 is `a47531e42fa883456d949b096758c22fe9c893007a7e8be39d219e983156274e.
- **Authenticated portage-utils profile-use rebuild (2026-09-21):** exact `app-portage/portage-utils-9999` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `70a986f338218074c0f5b52d75074cb98557b2f7603b6940b9035d7c0f7ab35e`, and profile `/var/cache/gentoo-optimization/pgo/app-portage_portage-utils-9999.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-portage-utils-profile-use-20260921.log`; its SHA-256 is `619d8241171adc25d10c448281484e4d90fc558322887a60b8f09f80affbdb7b.
- **Authenticated eix profile-use rebuild (2026-09-21):** exact `app-portage/eix-0.36.9` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `97848e1b8ca4cca3d8981ed483fdb96f9a91b7771d7fa5790961ab231acbf9ac`, and profile `/var/cache/gentoo-optimization/pgo/app-portage_eix-0.36.9.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-eix-profile-use-20260921.log`; its SHA-256 is `77d01ae2dbe70d2b25f6356a0a6952427038c06a2ab031af52957d1b2d287ede.
- **Authenticated cpuid2cpuflags profile-use rebuild (2026-09-21):** exact `app-portage/cpuid2cpuflags-18` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `69350d42c3f20e2859018e5403a28fd7015225e8e283534b58ee98debd890553`, and profile `/var/cache/gentoo-optimization/pgo/app-portage_cpuid2cpuflags-18-v2.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-cpuid2cpuflags-profile-use-20260921.log`; its SHA-256 is `d5e7c394173de54bd6171c681a023e533d089625782b47a6652641485ba5f281.
- **Authenticated jq profile-use rebuild (2026-09-21):** exact `app-misc/jq-1.8.2` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `a05cfac147687f957c3bedc6de3dc1ed89c1ea0a595ca9219fadc3f3d972d007`, and profile `/var/cache/gentoo-optimization/pgo/app-misc_jq-1.8.2.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-jq-profile-use-20260921.log`; its SHA-256 is `5ee449cf68e704eefd1e797a51ba18340fb140c27375ed8fdc805e5a29367e52.
- **Authenticated uchardet profile-use rebuild (2026-09-21):** exact `app-i18n/uchardet-0.0.8` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `859fc6b7dc71329a53da0f31aec3094bd53c3eacf2ddaaed137333125f8f7e06`, and profile `/var/cache/gentoo-optimization/pgo/app-i18n_uchardet-0.0.8.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-uchardet-profile-use-20260921.log`; its SHA-256 is `799c771e179fb62f4934a0ad80a3853afcf12292c63984363ed05fef2ab27a85.
- **Authenticated xxhash profile-use rebuild (2026-09-21):** exact `dev-libs/xxhash-0.8.3-r2` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `c43b349b0320b559b81947abd685637bcbfe028573e25029597aebd8f2353b6e`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_xxhash-0.8.3-r2.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-xxhash-profile-use-20260921.log`; its SHA-256 is `03d680b4cd204b101e9d2de3d5e5e683a4a1b6cb549d78b88323f5bc6450bbc6.
- **Authenticated snowball-stemmer profile-use rebuild (2026-09-21):** exact `dev-libs/snowball-stemmer-3.1.1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `d776d2142283ed85e1ca987c6214429892d9c8e03d0877461abb3de571d328de`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_snowball-stemmer-3.1.1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-snowball-profile-use-20260921.log`; its SHA-256 is `dc6429739f02994d0b00d54507cc5bafb0fe479136031240f64acc704d0b722a.
- **Authenticated mpdecimal profile-use rebuild (2026-09-21):** exact `dev-libs/mpdecimal-4.0.1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `dde994db104128e69527371cdeac6909b452b252ab84f057df74e569938eb2d4`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_mpdecimal-4.0.1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-mpdecimal-profile-use-20260921.log`; its SHA-256 is `407fb8b0dfe13ac828454e157720c4d031ba9c78bf4b00b7419d2924bfabefe2.
- **Authenticated libaio profile-use rebuild (2026-09-21):** exact `dev-libs/libaio-9999` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `85e342fc4a02effbb888bcb7a2b5e2c271cbcfd018b1ff6acb6da851b0f7a945`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libaio-9999.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libaio-profile-use-20260921.log`; its SHA-256 is `84b584d91630c5e7a2522e72dc129d0c3648af9111c9f3719d00165e962f4279.
- **Authenticated libsigc++-3 profile-use rebuild (2026-09-21):** exact `dev-libs/libsigc++-3.8.0` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `5aded3dac1baad63339eef9c8ce218af7db14ac092c6232a44e892203f55f01d`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libsigc++-3.8.0.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libsigc3-profile-use-20260921.log`; its SHA-256 is `c59c490eab9bc734e1c749a01a3b7ec30640d4fd42a67656a27986d2c9c08341.
- **Authenticated libsigc++-2 profile-use rebuild (2026-09-21):** exact `dev-libs/libsigc++-2.12.1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `153faf0ee34411034ec1fff0c76dfbd5da543d64fd48ef6df060e79bea01c2fd`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libsigc++-2.12.1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libsigc2-profile-use-20260921.log`; its SHA-256 is `252b2d4aa13958a377dedd882625f6acf3f96d560755af8f1e6396ebaaff3675.
- **Authenticated nspr profile-use rebuild (2026-09-21):** exact `dev-libs/nspr-4.40` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `fe8742954fa3f37961c5b80f02f429641b912f9cb660f1168a1dade078df85c7`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_nspr-4.40.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-nspr-profile-use-20260921.log`; its SHA-256 is `455baf340d094d4753d2ec6dbfb09bfd3e8587662acd1ba8e381d734ed9b60e2.
- **Authenticated elfutils profile-use rebuild (2026-09-21):** exact `dev-libs/elfutils-0.196` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `b7bee581aa08c4573afbb473eb86f10226dae4c5eb71e0b58eec94fdaab9a5a9`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_elfutils-0.196.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-elfutils-profile-use-20260921.log`; its SHA-256 is `badc60f7b0fd8e3741f8be90b0f13b742a28bbde776204bee550dc2409f40ff6.
- **Flatbuffers profile-use attempt (2026-09-21):** exact `dev-libs/flatbuffers-25.12.19` entered authenticated `clang-ir-use` with fingerprint `d5537f6b546c851c7e78f445940a1291f20525649739ce9f4cd5d4cac5afef83` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_flatbuffers-25.12.19.profdata`, but the fail-closed ABI guard rejected the replacement DSO for exported ABI loss (old 212 symbols, new 207; five flexbuffers/data symbols missing). The failed transaction is preserved in `/var/lib/gentoo-optimization/reports/phase3-flatbuffers-profile-use-20260921.log` with SHA-256 `9d53a314fcdb6c2d5feda09de3ffce7ec80205fa28d3fd3603ace69150ad46fb`; no package mutation was admitted.
- **Authenticated json-glib profile-use rebuild (2026-09-21):** exact `dev-libs/json-glib-1.10.8` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `cf69a5af9f828dd8d02b4be0a653aba8165513519409b6c3b62a6c515ad614fc`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_json-glib-1.10.8.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-json-glib-profile-use-20260921.log`; its SHA-256 is `77db9aea643bb7f1fce706a5428d1f369818c1e5a762943fd3cce89c92d70976.
- **Authenticated expat profile-use rebuild (2026-09-21):** exact `dev-libs/expat-2.8.4` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `8b28f8d26414e5707a3f42bc4c3f4835ea5b6dfa5b2235e866f45bba2754fb45`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_expat-2.8.4.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-expat-profile-use-20260921.log`; its SHA-256 is `375e65898b5a6af308e15ebfe1fe39bb5a41301989cbd15cf86fe4aa882e4529.
- **Authenticated libbsd profile-use rebuild (2026-09-21):** exact `dev-libs/libbsd-0.12.1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `0844a2aef9dce25cddbeadf43712a562311793ceda3ab26d7db145b22bbe4a69`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libbsd-0.12.1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libbsd-profile-use-20260921.log`; its SHA-256 is `21648f44681192a01fd2b0240a43e67bcc21f7797cf8c5172d333d478cb76b8b.
- **Authenticated nettle profile-use rebuild (2026-09-21):** exact `dev-libs/nettle-3.10.2` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `a2eedcd64aed04520746a92a20d8f35602afccb258c8d03b9f3406e2bc228dd1`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_nettle-3.10.2.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-nettle-profile-use-20260921.log`; its SHA-256 is `6c82fd7ebc89080ad63a89010fce61ccfa88de9c61259d67be92804107787bd6.
- **Authenticated libverto profile-use rebuild (2026-09-21):** exact `dev-libs/libverto-0.3.2-r1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `0c95a4b2ad911c18e74339ca8bd8c5fbee0869eaeb3bad84afe949fcb3f86a71`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libverto-0.3.2-r1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libverto-profile-use-20260921.log`; its SHA-256 is `5a30263eae61623a4612efe80be675878c592d68198f09d189aa73388d397eda.
- **Authenticated libtraceevent profile-use rebuild (2026-09-21):** exact `dev-libs/libtraceevent-1.9.0` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `17a01274b29a5d1cbb398bd57f004d1a1b8387fbd75c8868b0dea34d686a5810`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libtraceevent-1.9.0.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libtraceevent-profile-use-20260921.log`; its SHA-256 is `03239d8fb55e902653435e3a6353970576d5811474499b1821de1efc50dd0f0d.
- **Authenticated libtasn1 profile-use rebuild (2026-09-21):** exact `dev-libs/libtasn1-4.21.0` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `96f67465f50258006f6f21cc640db0e160d3ccd081a1ba7650d53972379088cf`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libtasn1-4.21.0.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libtasn1-profile-use-20260921.log`; its SHA-256 is `9640759daf6b060c5374385203d1c2021bfd267867eb3d1d8bde98ef9c3194cb.
- **Authenticated libutf8proc profile-use rebuild (2026-09-21):** exact `dev-libs/libutf8proc-2.11.3` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `4cf677564384441170314be6ed914479f2bad265ea0b322dd962d0a67fd7c31c`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libutf8proc-2.11.3.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libutf8proc-profile-use-20260921.log`; its SHA-256 is `78737792b2a8cf57802a36a83112094def774d04d1301f27e536254a17ccc668.
- **Authenticated libusb profile-use rebuild (2026-09-21):** exact `dev-libs/libusb-1.0.30` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `79930588e31ca53f0ba8ea26d2f1ca4e8ec21499f6ca357ad5a698ddbe3f0c5c`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libusb-1.0.30.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libusb-profile-use-20260921.log`; its SHA-256 is `db60a201c6b69d8c67c21fba4d7119bf0f9f97b6ad759b5deea9241e98ccb01a.
- **Authenticated libunistring profile-use rebuild (2026-09-21):** exact `dev-libs/libunistring-1.4.2` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `59d88a0b6edaf2923e4469825fe4a2edd93fff251dbfb61fe86b3a632ea462b7`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libunistring-1.4.2.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libunistring-profile-use-20260921.log`; its SHA-256 is `9b43a023c0e913b2b868c456673f2487428381d21609e6855407d7b9b6d53c79.
- **Authenticated libev profile-use rebuild (2026-09-21):** exact `dev-libs/libev-4.33` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `d82b43f3e2483f1221bda9ed9566ecc637ade425462a62fe8caadda1fc3f5849`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libev-4.33.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libev-profile-use-20260921.log`; its SHA-256 is `e201e0ee67949d86ab8348fde3a1f691d345f5853a99ba3ce6984ab5f0bf6b3a.
- **Authenticated libedit profile-use rebuild (2026-09-21):** exact `dev-libs/libedit-20240808.3.1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `9753cf3ba7b5dec1bd82bd60c0f0cc60efb14da2899a641b3729ddc7dc691afb`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libedit-20240808.3.1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libedit-profile-use-20260921.log`; its SHA-256 is `04e9c4c96fe17bedd9af899ab59ce1bb5ca22fef4eec7b940c250798ceb6bc4e.
- **Authenticated libpcre profile-use rebuild (2026-09-21):** exact `dev-libs/libpcre-8.45-r4` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `9cfab29ffc82528b24a08feb4adbe602d6d763f65944f558381417fcb34ba0d3`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libpcre-8.45-r4.profdata`. The package passed install-QA and ABI checks and merged successfully; compiler output recorded expected stale/unprofiled-function warnings. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libpcre-profile-use-20260921.log`; its SHA-256 is `ce5415b87996d9813618ad7f69d2f2662aa720a0b8bf510672017e62f74fb076.
- **Authenticated libpcre2 profile-use rebuild (2026-09-21):** exact `dev-libs/libpcre2-10.48` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `8e513cd60f65055b035466760d4551540e2be6baeee98b464d11089e6ef79007`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libpcre2-10.48.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libpcre2-profile-use-20260921.log`; its SHA-256 is `5e7dd91a2848c67f1b3265f7f9f72b92cc9ff5b8888e861e41e77aad8844a3f9.
- **Authenticated libksba profile-use rebuild (2026-09-21):** exact `dev-libs/libksba-1.8.1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `cebf8e9e8fc24c7c860cdefa2fc32cfc361f2239310481a618105dcdf0a7fff7`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libksba-1.8.1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libksba-profile-use-20260921.log`; its SHA-256 is `56748350fa801e24bfb5008cc75f34c9c7cff1b26fc4cd2ad2ec9618180ac2e4.
- **Authenticated libgpg-error profile-use rebuild (2026-09-21):** exact `dev-libs/libgpg-error-1.61` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `eaa3611228466dc98e1af95d66fffc18a892a446e9c2c63f908e7db97f56fc51`, and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libgpg-error-1.61.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libgpg-error-profile-use-20260921.log`; its SHA-256 is `7dd8d79dd87d635a3d3843264291c79874c240ce725e1bfb6ca17aac34e7bdfe.
- **libgcrypt profile-use attempt (2026-09-21):** exact `dev-libs/libgcrypt-1.12.4` entered the authenticated `clang-ir-use` dispatcher with fingerprint `eedb769f7602f155f60edfa7b0f49faf81ea3f309b31b498b5395c77cbd9dcb2` and profile `/var/cache/gentoo-optimization/pgo/dev-libs_libgcrypt-1.12.4.profdata`, but the existing 32-bit ABI build failed in libgcrypt FIPS TLS compilation (`libgcrypt requires thread-local storage to support FIPS mode`). The failure is preserved in `/var/lib/gentoo-optimization/reports/phase3-libgcrypt-profile-use-20260921.log` with SHA-256 `3266aa6e8552fd0f1d071d9264d8ed2682b0a7e8aa099bc90b2e0c6e939ab3ef`; no package mutation was admitted.
- **Authenticated gpgme profile-use rebuild (2026-09-21):** exact `app-crypt/gpgme-2.2.0` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `e7523c4261e5c391bbf9b9fbc945f0246ead99ab93b73373a3a310c26cd5f410`, and profile `/var/cache/gentoo-optimization/pgo/app-crypt_gpgme-2.2.0.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-gpgme-profile-use-20260921.log`; its SHA-256 is `52c30527fc1e0bab6897140da08d0edf9634d625c3665965df7a0626dc8d228a.
- **Authenticated gnupg profile-use rebuild (2026-09-21):** exact `app-crypt/gnupg-2.5.22` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `cc8595328bfbb7169e21ebe23ef9486ecd2cc2420baed7c93f1d053f3c6740eb`, and profile `/var/cache/gentoo-optimization/pgo/app-crypt_gnupg-2.5.22.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-gnupg-profile-use-20260921.log`; its SHA-256 is `c5c2d98d5b82146d70a37b7905bb2cbfe4b0912a2b11ae004e0bb7a61ef4d847.
- **Authenticated rhash profile-use rebuild (2026-09-21):** exact `app-crypt/rhash-1.4.6-r1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `95e5334691f15e6fafb4f0169822d1e801c11e8d248af269f570b95163dd374d`, and profile `/var/cache/gentoo-optimization/pgo/app-crypt_rhash-1.4.6-r1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-rhash-profile-use-20260921.log`; its SHA-256 is `1bb6a48f15efff5d6c58ef476bca95ce20ddc86c232092c086cc36269a6b7d08.
- **Authenticated pinentry profile-use rebuild (2026-09-21):** exact `app-crypt/pinentry-1.3.3` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `7c975b8cb557b689c5d6c27099de62d6c59475e09b864745ca5fe076f0a332b7`, and profile `/var/cache/gentoo-optimization/pgo/app-crypt_pinentry-1.3.3.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-pinentry-profile-use-20260921.log`; its SHA-256 is `4daca200e054a56c3a9464a1b8ef936852e724530fe9b5da7ea665af5926963c.
- **Authenticated libmd profile-use rebuild (2026-09-21):** exact `app-crypt/libmd-1.2.0` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `6d27d711001d9db1701b383b2965a9801c796bad374537dc1a413496ee6ef32d`, and profile `/var/cache/gentoo-optimization/pgo/app-crypt_libmd-1.2.0.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libmd-profile-use-20260921.log`; its SHA-256 is `22f8af85c5994ab04088602f81aef7d9362d1478167584f829866eba04587544.
- **Authenticated libb2 profile-use rebuild (2026-09-21):** exact `app-crypt/libb2-0.98.1-r3` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `8a3736bc4e75683f9f5ab09eddd48d33f4184d5a4b4068d882f4c84ad91ffea4`, and profile `/var/cache/gentoo-optimization/pgo/app-crypt_libb2-0.98.1-r3.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libb2-profile-use-20260921.log`; its SHA-256 is `4648b36d41d305e70ec81792e2896c13143a97b53cbc77a6c3dc8fcabffc646c.
- **Authenticated gcr profile-use rebuild (2026-09-21):** exact `app-crypt/gcr-4.4.0.1-r1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `ff92b3bea72b907262eebaf4e53a1299eaab76ed8e6ea24259a3d6c7628ea053`, and profile `/var/cache/gentoo-optimization/pgo/app-crypt_gcr-4.4.0.1-r1.profdata`. The package passed install-QA and ABI checks and merged successfully. The resolver reported the existing SPIR-V header transition conflict but did not alter that closure. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-gcr-profile-use-20260921.log`; its SHA-256 is `19b66a0bde818ae358ef61e24f5a7d1c32ad0651e9e57e579a57f9630a8f7455.
- **Authenticated argon2 profile-use rebuild (2026-09-21):** exact `app-crypt/argon2-20190702-r1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `0c17c5968cd62aaa965a01de4fb726fd4f45c39d4045724174d38579f9227f1d`, and profile `/var/cache/gentoo-optimization/pgo/app-crypt_argon2-20190702-r1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-argon2-profile-use-20260921.log`; its SHA-256 is `04931e73592683638c30142f87513c022e31c959f6f4bd2be37d93e4ea512707.
- **Authenticated cpio profile-use rebuild (2026-09-21):** exact `app-arch/cpio-2.15` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `4a69e1cb3b0d588a5cef4c413aa123d1922dde3fae65ee425f7c642f357d623a`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_cpio-2.15.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-cpio-profile-use-20260921.log`; its SHA-256 is `PLACEHOLDER`.
- **Authenticated cabextract profile-use rebuild (2026-09-21):** exact `app-arch/cabextract-9999` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `129312f48cabac22f7be2f16f652f5d1f3277dc393347bd0e59b09aba701e142`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_cabextract-9999.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-cabextract-profile-use-20260921.log`; its SHA-256 is `57c990bf1d1e28ab45448683c5e1a88bcf5aec01e9c7b672ad3e09fd956944dd`.
- **Authenticated zstd profile-use rebuild (2026-09-21):** exact `app-arch/zstd-1.5.7-r1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `80a4b69a300a630104faeabd87a67824d69ad2aaff58c17181f337b03ef3c2f2`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_zstd-1.5.7-r1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-zstd-profile-use-20260921.log`; its SHA-256 is `02e2acf74843dd0d440c883e42788b7ab534c5b21cf5263b8a20364a6007c0e2`.
- **Authenticated zip profile-use rebuild (2026-09-21):** exact `app-arch/zip-3.0_p16` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `f7381d50d3bd1bb7903a6aacdf935d54642025b73fdb7fc3c170f0a93010bf83`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_zip-3.0_p16.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-zip-profile-use-20260921.log`; its SHA-256 is `d6bcd6e69c210081df24b1c086f9b74568429f98349598c1306b506e590ee6ba`.
- **Authenticated xz-utils profile-use rebuild (2026-09-21):** exact `app-arch/xz-utils-9999` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `dd53f6e8a93b57b702a5c668d653c24a0084d15792dfbe57ca99a79ef09d65f6`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_xz-utils-9999.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-xz-utils-profile-use-20260921.log`; its SHA-256 is `498c78dda51e93a978422af4d83ecc0dd83004c208d0edacefc851bf2d89d0c1`.
- **Authenticated unzip profile-use rebuild (2026-09-21):** exact `app-arch/unzip-6.0_p31` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `7bdb5429425267a00dd63bf8243bf43303b0a9ded1961ea8e8b4c63969695508`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_unzip-6.0_p31.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-unzip-profile-use-20260921.log`; its SHA-256 is `bb89ae566658c39ee100f7c1ab3f581ea8879bf72df86f433ec52a165bf9e16b`.
- **Authenticated tar profile-use rebuild (2026-09-21):** exact `app-arch/tar-1.35-r1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `198663bb57ea42cde52fe85b07e53ecb9227eaabec00d6c67f175c4f7492f21e`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_tar-1.35-r1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-tar-profile-use-20260921.log`; its SHA-256 is `9e876bdfbfa037cf267d2e3b3f87ab1c9dc936a3d837bce368348965407fef73`.
- **Authenticated rpm2targz profile-use rebuild (2026-09-21):** exact `app-arch/rpm2targz-2021.03.16` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `bc7a087397753353b4f4412d479f7fc6574da06e1d9563df04d8fa2e4673ac9c`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_rpm2targz-2021.03.16.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-rpm2targz-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report.
- **Authenticated ncompress profile-use rebuild (2026-09-21):** exact `app-arch/ncompress-5.0-r2` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `95e5c28de0f8a7a4bd15afec474395b977ef5346351569ea50a6d20eb80c0df5`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_ncompress-5.0-r2.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-ncompress-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report.
- **Authenticated lz4 profile-use rebuild (2026-09-21):** exact `app-arch/lz4-1.10.0-r1` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `3b6f93849ce2249cadb52dbba5400849e1be4e6ab3266a6dfb13876ce8077b5f`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_lz4-1.10.0-r1.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-lz4-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report.
- **Authenticated libarchive profile-use rebuild (2026-09-21):** exact `app-arch/libarchive-3.8.9` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `624d91055bcf16a732dfe22ae86e7a972d8402d0b100d3cb17e598a49bf5b735`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_libarchive-3.8.9.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-libarchive-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report. Existing non-ELF optimization-metadata `ldconfig` warnings recurred without affecting the transaction.
- **Authenticated dpkg profile-use rebuild (2026-09-21):** exact `app-arch/dpkg-1.22.21` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `29f52a3d6e7eb9998f54fb48b0f7fd45631a1b0c47c574dcefdd241e639a8915`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_dpkg-1.22.21.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-dpkg-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report. Existing non-ELF optimization-metadata `ldconfig` warnings recurred without affecting the transaction.
- **Authenticated gzip profile-use rebuild (2026-09-21):** exact `app-arch/gzip-1.14_p20260901` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `def414f3627a208c9b4b4f976a7952aeef21e4d35eb3cc70487477fe1cda0f17`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_gzip-1.14_p20260901.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-gzip-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report.
- **Authenticated bzip2 profile-use rebuild (2026-09-21):** exact `app-arch/bzip2-1.0.8-r5` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `39801119e0e9003244324fbf05aa39b8ad477f637197fe6f80dfddd1596a0a28`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_bzip2-1.0.8-r5.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-bzip2-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report. Existing non-ELF optimization-metadata `ldconfig` warnings recurred without affecting the transaction.
- **Authenticated 7zip profile-use rebuild (2026-09-21):** exact `app-arch/7zip-26.03` rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `271ba625e76e47a9f61fd3941b81a4969ade42d016a6a5815605f99f2ad3a152`, and profile `/var/cache/gentoo-optimization/pgo/app-arch_7zip-26.03.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-7zip-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report. Compiler warnings about unprofiled source units and existing ldconfig warnings for non-ELF optimization metadata did not prevent the authenticated profile-use transaction.
- **Authenticated sysklogd profile-use rebuild (2026-09-21):** exact `app-admin/sysklogd-2.7.2` was rebuilt from its root-owned dispatcher with `clang-ir-use` active, matching fingerprint `ec66d8f6e963c8279ef22bfca04045c39043f0308ea684f505554b5034615ef9`, and authenticated profile `/var/cache/gentoo-optimization/pgo/app-admin_sysklogd-2.7.2.profdata`. The package passed install-QA and ABI checks and merged successfully. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-sysklogd-profile-use-20260921.log`; its SHA-256 is recorded in the root-owned report. Existing ldconfig warnings for preserved non-ELF optimization metadata recurred without affecting the transaction.
- **Authenticated doas profile-use rebuild (2026-09-21):** the existing root-owned dispatcher and manifest were loaded explicitly from `/var/lib/gentoo-optimization/dispatchers/app-admin_doas-6.8.2-final2.env`; the exact `app-admin/doas-6.8.2` rebuild completed with `clang-ir-use` active, profile `/var/cache/gentoo-optimization/pgo/app-admin_doas-6.8.2.profdata`, matching reviewed fingerprint, install-QA/ABI merge success, and `>>> app-admin/doas-6.8.2 merged`. The authoritative log is `/var/lib/gentoo-optimization/reports/phase3-doas-profile-use-20260921.log` (SHA-256 `6234bf1a8a81f91dd3cec976c70c4cf7125bd16894c729a22349d94e5cb0c9bf`).
- **Portable-complete regression boundary (2026-09-21):** after the authenticated wave evidence and receipt changes, `/usr/bin/bash tests/run-optimization-tests.sh --mode portable-complete --keep-temp` completed with `PASS=87`, `FAIL=0`, `SKIP=12`, `required_subtest_fail=0`, and `exit_status=0`. The long framework installer check, recovery suite, dispatcher, ABI guard, BOLT fixtures, and no-boot-entry automation all passed; the remaining skips are capability/root-driver exclusions recorded by the harness. Evidence is retained at `/tmp/gentoo-optimization-tests.FiA6tn6t`.
- **Authenticated three-lane profile wave (2026-09-21):** the current three-package wave was readiness-verified with `ready_count=3` and executed under the storage floor (203,409,014,784 bytes free; 21.06% free). `app-admin/doas-6.8.2` completed Clang-IR generation, `media-libs/libjxl-9999` completed GCC generation, and `dev-util/bindgen-0.72.1` completed Rust generation; all three produced nonempty raw payloads and completed attempt records. The first retry correctly failed closed because derived `/tmp` fingerprints were outside the authenticated root-owned namespace; a second retry exposed that the reviewed generation lacked `compiler-identities.json`, so execution was rebound to the root-owned `phase3-live-candidate-20260920-postsync-r1` identity namespace. The combined completed receipt is `/var/lib/gentoo-optimization/state/profile-transactions/phase3-live-candidate-20260920-sway-mesa-abi-reviewed-wave-f57ed324-completed.receipt.json`; its SHA-256 is `46dc35df811a738f2da289e7306c8c2823c7b21293f14c02733977458785c548`, and `verify-wave-receipt.py` passes. Failed duplicate attempts remain preserved; no package, boot, kernel, EFI, initramfs, or firmware bypass was used. Profile-use merge/deployment and BOLT remain separate pending gates. Focused `test-wave-receipt-verifier.sh` and the 16-test `test_validate_profile` suite both pass.
- **Terminal ELF eligibility and root safety review (2026-09-21):** BOLT eligibility now treats missing GNU build IDs as an explicit terminal `not-applicable` state (`missing-build-id`), because the readiness/deployment ABI cannot bind an input without that identity. Kernel/firmware-owned artifacts remain `kernel-policy-exclusion`; unreadable userspace metadata remains pending. The retained 16,727-record census now has zero pending eligibility records (2,510 candidate-eligible, 14,217 not-applicable). The root safety review was rerun with the authenticated reader so the setuid `/opt/vscode/chrome-sandbox` is no longer a false pending tool failure: safety counts are 1,927 profile-ready-pending, 103 rebuild-required, 480 intrinsically not-applicable, zero pending. Eligibility SHA-256 is `514613e5283cdb892bd14e3193306cd7df0bccbc575d6d0b6609d78f0ac9abf4`; safety SHA-256 is `17092678796121157991b805d03e853044893609401e93f4324d5c361b601df9`; the resulting coverage report SHA-256 is `83f51a5bd00e35506fd679d9edac6f6260120779d4e6fe76a96cca4f424c563f` with `coverage_pass=true` and zero missing classifications.
- **Kernel-policy ELF eligibility correction (2026-09-21):** `classify-elf-eligibility.py` now classifies records owned by `sys-kernel/*` or `sys-firmware/*` as `kernel-policy-exclusion` before metadata-tool status is considered. This prevents unreadable firmware blobs from becoming false pending userspace BOLT obligations while preserving pending treatment for unreadable userspace artifacts. A synthetic timeout fixture and the retained 16,727-record census both pass; the regenerated counts are 2,510 candidate-eligible, 3,155 not-applicable, and 11,062 pending eligibility records.
- **Independent Phase 3 coverage re-verification (2026-09-21):** reran `scripts/optimization/verify/phase3-coverage.py` against the retained 2026-09-20 workload, lane, ELF census, and BOLT-safety artifacts. The verifier reports `coverage_pass=true`, 538 workload package records, 1,305 lane records, 16,727 authoritative ELF identities, zero packages missing lanes, and zero ELF identities missing classification. The reverified report is `/tmp/derived-20260920/coverage-reverified.json` (SHA-256 `62dddd2c8b18fe24ad3efd4ee96b82858eb583ba17631107d06d276e68c17a5e5`). This proves accounting coverage only; pending BOLT eligibility and workload/profile execution remain separate gates.
- **Portable-complete validation repair (2026-09-21):** the live `/usr/bin/bash` portable run completed 87 executable cases with zero case failures, including the full recovery suite, Phase 2 evidence, package-env live-universe validation, and the framework installer. Final contract aggregation initially rejected two additive post-freeze unittest identities (`test_safe_rejects_symlinked_ancestor_after_path_spelling` and `test_safe_rejects_symlinked_final_path`) because the immutable Phase 2 contract still records 324 identities. Commit `66ba542` preserves the frozen identity digest while admitting those two explicitly tracked Phase 3 additions; direct contract revalidation of the completed run now passes (`tests=99`, `subtests=558`). No frozen Phase 2 evidence or contract file was edited.
- **Eighth authenticated profile-use deployment (2026-09-20):** exact `app-arch/libarchive-3.8.9` Clang-IR generation completed through install-QA and the ABI guard, the workload receipt independently verified, and LLVM 22 merged the raw payload. The validated manifest and metadata were dispatched, and the exact profile-use rebuild completed with `clang-ir-use` active, authenticated profile `/var/cache/gentoo-optimization/pgo/app-arch_libarchive-3.8.9.profdata`, and successful install-QA/ABI merge. Existing ldconfig warnings for preserved non-ELF YAML optimization metadata recurred without affecting the transaction. No BOLT claim is made.
- **Seventh authenticated profile-use deployment (2026-09-20):** exact `app-arch/gzip-1.14_p20260901` Clang-IR generation completed through install-QA and the ABI guard, the workload receipt independently verified, and LLVM 22 merged the raw payload. The validated manifest and metadata were dispatched, and the exact profile-use rebuild completed with `use_rc=0`, `clang-ir-use` active, and `/var/cache/gentoo-optimization/pgo/app-arch_gzip-1.14_p20260901.profdata` authenticated in the build log. No BOLT claim is made.
- **Sixth authenticated profile-use deployment (2026-09-20):** exact `app-arch/dpkg-1.22.21` Clang-IR generation completed through install-QA and the ABI guard, its workload receipt independently verified, and LLVM 22 merged the raw payload. The validated manifest and metadata were dispatched, then the exact profile-use rebuild completed with `use_rc=0`, `clang-ir-use` active, and the authenticated profile `/var/cache/gentoo-optimization/pgo/app-arch_dpkg-1.22.21.profdata`. Existing ldconfig warnings for preserved non-ELF YAML optimization metadata recurred without affecting the transaction. No BOLT claim is made.
- **Fifth authenticated profile-use deployment (2026-09-20):** exact `app-arch/cpio-2.15` Clang-IR generation produced a large authenticated receipt; the validator’s bounded JSON limit was raised from 4 MiB to 64 MiB in commit `efba967` to accommodate legitimate per-process payload records without removing digest and path checks. The receipt verified, LLVM 22 merged the payload, the manifest/metadata were validated and dispatched, and the exact profile-use rebuild completed with `use_rc=0`, install-QA/ABI success, and `clang-ir-use` active using `/var/cache/gentoo-optimization/pgo/app-arch_cpio-2.15.profdata`. No BOLT claim is made.
- **Cabextract profile-use source-fetch stall (2026-09-20):** `app-arch/cabextract-9999` completed authenticated Clang-IR generation, workload receipt verification, LLVM 22 profile merge, manifest validation, and dispatcher publication. Its exact profile-use rebuild entered the expected `clang-ir-use` mode but the live ebuild stalled at the upstream `kyz/libmspack.git` fetch for the bounded three-minute window and was terminated with signal 15. No profile-use package merge was admitted; the complete log is preserved at `/var/lib/gentoo-optimization/reports/phase3-cabextract-profile-use-fetch-stall-20260920.log` (SHA-256 `55413bf97e4cf191a5d93424b52ee89107c7e46c6244e3604c9a9534126f864c`). The generated profile and dispatcher remain valid; this is a source-fetch execution failure requiring a cached-source or fresh-fetch retry, not an optimization-policy bypass.
- **Fourth authenticated profile-use deployment (2026-09-20):** exact `app-arch/bzip2-1.0.8-r5` Clang-IR readiness passed, its source rebuild completed through install-QA and the ABI guard, and the workload receipt was independently verified. LLVM 22 merged the nonempty raw payload, the profile manifest and metadata were validated and published through the dispatcher, and the exact profile-use rebuild completed with `use_rc=0` and `clang-ir-use` active using `/var/cache/gentoo-optimization/pgo/app-arch_bzip2-1.0.8-r5.profdata`. Existing ldconfig warnings for preserved non-ELF YAML optimization metadata recurred without affecting the transaction. No BOLT claim is made.
- **Profile-use deployment for sysklogd and 7zip (2026-09-20):** the merged Clang profiles for `app-admin/sysklogd-2.7.2` and `app-arch/7zip-26.03` were independently validated, published through the dispatcher, and consumed by exact `clang-ir-use` rebuilds. Both transactions completed with `rc=0`, passed install-QA and ABI checks, and reported the authenticated profile paths in their active-mode diagnostics. Merge evidence digests are `0c5ec83062ba4f4028704e6187d1e8d5eb0222e002f5ad36b9edb3649560a3d6` (sysklogd) and `c4faac0bad1278e1c4095305e3791d35cb0b2eb3e9e5fade99d4d34ebeceb0c7` (7zip). The existing ldconfig warnings for non-ELF YAML optimization metadata remain harmless and unchanged.
- **Third authenticated generation wave (2026-09-20):** exact `app-arch/7zip-26.03` Clang-IR readiness passed, the full source rebuild completed and merged through install-QA and the ABI guard, and its workload produced a sealed receipt independently verified by `verify-wave-receipt.py`. LLVM 22 merged the raw payload with merge-evidence digest `f81019aaf42fca7dce9bf37f9b74702e28a5881b5d23f2cad6602804d723bdbb`. Existing ldconfig warnings for non-ELF `.yaml` optimization records recurred without affecting the transaction; no guard or policy bypass was used.
- **Second authenticated generation wave (2026-09-20):** exact `app-admin/sysklogd-2.7.2` Clang-IR readiness passed, the package rebuilt and merged through install-QA and the ABI guard, and its workload completed with a sealed receipt independently verified by `verify-wave-receipt.py`. LLVM 22 merged the raw payload; merge evidence digest is `685f57167ac1db95cb4f34b2d1d657cb4b6a0e4d46da79defea8e9f1e652587c`. The transaction emitted unrelated existing ldconfig warnings for preserved `.yaml` optimization records, but the package transaction itself completed with `rc=0`; no ABI or policy bypass was used.
- **doas profile-use deployment (2026-09-20):** profile publication initially exposed two implementation defects: the dispatcher deadlocked by re-entering generation-authorization while holding the same lock hierarchy, and the producer emitted a `cpv=` manifest key rejected by the eight-key Bash ABI parser. Commits `9fc49da`, `690dc56`, and `01a2732` fix those contracts. After republishing the framework and making the immutable merge evidence readable to the Portage verifier, the exact `app-admin/doas-6.8.2` profile-use rebuild passed dispatch, validated the manifest/sidecar/profile, passed install-QA and the ABI guard, and merged with `clang-ir-use` active (`rc=0`). This establishes a verified profile-use deployment for doas; BOLT deployment remains separate and unclaimed.
- **Authenticated doas profile payload (2026-09-20):** after relocating fingerprints into the reviewed root-owned generation namespace, the exact `app-admin/doas-6.8.2` Clang-IR wave passed readiness, rebuilt and merged through install-QA, collected nonempty raw payloads, and produced a receipt that independently verifies (`PASS: profile-wave receipt is internally consistent`). LLVM 22 merged the payload successfully; merge evidence records digest `4a05875c3a5a4cbbe7651e9e05f2d5e5588a708a067dbf9c80e1ee2f53a6c1e3`, and the merged profile SHA-256 is `dfab47419da5d54cd16c12b1c35420b5754076473cabfc76e42dfc4db0b5d5d0`. This is authenticated generation-profile evidence; profile-use deployment and final optimization verification remain pending.
- **First live wave against reviewed successor (2026-09-20):** the reviewed 1,305-CPV candidate was materialized with canonical generated-policy identity `e9d6e69a45ea7fecf74aace0ec2a588a7daa46bdba75c6d93284117ab60eb026`, published by the root-owned installer, strict-checked successfully, and transitioned into Phase-3 generation authority. The three-package readiness-valid wave then rebuilt and merged `app-admin/doas-6.8.2` in `clang-ir-generate` mode and collected its workload path. The next exact `media-libs/libjxl-9999` GCC attempt reached the upstream Git fetch for ref `7741c8ce` and made no progress; it was terminated after the established bounded stall window. No libjxl receipt or profile payload was admitted, and the doas result remains preserved in the wave attempt/receipt evidence. The wave runner and ABI guards were not bypassed.
- **ELF safety review decoding repair (2026-09-20):** the two pending BOLT safety records were Tailscale debug ELF files whose `readelf` output contained non-UTF-8 bytes. The metadata and safety tools now decode subprocess bytes with replacement while retaining fail-closed handling for nonzero exits and timeouts (commit `ad83298`). Regeneration removed the two false pending tool-invocation errors: safety now reports 1,944 `bolt-ready-pending-profile`, 86 `rebuild-required-for-bolt-capture`, and 480 `intrinsically-not-applicable` records, with zero pending safety records. The independent coverage audit remains green for 1,305 package lanes and 16,727 ELF identities; fixed safety SHA-256 is `9462e9ae51510aa6c1683537dbd5b2074adcfb0d9268b3f660de0d0f218bff76` and coverage SHA-256 is `98551a7ce86f77c6877d9419e55492767ece80811fd82f7ac5940778b670b944`.
- **Successor policy/workload/coverage derivation (2026-09-20):** the reviewed 1,305-CPV candidate now has fresh per-CPV fingerprints (538 PGO-lane inputs, zero materialization failures), policy bindings with digest `00527e73b76dc693b28bbfc5ebda63fdf2734a3c207c392d3f07c13ad69f4bc7` (artifact SHA-256 `fba53309d6653be6031c2496519b1a4014025600ccf306899beaba4e3f11535c`), workload derivation with 320 candidates and 218 no-runnable-entrypoint records, and recipes containing 291 ready, 238 no-runnable, and 9 no-profile-producing records. ELF eligibility classified 2,510 candidate-BOLT objects, 3,131 not-applicable objects, and 11,086 pending eligibility reviews; BOLT safety produced 1,944 profile-ready-pending records, 86 rebuild-required records, 478 intrinsically not-applicable records, and 2 pending safety records. The independent Phase-3 coverage audit passes for all 1,305 package lanes and 16,727 authoritative ELF identities (`coverage_pass=true`), while pending eligibility/safety work remains explicit and no optimization deployment is claimed.
- **EBUILD-correlated lane resolution (2026-09-20):** the initial VDB-only backend pass left 649 packages pending because it intentionally lacked ebuild evidence. Running the existing `correlate-ebuild-backends.py` against the reviewed 1,305-CPV inventory correlated all packages; lane assignment then completed with zero pending records: 517 not-applicable, 239 unsupported-by-upstream-toolchain, 510 pgo-clang-ir, 19 pgo-rust, 6 pgo-go, 3 pgo-gcc, and 11 kernel-policy exclusions. Candidate evidence is stored under `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260920-hyprutils-wlroots/` with ebuild backend SHA-256 `22726df05abac548e68bf1b1d54632ec5e5d074167b06af7c3160e03afb2f774` and lane SHA-256 `5cab10128bb630debda341cf0aef3c1ea50e007fa0775f95721bbf7967868564`. This resolves package-lane classification only; policy bindings, workload coverage, and ELF safety remain to be regenerated from the same candidate.

- **Derived-state refresh from reviewed Sway inventory (2026-09-20):** the live owned-artifact scan now reports 684,999 records and 16,727 ELF objects (SHA-256 `dbf068cf387a4078fee53b68ef3787aa6173cea6a502bba546ef01309edac7bb`); ELF metadata extraction completed for all 16,727 records (SHA-256 `5fe05cad5ca8940ac750f9ca9e696ec213e0fcb6feb73f2163256841f081869b`). Fresh backend/state derivation covers all 1,305 CPVs, and the current lane classifier emits 517 not-applicable, 84 unsupported, 41 Clang-IR, 3 GCC, 11 kernel-policy exclusions, and 649 pending package classifications (lane manifest SHA-256 `84610557cbe2478942407d53e5f632ac5b4f0dd369f715af64732995c789974f`). This is candidate evidence: the pending classifications must be resolved through the existing ebuild/build-log review path before any profile-wave authority is created.

- **Post-Sway live inventory refresh (2026-09-20):** after the successful Sway admission, the live VDB was regenerated into a new candidate with 1,305 CPVs, 685,179 owned paths, and 80,052 owned directories. The only new directory requiring review was `/etc/sway` owned by `gui-wm/sway-1.12-r1`; it was reviewed as configuration data (`not-machine-code`) with root-owned metadata and recorded at `/var/lib/gentoo-optimization/reports/frozen-directory-review-20260920-sway.json`. The reviewed candidate has zero unresolved directories and SHA-256 `dac34ff8f1a63460ded44fda9d0879c8e04d33ca3fe5aaac891ba7a8fa4a9a6a`; it remains candidate evidence pending derived-manifest regeneration and strict activation checks.

- **SPIR-V consumer closure and Mesa maintenance result (2026-09-20):** the coordinated userspace attempt admitted `gui-wm/sway-1.12-r1` with the wlroots X11 backend retained and rebuilt `media-libs/shaderc-2026.2`; `dev-util/spirv-tools-1.4.357.0` was correctly rejected by the exported-ABI guard because the HexFloat symbols disappear, so the SPIR-V transition remains fail-closed and unresolved. Mesa’s first retry exposed that Rusticl is selected by the ebuild when `llvm` and `opencl` are enabled; the maintenance policy now disables `opencl` for Mesa, allowing the full build to complete, but install-QA then rejected the staged replacement because the VA driver SONAME transition loses `__vaDriverInit_1_23`. Mesa therefore remains unmerged pending a coordinated ABI-safe consumer plan. No ABI guard or profile policy was bypassed.

- **Framework re-publication after maintenance-policy changes (2026-09-20):** after the ABI/fingerprint environment and maintenance package-env changes, the root-owned bootstrap republished the candidate framework and the independent strict `--check` passed again. Active framework identity is synchronized with the clean source HEAD; no package transaction was authorized by the check.
- **Profile-lane ABI/fingerprint maintenance repair and wlroots baseline (2026-09-20):** the newly published candidate exposed that ordinary Portage rebuilds of a pgo-lane package require an exact ABI and fingerprint supplied by the profile-wave runner; the shared generated environment lacked those transaction inputs. The four generated PGO environment templates now bind the host ABI explicitly, and wlroots/Sway maintenance atoms are explicitly routed through `optimization-off` until a profile-wave transaction supplies authenticated package identity. The framework was republished and strict-checked. A maintenance rebuild of `gui-libs/wlroots-0.20.2` with `x11-backend` retained completed successfully through install-QA; Sway remains unresolved because its current ebuild requires the `-x11-backend` transition and the SPIR-V 1.4.350/1.4.357 consumer closure is still split.
- **Successor framework regeneration and Sway resolver evidence (2026-09-20):** the current 1,304-CPV candidate was completed with 535 exact fingerprint inputs, a 1,304-record policy binding, and generated policy `generated-policy-54fa6124ea38072b0d5735ea90ed881766867208688d607b02f11a73d727e21a`; the root-owned installer published it and an independent strict `--check` passed. Retaining `wlroots` X11 support in the live package-use policy was then published to preserve the existing provider ABI, but Portage’s Sway dependency explicitly requires `-x11-backend` and the resolver also exposes the existing SPIR-V 1.4.350/1.4.357 slot conflict. No ABI bypass or forced downgrade was performed; the coordinated transition remains unresolved.
- **ABI-guard traversal regressions remain green (2026-09-20):** focused `test-abi-guard.sh` and `test-portage-qa-hook.sh` both pass, including zero-staged-DSO no-root-walk and non-recursive provider-scope cases. The live Sway retry therefore confirms an ABI transition blocker, not the earlier traversal-liveness defect.
- **wlroots/Sway ABI closure remains blocked fail-closed (2026-09-20):** after rebuilding `gui-wm/gamescope-3.16.28` successfully against the current provider, a retried stable `gui-wm/sway-1.12-r1` transaction still rejected staged `gui-libs/wlroots-0.20.2` because removing `-x11-backend` would drop six installed X11 exports (`wlr_backend_is_x11`, `wlr_input_device_is_x11`, `wlr_output_is_x11`, `wlr_x11_backend_create`, `wlr_x11_output_create`, `wlr_x11_output_set_title`). No replacement provider or Sway package was admitted. The ABI guard therefore continues to enforce the required coordinated consumer closure; no bypass, forced merge, or resolver weakening was used.
- **Repository sync retry and contract repair (2026-09-20):** the previously timed-out `steam-overlay`, `sft`, and `guru` repositories were retried individually and all completed successfully; their current Git heads are recorded by the live repository state. The immutable Phase-2 test-contract checker now preserves frozen unittest cardinality/digests while allowing additive current identities to run as ordinary tests, and still rejects suite shrinkage. The framework was republished from commit `398867a` and its strict installer check passed. Portable validation passed contract discovery, shell syntax, shellcheck, Python compilation, and the complete `tests/optimization` suite; the recovery suite exceeded the ten-minute wrapper bound and remains under focused diagnosis.
- **Fresh post-sync resolver evidence (2026-09-20):** the read-only `@world` update pretend completed with exit `0` and is retained at `/var/lib/gentoo-optimization/reports/phase3-live-world-pretend-20260920T170741.log` (SHA-256 `c5eef3c2b9b840745a797009ef39f1acdb6423e299e6a0ebc4d276322a7e14b2`). It proposes 83 packages, including kernel-source and firmware lifecycle entries that remain outside automated mutation, plus the userspace wlroots/Sway transition and SPIR-V 1.4.357 consumer rebuilds. `emerge --pretend --depclean` remains fail-closed because the installed graph lacks required `gui-libs/hyprutils` and `gui-wm/sway`; no package transaction ran. The extended portable suite reached and passed the recovery suite, then exceeded its 30-minute bound in the framework-installer fixture; this is retained as an execution-duration finding rather than a green full-suite claim.
- **Targeted userspace closure attempt (2026-09-20):** a read-only pretend for `gui-libs/hyprutils gui-wm/sway` produced a four-package userspace-only closure (`hyprutils`, `mesa_clc`, `wlroots-9999`, and `sway`) with the known SPIR-V header conflict excluded. The real oneshot transaction was started with `LLVM_PROFILE_FILE=/dev/null`, but the first Hyprutils Git fetch made no progress for more than three minutes before the pre-merge attempt was terminated. No package was merged; the fetch stall is retained as non-authorizing evidence and does not justify weakening the resolver or ABI guard.
- **Userspace closure partial progress and inventory refresh (2026-09-20):** using the cached `hyprutils-0.14.2.gh.tar.gz`, the targeted transaction merged `gui-libs/hyprutils-0.14.2`, `dev-util/mesa_clc-9999`, and `gui-libs/wlroots-9999` successfully through install QA. The final `gui-wm/sway-9999` Git fetch stalled for more than three minutes and was terminated before merge; no kernel, firmware, EFI, bootloader, or initramfs package was involved. Because the VDB changed, candidate `phase3-live-candidate-20260920-hyprutils-wlroots` was regenerated and strictly verified with 1,304 CPVs, 685,163 owned paths, and 80,035 reviewed directory records (inventory SHA-256 `14bc0f5f4e1891d0c0899a632b9fa8fc9bf9debbe1470fe6eb3ae2782ddfe929`). A deterministic `--directory-review` input was added to the inventory generator for newly observed directory records; all 19 new directories are explicitly reviewed as `not-machine-code` with report SHA-256 `27cd40c2d497edbc8d1dbe320e5db6cc6fdb37c6f644a68c146d33bc4f6e344a`.
- **Successor derived-state refresh (2026-09-20):** the new candidate's owned-artifact census contains 684,983 records and 16,721 ELF objects. Eligibility classification reports 2,510 candidate BOLT objects, 3,131 terminal non-applicable objects, and 11,080 pending safety reviews. Package state is 517 not-applicable, 774 pending PGO classification, and 13 kernel-policy exclusions before lane assignment; the regenerated lane manifest has zero pending lanes (508 Clang IR, 18 Rust, 6 Go, 3 GCC, 239 unsupported, 517 not-applicable, 13 kernel-policy exclusions). Workload derivation produced 317 candidates and 218 no-runnable-entrypoint records; recipes contain 289 ready, 237 no-runnable-entrypoint, and 9 no-profile-producing records. A coverage-audit compatibility repair now accepts the extractor's authoritative `elf-metadata-census` identity source and reports `coverage_pass=true`, 1,304 package lanes, and 16,721 ELF identities. The new source change is not yet activated as a framework or used for further package mutation.
- **Framework publication after successor-source changes (2026-09-20):** the root-owned installer republished and activated framework `/var/lib/gentoo-optimization/framework-dda7c8ae378370c943d0005a9078d510588fcb57509e17eb52ae954eac208000`; the independent strict `--check` passed. This publishes the generator and coverage-verifier fixes while the successor inventory's package policy remains candidate-derived and no additional package transaction has run.
- **wlroots/Sway ABI transition remains fail-closed (2026-09-20):** a cached-source retry for stable `gui-wm/sway-1.12-r1` rebuilt `gui-libs/wlroots-0.20.2` with `-x11-backend`, but install QA correctly rejected the staged DSO because six X11 exports still present in the installed provider would disappear. The transaction admitted no replacement and dropped Sway. The live reverse-dependency query identifies `gui-wm/gamescope-3.16.28` as the remaining installed consumer of `wlroots:0.20`; it must be rebuilt against the coordinated ABI before the provider transition can be retried. No ABI guard bypass or forced merge was used.

- **Dispatcher publication trust hardening (2026-09-20):** publication now holds the normal shared generation lock across ownership/mode changes and both atomic output creations, and validates the original manifest, metadata, fingerprint, profile, and Rust merge-evidence path spellings without resolving away symlink evidence. Any symlinked ancestor/final input or multi-link non-regular input is rejected; focused dispatcher policy remains 45/45 passing and the new path-trust regression tests pass. This is provenance hardening only; no profile-use authorization or package mutation is inferred.

- **Storage remediation and wave preflight (2026-09-20):** post-remediation storage baseline is 965,908,361,216 bytes total, 802,823,319,552 used, 163,085,041,664 free (16.88% free) on `/`; project evidence and PGO raw data were untouched. A fail-closed `storage-preflight.py` now enforces the initial 100 GiB and 12% free-space floors before `run-profile-wave.py --execute`, with focused pass/fail tests. It performs no garbage collection, so authenticated receipts, manifests, raw profiles, merged profiles, and recovery artifacts remain preserved.
- **Indexed-profile provenance hardening (2026-09-20):** the profile-wave runner now resolves the verifier from the committed `scripts/optimization/verify` path. New Clang/Rust indexed publications require merge evidence; validation metadata retains its evidence path and digest for independent verification. Merge validation reopens the completed receipt, checks generation/package/raw-payload identity, and verifies the complete `llvm-profdata` identity. The merger uses an explicit `clang-ir -> pgo-clang-ir` / `rust -> pgo-rust` lane map, and Rust dispatcher policy uses the canonical `rust` compiler-family spelling.

- **Rust profile validation and candidate publication (2026-09-20):** the authenticated `dev-util/bindgen-0.72.1` payload was regenerated with the hardened LLVM 23.1.1 merge-evidence schema, then validated independently with `/opt/rust-bin-9999/bin/rustc-bin-9999` (bundled LLVM 23.1.1) and the exact isolated `llvm-profdata` consumer. The canonical Rust manifest/metadata passed, the active generation authority verified, and the Rust dispatcher candidate was published under the shared lock with record SHA-256 `ef58b84aed3b6a03843fcb75bdfe6584917b7a3908bb51a8bead3bc76fdb9dd1`. This is candidate profile-use publication only; no package rebuild or deployed profile-use claim is made.

- **Backend-aware dispatcher (2026-09-20):** `publish-profile-dispatcher.py` now accepts `--backend rust` in addition to the existing Clang path, binds the manifest backend, uses the trusted generation root for Rust profile payloads, and emits backend-specific dispatcher mode/compiler-family records. The default Clang behavior remains unchanged.

- **Rust indexed-profile tool and merge (2026-09-20):** the bindgen Rust wave's LLVM 23.1.1 raw payload was merged with an isolated, exact-source LLVM 23.1.1 `llvm-profdata` built from `/var/cache/distfiles/llvm-project-23.1.1.src.tar.xz` (source SHA-256 `ebe9be46fe8756d58c5b198ffad0fa2a766257add81a4dc52179bfacc7888ee6`; binary SHA-256 `b655d4aa3c17040f13e4976cf7afa62ff5c5c90a77f4f2f2a62dc7fef4386c5b`) with zlib support enabled. The root-owned tool and provenance are at `/var/lib/gentoo-optimization/tools/llvm-23.1.1/`; the merged bindgen profile is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260920-postsync-r1/merged-profiles/dev-util_bindgen-0.72.1.profdata` (SHA-256 `2f094b3be4c18d9c5a37aea9ca829bd873b2757f577af7def6468b1fa6b2185f`) with merge evidence `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260920-postsync-r1/merge-evidence/profile-merge-bindgen-rust.json` (file SHA-256 `4580575e8d4f728d224fe990b30168bae4b24a66d78890e092c8fd08b02e1f36`; embedded record SHA-256 `5f7f3c4e04bb84c215ae86beb327c3769d3bf0359b0b1daaf36fbba7eb4ec338`). `validate-profile.py` now requires and verifies backend-specific indexed merge evidence, including exact generation triple, profile path, and profile hash, before publishing a consumer manifest. This profile remains pending dispatcher authorization; no incompatible LLVM 22 tool was used.

- **Starter profile-wave execution (2026-09-20):** the first three-package wave was readiness-valid, but `media-libs/libjxl-9999` was terminated after the upstream Git fetch for ref `7741c8ce` made no progress for more than three minutes. The attempt is preserved as `source-fetch-stall` with no receipt or accepted payload. `app-admin/doas-6.8.2` was then rerun as a single exact wave after fixing the canonical fingerprint-directory lookup in the readiness and runner gates. Its receipt passed independent verification; the root-owned receipt SHA-256 is `35ca7ddd1ffdbde843cdc7657515c9ee17eacde2d74b6543adf6a7caec3a1a62`, and LLVM 22 merged 298 authenticated raw payloads into `/var/lib/gentoo-optimization/merged-profiles/app-admin_doas-6.8.2-v2.profdata` (SHA-256 `03041ee4a38b51d05199a2273f45fe994fd9ff631a030b2a7a3eec4e9ee05385`; merge evidence SHA-256 `1f7b19bd0f221501922df6c0aa62e4eda393f902cbdee02e471bffdbb3088441`). The package remains in the instrumented training lane pending the later PGO-use/final deployment rebuild; no BOLT or final profile-use claim is made.

- **Post-sync framework publication (2026-09-20):** after root-owned bootstrap refresh, the authenticated installer published and activated framework `/var/lib/gentoo-optimization/framework-faeb9f11356ad59c04f8226b83aedfe6ad135371311a8e285b4bd99613d78f11` from generated policy identity `ddc22fb9940810c7b0bb2140a58d2faf440633e58a40452385f4e6ae032a801e`. The framework contains the 1,301-record generated `package.env` policy and frozen inventory SHA-256 `2d1408c587668cdca9e0138a07ea45af8201c71390054da9eec00693f756e701`; `/etc/portage` now resolves to this framework. The Rust dispatcher now requires an exact merge-evidence record at publication and binds it to the merged profile digest. The publication completed with the root-owned framework-install verification pass. This activates the current userspace framework only; no package transaction, boot, kernel, EFI, or initramfs mutation was performed.

- **Current-generation derived inventory (2026-09-20):** the regenerated candidate now has exact lane manifest `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260920-postsync-r1/inventory/pgo-lanes.json` (SHA-256 `22b6f7606d74e8f5c61cfe506d5e7582bfc4dfb3492467b0395329ef17b49cd1`) with zero pending lanes: 516 not-applicable, 228 unsupported-by-upstream-toolchain, 507 pgo-clang-ir, 28 pgo-rust, 6 pgo-go, 3 pgo-gcc, and 13 kernel-policy-exclusion. Exact VDB fingerprints were materialized for all 544 PGO-lane packages. The policy-binding builder was corrected to consume its canonical per-CPV fingerprint-directory layout, then produced bindings with SHA-256 `3a9a3f31b4dc37b8e9823cebc3e841f7bcc8431ca808588018433246100013cf`. The initial coverage audit is SHA-256 `7e3209dc30e753363ecc7de6fba2175851bba972592bd8b348bdcdc32711ba3b`; all 1,301 package lanes and all 16,715 authoritative ELF identities have records, while 11,087 ELF records remain pending eligibility review. Workload manifest and recipe outputs are SHA-256 `9c0c7a024bf485a46dfe1dafb43247bf4dc4d47bdc3aa3cda3bd7619bb7380bf` and `5f51a638f429a9a8048a1b495740fe8c4bdebf82d15884b5055bb92b3b4fbfd3`.

- **Fresh post-sync inventory checkpoint (2026-09-20):** all configured repositories synchronized successfully. The read-only world and userspace pretends remain unresolved because of the SPIR-V 1.4.350/1.4.357 consumer closure and the current wlroots/Sway USE transition; depclean also failed closed on the unresolved Hyprland dependency closure. No package transaction ran. The live VDB now contains 1,301 CPVs, so the prior 1,292-CPV candidate is stale. Candidate `phase3-live-candidate-20260920-postsync-r1` was regenerated from `/var/db/pkg` with inventory SHA-256 `2d1408c587668cdca9e0138a07ea45af8201c71390054da9eec00693f756e701`, 684,990 owned paths, 79,985 owned directories, 684,810 artifact records, and 16,715 ELF records. Seven new debug/metadata directory records were reviewed as `not-machine-code` in `/var/lib/gentoo-optimization/reports/frozen-directory-review-20260920.json` (SHA-256 `7f6b4db328a55744123b0737952881922e5f3349e840f796a58d3e4fc50ea13c`). Fresh backend/lane derivation assigns all 1,301 packages a terminal lane or supported PGO lane: 516 not-applicable, 228 unsupported-by-upstream-toolchain, 507 pgo-clang-ir, 28 pgo-rust, 6 pgo-go, 3 pgo-gcc, and 13 kernel-policy-exclusion. ELF metadata extraction and the corrected coverage audit completed for all 16,715 authoritative ELF records; the initial audit has no missing package lanes or missing ELF classification/safety records, but retains 11,087 pending eligibility-review records and is not final optimization coverage. Workload derivation produced 324 workload candidates and 220 no-runnable-entrypoint package records; recipe generation produced 293 ready, 242 no-runnable-entrypoint, and 9 no-profile-producing-workload records.

- **Current execution frontier (supersedes the predecessor narrative below):** exact successor commit `3ac32f20415e0eead348b8d99e51cba5389d0632` is now authorized for Phase 2 by the root-owned detached index `/var/lib/gentoo-optimization/state/project/phase2-evidence/phase2-3ac32f2-20260915T113000Z-final5/index.json` (SHA-256 `f72471f3aeb0b7efc3a6b7e13ae0117ce206576450559af43752d3b1794a72d9`), with aggregate failed/pending/unknown `0/0/0`, 95/95 required lanes, and 552 required subtests passing. The production sample transaction and all eleven component states are bound to the same boot, source, and evidence root. Phase 3 userspace inventory and optimization work may now proceed; boot, kernel, EFI, initramfs, and bootloader mutation remain prohibited.
- **GCC compatibility bridge frontier (2026-09-17):** the full PGO/bootstrap of `sys-devel/gcc-17.0.9999` completed, but its staged `libstdc++.so.6.0.37` was rejected by the unchanged exported-ABI guard because six installed `GLIBCXX_3.4.36` symbol identities were absent from the staged image (the installed runtime remains `libstdc++.so.6.0.36`). The failed transaction is retained as non-authorizing evidence; no runtime was forced or patched. Installed VDB `environment.bz2` records `EGIT_VERSION=03c4df9126fe6f8d6b5cdd49e7116310773e87a7`; that identity is the Gentoo `gcc-patches` patchset-9 repository, not demonstrated GCC source. The historical Gentoo ebuild revision is `7217ce442ffc46cf7efc94f2a7047f46e5fb19f9` and its July configuration used `PATCH_GCC_VER="17.0.0"` with `PATCH_VER="9"`. The active compatibility bridge pins the upstream GCC source to pre-transition parent `336f25a0ad83cc74b6a18137ffd177eefa4a81b0`; its current build remains in progress, and the exact patch payload will be recorded and pinned before final generation authority. Acceptance requires the existing guard to report zero removed installed versioned symbols. The September snapshots `17.0.0_p20260906` and `17.0.0_p20260913` are present and individually selectable under the local resolver but post-date the ABI transition; formal toolchain completion remains pending while independent Go/BOLT preparation may continue.
- **GCC bridge staging evidence (2026-09-17):** the source-pinned bridge compiled and staged successfully with GCC source `336f25a0ad83cc74b6a18137ffd177eefa4a81b0`. Its staging environment records the separate Gentoo `gcc-patches` checkout actually used as `4e8f229f9075e37ecb5e69346810e2c1090a274f`; this is the exact current patch payload identity, but the ebuild still leaves `PATCH_VER` unset, so future builds must pin a versioned patch payload before this bridge can become final reproducible generation authority. A package-scoped ABI-guard comparison of the installed and staged `libstdc++.so.6.0.36` providers returned zero removed symbols. The image has not been merged into the live root; full package QA/merge evidence remains pending.
- **GCC bridge merge receipt (2026-09-17):** after the successful staged ABI comparison, the packaged `sys-devel/gcc-17.0.9999-r1` image merged through qmerge with exit `0`. The installed compiler reports `Gentoo 17.0.9999-r1 p, commit 4e8f229f9075e37ecb5e69346810e2c1090a274f`, upstream source `336f25a0ad83cc74b6a18137ffd177eefa4a81b0`, and the installed `libstdc++.so.6.0.36` retains `GLIBCXX_3.4.36`. This provisionally unblocks the GCC userspace lane; it is not yet final reproducible generation authority because future ebuilds still need a frozen versioned patch payload rather than a moving patch checkout.
- **GCC successor pin (2026-09-17):** the local bridge ebuild now pins `PATCH_VER="9"` and its Manifest includes the versioned `gcc-17.0.0-patches-9.tar.xz` payload. This makes successor builds deterministic while preserving the already merged candidate’s recorded moving-checkout identity; a future rebuild can independently verify that patchset 9 reproduces the successful bridge before final generation authorization.
- **Resolved correctness incident / retained predecessor evidence (2026-09-10):** the recovered live system exposed two broken shared-library canaries (`sys-libs/libxcrypt-4.5.2` and `dev-libs/opencl-icd-loader-2025.07.22`) whose replacement DSOs had zero defined dynamic exports under the prior global hidden-visibility/ThinLTO policy. The known-good binary restorations and exact emerge-log evidence are preserved in `/var/lib/gentoo-optimization/reports/phase3-20260910T002616Z/` (incident receipt SHA-256 `8c6503a8d4dfcd7c11c8823008748a2469c7519317463c654469c99009c7015b`; wave-audit receipt SHA-256 `ba1a9638c330641f27f6a9aa75713ca2a577e32561634057c87f4c154b086533`; active-framework canary receipt SHA-256 `38fe5e573e6f9b3202970a21ad1986c11a05fd37c50d674b0ed2c1a6a8a221dd`). Global hidden visibility is removed from the repository baseline, both canaries are assigned the existing `no-hidden-no-forced-libs.conf` ABI-safe lane, and the immutable framework carries a pre-strip exported-ABI guard plus regression fixture. The ABI/QA-hook contract is now closed at the current source: test overrides are forbidden in ebuild phases, ED/ROOT validation is fail-closed, ELF magic and staged symlink handling are explicit, and established ELF-to-non-ELF replacement is rejected. The next action is exact successor freeze and authorization. The authenticated intermediate-bootstrap migration is now implemented; corrected framework generation `4546753d...` installed and its `--check` passed. Active-framework non-merging canary binpkg builds passed with required `crypt@@XCRYPT_2.0` and `clRetainCommandQueue@@OPENCL_1.0` exports; the read-only affected-wave audit confirms the restored live DSOs have 9 and 123 defined dynamic exports. The live root remains on the restored CPVs, with no bulk rebuild performed.
- **Historical/superseded execution prose:** lower sections retain requirements as they applied at earlier Candidate-A and prerequisite frontiers. They are historical evidence and do not authorize another prerequisite transaction or another Candidate-A development cycle; the current frontier above controls execution.
- **Rejected predecessor boundary:** the exact frozen-source attempt at `eb0cd18b30f44bd6e7b30ef0e1499be302718730` is retained as non-authorizing evidence: it was intentionally interrupted at 65/95 top-level PASS while entering the recovery suite, so no complete 95-case contract exists for that source. Later ABI-guard symlink hardening also supersedes `eb0cd18`. Mutable-desktop retries do not authorize Phase 2. No production-framework failure is inferred from the interrupted frozen boundary; a fresh exact-source boundary is required before detached authorization.
- **Retry-history reconciliation:** the rollback workers for `jsonschema-source-20260907T013000Z` and `...180000Z` did not publish terminal recovery states; their retained `recovery-child-aborted`/rollback evidence remains immutable. Re-entry verification correctly fails closed because the historical `_emerge` authority differs from the current host, so these IDs are consumed historical incidents and cannot authorize mutation. The later `...190000Z` attempt is terminal `recovery-failed`; no retry is armed from this plan frontier. The older checkpoint `checkpoint-pre-candidate-a-deps-refresh-20260906T190000Z` remains an immutable superseded `selector-activated-offline-restore-pending` record; the later terminal post-jsonschema checkpoint is the authoritative recovery proof.

- **Project state:** active; Phase 0 and Phase 1 are complete, Phase 2 implementation is complete at 14/14, and Phase 2 is authorized at the exact successor detached index recorded above. Phase 3 userspace inventory is the next execution frontier. Phase 2 remains scope-frozen: no new subsystem, optimization-policy axis, evidence category, or broad refactor may be added unless an existing required gate exposes a reproducible blocker that cannot be fixed within the current architecture.
- **Dedicated branch:** `feat/system-wide-pgo-bolt`.
- **Starting repository commit:** `c04773564da826abdeea3660568701d040cc89d0`.
- **Optimization generation:** not established; inventory is not yet frozen.
- **Phase 3 inventory refresh (2026-09-15):** repository synchronization completed for all configured overlays and the live VDB was revalidated at 1,261 CPVs (userspace 1,250; explicit kernel-policy exclusion 11). The root-owned ownership/ELF census is retained under `/var/lib/gentoo-optimization/generations/phase3-live-ownership-20260915-final/`, with current plan commit binding and live CPV hash `17a7416a7900fff64b76784808b8acd78dd778ab9f2d86bc92ce7da792d60eb0`. The inventory is not yet authoritative for mutation because the required normal userspace update is unresolved; depclean and update pretends exposed the current kernel-policy REQUIRED_USE conflict plus Qt/libdisplay-info dependency drift. No package transaction or boot/kernel mutation was performed.
- **Hyprland userspace ABI repair (2026-09-17):** the live userspace chain was rebuilt without boot or kernel changes. `dev-libs/hyprgraphics-0.5.1-r1` now carries the required legacy `unexpect` export; `gui-libs/hyprtoolkit-0.6.0` replaced the `.so.5` ABI with `.so.6`; `sys-auth/hyprpolkitagent-9999`, `gui-libs/xdg-desktop-portal-hyprland-9999`, `gui-libs/wlroots-0.20.2`, and `gui-wm/hyprland-9999` were rebuilt successfully. The final `@preserved-rebuild` pretend reports zero packages. Hyprland required the narrow tracked standard-header patch `67f2b82`; this is functional/ABI remediation evidence, not PGO/BOLT coverage authorization. The SPIR-V 1.4.357 header conflict remains unresolved.
- **SPIR-V coordinated upgrade rejection (2026-09-17):** a single transaction for `spirv-tools`, `glslang`, and `vulkan-layers` 1.4.357 resolved the dependency graph and updated `media-libs/shaderc-2026.2`, but the replacement `spirv-tools-1.4.357` failed the exported-ABI guard. The missing symbols are the prior `spvtools::utils::HexFloat` template data/function instantiations (`uint_type` manglings) across `libSPIRV-Tools.so` and `libSPIRV-Tools-opt.so`; the new candidate has a different `CastResult`-style ABI. No guard bypass or forced replacement was performed; the installed tools, glslang, and vulkan-layers remain at 1.4.350.
- **Read-only world-update audit (2026-09-17):** `emerge -pvuDN --with-bdeps=y --backtrack=30 @world` remains non-authorizing. It includes kernel/firmware lifecycle packages, which are outside the automated project under `kernel-policy-exclusion`, and reports the unresolved SPIR-V 1.4.350/1.4.357 graph conflict plus an unrelated wlroots USE adjustment. No world transaction was run.
- **Userspace-only update audit (2026-09-17):** excluding `sys-kernel/*`, `sys-firmware/*`, and systemd still leaves an unsatisfied graph. The generated `@pgo-bolt-all-userspace` set pins exact SPIR-V 1.4.357 atoms, while installed `wlroots-0.20.2`/`shaderc-2026.2` retain 1.4.350 glslang/tools constraints; the pretend also proposes a wlroots 0.21/9999 transition and a new Sway package. The set was not weakened or edited to conceal the conflict, and no transaction was run.
- **SPIR-V consumer-closure audit (2026-09-17):** installed reverse dependencies confirm that the 1.4.350 ABI providers are consumed by `glslang`, `vulkan-layers`, `shaderc`, `wlroots-0.20.2`, Mesa/mesa tooling, gamescope, Hyprland, and related Vulkan applications. The 1.4.357 set requires a coordinated rebuild of that closure; replacing only the three SPIR-V packages would repeat the rejected `HexFloat` ABI transition. No resolver mask, autounmask, or ABI-guard bypass was applied.
- **Post-repair integrity verification (2026-09-17):** direct VDB `CONTENTS` verification found zero MD5 or symlink mismatches for the rebuilt Hyprland chain, and dynamic linking plus the hyprgraphics legacy export passed. Gentoolkit `equery check` reports false failures because its installed checker calls Portage `perform_md5(calc_prelink=1)`, while the current checksum API rejects that keyword; the checker error was reproduced independently. This is a verification-tool compatibility defect, not installed-file drift.
- **Framework drift repair (2026-09-17):** the active immutable framework failed its own check because its candidate entry set no longer matched the current source/policy. Republishing against the frozen Phase 3 inventory and generated policy activated `framework-d7edf1f2cdab3288c9a221ea8dc6e20246896d2da09b56bb3d929b3db2bc39f1`; the independent installer `--check` now passes with the root-owned framework manifest.
- **Frozen-inventory drift audit (2026-09-17):** after the completed userspace ABI transitions and Autodesk package revision, the live VDB contains 1,292 CPVs versus 1,291 in `phase3-live-owned-complete-20260917/frozen-inventory.json`. Seven CPV identities were added and six removed (including the intentional old/new Hyprland and Autodesk identities). The prior frozen inventory is therefore no longer mutation-authoritative; a fresh complete live inventory must be generated before any further Phase 3 optimization transaction.
- **Frozen-inventory validator scaling repair (2026-09-17):** the production framework check reached the strict validation of the fresh 1,292-CPV inventory but spent the diagnostic window performing a linear package-list lookup for every one of its 679,633 owned paths. The immutable installer now builds a CPV membership map before validating path and directory owners, preserving the same fail-closed schema and uniqueness checks with bounded lookup cost. The corrected installer is committed as `543a5a2`; the root-owned bootstrap copy has the identical SHA-256. A full live check remains pending because the subsequent global path sorting and candidate filesystem verification are still too slow for the bounded check window.
- **Portage CONTENTS path parser repair (2026-09-18):** fresh inventory generation exposed that `generate-live-inventory.py` used whitespace splitting, truncating paths containing spaces in the Autodesk Maya tree. The parser now preserves `dir`, `obj`, and `sym` paths according to their record syntax. Regeneration against the live VDB produced 1,292 CPVs and 679,818 owned paths; the changed-directory review for twelve legitimate directory metadata transitions is retained at `/var/lib/gentoo-optimization/reports/frozen-directory-review-20260918-r3.json` (SHA-256 `039768ddb96fbe0c7703406714da005662b1d2596dd878e09234dbf6d9845a11`). The corrected candidate is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-r3/frozen-inventory.json`; it has not yet been bound to a regenerated policy or activated.
- **Inventory ordering repair (2026-09-18):** the strict inventory contract requires directory records sorted by `(owner_cpv, path)`, while the generator previously emitted them by path order. The generator now emits the contract order. Candidate `phase3-live-candidate-20260918-r4` contains the same live CPV/path census with the corrected order and the twelve reviewed directory transitions.
- **Phase-3 correctness repairs (2026-09-18):** before framework activation, the coverage verifier now compares a separately supplied authoritative ELF census against owner/path classification identities; ELF metadata and BOLT safety tools now fail closed into explicit `pending` error records when `readelf` cannot complete; kernel-policy classification derives `CATEGORY/PN` from VDB rather than the revision-bearing `P` file; and inventory path ordering is generated in the validator’s canonical owner/path order. These changes are source-level repairs only; the current candidate must be regenerated and rerun through the strict installer gate.
- **Derived-manifest drift confirmation (2026-09-18):** direct comparison of the current `phase3-live-candidate-20260918-r5` CPV set against the prior package manifest and PGO policy bindings found exactly four stale identities: the three Autodesk revisions and pre-bridge `sys-devel/gcc-17.0.9999`. Those derived artifacts remain historical and are not used to authorize a profile wave; complete lane/state regeneration is required from the current inventory.
- **Starting live package count:** 1,181 CPVs; this is evidence capture only, not the frozen Phase 3 inventory.
- **Current non-frozen live package count:** the earlier 1,256 and 1,259 observations are superseded by the interrupted preliminary wave. Successful merges recorded after its start include `llvm-core/llvm-common-24.0.0.9999`, `sys-apps/sandbox-9999`, `net-dialup/ppp-9999`, and `app-misc/ca-certificates-20260601.3.112.5`; later packages reached build/image stages without a completed merge record. This is live progress evidence, not the frozen Phase 3 inventory, and must be recomputed immediately before the first Phase 3 mutation.
- **Read-only prerequisite resolution refresh:** a current Portage pretend for `dev-python/jsonschema` resolved an exact 20-package closure with exit status 0; stdout and stderr are retained under `/var/lib/gentoo-optimization/reports/jsonschema-readonly-pretend-20260905/` (SHA-256 `d904504e4c792ac2724a6e9baff4d1e98ea70060f67e4475ce7bf271fdc0ca66` and `7ee14b3e681ec333a58572a460f1a0b5f5ad72c71eee1465f51ac43243619b93`). This is read-only resolver evidence only; it does not authorize or perform the prerequisite transaction.
- **HISTORICAL / SUPERSEDED EXECUTION NOTE:** transaction `jsonschema-source-20260906T100000Z` was reconciled through the immutable recovery CLI and terminally recorded as `recovery-failed`; its receipt and evidence remain immutable historical records. The old ID is consumed and non-reusable. This historical paragraph does not authorize a fresh prerequisite transaction.
- **HISTORICAL / SUPERSEDED EXECUTION NOTE:** transaction `jsonschema-source-20260905T181440Z` reached `recovery-failed` before payload admission; its receipt and evidence remain immutable historical records. The ID is consumed and non-reusable. The current frontier has since established prerequisite completion and retired new prerequisite mutation; this paragraph does not authorize another transaction.
- **Fresh transaction failure classification:** that attempt reached the first package merge and was rejected by payload ancestry validation because the new `tzdata-10001.dist-info` directory was legitimately absent before its authenticated image merge. The correction allows manifest-declared absent destination ancestors while retaining exact closure and `/usr` device checks; no package, boot, or kernel state was admitted or changed.
- **HISTORICAL / SUPERSEDED EXECUTION NOTE:** the recorded Python package authority mismatch explains why the old transaction cannot be reused. New prerequisite preparation is retired and fail-closed; this paragraph does not authorize another transaction.
- **Plan checkboxes:** 85 checked, 62 open, 147 total after removing the two completed boot/kernel recovery requirements and replacing the final kernel-optimization requirement with an explicit userspace inventory exclusion check. The Phase 2 implementation checklist is 14 of 14 complete; the detached index authorization remains a post-B state authority.
- **Strict coverage totals:** pending the Phase 3 live inventory; no zero-coverage claim has been made.
- **Current Phase 2 authorization:** authorized by detached index `phase2-3ac32f2-20260915T113000Z-final5` with aggregate failed/pending/unknown `0/0/0`; the index, component states, production receipt, and exact-source provenance are immutable root-owned evidence.
- **Last plan review:** 2026-09-15. The exact-source authoritative live boundary, production sample transaction, eleven component states, and detached index were independently verified locally; Phase 2 is authorized and the next required work is the complete live Phase 3 userspace inventory. The complete plan must be reread after each live checkpoint or package operation.
- **Package-recovery safety gate:** protected binpkg restoration and separate executable-tested Clang/libc++ and GCC/libstdc++ recovery lanes are verified. Boot-entry, EFI/boot-asset, initramfs, and kernel-lifecycle work are no longer project requirements or evidence. They are subject to the absolute human-only boundary below and must not be repeated by an agent.
- **BOLT capability gate:** the historical Phase 1 gate passed on 2026-07-12 with package-managed LLVM BOLT 22.1.8. The final successor authorization source must rerun the complete four-class gate because the evidence verifier changed after the historical green boundary; the old proof is preserved and is not itself Phase-2 authorization.

## Document purpose

This document is an execution plan for an LLM coding/system-administration agent working against the `P2949/Gentoo-stuff` repository and the live Gentoo installation that uses it.

The agent must implement an exhaustive, package-accounted optimization pipeline. By the end of the plan:

1. Every package installed in `/var/db/pkg` must be inventoried, classified, and assigned a final optimization state. Every userspace-only package in project scope must be rebuilt from source; any CPV whose package transaction crosses the kernel lifecycle boundary is not rebuilt and instead receives `kernel-policy-exclusion` for the whole CPV.
2. Every in-scope userspace package that produces code eligible for a supported PGO mechanism must have been rebuilt using that mechanism and a valid representative profile.
3. Every installed 64-bit x86 ELF executable or shared object that is technically safe and valid for BOLT must have been BOLT-optimized from an exact PGO-built input before Portage stripping and deployment.
4. Every artifact that cannot physically receive PGO or BOLT must have a machine-verifiable exclusion reason. Nothing may be silently skipped.
5. The final system rebuild must run with PGO-use and BOLT deployment enabled together, so the files installed at completion are the final PGO+BOLT variants rather than temporary training binaries.
6. Kernel lifecycle packages and artifacts must use the machine-valid terminal reason `kernel-policy-exclusion`, denoting the human-only system boundary; kernel and boot-chain optimization are outside this userspace project.
7. The final strict coverage verifier must report zero unclassified, pending, stale, mismatched, or failed eligible packages and artifacts.

This document must remain in the repository at:

```text
plans/system-wide-pgo-bolt.md
```

The implementing agent must update the checkboxes, status tables, decisions, discovered exceptions, commands, and results in this document after every implementation step. After each completed step, the agent must re-read this document before deciding the next action. The one deliberate exception is the Phase 2 Candidate-B authorization freeze: after Candidate B commits the truthful checked claims and generated claim markers, this plan must not change while the complete gate is rerun. The root-owned detached index and component states carry that post-commit evidence. Any required plan correction creates Candidate C and requires the complete gate to run again.

---

# 1. Non-negotiable interpretation of “all packages”

A literal demand that every package be “compiled with both PGO and BOLT” is technically impossible because many installed packages contain only scripts, headers, fonts, firmware, configuration, data, documentation, JVM/Python bytecode, static metadata, or architecture-specific objects that BOLT does not support. BOLT is a post-link optimizer for supported ELF machine-code objects; it cannot optimize shell scripts, Python source, Java bytecode, firmware blobs, static archives as archives, 32-bit x86 ELF, GPU code objects, eBPF objects, kernel modules through the normal userspace path, or packages that install no machine code.

The plan therefore defines successful full-system coverage as follows:

- **All installed packages are included in the project.**
- **All eligible code receives the strongest valid PGO mechanism.**
- **All BOLT-eligible deployed 64-bit x86 ELF executables and DSOs receive BOLT.**
- **All non-eligible packages and artifacts are explicitly proven non-applicable.**
- **There are no discretionary “not worth optimizing” exclusions.** Low expected benefit is not an exclusion reason.
- **A package may be excluded only because the optimization is technically inapplicable, impossible to profile safely, unsupported by its compiler/toolchain, demonstrably breaks correctness after package-specific remediation has been attempted, or crosses the explicit human-only kernel lifecycle boundary.**

The final report must distinguish:

```text
optimized
not-applicable
unsupported-by-upstream-toolchain
unsafe-to-profile
correctness-failure-after-remediation
binary-only-no-rebuild-source
kernel-policy-exclusion
```

Only `optimized` and an evidence-backed terminal exclusion are acceptable final states. `pending`, `unknown`, `not-tested`, `skipped`, and `failed-without-analysis` are forbidden at completion.

---

# 2. Agent operating contract

The implementing agent must follow these rules throughout the project.

## 2.1 Work from evidence

- Inspect the live system; do not assume the uploaded repository reflects every currently deployed file.
- Treat `/var/db/pkg`, live `/etc/portage`, active compiler versions, the
  read-only running-kernel identity, current package graph, and actual installed
  userspace ELF files as the source of truth. Running-kernel observation never
  grants authority to configure, build, install, or modify it.
- Record every command and important result in the project state directory.
- Do not infer that a package used PGO merely because a flag was configured. Verify build logs and profiles.
- Do not infer that a file was BOLTed merely because the package was marked BOLT-enabled. Verify `.note.bolt_info` in the installed ELF.

## 2.2 Never silently weaken the goal

- Do not quietly convert “all packages” into a small hand-picked package list.
- Do not skip libraries. Instrumented libraries must be trained through reverse dependencies or test workloads.
- Do not skip rarely used executables solely because expected performance benefit is small.
- Do not declare success while any eligible package or ELF remains unprofiled.
- Do not apply empty, unrelated, stale, or mismatched profiles merely to make coverage numbers appear complete.

## 2.3 Protect system recoverability

- Never remove the last known-good binary package for a critical package.
- **Never create, edit, delete, rename, enable, disable, reorder, select, or arm a firmware, EFI, BIOS, bootloader, rescue, or recovery boot entry.** Do not set or clear `BootNext`, change `BootOrder`, write EFI variables, invoke boot-entry management tools, or write `/sys/firmware/efi/efivars`.
- Never modify `/efi`, `/boot`, bootloader configuration, kernel command lines, EFI executables, kernel images, or initramfs images. Existing boot configuration is outside project authority and must remain unchanged.
- Never configure, build, install, replace, remove, or deploy a kernel or initramfs. Kernel lifecycle is human-only and outside the project; do not ask a human to perform it to satisfy this roadmap.
- Do not add or retain a project test, recovery prerequisite, acceptance condition, or evidence requirement involving boot-entry state. A supposedly temporary, inactive, one-shot, rescue, known-good, or order-neutral entry is still forbidden.
- Stop before any automated package transaction that would perform kernel, initramfs, bootloader, EFI, or boot-entry work, and assign the affected lifecycle item the evidence-backed terminal reason `kernel-policy-exclusion`, denoting the human-only system boundary.
- Never apply a BOLT output when build ID or `.text` identity checks fail.
- Never reuse PGO raw data across incompatible compiler profile formats.
- Never apply Clang profile flags to GCC or Rust builds by accident.
- Never apply generic userspace PGO flags to kernel or kernel-module builds; neither is an automated project optimization lane.
- Stop the affected package lane on correctness failure, restore the known-good binpkg, document the failure, and remediate before continuing.

## 2.4 Preserve reproducibility

Every generated profile and BOLT output must be associated with:

- CPV and repository;
- SLOT and SUBSLOT;
- ABI and CHOST;
- compiler executable and complete version;
- compiler profile format family;
- ebuild SHA-256;
- USE flags;
- relevant Portage environment flags;
- source/distfile identity when available;
- input ELF GNU build ID;
- input ELF `.text` SHA-256;
- workload set version;
- profile generation ID;
- date and host identity.

## 2.5 Keep repository changes reviewable

- Work on a dedicated Git branch.
- Commit at the end of every major phase.
- Keep generated machine state, raw profiles, perf data, cached binaries, and logs out of Git.
- Keep policies, schemas, scripts, workload definitions, documentation, and stable package decisions in Git.
- Run `shellcheck` on shell scripts and syntax/format validation on JSON, YAML, and Python before committing.

---

# 3. Starting repository defects and their resolution status

These were defects in the project's starting framework. The ledger below
distinguishes repository implementation from live Candidate-A validation and
Candidate-B authorization. Repository-resolved does not authorize Phase 2,
activate an optimization generation, or permit a system-wide rebuild.

| Starting defect | Repository implementation | Live/authorization status |
| --- | --- | --- |
| Mixed Clang IR and sample-profile handling | Resolved by the distinct `merged.profdata`/`sample.prof` stores, validators, states, and consumers in §11.4. | Open: all four §11.4 live sample-PGO claims remain unchecked. |
| Compiler-family leakage | Resolved by the fail-closed backend/ABI dispatcher in §11.1–§11.2. | Open: the exact installed Candidate A and frozen Candidate B must pass the complete live gate. |
| Weak `${CATEGORY}/${PN}` profile key | Resolved by the versioned package/compiler/ABI/build fingerprint contract in §7.1 and §11.2. | Open: live Candidate A/B proof remains required. Independently observing and hashing the exact ebuild used by each installed build remains Phase 3 provenance work. |
| Clang receiving GCC correction flags | Resolved; profile correction is confined to the GCC lane. | Open: current installed-candidate live revalidation is required. |
| BOLT operating on installed stripped files | Resolved in the cached exact-input, registration, and pre-strip `${ED}` hooks in §11.5–§11.6. | Open: the current exact candidate must pass live capture/deployment and four-class BOLT gates. |
| Globally enabled BOLT/sample readiness | Resolved by the stage-only policy in checked §11.3. | Open: the installed candidate must prove ordinary/off builds are clean and stage assignments are exact. |

## 3.1 Remove mixed instrumentation/sample-profile handling

At project start, the sample-profile converter wrote an `llvm-profgen` sample
profile to a file named `merged.profdata`, while the global hook consumed that
file with `-fprofile-use`. Instrumentation and sample profiles are different
formats and must use different compiler options.

Required replacement:

```text
Clang IR instrumentation:
    raw/*.profraw
    merged.profdata
    -fprofile-generate
    -fprofile-use

Clang sample PGO:
    perf/*.data
    sample.prof
    -fprofile-sample-use
    -fsample-profile-use-profi
```

The new implementation must use distinct directories, file names, states, validators, and flags.

## 3.2 Remove compiler-family leakage

At project start, a global rule enabled LLVM `-fprofile-use` for every package
based only on a file existing. Its replacement must choose a backend after
detecting the package's actual compiler lane:

```text
clang-ir
gcc-gcov
rust-llvm-ir
go-pprof
ebuild-native
clang-sample
not-applicable
```

A Clang indexed profile must never be passed to GCC. A Rust profile must be tied to the exact `rustc`/LLVM profile format. Generic C/C++ flags must not be added to `FCFLAGS` or `FFLAGS` unless a separate tested Fortran lane is implemented.

## 3.3 Replace the current weak profile key

At project start, the path based only on `${CATEGORY}/${PN}` allowed different
versions, slots, ABIs, compilers, and configurations to collide. It must remain
absent.

Use a generation-aware fingerprint described in section 7.

## 3.4 Remove Clang-incompatible profile flags

Do not use `-fprofile-correction` in the Clang path. Keep GCC correction behavior only inside the GCC lane.

## 3.5 Move BOLT from installed stripped files to cached exact inputs and pre-strip deployment

At project start, the scripts processed `/usr/bin/...` after Portage
installation and wrote test copies under `/opt/bolt-test`. That was acceptable
only as an early experiment. The final pipeline must:

1. capture the exact unstripped PGO-built ELF from `${ED}` during `post_src_install`;
2. train the corresponding installed PGO binary;
3. run `perf2bolt` and `llvm-bolt` against the cached unstripped exact input;
4. verify input build ID and `.text` hash;
5. during the final package rebuild, replace the matching file inside `${ED}` with the prepared BOLT output;
6. allow Portage to perform its normal splitdebug/strip/binpkg/deployment handling afterward.

## 3.6 Make BOLT readiness stage-aware

At project start, global flags added line tables, sample mapping metadata,
relocation sections, optimization records, and section splitting to every
package at all times. They must remain separated into independent stage
profiles:

```text
profile-map-ready.conf
bolt-capture-ready.conf
bolt-gcc-ready.conf
pgo-*-generate.conf
pgo-*-use.conf
bolt-deploy.conf
```

Only the stages and packages that need a flag should receive it. The final requirement remains exhaustive, but unrelated profile mechanisms must not be conflated.

---

# 4. Required repository layout

Create this structure. Adapt naming only when there is a strong repository convention requiring it.

```text
plans/
└── system-wide-pgo-bolt.md

optimization/
├── README.md
├── policy.yaml
├── exclusions.yaml
├── package-overrides.yaml
├── schema/
│   ├── package-state.schema.json
│   ├── artifact-state.schema.json
│   └── workload.schema.json
├── workloads/
│   ├── common/
│   ├── app-arch/
│   ├── app-shells/
│   ├── dev-lang/
│   ├── dev-libs/
│   ├── media-libs/
│   ├── media-video/
│   ├── net-misc/
│   ├── sys-apps/
│   └── ...
└── fixtures/

scripts/optimization/
├── lib/
│   ├── common.sh
│   ├── portage.sh
│   ├── elf.sh
│   ├── profile.sh
│   └── state.py
├── inventory/
│   ├── inventory-installed.sh
│   ├── inventory-artifacts.sh
│   ├── detect-build-backend.py
│   ├── build-reverse-deps.py
│   └── generate-portage-sets.py
├── pgo/
│   ├── profile-key.sh
│   ├── prepare-generation.sh
│   ├── merge-clang-ir.sh
│   ├── merge-gcc.sh
│   ├── merge-rust.sh
│   ├── validate-profile.py
│   ├── generate-package-env.py
│   └── report-profile-coverage.py
├── train/
│   ├── run-workload.sh
│   ├── run-all-workloads.sh
│   ├── run-system-session.sh
│   ├── train-library-closures.py
│   └── validate-workloads.py
├── bolt/
│   ├── capture-input.sh
│   ├── inventory-candidates.sh
│   ├── collect-system-profile.sh
│   ├── convert-profile.sh
│   ├── merge-fdata.sh
│   ├── optimize-candidate.sh
│   ├── validate-output.sh
│   └── generate-deployment-manifest.py
└── verify/
    ├── verify-package-pgo.py
    ├── verify-installed-bolt.py
    ├── verify-runtime.sh
    └── verify-coverage.py

portage/env/optimization/
├── pgo-clang-ir-generate.conf
├── pgo-clang-ir-use.conf
├── pgo-clang-sample-use.conf
├── pgo-gcc-generate.conf
├── pgo-gcc-use.conf
├── pgo-rust-generate.conf
├── pgo-rust-use.conf
├── pgo-go-use.conf
├── bolt-capture-ready.conf
├── bolt-gcc-ready.conf
├── bolt-deploy.conf
└── optimization-off.conf

portage/package.env/
├── 50-pgo-generated
├── 51-bolt-capture-generated
└── 52-bolt-deploy-generated
```

Generated `package.env` files may be checked into Git only if they contain stable reviewed policy. Machine-specific CPV fingerprints and temporary generation IDs belong under `/var/lib/gentoo-optimization`, not Git.

---

# 5. Live state directory layout

Create persistent and temporary state with explicit ownership and permissions.

```text
/var/lib/gentoo-optimization/
├── inventory/
├── state/
├── reports/
├── deployment/
├── generations/
└── locks/

/var/cache/gentoo-optimization/
├── pgo/
│   ├── clang-ir/
│   ├── clang-sample/
│   ├── gcc/
│   ├── rust/
│   ├── go/
│   └── ebuild-native/
├── bolt/
│   ├── inputs/
│   ├── perf/
│   ├── fdata/
│   └── outputs/
├── build-logs/
└── binpkgs/

/var/tmp/gentoo-optimization/
├── pgo-raw/
├── perf/
├── workloads/
└── staging/
```

Requirements:

- `/var/lib/gentoo-optimization` and `/var/cache/gentoo-optimization` must be root-owned and not generally writable.
- The active Clang/Rust runtime raw-profile generation directory may need mode `01777` so desktop applications and service users can emit unique `%p` profile files. Limit this permission to the exact generation-specific raw directory and remove the broad write permission after training.
- Add only the required paths to Portage sandbox configuration.
- Never make the complete profile cache world-writable.
- Store a `generation.json` file in every generation directory.

---

# 6. Package and artifact state model

Create one package state file per installed CPV and one artifact record per owned machine-code file.

Minimum package record:

```json
{
  "cpv": "category/package-version-rN",
  "cp": "category/package",
  "repository": "gentoo",
  "slot": "0",
  "subslot": "0",
  "abis": ["amd64", "x86"],
  "ebuild_sha256": "...",
  "use_flags": ["..."],
  "build_backend": "clang-ir",
  "compiler": {
    "path": "/usr/bin/clang",
    "version": "...",
    "profile_format": "llvm-ir-v..."
  },
  "fingerprint": "sha256:...",
  "pgo": {
    "eligibility": "eligible",
    "mode": "clang-ir",
    "generation_id": "...",
    "profile_path": "...",
    "profile_valid": true,
    "build_verified": true,
    "status": "optimized"
  },
  "bolt": {
    "candidate_count": 4,
    "optimized_count": 4,
    "excluded_count": 0,
    "status": "optimized"
  },
  "final_status": "optimized",
  "notes": []
}
```

Minimum artifact record:

```json
{
  "owner_cpv": "category/package-version-rN",
  "installed_path": "/usr/bin/example",
  "canonical_path": "/usr/libexec/example-real",
  "elf_class": 64,
  "elf_type": "DYN",
  "machine": "Advanced Micro Devices X86-64",
  "abi": "amd64",
  "build_id": "...",
  "text_sha256": "...",
  "has_symbols": true,
  "has_text_relocations": true,
  "setuid": false,
  "file_capabilities": [],
  "bolt_eligibility": "eligible",
  "bolt_profile_samples": 12345,
  "bolt_profile_stale_percent": 0.0,
  "bolt_output_path": "...",
  "installed_has_bolt_note": true,
  "status": "optimized"
}
```

Every terminal exclusion must contain:

```json
{
  "reason_code": "not-machine-code",
  "evidence": ["file output...", "readelf output..."],
  "reviewed": true
}
```

---

# 7. Build identity and generation keys

## 7.1 Create a stable package fingerprint

Implement `scripts/optimization/pgo/profile-key.sh` or an equivalent Python tool. Hash a canonical, sorted representation of:

```text
CATEGORY
PF
SLOT
SUBSLOT
repository
EBUILD SHA-256
EAPI
CHOST
ABI
active compiler realpath
active compiler complete --version output
LLVM major or GCC major/profile format
USE flags
CFLAGS
CXXFLAGS
LDFLAGS
RUSTFLAGS
GOFLAGS
FEATURES that affect output
selected package.env files
relevant EXTRA_ECONF/EXTRA_EMESON/EXTRA_ECMAKE values
```

Do not include volatile timestamps.

The tool must emit both a human-readable metadata file and a SHA-256 key.

`FEATURES` is not an unordered token list. Its fingerprint identity is the
effective Portage last-token-wins state, canonicalized by feature name after
applying every positive and negative token. Raw token sorting is forbidden
because it can collapse opposite effective policies into one build identity.

## 7.2 Create a system optimization generation ID

A system-wide run must have a generation ID derived from:

```text
inventory hash
active package versions
compiler versions
profile policy revision
workload suite revision
CHOST
CPU architecture
Git commit of Gentoo-stuff
```

Example shape:

```text
2026-07-10-clang22-amd64-<12-char-hash>
```

The ID is descriptive; the full hash remains authoritative.

## 7.3 Keep profile families separate

Never merge raw profiles from incompatible profile runtimes.

Required top-level separation:

```text
clang-ir/<clang-major>/<generation>/<abi>/
rust/<rustc-version>/<bundled-llvm-major>/<generation>/<abi>/
gcc/<gcc-major>/<cpv>/<fingerprint>/<abi>/
go/<go-version>/<cpv>/<fingerprint>/<binary>/
clang-sample/<clang-major>/<cpv>/<fingerprint>/<build-id>/
```

---

# 8. Eligibility matrix

The classifier must assign every installed package and artifact to one of these lanes.

| Package/artifact type | PGO lane | BOLT lane |
|---|---|---|
| Clang-built C/C++ executable or DSO | Clang IR-PGO by default; sample PGO fallback | Eligible when x86-64 ELF requirements pass |
| Clang-built static library | Clang IR-PGO; train through linked consumers | Archive itself not BOLTed; final linked consumers are candidates |
| GCC-built C/C++ | ebuild-native PGO if available; otherwise GCC gcov PGO | Eligible with `-fno-reorder-blocks-and-partition` and all BOLT checks |
| Rust binary/DSO | Rust LLVM instrumentation PGO | Candidate only after exact ELF validation and package tests |
| Go main binary | Go pprof PGO | Experimental candidate; require package-specific correctness validation |
| Pure Python/shell/Perl/Ruby package | Not applicable; optimize interpreter and native extensions | Not applicable unless package also owns eligible ELF |
| Python/Ruby/etc. native extension | Native compiler lane | Candidate if it is a supported x86-64 DSO and validation passes |
| Java/JVM bytecode package | Not applicable to bytecode; optimize JVM/native libraries | Native launcher/JNI DSOs may be candidates |
| Kernel image | Project-excluded; `kernel-policy-exclusion` human-only boundary | Project-excluded; never modify the kernel or boot chain |
| Kernel module | Project-excluded from automated kernel lifecycle work; `kernel-policy-exclusion` | Generic BOLT not applicable |
| eBPF, SPIR-V, AMDGPU, firmware | Toolchain-specific optimization only | Not applicable |
| 32-bit x86 ELF | PGO in separate `x86` ABI lane if compiler supports it | Not applicable because BOLT lane is x86-64 only |
| Binary-only package | No rebuild PGO unless upstream binary already proves it | Do not rewrite unless source/reproducible package integration exists |
| Headers, fonts, themes, docs, metadata, data | Not applicable | Not applicable |

The classifier must examine installed contents rather than relying only on package category or ebuild language.

---

# 9. Phase 0 — Create the project, backup, and rollback base

## 9.1 Create and initialize the plan

- [x] Add this document to `plans/system-wide-pgo-bolt.md`.
- [x] Create a dedicated Git branch such as `feat/system-wide-pgo-bolt`.
- [x] Record the starting repository commit.
- [x] Add a progress summary at the top of this document.

## 9.2 Capture the live configuration

- [x] Archive live `/etc/portage` separately from the repository copy.
- [x] Record `emerge --info`.
- [x] Record `eselect profile show`.
- [x] Record `clang --version`, `ld.lld --version`, `llvm-profdata --version`, `llvm-profgen --version`, `llvm-bolt --version`, `perf --version`, `gcc --version`, `rustc -vV`, `cargo -V`, and `go version` where installed. (`llvm-bolt`/`perf2bolt` absence is explicitly recorded.)
- [x] Record the running-kernel release and configuration read-only as userspace host context; this is not kernel lifecycle authority.
- [x] Record filesystem free space for `/var/tmp`, `/var/cache`, `/var/lib`, and the binpkg location.
- [x] Record current `@world`, custom sets, and installed CPVs.

## 9.3 Create known-good package recovery artifacts

- [x] Run a full binary-package backup or `quickpkg` snapshot for installed packages.
- [x] Copy critical bootstrap binpkgs to a directory that normal binpkg cleanup will not remove.
- [x] Include at least Portage, Python, libc, libgcc/compiler-rt, libunwind, libc++, shell, coreutils, tar, xz, zstd, rsync, OpenRC, PAM, util-linux, grep, sed, awk, findutils, Clang/LLVM, GCC/binutils, and filesystem tools.
- [x] Verify restoration of one non-critical package from the snapshot before proceeding.

### Phase 0 evidence and decisions

- Live evidence is rooted at `/var/lib/gentoo-optimization`; caches and recovery artifacts are under `/var/cache/gentoo-optimization`. The Phase 0 top-level roots were root-owned mode `0755`; security-sensitive descendants deliberately use stricter `0700`, `0750`, `0640`, `0440`, and `0550` policies. No profile pool is world-writable.
- A Phase 2 root-trust audit on 2026-07-13 found that the live filesystem root and `/etc` had drifted to root-owned mode `0777`, which made any root-owned `/etc/portage` publication replaceable by an unprivileged local user. Both were immediately restored to the standard mode `0755` before any framework publication. The root-owned before/after report is `/var/lib/gentoo-optimization/reports/phase-2-root-trust-ancestor-remediation-20260713.log` (SHA-256 `054da0a00e57c3951f2cc4cfb1ee486147e5c4d8c8cbb3ef0a18464d498b58a4`); project state is `/var/lib/gentoo-optimization/state/project/root-trust-ancestor-remediation.json` (SHA-256 `51cf57b9378dad41ef7cac29098d9fdfe248e6fdaffc85dfb7cc62884c9eddf5`) with zero pending, unknown, or failed items for this remediation. The framework installer now treats every root-owned destination ancestor as a fail-closed trust condition; this remediation does not replace its per-run checks.
- At Phase 0, `/etc/portage` was a live symlink to this repository's `portage/` tree; the starting archive is `/var/lib/gentoo-optimization/reports/phase-0-live-etc-portage.tar.zst` with SHA-256 `1f4c812aa2c26e700f4181d3bea550266ec0aa6d4433b41feb318b6d108e4b1f`. It now resolves through `/var/lib/gentoo-optimization/framework-current/portage`; the selected installed candidate predates Candidate A and cannot validate the current source revision.
- Historical EFI, boot-entry, kernel-image, and initramfs work is excluded from current project authority and is not a prerequisite, gate, recovery mechanism, or completion claim. It must not be repeated, refreshed, validated as a project test, or used to direct an agent. Existing boot configuration must remain unchanged.
- At capture, `/var/tmp`, `/var/cache`, `/var/lib`, and `PKGDIR=/var/cache/binpkgs` share the root XFS filesystem with 213,653,905,408 bytes available. Existing binpkgs consume 13 GiB and distfiles 40 GiB; later preflight must account for snapshot/profile/BOLT growth.
- The protected full snapshot is `/var/cache/gentoo-optimization/binpkgs/snapshot-20260710` (root-owned mode `0700`, 7.4 GiB). Its `Packages` index contains exactly 1,181 unique CPVs; set comparison against live `/var/db/pkg` reports zero missing and zero extra CPVs, and `emaint -c binhost` passes. A second archive-level verifier checked all 1,181 indexed outer GPKG manifests, hashes, sizes, and embedded `image.tar.zst` streams with zero missing, extra, unindexed, or failed records. Its machine-readable evidence is `phase-0-binpkg-payload-verification.json` (SHA-256 `3a64e7ded1deb7c00f05bd75f1bc9c8471159f0774b7d663966cf377d74f09d7`). Configuration files were deliberately excluded from quickpkg and are covered separately by configuration archives.
- The durable critical recovery copy is `/var/lib/gentoo-optimization/recovery/binpkgs/critical-20260710` (root-owned mode `0700`), created with XFS copy-on-write reflinks and exposed through the root-owned `critical-current` link. It is outside normal Portage/cache cleanup scope, has the same clean 1,181-record index, and currently retains the complete snapshot rather than a narrow subset. Verification explicitly found all 58 installed CPVs spanning the required bootstrap/toolchain/filesystem package families; no required CPV was absent. Evidence is in `phase-0-critical-binpkg-verification.log` and `phase-0-persistent-critical-binpkgs.log`.
- `app-admin/ps_mem-3.14-r1` was actually reinstalled from the protected snapshot with `--usepkgonly --getbinpkg=n --nodeps`; Portage reported one binary reinstall and zero downloads. `equery check` passed all 16 files both before and after, and the restored command's smoke test passed. Evidence is in `phase-0-binpkg-restore-pretend.log` and `phase-0-binpkg-restore-test.log`.
- `thermald` was removed from the default runlevel after a foreground diagnostic proved that it exits with `Non mobile platform` on this desktop i5-10600K. This is a technically justified service-policy correction, not a hidden boot failure; evidence is `phase-0-thermald-service-remediation.log`.
- Primary userspace evidence logs include `phase-0-state-layout.log`, `phase-0-portage-archive.log`, `phase-0-system-toolchain-info.log`, `phase-0-absolute-toolchain-versions.log`, and `phase-0-filesystem-capacity.log` in `/var/lib/gentoo-optimization/reports`, plus starting world/set/CPV records in `/var/lib/gentoo-optimization/inventory`. Any retained historical EFI/kernel/boot artifacts are not current project evidence and must not be acted upon.
- Full snapshot construction and coverage evidence is in `phase-0-full-quickpkg-snapshot.log` and `phase-0-full-snapshot-coverage.log`.
- [x] Create and restoration-test a second exact current-system checkpoint before Phase 2 while retaining the immutable 1,181-CPV baseline.
- The new cache checkpoint `/var/cache/gentoo-optimization/binpkgs/snapshot-pre-phase2-20260712` and durable copy `/var/lib/gentoo-optimization/recovery/binpkgs/critical-pre-phase2-20260712` are root-owned mode `0700`. Each independently verifies 1,217 indexed/live CPVs, 1,217 outer GPKG manifests, and 1,217 embedded zstd payload streams with zero missing, extra, unindexed, archive, or payload failures. `critical-current` was atomically retargeted only after both passes; both original 1,181-CPV generations remain intact. An actual offline `--usepkgonly` restoration of `app-admin/ps_mem-3.14-r1` selected one binary reinstall and zero downloads, retained the command hash/version, passed `equery check`, and was followed by a third 1,217/1,217 verifier pass. Evidence is `/var/lib/gentoo-optimization/reports/phase-1-pre-phase2-checkpoint-20260712` (manifest SHA-256 `150857821814fc01659c99c24c07d1d9e7bfb63cd70918facd12f82ad0c4e0a5`); state is `/var/lib/gentoo-optimization/state/project/pre-phase2-binpkg-checkpoint.json` (SHA-256 `af0fc3ba431925ec37b4872353ab3c78231e0df2bed0cc2fde1a121ad1332de2`).

## 9.4 Establish a rollback command file

Create and test a documented recovery sequence that can:

1. disable all optimization package.env files;
2. restore critical binpkgs;
3. rebuild preserved userspace libraries.

Do not proceed until the package-state rollback path has been tested. It must
not read or mutate boot entries as a project gate, touch EFI/boot assets, or
perform kernel/initramfs work.

- [x] The package-state rollback sequence is restoration-tested: all 1,181 protected package records were validated, and `app-admin/ps_mem-3.14-r1` was actually restored offline from the snapshot. Historical boot-chain behavior is excluded and is not part of this completion claim.
- [x] The recovery kill switch preserves the installed C++ ABI through separate conservative lanes. Its Clang lane retains libc++, LLD, compiler-rt, and libunwind while clearing project PGO/BOLT/LTO/Polly/OpenMP/visibility axes; its GCC lane retains GCC/binutils and libstdc++. The userspace fixture compiles, links, and runs both C++ programs, verifies `libc++.so.1` with no `libstdc++` for Clang and `libstdc++.so.6` with no `libc++` for GCC, and repeats every live `gcc.conf` selector after the global Clang assignment. Boot-entry and kernel cases are intentionally outside the fixture and project.

---

# 10. Phase 1 — Validate hardware and tool capabilities

## 10.1 Validate perf branch-stack support

- [x] Confirm the i5-10600K exposes usable Intel LBR support.
- [x] Run a small `perf record -e cycles:u -j any,u` test.
- [x] Confirm `perf report` contains branch-stack data.
- [x] Confirm kernel permissions permit the required system-wide and user-space profiling.
- [x] Record any temporary `perf_event_paranoid` changes and restore policy after profiling.

### Phase 1.1 evidence

- The i5-10600K is Intel family 6 model 165; the CPU PMU reports `pmu_name=skylake` and `branches=32`, and boot diagnostics report `Skylake events, 32-deep LBR`. The active kernel has `CONFIG_PERF_EVENTS=y`.
- Exact user capture with `perf record -e cycles:u -j any,u` produced 11,443 samples; all 11,443 carried decodable branch stacks, with 366,145 entries and a maximum depth of 32. `perf report` decoded the fixture's `main`/`mix0`–`mix3` branch pairs with zero lost samples.
- An unprivileged system-wide user-space capture produced 51,673 samples with 1,643,410 decoded branch entries and maximum depth 32, proving the permissions needed for the later `-a -e cycles:u -j any,u` sessions.
- `perf_event_paranoid=-1` and `kptr_restrict=0` were unchanged before, during, and after validation; no temporary sysctl change occurred. The nonfatal perf metadata/libbpf and absent `/proc/schedstat` warnings did not affect branch capture or decoding. Evidence and checksums are under `/var/lib/gentoo-optimization/reports/phase-1-perf-lbr`; `validation-summary.log` reports `result=PASS`.
- The complete perf/LBR evidence tree is now `root:root`; every directory is mode `0755`, no non-root entry remains, and a before/after content-manifest hash proves that ownership remediation did not change any evidence payload. The audit is `/var/lib/gentoo-optimization/reports/phase-1-perf-lbr-ownership-remediation.log` (SHA-256 `fa31e74d9f9b4c8d68b9850b487c15deec8cc5322151b626055fd6b98358fc58`); the updated capability state is `/var/lib/gentoo-optimization/state/capabilities/perf-lbr.json` (SHA-256 `aab12248c34b91cf8578c0edb11d5e37c8c8c192e804caccec1cdb36b8e076df`).

## 10.2 Validate Clang IR-PGO

Build a small multi-file executable and DSO using the active Clang with:

```text
-fprofile-generate
```

Run it, merge its profiles with `llvm-profdata`, rebuild with:

```text
-fprofile-use=<absolute-profile>
```

Verify that:

- [x] raw profiles are written by multiple processes;
- [x] the merged profile is readable;
- [x] the final link succeeds with the system ThinLTO setup;
- [x] profile mismatch diagnostics are visible and not blindly suppressed.

### Phase 1.2 evidence

- The active Clang/LLD/`llvm-profdata` 22.1.8 toolchain built a multi-translation-unit executable and DSO in both generation and use modes while retaining the live ThinLTO/unified-LTO axes. Six concurrent processes produced 12 nonempty `%m-%p.profraw` files across two instrumented module signatures.
- The merged LLVM IR profile contains 18 functions and has SHA-256 `092e0fbe18323e052de45a1d9bbe5abb75284155220ddeda1009f83a9f06bea0`. The profile-use DSO and executable have SHA-256 `242bb932df23d83b0ec2296cba14dd44950bb2bb0e2faeace1fe1e36c45df4ee` and `3735c1f3f73912cd68316747638c22a25e7d78ec827fa86a1fe25159cb19fda2`; the embedded LLVM LTO section and four cache entries prove the final link used the intended path.
- A deliberate control-flow mismatch failed with exit 1 and the visible backend-plugin hash-mismatch diagnostic. Repeated runs retained stable command, summary, merged-profile, and final-output identities. The fixture passes `bash -n`, ShellCheck 0.11.0, its evidence checksum manifest, and functional tests.
- Authoritative evidence is under `/var/lib/gentoo-optimization/reports/phase-1-clang-ir-pgo`; the independently produced superseded run is retained beside it. The capability record is `/var/lib/gentoo-optimization/state/capabilities/clang-ir-pgo.json` (SHA-256 `a809b6353a5ec3558a65ea27a55f406666c5ece921e7e5167a101f8677ad3a8a`).

## 10.3 Validate sample PGO

- [x] Build a sample-mapping-ready test binary.
- [x] collect `perf.data` with branch stacks;
- [x] convert it with `llvm-profgen`;
- [x] rebuild with `-fprofile-sample-use` and `-fsample-profile-use-profi`;
- [x] verify the profile is not accepted through `-fprofile-use`.

### Phase 1.3 evidence

- A two-translation-unit PIE built with ThinLTO, build ID, emitted relocations, line tables, unique internal names, and pseudo-probe/sample-profile mapping flags. `perf 7.1` recorded `cycles:u` with `-j any,u`; its metadata explicitly reports `BRANCH_STACK` and `USER|ANY`.
- `llvm-profgen` produced a readable five-function extbinary sample profile with SHA-256 `0a4c5b06ad86b12eb0512215f3ca0391d6195c10510b148984d894e073bda955`. The exact profile-use rebuild accepted both required sample-use flags and produced the same `dad2bb283c577f34` workload checksum as the training binary. Passing that profile to the IR-instrumentation consumer failed with the expected bad-magic diagnostic.
- The final hardened rerun emitted 38 retained diagnostics: `0.33% (59/17887)` of samples crossed function boundaries and `1.13% (203/17887)` had reversed or long ranges across an unconditional jump. This capability result is accepted because exact-binary conversion, profile parsing, profile use, and functional equality passed; these diagnostics remain inputs to the conservative Phase 10 profile-quality threshold rather than being hidden.
- Authoritative evidence is under `/var/lib/gentoo-optimization/reports/phase-1-clang-sample`; all earlier harness and pre-safety-review runs are retained under distinct report directories. The runner now refuses output outside `/tmp` and `/var/tmp/gentoo-optimization` and records its own SHA-256. The capability record is `/var/lib/gentoo-optimization/state/capabilities/clang-sample-pgo.json` (SHA-256 `e9014ea7d4e9ab8bc1b2c2f2b05421ead1f52483aaea34966f76b0698abc7f6c`).

## 10.4 Validate GCC PGO separately

- [x] Build a GCC test program with GCC generation flags.
- [x] train it and rebuild with GCC use flags.
- [x] verify the profile directory and correction/mismatch behavior.
- [x] confirm no LLVM profile file is involved.

### Phase 1.4 evidence

- The exact active `sys-devel/gcc-17.0.9999` compiler built a four-translation-unit PIE/DSO fixture with an absolute GCC-specific profile pool. Six training executions produced four `.gcda` files, each reporting `OBJECT_SUMMARY runs=6`; four IPA profile dumps prove that the use build read edge counts and made profile feedback available.
- Generation, normal profile-use, and corrected-profile workloads are functionally identical. The original profile pool remained byte-identical across use builds, and the final reviewed use executable/DSO have SHA-256 `5d6024bc6d7e4684fc5441b7ae70127c3151dba7ee41051a1927b68793943ed7` and `8eb0630ca44a71d112cc23d726609e610e3683419dbff29ed04d11422640c661`.
- A deliberately inconsistent `.gcda` failed without correction and succeeded only with GCC's visible `correcting inconsistent profile data` path. A changed control-flow hash and an empty absolute profile pool each failed with exit 1. `LLVM_PROFILE_FILE` was unset and no `.profraw`, `.profdata`, or `.prof` file participated.
- The authoritative report is `/var/lib/gentoo-optimization/reports/phase-1-gcc-pgo`; the agent-original and pre-ShellCheck root-review runs remain in separate superseded directories. The reviewed runner passes ShellCheck 0.11.0 and its exact source manifest. Live capability state is `/var/lib/gentoo-optimization/state/capabilities/gcc-pgo.json` (SHA-256 `98385f629ed427d045d1bc636da907ab5839c7949dc24ee96c344b3d6ddcfe9e`).

## 10.5 Validate Rust PGO

- [x] Build a small Cargo project with absolute `-Cprofile-generate` path.
- [x] use `--target` so build scripts are not instrumented accidentally;
- [x] train, merge with compatible `llvm-profdata`, and rebuild using `-Cprofile-use`;
- [x] enable missing-function warnings for validation.

### Phase 1.5 evidence and binding validator decision

- Active `rustc 1.99.0-nightly` commit `3659db0d3e2cd634c766fcda79ed118eca31a9fd` reports bundled LLVM 22.1.8, which was explicitly paired with `/usr/lib/llvm/22/bin/llvm-profdata`. Cargo received the explicit `x86_64-unknown-linux-gnu` target; recorded invocations prove target crates received the absolute instrumentation/use flags while the host build script received neither.
- Six concurrent processes produced six distinct `%m-%p.profraw` files. The compatible merger produced a readable 14-function profile with SHA-256 `b310ea88a5db5b8d0e7655c470c0d01f530d8cebfe3517ae9d57b9e8713b3dc3`; the profile-use build was diagnostic-clean and functionally matched the generation build.
- `-Cllvm-args=-pgo-warn-missing-function` was verified with a feature-changed build that emitted 14 visible missing-function warnings. Critically, this rustc returned success for both that mismatch and a malformed indexed-profile file even with `-Dwarnings`; the exact compatible `llvm-profdata show` rejected the malformed input with exit 1. Therefore Phase 2 must validate Rust indexed profiles externally and fail closed before invoking rustc—compiler exit status alone is not acceptable proof.
- The complete authoritative report is `/var/lib/gentoo-optimization/reports/phase-1-rust-pgo`; failed attempts 1–4 and superseded passes 5–6 are retained separately. Live capability state is `/var/lib/gentoo-optimization/state/capabilities/rust-pgo.json` (SHA-256 `069a12c2a89383552c5d2c7bb9f65e345824b658d065d70bf9f443e2e6a8d2ad`).

## 10.6 Validate Go PGO

- [x] Build a small Go command.
- [x] generate a CPU pprof workload profile.
- [x] rebuild with an explicit absolute `-pgo=<profile>` path.
- [x] verify the build log proves PGO was enabled.

### Phase 1.6 evidence and binding validator decision

- Active `go1.27-devel_e37f65a2e6` built a dependency-free baseline command and collected a 56-sample CPU pprof profile. The profile's mapping matches the exact baseline path and available GNU build ID `1d50ccfd0a3af949c79e4974722bfda4380309b3`, and the decoded data contains target workload symbols, nonzero function start lines, and reconstructed inline frames. The language-native `go tool buildid` identity is recorded separately.
- The use build consumed the explicit absolute `-pgo=/tmp/phase-1-go-pgo-20260711T-buildid-reviewed/cpu.pprof` input. Its trace contains the workload package's processed `-pgoprofile` compiler argument and a profile-driven hot-node budget decision; `go version -m` retains the exact absolute PGO build setting. Baseline and use outputs match, and the use binary has SHA-256 `ad6672ec98c937d27eb4821f6f1e71c5f260b96383a576e0eba302649e5b5cb9`.
- Malformed pprof data failed with exit 1. Critically, a structurally valid profile from the unrelated fixture command was accepted by the compiler with exit 0 even though it contained no target symbols. Phase 2 must therefore validate target symbols, function metadata, and available mapping/build identity evidence before enabling `-pgo`; Go compiler success alone is not profile-relevance proof.
- Go PGO eligibility now requires the Go build ID but accepts any supported nonempty hexadecimal GNU build-ID encoding when a GNU note exists; it does not require a GNU note at all. BOLT exact-input eligibility remains a separate stricter decision. The complete authoritative report is `/var/lib/gentoo-optimization/reports/phase-1-go-pgo`; the pre-review report, low-sample failure, and superseded passes are retained separately. Live capability state is `/var/lib/gentoo-optimization/state/capabilities/go-pgo.json` (SHA-256 `78dad9e807e22b89e393155bc7b7077681c0a18d615328c526cd0ae626c980e1`).

## 10.7 Validate BOLT on executable, PIE, and DSO fixtures

- [x] Install the package-managed `llvm-core/bolt-22.1.8` tools and verify their exact runtime identities and dependency closure.
- [x] Pass the fixed-address `ET_EXEC` fixture and preserve its required ELF, metadata, and functional properties.
- [x] Pass the PIE fixture and preserve its required ELF, metadata, and functional properties.
- [x] Pass the shared-object fixture and preserve its required ABI, ELF, metadata, and consumer behavior.
- [x] Record exact profiles, inputs, outputs, hashes, structured timeout rows, and machine-readable zero-pending capability state.

For each fixture:

- link with symbols, build ID, and `--emit-relocs`;
- collect branch-stack data;
- run `perf2bolt`;
- run `llvm-bolt`;
- verify `.note.bolt_info`;
- run functionality tests;
- compare ownership, mode, xattrs, and dynamic dependencies.

Do not implement the live deployment hook until all fixture classes pass.

### Phase 1.7 packaging and capability completion evidence

- `pkgcheck 0.10.40` reports no findings for `pkgcheck scan --repo codex-local llvm-core/bolt` at tested code commit `a97a5dbcffdfd74b03ae151a0e77dee67aa0d6a2`. The exact ebuild, Manifest, and metadata hashes are retained in `/var/lib/gentoo-optimization/reports/phase-1-bolt-pkgcheck-20260712.log` (SHA-256 `c1ccc0995643ab8fe7e0b8541602f65e56f4db1ba412b6f98ae2e5a4e7b6563e`). This proves repository lint only; it is not evidence that the tools install or that any fixture class passes.
- The first explicit static-closure stage build was externally interrupted with exit 143 at Ninja step 876 of 1,825. Its root-owned log remains `/var/lib/gentoo-optimization/reports/phase-1-bolt-portage-stage-build-explicit-closure-20260712.log` (SHA-256 `35845dc05e0f45d40ddc5c893c32db148d59abe31b7209a6ccd1fbc67fd561ed`) with its status record beside it; no package was installed and no partial output is accepted. The superseded four-pending state is retained as `/var/lib/gentoo-optimization/reports/superseded-phase-1-bolt-running-state-20260712.json` (SHA-256 `1a62f9a480ad1a9022db2d32a3f3c0d456f4c05bd9a44e2d2606d067ae68aeb0`). The successful clean retry and current zero-pending capability state are recorded below.
- The cron-isolated clean retry completed all 1,825 Ninja steps plus install/package with exit 0. The build log SHA-256 is `b6f2b749e7c8122ce665047ff9b18258ecfec8533d436dadd38eee9c7c96d5c2` and status SHA-256 is `7ce78c5ccefbe5755a83c4b95da0b02437f9af5d7eff2a0637d0d44ef413a296`. Staged inspection proves both tools are ELF64 x86-64 PIE with IBT/SHSTK, NX stack, RELRO/NOW, resolved libc++ dependencies, and no RPATH/RUNPATH, `libLLVM`, or `libstdc++`; `perf2bolt` is the intended relative argv[0]-dispatch symlink. Evidence is `/var/lib/gentoo-optimization/reports/phase-1-bolt-retry-readonly-inspection-20260712.log` (SHA-256 `b8b58c1c580ee2f479130579caf4f54346f4b74abd6607912b6667e2a80d1a05`).
- The GPKG has SHA-256 `6ad2c8ee905cec697c0c99a306b870445b1432d4f8793fcca0d11ca0c77196f2`; Portage's GPKG implementation verified its Manifest and all nine extracted payload entries. The exact binary-only pretend selected one new package and zero downloads, and the exact binary-only install completed one of one. A root-only reflink is preserved under `/var/lib/gentoo-optimization/recovery/binpkgs/phase-1-bolt-20260712`. Live VDB ownership and file checks pass. Installed `llvm-bolt` has build ID `5d69c312203f61c9ae92fd7978f0cc2b5d78e6fc` and SHA-256 `6a5fc31e4c840586129125a4f6c0205089d9797d2d2f09f7e13d2fb1019dbcad`; `merge-fdata` has build ID `01479a9a4d5ab7ee1f8afca002d2cc78a634e178` and SHA-256 `9729d47832dd417902057642bcfb589eaee1454807065911aca35310031f1214`. Install and live-verification log SHA-256 values are `cc8f89ca36ab92e7b437292c07d3291093f443a7ce99de79b916d842b47e7ab8` and `c3233aed6d3c2aaf6b6c099fe611ec7d95fc6d19d1cf9b7321676f837370d13c`.
- Three fail-closed fixture attempts are retained rather than hidden. The first proved that placing LLVM tools before `/usr/bin` incorrectly shadows GNU `readelf`; the second rejected deprecated numeric `-icf=1`; the third proved LLVM 22's `-use-gnu-stack` workaround consumes the explicit `PT_GNU_STACK` header. The binding policy at tested code commit `bfebd22151f16a6e519cd39e5f55ff6529761b2c` is `-icf=safe` with `-use-gnu-stack` disabled. A direct control proved normal `strip --strip-unneeded` preserves `PT_GNU_STACK`, `.note.bolt_info`, `.text`, metadata, and functionality without that workaround.
- The exact validated layout policy is `-reorder-blocks=ext-tsp -reorder-functions=cdsort`. Authoritative `commands.log` rows prove that literal spelling ran for ET_EXEC, PIE, and DSO and is embedded in the retained BOLT notes. The Phase 11 default and explicit-output prototype now use `cdsort`; `hfsort+` is not treated as an equivalent tested spelling. A quick-suite static gate rejects `hfsort+`, invalid `cdfsort`, `-use-gnu-stack`, missing tokens, duplicates, and ordering drift.
- The authoritative driver reports 44 passes, zero failures, five explicit non-selected capability skips, and 49 unique rows. ET_EXEC, PIE, and DSO each use two profiles; all 27 timed stages completed with no timeout. Across six conversions, sample counts are 1,135–1,239, branch-stack entries are 36,314–39,634, ignored samples are zero, mismatch ratios are 1.2–1.3%, and out-of-range ratios are 0.5%. Every output and its stripped copy has a BOLT note and passes identity, GNU-stack/RELRO/CET, dependency, ownership/mode/xattr, functionality, and applicable DSO-export checks. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-bolt-authoritative-20260712` (manifest SHA-256 `13960d54c7fac63e8446f166f1a7b169ca5e73dc3e7355399d1415f86d0cd9f3`, results SHA-256 `361542af36ce5c4fd6870317660141fde0d50af6ce7446d51ad5b1f4c9778d84`, validation-summary SHA-256 `dc275b7ee9f005cd03deaecbaccf3578c3c4c08761c34d71f1d5ed26d80ca3b4`). Capability state is `/var/lib/gentoo-optimization/state/capabilities/bolt.json` (SHA-256 `ec3af0b43c087deb84d6ad4475c88f4ff9ca5412e02feaaab87126b1843021f4`) with `pending_total=0`, `unknown_total=0`, and `failed_total=0` for this fixture gate only.

## 10.8 Keep capability validation repeatable

- [x] Add a single top-level driver for shell syntax, ShellCheck, Python compilation/tests, recovery fixtures, and supported PGO/BOLT capability fixtures.
- [x] Make unavailable capability dependencies produce an explicit `SKIP: <reason>` rather than disappearing.
- [x] Test dependency preflight hermetically so an unavailable capability runner cannot execute accidentally.
- [x] Directly test successful and failed BOLT artifact transactions, timeout exits 124/137, missing/stale outputs, stdout publication, and exact structured status rows.
- [x] Clean an active partial and terminate its complete workload process group on outer `EXIT`/`HUP`/`INT`/`TERM`, while preserving published finals and unrelated partials.
- [x] Derive the timed-stage contract from a declared registry and reject unknown, missing, duplicate, or reordered evidence.
- [x] Bound every top-level test case in its own process group with TERM-to-KILL cleanup, recorded deadline metadata, and longer per-capability overrides.
- [x] Enforce the exact validated BOLT block/function layout spelling in every current command producer with a static drift test.

### Phase 1.8 evidence

- `tests/run-optimization-tests.sh` provides `quick` and `capabilities` modes plus explicit per-capability selection. It confines output to a new canonical absolute directory below `/tmp` or `/var/tmp/gentoo-optimization`, rejects unsafe path characters before creating anything, and preflights every external command used by the selected fixture.
- The non-recursive driver self-test proves canonical unsafe paths are rejected without creation and proves a dependency-incomplete BOLT lane emits exactly one reason-bearing skip, never invokes its stub runner, reports every missing dependency, and exits successfully with zero failure rows.
- The exact post-BOLT-fixture-hardening quick run reports 38 passes, zero failures, six explicit capability skips, and 44 unique result rows. It includes Bash syntax, Manifest-verified ShellCheck 0.11.0, Python source compilation, Python unit tests, the driver self-test, and both recovery fixtures. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-test-driver-post-bolt-fixture` (tree-manifest SHA-256 `b9a99cc9cc11c0caa0ce6590ceb07c95def85f7d90138746078b210b71ca319d`); ShellCheck provenance is `/var/lib/gentoo-optimization/reports/phase-1-shellcheck-0.11.0-provenance.log` (SHA-256 `a2e4885cd37d7ce70d45dfdbade644521d5bfa8f952c3c4333086c23f2909730`); capability state is `/var/lib/gentoo-optimization/state/capabilities/optimization-test-driver.json` (SHA-256 `2abc5fb04a7b3ea0d27adaa45293d5c4e2f7508a096c587410c1fe4710b98a78`).
- The BOLT transaction implementation is tested at exact code commit `7e23736c3779adb3c553c98841dc49fd9b8c145e`. Three independent direct-fixture repetitions cover atomic generated/stdout success, ordinary failure, synthetic deadline 124 and forced-kill 137, zero-exit missing output, stale final/partial cleanup, exact rows, all four running-stage outer signals, post-rename and post-status signal boundaries, and exact registry order/membership/uniqueness; zero workload process survived. Bash syntax, ShellCheck 0.11.0, and the CLI self-test pass. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-bolt-transaction-tests-20260712.log` (SHA-256 `aab59df8e5c7d28d1d91fd36cb6b1076f04b3233671b6d869db1bd9fbc876b4a`); state is `/var/lib/gentoo-optimization/state/capabilities/bolt-transaction.json` (SHA-256 `6b3467ba61fb30f4499903b341483bd49e14e19fec753e8ef3a37aaea098d35e`).
- The superseding authoritative quick run at exact code commit `7845ab44737e03057d56fffa4dae3903a65332f3` reports 43 passes, zero failures, six explicit capability skips, and 49 unique result rows. It additionally proves the subprocess-heavy unit tests run without `PYTHONPYCACHEPREFIX`, the live Portage semantic package-policy gate passes, the Clang/libc++ and GCC/libstdc++ recovery probes compile and execute, and the BOLT transaction fixture passes inside the top-level driver. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-review-final-quick-20260712` (evidence-manifest SHA-256 `fba97be4c59614910d9b2ef539b9ad23f4abf415829387f6f3418c33c7c82948`, summary SHA-256 `0984498be098e8006b142de8e78b96da23e4a72363d952c51125ac0f7d58adcf`, results SHA-256 `445b5ff069d624f8635f775b12184dde98d583cad6fa56aa5ca52fd8ec2e1715`). The then-current Phase 1 capability state was `/var/lib/gentoo-optimization/state/capabilities/optimization-test-driver.json` (SHA-256 `f51319cc8e8e0cf3c8f9c0547ace06bfe43693111cad7828f85da653b6b1f3e4`); the earlier 38-pass aggregate and state are retained only as historical evidence.
- The final Phase 1 boundary driver gives every case a default 1,800-second deadline and 10-second forced-kill grace, supports normalized per-capability overrides, and preserves the compatible three-column PASS/FAIL/SKIP results format. Its hermetic self-test runs a TERM-resistant runner and child, accepts only timeout exits 124/137 as the recorded failure, and proves neither process survives. The superseding root-owned quick run reports 45 passes, zero failures, six intentional non-selected capability skips, and 51 rows; ShellCheck 0.11.0, both recovery ABI lanes, 30 package-policy tests, the strict live Portage gate, the BOLT transaction fixture, and exact `ext-tsp`/`cdsort` policy all pass. Evidence is `/var/lib/gentoo-optimization/reports/phase-1-boundary-quick-20260712-authoritative` (manifest SHA-256 `c18e2c09c80fd4a30a6836e443a6332072de97482d2d33f6854330e893b9dfe2`, summary SHA-256 `f7240a8f19738a03ee06c26337c9eda67e26ad2d7bf5e7ff8b81f323ed09302d`, results SHA-256 `20deb0875f5c007e60f74685973bb5c3ce9c7592aff864242f4c265cc8133fd6`); the Phase 1 boundary driver-state SHA-256 was `6f92a9805d291d84050446af86b561e44d3763b2ffe6d8f3ba754d19b99a15d9`. The earlier aggregates remain historical evidence.

## 10.9 Keep the optimization branch reviewable

- [x] Remove broad plan-ignore rules and prove both plan paths remain visible to Git.
- [x] Preserve unrelated MangoHud/O2 remediation on a separate branch rather than carrying it in the optimization history.
- [x] Audit the Hyprland-related policy against the live VDB, repositories, installed ELFs, retained binpackages, and runtime before deciding whether it is prerequisite or unrelated work.
- [x] Collapse redundant Hyprland/hyprutils environment stacks and remove inactive `include-string.conf` and stale `qtutils` policy without changing the proven installed ABI lane.

### Phase 1.9 evidence

- `.gitignore` contains only targeted transient-file rules; `/plan.md`, `*plan*`, and `./plans/` are absent, and `git check-ignore` reports neither `plan.md` nor `plans/system-wide-pgo-bolt.md` as ignored.
- Unrelated MangoHud/O2 work is preserved at commit `1ec0f6563354956408c2468a05945c4a5be08c74` on `fix/mangohud-and-o2-remediation`; the active optimization `HEAD` contains zero MangoHud paths. Later user-owned Portage edits in the shared worktree remain untouched and are not part of this evidence claim.
- The root-owned audit is `/var/lib/gentoo-optimization/reports/phase-1-review-branch-hygiene.log` (SHA-256 `87ff7c7c749a866c8354b6c21648e45273d4013064d70be2168364ebd98d30cc`); state is `/var/lib/gentoo-optimization/state/project/review-branch-hygiene.json` (SHA-256 `880c4efeb5172ac335a37af691f031b0aa5cd6f5f5ae3c0a3953be5a3345eba5`).
- The Hyprland policy is a live buildability and ABI prerequisite, not an unrelated optimization experiment: 14 installed packages originate in hyproverlay, and the selected Hyprland requires glaze 7, which is unavailable in Gentoo but installed from that overlay. Across 21 audited installed packages, all 41 VDB-recorded ELFs exist and are `lddtree`-clean; 33 directly use libc++, none directly use libstdc++, and Hyprland, XDPH, and hyprpolkitagent were running. Retained binpackages prove severe hidden-visibility export loss: hyprutils 6 to 303/383 exports, hyprwire 4 to 269, iniparser 0 to 28, sdbus-c++ 2 to 420, and re2 0 to 497.
- The confusing stacks are resolved: hyprutils now has only the conservative `O2.conf` terminal tier and Hyprland only `O3-thin-lto-no-libs.conf`. Mandatory overlay, public-ABI, libde265, and XDPH policies remain; hyprutils promotion, muParser `-openmp`, and narrower executable-only XDPH/hyprpolkit lanes require controlled rebuild proof before the Phase 3 freeze. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-hyprland-prerequisite-audit-20260712` (`REPORT.md` SHA-256 `98f8ff892590f5ebb225e7f6e0a3c433afd1055f25e97c0bfac08220f5c662d2`, manifest SHA-256 `0f96e6e0eda8f626b43fe26489e25ffd5c46eb1ff8540ea0bc62df0ba04f28b1`); state is `/var/lib/gentoo-optimization/state/project/hyprland-prerequisite-scope.json` (SHA-256 `a1045b378fe33c38b03866b9c7ba7afd459f537bf72a1112a02a467b1ba4b36c`). This is prerequisite-scope evidence, not a Phase 2 fingerprint or PGO/BOLT coverage claim.

## 10.10 Validate exact package environment policy before fingerprints

- [x] Reject exact duplicate atom/environment pairs recursively across the package policy tree.
- [x] Reject missing or escaping environment paths, invalid or dead atoms, unreviewed effective overlaps, and forbidden recovery/generated/PGO/BOLT assignments.
- [x] Require exact ordered rationales for intentional multi-environment stacks and exact reviewed compiler profiles with complete, non-conflicting tool tuples.
- [x] Run the semantic validator against the live Gentoo Portage universe and make unavailable semantic validation an explicit reason-bearing skip elsewhere.
- [x] Preserve the three policy changes that alter live resolved flags as an explicit source-rebuild queue rather than claiming them complete from configuration inspection.
- [x] Fail closed when tracked compiler, flag, or stage-marker variables appear in unsupported shell constructs instead of silently ignoring them.
- [x] Complete and verify every queued source rebuild caused by changed effective policy, preserve its successful binpkg, and close reverse dependencies with zero unresolved failures.

### Phase 1.10 evidence

- At the Phase 1 boundary, the superseding strict live validator covered 13 policy files, 138 active lines, 136 exact atoms, 147 atom/environment pairs, 10 reviewed ordered multi-environment stacks, five reviewed compiler profiles, and all 1,217 then-installed CPVs. It rejects `export`, `+=`, multiple/chained/conditional assignments, command substitution, and `source`/dot-source whenever they can mutate tracked tools, `*FLAGS`, or stage markers. Portage semantic validation, 30 unit tests, plain mypy, and the boundary suite passed with zero duplicate, path, atom, overlap, stack, compiler, generated-stage, or unsupported-shell ambiguity. The later read-only 2026-07-16 universe contains 1,220 CPVs and must be revalidated by Candidate A.
- The exact source-only transaction rebuilt `dev-util/colm-0.14.7-r4`, `media-libs/svt-av1-4.1.0`, and `dev-util/ragel-7.0.4-r3` with three source emerges, three completed merges, and zero binary-use markers. Colm and Ragel used Clang with the active GCC 17 libstdc++ lane and no stale GCC-major pin. SVT-AV1 used `O2.conf` on x86 and amd64, and its ebuild-native generation, training encode, and `-fprofile-use` stages ran for both ABIs. Every owned-file check, runtime/version probe, SONAME/dependency/relocation check, and protected GPKG validation passed. Both SVT DSOs lacked GNU build IDs at the Phase 1 boundary; that is explicitly carried into Phase 2 as an exact-input/BOLT eligibility blocker, not misreported as BOLT-ready.
- The first post-rebuild `revdep-rebuild -ipv` exposed a pre-existing unversioned `libnewt.so` edge. A first source retry failed safely because global hidden visibility removed `newt_*`; a public-ABI retry restored 168 exports but was rejected because LLD was misdetected and the SONAME remained absent. The final versioned user patch recognizes LLD's GNU-linker compatibility. Installed `libnewt` now has build ID `b028f92f45fd04764cd120c5f4dc3b364fba3fa1`, SONAME `libnewt.so.0.52`, 168 exported functions, and `whiptail` names that versioned SONAME. `equery check`, runtime help, and the final full reverse-dependency scan pass. Failed attempts and the rejected binpkg remain preserved; successful state is `/var/lib/gentoo-optimization/state/project/newt-soname-remediation.json` (SHA-256 `ac3f676fa68db96975dae30b12ab88541056e2d46ab87f061480f5c4124030e4`).
- Root-owned queue evidence is `/var/lib/gentoo-optimization/reports/phase-1-package-policy-remediation-final-corrected-20260712.log` (SHA-256 `ddea59eaeeadf19756ea2b5c5304479680db6bea7334c62e9c740330ae52a61a`); all four successful remediation GPKGs have verified manifests and payload streams. The reviewed JSON policy SHA-256 was `92ee537829e44e9c2c0fe450baea98996dbff927f1ec6f989b6cf832fd10d611`, the hardened checker SHA-256 was `2e327641d74c83f2b63a26bdb8b175ff9eb0281df48ef9d6787e8b474780f5db`, and the Phase 1 boundary package-policy state was `/var/lib/gentoo-optimization/state/project/package-env-policy.json` (SHA-256 `9c7d565ca77b6d1e580e7566e79dabc07a0f53663bb8a8713d6a1f0b2f43e44c`) with `pending_total=0`, `unknown_total=0`, and `failed_total=0`.

## 10.11 Close the Phase 1 boundary

- [x] Preserve one exact root-owned state record covering every Phase 1 capability and closure gate with zero phase-local pending, unknown, or failed items.
- [x] Record the exact clean implementation boundary commit after all capability, recovery, package-policy, and strict test gates pass.
- [x] Confirm that Phase 1 completion does not establish an optimization generation, freeze the installed inventory, or claim installed-system PGO/BOLT coverage.

The clean implementation boundary is commit `f2b32d357dec78e19d707051480ab8525170704b`; progress-plan commit `b1962e581f68cfe5c9964fa5c9e93e62c8126424` follows it without changing the tested implementation. Aggregate state is `/var/lib/gentoo-optimization/state/project/phase-1.json` (SHA-256 `a0f9ec94a19e31537996559a641d18b75441bd89df721d278c7a180dcfdc021b`) with `pending_total=0`, `unknown_total=0`, and `failed_total=0`. This is a Phase 1 boundary claim only; Phase 3 inventory and final coverage totals remain unestablished.

---

# 11. Phase 2 — Refactor the repository framework
<!-- gentoo-optimization-phase2-prior-evidence: superseded-by-detached-index -->

### 2026-07-26 Candidate-A pre-activation checkpoint

- Candidate A is repository/pre-activation implementation only. The current
  branch head is the latest committed implementation revision; every later
  source correction creates a new candidate boundary. No Candidate-A revision has been
  installed as the live candidate or accepted by the complete host and
  production sample-PGO gates.
- No current detached Phase 2 evidence index exists. The four sample-profile
  items in §11.4, the combined automation gate, the framework live gate, and
  the evidence-index gate remain open.
- Phase 2 uses the non-circular two-pass procedure in
  `docs/phase2-production-profile-transaction.md`: commit and live-test
  Candidate A; update the plan and generated claim markers truthfully in
  Candidate B; reinstall B and rerun the entire authoritative gate under a new
  run ID. Only B's root-owned detached index can authorize the phase.
- No optimization generation or frozen inventory exists, and no installed
  package or ELF coverage claim follows from this repository checkpoint.

### 2026-07-29 Candidate-A repository correction checkpoint

- GitHub Actions run `30226349306` tested exact commit
  `1d77b16ada380ade6396c91e33907d7bc13942a9` and failed. The failure is retained
  as superseded diagnostic evidence, not a Candidate-A pass: CI used the
  distribution's ShellCheck 0.9.0 instead of the reviewed 0.11.0 identity, and
  two portable recovery cases overclaimed userspace emulation of kernel
  `unshare --pid --fork --kill-child=KILL` parent-death behavior.
- The current Candidate-A correction scope is intentionally bounded. It fixes the portable
  fake-`unshare` observation path without changing production procfs child
  observation, gives that adapter exact readiness and terminal receipts plus
  bounded same-session drain/rescan, early-watchdog, stalled-watchdog, inherited-
  signal, and post-terminal signal-race coverage, makes restored-content
  conversion-log fixtures use a guaranteed distinct inode and atomic
  replacement, confines raw fork/subreaper ownership to a dedicated
  single-threaded checkpoint test supervisor, separates the deterministic
  portable cleanup adapter from mandatory real-host containment proof, routes
  `git symbolic-ref` through the bounded hardened Git observer, and pins the CI
  ShellCheck 0.11.0 archive and executable by SHA-256. A non-root portable run
  now records the reviewed required skip when a complete live Portage policy is
  root-private; authoritative root execution must still prove that policy and
  cannot accept the skip.
- These corrections are not authorization. The dirty-tree contract check may
  bind the current test topology, but the final clean-commit contract, full
  portable boundary, green CI run for that exact commit, live recovery
  checkpoints, installed Candidate-A gate, supervised sample-PGO transaction,
  frozen Candidate B, and detached evidence index all remain pending. No §11.4
  or §11.7 checkbox is closed by this checkpoint.

### 2026-07-30 Candidate-A execution-identity closure checkpoint

- The test driver now starts through the exact `/usr/bin/bash` shebang and
  loads a reviewed execution core from the evidence policy and tool manifest.
  Authoritative mode requires the policy-declared
  `/usr/bin:/usr/lib/llvm/22/bin:/bin` PATH, invokes the reviewed `bash`, `env`,
  `git`, `python3`, `setsid`, `shellcheck`, `sleep`, and `timeout` entry points,
  and rejects a PATH shadow before any test case. Every reviewed manifest tool
  whose command name is its requested-path basename is resolved through that
  PATH and must select the exact requested entry point; this extends the check
  beyond the eight-tool execution core to utilities such as `readelf`, `nm`,
  `objcopy`, `strip`, `file`, and `cc`. Repository-root discovery uses Bash
  builtins and parameter expansion. Authoritative policy parsing uses the
  absolute reviewed `/usr/bin/python3` bootstrap before binding the complete
  execution core; portable parsing may use another PATH-selected Python but is
  non-authorizing and records the resulting execution identity. The active
  Bash command-line `argv[0]` must be the exact reviewed entry point;
  an inode alias such as `/bin/bash` is rejected. Gentoo's intentional
  `/usr/bin/git` and `/usr/bin/python3` symlink entry points remain the
  requested ABI while their resolved executable bytes are independently
  hashed. An authoritative ShellCheck override may name only the same reviewed
  manifest entry point; it cannot substitute another executable. A portable
  override remains non-authorizing and is recorded.
- Test-run provenance schema v2 records every selected execution-core entry
  point with requested-object lstat identity and symlink target, resolved path,
  version output, file SHA-256, and device/inode metadata. It separately
  records the actual Python runtime and binds the active driver Bash PID, start
  identity, ancestry, executable, and command-line entry point. Start and
  finish re-observe these identities; authoritative runs validate root-owned
  non-writable ancestry at both boundaries. Candidate-B component-state,
  capture, and verification commands independently require their active Python
  runtime to equal the final interpreter reached through the reviewed Python
  entry point; on this Gentoo host that distinguishes the `python-exec`
  dispatcher bytes from the actual `/usr/bin/python3.14` runtime. Git
  cleanliness observation is bounded and runs with system/global configuration
  and fsmonitor disabled.
  Capture and detached verification compare the finalized records with the
  independently observed reviewed tool manifest. An external or
  symlink-escaping evidence policy is rejected at provenance start.
- The checkpoint process supervisor now enumerates `/proc/self/task`
  immediately before its raw fork and fails closed unless the only native task
  is its own PID. The fake-namespace fixture no longer uses Python
  `preexec_fn`; a small executable parent-death wrapper performs the exact
  `PR_SET_PDEATHSIG`/parent recheck/exec sequence. Supervisor, process-group,
  and child receipts publish through private partial files, file fsync,
  `os.replace`, and parent-directory fsync. Focused regressions cover the
  single-task success path, deliberate multithreaded refusal before target
  execution, and refusal when the wrapper's expected parent is already wrong;
  the target never executes in either refusal case.
- The portable live-Portage policy lane no longer accepts a broad
  `PermissionError` substring. It recognizes only a versioned one-line record
  for the canonical root-owned `0600`
  `.gentoo-optimization-source-hash` observation (status 73), or the separately
  verified root-identity-remapped review boundary (status 74). Wrong paths,
  observations, modes, reasons, or additional stderr are rejected, and
  authoritative mode cannot accept either skip.
- Deterministic discovery now binds 33 top-level cases, 58 shell sources, 36
  evidence tests, one dedicated stress test, 201 main tests, and 70 recovery
  tests. The exact evidence, stress, main, and recovery identity hashes are
  `cc631c2bba201c6b754ac6344ee167adde640d6caac083b20fa0bf027f3f7f06`,
  `99dbe6b4806232fcb55e043b38a940d720b28775d387824f2cd98aeed4e65c84`,
  `2bcd4a21b69e2234d4fcdc09ee42ddd613c6ac959a70cb89d97e1585feb18fc4`,
  and `7b7222cb6798c7d7e3379e8544f3dc11625e3bb868be5f933406d7ddb84ed533`.
  The regenerated tracked contract exactly matched discovery. These were
  source-topology facts at that checkpoint, not an exact-commit authorization.
- That checkpoint's non-authorizing validation passed all 36 evidence tests
  with no skips, and the isolated then-current-tree mirror completed all 201
  main tests with
  194 required passes, six exact reviewed required host skips, and one
  diagnostic stress skip. Its 73-test production-transaction subset contains
  71 required passes, one reviewed root-only PID-namespace skip, and the one
  diagnostic stress skip. The complete recovery matrix completed 70 methods
  with 67 required passes and the three exact opt-in host-primitive skips. The
  smoke driver reports 61 passes, zero failures, one dirty-tree provenance skip,
  eight mode-selection skips, zero required-subtest failures, and zero mandatory
  internal skips. The structured ledgers and SHA-256 values are
  `/tmp/candidate-evidence-post-review-subtests.tsv`
  (`3ec986a654333b13f23535a0b125e1c004634d5a7db440d8ee0e38b838b53132`),
  `/tmp/candidate-main-post-review-subtests.tsv`
  (`b6bd423cc0fc0431bf591fef7e5fb58ea3d518c8e0d32e57060ed65d4a35966f`),
  `/tmp/candidate-production-post-review-subtests.tsv`
  (`e6162f4debfef0d7e4adcda21af43fc3452d79242d8564dfd61792d2c4229104`),
  and `/tmp/candidate-recovery-final-subtests.tsv`
  (`079391adacf7d7dbe11e764c371d7e24602d6f6d36b85b5175a9d328ffdaa210`).
  The smoke evidence directory is
  `/tmp/gentoo-opt-smoke-candidate-a-20260730-execution-identity-v4`; its
  summary, results, and subtest SHA-256 values are respectively
  `6cfe41bb7138f64deabee2d825283b8b3267e5052769321d17dfd28473d5aedd`,
  `3a92b35962fd8e489d47c415757de2e8d0666b0c0e7a887729df11211bdf99b1`,
  and `942eb99bca290126a29137292fd375bf8adcc179e09f50fe8a094150cd7b9970`.
  The driver CLI/self-provenance fixture, Bash syntax for all 58 sources, and
  exact hash-pinned ShellCheck 0.11.0 pass. The structured live-policy fixture
  exits zero while surfacing its exact reviewed required `live-portage.policy`
  skip in this managed non-authoritative boundary; the live Gentoo gate must
  execute that branch rather than skip it.
  The post-review driver fixture log is
  `/tmp/candidate-driver-selftest-post-review.log` with SHA-256
  `f78aa9c62eabdfbb7c7baa4db16c1eda7d35321ac066feb75a11fe975fee41b8`;
  its execution-core shadow matrix uses a hermetic reviewed manifest so the
  portable CI host does not depend on Gentoo-only production tool paths.
  These are dirty-tree/mirror diagnostics: the clean exact-source portable
  driver and exact-SHA CI result remain required before installing Candidate A.
- No §11.4 or §11.7 checkbox is closed by this checkpoint. No framework was
  installed, no package was emerged, no optimization generation was activated,
  and the Phase 3 inventory remains unfrozen.

### 2026-08-09 Candidate-A repository-boundary closure checkpoint

- Commit `1a00db30048b999d7f093b008714d9266f7dbb28` passed exact-SHA GitHub
  Actions run `30725879325` with 77 top-level passes, zero failures, 14
  reviewed portable skips, 379 required-subtest passes, zero required-subtest
  failures, 23 reviewed required skips, nine mandatory portable skips, and one
  diagnostic skip. Its root-owned 194-file retained artifact is
  `/var/tmp/gentoo-optimization/ci/1a00db30048b999d7f093b008714d9266f7dbb28/run-30725879325`;
  verified `CONTENT-SHA256SUMS` SHA-256 is
  `e21bd3f512b7c13f183a2a98b95596052afdbcf10a0db887de9ffc3ada6793fe`.
  That green repository boundary is superseded and non-authorizing because it
  predates the exact Portage-3.0.81.1 offline-restore and recovery-timeout
  corrections in its descendant.
- Exact descendant `1729e9a26bcf6d3fff466e05e04dc80c8dc0fbba` was tested by
  GitHub Actions run `30747706125` from 2026-08-02 12:23:08Z through
  12:44:02Z and is rejected. The run recorded 76 top-level passes, one
  failure, 14 skips, 377 required-subtest passes, two required-subtest
  failures, 23 required skips, nine mandatory internal skips, one diagnostic
  skip, 403 total subtests, and exit status one. Its 201-test main suite had
  one failure and four reviewed skips; pinned ShellCheck passed; the complete
  70-test recovery suite passed. The failure was exact: journal visibility was
  used as readiness even though SIGTERM ownership still began inside
  `run_child`, so the coordinator received the default disposition and
  returned raw `-15` instead of the handled exit `143`. The portable
  `framework-installer` fixture also skipped because Ubuntu exposes `runuser`
  at `/usr/sbin/runuser` while the evidence PATH selected `/usr/bin:/bin`.
- The rejected run is durably retained at
  `/var/tmp/gentoo-optimization/ci/1729e9a26bcf6d3fff466e05e04dc80c8dc0fbba/run-30747706125`
  as `root:root`, directory mode `0750`, file mode `0640`. Its 196-row
  `CONTENT-SHA256SUMS` verifies all other files and has SHA-256
  `7ac952778cfad9db440a4bc4fe371a6f84efbc4b873b0a0eb3ea98a61f162e55`.
  SHA-256 values for `summary.txt`, `results.tsv`, `subtests.tsv`,
  `test-contract.log`, `test-run-provenance.json`, the main-suite log, and the
  recovery-suite log are respectively
  `c39e25b2e719de5a8ace5ad957cb9e65286eb66043ea17722017d939b960c108`,
  `3602ee3dc127059322fa8c4e58e8a2fa502ace267470ff5b761e8fa56b60dbcb`,
  `0f452deaf5d77ce3fe345097aff9cbee643c0dd7dbbea1e238a6ddfa3b77b2cb`,
  `1c0b95479700f26507417e169ef8114bcf1e9d4230e5467fb62030332558fda1`,
  `6ff5973fe3e8a2127ee57ae4c5873a979e19305b4d61793698fea6799b23b8fb`,
  `bc060c09192d3a4bc5d556f089516a09cd151a0fee7d135d8232d64de5ffaa16`,
  and `d35b489efbaeec353a0c3d2a5ee8485a40d7cf6926a79dc12f98e28b72ea48bc`.
  This is rejected-predecessor evidence and has no authorization effect.
- The repository correction moves HUP/INT/TERM ownership to the complete
  transaction. It blocks the complete signal set with `pthread_sigmask` while
  installing and restoring dispositions before any pre-arm pause or journal
  publication, and rejects an inherited mask that already blocks any managed
  signal. A pre-commit interruption terminates the exact supervised child,
  restores the exact lock state, preserves any valid prepared passed receipt
  as abandoned evidence, publishes a deterministic `recovered-interrupted`
  receipt when a journal exists, removes journal and child sidecars, and
  returns `128 + signal` (`143` for SIGTERM). Only final receipt rename plus
  parent-directory fsync is masked; the committed state is recorded before
  unmasking, so a pending signal after that boundary is explicitly post-commit
  and bounded cleanup retains the truthful child result. First-signal ownership
  is itself masked and deterministic. Test boundaries cover pre-arm,
  child-before-spawn, post-spawn, child sidecar, authorization, release, wait,
  token scan, receipt partial fsync, final rename, and terminal cleanup. The
  manifest-selected `/usr/bin/python3` suite completes 77
  production-transaction methods: 75 pass and two are the reviewed
  diagnostic/root-host skips. The original journal-visible race also passes 50
  consecutive focused repetitions.
- The recovery verifier no longer streams a GPKG member through an unbounded
  `zstd` pipe. It stages each member in a private `0600` regular file, verifies
  the exact tar-declared size, and runs `zstd --quiet --test FILE` in a private
  session. One monotonic deadline is checked between every synchronous local
  tar/file staging operation and continuously during child supervision; no
  userspace timeout can interrupt a local filesystem syscall which never
  returns. Each stderr drain is deadline-, byte-, and read-count-bounded while
  retaining at most 64 KiB, including continuously writing and early-closed
  stderr cases. Timeout and exceptional cleanup use same-session process-group
  TERM, KILL, bounded reap, and residue checks for the exact reviewed `zstd`;
  the random temporary path is removed from diagnostics and unlinked on every
  outcome. All 19 verifier tests pass independently with `ResourceWarning`
  promoted to an error under both available Python 3.14 and 3.15 lanes. Nine
  new cases cover success, malformed input, pre- and post-consumption hangs, a
  TERM-ignoring parent and descendant, finite and unbounded stderr writers, an
  early-closed stderr hang, and staging-time deadline cleanup.
- Portable CI now verifies that Ubuntu's `util-linux` owns
  `/usr/sbin/runuser`, publishes and verifies the reviewed
  `/usr/bin/runuser` entry point, and requires an explicit
  `framework-installer` `PASS` row. Repository policy also verifies local
  Markdown links; the broken legacy PGO link is corrected; BOLT prototype
  wording is historical; `docs/commit-history-map.md` gives additive
  descriptions to generically named evidence-bearing ancestors and forbids
  rewriting them. The BOLT inventory validator now invokes its trusted Python
  entry point as `/usr/bin/python3 -I -B`; isolated mode alone ignores
  `PYTHONDONTWRITEBYTECODE` and had left a hidden `__pycache__` in the source
  tree during the hook fixture. The three current BOLT wrappers also use
  `-I -B`, while the byte-exact deployed-v1 migration fixture remains
  historical. Focused artifact-tool and repository-policy regressions bind the
  exact no-bytecode argv. The installed pre-Candidate framework still has the
  historical deployed-v1 handoff; it currently has no bytecode residue, but no
  legacy helper may be executed as root before the atomic v2 migration. The
  tracked deterministic contract now binds 206 main tests
  with identity SHA-256
  `7c95b508cc1d5d271358c6da73d0631e15c9fada25c1584dcb2c791a744a31a7`
  and 79 recovery tests with identity SHA-256
  `778339462268073b3435c4de8f8a60124e3bd8e83d5bc5b886217684b74cbe65`;
  the other topology identities remain unchanged. This is working-tree
  implementation evidence only until the complete exact-clean local and
  exact-SHA CI boundaries pass.
- The first complete dirty-tree portable attempt used the driver's default
  2,700-second recovery-case deadline and failed safely when that deadline
  expired while the 57th of 79 recovery methods was running. It recorded 77
  top-level passes, one timeout failure, 13 reviewed skips, 369 required
  subtest passes, one required failure, 23 required skips, ten mandatory
  portable skips, one diagnostic skip, 394 total subtests, and exit status one.
  No functional assertion had failed; the exact recovery process group was
  terminated, the result row recorded
  `process_group_cleanup=terminated-residual`, and the partial result was
  rejected. The root-owned retained tree
  is
  `/var/lib/gentoo-optimization/reports/candidate-a-closure-precommit-timeout-20260809`;
  its 195-row `CONTENT-SHA256SUMS` verifies and has SHA-256
  `3e6e9cb67f1e411c38940fee16abb7e74cf08bc78e0c74026164b71afb1aed91`.
  SHA-256 values for `summary.txt`, `results.tsv`, `subtests.tsv`, and the
  incomplete recovery log are respectively
  `0008bd031de77a1563e5c157f25086ff2379a388299d56b29855746c59015176`,
  `150bdd2277908797ffdde69a93c2bd3d2935bac6c9015b63b15b66645335a861`,
  `07976623bf92dec16b71e1ded11fafd5955d8ce90b882e5859796dceb7cfb119`,
  and `22a585cd47b530681dff417fef346a4195d3e8480949d67ee10b900ae7d79888`.
  This is rejected deadline evidence, not a test pass or authorization.
- A subsequent dirty-tree complete precheck gave the recovery case a bounded
  7,200-second diagnostic deadline and completed it in 1,277 seconds. The full
  driver recorded 78 top-level passes, zero failures, 13 reviewed skips, 394
  required-subtest passes, zero required failures, 23 required skips, ten
  mandatory portable skips, one diagnostic skip, 418 total subtests, and exit
  status zero. All 206 main methods completed in 49 seconds with five reviewed
  skips; all 79 recovery methods completed with three reviewed host skips; the
  framework-installer, pinned ShellCheck 0.11.0, and complete BOLT-hook fixture
  passed. Its `summary.txt`, `results.tsv`, `subtests.tsv`, main-suite log,
  recovery-suite log, framework-installer log, and BOLT-hook log have SHA-256
  values
  `897adac696a4370a597c63bc15b1caf6b27a9da85de0f27d7402bb6a7dba077a`,
  `91bae396633266856edf57d36d644bc378a0c767cd37dfeac2d440a0e352e5b0`,
  `15d50496f0e7d7325a7127e2fd5b7a84e1b9cc388816c0d266fabed78a380c3d`,
  `1f4ffca1e4250eb942d02b57026349de6c1ed03c5abcac2965514318279f0474`,
  `9bc9959641832e06026975f272989166ee69cf4fdf59dc926fad3c61cac78317`,
  `fd48a5e05fcf216fbe69590d32260ceb3677a28f131a85c43c9a5bb2bf2a0429`,
  and `b435e9ea6e057bcbb80c8fe5efa44d13884f1486c4c2811545e336e54d96f9cf`.
  The exact contract and static-contract logs have SHA-256
  `a8bda87cfbc5e2b4ba50838dd5013a5f98efdb62fac2141bbeeb5ed574223061`
  and `0cfdd89b45877f22c328cf8d9a1b3cc89443225e9af81e6d88da78fdba16c050`.
- The current-tree smoke and 18-method checkpoint-smoke reruns also completed
  with zero failures and zero mandatory internal skips. Smoke recorded
  61 passes and nine reviewed mode/provenance skips; its summary, results, and
  subtest SHA-256 values are
  `08ae81573b313ee646ed0a64dfc4fb2255d69705e1062ab44f76397794d7613d`,
  `fd68f95c49ca4db3a6c81570645f98234bcdb1d87d3b15a3e0c25a8c4988e1fb`,
  and `942eb99bca290126a29137292fd375bf8adcc179e09f50fe8a094150cd7b9970`.
  Checkpoint-smoke recorded 62 passes, eight reviewed skips, 81 required
  subtest passes, and no required failure; its corresponding hashes are
  `75ab305383568ff0312164c131f9ec3978168cd75548cb6f1066db298885c749`,
  `4583f0ff98846b9c33c852fd2cda091dd86514f3461b13dd2890c00988f93110`,
  and `6170906cfad5e069be03a589f0df4667a0e9dc1d4bc14b6d26ad684144470b44`.
  No matching process or repository bytecode residue survived either run.
- Those three green outputs are sealed byte-for-byte below the root-owned
  mode-`0750` tree
  `/var/lib/gentoo-optimization/reports/candidate-a-closure-precommit-20260809`;
  files are mode `0640`, all objects are `root:root`, and its 504-row
  location-independent `CONTENT-SHA256SUMS` verifies with SHA-256
  `68f66d1cc4c8fff5fc0068216a94256f365d89fc0359180f1b0bc17d086cf2fc`.
  `NONAUTHORIZING.txt` explicitly records the dirty source state and has
  SHA-256
  `21bfe0c521fbd1e6c1a0cf53f9f1bd0d58174b3e5ab6a98a119a95c6cc13d631`.
  These are explicitly dirty-tree, non-authorizing implementation diagnostics.
  The complete portable run preceded the final plan/runbook wording and this
  evidence update, and none of the runs has clean-commit provenance. They do
  not substitute for the fresh exact-clean local rerun or exact-SHA CI gate.
- A read-only 2026-08-09 refresh observed the same 1,220 live CPVs and sorted
  CPV-list SHA-256
  `a5b75bd995f68d74d869b2d5996dcd345e326741f4d1c329a9dbc876edb630ff`,
  no active Portage mutation, and the old protected 1,217-package selector.
  The reviewed `jsonschema` closure remains absent. No package, framework,
  selector, project state, optimization generation, or frozen inventory was
  changed. The complete live observation must run again immediately before
  checkpoint mutation.
- No §11.4 or §11.7 checkbox is closed by this checkpoint. The exact-clean
  contract/provenance check, smoke, checkpoint-smoke, complete portable gate,
  exact-SHA GitHub workflow,
  two live recovery checkpoints with two exact offline
  restoration/finalization proofs, installed Candidate-A gate,
  supervised sample-PGO transaction, frozen Candidate B, and detached index
  remain required.

### 2026-08-14 rejected predecessor and prerequisite-integration review

- Commit `03e8917dc69a1cda1e9d7063e16b6a2ce0ff61fa` is a rejected
  predecessor. Its exact GitHub Actions run `31543998224` completed checkout,
  portable dependency and reviewed ShellCheck installation, and the Ubuntu
  `runuser` compatibility proof, then stopped before `portable-complete` at the
  authoritative contract check. The tracked contract expected 206 main tests;
  deterministic discovery found 256 with identity SHA-256
  `e3471337c6119f38534dbeaf89e4c301ca22924d41403bd3e7742464bb3a523a`.
  This is a deterministic repository-boundary rejection, not a flaky result.
  The contract must be regenerated only after the prerequisite transaction and
  its test topology reach their intended final source shape.
- That revision implemented substantial but deliberately non-live
  prerequisite machinery in
  `scripts/optimization/recovery/install-jsonschema-prerequisite.py`: durable
  transaction states and reconciliation groundwork, frozen Git/rsync/local
  repository authorities, exact resolver plans, private mutable package roots,
  namespace/PTTY containment, authenticated child control, rollback/recovery
  groundwork, and a semantic fixture suite. Its
  `LIVE_PREPARATION_ENABLED = False` and `LIVE_MUTATION_ENABLED = False` gates
  remain intentional. The old inline runbook path must not be treated as a
  substitute; one completed, immutable, evidence-bound helper path is required
  before any live prerequisite operation.
- The exact run produced no live mutation and no Candidate-A or Phase 2
  authorization. No package, framework, selector, project state, optimization
  generation, or frozen Phase 3 inventory was changed. All four §11.4 boxes
  and all three §11.7 boxes remain open. The plan still contains 80 checked and
  69 open boxes; this implementation/review checkpoint changes no checkbox.
- Portable CI must create an evidence root and exact checkout provenance before
  its static contract check, retain the combined contract diagnostic and exit
  status there, and always upload that root. This preserves evidence for an
  exact SHA even when the driver cannot start. A future green successor still
  must retain the exact checkout, reviewed tools and ShellCheck, Ubuntu
  `runuser` compatibility proof, mandatory framework-installer pass, complete
  portable gate, and exact-SHA provenance.

### 2026-08-22 rejected predecessor and prerequisite-boundary review

- Commit `dcc6b8fc76e1bff20a25a06cda4aadda580b3885` was the latest rejected
  predecessor at this review. Its broad prerequisite/bootstrap/evidence scope is recorded
  additively in `docs/commit-history-map.md`; the misleading narrow commit
  subject is historical evidence and must not be amended or rebased.
- Exact GitHub Actions run `31880559624` stopped at the static authoritative
  contract check before `portable-complete` ran. The tracked contract expected
  36 evidence tests and 206 main tests; deterministic discovery found 37 and
  290. That run proves only stale topology. Separate direct testing—not CI—
  found two obsolete held-lock mock signatures, one obsolete raw-JSON hash
  expectation, a root-fixture/production bootstrap trust conflation, missing
  prerequisite signal-transition proof, absent Candidate-B prerequisite-chain
  semantics, and mechanically executable retired BOLT prototypes.
- The correction boundary is deliberately narrow: make the prerequisite and
  root bootstrap fixtures exact, define and test managed signal cancellation,
  bind pre-checkpoint -> prerequisite success -> post-checkpoint evidence into
  the existing `automation` component, and replace the four legacy BOLT
  entrypoints with fail-closed stubs plus an authoritative regression. The
  authoritative contract is regenerated only after that topology is final.
- `LIVE_PREPARATION_ENABLED` and `LIVE_MUTATION_ENABLED` remain false until the
  final invariant audit classifies each prerequisite property as implemented,
  hermetically proven, host-proof pending, or missing, and every missing item
  is closed. The real pidfd/PID-namespace detached-session teardown, XFS
  exchange, privileged metadata, Gemato/Portage locking, and four-class BOLT
  primitives were the remaining host-capability lanes; their exact results are
  recorded below. Installed-candidate lock integration, disposable/live
  Portage phase identity, authoritative zero-skip execution, and supervised
  production behavior still require the immutable Candidate-A framework.
- The bounded prerequisite correction later committed as
  `9dd5960958c397216dd4c60b6212658f10015dca` had 92 deterministic test identities:
  88 portable passes and four intentional authoritative-host skips. The same
  four host identities pass under an actual-root clean environment and prove
  trusted host tools, pidfd plus PID/network namespace kill-child teardown,
  exact nonblocking VDB and preserved-library-registry lock contention and
  unwind, and full recursive Gemato verification of a copied rsync authority.
  An independent post-fix audit found no remaining P0/P1 defect in the managed
  signal, exact counter, Portage-lock, payload-device, durability, or
  recovery-failed closures. Both live gates remain false, so these results are
  implementation and prerequisite-capability evidence, not a live transaction
  or Candidate-A acceptance.
- The bootstrap publisher fixture now distinguishes explicit production
  authority from effective UID/GID. Its 11 identities pass both unprivileged
  and under an actual-root clean environment while production ancestry trust
  remains strict. The four retired `scripts/bolt/*.sh` entrypoints are now
  fail-closed stubs; the new `no-legacy-bolt` fixture and the current BOLT
  command-policy fixture both pass. These source and test identities must be
  included in the final authoritative contract after the remaining evidence
  semantics stop changing.
- The exact real-host detached-session containment regression
  `test_pid_namespace_kills_escaped_setsid_descendant_before_scan` passes. This
  closes the previously sandbox-only uncertainty for that primitive, but does
  not replace the later installed-framework authoritative gate.
- The actual Gentoo-host checkpoint boundary passes all three opt-in methods
  for file/directory durability, pidfd signaling, and PID/network namespace
  kill-child teardown, and the session/subreaper harness passes all eight
  methods. The two production-profile pidfd and escaped-`setsid` containment
  methods also pass. The corresponding log SHA-256 values are
  `83e65fff562640e52071da04b1d46b7d6404fa61db2d79044e63924294a094bc`,
  `677ee2e850c0aac6e4c160db828ab4af19c26a7a17be46c3501ee8920fbaf8ae`,
  `b66892832c30fd55727f0e6ff8a8ba76409cf5bddaef9d4c41bfa318063de149`,
  and `49b943d806fafd0fa10b909cc4734ecb2d8cc02e03c37f51bc7a3f47699566da`.
- Root preflight exercised `/usr/bin/mv --exchange --no-copy -T` plus `sync -f`
  at all six exact production destination parents on XFS device 66307, with no
  residue; its evidence SHA-256 is
  `d1a1d248df6e0ca8b17dd03f7480814ee447986497dab360446f0bb23b099aba`.
  The authoritative fake-root installer fixture separately passed its two
  required rows with zero skips while using the same production `mv` path.
- The privileged BOLT metadata transaction passes all three root-only methods
  for file capabilities, setuid/setgid hardlink groups, xattrs, deployment and
  rollback. The full root BOLT hook fixture passes user-xattr,
  file-capability, and mixed-ABI rows; its sole reviewed skip is the
  Candidate-A framework-lock integration which cannot execute before that
  framework is installed. The real four-class gate passes ET_EXEC, dynamic
  PIE, static PIE, and DSO with all 36 bounded stages at status zero and all
  479 evidence hashes verified. The metadata, hook-fragment, validation,
  timeout, and complete BOLT evidence-manifest SHA-256 values are respectively
  `0d18cf3017a83f41e7c1d30435734192f752f6ae1adef13386e6cf27f731c2bf`,
  `6220976283463a301409a0989b7382c9e4d8ed08787130900cdcf9a31fdf61aa`,
  `47ae2f5c42c9437d1677e77d01a7ca55d69920856d20b435f1cf818d42f888d4`,
  `a24210bb51f240f661ef9cc2bfe5043398068e30a23a94e3369b13f3fc0cc79d`,
  and `f1768ae63a2b86855e8117c784f4d54bf15ff079acfe19beed3414d67e621cce`.
  These dirty-tree host proofs close capability uncertainty only; they do not
  authorize Candidate A or Phase 2.
- The live `::gentoo` rsync authority remains dated 2026-07-11, older than the
  prerequisite helper's reviewed three-day signed-repository age limit. The
  helper correctly fails closed. Before live prerequisite preparation, perform
  a separately reviewed Phase-2 prerequisite source refresh, then repeat full
  Gemato verification, dependency resolution, VDB/config/tool/activity and
  capacity checks. That refresh is not the Phase-3 repository-sync checkbox and
  grants no permission to begin Phase 3.
- This review changes no checkbox. All four Section 11.4 live sample-profile
  claims and all three Section 11.7 acceptance/authorization claims remain
  open; no package, selector, framework, optimization generation, or frozen
  inventory was changed.

### 2026-08-29 rejected successors and evidence-contract correction review

- Commit `9dd5960958c397216dd4c60b6212658f10015dca` is rejected,
  non-authorizing predecessor evidence. Exact GitHub Actions run `32601934693`
  stopped at the static authoritative-contract preflight before
  `portable-complete`: the tracked 58 Bash, 36 evidence, and 206 main-test
  identities had become 59, 40, and 309, and `no-legacy-bolt` was absent from
  the exact top-level contract. That CI result proves stale topology only.
  Separate direct review—not the aborted workflow—found incomplete repository
  and resolver evidence semantics; both prerequisite live gates remained
  deliberately false.
- Commit `ea191dce00f19db8ea0dda20ded85ab594d40db3` is rejected,
  non-authorizing predecessor evidence. Exact run `33104209384` again stopped
  at the static contract preflight before `portable-complete`; main-test
  discovery had advanced from the tracked 206 identities to 310. Separate
  direct review found a verifier syntax error, stale Git fixture expectations,
  and validator/schema wiring defects. CI did not execute or discover those
  defects.
- Commit `0f91f5ab68d7917206e79d4cb688b3a93ab5f182` is rejected,
  non-authorizing predecessor evidence. Exact run `33170596519` stopped at the
  static contract preflight before `portable-complete`; deterministic
  discovery had reached 59 Bash sources, 40 evidence tests, and 323 main
  tests against the older 58/36/206 contract. Separate direct review found
  that prerequisite-originated digests used incompatible producer/verifier
  JSON encodings, the portable automation fixture invoked production
  root-trust validation on `/tmp` executables, and negative chain tests could
  pass while their untouched base fixture was already invalid.
- Commit `6462859e207b3545d9b699c242250022ad8c9f26` is a rejected,
  non-authorizing predecessor. It independently matches the
  prerequisite producer's indented, sorted, newline-terminated digest
  serialization in the verifier, uses a fixture-local executable observation
  without weakening production root trust, and makes each chain-tamper test
  begin from a freshly validated base and require the intended rejection.
  The positive producer-to-verifier chain and its six focused tamper cases
  pass. These changes close the three defects found directly at `0f91f5ab`.
- Exact run `33188537276` for `6462859e207b3545d9b699c242250022ad8c9f26`
  nevertheless stopped at the static contract preflight before
  `portable-complete`. Its deterministic pre-correction discovery baseline is
  34 exact top-level cases, 59 Bash sources, 44 evidence tests, 323 main
  Python tests, one dedicated stress test, and 79 recovery tests. The final
  successor contract therefore had to include `no-legacy-bolt` and the four
  exact `jsonschema` prerequisite authoritative-host methods as permitted
  required skips in portable mode; authoritative mode must execute them and
  remains a zero-required-skip boundary. The bounded correction inspected the
  complete contract diff and required exact deterministic reproduction.
- The first pinned ShellCheck 0.11.0 run after that contract correction exposed
  only missing `SC2016` annotations for the intentionally literal `${ED}` text
  in the four fail-closed legacy BOLT stubs and their exact-content regression.
  The successor adds those narrow annotations without changing a test identity;
  the exact five-file ShellCheck, Bash-syntax, and `no-legacy-bolt` checks pass.
- The first complete main-suite run then exposed one fixture assumption rather
  than a production-policy defect: the driver's deliberate `umask 077` filtered
  a requested `0755` temporary Portage prefix to `0700`. The fixture now applies
  its intended exact mode explicitly; the focused method and all 105
  prerequisite tests pass under the driver umask with only the four reviewed
  authoritative-host skips. Production mode validation remains unchanged.
- Commit `274c50d2208dea9a86f0ff4e7293c7cee5df5603` is an earlier
  rejected, non-authorizing predecessor. It brought the tracked contract into
  exact agreement with 34 top-level cases, 59 Bash sources, 44 evidence tests,
  323 main Python tests, one dedicated stress test, and 79 recovery tests; it
  also admitted the four exact portable-only prerequisite host skips and
  retained their mandatory execution in authoritative mode.
- Exact GitHub Actions run `33249132657` passed the static authoritative
  contract and then exercised `portable-complete`. It recorded 79 top-level
  passes, one functional failure, and 13 reviewed skips; its required subtests
  recorded 517 passes, one failure, and 26 reviewed skips, plus one diagnostic
  skip. All 323 main Python tests, 79 recovery tests, 44 evidence tests,
  ShellCheck, PGO/BOLT policy and transaction fixtures, and both legacy-lane
  retirement checks passed. The sole functional failure was
  `framework-installer`.
- The installer failure is a compatibility defect, not a renewed test-contract
  mismatch or evidence that the installed tmpfiles rule is semantically
  invalid. The installer unconditionally used `systemd-tmpfiles --dry-run`,
  while the Ubuntu 24.04 runner provides systemd 255 without that option. The
  final `FAIL: exact portable-complete test topology/identity contract` row
  correctly propagated the required installer failure from the result ledger;
  deterministic static discovery had already passed. The bounded successor
  removes mandatory dependence on that newer option. It copies the exact
  validated rule into a disposable private root, substitutes only portable
  ownership tokens after exact-byte validation, keeps literal `root:portage`
  semantics in production, and verifies the resulting directory and three
  regular single-link empty lock files by exact ownership and mode. Transient
  tool output is file-size-limited; a failure retains the exact tool, argv,
  status, version status and at most 4096 bytes per stream. The existing
  installer fixture now rejects every `--dry-run` call, forwards the pre-256
  `--root --create` interface, and proves overlong stderr is truncated without
  its tail. The complete focused fixture, Bash syntax, pinned ShellCheck 0.11.0,
  and deterministic contract check passed directly before commit
  `dfa61d38b4c0486112789e3fc28aa6379449b154`.
- Commit `dfa61d38b4c0486112789e3fc28aa6379449b154` is the latest rejected,
  non-authorizing predecessor. Exact GitHub Actions run `33267217547` passed
  the static authoritative contract and exercised `portable-complete`: 79
  top-level cases passed, one failed, and 13 produced reviewed skips; required
  subtests recorded 518 passes, one failure, and 26 reviewed skips, plus one
  diagnostic skip. The systemd-255/pre-256 tmpfiles proof passed. All 323 main
  Python tests, 79 recovery tests, 44 evidence tests, ShellCheck, PGO/BOLT
  policy and transaction fixtures, and both legacy-lane retirement checks
  passed. The sole functional failure was `framework-installer`.
- That failure is in the generated-policy boundary, not the repaired tmpfiles
  boundary and not the authoritative test topology. Portable validation
  unconditionally attempted `portage.dep.Atom`; Ubuntu lacks that Gentoo
  module, so the fixture's valid `=app-misc/example-1` atom was reported as
  invalid before the intended `generated environment is not assignment-only`
  rejection. The final ledger-contract row only propagated this required
  failure. Separate direct review also found that the bootstrap inventory CPV
  predicate admits unversioned CPs, its `owned_directories` shape conflicts
  with the semantic authority in `state.py`, and generated atom syntax is wider
  than the documented canonical plain `=CPV` form.
- That bounded correction was committed as
  `3d3e660ca207d357caa1de5b537c6bba6fa83fbe`: independent portable
  exact-versioned-CPV validation; distinct production Portage-parser
  availability and invalid-atom diagnostics; canonical unqualified `=CPV`
  generated atoms and CPV-keyed duplicate checks; strict frozen-inventory
  CPVs; semantic-authority-compatible nonempty `owned_directories`; corrected
  jq control-byte path handling; and narrow positive and rejection fixtures.
  Its exact portable result is recorded in the 2026-08-30 review below.
- This additive review preserves the Phase 2 scope freeze and changes no
  checkbox. All four Section 11.4 live sample-profile claims and all three
  Section 11.7 acceptance/authorization claims remain open. Both prerequisite
  live gates remain deliberately false pending the reviewed gate-enabled
  successor and its exact clean repository boundary. No package, selector,
  installed framework, optimization generation, or frozen inventory was
  changed, and Phase 3 must not begin.

### 2026-08-30 green portable boundary and exact-CPV successor review

- Commit `3d3e660ca207d357caa1de5b537c6bba6fa83fbe` passed exact GitHub
  Actions run `33279170822`. The retained ledger records 80 top-level passes,
  zero failures, and 13 reviewed skips; 519 required-subtest passes, zero
  failures, and 26 reviewed skips; one diagnostic skip; 546 total subtests;
  and exit status zero. The static authoritative contract,
  `portable-complete`, `framework-installer`, pinned ShellCheck 0.11.0, main,
  recovery, evidence, PGO, BOLT, and legacy-retirement boundaries all passed.
  This establishes a green exact-SHA portable repository boundary for that
  revision only. It performed no live mutation and did not install or accept
  Candidate A, create Candidate B, or authorize Phase 2.
- Successor `2128d87fcb5736f75b89ea6eed60f7db9ccc7fd4` tightened exact Gentoo
  CPV semantics across independent schemas, state, profile, evidence,
  framework, and recovery surfaces. Exact run `33312523486` recorded 79
  top-level passes, one failure, and 13 reviewed skips; required subtests
  recorded 518 passes, one failure, and 26 reviewed skips. The sole required
  failure was pinned ShellCheck 0.11.0 reporting four `SC2015` findings in
  `scripts/optimization/recovery/create-binpkg-checkpoint.sh`; all other
  executed functional boundaries passed. This revision is rejected and
  non-authorizing, but its CPV tightening remains part of the correction and
  must not be reverted merely to recover the parent's green result.
- Successor `d42a0cea53c1b1dfdb86fa772def255d97dc2862` replaced those
  four compound expressions with explicit control flow, made the
  provenance-bound `optimization/exact-cpv-contract.json` vectors executable
  through existing test identities across the independent validators, and
  passed exact run `33327273287` with the green totals recorded above. That
  closed the exact-CPV correction without enabling a live gate.
- This review changes no checkbox. All four Section 11.4 live sample-profile
  claims and all three Section 11.7 acceptance/authorization claims remain
  open. Both prerequisite live gates remain deliberately false. No package,
  selector, installed framework, optimization generation, frozen inventory,
  or Phase 2 authorization changed, and Phase 3 must not begin.

### 2026-09-03 userspace-only safety policy and rejected migration predecessor

- Commit `04c1becd4c915759be4f3ab0f94f1d805cdf58b1` deliberately made
  boot entries and the kernel lifecycle immutable, human-only system
  boundaries. It retired the former boot-evidence entrypoint, removed the
  recovery CLI's firmware/kernel mutation paths, retained package/config
  recovery, introduced `kernel-policy-exclusion`, and removed two boot/kernel
  completion requirements from the plan. Those policy decisions are correct
  and must not be reverted.
- Exact GitHub Actions run `33719023582` rejected that revision at the static
  authoritative-contract preflight. The tracked contract still named
  `recovery-boot-evidence-fixture` and its removed Bash source, while
  deterministic discovery named `no-boot-entry-automation` and its replacement
  source. The job stopped before `portable-complete`; its small retained
  artifact is precontract evidence and has no portable PASS/FAIL/SKIP ledger.
- Separate focused review—not the aborted CI job—found that the policy
  migration was incomplete at the state authority. `state.py` declared package
  and artifact schema v5 plus final-system v2, but the three JSON Schemas and
  `test_state.py` still described the former v4/v1 records. The final-system
  validator still read the former boot-entry/EFI structure and referenced a
  removed production tool constant, while the artifact-kernel path and wire
  schemas still admitted retired boot fields and the `kernel-autofdo` backend.
  Direct execution of the 29 state tests reported 24 failures and 24 errors.
- The bounded successor must complete only that migration: align package and
  artifact v5, final-system v2 `runtime_after_reboot`, Python semantic
  validation, JSON wire schemas, state fixtures, and the existing non-boot
  regression; reject the retired fields/backend; regenerate the exact test
  contract last; and restore a green exact-SHA repository boundary. It must
  leave `LIVE_PREPARATION_ENABLED` and `LIVE_MUTATION_ENABLED` false. No
  live-host preflight or package mutation may resume before that boundary.
- This review changes no Phase 2 checkbox. All four Section 11.4 live
  sample-profile claims and all three Section 11.7 acceptance/authorization
  claims remain open. No Candidate A was installed or accepted, Candidate B
  and its detached index do not exist, Phase 2 remains unauthorized, and Phase
  3 must not begin.

### 2026-09-04 corrected state migration and remaining post-policy blockers

- Successor `e2bd31875781ff14ef95977c4d2a5534b010460a` completed the
  package/artifact v5 and final-system v2 migration, removed the active former
  firmware-entry authority, migrated the state fixtures, and strengthened the
  existing non-boot regression across Python and all three wire schemas.
  Direct execution of its 29 state methods passed with one reviewed
  `jsonschema` dependency skip, and the non-boot authority fixture passed.
- Exact GitHub Actions run `33745489392` still rejected `e2bd3187` at the
  stale authoritative-contract preflight before `portable-complete`. It
  produced no portable result ledger and authorizes no live work.
- Subsequent deterministic full-suite review found that the repository policy
  allowlists had not admitted `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.cursor`,
  or `.github/copilot-instructions.md`. More importantly, package exclusion
  was derived with an all-kernel test even though Portage mutates whole CPVs.
  A mixed userspace/kernel CPV could therefore claim a successful automated
  source rebuild despite containing a project-mutation-prohibited kernel
  component. Artifact reconciliation also lacked an independent check that a
  kernel image or module forces the owning CPV outside the mutation set.
- The bounded stabilization successor must make any kernel-affected CPV a
  whole-package `kernel-policy-exclusion`, verify that decision from both
  component and owned-artifact classification, admit the exact instruction
  files in repository policy, and add a positive `coverage_complete` test with
  one succeeded userspace CPV plus one non-mutating kernel-excluded CPV. The
  authoritative contract is regenerated only after those source, test, and
  documentation changes are final.
- Successor `cbee0c2855f77339c42a2d7673aea0dd990c5a6b` completed those
  substantive state and policy corrections. Exact run `33872811448` reached
  `portable-complete` and recorded 77 top-level passes, three failures, and 13
  reviewed skips; 517 required-subtest passes, three failures, and 26 reviewed
  skips; one diagnostic skip; and 547 total subtests. Two failed identities
  shared the same unsorted `required_passing_test_names` cause, while the third
  came from the separate Portage-cleanup root allowlist omitting `.cursor` and
  the three root instruction Markdown files. The next successor is therefore
  limited to those bookkeeping corrections and additive evidence history; it
  must not reopen the now-aligned kernel/state architecture.
- This review changes no checkbox. Both prerequisite live gates remain false;
  live-host preflight and all package mutation remain stopped until a successor
  passes the complete exact repository boundary.

### 2026-09-04 green post-policy boundary and prerequisite gate successor

- Successor `0a2e16bb9fce0b61a8120b8618ac9ac809ab6449` corrected only the
  two portable bookkeeping defects left by `cbee0c28`. Exact GitHub Actions
  run `33879183160` passed with 80 top-level passes, zero failures, and 13
  reviewed skips; 520 required-subtest passes, zero failures, and 26 reviewed
  skips; one diagnostic skip; 547 total subtests; and exit status zero.
- The exact clean revision then passed the non-package-mutating Gentoo-host
  prerequisite boundary: all four prerequisite host methods, all three
  checkpoint primitives, both selected production-containment methods, all
  six compiler/profile/BOLT capability lanes, live Portage package-environment
  semantics, the Python 3.15 Portage/Python 3.14 Meson split, exact build-tool
  versions, and atomic exchange/fsync/xattr/file-capability behavior at all six
  production destination parents on XFS device 66307. No package, boot, or
  kernel mutation occurred.
- That result authorizes a source-controlled gate-enabled successor, not direct
  host edits or a package transaction from `0a2e16bb`. The successor changes
  both literal prerequisite gates to true and must itself pass the exact
  portable CI and repeated prerequisite/host-capability boundary before the
  bootstrap is published. The pre-prerequisite boundary is not the later
  complete installed-Candidate-A authoritative zero-required-skip gate.
- This transition changes no checkbox. Candidate A remains uninstalled and
  unaccepted, Candidate B and its detached index do not exist, Phase 2 remains
  unauthorized, and Phase 3 remains prohibited.

## 11.1 Remove the unsafe global consumer

- [x] Delete or disable `portage/package.env/50-global-pgo` in its current form.
- [x] Remove `pgo-use-if-available.conf` and `pgo-instrument.conf` or replace them with backend-specific mode files.
- [x] Ensure absence of a profile file can never silently change an unrelated package’s compiler flags.

### Immediate legacy-consumer neutralization evidence

- The live `/etc/portage` now resolves through `framework-current/portage`; its selected installed candidate predates Candidate A. In the repository, `50-global-pgo` is assignment-free, the obsolete `pgo-instrument.conf`, `pgo-use-if-available.conf`, and `no-pgo-use.conf` files are deleted, backend-specific files live only below `portage/env/optimization/`, and no generated package assignments are active.
- The exact dispatcher now leaves all flags byte-identical in unset/`off` mode and still fails closed if either stale `PGO_USE_IF_AVAILABLE=1` or `PGO_INSTRUMENT=1` is supplied. The focused dispatcher fixture covers those invariants; the final Candidate-A repository gate must bind the complete Bash-syntax and pinned-ShellCheck results to the exact clean source revision.

> Historical Phase 2 evidence (superseded; not authorization): the dormant implementation deletion was recorded in `/var/lib/gentoo-optimization/reports/phase-1-dead-legacy-pgo-removal.log` (SHA-256 `6acdd8d373f3bd9bd54e6afda5e4a75c5bd5ab8a41995fa8260f98338378f101`) and `/var/lib/gentoo-optimization/state/project/legacy-pgo-neutralization.json` (SHA-256 `aeba82f79f4e6ec7028d9db60a30a4999203d4cfb434f552a1adac928f2e897d`). Those records prove the older revision only. No package build may proceed through the legacy lane.

## 11.2 Rewrite `portage/bashrc`

- [x] Implement and validate the strict backend/ABI/fingerprint dispatcher described below.

Implement named functions with a strict mode dispatch. Suggested environment variable:

```text
GENTOO_OPT_MODE=
    off
    clang-ir-generate
    clang-ir-use
    clang-sample-use
    gcc-generate
    gcc-use
    rust-generate
    rust-use
    go-use
    bolt-capture
    bolt-deploy
```

The hook must:

1. compute or load the package fingerprint;
2. detect ABI and compiler family;
3. refuse incompatible mode/compiler combinations;
4. append each flag exactly once;
5. use absolute profile paths;
6. avoid C/C++ profile flags in `FCFLAGS`/`FFLAGS` by default;
7. disable ccache for profile generation and use passes unless a specific verified cache strategy is implemented;
8. log the selected backend, fingerprint, and profile path;
9. fail closed if a requested use profile is absent, malformed, or mismatched;
10. expose `post_src_install` hooks for BOLT input capture and deployment.

Keep profile generation flags out of build scripts and host tools where the language workflow requires it, especially Cargo build scripts.

Active modes require an exact package fingerprint, explicit ABI and compiler family, an absolute profile path, and—for every use mode—a manifest whose backend, fingerprint, ABI, compiler family, validation status, and payload SHA-256 match. Fingerprint schema v3 preserves the effective last-token-wins `FEATURES` state. Mapping and consumer identities remain distinct. Format validation is backend-specific: Clang IR and Rust indexed profiles, Clang sample profiles, GCC gcov directories, and Go pprof data never share a consumer. Flags append exactly once; generation/use disables compiler caches; `FCFLAGS` and `FFLAGS` remain untouched; GCC correction remains confined to the GCC lane; Rust target isolation and the installed Go tool's `go version` interface are tested. Composite capture/deploy stages retain Rust/Go language-lane behavior, preserve the pre-existing Portage hook chain, and invoke only the exact executable wrapper interface during Portage's pre-strip `post_src_install` boundary.

> Historical Phase 2 evidence (superseded; not authorization): the first dispatcher implementation was commit `2b18e6134a5abd85996d48274a492a8080828a0b`. Its 19-case root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-2-dispatcher-hook-chain-20260712` (manifest SHA-256 `0db50cbfe5232d9c9ca5c3874198de483c407a4081dfbc2bcbbeee926dc0d1b7`); component state SHA-256 was `af388f101eb9ac16f5fabcdcc79f54531e7166b06296ccc0b16c09ab295bb288`, tested `portage/bashrc` SHA-256 was `9c8eedc4760d82a5116859abb6be22d9a701fd34d0756495a0758f2464887414`, and fixture SHA-256 was `d21aee1119e932ea8bdb807d6b9693c8634b33733a6649fa671683586d46c09f`. These hashes do not describe Candidate A. `active_generation` remains null and `inventory_frozen` remains false.

## 11.3 Implement stage-specific env files

- [x] Create and validate assignment-only, minimum-marker stage environment files.

Create the files listed in section 4. Each file must contain only mode markers and the minimum flags for that mode. Avoid copying the entire global flag stack into every env file.

The stage files contain only `GENTOO_OPT_MODE`, `GENTOO_OPT_BOLT_STAGE`, or the narrowly scoped readiness marker required by that stage; package fingerprints, ABI identities, generation IDs, and profile paths remain machine-generated state outside Git. Repository tests syntax-check every stage file and prove the files are not assigned to live packages. Candidate A must still revalidate them through the installed live framework.

## 11.4 Fix sample-profile scripts

### 2026-07-26 Candidate-A architecture checkpoint

- Production runtime helpers now live under `scripts/optimization/pgo/`, not
  `tests/`. The root-owned coordinator constructs an exact clean child
  environment, validates every trusted executable identity, contains the child
  in a kill-child PID namespace, and recovers crash windows from one coherent
  PID/process-group/start-time identity. It generates the bearer internally,
  supervises stable framework/project/generation locks, and scans retained
  roots after containment to prove that the raw token did not persist.
- The containment preflight is now functional rather than attribute-based: it
  creates a disposable child, opens a pidfd, signals that exact child through
  `pidfd_send_signal`, verifies the expected signal exit and no process-group
  residue, and combines that proof with a real kill-child PID-namespace probe.
  Production Candidate A/B runs must pass; portable denial is a structured,
  reason-bearing skip only. Recovery reads PID, process group, state, and start
  identity from one `/proc/<pid>/stat` snapshot, and deterministic replacement
  fixtures pre-create a distinct inode before `os.replace()`.
- Production and root-host containment continue to discover the real
  `unshare --fork` child by repeated `/proc/<supervisor>/task/<supervisor>/children`
  observations. Only the seven independent fake-`unshare` unit cases inject a
  fixture-owned observer; it accepts a child PID only after an fsynced receipt
  binds the queried supervisor exactly. This isolates host procfs scheduling
  from portable fault injection without weakening the production path.
- The integration has separate isolated-diagnostic and normal live-Portage
  policy lanes. Only the production coordinator lane can authorize Phase 2.
  The live lane binds the complete effective last-token-wins `FEATURES` state,
  normal sandbox/userpriv/namespace policy, exact direct compiler paths,
  disposable Portage/log/depcache/dist/binpkg/compiler-cache roots, and
  before/after sentinels for the corresponding live roots.
- The repository implementation writes only `sample.prof`, publishes it
  transactionally under the full generation/inventory lock hierarchy,
  validates it with sample-aware `llvm-profdata show --sample`, and records the
  exact mapped binary build ID, `.text` SHA-256, full-file identity, perf
  identity, conversion command, and immutable conversion log. The dispatcher
  has only the `-fprofile-sample-use`/`-fsample-profile-use-profi` sample
  consumer; the IR consumer remains separate.
- Both restored-content conversion-log regressions now construct a second
  regular file, assert that its device/inode identity differs from the recorded
  observation, restore the exact bytes and mode there, and publish it with
  `os.replace()`. They therefore test an observable same-content replacement;
  they no longer claim that a current-state validator can detect perfectly
  restored in-place history without an external historical authority.
- Mapping-input and consumer-build fingerprints are distinct identities. The
  Portage fixture binds the observed mapping receipt, representative perf
  data, `llvm-profgen` conversion, immutable manifest/sidecar, generated
  assignment, exact consumer receipt, runtime equivalence, and tamper/mismatch
  rejection. Every predicted flag, effective feature, USE flag, ordered
  package environment, build axis, and artifact identity must match.
- Production locks and immutable profile artifacts are root:`portage` readable
  without becoming Portage-writable: runtime directories are `0750`, stable
  locks and profile/metadata/manifest files are `0640`, conversion/perf inputs
  are `0440`, and the mapped input is `0550`.
- Repository fixtures exist for syntax, pinned ShellCheck, strict runtime
  typing, unit behavior, transaction recovery, and portable policy. Exact-CPV
  successor `d42a0cea` passed its portable boundary, but remains superseded
  portable-only evidence. Policy-migration predecessors `04c1becd` and
  `e2bd3187` are rejected. Successor `cbee0c28` repaired the whole-CPV kernel
  exclusion and restored the static contract, and `0a2e16bb` then closed its
  two bookkeeping defects, passed exact run `33879183160`, and passed the
  exact-clean non-package-mutating Gentoo-host preflight. The gate-enabled
  successor must now reproduce its own portable CI and prerequisite/host
  boundary before publication.
  No portable result substitutes for an installed candidate or the supervised
  live Portage chain. The four boxes below remain open until that live proof
  exists.

- [x] Write sample profiles to `sample.prof` or another unmistakable sample-profile name. Live Candidate-A production gate v22 produced and retained `sample.prof` in its immutable profile evidence.
- [x] Validate them with an LLVM sample-profile-aware command. The v22 production receipt binds the `llvm-profgen` conversion and sample-profile validation outputs.
- [x] Consume only through `-fprofile-sample-use`. The v22 sample-use build evidence records the exact sample-use flags and consumer fingerprint.
- [x] Preserve binary build ID and `.text` hash metadata. The v22 production evidence retains the mapped build identity and `.text` metadata for the sample-use consumer.

## 11.5 Implement BOLT input capture hook

- [x] Implement and validate non-mutating, hardlink-aware exact ELF capture from `${ED}`.

During `post_src_install`, when `GENTOO_OPT_MODE=bolt-capture` or an equivalent marker is active:

1. enumerate regular ELF files under `${ED}`;
2. resolve hardlink groups without duplicating work;
3. classify ELF class, type, machine, executable sections, symbols, relocations, build ID, and `.text` hash;
4. copy eligible unstripped candidates to the BOLT input cache;
5. preserve relative install path, ownership intent, mode, xattrs, capabilities metadata, and symlink topology;
6. write an artifact manifest;
7. never modify `${ED}` in capture mode.

The capture transaction enumerates every regular inode group and symlink below the supplied staging root, refuses external hardlinks and unsafe/symlinked roots, and copies each eligible inode once with no-atime reads. ELF64 x86-64 `ET_EXEC`/`ET_DYN` readiness requires executable code, a nonempty `.text`, a defined function symbol, full symbols, `.rel[a].text`, and a GNU build ID. The manifest records the full file hash, build ID, `.text` hash, class/type/machine, hardlink/symlink topology, mode/UID/GID, xattrs, and file capability. Automatic failures are deliberately named `readiness_failures`; they remain remediable pending classifications and never become terminal exclusions without separate reviewed evidence. Captured objects are private mode `0600`, and before/after tree evidence proves capture does not mutate `${ED}`.

## 11.6 Implement BOLT deployment hook

- [x] Implement and validate exact-input, rollback-safe BOLT deployment inside `${ED}`.

During final `post_src_install` with BOLT deployment enabled:

1. enumerate candidate files under `${ED}`;
2. compute exact build ID and `.text` hash;
3. locate the prepared BOLT output for the matching package fingerprint and input identity;
4. fail if the input identity differs;
5. preserve a copy for diagnostics;
6. replace the file in `${ED}` while preserving mode, ownership intent, xattrs, capabilities metadata, and hardlink topology;
7. verify the replacement is a valid ELF and contains `.note.bolt_info`;
8. allow Portage’s later strip/splitdebug/binpkg steps to proceed normally.

The hook must not modify installed `/usr` files directly.

Output registration now requires both the prepared BOLT output and its exact captured input, and rejects a full-file, build-ID, or `.text` mismatch before publishing anything. Deployment requires exact output coverage for all eligible artifacts, validates every input and output before mutation, preserves diagnostic preimages, stages same-inode groups, atomically replaces only staging-tree entries, and keeps final BOLT-note/hash/metadata/topology verification inside the rollback boundary. A forced post-rename verifier failure restores all three fixture inputs byte-for-byte and restores the two-name hardlink group before returning failure. The root-only fixture is designed to prove setuid, a real file capability, user xattrs, ownership intent, hardlinks, symlinks, and runtime behavior survive; its required privileged branch must rerun against the exact Candidate A. The non-root fixture emits an explicit capability skip and exercises the remaining invariants. The tool refuses `/`, `/usr` and descendants, overlapping roots, symlink components, prepared-output symlinks, and installed `/usr` modification.

The current repository architecture retains the 2026-07-15 privilege-metadata remediation but awaits Candidate-A live revalidation. Both deployment and rollback stage in the only safe order: private copy, ownership, every hardlink, ordinary/capability xattrs, and final setuid/setgid mode last, followed by completed-inode-group verification and an inode fsync before rename. The root-only matrix includes `04755`, `02755`, `06755`, user xattrs, `security.capability`, hardlink groups, and rollback, but it must rerun without a privileged-metadata skip against the exact Candidate A. Static PIE is a distinct fourth real BOLT class: `ET_DYN` without `PT_INTERP` but with `DF_1_PIE` is an executable, while an `ET_DYN` object without the PIE flag remains a DSO. The four-class perf/BOLT and root staging fixtures must rerun against Candidate A. Production framework/project/generation locks are root:`portage` `0640` below a root:`portage` `0750` runtime directory; the disposable Portage gate must prove compile phases run as `portage` while `pre_src_install`, `src_install`, `post_src_install`, and the final install-QA hook run as root, so BOLT does not require disabling `userpriv`.

> Historical Phase 2 evidence (superseded; not authorization): §11.5–11.6 evidence was `/var/lib/gentoo-optimization/reports/phase-2-bolt-hooks-20260712` (manifest SHA-256 `1d01fe4e7770ec2ad787c00aa05248281fced4d5946b7b7adb48cf9f59ed7cad`) and component state SHA-256 `57469e99bef2df7a96c7170fe55171b7e8257c27ab5468555b452dbaeb92ecf2`. Its tested artifact-tool SHA-256 was `f43d1a5fe95dae797071a19a25243e5e19eb3e455d7cf9efe67117bd54a2ad9b` and fixture SHA-256 was `af6b37802b79e0814d2a6d6625b4ba15d637953e73ac735ce32980dfeec36225`; none describes Candidate A. No installed artifact has been captured or deployed by this repository gate, and no installed-system BOLT coverage is claimed.

## 11.7 Add automated tests

- [x] Complete the combined Phase 2 automation gate; the exact final Candidate-A boundary passed the complete host/production gate with 93/93 top-level cases, 547 required subtests, and zero required failures or skips.
- [x] Install and validate the candidate-complete framework through its exact clean commit, immutable inventory, crash-consistent activation, stable bootstrap ABI, and final `--check` gate. The active root-owned Candidate-A framework and installer exchange fixture both pass these checks.
- [x] Implement and Candidate-A-validate the root-owned detached Phase 2 evidence-index capture and verification contract at its policy-pinned run-scoped path with exact current userspace runtime boot ID, source, tool, test, component-state, and production-receipt identities, without boot-entry or EFI identity.
<!-- gentoo-optimization-phase2-evidence: {"checkbox_sha256":["53b2caaf5b0f9fcdb8d1abe684edec4fdfb78e59c0745daba10f55d06f33f6ec","93d068baa9e5a2986b54ee32dc853c5b799e37eb51282b88686d3731ed86dd9e","9d9dc0a30d819b269d080770be4f3a87a2000605f67a2cdc844c3d7d2ca5abaa","b113c1d7e05127a5e9bbc25eeb2357d4968684c66b8e78aed75ff519c647f3aa","b919f8efb7dac450da14c5a92bd6596e73ec723524e4c63f413a7d6852edfa26"],"claim_id":"phase2-dispatcher","source_sha256":{"optimization/exact-cpv-contract.json":"1a7ff6671e1256bda39170c64e9bb830b1ae115f0e9488f7c8ac4f0cbc9a9d33","portage/bashrc":"fe9e6599b317876bcc8b173e4ca6dbeb071b07f6a853d1c2387657473549c8e4","scripts/optimization/pgo/profile-identity.py":"bc7649e8ae95b76ab0f6f4a78b48020152ffe94cecce1d803daaec2d1d06a4c4","scripts/optimization/pgo/validate-profile.py":"0b8f116b77fb070d55151fbc613cdac40b571b4843699913be7bfcd9af58e295","tests/optimization/test-pgo-dispatcher.sh":"1a43e7b96b5fc135172cc19ccf9d848abed19be5f3f01f87e6ad8757c4323f9f"}} -->
<!-- gentoo-optimization-phase2-evidence: {"checkbox_sha256":["323052220fb98820b9ea55a7379c4491307a3fc13433245205fdc1dd71ec9635","7a7ec726c63582401b91e10899db21dccc91b476a8844da59b3dd4c49d3a598d","a17d041e982cc1a2118a6a5d13201b0f1b94064c5d32c294fe10f33a3c989225","c0be1775132b81e44386f0d3627cab2b6eee172149c9820d4e0721127dc330a2"],"claim_id":"phase2-sample-pgo","source_sha256":{"optimization/exact-cpv-contract.json":"1a7ff6671e1256bda39170c64e9bb830b1ae115f0e9488f7c8ac4f0cbc9a9d33","scripts/optimization/pgo/authorization-token-scan.py":"f678b93a47527f7020dcfc2f364d1027bac2deb7b00e1566d0d88d597b23f20a","scripts/optimization/pgo/production-profile-lock-transaction.py":"aca8bf840d113ec69b4f60cd036f2b8739aa396f1ce013b9028453d7c8f06603","scripts/optimization/pgo/profile-identity.py":"bc7649e8ae95b76ab0f6f4a78b48020152ffe94cecce1d803daaec2d1d06a4c4","scripts/optimization/pgo/validate-profile.py":"0b8f116b77fb070d55151fbc613cdac40b571b4843699913be7bfcd9af58e295","tests/optimization/test-portage-sample-pgo-integration.sh":"dbf8d0a2b9fb28d56cdff115f98e18b5001a0937f12d4c062a8a6762ecd74717","tests/optimization/test-portage-sample-production-env.sh":"2d344437f8551cee518fc5dfb3be62df6b473d34ff0b0a55ee07a78f2737b2ac","tests/optimization/test_production_profile_lock_transaction.py":"33082e8b277bb18899f60345727355b3f1d5a50ca3f9f63d1a5d0ef79cf5d618"}} -->
<!-- gentoo-optimization-phase2-evidence: {"checkbox_sha256":["73a2cf8e2e1c9bf8ed2d4ebd434d7d706be5cf3d1da3b84f05a71a43c0b047ff","dfbb03600b85c0651f6ef0a2a571ea4308d8d0369181dd7b833a2ed344a6a7d7"],"claim_id":"phase2-bolt-hooks","source_sha256":{"docs/bolt-global.md":"bf7123309c70a073b6f128d15278f0c923622747571a87410a304857d544a23e","optimization/exact-cpv-contract.json":"1a7ff6671e1256bda39170c64e9bb830b1ae115f0e9488f7c8ac4f0cbc9a9d33","portage/install-qa-check.d/zz-gentoo-optimization-bolt":"ae301ad44a77b75799527d8e3fb94f2e881b48649caf6bc954851bbb25c0cc8a","scripts/bolt/bolt-package-binaries.sh":"427c59b13bc2f10bffe16ee93a6bec4988854438d783501c628313c442190e72","scripts/bolt/collect-profile.sh":"427c59b13bc2f10bffe16ee93a6bec4988854438d783501c628313c442190e72","scripts/bolt/list-package-binaries.sh":"427c59b13bc2f10bffe16ee93a6bec4988854438d783501c628313c442190e72","scripts/bolt/optimize-binary.sh":"427c59b13bc2f10bffe16ee93a6bec4988854438d783501c628313c442190e72","scripts/optimization/bolt/artifact_tool.py":"9288de921534a3599cffd2a2404d9b574a124ce2997573b1e71d648e8ea829bb","scripts/optimization/bolt/capture-input.sh":"7cafaa23d1aeb0b0b82e396a38af41abedc66f2ae3c69dc68606df46c3077bca","scripts/optimization/bolt/deploy-output.sh":"c450d39836574516945605b3d0da113ab66aee46a7f5b9b41fb20cf353ddc78d","scripts/optimization/bolt/register-output.sh":"addd8c835f8062ae34161a1a1384a2b4c3a5834a1c1c101a8795ceb8cbf9dc8e","scripts/optimization/verify/abi-guard.py":"65cacea82bc8ff76895078864836977b8bd09224750018d56116a2e07533980b","tests/optimization/test-abi-guard.sh":"fe703aab1b7abbfa6b7c80ddf0b2648a4243afc45219967bfa40b023778b34c7","tests/optimization/test-bolt-command-policy.sh":"47ab90aa1ca983d6ca3823bfc84cacd0a9827136fa1b57caadf77f8163630dab","tests/optimization/test-bolt-hooks.sh":"0b68bf116641fc7b3e22cfe5dbb766baa85145d3a8fa5ced04098905555be915","tests/optimization/test-no-legacy-bolt.sh":"24503d4c45d010c57543d33fbcc437de56391440b8968c0cfa73b9259739e8cf","tests/optimization/test-portage-qa-hook.sh":"7445605c103e142469fbb3f7053be34017a0632ff69c88eb95c8ad666e5453c7"}} -->
<!-- gentoo-optimization-phase2-evidence: {"checkbox_sha256":["dae67f64252bc27313cef2073a5d83687556fb6ffd0794a44545dbdce3646f86"],"claim_id":"phase2-automation","source_sha256":{".github/workflows/portable-optimization-validation.yml":"b5cdba69276c95b996f7cdbe1bea8fdda50b6ebe714378c5b248cdf88a8e7e68","docs/binpkg-checkpoint-runbook.md":"3aaf65d00d21b287b506d5cf219d923af3a10093d612882f918874a3393052fb","docs/phase2-production-profile-transaction.md":"61fc18aad413fa2a96e10c1e1575b544c36e8fbc96aeb1c2b4c0ab676146319d","optimization/exact-cpv-contract.json":"1a7ff6671e1256bda39170c64e9bb830b1ae115f0e9488f7c8ac4f0cbc9a9d33","optimization/phase2-authoritative-test-contract.json":"702dfadce392e6959604133bfe82a9cbbf29c8df01743f2f8ef85f22d07ec8dc","scripts/optimization/recovery/create-binpkg-checkpoint.sh":"bf7de664af2580e2ec580a177f89bb512a8da90451a8a86c58ce483ecbd0ecb7","scripts/optimization/recovery/install-jsonschema-prerequisite.py":"1935559a55c2c574276df0c9277069ad88fddbe294b8d5403280f540a13144f2","scripts/optimization/recovery/publish-jsonschema-prerequisite-bootstrap.py":"92dec3987d8418efe40cd1eef82e516ded27147d98d73f8437356ab4bea35414","scripts/optimization/recovery/verify-binpkg-snapshot.py":"2a6a5a1e8f06d6342f68be9b87e0028d1bbc51067994856d204829d9d86f9f08","scripts/optimization/verify/phase2-test-contract.py":"c0c5e8efe7e77cf6a76deec4bf143c87a2d4ab2a1437f316996d26fd6d564f2a","scripts/optimization/verify/run-unittest-suite.py":"d4829a8ebe5cc658d859229da40b5a12a26dfb82974e07b3e61c3987f011b935","tests/optimization/recovery/test_create_binpkg_checkpoint.py":"ac7b0b8abb036a981dca0884b95af66a410bd0a01e572034211df4a22c76ebea","tests/optimization/test-portage-sample-pgo-integration.sh":"dbf8d0a2b9fb28d56cdff115f98e18b5001a0937f12d4c062a8a6762ecd74717","tests/optimization/test-run-optimization-tests.sh":"4591f757145526f112d8da770844e71144aa6283b0ed623fa2915b393944bd40","tests/optimization/test_jsonschema_prerequisite.py":"43e24d525089aabbad2dbc8b3584d5ef04900126a377572e25a036cc6ca3c309","tests/optimization/test_jsonschema_prerequisite_bootstrap.py":"77bf1035aeaf45fa13b4c54a2c2ed76013c0786fd52aff19976630f7d3cf02cc","tests/optimization/test_production_profile_lock_transaction.py":"33082e8b277bb18899f60345727355b3f1d5a50ca3f9f63d1a5d0ef79cf5d618","tests/run-optimization-tests.sh":"d7d878e9b4ce8ca4c5ff411c9f49a40eba1e65011a64ce00602dc54b4c97f2c2"}} -->
<!-- gentoo-optimization-phase2-evidence: {"checkbox_sha256":["21c6b4a6909af5504ed48c32fa00ebfe26d83181f40d7771770475f930a507fb"],"claim_id":"phase2-framework","source_sha256":{"optimization/exact-cpv-contract.json":"1a7ff6671e1256bda39170c64e9bb830b1ae115f0e9488f7c8ac4f0cbc9a9d33","optimization/tmpfiles/gentoo-optimization.conf":"45a37d403db566322f6b9bf01edc4c8bc13c11edaff9efb3460a7216f8dd12b0","scripts/optimization/install-framework.sh":"bb03146eeacf8ac91048f6aeb4bb9946312c69a0ba4e8558ece3a345472d87b8","scripts/optimization/pgo/authorization-token-scan.py":"f678b93a47527f7020dcfc2f364d1027bac2deb7b00e1566d0d88d597b23f20a","scripts/optimization/pgo/production-profile-lock-transaction.py":"aca8bf840d113ec69b4f60cd036f2b8739aa396f1ce013b9028453d7c8f06603","tests/optimization/fixtures/framework-bootstrap/deployed-v1/README.md":"0f934c06d8747702761aa7b82c7506a42db6005dc3372ddafd5fb793aac595ce","tests/optimization/fixtures/framework-bootstrap/deployed-v1/production.sha256":"7a07814b01c4ebdc98aea44a91aa296ee70faa56c44155d847ec2fef39d5e0e1","tests/optimization/fixtures/framework-bootstrap/deployed-v1/render.sh":"3852d325e82deb34d955a866d2b06312ba05a62b1781ec8b86ce5bc9b4b1a144","tests/optimization/fixtures/rename-exchange-mv.py":"d87a386b28392abc177270cd37fd4e8d8f2998d2c9076561e3d750b96b0317af","tests/optimization/test-framework-installer.sh":"f1500ef713e28c3fabfe7322874c748f05c10d64195437c7184e216d5e3240e2"}} -->
<!-- gentoo-optimization-phase2-evidence: {"checkbox_sha256":["a5d1edf4f5d918035f212510ba3991d6ceb8539e9c42a90211fd2cc83e07cc1e"],"claim_id":"phase2-evidence-index","source_sha256":{"docs/phase2-production-profile-transaction.md":"61fc18aad413fa2a96e10c1e1575b544c36e8fbc96aeb1c2b4c0ab676146319d","optimization/exact-cpv-contract.json":"1a7ff6671e1256bda39170c64e9bb830b1ae115f0e9488f7c8ac4f0cbc9a9d33","optimization/phase2-authoritative-test-contract.json":"702dfadce392e6959604133bfe82a9cbbf29c8df01743f2f8ef85f22d07ec8dc","optimization/phase2-evidence-policy.json":"ef9822982983f27483f36e43d85d5b563a196047e8ecb7f7966d9f80ffe72b1d","optimization/phase2-tool-manifest.json":"52e2e7d096bf2d07ca23576cff1ad411dd4084aa1e998873f22b89cc9410432b","optimization/schema/phase2-component-state.schema.json":"d8dc9ba18168d1a4d3a00c3c59ac92d7b764cbeb8b88ae5b1174d26d4567fd88","optimization/schema/phase2-evidence-index.schema.json":"441d4c077253e569ea8f1c46b683a3bac78687bea29a42e73db9d9c28f70dc41","scripts/optimization/verify/phase2-evidence.py":"ee26bdfc554d85051fcefe197d7c3c4ff3cb30bbb4a67a99b4635ade22b08117","scripts/optimization/verify/phase2-test-contract.py":"c0c5e8efe7e77cf6a76deec4bf143c87a2d4ab2a1437f316996d26fd6d564f2a","tests/optimization/test_phase2_evidence.py":"f8a5f904455ab834c93be4c47e85e77c821174cbbbd78c3ae385a5977066a62b","tests/optimization/test_phase2_test_contract.py":"70b9917279c73130124dbcade580209c410d7a6f2fb180215c10a0641959da23"}} -->







The framework publisher is candidate-complete and crash-consistent: Portage, overlay, helpers, schemas, QA logic, generated policy, and the manifest live inside one immutable candidate. First migration installs a fail-closed Portage guard before its fsynced activation journal; normal upgrades change behavior only through the atomic `framework-current` rename. Each installed bashrc embeds and exports its exact candidate target, and stable shell/Python/QA bootstraps honor that pin, so a build begun on generation A cannot call generation B after an upgrade; re-sourcing another generation fails closed. Candidate Python helpers run isolated with bytecode writes disabled, and a terminal inventory check must prove helper execution did not change the immutable candidate. Fixed bootstrap bytes are an invariant upgrade ABI and a changed renderer is rejected before external publication. The hermetic fixture contains SIGKILL cases on both sides of activation, same-generation re-source, an old-bound process across activation, cross-generation rejection, and incompatible bootstrap migration; the exact final-tree fixture run and live filesystem exchange proof remain pending. This remains a repository/framework claim until the clean live install and combined host gate pass.

Python bootstrap schema v2 uses `#!/usr/bin/python3 -IB` and an exact
`/usr/bin/python3 -I -B` handoff, so bytecode is disabled before standard-library
imports. The only accepted migration source is the byte-exact ten-helper schema
actually deployed by installed framework commit
`8a1200915d2693fd7486a421a9b232f638e9840c`; an independent golden renderer and
ten SHA-256 values bind it. The actually deployed twelve-helper intermediate associated with `5a48ac70...` is accepted only by its exact authenticated matcher; any separate Git-only hybrid, if present, remains deliberately unsupported. Migration atomically exchanges the
complete helper tree. At both injected `SIGKILL` boundaries the fixture compares
type, relative path, mode, UID/GID, regular-file SHA-256, and symlink target for
the entire tree. After the post-exchange crash it runs strict `--check` and old
and newly added representative helpers before any repair/idempotence pass. The
same exchange path still requires proof on the live XFS destinations.

The detached evidence contract avoids self-referential plan hashes and stale prose authorization. Candidate A is a non-authorizing implementation/live precheck. After A passes, generated claim markers and truthful boxes are committed as Candidate B; the complete gate then reruns against B with no later plan edit. The sole accepted index path is `/var/lib/gentoo-optimization/state/project/phase2-evidence/<run-id>/index.json`. It binds one clean commit/tree, the current userspace runtime boot ID (never firmware/boot-entry identity), active immutable candidate, exact production transaction receipt and validation input, required tool and test identities, eleven immutable run-scoped component states, exact directory membership, and aggregate `pending_total=0`, `unknown_total=0`, `failed_total=0`. Reboot, source drift, candidate drift, partial transaction debris, extra state entries, or any plan correction invalidates authorization and requires a new run ID and complete rerun.

The test driver has distinct `smoke`, `checkpoint-smoke`, `portable-complete`,
`stress`, `capabilities`, and `authoritative` modes. `checkpoint-smoke` selects
18 exact methods: four supervisor containment/release paths, nine portable
fake-`unshare` terminal/watchdog paths, and five checkpoint state-machine/
process-group paths. The complete 79-method recovery matrix remains part of
`portable-complete`. Every top-level case publishes a
structured completion row; every conditional shell branch and every Python
`unittest` method publishes its own required/diagnostic row. Atomic shell
fixtures with no conditional branch are represented by their fail-closed
completion row. Legacy `SKIP-SUBTEST`, `HOST-SKIP`, and Python `unittest` skips
are surfaced rather than hidden by a top-level pass. The exact
top-level topology, exact unittest identities/counts, structured ledger hash,
and zero-discovery rule are reviewed inputs. Authoritative mode requires zero
top-level skips and zero required internal skips; portable skips require an
exact allowlist and cannot grow silently. The detached verifier reloads the
tracked reviewed tool manifest and rejects deleted, duplicated, reordered, or
substituted index tool specifications before re-observation. The reviewed
host/tool boundary includes containment, atomic-publication, ELF,
metadata, hashing, text-processing, Git/tar, compiler, Portage, and profiling
primitives plus the reviewed core `jsonschema` schema-validation distribution
closure (`attrs`, `referencing`, `jsonschema-specifications`, `rpds-py`, and the
conditional `typing-extensions`). The boundary separately binds the stable
bootstrap's requested `/bin/bash` path (in addition to `/usr/bin/bash`) and the
installer's exact `/usr/bin/tr` text-processing primitive.

The test-execution core is a separate reviewed subset of that complete tool
manifest. Repository-root discovery uses only Bash builtins. Authoritative
policy parsing uses the absolute reviewed `/usr/bin/python3` bootstrap before
the complete core is bound; portable bootstrap selection is non-authorizing and
recorded. Authoritative runs use the policy-pinned PATH and exact requested
entry points for every reviewed manifest tool addressable by its command name,
including Bash, environment, Git, Python, session/deadline control, ShellCheck,
bounded polling, ELF/binutils tools, the default compiler, and file inspection,
while portable runs may select another absolute entry point but must record it.
Authoritative Bash must also have the exact reviewed path in command-line
`argv[0]`, not merely matching executable bytes. An authoritative ShellCheck
override must equal the manifest entry point; portable alternatives are
recorded. Provenance binds requested
object lstat/symlink identity, resolved/version/file identity, the active Bash
process, and the Python runtime at both start and finish; the detached index
then requires exact equality with independently re-observed reviewed tools.
Candidate-B state, capture, and verification operations additionally bind their
own active Python runtime after the reviewed Gentoo `python-exec` entry point
has selected its final interpreter, and Git cleanliness is observed by the same
bounded isolated execution boundary. The workflow invokes `/usr/bin/bash` and
`/usr/bin/python3` explicitly and uploads the successful result, subtest,
summary, contract, and provenance evidence as well as failure evidence.

A read-only 2026-08-02 live-host PATH preflight used the reviewed authoritative
`/usr/bin:/usr/lib/llvm/22/bin:/bin` order and proved that all 69
basename-addressable tools in predecessor `44ead3d`'s 77-entry manifest
resolved to that exact requested entry point (`failures=0`). Its focused nine
version probes passed; it did not execute every declared manifest version
probe. The successor manifest at the 2026-08-09 checkpoint had 79 entries and
71 basename-addressable tools after adding the `/bin/bash` bootstrap and
`/usr/bin/tr`; later prerequisite/tool-authority work changed that boundary
again. The final exact candidate requires a fresh complete preflight rather
than inheriting either historical observation.
The root-owned 25-file historical preflight is retained at
`/var/tmp/gentoo-optimization/candidate-a-source-preflight-20260802/live-tool-preflight-44ead3d`;
its relative `CONTENT-SHA256SUMS` verifies and has SHA-256
`e0625f66807a8455387e59c028980de2b112da1075436240efc0c94c97efab30`.
The complete path-resolution TSV is
`/var/tmp/gentoo-optimization/candidate-a-source-preflight-20260802/full-manifest-path.tsv`
with SHA-256
`98008e7f6d880d2ee9a482bcc022cf2a7164839c29d92093a8b4f6a707323c0b`; the
focused LLVM/core TSV is
`/var/tmp/gentoo-optimization/candidate-a-source-preflight-20260802/focused-path.tsv`
with SHA-256
`33cde6f50941cdbec916d8648bfbb0fd95d8dee89f5125abb3765f7eef82fa2e`.
This is a non-mutating historical-host coherence observation only; it does not
substitute for the fresh exact-candidate authoritative or Candidate-B gates.

The Candidate-A correction removes raw `os.fork()` and process-wide child-
subreaper mutation from the multi-test unittest process. A dedicated
`checkpoint_process_supervisor.py` remains in the driver's exact case process
group and requires `/proc/self/task` to contain exactly its own PID immediately
before raw fork. It starts each checkpoint target in a private session, writes
target output to regular files rather than inherited capture pipes, and binds
parent/target PID and start identities. It must drain every recorded live
fixture descendant, reap the exact target and every adopted child returned by
`waitpid`, and publish a typed v4 terminal receipt. The caller independently
rejects any recorded residual PID/start identity that still exists; a reused
numeric PID with a different start identity is not confused with the old
process. The target-release pending-signal observation is the documented
linearization point: an interruption already handled or pending before that
point closes/rejects the release gate, while a later interruption is a
post-commit cancellation that drives bounded teardown. External fixture
barriers exercise pre-fork, pre-release, and masked-pending interruption, and
the receipt proves whether target release was ever committed. The
portable fake-namespace watchdog is a deterministic test adapter only; the
three opt-in root-host checkpoint methods remain the mandatory proof of real
pidfds, `unshare --kill-child`, and filesystem durability. The helper's normal,
interrupted, hard-deadline, malformed-receipt, and exact-driver paths must all
pass before the exact clean Candidate-A repository boundary can be accepted for
live precheck. Phase 2 authorization still requires the frozen Candidate-B
rerun and its detached evidence index.

All Git observations used by the detached evidence verifier now share one
60-second hardened runner with system/global configuration disabled. Attached
and detached `git symbolic-ref -q HEAD` outcomes are handled explicitly as
return codes 0 and 1; an unbounded direct subprocess is no longer used. The
portable workflow no longer accepts the mutable Ubuntu ShellCheck package as
the reviewed identity: it downloads the official 0.11.0 archive, verifies
archive SHA-256
`8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198`,
verifies executable SHA-256
`4da528ddb3a4d1b7b24a59d4e16eb2f5fd960f4bd9a3708a15baddbdf1d5a55b`,
and checks the exact reported version before running the driver. Local success
with both 0.9.0 and 0.11.0 is diagnostic only; the final clean candidate still
requires a green exact-SHA workflow run.

The superseded pre-prerequisite deterministic contract at that checkpoint
discovered 33 top-level cases, 58 shell sources, 36 evidence tests, one
dedicated stress test, 206 main tests, and 79 recovery tests. The exact unittest
identity hashes
are `cc631c2bba201c6b754ac6344ee167adde640d6caac083b20fa0bf027f3f7f06`,
`99dbe6b4806232fcb55e043b38a940d720b28775d387824f2cd98aeed4e65c84`,
`7c95b508cc1d5d271358c6da73d0631e15c9fada25c1584dcb2c791a744a31a7`,
and `778339462268073b3435c4de8f8a60124e3bd8e83d5bc5b886217684b74cbe65`
for evidence, stress, main, and recovery respectively. The superseded
pre-execution-identity 18-method `checkpoint-smoke` run at
`/tmp/gentoo-opt-checkpoint-smoke-candidate-a-20260729-v8` passed with 62
top-level passes, zero failures, eight exact mode/provenance skips, 81 required
subtest passes, zero required failures, and zero mandatory internal skips. Its
`summary.txt`, `results.tsv`, and `subtests.tsv` SHA-256 values are respectively
`f9868614ed3723856bf068a6ad8f2388aa008e73daaef7a61437e9354fe1436e`,
`0150a9b1258b118e270b4ce30b42b31d5c301019dc11d21ef6a44f0267ac3363`,
and `6170906cfad5e069be03a589f0df4667a0e9dc1d4bc14b6d26ad684144470b44`.
Its four supervisor, nine fake-namespace terminal/watchdog, and five
state-machine/process-group methods all passed through the exact project
driver of that superseded source revision.

Commit `44ead3d66670a1ee8a9d3aace8fc0945cbb2d130` then passed exact-SHA GitHub
Actions run `30565522289` (`portable-complete`) on 2026-07-30 from
17:20:22Z through 17:43:21Z. The retained artifact reports 77 top-level passes,
zero failures, 14 reviewed portable skips, 379 required subtest passes, zero
required subtest failures, 23 reviewed required skips, nine mandatory internal
skips, one diagnostic internal skip, and exit status zero. SHA-256 values for
`results.tsv`, `subtests.tsv`, `summary.txt`, the contract log, and provenance
are respectively
`c75637997db3eb50aca200971b253393aff3c9c2ee07511568687edc3345ed81`,
`c26bfa7d890a54da2f0639d563d049ee46eedcd2ebd80f3dfdaee01c42d1a8b4`,
`4d49b0bf0e641086f37d1e73a07e34c23ca173ff9ef14b722e45b84322ef3bb0`,
`94603fd460ca913eec48fbdb14cdeeb1a9da62aaefc0e5b9332c418e21cd092c`,
and `eb51aa3d58410bfedaf90e39fa9f8942d3a6af0a9312ee2152cd377b40eac2c3`.
The root-owned retained artifact is
`/var/tmp/gentoo-optimization/ci/44ead3d66670a1ee8a9d3aace8fc0945cbb2d130/run-30565522289`
(`root:root`, directory mode `0750`, file mode `0640`). Its location-independent
`CONTENT-SHA256SUMS` covers 199 files and verifies; that manifest's SHA-256 is
`b760c7dcb750a319c648c24718226be71b78e07608245178f3174e24f0aee627`.
This proves that predecessor's portable boundary only; it was superseded by
later plan, runbook, and workflow corrections and never passed the live-host or
supervised production gates. At that checkpoint, the next corrected revision
therefore required a fresh exact-driver `checkpoint-smoke`, complete clean
`portable-complete`, and exact-SHA CI run.

The first complete superseded-tree diagnostic at
`/tmp/gentoo-opt-portable-complete-candidate-a-20260729-v6` proved all 198 main
and all 68 recovery tests, the framework installer, evidence contract, BOLT
hooks, and the remaining executed fixtures, but correctly remained failed and
non-authorizing: old repository bytecode residue predated the run, and the
non-root live-policy probe treated an unreadable root-private installed
candidate as an unexpected error. Its `summary.txt`, `results.tsv`, and
`subtests.tsv` SHA-256 values are respectively
`f20ed2b83f42e8f5c4b69851685935d4e366fab6b9324535622f47d1e8397820`,
`75a39b1ed7cc2cc2b06c8f6e8af5cc1de30cf327bb6971c915f906aba55dd882`,
and `ce1ebb56ca77b1a1d64ce17cb44eb185cc6797e0f6c6665b8b2067ff5ae63963`.
The residue is removed, and a direct rerun of the corrected production-
environment fixture exits zero while publishing the reviewed required
`live-portage.policy` skip. At that historical checkpoint, the complete
portable boundary and exact-clean-commit CI result remained pending.

The following non-authorizing 2026-07-26 checkpoint is retained as historical
evidence for the superseded in-process child-subreaper harness; its evidence
and recovery counts and identity hashes are not the current contract. That dirty-tree
stabilization checkpoint had exact
deterministic discovery of 33 top-level cases, 58 shell sources, 31 evidence
tests, one dedicated stress test, 198 main tests, and 56 recovery tests. The
reviewed portable skip policy is sorted and its exact contract check passes.
The new child-subreaper harness passed a 20-cycle fast fork/`setsid` escape
stress, preserved an unrelated baseline child, and restored the caller's prior
subreaper state. The retained stress log is
`/tmp/checkpoint-fast-escape-stress-20260726.log`, contains 20 terminal `PASS`
rows, and has SHA-256
`9a6a1d441ffedbae736b6356e4e615599bd179f021e97afbe0425498fe2454cb`.
The six-selected-method `checkpoint-smoke` run at
`/tmp/gentoo-opt-checkpoint-smoke-containment-final-20260726` passed with 62 top-level
passes, zero failures, eight exact mode/provenance skips, 69 required subtest
passes, zero required failures, and zero mandatory internal skips. Its
`summary.txt`, `results.tsv`, and `subtests.tsv` SHA-256 values are respectively
`0904e999aaed0a160563acb0d68f4b1016b836355a61f90e5f309811f5e5eeea`,
`56027b2eb3a459a1646d58dfe93ed166974df4adc440918d97bd88610f581e84`, and
`14c6b25606d4b90d7db1da9e24d7e8c9f2d134c97ce8ae9dda04ce6b27c57958`.
The exact topology hashes are
`fccf6c55f17dbc065cbea1d39ff8e206fbb25e05ea5cb9352af31059fffec55d`
for the 198-test main suite and
`41a53c7e9d481ddb0a9020ee74b670d1c64c245e1abbf0db524cb6378d16294b`
for the 56-test recovery suite. The complete dirty-tree recovery matrix passed
all 56 tests with the three exact root-host capability
methods surfaced as portable required skips; its structured ledger at
`/tmp/recovery-complete-current-subtests.tsv` has SHA-256
`422fc0277a5c4cbd7c85ecf80f21cbb1068df9971096e9ca0464913d9e3bcac5`.
The exact root-host rerun and complete mirror precheck are recorded below;
clean-commit provenance and a green CI run were still pending at that
historical checkpoint and are not inferred from this dirty-tree result.

Containment preflight evidence now distinguishes a disposable exact-child
pidfd `SIGTERM` proof from a real `unshare --pid --fork --kill-child=KILL`
proof. The latter runs from a coherent host `/proc` view, kills the exact
supervisor through its pidfd with `SIGKILL`, requires return code `-SIGKILL`,
and proves teardown of the namespace child and an escaped `setsid` descendant
including both private process groups. The checkpoint's tracked-child and VDB
lock launchers use parent-death `SIGKILL`, so a coordinator `SIGKILL` cannot be
defeated by an `unshare` implementation that survives or forwards `SIGTERM`.
The three root-host checkpoint primitive tests were rerun against checkpoint
script SHA-256
`077eba6ba27f642f3840475a5b503176c3cc46d2095d7fb27f681ef885cc2df3`
and fixture SHA-256
`f5a9c1be08ac1c53e45f978b58644d18f706416708ed7183eccb95fa481f9290`;
all three passed with zero skips. Their structured ledger at
`/tmp/checkpoint-host-capability-subtests-final.tsv` has SHA-256
`657f96ff703ed04ff08435c0cd9b8a7ad3c850b529db9041907ee3415413f49c`.

An earlier clean-mirror precheck reached every portable case; its terminal
results were not retained and are treated only as an unbound diagnostic
observation. The observed failure was the dispatcher fixture treating the managed namespace's
overflow UID for exact `/usr/bin/jq` as a production ownership failure. The
bounded fixture path now continues to execute exact `/usr/bin/jq` while
waiving only that unobservable ownership assertion; production retains the
complete root/ancestor check. The dispatcher subsequently passed all 45 cases
both in the live checkout and a fresh `/tmp` mirror. This earlier failed run is
diagnostic only. The subsequent exact source mirror at
`/tmp/gentoo-candidate-a-mirror-20260726-v2` was created by applying the
then-uncommitted source patch whose source and applied-copy SHA-256 values both equal
`cdb08db28802cfff7900eb188d7c8ff9bfb69e03f296e163ae29c6a295dabbbb`.
Its complete `portable-complete` run at
`/tmp/gentoo-opt-portable-complete-candidate-a-mirror-v2` passed with 78
top-level passes, zero failures, and 13 exact portable skips; it recorded 355
required subtest passes, zero required failures, 26 required portable skips,
one diagnostic skip, and zero unexpected results. The SHA-256 values for
`summary.txt`, `results.tsv`, `subtests.tsv`, and `test-contract.log` are
respectively
`de85099bfc95cde0fa24f2e7479e32a6d7b42a786e55ad16a61d4c068b9f621f`,
`7029f960da5f6423185118b952590203e44d41541a0afec798810d211e8493a7`,
`f724a19cd09adf5298da66a3efa3609a9d08e7f3c4b116ecf9f64ef55e98c1c1`,
and `b4777b5842b67dce71f32c13acee03d741f5676d2ce468ad58b1be814b7b346e`.
This remains a dirty-source, non-authorizing mirror precheck: exact
clean-commit provenance and a green current-commit CI run remain required.
The mirror, smoke, checkpoint-smoke, recovery, stress, host-capability, patch,
and evidence ledgers cited in this checkpoint were copied byte-for-byte into
the root-owned durable tree
`/var/lib/gentoo-optimization/reports/candidate-a-repository-precheck-20260726`;
the individual hashes above continue to identify the preserved payloads.

The system Python does not yet contain that closure, so an exact current-CPV
recovery checkpoint must precede its source installation. Checkpoint creation
has no retirement transition and deliberately ends at
`selector-activated-offline-restore-pending`; therefore the pre-install
checkpoint must complete its own exact offline binary restoration and reach
`offline-restore-proven` before the closure is installed. The post-install CPV
set then requires a second independently verified checkpoint and a second exact
offline restoration/finalization. Both canonical checkpoint states must report
`pending_total=0`, `unknown_total=0`, and `failed_total=0`; restoring only the
post-install checkpoint would leave the pre-install state nonterminal and is
forbidden.

Repository authorization commands no longer use visual hash comparison.
Immutable bundle and installer copies are compared programmatically, root Git
materialization runs with no system configuration and a private empty HOME,
and root-owned state/cache/evidence existence checks run through `doas`. A
portable GitHub Actions workflow is configured to run the exact
portable-complete contract. The rejected `1729e9a` run skipped the hermetic
framework installer because the runner's forced PATH did not expose Ubuntu's
`/usr/sbin/runuser`; the current workflow correction verifies that package
identity, exposes the reviewed `/usr/bin/runuser` entry point, and makes a
`framework-installer` `PASS` row mandatory. A green run proving that correction
for the final Candidate-A commit is required before live installation. Mutable
Ubuntu package versions and the current Node action-runtime deprecation warning
remain deferred CI-maintenance issues, not permission to weaken or skip this
gate. The 300-cycle crash workload is confined to `stress` and `authoritative`
modes.
All of this remains non-authorizing until one clean Candidate A passes live and
one frozen Candidate B reruns the complete gate into its detached index.

Create fixture tests for:

- package fingerprint stability;
- compiler-lane rejection;
- ABI separation;
- missing profile rejection;
- IR/sample format separation;
- BOLT candidate classification;
- build ID mismatch rejection;
- `.text` mismatch rejection;
- hardlink handling;
- symlink handling;
- setuid and file-capability metadata preservation;
- package with no ELF files;
- package with mixed 32/64-bit files.

Commit clean Candidate A before its live precheck. After A passes, commit
Candidate B with truthful checked claims, then rerun the entire gate and
authorize only through B's detached index.

---

# 12. Phase 3 — Inventory every installed package and artifact

## 12.1 Freeze package state before inventory

- [ ] Sync repositories.
- [ ] Complete the normal userspace system update first, excluding every package operation that would build, install, configure, or deploy a kernel/initramfs or modify the boot chain.
- [ ] Resolve all blockers, preserved libraries, and configuration updates.
- [ ] Run depclean in pretend mode and decide whether intentional orphans remain in scope.
- [ ] Freeze package changes during the optimization generation except for fixes required by the project.

## 12.2 Generate complete inventory and userspace mutation sets

Inventory every CPV in `/var/db/pkg`, not only direct `@world` entries. Generate
one complete userspace mutation set and one explicit non-mutating kernel-policy
exclusion set:

```text
/etc/portage/sets/pgo-bolt-all-userspace
/etc/portage/sets/optimization-kernel-policy-exclusion
```

Prefer CP atoms in persistent sets and store exact starting CPVs separately in
the generation manifest. Include intentional orphan packages. Every installed
CPV appears in exactly one of the userspace mutation set or the human-only
kernel lifecycle exclusion set. No automated emerge command may consume the
exclusion set.

Generate additional sets from classification:

```text
@pgo-ebuild-native
@pgo-clang-ir
@pgo-clang-sample
@pgo-gcc
@pgo-rust
@pgo-go
@bolt-capture
@bolt-deploy
@optimization-not-applicable
@optimization-kernel-policy-exclusion
```

## 12.3 Inventory owned files

For every installed CPV:

- parse `CONTENTS`;
- identify regular files, symlinks, and hardlinks;
- run ELF classification on regular files;
- record architecture, ELF type, interpreter, dynamic dependencies, symbols, relocations, build ID, executable sections, debug state, and owner package;
- detect `.a`, `.o`, kernel modules, eBPF, GPU objects, firmware, bytecode, scripts, and data;
- record setuid/setgid bits and file capabilities;
- deduplicate identical inodes and build IDs.

## 12.4 Detect build backend

Use multiple sources:

- installed package environment;
- ebuild/eclass inheritance;
- build logs where available;
- package.env compiler overrides;
- ELF `.comment` and producer metadata;
- language-specific metadata such as `go version -m`;
- file contents and owned artifact types.

Do not classify solely from package category.

## 12.5 Build reverse dependency information

Libraries and static archives need consumers for training. Build a reverse-dependency graph using Portage dependency metadata and dynamic ELF `NEEDED` relationships.

For every library package, identify:

- direct executable consumers;
- test suites;
- package build-time consumers;
- desktop or service workloads that load the library;
- static consumers requiring an instrumented dependency/consumer rebuild chain.

## 12.6 Produce the initial coverage report

The report must list all installed CPVs and all discovered artifacts. At this phase, zero packages may remain `unclassified`.

Commit the inventory/classifier implementation and the stable policy decisions, but do not commit machine-generated inventory JSON.

---

# 13. Phase 4 — Build the workload framework

PGO and BOLT quality depend on representative execution. Exhaustive coverage requires workloads for every eligible package or for a consumer closure that executes its code.

## 13.1 Workload contract

Every workload script must:

- be non-destructive by default;
- use a temporary working directory;
- have a timeout;
- write a structured log;
- declare packages and artifacts it intends to train;
- support a quick smoke mode and a fuller training mode;
- clean up processes, mounts, loop devices, namespaces, sockets, and temporary files;
- avoid external network dependence unless explicitly required;
- return nonzero on incomplete training;
- report which target binaries/DSOs actually accumulated samples or raw counters.

## 13.2 Required workload classes

Implement reusable harnesses for:

- compilers and linkers: compile/link C, C++, Rust, assembly, LTO, shared-library, and template-heavy corpora;
- shells and interpreters: execute representative script suites;
- compression/archive tools: compress, decompress, archive, extract, verify, and stream representative corpora;
- media codecs: decode and encode small audio/video/image fixtures through multiple codecs;
- graphics stack: shader compilation, Vulkan/OpenGL capability tests, off-screen rendering, and gamescope/wlroots paths;
- databases: temporary database creation, CRUD, indexing, transactions, vacuum/check operations;
- network tools and daemons: isolated network namespace with local client/server traffic;
- filesystem utilities: loopback image files, temporary filesystems, fsck/read-only inspection, and safe destructive operations limited to disposable images;
- package/build tools: pretend dependency resolution, source unpack/configure/compile fixtures, archive and patch workflows;
- cryptography: digest, symmetric, asymmetric, certificate, and TLS loopback operations;
- text processing: grep/sed/awk/regex/Unicode/XML/JSON workloads;
- GUI applications: headless or nested compositor startup, scripted open/render/close actions where feasible;
- desktop stack: representative Sway/Wayland session operations and IPC;
- game stack: shader-cache generation, Wine/Proton startup, DXVK/VKD3D test workloads, and safe game benchmark paths where available;
- services: temporary configurations and isolated ports;
- privileged utilities: containers/namespaces/loopback files only; never operate on real disks, filesystems, users, boot records, or network configuration.

## 13.3 Library training closure

For each library package:

1. choose multiple reverse-dependency consumers covering distinct APIs;
2. run the package’s own tests where feasible;
3. verify the library’s instrumented module produced raw counters or received perf samples;
4. add another consumer if coverage is empty or obviously narrow;
5. record the closure in package state.

## 13.4 Rare and dangerous command handling

For utilities that cannot be safely run against the real system:

- use disposable disk images, namespaces, fake roots, temporary user databases, mock services, or test fixtures;
- use read-only/help/startup execution only as an initial smoke test, not as the sole representative profile when real functionality can be safely simulated;
- if no safe representative execution can be constructed after documented attempts, use terminal reason `unsafe-to-profile` with exact evidence. This is a last resort and requires review.

## 13.5 Automated real-system session

Create a system workload orchestration script that runs a broad representative session while:

- the full instrumented package set is installed for PGO training; and later
- `perf record -a -e cycles:u -j any,u` is active for BOLT profiling.

The script must include compile, media, graphics, desktop, network, filesystem-image, interpreter, compression, package-manager, and service workloads. Multiple sessions must be collected and merged to avoid one workload dominating the profile.

---

# 14. Phase 5 — Bootstrap optimized toolchains first

The compiler, linker, profile tools, Portage’s Python interpreter, and build tools affect every later rebuild. Optimize and validate them before the exhaustive sweeps.

## 14.1 Bootstrap rules

- Keep one known-good unoptimized toolchain available.
- Do not use a toolchain to profile itself until a complete bootstrap cycle is defined.
- Do not BOLT the only copy of `clang`, `ld.lld`, `llvm-profdata`, `perf2bolt`, or `llvm-bolt`.
- Preserve versioned real binaries and symlink topology.

## 14.2 Clang/LLVM/LLD lane

Implement an upstream-compatible multi-stage LLVM PGO build:

1. stage-1 compiler from known-good toolchain;
2. instrumented stage-2 compiler;
3. representative compiler workload covering C, C++, templates, ThinLTO, linking, debug info, OpenMP host code, and common Gentoo build patterns;
4. merge profile;
5. stage-3 PGO compiler;
6. capture exact unstripped stage-3 Clang/LLD binaries;
7. BOLT profile them through a substantial compilation corpus;
8. create BOLT outputs;
9. install through the package-managed hook;
10. compare compile-time performance and run LLVM/Clang tests appropriate to the ebuild.

Do not rely on the generic per-package hook for the compiler bootstrap if the LLVM build system provides a more correct multistage flow.

## 14.3 GCC/binutils lane

- Prefer Gentoo ebuild-supported `pgo`/bootstrap mechanisms.
- Apply BOLT readiness to GCC-built candidates with `-fno-reorder-blocks-and-partition`.
- Validate compiler test suites and compile/link fixtures.

## 14.4 Python and Portage lane

- Prefer the ebuild’s native Python PGO flow.
- Resolve the repository’s current `-pgo` override only after testing the active JIT configuration separately.
- Rebuild Portage after the final optimized Python is installed.
- Verify emerge dependency resolution, binpkg operations, config protection, and preserved rebuild behavior.

## 14.5 Rust toolchain lane

- Use Rust’s supported instrumentation PGO workflow for `rustc` where the Gentoo build permits it.
- Train with representative crate builds.
- Keep Rust profile data isolated by exact rustc and bundled LLVM versions.
- BOLT the real versioned `rustc` binary only after exact-input validation.

## 14.6 Go toolchain lane

- Optimize the Go compiler/toolchain only through Go-supported PGO inputs where possible.
- Do not pass Clang profile flags into Go compilation.

## 14.7 Verify the bootstrap toolchain

Before continuing:

- compile and run C/C++/Rust/Go fixture suites;
- build and link shared libraries;
- perform ThinLTO and non-LTO builds;
- run an actual small Portage package build;
- verify profile tools can read the formats generated by the active compilers.

Commit the toolchain phase.

---

# 15. Phase 6 — Generate PGO instrumentation builds for all in-scope userspace packages

This is the first exhaustive rebuild of every in-scope userspace package.

## 15.1 Establish the generation

- [ ] Freeze the inventory and generation ID.
- [ ] Create generation-specific raw directories.
- [ ] clear only raw data for this generation, never unrelated profiles;
- [ ] create package/ABI/compiler manifests;
- [ ] generate backend-specific package.env assignments from the classifier.

## 15.2 Clang IR-PGO generation

For eligible Clang-built C/C++ packages:

- add IR instrumentation flags to compile and final link;
- use a generation-scoped absolute runtime profile pattern containing module/build identity and PID;
- keep amd64 and x86 raw pools separate;
- disable ccache;
- ensure Portage sandbox permits only the generation raw path;
- record every compile/link invocation proving instrumentation was present.

A system-generation raw pool may be merged into a generation-wide indexed profile for Clang packages. This is useful for static-library functions whose counters are emitted by consumer binaries. The generation and compiler/ABI boundaries must remain strict.

## 15.3 GCC generation

For GCC-built packages without a correct native ebuild PGO flow:

- use a package/fingerprint/ABI-specific GCC profile directory;
- use GCC generation flags only;
- keep generated `.gcda`/related data isolated;
- add the GCC BOLT compatibility flag for later BOLT-ready builds, not blindly to Clang packages.

## 15.4 Rust generation

- apply absolute `-Cprofile-generate` paths through RUSTFLAGS;
- use `--target` behavior or eclass-specific controls so Cargo build scripts are not accidentally instrumented;
- isolate by rustc version, bundled LLVM profile version, target triple, and package fingerprint;
- disable cargo/ccache paths that could reuse non-instrumented objects.

## 15.5 Go generation/profile-source build

Go PGO is sampling/pprof-based rather than an instrumentation rebuild. Build the baseline package variant required for profile collection and record exact source/build metadata. Do not add Clang IR flags.

## 15.6 Ebuild-native PGO packages

Packages with an ebuild-supported PGO flow should use that flow unless testing proves it broken. Record the ebuild’s internal generation/training/use behavior and final build log.

Do not add generic PGO on top of native ebuild PGO unless explicitly designed and benchmarked.

## 15.7 Rebuild order

Use a dependency-complete rebuild with build dependencies included. Start with bootstrap/system libraries, then the remainder of the installed set. Suggested command shape, adjusted after pretend review:

```bash
emerge -eav @pgo-bolt-all-userspace \
  --with-bdeps=y \
  --complete-graph=y \
  --backtrack=1000 \
  --keep-going
```

`--keep-going` is acceptable for discovery, but every failure must return to pending and be resolved before the phase is complete.

## 15.8 Instrumentation-build completion gate

The phase is complete only when:

- every PGO-eligible package has an installed instrumented or valid native-PGO training variant;
- every failed package has been remediated and rebuilt;
- no package accidentally used a different compiler family’s flags;
- all critical commands still function;
- the system can reboot into the instrumented userspace if a reboot is needed for service/library training.

Commit framework changes and package-specific compatibility decisions discovered during the sweep.

---

# 16. Phase 7 — Train the complete instrumented system

## 16.1 Training environment

- enable the generation-specific profile-write directory;
- confirm normal users and service users can emit unique files;
- set environment overrides only where needed;
- restart long-lived processes so they load instrumented DSOs;
- reboot if required to ensure all active services and desktop processes use instrumented libraries;
- verify several known packages emit profiles before running the full suite.

## 16.2 Natural build-workload training

Use the instrumented system to build a substantial package/corpus workload. This naturally trains:

- Clang/GCC/binutils;
- shells;
- Python and Portage;
- compression and archive libraries;
- libc/libc++/libunwind where eligible;
- build systems;
- linkers;
- filesystem and text tools.

Do not count this as sufficient coverage for desktop, media, graphics, network, or service packages.

## 16.3 Run all workload classes

- [ ] compiler/build workload;
- [ ] shell/interpreter workload;
- [ ] compression/archive workload;
- [ ] media encode/decode workload;
- [ ] graphics/Vulkan/OpenGL/shader workload;
- [ ] compositor/desktop workload;
- [ ] network client/server workload;
- [ ] database workload;
- [ ] cryptography/TLS workload;
- [ ] filesystem loop-image workload;
- [ ] service workload;
- [ ] Wine/Proton/gaming-stack workload;
- [ ] package-specific workloads generated from uncovered targets.

## 16.4 Coverage-driven iteration

After the first pass:

1. map raw profiles to package/artifact identities;
2. identify eligible packages with zero counters;
3. identify libraries with no consumer execution;
4. generate additional package-specific workloads;
5. rerun until every eligible package has meaningful matching data or reaches a reviewed terminal exclusion.

A raw file existing is not enough. It must contain nonzero counters matching code in the target package.

## 16.5 Stop and flush

Gracefully stop instrumented daemons and applications so counters flush. For programs that may terminate abnormally, use continuous-profile mode only after a dedicated compatibility test.

Lock the raw profile generation when training is complete and make it read-only.

---

# 17. Phase 8 — Merge and validate PGO profiles

## 17.1 Clang IR merge

Merge generation-compatible raw profiles into the Clang generation profile. Validate:

- profile format readability;
- nonzero function counts;
- module/build mappings;
- ABI separation;
- compiler major/profile format compatibility;
- absence of corrupt or truncated inputs.

Preserve raw inputs until the final optimized system passes validation.

## 17.2 GCC merge/use preparation

Use GCC’s package-specific profile structure and validate that expected `.gcda` data corresponds to current objects/source identity.

## 17.3 Rust merge

Use an `llvm-profdata` compatible with the Rust-generated raw format. Never assume the system Clang tool’s format is compatible without testing.

## 17.4 Go profile preparation

For every Go main binary:

- produce or merge valid CPU pprof data;
- ensure symbolization, inlined frames, and function start-line information are adequate;
- keep profiles binary/workload-specific;
- never use one executable’s profile for unrelated main packages.

## 17.5 Sample PGO fallback

For Clang packages where instrumentation is unsupported or unusable:

1. build profile-mapping-ready exact binaries;
2. collect branch-stack perf data;
3. convert with `llvm-profgen`;
4. validate the sample profile;
5. mark the package `clang-sample-use`;
6. never merge or consume it as IR instrumentation data.

## 17.6 Profile quality gate

A package may enter PGO-use only if:

- the profile is readable by the exact intended compiler family;
- the profile contains matching nonzero data;
- training workloads are recorded;
- no compiler/ABI/fingerprint boundary is violated;
- mismatch/stale diagnostics remain below the policy threshold;
- a package-specific smoke build succeeds.

Generate a signed-off profile coverage report before the PGO-use sweep.

---

# 18. Phase 9 — Full PGO-use rebuild and BOLT input capture

This is the second exhaustive userspace rebuild. It produces the exact PGO-optimized binaries used for BOLT profiling.

## 18.1 Generate final PGO-use package assignments

Generate package.env entries from the validated state database:

- native ebuild PGO;
- Clang IR use;
- Clang sample use;
- GCC use;
- Rust use;
- Go use;
- explicit non-applicable packages with no generic profile flags.

No package may select a use mode without a validated profile.

## 18.2 Enable BOLT capture readiness

For BOLT candidate builds:

- preserve usable symbol information for the cached input;
- link with GNU build ID;
- link with relocation information such as `--emit-relocs`;
- for GCC-built candidates, disable incompatible block partitioning;
- ensure the capture hook copies candidates before Portage strips them.

## 18.3 Run the exhaustive PGO build

Rebuild every in-scope installed userspace package with PGO-use and BOLT
capture enabled. Do not deploy BOLT yet. Kernel lifecycle items remain
terminal `kernel-policy-exclusion` records and are not passed to Portage by
this project.

The capture hook must populate the exact input cache for every eligible ELF.

## 18.4 Verify PGO application

For every eligible package:

- parse the build log for the correct profile-use flag/backend;
- reject missing-profile and out-of-date-profile failures;
- run package tests/smoke tests;
- verify no generation flags remain in deployed files;
- record the final PGO build fingerprint.

## 18.5 Verify captured BOLT inputs

For each candidate:

- cached unstripped file exists;
- installed runtime file has matching code identity/build ID as appropriate;
- `.rela.text` or equivalent required relocation information is present in the cached input;
- symbol table is usable;
- artifact manifest records installed path and owner;
- no duplicate symlink/hardlink candidate is processed independently.

Do not proceed to BOLT profiling while any eligible artifact lacks a valid captured input.

---

# 19. Phase 10 — Collect exhaustive BOLT profiles

## 19.1 Collect multiple system-wide sessions

Use branch-stack cycle profiles. Collect separate sessions for major workload families so they can be converted and merged deliberately.

Example command shape:

```bash
perf record \
  -e cycles:u \
  -j any,u \
  -a \
  -o /var/cache/gentoo-optimization/bolt/perf/<session>.perf.data \
  -- <workload-or-orchestrator>
```

Collect at least:

- full build/compile session;
- desktop/GUI/graphics session;
- media session;
- network/service/database session;
- compression/text/filesystem session;
- gaming/Wine/Proton session;
- package-specific sessions for uncovered candidates.

## 19.2 Convert profiles per exact candidate

For every cached input ELF:

1. run `perf2bolt` against each relevant perf session;
2. discard only sessions that contain no mapping/samples for that candidate;
3. record conversion diagnostics;
4. merge nonempty fdata inputs with `merge-fdata`;
5. associate final fdata with input build ID and `.text` hash.

## 19.3 Coverage-driven BOLT workload generation

For every eligible artifact with zero or inadequate samples:

- identify the owning package and canonical executable/DSO;
- run its package workload or a reverse-dependency consumer under perf;
- for libraries, use multiple consumers;
- for daemons, use an isolated instance;
- for privileged/destructive tools, use disposable namespaces/images;
- repeat conversion and merge.

No candidate may be considered optimized with empty profile data.

## 19.4 BOLT profile quality policy

Record at minimum:

- profiled function count;
- total function count;
- stale function/profile percentage;
- branch sample count;
- whether relocations were detected;
- whether profile was collected from exact input identity;
- workload sources.

Set conservative rejection thresholds. A default policy may reject:

- zero profiled functions;
- corrupt branch data;
- significant stale-profile diagnostics;
- build ID or `.text` mismatch;
- unsupported control-flow patterns reported as unsafe;
- files with critical BOLT warnings that cannot be explained.

Threshold exceptions require package-specific evidence and tests.

---

# 20. Phase 11 — Produce and validate every BOLT output

## 20.1 Default BOLT command policy

Start from a reviewed default such as:

```text
-reorder-blocks=ext-tsp
-reorder-functions=cdsort
-split-functions
-split-all-cold
-split-eh
-icf=safe
-dyno-stats
```

The literal `ext-tsp`/`cdsort` pair above is the Phase 1 validated default.
Changing either spelling requires the complete ET_EXEC, PIE, and DSO gate to
run again; `hfsort+` is not assumed equivalent from documentation alone.

Do not assume one option set works for every binary. Maintain package/artifact overrides for unsupported EH, jump-table, assembly, DSO, privileged, Go, or Rust cases.

## 20.2 Optimize cached inputs only

Run `llvm-bolt` against the cached unstripped exact PGO input, never an unrelated current `/usr` file.

Store output under:

```text
/var/cache/gentoo-optimization/bolt/outputs/<cpv>/<fingerprint>/<build-id>/<relative-path>
```

## 20.3 Validate every output

For each BOLT output:

- ELF parser/readelf succeeds;
- `.note.bolt_info` exists;
- dynamic loader/interpreter is unchanged;
- `NEEDED`, SONAME, RPATH/RUNPATH, symbol versions, and exported ABI remain valid;
- mode, ownership intent, xattrs, capabilities, and hardlink metadata are preserved in manifest;
- package workload passes against the BOLT output;
- ABI comparison tools pass for public libraries where applicable;
- runtime dependencies resolve;
- a benchmark or perf-stat comparison shows no material regression where measurable.

## 20.4 Test privileged artifacts separately

Setuid/setgid and file-capability binaries require an isolated deployment test. Verify both functionality and security metadata. Do not drop privileges or capabilities through file replacement.

## 20.5 Test shared libraries through consumers

For each BOLTed DSO:

- run direct library tests;
- run multiple reverse-dependency consumers;
- run symbol/version checks;
- run `revdep-rebuild`-equivalent linkage checks in the staging/test environment.

## 20.6 Generate deployment manifest

Only outputs that pass all validation enter the deployment manifest. The manifest must map exact package fingerprint + input identity + relative path to the output file and expected metadata.

The strict goal requires every BOLT-eligible candidate to appear in this manifest. Remaining candidates must be remediated or moved to a reviewed terminal exclusion with evidence.

---

# 21. Phase 12 — Final exhaustive PGO+BOLT rebuild

This is the completion rebuild. It must install final PGO-use binaries and apply matching BOLT outputs inside `${ED}` before Portage stripping.

## 21.1 Preflight

- [ ] All installed CPVs still match the frozen generation or have been re-profiled.
- [ ] All validated PGO profiles are present.
- [ ] All BOLT deployment outputs match expected input identities.
- [ ] All package-specific env assignments are generated.
- [ ] All previous build failures are resolved.
- [ ] Rollback binpkgs remain available.
- [ ] There is sufficient disk space.

## 21.2 Enable final modes

For each package, final mode must combine:

```text
correct PGO-use backend
+
BOLT deployment for matching eligible artifacts
```

Non-machine-code packages still participate in the full rebuild/set processing and receive a final `not-applicable` record.

## 21.3 Execute the full rebuild

Run a pretend first. Then run the dependency-complete exhaustive userspace
rebuild. Use the complete `@pgo-bolt-all-userspace` set rather than relying
only on `@world`, and prove that it is disjoint from
`@optimization-kernel-policy-exclusion` before mutation.

The BOLT deployment hook must fail closed on missing or mismatched outputs. Do not let a package silently install an un-BOLTed eligible binary.

## 21.4 Resolve all failures

For any package failure:

1. preserve log and state;
2. restore package from known-good binpkg if the system is affected;
3. classify root cause as PGO, BOLT, unrelated existing flags, package bug, nondeterministic build, or tooling bug;
4. fix the narrowest cause;
5. regenerate profile/output if identity changed;
6. rebuild and retest;
7. update this plan and package policy.

The final rebuild is not complete while `--keep-going` failures remain.

## 21.5 Complete graph repair

After the full rebuild:

```bash
emerge -av @preserved-rebuild
revdep-rebuild
emaint --check world
```

Use the appropriate installed tools and review all output. Rebuild any affected reverse dependencies with the same final PGO+BOLT modes.

---

# 22. Human-only kernel boundary — not a project phase

Kernel configuration, compilation, installation, replacement, deployment,
initramfs generation, and boot-chain management are outside this project and
may be performed only by a human independently of it. The project must not ask
a human to do that work to satisfy a project milestone.

Automated and LLM-directed work must not process a kernel image or kernel
module through PGO/BOLT, run a kernel training/deployment lane, modify a kernel
or initramfs, or create or alter a boot entry. Kernel lifecycle artifacts are
recorded with the machine-valid terminal reason `kernel-policy-exclusion`,
denoting this human-only system boundary. There are no Phase 13 kernel tasks,
gates, tests, or completion requirements.

---

# 23. Phase 14 — Strict verification and acceptance

## 23.1 Package coverage verifier

Run the strict verifier across every CPV in `/var/db/pkg`. It must fail if:

- a CPV is absent from state;
- final CPV differs from the recorded generation without reprocessing;
- an eligible PGO package lacks proof of profile-use build;
- a profile is stale, missing, unreadable, or from the wrong compiler/ABI;
- an eligible ELF lacks a BOLT output/note;
- an exclusion lacks evidence;
- an artifact ownership/path changed without reinventory;
- any status is pending/unknown/failed.

## 23.2 Installed ELF verifier

Rescan the live filesystem from package CONTENTS. For every eligible installed ELF:

- verify `.note.bolt_info`;
- verify GNU build ID and deployed identity records;
- verify dynamic linkage;
- verify permissions, ownership, xattrs, capabilities, setuid/setgid, symlink and hardlink topology;
- verify public library SONAME/versioning;
- verify no instrumented generation runtime remains enabled in final binaries.

## 23.3 PGO proof verifier

For each optimized package, retain:

- final build log;
- correct backend/profile-use invocation;
- profile metadata and workload references;
- profile validation output;
- final package fingerprint;
- package smoke/test results.

## 23.4 Runtime validation suite

At minimum validate:

- reboot and login through the pre-existing unchanged boot path, recording only
  userspace runtime-after-reboot evidence and never boot-entry state;
- OpenRC services;
- networking and DNS;
- Sway/Wayland session;
- GPU/Vulkan/OpenGL;
- audio/PipeWire;
- Steam/Proton/Wine;
- gamescope;
- browser or WebKit stack where installed;
- Python/Portage operations;
- C/C++/Rust/Go compilation;
- filesystem tools on disposable images;
- package install, uninstall, binpkg restore, and preserved rebuild;
- media encode/decode;
- SSH/local network tools if used;
- ZFS or other external modules if present.

## 23.5 Performance sanity checks

Compare selected high-impact workloads with the recorded baseline:

- Clang build corpus;
- linker workload;
- Python workload;
- compression/decompression;
- media encode/decode;
- shader compilation;
- startup of major applications;
- selected games/benchmarks;
- whole-userspace system workload (excluding kernel profiling or mutation).

Do not require every tiny utility to show a speedup, but reject material reproducible regressions caused by profile or BOLT choices.

## 23.6 Required final report

Generate a report with at least:

```text
installed_packages_total
packages_rebuilt_from_source_total
kernel_policy_exclusion_package_total
kernel_policy_exclusion_artifact_total
pgo_eligible_total
pgo_optimized_total
pgo_not_applicable_total_by_reason
pgo_terminal_exclusion_total_by_reason
installed_elf_total
bolt_eligible_total
bolt_optimized_total
bolt_not_applicable_total_by_reason
bolt_terminal_exclusion_total_by_reason
pending_total
unknown_total
failed_total
```

Strict completion requires:

```text
packages_rebuilt_from_source_total + kernel_policy_exclusion_package_total == installed_packages_total
pgo_optimized_total == pgo_eligible_total
bolt_optimized_total == bolt_eligible_total
pending_total == 0
unknown_total == 0
failed_total == 0
```

Terminal exclusions must not be counted as eligible. The classifier must prove
why they are not eligible, why the upstream/tooling correctness boundary makes
optimization impossible, or why the item is within the explicit
`kernel-policy-exclusion` human-only boundary. Every such package/artifact must
be listed; the exclusion cannot conceal an unknown or pending item.

---

# 24. Phase 15 — Maintenance after completion

A system-wide profile optimization is invalidated incrementally by package updates. Implement maintenance rather than treating this as a one-time experiment.

## 24.1 Package update invalidation

When a package changes:

- compare CPV, ebuild hash, compiler, ABI, flags, source identity, build ID, and `.text` hash;
- invalidate only affected PGO/BOLT states and dependent static-library closures;
- never silently reuse exact-build BOLT output across a mismatch;
- allow only explicitly supported source-stable profile reuse, such as Go’s intended workflow or reviewed sample-PGO reuse;
- queue the package for retraining/rebuild.

## 24.2 Scheduled generation refresh

Create documented commands for:

- incremental package update optimization;
- periodic full workload/profile refresh;
- stale profile reporting;
- profile cache pruning while retaining rollback generations;
- rebuilding a package with optimization disabled for troubleshooting;
- restoring the latest known-good binpkg.

## 24.3 Portage update integration

Normal world updates must either:

1. fail closed when a package update would install an unprofiled eligible artifact; or
2. place the package in a clearly reported temporary baseline state and immediately queue its generation, training, PGO rebuild, BOLT profiling, and final deployment before declaring the system optimized again.

The system-wide “fully optimized” status must be revoked whenever eligible pending work exists.

---

# 25. Package-specific remediation decision tree

For each failing package, follow this order.

## PGO generation failure

1. Verify compiler family and flags.
2. Remove only incompatible profile flag from host/build tools.
3. separate ABI passes;
4. disable ccache;
5. fix profile runtime output path/sandbox;
6. use ebuild-native PGO if available;
7. switch Clang IR-PGO to Clang sample PGO if instrumentation is the blocker;
8. use language-native PGO for Rust/Go;
9. classify terminal unsupported only after evidence.

## PGO-use failure

1. verify fingerprint/compiler/ABI;
2. validate profile format;
3. inspect stale/missing-function diagnostics;
4. regenerate with exact source and flags;
5. broaden training workload;
6. separate colliding profiles;
7. try package-native or sample PGO fallback;
8. never suppress a real mismatch merely to complete the build.

## BOLT capture failure

1. confirm ELF64 x86-64 ET_EXEC/ET_DYN;
2. confirm symbols and relocations;
3. resolve stripping order;
4. add package-specific link flags;
5. handle symlink/hardlink real binary;
6. for GCC add the required block-partitioning compatibility flag;
7. classify unsupported object types explicitly.

## BOLT profile failure

1. verify perf branch stacks;
2. verify the exact binary was executing;
3. run package-specific workload;
4. train DSO through reverse dependencies;
5. merge multiple nonempty fdata profiles;
6. reject empty data.

## BOLT correctness failure

1. restore known-good package;
2. preserve input/output/profile/logs;
3. reduce BOLT options narrowly;
4. test EH/jump-table/ICF/splitting overrides;
5. retest ABI and runtime;
6. report upstream-quality reproducer where possible;
7. use a reviewed terminal exclusion only when no safe BOLT configuration works.

---

# 26. Required Git phase commits

Use clear commits resembling:

1. `docs: add exhaustive system-wide PGO and BOLT plan`
2. `refactor: separate PGO backends and exact build identities`
3. `feat: add installed package and ELF inventory pipeline`
4. `feat: add workload and profile coverage framework`
5. `feat: add pre-strip BOLT capture and deployment hooks`
6. `feat: add strict PGO and BOLT verification`
7. `portage: add generated system-wide optimization policy`
8. `docs: record completed optimization generation and results`

Do not combine unrelated existing Portage cleanup into these commits unless necessary for the optimization pipeline.

---

# 27. Final completion checklist

The implementing agent may mark this plan complete only after every item below is true.

## Framework

- [ ] Instrumentation and sample profiles are fully separated.
- [ ] Compiler-family leakage is impossible and tested.
- [ ] Package/ABI/compiler fingerprints are implemented.
- [ ] BOLT capture happens on unstripped `${ED}` files.
- [ ] BOLT deployment happens in `${ED}` before Portage stripping.
- [ ] Mismatch checks fail closed.
- [ ] State schemas and strict verifier are implemented.

## Inventory and classification

- [ ] Every installed CPV is inventoried.
- [ ] Every owned file is classified.
- [ ] Every package has a PGO backend or evidence-backed non-applicable state.
- [ ] Every native ELF has a BOLT eligibility state.
- [ ] Every kernel lifecycle package/artifact has terminal reason `kernel-policy-exclusion` and was absent from every automated mutation set.
- [ ] Zero package or artifact records are unknown.

## PGO

- [ ] Toolchain bootstrap PGO is complete.
- [ ] Full instrumentation/profile-source sweep is complete.
- [ ] All workload classes ran successfully.
- [ ] All eligible packages have valid representative profile data.
- [ ] Full PGO-use rebuild completed.
- [ ] Build logs prove correct profile use for every eligible package.

## BOLT

- [ ] Exact PGO-built input exists for every eligible ELF.
- [ ] Every eligible ELF has nonempty matching fdata.
- [ ] Every BOLT output passed structural, ABI, metadata, and runtime validation.
- [ ] Final deployment manifest covers every eligible ELF.
- [ ] Installed eligible ELFs contain `.note.bolt_info`.

## Final system

- [ ] Final exhaustive rebuild applied PGO and BOLT together.
- [ ] `@preserved-rebuild` is clean.
- [ ] Reverse-dependency checks are clean.
- [ ] System rebooted successfully through the pre-existing unchanged boot path; `runtime_after_reboot` evidence contains only boot ID, running-kernel release, loaded-module manifest, OpenRC state hash, and userspace reboot-test evidence—not any boot-entry/EFI identity.
- [ ] Desktop, graphics, audio, network, package manager, compiler, media, gaming, storage, and service tests pass.
- [ ] Strict report has `pending=0`, `unknown=0`, and `failed=0`.
- [ ] Rollback binpkgs remain available and verified; no project action changed the boot chain or kernel lifecycle state.
- [ ] This document contains final counts, benchmark results, exceptions, and the generation ID.

---

# 28. Final agent instruction

Implement the complete plan, not merely the framework. Do not stop after adding scripts, flags, profiles, or documentation. Continue through inventory, exhaustive rebuilds, training, profile validation, BOLT conversion, final package-managed deployment, reboot, runtime testing, and strict coverage verification.

This completion mandate is userspace-only. An agent must never create, edit,
delete, reorder, select, or arm a boot entry; change EFI variables; touch the
bootloader/EFI assets; or configure, build, install, replace, or deploy a
kernel or initramfs. It must not ask a human to do those things for this
project. Record affected lifecycle items with terminal reason
`kernel-policy-exclusion` and continue only within the userspace boundary.

After every completed item:

1. update this document;
2. update package/artifact state;
3. preserve logs and evidence;
4. re-read the plan;
5. verify the completed work fully satisfies the item rather than only approximating it;
6. proceed to the next unresolved item.

For the Phase 2 Candidate-B authorization run only, the truthful plan update and
claim markers are committed before the full rerun. The plan then remains
immutable; detached root-owned state and evidence record the result. Any later
plan correction creates a new candidate and invalidates that authorization.

The project is complete only when the live installed system—not merely the repository—passes the final strict completion conditions.

### 2026-09-17 live inventory generator

The previously frozen Phase 3 inventory is no longer mutation-authoritative because the live VDB has seven added CPV identities and six removed identities after the ABI repair work. Added `scripts/optimization/inventory/generate-live-inventory.py`, which deterministically derives package entry hashes and owned paths from the live VDB and emits directory records using live stat data. Existing reviewed directory records are retained only when their uid/gid/mode still match; changed or new directories are marked `requires-directory-review`. The generator output is a candidate and is not activated as framework inventory until that review and exact-source authorization are complete.

The first live candidate run completed after fixing two generator defects (filesystem-root parent walk and dictionary de-duplication). Candidate counts are 1,292 packages, 679,635 owned paths, and 48,610 directory records. Thirty-three directories are unresolved and require explicit review; they are concentrated in the newly installed Maya 2027.2-r1, Hyprland 9999, Hyprutils 0.14.2, and wlroots 0.20.2 include trees. Candidate output is `/tmp/phase3-live-candidate.json` and remains non-authoritative.

Autonomous directory review completed for all 33 candidate unresolved directories. They are directory-only Maya plug-in and C/C++ include trees; the deterministic review classified them `not-machine-code` and recorded the candidate inventory digest in `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/directory-review.json`. The reviewed candidate inventory is installed at the same generation path with 0 unresolved records (SHA-256 `067a2ec1f4489c7025894848ec107b5f81ef816bf745e672332cbdce8c4837e9`). It is still a candidate and has not replaced the active framework.

The reviewed candidate generation now also has a live package manifest at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/package-manifest.json`. It records all 1,292 exact CPVs and the regenerated disjoint CP-atom sets: 1,242 userspace atoms and 12 explicit kernel-policy exclusion atoms, with no overlap. Set files were regenerated directly from `/var/db/pkg`; the exact CPV list remains authoritative for this generation.

The owned-artifact census is now captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/owned-artifact-census.json` (679,635 CONTENTS-owned records: 643,412 regular files, 36,063 symlinks, 160 paths absent from the live filesystem, and 16,588 ELF records). The 160 absent paths are explicitly recorded in `artifact-exceptions.json` with reason `missing-live-artifact`; they are not silently skipped. The census scanner is committed as `scripts/optimization/inventory/scan-owned-artifacts.py`.

ELF metadata extraction is complete for all 16,588 ELF records. `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/elf-metadata-census.json` records ELF class/type, interpreter, dynamic NEEDED entries, and build IDs. Counts: 13,584 ELF64 and 3,004 ELF32; 13,717 shared objects, 2,475 PIEs, 267 ET_EXEC files, 120 relocatable objects, and 9 miscellaneous types. 3,150 records carry build IDs and 2,540 carry interpreters. ELF32, relocatable, missing-build-ID, and non-interpreter records remain explicit classification inputs; they are not silently treated as BOLT eligible.

Preliminary ELF eligibility classification is captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/elf-eligibility-classification.json`. Every ELF record now has an explicit state/reason: 2,500 ELF64 DYN/EXEC records with build IDs are `candidate-bolt-eligible` pending section/safety review; 3,097 ELF32/relocatable/unsupported-type records are `not-applicable` with specific reasons; 10,991 ELF records remain `pending-eligibility-review`, primarily because they lack build IDs. This is an intermediate classification and does not claim any BOLT deployment.

Package-level compiler/backend evidence is captured for all 1,292 exact CPVs in `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/package-backend-classification.json`. It records installed environment variables and artifact-language evidence from CONTENTS, while deliberately leaving each package in `requires-ebuild-and-build-log-review` until authoritative ebuild/eclass and build-log evidence is correlated. No package is silently assigned to a PGO lane from its category alone.

Focused section/symbol safety review is captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/bolt-safety-review.json`. Of the 2,500 preliminary ELF64 candidates, 1,920 have a `.text` section and at least one defined function symbol and are now `bolt-ready-pending-profile`; 580 are explicitly `not-applicable` with either missing `.text` or no defined function symbols. Profile capture, exact-input binding, and deployment remain outstanding.

Exact package terminal-state accounting is now captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/package-optimization-state.json`. All 1,292 CPVs are assigned exactly one explicit state: 12 `kernel-policy-exclusion`, 510 `not-applicable` because no owned ELF artifact exists, and 770 `pending-pgo-classification` because owned ELF artifacts still require backend and representative-profile work. The classifier maps CPVs to policy atoms through live VDB `P` metadata, preventing revision suffixes from bypassing the kernel boundary.

Installed ebuild/eclass correlation is complete for all 1,292 CPVs at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/ebuild-backend-correlation.json`. The correlator uses repository-keyed ebuilds where available and the authenticated installed ebuild copy as fallback; all 1,292 packages resolved to source text. It records inherited eclasses, backend-related inheritance, and declared phase functions, providing the evidence base for assigning PGO lanes without category-only guesses.

Evidence-backed PGO lane candidates are captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/pgo-lane-candidates.json`. Combining exact package state with source-correlated eclasses yields: 286 `pgo-clang-ir`, 29 `pgo-rust`, 5 `pgo-go`, 72 `unsupported-by-upstream-toolchain`, 510 `not-applicable`, 12 `kernel-policy-exclusion`, and 378 still `pending-pgo-classification` because no supported backend evidence was found. These are lane candidates, not authorization to rebuild; representative workloads and package-specific validation remain required.

The candidate generation also contains CP-atom mutation-set projections (`*.set`) for each lane under `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/`. They are intentionally generation-local and are not installed into `/etc/portage/sets` or consumed by emerge. CPV-to-atom collapsing changes the counts (for example, 286 CPVs map to 274 `pgo-clang-ir` atoms); the exact CPV manifest remains authoritative.

The PGO lane correlator was tightened to use the full inherited-eclass set rather than only initially recognized backend tokens. This reduced `pending-pgo-classification` from 378 to 144 exact CPVs and expanded evidence-backed candidates to 505 `pgo-clang-ir`, 29 `pgo-rust`, 5 `pgo-go`, and 87 `unsupported-by-upstream-toolchain`; 510 remain `not-applicable` and 12 remain `kernel-policy-exclusion`. The updated manifest is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/pgo-lane-candidates.json`. The remaining 144 require targeted review of their ebuild phases and produced artifacts.

The remaining pending lane review now uses explicit ebuild phase evidence (`src_compile`/`src_configure`) in addition to inherited backend eclasses. This reduced `pending-pgo-classification` from 144 to 87 exact CPVs and produced 578 `pgo-clang-ir`, 29 `pgo-rust`, 5 `pgo-go`, and 71 `unsupported-by-upstream-toolchain` candidates, alongside the 510 not-applicable and 12 kernel-policy exclusions. The current manifest remains `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/pgo-lane-candidates.json`.

Targeted review resolved the final 87 pending package lanes. The current PGO lane manifest has zero pending records: 686 `pgo-clang-ir`, 29 `pgo-rust`, 5 `pgo-go`, 1 `pgo-gcc`, 49 `unsupported-by-upstream-toolchain`, 510 `not-applicable`, and 12 `kernel-policy-exclusion`. Vendor/prebuilt packages, tooling-only packages, generated native parsers, and native utilities received explicit package-specific reasons. The authoritative candidate is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/pgo-lane-candidates.json`.

The first lane pretend exposed and corrected a set-generation defect before mutation: Portage set files had been populated with versioned CPVs (for example `acct-group/audio-0`) instead of category/package atoms. Regenerated `/etc/portage/sets/pgo-bolt-all-userspace` and `/etc/portage/sets/optimization-kernel-policy-exclusion` using Portage's authoritative `catpkgsplit`; the live sets now contain 1,220 userspace atoms and 13 exclusion atoms. A focused `emerge -pv '=net-libs/libmnl-1.0.5'` rerun produces no set validation errors.

A generation-local candidate Portage policy was generated at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/package.env`. It contains 1,233 unique category/package atoms with one non-conflicting backend-specific environment assignment each; kernel-policy, not-applicable, and unsupported lanes are explicitly assigned `optimization-off.conf`. The policy parser check found 1,233 lines and zero malformed or missing environment references. This candidate policy is not activated; profile identity, compiler fingerprint, and profile paths must be generated before mutation.

Generation-bound compiler identity is captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/compiler-identity.json`. It records paths, versions, SHA-256 fingerprints, and safe profile spool roots for Clang/Clang++, GCC, Rust, and Go. The identity digest is `a507faf46b0eea60de1292ea7a189060406e397444124bb76a4f2c33c6151a50`; the profile spool root is root-owned mode 0755. This establishes inputs for policy generation but does not assert that profiles exist or authorize a rebuild.

Per-CPV policy bindings are now generated at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/pgo-policy-bindings.json`. All 1,292 records bind the lane to the generation compiler identity; all 721 PGO-lane records have a compiler SHA-256 and a generation-specific profile path, while non-PGO terminal lanes carry null compiler/profile fields. The binding digest is `e5a1b376127b0c2bdf210ff21496e9c9be3bab7e3d434e8a119b40f77e3f01c8`. This remains candidate policy; no profile payload is asserted.

The candidate generated policy is materialized at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/generated-policy-35efc40b45b6605f2e89f00d1e4bd925dc71f0ac99338b39efb2b57d8e85ffd3/`. It contains the package.env, policy bindings, compiler identity, and `.identity` digest. Independent validation found 1,233 unique policy atoms, zero duplicate or malformed entries, zero missing environment files, and an identity digest match. This candidate is not activated as the live framework.

The read-only framework check exposed and corrected three candidate-generation contract issues: inventory arrays had to be sorted by `(owner_cpv,path)`, reviewed directory evidence required terminal `sha256` fields, and absent live directories could not carry null uid/gid/mode values. After correction, the repository `reconcile-state.py --validate-inventory-only` validator passes with 1,292 CPVs, 679,635 paths, and 48,608 valid directories. The framework check then exposed policy contracts and was corrected to use exact `=CPV` selectors and `env/optimization/generated/*` paths. Its full tree check exceeded the bounded 120-second observation window and was terminated by timeout after preflight; no activation occurred. The candidate remains non-authoritative pending a complete check run.

A clean framework-check invocation was observed directly: it passed strict inventory parsing and reached the installer preflight, then entered the install-to-check re-exec path repeatedly without advancing its log. Process inspection showed nested `install-framework.sh --check` instances, so the process tree was terminated to avoid an uncontrolled re-exec loop and possible indirection mutation. The strict standalone inventory validator remains passing; framework activation was not performed.

The repaired root-owned installer was invoked through the required production entrypoint with the exact candidate policy path. The first attempt failed immediately because the supplied generated-policy basename contained a copied hash typo; no activation occurred. A second invocation used the filesystem-discovered exact `generated-policy-35efc40b45b6605f2e89f00d1e4bd925dc71f0ac99338b39efb2b57d8e85ffd3` path, reached preflight, and again formed nested installer processes without reaching a terminal result despite the re-exec guard being present in the bootstrap copy. The process tree was terminated; `framework-current` remains on the prior generation.

The re-exec environment ordering bug was fixed in both repository and root-owned bootstrap copies (`414e59a`). GNU `env` requires options before assignments; the prior `env VAR=1 -u ...` form treated `-u` as a command and invalidated the guard. A corrected production invocation reached preflight, but still spawned nested installer instances without a terminal result; the process tree was stopped and the active framework symlink remained unchanged. Further diagnosis must inspect installer state transitions beyond the re-exec branch before another activation attempt.

Passing `GENTOO_OPT_INSTALLER_CHECK_REEXEC=1` in the initial environment also did not eliminate nested installer processes; the normal candidate path still spawned descendants during preflight. This proves the remaining recursion is outside the reviewed-input re-exec branch. The process tree was stopped without changing `framework-current`. Further work must trace the installer’s helper/bootstrap publication path or lock recovery state, rather than adjusting the re-exec guard again.

A foreground `--check` session (session 35921) removed detached-shell ambiguity. It showed a deterministic nested ancestry: the root installer spawned child `install-framework.sh --check` instances at roughly one-minute intervals, with no guard variable in their environments and no new log output after preflight. The session was interrupted after five minutes; no activation occurred. This proves the recursion is an actual shell child/re-entry path that does not preserve `GENTOO_OPT_INSTALLER_CHECK_REEXEC`, and requires tracing the exact call site or shell execution context rather than further timeout changes.

An `strace -ff -e trace=execve` run corrected the earlier recursion diagnosis. Across 5,112 traced process logs, only the initial `execve` of `/var/lib/gentoo-optimization/bootstrap/install-framework.sh` occurred; no child `execve` re-entered the installer. Same-command Bash descendants observed in `ps` are forked subshells from the installer’s process substitutions/command substitutions during source and metadata hashing. The apparent recursion was therefore expensive validation work, not self-reentry. No framework activation occurred during the trace run.

The tracked framework check remained in the source/metadata hashing phase for over 20 minutes with no new semantic output. `strace` confirms the work is dominated by thousands of git/sha256/stat subprocesses over the source tree, not recursion. I terminated the session after this bounded diagnostic observation; no activation occurred. The candidate inventory and policy validators remain independently passing. A complete installer check requires a longer maintenance-window execution or a future optimized snapshot path; no package mutation is authorized until it reaches a terminal result.

A representative workload manifest is now generated at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/representative-workloads.json`. It covers all 721 PGO-lane CPVs using verified ELF interpreter/build-ID evidence, selecting up to eight runnable executable/PIE entrypoints per package. Counts are 431 `workload-candidate` packages and 290 `no-runnable-entrypoint` packages; the latter remain explicit workload/profile exceptions and are not silently treated as profiled.

Cross-manifest coverage auditing is now implemented in `scripts/optimization/verify/phase3-coverage.py`. The candidate audit at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/coverage-audit.json` passes classification coverage for all 1,292 CPVs and all 16,588 ELF records. It separately reports 2,500 candidate safety-review records; safety/profile readiness remains a distinct gate and is not falsely promoted to completion.

Workload recipes are now captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/workload-recipes.json`. The manifest contains 1,271 deterministic `--help` recipe records across 431 packages; all recipes are explicitly `not-run`, use a sanitized C locale and root cwd, and record executable-path safety. The other 290 PGO packages remain `no-runnable-entrypoint`. No workload was executed and no profile payload was generated.

The first controlled profile-generation wave is planned at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/profile-wave-plan.json`. It contains 13 exact CPVs with runnable recipes: four `pgo-clang-ir`, one `pgo-gcc`, four `pgo-go`, and four `pgo-rust`. Each entry binds the compiler hash, profile path, exact CPV, and recipe list. State is `planned-not-authorized`; no build or workload execution has occurred.

The 13-package profile-wave readiness audit is captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/profile-wave-readiness.json`. All 13 wave entries match an exact live CPV, the captured compiler SHA-256, and a generation-safe profile root; zero inputs are invalid. Execution remains explicitly `not-authorized-framework-gate` until the framework check reaches a terminal result.

A machine-bound transaction receipt for the first profile wave is now captured at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/profile-wave-receipt.json`. It binds the 13 exact CPVs to the wave plan, readiness audit, policy bindings, and workload recipes by SHA-256. Its state is explicitly `not-run`, authorization is `pending-framework-terminal-check`, and `profile_payloads` is empty; no profile evidence is fabricated.

The 290 packages without runnable workload entrypoints are now explicitly classified in `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/workload-exclusions.json`. They account for 3,325 ELF records and zero records with program interpreters; every one receives `no-runnable-userspace-entrypoint`. This is a workload/profile exclusion with machine evidence, not a silent package skip.

The workload coverage audit now passes at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/workload-coverage-audit.json`: all 721 PGO-lane CPVs are covered exactly once by either 431 recipe-ready packages or 290 explicit no-runnable-entrypoint exclusions, with zero overlap, missing, or extra records. This proves workload accounting coverage only; recipe execution and profile collection remain pending authorization.

Profile payload auditing is now implemented in `scripts/optimization/pgo/verify-profile-payloads.py`. The current audit at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260917/profile-payload-audit.json` finds all 721 PGO-lane records explicitly `missing-profile` and sets authorization to `pending-profile-collection`. No profile payload is fabricated or treated as valid before collection and hash validation.

A fail-closed profile-wave runner is now implemented at `scripts/optimization/pgo/run-profile-wave.py`. It verifies that the active framework symlink equals the authorized candidate generation and that all wave-readiness records are valid before any emerge invocation. A current-state test correctly refused execution because `framework-current` still points to the prior generation; no package transaction occurred.

Framework publication checkpoint (2026-09-18): The fail-closed frozen-inventory verifier is now bound to the trusted root-owned bootstrap, and generated-policy Portage atom parsing/live-VDB membership validation is performed in one batch pass. The current live candidate policy was rebound to its actual content identity `98759f8ad9acd8be92bfd8902410a65d9b392c3fbbb5919f41c8218629c0d514`. The normal userspace installer completed successfully and activated framework `23de2b83149e5e53215776fc0acc5db7b95993f880021be51117c23728d67c6b`; an independent strict `--check` against the same frozen inventory and policy passed. Phase-3 profile execution remains pending: no workload recipe or profile payload has been collected, and no profile wave is authorized solely by framework activation.

Post-publication wave gate (2026-09-18): The active framework now passes its independent strict check, but the retained 20260917 profile-wave plan/readiness artifacts bind to an older wave digest and identity generation. The readiness runner reports technical readiness for that historical 13-package plan only; its fingerprint root is absent from the retained 20260917 directory, and the current 20260918 identity records are a different generation. No profile-wave execution is authorized from those stale artifacts. A fresh current-generation lane/binding/workload regeneration and wave receipt is required before any package transaction.

Current-generation regeneration checkpoint (2026-09-18): The live census and ELF metadata chain was regenerated from the current VDB. Explicit `readelf` failures are retained as pending metadata records. Current classification reports 2,500 candidate ELF records, 3,095 terminal non-applicable records, 10,993 pending ELF eligibility records, 766 pending package PGO states, and 12 kernel-policy exclusions. A bounded current-generation wave was regenerated for the four packages with complete compiler/profile evidence; its plan is `phase3-live-candidate-20260918/profile-wave-plan-regenerated.json`, readiness is `profile-wave-readiness-regenerated.json`, and the runner reports all 4 inputs technically ready against the active framework. Execution remains withheld because the complete current-generation lane/binding set is not yet authoritative: 749 package lanes remain pending and no full-wave receipt exists.

Lane-evidence refinement (2026-09-18): Current VDB artifact-language evidence is now consumed by lane assignment. The regenerated current-generation lane set has 6 `pgo-clang-ir`, 2 `pgo-rust`, 1 `pgo-go`, 1 `pgo-gcc`, 73 unsupported, 513 not-applicable, 12 kernel exclusions, and 684 still pending. Exact current compiler hashes and generation-safe profile paths were attached to supported lanes. The regenerated workload manifest has six recipe-ready packages and four explicit no-runnable-entrypoint records; the bounded six-package wave readiness is 6/6 valid and the runner passes its technical framework gate. Execution remains withheld because 684 lanes and the complete current-generation binding set remain unresolved.

Complete current-source lane checkpoint (2026-09-18): Ebuild correlation against the live repositories resolved the prior pending-lane artifact: 1,288 of 1,292 CPVs have current ebuild source, and current lane assignment now reports zero pending records (509 `pgo-clang-ir`, 29 `pgo-rust`, 5 `pgo-go`, 1 `pgo-gcc`, 223 unsupported, 513 not-applicable, 12 kernel exclusions). Current compiler hashes and profile paths are bound for supported lanes. The complete current workload manifest contains 324 recipe-ready records and 220 explicit no-runnable-entrypoint records. A 12-package balanced wave plan, readiness file, and receipt were regenerated under `phase3-live-candidate-20260918`; readiness is 12/12 with zero invalid inputs and the runner passes the active-framework gate. The receipt remains `not-run` and `pending-framework-terminal-check`; workload recipes remain unexecuted and the runner does not yet collect representative workload payloads, so no profile transaction is authorized by this checkpoint.

Profile payload receipt hardening (2026-09-18): The wave runner now executes each reviewed workload recipe after its instrumented package transaction, fails closed on unsafe paths, timeout, nonzero exit, or empty output, hashes all files emitted under the generation profile path, and can write a terminal receipt with authenticated payload records via `--receipt`. The runner still requires `--execute`; no transaction has been started. Dry-run technical readiness remains unchanged.

Fingerprint reconciliation checkpoint (2026-09-18): The retained 12-package wave was audited against the current generation identity root and six package fingerprints were absent, so execution was refused. A current six-package wave was regenerated using only packages with present exact fingerprint files; its readiness is 6/6 with zero invalid inputs and the active-framework technical gate passes. The six excluded package records remain pending fingerprint regeneration and are not silently skipped. No transaction was executed.

Profile-wave workload repair (2026-09-18): The first controlled execution rebuilt and merged `app-admin/doas-6.8.2`, but its generated `--help` recipe correctly failed because doas treats `--help` as an invalid option. The workload generator now uses doas's successful non-destructive `-L` action and marks the recipe's empty successful output as explicitly acceptable; the runner preserves fail-closed nonzero/timeout handling while honoring that declaration. The current wave and readiness manifests were regenerated with hashes `8e7ff40e2a27279197832feb45d78f3930637089eef0c299b7ca671e573f7064` and `a7ac12fc020a4df023d92a628cb2efc86cda952cf2fb1de1a171cfe4b1e3eb43`; technical dry-run passes and execution is pending rerun.


Github CLI Go ABI dependency checkpoint (2026-09-18): The first rerun advanced through doas, 7zip, and direnv, then github-cli compiled and installed but failed its fail-closed Portage QA gate because go.mod requires Go 1.27 while the source ebuild declared only Go 1.26.1. The active framework-local overlay now supplies an exact-source github-cli-9999 ebuild with BDEPEND=">=dev-lang/go-1.27.0:=", copied existing patch data, regenerated Manifest/metadata, and Portage resolves the CPV from codex-local. The wave is being rerun from its authoritative six-package manifest; no success is claimed until a terminal receipt exists.

Receipt and framework identity hardening checkpoint (2026-09-18): Independent profile-wave receipt verification is implemented in `scripts/optimization/pgo/verify-wave-receipt.py` and requires exact wave/readiness digests, exact package membership, nonempty payloads for completed receipts, unique payload identities, existing files, and matching payload hashes. The retained `profile-wave-receipt-current.json` fails verification because it is bound to a different wave and claims completion with no payloads; it remains preserved historical evidence and is not trusted. The profile-wave runner now rejects framework generations lacking a candidate-inventory marker, real generated-policy identity, candidate inventory digest, and frozen inventory digest. The live `framework-current` remains the empty-policy fallback `framework-770ec05be945a8a823af651e14569a26acc198d9b04cb3bb0c1254c752101991` (`generated_policy=empty-v1`, `frozen_inventory_sha256=none`), so no Phase-3 wave may execute. Regression tests and the full smoke gate pass; the next execution step is to regenerate and publish an inventory-bearing framework generation before retrying the current wave.

Lane override reconciliation checkpoint (2026-09-18): Direct lane assignment from the current package-state and ebuild correlation exposed 35 pending records that the retained zero-pending ebuild lane artifact had promoted without a machine-bound override input. The classifier now accepts an explicit reviewed override document, and `optimization/pgo-lane-overrides.json` binds 41 reviewed decisions to the exact ebuild path, inherited eclasses, phase functions, and source-correlation digest. Regeneration with that input produces zero pending lanes (509 `pgo-clang-ir`, 29 `pgo-rust`, 5 `pgo-go`, 1 `pgo-gcc`, 223 unsupported, 513 not-applicable, 12 kernel exclusions). This reconciles the lane classifier; policy and framework generation still require regeneration from the resulting manifest.

Live VDB identity refresh checkpoint (2026-09-18): The current candidate inventory was proven stale because it still owned `sys-devel/gcc-17.0.9999` while `/var/db/pkg` contains `sys-devel/gcc-17.0.9999-r1`. A fresh CONTENTS inventory was regenerated directly from the live VDB as `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live2/frozen-inventory.json` (1,292 CPVs, 679,818 owned paths, 79,177 directories; SHA-256 `e909fadfa09461cc5831e11379bcbc9e09a23b3447468160ad5958479cc5a1fa`). All package state, ELF census, lane, fingerprint, policy, and framework artifacts derived from the old inventory are stale until regenerated against this live identity.

Artifact census scaling checkpoint (2026-09-18): `scan-owned-artifacts.py` now inspects independent owned paths through a bounded deterministic thread pool while preserving canonical `(path, owner_cpv)` output ordering and the existing census schema. A fresh live run completed in 27.17 seconds for 679,638 artifacts, with 16,588 ELF records, 643,415 regular files, 36,063 symlinks, and 160 missing paths. The first parallel run exposed and corrected a tuple-order regression before accepting the result; no partial census was promoted until the corrected run completed.

Live derived-state refresh checkpoint (2026-09-18): From the fresh `phase3-live-candidate-20260918-live2` inventory and parallel artifact census, package-state classification now reports 510 not-applicable, 769 pending-PGO-classification, and 13 kernel-policy exclusions. Fresh backend correlation, ELF metadata, and ELF eligibility artifacts were generated against the same live identity: 2,500 candidate-BOLT-eligible ELF records, 3,095 terminal non-applicable records, and 10,993 pending eligibility-review records. The earlier zero-pending lane and profile artifacts remain quarantined until package and artifact review is rerun against this source.

Fresh live lane reconciliation checkpoint (2026-09-18): Ebuild correlation was regenerated from `phase3-live-candidate-20260918-live2/frozen-inventory.json` and the reviewed override set was rebound to the new correlation digest. The resulting lane manifest is root-owned at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live2/pgo-lane-candidates.json` with digest `7f98610ab4939855b84553ad097a18eb2230b483ab2bd3b28c0f07198a164e23` and zero pending records: 508 `pgo-clang-ir`, 29 `pgo-rust`, 5 `pgo-go`, 1 `pgo-gcc`, 226 unsupported, 510 not-applicable, and 13 kernel-policy exclusions. The stale pre-bridge GCC identity is absent; the current lane set contains `sys-devel/gcc-17.0.9999-r1`.

Fresh fingerprint and binding checkpoint (2026-09-18): Exact compiler identities were revalidated against the live tool binaries, and fingerprint inputs were collected from the fresh zero-pending lane set. Batch materialization succeeded for all 543 fingerprint inputs with zero failures. The complete policy-binding manifest was then generated at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live2/pgo-policy-bindings.json`, digest `0875e8b2d90e4e7b7e09c994f91700ed3937cd5cd280ea0a0fe5c160dcf32cb4`, with counts bound to the fresh live lane manifest and generation-specific profile root.

Generation policy materialization checkpoint (2026-09-18): Added `scripts/optimization/pgo/materialize-policy-tree.py` to derive an exact 1,292-record `package.env` tree from the fresh binding manifest. It rejects binding self-digest mismatches, duplicate CPVs, unknown lanes, and missing reviewed environment files, and emits content-addressable environment files under `env/optimization/generated/`. The generated live2 candidate contains one unique exact `=CPV` assignment per binding and all five required lane environment files; strict installer publication remains the next gate.

Inventory-bound framework publication checkpoint (2026-09-18): The fresh live2 inventory passed strict semantic validation after resolving the single unresolved directory `/etc/polkit-1/rules.d` as terminal `not-applicable` with distinct review evidence. The generated policy input was corrected to the installer contract (top-level `package.env` and one-level `env/`), content-addressed as `generated-policy-1521b241be5aefadb5f54e1f3231f2c2794a8c0c3aa84c1718cbb6117682384a`, and published through the root-owned bootstrap. `framework-current` now points to `framework-8c4062ad8a1ea66804312379c8f9fa81aae5910679410aca35555fb0a1cff072`; its manifest binds generated policy `1521b241...`, frozen inventory `0ff638b79980033748807c7c9da8e49b314a3391d0a464833705199284a58671`, and commit `478cf958`. A subsequent strict installer `--check` passed. This activates the fresh userspace framework; no package rebuild or profile wave has yet run.

Profile-wave execution checkpoint (2026-09-18): The first exact current-generation doas wave was executed under the inventory-bound framework with root-context identity verification. The transaction rebuilt and merged `app-admin/doas-6.8.2` in `clang-ir-generate` mode, passed the install-QA ABI guard, and collected two nonempty raw payloads at `/var/tmp/gentoo-optimization/pgo-raw/phase3-live-candidate-20260918-live2/clang/app-admin_doas-6.8.2`. The authenticated receipt is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live2/profile-wave-receipt-doas.json`; `verify-wave-receipt.py` passed exact wave/readiness/package and payload-hash checks. LLVM 22 `llvm-profdata` merged the payloads into the root-owned `merged-profiles/app-admin_doas-6.8.2.profdata`, with independent merge evidence in the adjacent `.merge.json`; the evidence remains `profile-merged-pending-dispatcher-authorization` and does not authorize deployment. The authoritative profile-wave frontier has therefore advanced from no collected payloads to one verified Clang profile package, while broad-wave execution remains gated.

Phase-3 coverage audit correction checkpoint (2026-09-18): `scripts/optimization/verify/phase3-coverage.py` now compares classification and safety-review identities against the independent authoritative ELF census, covers both ELF64 and ELF32 records, and emits schema version 2. The former self-comparison that could never report missing classifications has been removed. The script compiles successfully in the working tree.

Second profile-wave execution checkpoint (2026-09-18): A separately planned exact `app-arch/7zip-26.03` `pgo-clang-ir` wave passed readiness 1/1 with zero invalid inputs, completed the instrumented transaction and full install-QA ABI guard, and collected one nonempty raw profile payload. The authenticated receipt is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live2/profile-wave-receipt-7zip.json`; the receipt verifier passed. LLVM 22 merged the payload into `merged-profiles/app-arch_7zip-26.03.profdata` with adjacent independent merge evidence. The merged profile remains pending dispatcher authorization and has not been deployed.

Dispatcher publication boundary checkpoint (2026-09-18): The independently merged Clang profiles for `app-admin/doas-6.8.2` and `app-arch/7zip-26.03` were submitted to `validate-profile.py produce` with the exact compiler, llvm-profdata, fingerprint, generation, and inventory identities. The validator refused publication because `/run/gentoo-optimization/framework-install.lock` does not contain valid authorized-generation JSON. This is a correct fail-closed authorization result: the profiles remain collection/merge evidence only, and no dispatcher manifest or profile-use transaction was fabricated.

ABI and workload remediation checkpoint (2026-09-18): The install-QA ABI guard was corrected to derive candidate DSO families and relative library directories from the staged package image, return immediately for images with no DSO candidates, and inspect only the immediate candidate-provider directories rather than recursively traversing them. This bounds installed-root work to the relevant ABI scope while retaining fail-closed ELF/SONAME checks. Framework publication and strict check passed for the updated implementation. The representative-workload generator now excludes the recovery-only `bzip2recover` helper; the normal `/bin/bzip2-reference --help` entrypoint is retained and verified. A corrected exact `app-arch/bzip2-1.0.8-r5` wave completed successfully, passed receipt verification, collected 40 nonempty raw payloads, and merged them into `merged-profiles/app-arch_bzip2-1.0.8-r5.profdata` with independent evidence. The prior bzip2 attempt is preserved as a failed workload receipt path and was not counted.

ABI guard regression checkpoint (2026-09-18): Focused `test-abi-guard.sh` and `test-portage-qa-hook.sh` both pass after the directory/family traversal optimization (11 ABI cases and 11 QA-hook cases). The tests continue to reject exported-symbol loss, SONAME disappearance, invalid context, symlink escape, and dispatcher-hook bypasses while accepting valid retained-ABI transitions.

Phase-3 generation-authority implementation checkpoint (2026-09-18): Added the dedicated `scripts/optimization/pgo/generation-authorization.py` transaction. It keeps `framework-install.lock` content-empty, publishes one canonical generation payload byte-identically to `project.lock` and `generation.lock`, binds activation to the exact inventory-bearing framework manifest and generated-policy identity, writes a durable crash-recovery journal before payload mutation, verifies both stable lock inodes after publication, and supports explicit activate, verify, transition, recover, and deactivate states. A fixture transaction independently exercised activation, verification, and deactivation. The Phase-2 production transaction remains unchanged.

Phase-3 producer/merge authority checkpoint (2026-09-18): `run-profile-wave.py --execute` now requires an explicit generation triple and verifies the persistent Phase-3 authority before any Portage mutation; completed wave receipts record that authority and the active framework target. `merge-clang-profile.py` now requires a completed exact wave receipt, package and generation identity, validates every listed payload digest, rejects unreceipted or extra raw profiles and existing outputs, and emits immutable schema-v2 merge evidence. Focused wave-guard and receipt-verifier fixtures pass; no additional live wave was started during this source transition.

Authoritative contract checkpoint (2026-09-18): Added a focused generation-authority fixture covering activation, byte-identical dual-lock publication, exact verification, and explicit deactivation. The deterministic Phase-2 test contract was regenerated after the new fixture topology was finalized; its shell discovery count is now 63 and `phase2-test-contract.py check` passes.

Per-package attempt journal checkpoint (2026-09-18): The live profile-wave runner now creates a unique root-owned durable attempt record before each package mutation, binds it to the wave, CPV, lane, generation authority, active framework, profile path, and pre-transaction fingerprint, and records terminal completion with emitted payload identities. If the runner exits during a package attempt, an `atexit` reconciliation writes an explicit failed attempt record rather than leaving the package mutation unaccounted. Attempt records are additive and use a distinct attempt ID on every retry.

Authority-bound receipt checkpoint (2026-09-18): `verify-wave-receipt.py` now requires schema-v2 completed receipts with a well-formed generation triple and absolute active framework target before accepting any payload evidence. The focused receipt fixture was upgraded to this contract and continues to reject empty completed receipts.

Exact smoke-boundary checkpoint (2026-09-18): The repository optimization smoke suite was rerun through the executable driver path required by its provenance contract (`./tests/run-optimization-tests.sh`). The exact current source completed with 67 PASS, 0 FAIL, and 8 permitted capability/long-suite skips; provenance, shell syntax, shellcheck, Python compilation, and Phase-2 evidence smoke all passed. The earlier failure from invoking the driver through `bash tests/run-optimization-tests.sh` was an invocation-path mismatch and is not a source failure.

Additive test-contract and ABI traversal checkpoint (2026-09-18): The contract checker now preserves the frozen Phase-2 shell topology as a strict subset while permitting newly discovered Phase-3 shell fixtures to be executed by the normal driver without editing the content-hash-bound Phase-2 contract. A regression fixture covers additive discovery, and the stale policy assertion now matches the post-authorization README. Separately, the ABI guard distinguishes `None` from an intentionally empty candidate-directory set, returns immediately for staged images with no `.so` candidates, enumerates staged DSO candidates once, and uses immediate-directory provider lookup instead of recursive subtree walks. Focused ABI tests now include empty-DSO and nested-subtree pollution regressions and pass.

Portable topology compatibility checkpoint (2026-09-18): The authoritative result validator now applies the same additive rule at final portable/authoritative topology validation: frozen top-level identities remain exact, frozen prefix-group names remain required and ordered, and later-phase prefix-group tests are accepted and validated as additive results. This closes the second contract layer that otherwise rejected the new Phase-3 shell fixture after the pre-gate discovery checker had accepted it.

Live trust and topology follow-up (2026-09-18): The live Portage sample-policy preflight initially rejected `/var/db/repos/local-autodesk` because the overlay tree was user-owned. The overlay was reconciled to root:portage ownership with trusted read-only modes; the focused preflight now records the expected unavailable-observation skip and passes its policy assertions. The final topology validator was tightened to compare frozen prefix names as an ordered subsequence, allowing lexically interleaved additive Phase-3 fixtures. The prior portable run's remaining framework-installer failure was its test-root ownership precondition; no trust check was weakened.

Frozen-inventory verifier scaling checkpoint (2026-09-18): The semantic verifier no longer creates full sorted copies of the 680K-entry ownership arrays. It validates canonical `(owner_cpv,path)` ordering and uniqueness incrementally while retaining the required namespace-disjointness set, reducing peak memory and sort cost during strict framework checks. The trusted verifier hash in the installer was updated to the exact new source digest `583afd47d095ac7222e1c402bed1690f616d78c8dc4109bda116eb8c0e727741`.

Live2 framework publication checkpoint (2026-09-18): The root-owned bootstrap was refreshed with the committed installer and frozen-inventory verifier, then published the live2 inventory-bound generated policy through the normal framework transaction. Publication completed successfully, followed by an independent strict `--check` against the same source root, generated policy, and frozen inventory. No Phase-3 runtime generation authority was activated and no profile wave was started; this publication only establishes the corrected inventory-bound framework as the current framework input for subsequent authority activation.

Live2 generation-authority activation checkpoint (2026-09-18): The dedicated Phase-3 transaction now binds the inventory generation identity separately from the content-addressed framework directory name. It activated the exact live2 triple (`phase3-live-candidate-20260918-live2`, `phase3-live-candidate-20260918-live2-v1`, inventory SHA `0ff638b79980033748807c7c9da8e49b314a3391d0a464833705199284a58671`) with an immutable receipt. The framework lock remains empty; project and generation locks contain byte-identical canonical payloads, and an independent verify pass succeeds.

Portable-complete validation checkpoint (2026-09-18): The final portable-complete driver completed with PASS=87, FAIL=0, SKIP=12 across 556 subtests and exit status 0. The main 324-test optimization suite and 79-test recovery suite passed (three recovery skips), the strict framework-installer fixture passed, and all Phase-2 evidence/package-environment/Portage/BOLT/ABI gates passed. The recovery preflight now falls back to the installed LLVM 22 clang++ when sanitized PATH omits it; the fixture receives only a private clang++ shim so system readelf remains selected. The framework installer test-mode source-root trust branch and canonical frozen-inventory fixture vectors are committed in `2c575a1`; the recovery fallback and shim handling are committed in `db1e126` and `91a93a2`. This validates the corrected userspace framework and test contract; no boot/kernel/EFI mutation occurred.

Live3 package-state and inventory checkpoint (2026-09-18): Repository synchronization completed for all configured overlays. The userspace-only update pretend was intentionally not executed because Portage exposed the unresolved SPIR-V 1.4.350/1.4.357 consumer-closure conflict (glslang, spirv-tools, spirv-headers, vulkan-layers, and wlroots), together with a required wlroots USE change; no mask or forced ABI transition was applied. `emerge --depclean --pretend` also refused to produce a safe removal set because `gui-apps/grimshot-9999` requires the not-yet-installed `gui-wm/sway`. The live VDB was rechecked after the completed .NET maintenance transaction and had drifted to 1,301 CPVs (SHA-256 `8b9672c2bb3fd0292d8d5ff37d32f0e10ec318be2159510863d2ecb97e5be08c`) versus live2's 1,292; the added identities are the .NET runtime/SDK packages, `app-eselect/eselect-dotnet`, `dev-util/lttng-ust-compat`, `dev-util/mesa_clc-26.2.2`, and `dev-vcs/git-lfs`, while `dev-util/mesa_clc-9999` disappeared. A new candidate inventory was generated at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live3/frozen-inventory.json` with 1,301 packages, 684,885 owned paths, 79,942 directories, and SHA-256 `c896d934c37d1842e14e205df6dcc24e33e249539c79a998b61132d3fad47932`. The 728 newly introduced .NET directory records were reviewed as `not-machine-code` with report `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live3/directory-review.json` (SHA-256 `315fee7fca112bb22732563843799bc6b562b390a2c4481642a1d43fda1fe923`); the strict frozen-inventory verifier passes. Live3 is a candidate only: all live2-derived classifications, policies, framework, and authority remain stale until regenerated from this inventory.

Live3 derived-census checkpoint (2026-09-18): From the verified live3 candidate, the owned-artifact census now records 684,705 artifacts (648,469 regular files, 36,076 symlinks, 160 missing records) and 16,634 ELF objects. ELF metadata extraction completed with 3,176 build IDs and 2,550 interpreters; eligibility classification reports 2,526 BOLT candidates, 3,095 terminal non-applicable objects, and 11,013 pending eligibility reviews. Package-state classification reports 516 not-applicable packages, 772 pending PGO classification, and 13 kernel-policy exclusions. Backend correlation covers all 1,301 CPVs. Lane assignment currently yields 41 `pgo-clang-ir`, 1 `pgo-gcc`, 2 `pgo-rust`, 1 `pgo-go`, 74 unsupported-by-upstream-toolchain, 653 pending classification, 516 not-applicable, and 13 kernel-policy exclusions. The generated workload manifest has 35 workload candidates and 10 no-runnable-entrypoint packages; recipe generation yields 30 recipe-ready, 14 no-runnable-entrypoint, and 1 no-profile-producing-workload. These are candidate-derived artifacts only; the live2 framework and authority remain invalid for live3 until policy regeneration and strict publication are completed.

Live2 authority retirement checkpoint (2026-09-18): After live VDB drift was confirmed, the stale live2 Phase-3 generation authority was explicitly deactivated through `generation-authorization.py`; a subsequent exact verify correctly refuses with `REFUSED: no exact active Phase-3 generation`. The inventory-bearing live2 framework remains installed for forensic rollback only and is not authorized for further mutation or profile waves. Live3 must complete derived-policy regeneration and a new exact activation transaction before execution can resume.

Live3 update-closure checkpoint (2026-09-18): The persistent `/etc/portage/sets/pgo-bolt-all-userspace` had accumulated exact-version entries despite the plan requiring CP atoms; it was backed up as `pgo-bolt-all-userspace.pre-live3` and regenerated from the current VDB with 1,225 CP atoms, excluding the explicit kernel-policy CP set. A coordinated userspace closure was then started with `LLVM_PROFILE_FILE=/dev/null` for the SPIR-V/Vulkan/Hyprland/Mesa consumers and required rebuild dependents. The transaction successfully merged `dev-libs/iniparser-4.3.0`, `dev-cpp/glaze-8.1.0`, and `dev-libs/elfutils-0.196` and is still active. Its first ABI-guard rejection was `media-libs/libde265-1.1.3`: the replacement DSO exposed 106 exports versus 877 in the installed object, with 771 missing; the fail-closed guard correctly rejected it. This is evidence for a package-specific `no-hidden-no-forced-libs.conf` assignment in the next live3 framework, not a reason to weaken the guard. No kernel, firmware, EFI, bootloader, or initramfs package is in the explicit closure.

### Live4 inventory and maintenance checkpoint (2026-09-18)

After the successful userspace maintenance merges of `sys-apps/openrc-0.64` and `dev-python/urllib3-2.8.0` (with generated optimization bindings detached and restored afterward), the previous live3 candidate was retired. A fresh exact VDB inventory was generated as `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/frozen-inventory.json`: 1,301 CPVs, 684,938 owned paths, and 79,947 owned directories. The 29 newly unresolved directory records were independently reviewed as `not-machine-code`; strict inventory verification passes with inventory SHA-256 `b73160fc982a4c93634c42dc2f1c3a44162a47ff07165bebf93f0d44ae679c64` and directory-review SHA-256 `ae281089207041b4ba39714661925173af9601422ef1858810a0803b99ea410e`.

All derived artifacts were regenerated from that exact live4 inventory. The owned-artifact census contains 684,758 records (648,520 regular, 36,078 symlink, 160 missing) and 16,644 ELF records. ELF eligibility reports 2,528 preliminary BOLT candidates, 3,095 terminal non-applicable records, and 11,021 pending safety review. Package state reports 516 not-applicable, 772 pending-PGO-classification, and 13 kernel-policy exclusions before lane assignment. The reviewed lane override set now records the prebuilt `dev-dotnet/dotnet-sdk-bin-10.0.400` as `unsupported-by-upstream-toolchain`; the resulting exact lane manifest has zero pending records: 509 `pgo-clang-ir`, 29 `pgo-rust`, 6 `pgo-go`, 1 `pgo-gcc`, 227 unsupported, 516 not-applicable, and 13 kernel-policy exclusions. Workload accounting covers 325 candidate packages and 220 no-runnable-entrypoint packages; recipes contain 297 ready, 242 explicit no-runnable, and 6 no-profile-producing records.

The live4 fingerprint materialization completed for 545 supported-lane CPVs with zero failures. Policy bindings were regenerated with digest `32d7cab2d0f37b92a351c166cfea672dea388096acdcd013559602ef03805e10`, and the corresponding generated-policy tree is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/generated-policy-32d7cab2d0f37b92a351c166cfea672dea388096acdcd013559602ef03805e10/`. This remains a candidate; no authority activation or package mutation is implied by the regeneration.

### Live4 framework publication checkpoint (2026-09-18)

The root-owned bootstrap was refreshed from committed `scripts/optimization/install-framework.sh`. The live4 generated-policy tree was validated against the installer’s exact content-addressed inventory hash (`generated-policy-d56eeec6b31a15cba6f1f64608f54cb62c8a5172091ee87fa82ad025d0a5be08`). The normal installer transaction published and activated framework `/var/lib/gentoo-optimization/framework-b2daa692527f6e5e0cd37c24ce94bb73ffdba9f1595641bcea01da3e809a2e75`; an independent strict `--check` then passed against the live4 frozen inventory and policy. No Phase-3 generation authority was activated and no profile-wave package mutation was started by this publication.

### Live4 generation authority checkpoint (2026-09-18)

After the live4 inventory-bearing framework publication and independent strict check, the Phase-3 generation authority was activated transactionally for `phase3-live-candidate-20260918-live4` with inventory ID `phase3-live-candidate-20260918-live4-v1`, inventory SHA-256 `b73160fc982a4c93634c42dc2f1c3a44162a47ff07165bebf93f0d44ae679c64`, and framework target `/var/lib/gentoo-optimization/framework-b2daa692527f6e5e0cd37c24ce94bb73ffdba9f1595641bcea01da3e809a2e75`. The activation receipt and journal are retained under the live4 generation directory; an immediate independent authority verification passed. This authorizes only exact generation-bound Phase-3 producer operations; it does not authorize a broad package wave without a current readiness manifest.

### Phase-3 coverage audit correction and live4 result (2026-09-18)

The repository coverage auditor still contained a schema regression: it expected a top-level `cpvs` array and ELF class labels that the live inventory does not emit. It now derives package identities from `packages`, derives authoritative ELF identities from the independent owned-artifact census (`elf` metadata present), and compares those identities against the classification and safety records. The corrected script compiles and the live4 audit passes: 1,301 packages, 16,644 authoritative ELF records, zero missing classifications, and zero missing safety records. This is coverage accounting; the 11,021 pending ELF safety reviews remain explicit and no BOLT deployment is claimed.

### Live4 authority rotation after coverage correction (2026-09-18)

The coverage-auditor schema correction changed the source aggregate after the first live4 publication. The prior authority was explicitly deactivated, the corrected source was republished, and an independent strict installer check passed for framework `/var/lib/gentoo-optimization/framework-eca2741b00e1e1c9bbb37f7ecbf3761d8b8e9fb4946dd6920fd560c5c7e9dfda`. The same live4 generation triple was then reactivated transactionally and independently verified. No package mutation occurred during this authority rotation.

### Live4 first profile payload and receipt-sealing repairs (2026-09-18)

The exact live4 readiness wave initially selected `app-admin/doas-6.8.2` and an unavailable `dev-lang/ruby-3.4.9`. The controlled runner correctly refused the latter because no exact ebuild exists in the configured repositories. The reviewed lane override now records that installed CPV as `binary-only-no-rebuild-source`; live4 regeneration reduced the Rust lane to 28 records and increased terminal unsupported records to 228. The policy and framework were regenerated, strictly checked, and the same live4 authority was rotated and reverified.

The first doas training attempts exposed two implementation defects. Privileged helper invocations inherited the package profile path and polluted the package spool with administrative `doas` profiles; the runner now removes that path from the helper parent environment and suppresses only helper-runtime output. The runner also now seals a complete package spool by requiring two identical post-transaction snapshots. The merge verifier was corrected to compare receipt completeness against the exact package spool rather than all `.profraw` files in the shared generation root. Failed unreceipted helper profiles remain preserved under the live4 `unreceipted-helper-profiles` directory.

A clean one-package live4 wave for `app-admin/doas-6.8.2` then completed under the active authority. Its authenticated receipt is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-wave-receipt-doas-live4-final.json`; receipt verification passed. The receipt-bound raw payloads merged successfully with LLVM 22 into `/var/lib/gentoo-optimization/merged-profiles/app-admin_doas-6.8.2-live4-final.profdata`. Independent merge evidence is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-merge-doas-live4-final.json`, with evidence digest `30fe24a04271bdbf9925e30496c574afc7c48d26b012d61a726a824c8e30d11f`. This profile remains collection/merge evidence; dispatcher publication and PGO-use deployment are still pending.

### Live4 profile validation boundary (2026-09-18)

The merged doas profile was independently accepted by `validate-profile.py produce` under the active live4 authority using canonical Clang 22 and LLVM 22 `llvm-profdata` identities. The produced eight-line dispatcher manifest and metadata sidecar are retained as candidate validation evidence; they have not been installed into the profile-use cache or referenced by an active package policy, so no PGO-use deployment is claimed. The profile remains a valid collection/merge artifact pending a dedicated dispatcher-publication transaction.

The full portable suite was isolated to `tests/optimization/recovery`. The deterministic fixture tests reached the host-capability boundary, but the real-host `create-binpkg-checkpoint` test `test_ambiguous_offline_command_crashes_require_explicit_retry` entered a private `unshare --pid --fork --kill-child=KILL` copy operation that did not reach its supervisor deadline within the observation window. The test process was terminated without package or framework mutation; its host-specific teardown behavior remains an unresolved validation item and is not converted into a skip or a pass.

### Live4 second profile payload (2026-09-18)

The first live4 7zip attempt rebuilt and merged `app-arch/7zip-26.03` but was terminally rejected before receipt creation because the projected wave contained the enclosing workload-package record instead of its nested recipe list. The attempt remains journaled. The wave plan was corrected from the authoritative workload manifest and rerun. The exact 7zip transaction then completed with the ABI guard and corrected `/usr/bin/7zz --help` workload recipe; receipt verification passed at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-wave-receipt-7zip-live4-final.json`. LLVM 22 merged the receipt-bound payloads into `/var/lib/gentoo-optimization/merged-profiles/app-arch_7zip-26.03-live4.profdata`; independent merge evidence is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-merge-7zip-live4.json` with digest `c90d06c3478d94d6879db58ecb5e18e3bdf5a94a47ebaaa8f6f3acf470c81cd9`.

The 7zip profile passed `validate-profile.py produce` with the exact Clang 22/LLVM 22 tool identities and live4 generation triple. Its immutable cache payload and manifest are under `/var/cache/gentoo-optimization/pgo/phase3-live-candidate-20260918-live4/app-arch_7zip-26.03/`; the manifest reports `validation_status=passed` and profile SHA-256 `f8e73b2e6fcb906b5bd280763145da914a9fc9070223985c132249cf6e068739`. The profile is validated collection evidence and is not yet referenced by an active profile-use package policy.

### Live4 profile-use publication candidate (2026-09-18)

Added `scripts/optimization/pgo/publish-profile-dispatcher.py`, a fail-closed publication transaction for an exact CPV. It requires the validated Clang-IR manifest and deterministic metadata sidecar under the immutable profile cache, the generation fingerprint file, matching payload and fingerprint digests, and a valid generation-bound CPV. It atomically creates a profile-use environment fragment and a content-addressed publication record; existing outputs, unsafe paths, malformed identities, sidecar mismatches, and payload changes are refused. The live4 7zip profile produced a candidate-only fragment and record under `profile-use-candidate/7zip/`, with record SHA-256 `a1ad4a3d33e449f61aa70589577ba11fae3d9fea83e8fa2027a31c8615da68c5`. The fragment is not referenced by active `package.env`; no profile-use build or dispatcher activation is claimed.

## Live4 profile-use publication candidate (2026-09-18)

The ABI-guard traversal repair is present and focused validation remains green: 11/11 ABI cases and 11/11 Portage-QA cases. A generation-bound profile-use publisher was added at `scripts/optimization/pgo/publish-profile-dispatcher.py`. It validates an exact Clang-IR manifest, metadata sidecar, immutable cache payload, generation fingerprint, profile digest, and CPV, then atomically emits a profile-use environment fragment and publication record. Running it through the root-owned path against `app-arch/7zip-26.03` produced record SHA-256 `a1ad4a3d33e449f61aa70589577ba11fae3d9fea83e8fa2027a31c8615da68c5` under the live4 generation. A generated-policy candidate was constructed and the framework check correctly rejected the first attempt because the candidate identity did not match the installer’s exact tree contract; the active framework and package policy were not changed. Profile-use activation and rebuild remain pending until the candidate policy is regenerated with the installer’s canonical identity procedure.

### Live4 profile-use authority transition attempt (2026-09-18)

The existing live4 generation authority was deactivated transactionally before checking the profile-use policy candidate, as required when the generated-policy identity changes. The strict framework check still rejected the candidate at `verify_generated_policy` with `generated-policy identity differs`; no framework candidate was activated and no package policy changed. The prior live4 authority was immediately reactivated against framework `framework-2a9127943d5f096c7ea9661ad0269a76422468132544588b2a84dfbdcb45ad35`, and an independent generation-authority verification passed. The candidate identity mismatch is now isolated to the installer publication path and will be instrumented before another attempt; repeated unchanged retries are not authoritative.

### Live4 first profile-use deployment (2026-09-18)

The generated-policy candidate was published through the normal installer transaction after the identity diagnostic established that `--check` compares the currently active framework; the candidate must be installed before checking. Framework `framework-acbeee5ddfbe7bfcfaa1fe95f6377d36161ed4b23762916746db734117e9f582` passed the installer’s post-publication strict check, and live4 authority was reactivated and independently verified against it. The exact package mapping for `=app-arch/7zip-26.03` now selects `clang-ir-use` with the validated live4 profile, manifest, metadata, and fingerprint.

The first rebuild exposed a publication-permissions defect: root-owned cache payloads were not readable by the Portage build user. The profile cache remains root-owned and non-writable, but its files and directory are now group-readable/traversable by the `portage` group. `publish-profile-dispatcher.py` now enforces that contract during publication. A controlled exact 7zip rebuild then completed through install-QA with Clang 22 `-fprofile-use` flags. Durable Portage log evidence records 596 exact profile-use flag occurrences and zero newly created `.profraw` files after the rebuild cutoff. Receipt: `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-7zip-receipt.json`, SHA-256 `4ebe1a023859c59a8815da25fc9ba648d4cab31c4a6c5ac8ec9c546f941412ae`. Installed `/usr/bin/7zz` and `/usr/lib64/7z.so` were replaced by this successful transaction; no BOLT deployment is claimed yet.

## Live4 first profile-use deployment (2026-09-18)

The exact `app-arch/7zip-26.03` profile-use mapping was published through the normal framework transaction and the live4 authority was reactivated against framework `framework-acbeee5ddfbe7bfcfaa1fe95f6377d36161ed4b23762916746db734117e9f582`. The first build exposed and corrected a cache-permission defect: validated root-owned profile payloads must be group-readable/traversable by the Portage user while remaining immutable. The publisher now enforces that contract. A controlled exact rebuild completed successfully with 596 durable `-fprofile-use` occurrences in the Portage log and zero new `.profraw` files after the transaction start. Receipt SHA-256: `4ebe1a023859c59a8815da25fc9ba648d4cab31c4a6c5ac8ec9c546f941412ae`. No BOLT deployment is claimed.

### Live4 second profile-use deployment (2026-09-18)

Published the validated `app-admin/doas-6.8.2` profile-use fragment and rebuilt the exact CPV under a new generated-policy/framework transaction. The clean policy candidate used generated-policy identity `a5409aed24565011a460786d19941c4f2bb7a1cf25ec46e19b2f0755472811c5`; framework publication passed and live4 authority was reactivated against framework `/var/lib/gentoo-optimization/framework-5d4cd4710236760e5428d16287a3eb3986e7e4fec6676422da03a48605b83cb5`. The controlled rebuild completed through install-QA with 11 durable exact `-fprofile-use` flag occurrences, zero new raw profiles, and successful merge. Receipt `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-doas-receipt.json` has SHA-256 `bc19be1c7b5be32420d0068f07afe003f5598214d97de71adbbb012f585f46ed`.

## Live4 second profile-use deployment (2026-09-18)

Published and rebuilt exact `app-admin/doas-6.8.2` under the live4 profile-use dispatcher. The clean generated-policy identity was `a5409aed24565011a460786d19941c4f2bb7a1cf25ec46e19b2f0755472811c5`; framework publication and authority reactivation passed. Durable log evidence records 11 exact `-fprofile-use` flag occurrences, zero new raw profiles, and successful install-QA/merge. Receipt SHA-256: `bc19be1c7b5be32420d0068f07afe003f5598214d97de71adbbb012f585f46ed`.

### Live4 bzip2 profile-use rejection and safe revert (2026-09-18)

The live4-refresh bzip2 profile was validated and published from the earlier merged payload, then tested in an exact profile-use rebuild. The build reached install-QA but the unchanged exported-ABI guard rejected the staged DSOs: `libbz2.so.1` and its symlink/provider set lost `__llvm_write_custom_profile` (old export count 36, new count 35). No replacement was merged. The complete failed Portage log is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-logs-bzip2/app-arch:bzip2-1.0.8-r5:20260918-154724.log`. The active policy was transactionally reverted to the previously proven doas/7zip profile-use mapping with bzip2 back in its generate lane; framework `/var/lib/gentoo-optimization/framework-b064688ef5eed8796a70ad51815d409653846ae40d9f3c4e8aae1fbd2a499b04` passed publication and live4 authority verification. This is retained correctness-failure evidence, not a bypass or a terminal package exclusion yet.

## Live4 bzip2 profile-use rejection and safe revert (2026-09-18)

A live4-refresh bzip2 profile-use rebuild reached install-QA but was rejected by the exported-ABI guard because the staged `libbz2.so.1` provider lost `__llvm_write_custom_profile` (36 old exports versus 35 new). No replacement merged. The failed log is retained under the live4 `profile-use-logs-bzip2` directory. The active policy was transactionally reverted to the proven doas/7zip profile-use mapping with bzip2 back in generate mode; framework `framework-b064688ef5eed8796a70ad51815d409653846ae40d9f3c4e8aae1fbd2a499b04` and live4 authority verification passed.

### Live4 cabextract wave fetch interruption (2026-09-18)

A fresh exact `app-arch/cabextract-9999` Clang-generation wave passed readiness (`1/1`, zero invalid inputs) and entered the real Portage transaction under the active live4 authority. Its ebuild stalled in the network sandbox while fetching the `kyz/libmspack` Git repository: the fetch had no socket, CPU, or log progress for more than four minutes and never reached compilation or installation. The runner and child transaction were terminated cleanly; the attempt journal remains under `profile-wave-attempt-cabextract/`, no package merge occurred, and independent live4 authority verification still passes. This is preserved as a fetch-stage attempt failure, not a PGO result or an authorization bypass.

## Live4 cabextract wave fetch interruption (2026-09-18)

The fresh exact `app-arch/cabextract-9999` Clang-generation wave passed readiness (`1/1`) but stalled in the ebuild network sandbox fetching `kyz/libmspack` with no progress for more than four minutes. The transaction was terminated before compilation or installation, its attempt journal is preserved, and live4 authority verification still passes. No package merge or PGO result is claimed.

### Live4 cabextract retry and workload correction (2026-09-18)

The first cabextract attempt’s Git fetch interruption was retried successfully; the exact package rebuilt and merged in `clang-ir-generate` mode. Its generated workload recipe used `cabextract --help`, which correctly prints usage but exits status 1, so the runner rejected the package after merge and retained the attempt. A corrected derived wave changed the recipe to `cabextract --version`, passed readiness, and rebuilt/merged the package again, but the instrumented workload produced no `.profraw` files and the runner remained in its bounded post-transaction quiescence wait. That second attempt was terminated without profile admission. Both attempt journals are preserved; cabextract remains in generation state with no validated profile payload and no profile-use deployment claim.

## Live4 cabextract retry and workload correction (2026-09-18)

The cabextract fetch interruption was retried successfully and the package merged in generate mode. Its original `--help` recipe exited 1 after printing usage, so a corrected `--version` recipe was tested in a fresh wave. The corrected transaction merged but emitted no raw profile and remained in the runner's bounded quiescence wait; it was terminated without profile admission. Attempt journals are preserved and no cabextract profile-use result is claimed.

### Live4 cabextract profile collection and use deployment (2026-09-18)

The cabextract no-profile result exposed a runner bug: the outer helper suppression `LLVM_PROFILE_FILE=/dev/null` leaked into representative workload recipes. `run-profile-wave.py` now explicitly assigns the package spool pattern for generated-lane workloads while keeping privileged helper output suppressed. The corrected source was published and live4 authority reactivated against framework `/var/lib/gentoo-optimization/framework-bede83f4a2eb5ab896fa7e9de8b76d4262e9cf12dc1696f2150f81ddb69c7701`.

A fresh cabextract generation wave then collected one nonempty raw payload after the corrected `--version` recipe, passed receipt verification, and merged with LLVM 22. The validated live4 profile was published under `app-arch_cabextract-9999-live4`; exact profile-use policy publication passed as framework `/var/lib/gentoo-optimization/framework-ca322a63563098c2ef649617bce33e31bdafac2811d086fce784e25bc8d0381a`. A controlled profile-use rebuild completed through install-QA and merge with 12 durable `-fprofile-use` occurrences. No BOLT deployment is claimed yet.

## Live4 cabextract profile collection and use deployment (2026-09-18)

Fixed the profile-wave runner so helper suppression does not leak `LLVM_PROFILE_FILE=/dev/null` into representative generated workloads. A fresh cabextract wave then collected one nonempty raw payload with the corrected `--version` recipe, passed receipt verification, merged, and validated under live4. The profile-use mapping was published and a controlled cabextract rebuild completed through install-QA/merge with 12 exact `-fprofile-use` occurrences. No BOLT deployment is claimed.

### Workload recipe reproducibility repair (2026-09-18)

The live cabextract wave established that `/usr/bin/cabextract --help` prints usage but exits 1, while `--version` is a successful non-destructive representative workload that collected the validated profile payload. The authoritative `build-workload-recipes.py` generator now emits `--version` for cabextract so regenerated workload manifests reproduce the accepted recipe rather than depending on a manually corrected derived file. The generator was compiled and regenerated against live4; the exact cabextract record contains the expected `['/usr/bin/cabextract', '--version']` argv.

### Ruby Rust-lane terminal classification (2026-09-18)

A readiness-authorized exact `dev-lang/ruby-4.0.6` wave was refused before mutation by the profile runner because Ruby's bundled Rust toolchain reports LLVM 23 while the active Clang/profile toolchain is LLVM 22. The runner correctly rejected generation as ABI-incompatible; no package transaction or profile payload was admitted. This is retained as package-specific evidence in `wave-ruby-prep.json` and `readiness-ruby-prep.json`. A derived override candidate records the exact CPV as `unsupported-by-upstream-toolchain` with reason `rust-bundled-llvm23-incompatible-with-active-llvm22`; regenerated live4 lane candidates now contain 229 unsupported records and 27 remaining Rust-lane records. The active framework and authority were not changed by this candidate classification.

### Live4 cpio Clang profile generation (2026-09-18)

The exact `app-arch/cpio-2.15` Clang-IR generation wave passed readiness (`1/1`, zero invalid inputs), completed the real Portage rebuild through install-QA and merge, and collected one nonempty raw payload. The authenticated receipt is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-wave-receipt-cpio-prep.json`; receipt verification passed. LLVM 22 merged the payload into `merged-profiles/app-arch_cpio-2.15.profdata`, and `validate-profile.py produce` accepted it with the live4 fingerprint and canonical Clang/LLVM 22 identities. Dispatcher publication and profile-use deployment remain pending for this package.

The cpio merged profile was independently validated and republished into the immutable live4 profile cache with the cache-bound profile path. The exact dispatcher publisher accepted the manifest and emitted candidate record `15f402d94e9a0b10cbfcaf808f6c73fcb8bd9e0929716c2f95aa5e9c146a43b2` under `profile-use-candidate/cpio/`. This remains candidate evidence until the generated policy is transactionally published and an exact profile-use rebuild proves deployment.

### Live4 cpio profile-use deployment (2026-09-18)

After correcting the CPV-specific fingerprint, profile path, and deterministic metadata-sidecar references in the generated policy, the exact `app-arch/cpio-2.15` profile-use transaction completed through install-QA and merge under framework `framework-01ad24358f322b2f55d4644ec38372ecc9a301e88556e1325f427c1e5cc088ff`. The captured durable log records repeated `mode=clang-ir-use` dispatch with the exact live4 cache profile and 147 `-fprofile-use` occurrences; no new raw profile was produced. The installed `/bin/gcpio` identity and log/profile hashes are recorded in `profile-use-cpio-receipt.json` (receipt SHA `3cc50262156fd6cf341950bd22c4c6cda4c3483892d7199a80919624f6f6aa5c`). The earlier policy-path attempts remain preserved as pre-compilation failures.

### Live4 gzip profile generation and dispatcher candidate (2026-09-18)

The exact `app-arch/gzip-1.14_p20260901` Clang-IR wave passed readiness (`1/1`, zero invalid inputs), completed the generate-lane Portage rebuild and workload, and collected one nonempty raw payload. Receipt verification passed; LLVM 22 merged the payload into `merged-profiles/app-arch_gzip-1.14_p20260901.profdata`, and `validate-profile.py produce` accepted it under the live4 fingerprint. The immutable cache payload was published and the dispatcher emitted candidate record `9aad86f48f8ac3d694ddf29d48e5bf86bfad0edbe13dbfb257f8b386af71644e`. Profile-use policy publication and the exact use rebuild remain pending.

### Live4 gzip profile-use deployment (2026-09-18)

The exact `app-arch/gzip-1.14_p20260901` profile-use policy was published and the controlled rebuild completed through install-QA and merge under the live4 framework. The durable log records `clang-ir-use` dispatch with the exact cache profile and 64 `-fprofile-use` occurrences, zero new raw profiles, and successful installation of `/bin/gzip-reference`. Receipt `profile-use-gzip-receipt.json` records the profile, log, binary, framework, and content hashes.

### Live4 libarchive profile generation and workload correction (2026-09-18)

The exact `app-arch/libarchive-3.8.9` Clang-IR generation transaction passed the ABI guard and merged successfully under the live4 authority. The initial generated workload was rejected because `bsdunzip --help` exits 1 after printing usage; that failed attempt and receipt remain preserved. The authoritative workload generator now selects the successful non-destructive `--version` action for all libarchive BSD utilities. A regenerated one-package wave then passed readiness, collected twelve nonempty raw payloads, and produced merged profile evidence at `/var/lib/gentoo-optimization/merged-profiles/app-arch_libarchive-3.8.9-live4.profdata` with independent merge evidence `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-merge-libarchive-live4.json` (digest `729d011ce57a66bce38868efd0bcb2e4078a3f993622645f3275cf131d68fb2b`). Profile validation and profile-use deployment remain pending; no BOLT deployment is claimed.

The merged libarchive profile was then copied into the immutable live4 cache and independently accepted by `validate-profile.py produce` under the exact Clang 22/LLVM 22 tool identities and live4 generation triple. The cache manifest and metadata sidecar are retained under `/var/cache/gentoo-optimization/pgo/phase3-live-candidate-20260918-live4/app-arch_libarchive-3.8.9/`. The generation-bound dispatcher publisher accepted that cache payload and emitted candidate record `58e6eef0a3b8bafe0bad0c8869eaaf6cd31b14b828bbe6f439257959e06236ed`; active policy publication and the exact profile-use rebuild remain pending.

The exact libarchive profile-use rebuild then dispatched `clang-ir-use` with the validated live4 cache profile and reached install-QA, but the exported-ABI guard rejected the staged DSOs because the prior instrumented provider exported `__llvm_write_custom_profile` and the profile-use provider did not (`old=454`, `new=603`). No replacement merged. The failed log is retained in the Portage build tree and the profile-use attempt remains correctness-failure evidence. The active policy was transactionally reverted to the previous libarchive generation mapping and live4 authority was independently reverified; no ABI guard bypass or terminal exclusion has been claimed.

The ABI finding exposed a guard-model defect: `__llvm_write_custom_profile` is an LLVM instrumentation runtime helper, not package ABI, and is intentionally absent from ordinary/profile-use DSOs. The guard now filters that one implementation symbol while preserving all public export, SONAME, ELF-type, symlink, and provider-loss checks; the focused ABI suite remains 11/11. After a controlled `optimization-off` baseline rebuild removed the training helper from the installed libarchive image, the exact validated profile-use mapping was republished and the live4 authority rotated. A second exact rebuild completed through install-QA and merge with `mode=clang-ir-use`, 532 durable `-fprofile-use` log occurrences, zero new raw profiles, and a successful installed `/usr/bin/bsdtar`. Receipt `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-libarchive-receipt.json` has SHA-256 `3dbe0df8b36f47592a8a911d4766e1e05c86ac4f2ba9f4021b831abf67bf4d71`. No BOLT deployment is claimed.

### Live4 lz4 profile generation (2026-09-18)

The next exact `app-arch/lz4-1.10.0-r1` Clang-IR generation wave passed readiness (`1/1`, zero invalid inputs), completed through install-QA and merge, and collected one nonempty raw payload from the authoritative `/usr/bin/lz4 --help` workload. Receipt verification passed; LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-arch_lz4-1.10.0-r1-live4.profdata`, with independent merge evidence `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-merge-lz4-live4.json` (digest `d687712e5bef1206605e1efe72377871c67c82015b44451c06a70de14fdb48f0`). Profile validation and profile-use deployment remain pending; no BOLT deployment is claimed.

The lz4 profile was accepted into the immutable live4 cache under the exact Clang 22/LLVM 22 identity and published through the dispatcher as record `c0b1c707f38bc733fb60ab06cdd1f878bfcd3877fca807628d55c2365b209271`. The exact `clang-ir-use` policy was then published and authority-rotated. A controlled rebuild completed through install-QA and merge with 18 durable `-fprofile-use` occurrences, zero new raw profiles, and successful `/usr/bin/lz4` installation. Receipt `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-lz4-receipt.json` has SHA-256 `19ea08dc81ea27fda2e8b2129f5d0ac7efb3b38e05099560570581da2d024592`.

### Live4 ncompress profile generation and workload correction (2026-09-18)

The first exact `app-arch/ncompress-5.0-r2` generation wave passed the transaction but correctly rejected the generated `compress` workload because the utility treats `--help` as an unknown option. The authoritative workload generator now selects ncompress's successful historical `-V` version action. The regenerated wave passed readiness (`1/1`, zero invalid inputs), rebuilt and merged under the live4 authority, collected a nonempty raw payload, and produced merged profile evidence at `/var/lib/gentoo-optimization/merged-profiles/app-arch_ncompress-5.0-r2-live4.profdata` with independent evidence digest `4dbaa282f0ead1ac64781a8176f322a4b27af0391f379d2f5bf2a98e2b7bd38d`. Profile validation and profile-use deployment remain pending.

The ncompress profile was accepted into the immutable live4 cache and published through the dispatcher as record `24d38ae1b27112a7b1869586f85962df6d8d893868f9c7181ffe2d15f09af00b`. Its exact `clang-ir-use` policy was published and authority-rotated; the controlled rebuild completed through install-QA and merge with one durable `-fprofile-use` occurrence, zero new raw profiles, and successful `/usr/bin/compress` installation. Receipt `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-ncompress-receipt.json` has SHA-256 `73f91cced0fa36f4704b8d184303b4fc88135b538e34483642564888e9242195`.

The rpm2targz generation attempt passed the package transaction but its only entrypoint requires input and was retained as an open workload boundary; no payload was admitted. The next exact `app-arch/tar-1.35-r1` generation wave passed readiness (`1/1`, zero invalid inputs), completed through install-QA and merge, and collected a nonempty `/bin/gtar --help` profile. LLVM 22 merged it into `/var/lib/gentoo-optimization/merged-profiles/app-arch_tar-1.35-r1-live4.profdata`; independent merge evidence digest is `f1f38cd6909e1fb36d6fb5fd7ed0d5ab3fdc35d703c7a49b2fc71a69ee8d6355`. Profile validation and profile-use deployment remain pending.

The tar profile was accepted into the immutable live4 cache and published through the dispatcher as record `64251b463592d2eb4dd71d754181ad79f74f43d8b97bda1f239e5baad5ea5197`. Its exact `clang-ir-use` policy was published and authority-rotated; the controlled rebuild completed through install-QA and merge with 197 durable `-fprofile-use` occurrences, zero new raw profiles, and successful `/bin/gtar` installation. Receipt `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-tar-receipt.json` has SHA-256 `0ae52050e2613ee086cdaac32e870f4d3d5bb5a33a4f37fd7fb681b044ba83a0`.

The exact `app-arch/zip-3.0_p16` generation attempt was refused before compilation because this legacy ebuild presents `CC` with shell-style compiler flags while the current dispatcher requires a single compiler command. No package merge or profile payload was admitted; the complete attempt and build log are retained. This is an implementation compatibility defect to fix in the dispatcher/eclass boundary, not a terminal package exclusion.

The compiler resolver now accepts a safe compiler executable followed by ordinary non-shell arguments, while rejecting shell metacharacters and wrappers; the dispatcher continues to canonicalize and export the immutable compiler executable. After republishing the framework and re-verifying live4 authority, the exact zip generation wave completed successfully. The initial `--help` recipes were then corrected because zipnote returned status 16; the authoritative workload generator now uses `-v` for all zip utilities. The corrected wave passed readiness (`1/1`), merged through install-QA, collected a nonempty profile, and LLVM 22 produced `/var/lib/gentoo-optimization/merged-profiles/app-arch_zip-3.0_p16-live4.profdata` with independent merge digest `6114db7521b7b50844070176dfb66bbc14a850f9a464a8cfb11a741f9534d464`. Profile validation and profile-use deployment remain pending.

The zip profile was accepted into the immutable live4 cache and published through the dispatcher as record `19957e206609a91257a4318a9cf08431d044685eaa088babe572591f3a4dee9d`. Its exact `clang-ir-use` policy was published and authority-rotated; the controlled rebuild completed through install-QA and merge with 27 durable `-fprofile-use` occurrences, zero new raw profiles, and successful `/usr/bin/zip` installation. Receipt `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-zip-receipt.json` has SHA-256 `5ecda12075dd662da52f1afbc65f4ec98312a1781cac480ceb48acb8e0e75f4b`.

### Live4 rpm2targz workload boundary (2026-09-18)

The exact `app-arch/rpm2targz-2021.03.16` generation transaction passed install-QA and merged under live4, but its sole installed entrypoint `/usr/bin/rpmoffset` requires an RPM stream on standard input and exits 1 with no input. The current representative-workload schema binds argv only and cannot safely invent an input artifact, so the runner rejected the recipe before profile admission. The failed attempt is retained; no profile or terminal exclusion is claimed. An input-bound workload implementation or an evidence-backed package-specific terminal state is still required.

### Live4 ABI-guard traversal verification and xz receipt reconciliation (2026-09-18)

The live ABI guard implementation now distinguishes `relative_dirs=None` from an intentionally empty directory set, returns immediately for staged images with no `.so` candidates, and limits installed-provider discovery to immediate candidate parent directories. The focused ABI suite passed all catastrophic-loss, SONAME, symlink, ELF-type, empty-DSO, and non-recursive-provider cases. The xz final runner receipt initially listed six payloads while the shared raw spool continued receiving late nonempty payloads; the independent merger correctly refused that incomplete receipt. After confirming no active xz/emerge processes remained, a reconciled receipt was produced from all eleven nonempty payloads, independently hashed, verified against the live4 generation triple, and merged successfully with LLVM 22. The merged profile passed `validate-profile.py produce` and the generation-bound dispatcher publisher emitted candidate record `5db46710d5f6af8d4386603b1ba989af43f4cbcbeb9d63ba3ed070a03734c71b`. Exact policy publication and profile-use deployment remain pending; no xz profile-use success or BOLT deployment is claimed.

### Live4 xz profile-use publication and fetch-stage interruption (2026-09-18)

The reconciled xz profile passed generation-bound validation and was published into the immutable live4 cache. The policy tree was transactionally installed and live4 authority transitioned to the resulting framework; a controlled exact `app-arch/xz-utils-9999` rebuild entered `clang-ir-use` with the expected fingerprint and profile path, confirming dispatcher deployment. The ebuild then stalled during its upstream Git fetch with no socket/CPU/log progress for more than two minutes. The emerge and fetch process group was terminated cleanly; no compilation, install-QA, merge, or profile-use deployment is claimed from this attempt. The complete fetch-stage log is retained under `profile-use-logs-xz/`; xz remains pending a fresh exact profile-use rebuild using a resolved source/fetch path.

### Live4 xz profile-use deployment (2026-09-18)

The xz ebuild's stalled upstream fetch was resolved by using the authenticated existing Portage Git cache through `EGIT_OVERRIDE_REPO_TUKAANI_PROJECT_XZ=file:///var/cache/distfiles/git3-src/tukaani-project_xz.git`. The exact `app-arch/xz-utils-9999` rebuild then entered `clang-ir-use` with the live4 fingerprint and immutable cached profile, completed compilation, install-QA, and merge, and installed `/usr/bin/xz` (SHA-256 `bf1d268d68996cdd79f81e51e68599925ee8c45429bc07616d08a9da229e6a6b`). The durable log records 380 `-fprofile-use` occurrences and successful merge under framework `framework-fb5b08ae44f9a3c9bfb179d55924fbd91bc55834ca9e896fe2b451026fc3262d`. The profile-use receipt is `profile-use-receipt-xz.json` with SHA-256 `92b1d1e3902c89379858486250aeb4dda8151b8c67020e93fa012fd6a33136fd`; the xz spool contains 43 nonempty late profile files from the generate/use attempts, which are recorded rather than silently discarded. This is a verified profile-use deployment; no BOLT deployment is claimed.

### Input-bound rpm2targz workload implementation (2026-09-18)

The prior rpm2targz workload boundary was an implementation gap, not a package exclusion. Representative workload recipes now support a fail-closed `stdin_path` fixture under `/var/lib/gentoo-optimization/workloads/`; the runner canonicalizes the path, rejects symlinks and escapes, opens the immutable fixture, and feeds it to the reviewed entrypoint. A root-owned minimal RPM fixture was built with `rpmbuild` and verified through `/usr/bin/rpmoffset` (successful conversion with five-byte output). The workload generator now emits this input-bound recipe for `app-arch/rpm2targz-2021.03.16`; regeneration produced a `recipe-ready` record. Python compilation and the focused profile-wave guard test passed. A fresh rpm2targz generation wave is still required to collect and merge its profile.

### Live4 rpm2targz input-bound profile generation and deployment (2026-09-18)

The new input-bound workload machinery was exercised end-to-end for `app-arch/rpm2targz-2021.03.16`. A minimal root-owned RPM fixture was supplied to `/usr/bin/rpmoffset`; the exact generate wave passed install-QA and merge, its receipt verified, and LLVM 22 produced a validated live4 profile. Dispatcher publication and framework authority rotation succeeded. The exact profile-use rebuild then completed with `clang-ir-use`, two durable `-fprofile-use` occurrences, successful install-QA and merge, and installed `/usr/bin/rpmoffset` (SHA-256 `ba66730cd28e07cde35ebafe067c841bd73bab7ab54f91bda3b5315f60ed6b34`). Receipt `profile-use-receipt-rpm2targz.json` records zero new raw profiles and the durable log/profile hashes. No BOLT deployment is claimed.

### Live4 cabextract profile-use retry (2026-09-18)

The existing validated cabextract profile and dispatcher mapping were independently checked. A fresh exact profile-use rebuild entered `clang-ir-use` with the expected fingerprint and cached profile, but the ebuild stalled again while fetching `kyz/libmspack.git` with no progress for more than two minutes. The process group was terminated cleanly and the fetch-stage log remains under `profile-use-logs-cabextract-retry/`; no profile-use deployment is claimed from this retry. The validated cabextract profile remains candidate evidence pending a fetch-cache override analogous to the successful xz repair.

### Live4 cabextract profile-use deployment (2026-09-18)

The cabextract profile-use fetch interruption was resolved with the authenticated cached `kyz/libmspack` repository via `EGIT_OVERRIDE_REPO_KYZ_LIBMSPACK=file:///var/cache/distfiles/git3-src/kyz_libmspack.git`. The exact rebuild entered `clang-ir-use`, completed through install-QA and merge, and installed `/usr/bin/cabextract` (SHA-256 `6f2730ca242387559ec6811c0a5c80c556849c350598ecffc61975e53c72e7de`). The durable log records 12 `-fprofile-use` occurrences and zero new raw profiles. Receipt `profile-use-receipt-cabextract.json` records the framework, profile, binary, and log hashes. No BOLT deployment is claimed.

### Live4 dpkg Clang profile generation (2026-09-18)

The exact `app-arch/dpkg-1.22.21` wave was planned against the authoritative live4 lane, compiler, and fingerprint manifests. Readiness passed (`1/1`, zero invalid inputs); the real generate-lane transaction completed through install-QA and merge, the reviewed dpkg utility workloads ran, receipt verification passed, and LLVM 22 merged the payload. Independent merge evidence is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-merge-dpkg-live4.json` with digest `8d07d4f31c664e1c9678c3e504cd346a529319edc6503de7c74323f4b1034a0a`. Profile validation, dispatcher publication, and exact profile-use deployment remain pending.

### Live4 dpkg profile-use deployment (2026-09-18)

The validated dpkg profile was published through the live4 dispatcher and policy framework. The exact `app-arch/dpkg-1.22.21` rebuild entered `clang-ir-use`, completed compilation, install-QA, and merge, and installed `/usr/bin/dpkg` (SHA-256 `be404f6650ddf1a640b31855b974fb883c67469baa29956d0925ae950c06b954`). The durable log records 258 `-fprofile-use` occurrences and zero new raw profiles. Receipt `profile-use-receipt-dpkg.json` records the deployment hashes. No BOLT deployment is claimed.

### Live4 unzip workload correction and generation (2026-09-18)

The first exact `app-arch/unzip-6.0_p31` generation transaction merged, but `funzip --help` exited 3 because funzip requires archive input. The authoritative workload generator now excludes `funzip` and `unzipsfx` and emits the successful standalone `/usr/bin/unzip -v` recipe. A fresh readiness-authorized wave passed (`1/1`, zero invalid inputs), rebuilt and merged the package, ran the corrected workload, passed receipt verification, and merged the raw payload with LLVM 22. Independent merge evidence is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-merge-unzip-live4.json` with digest `1d6b830dbaadb1a5daabb614b476cc7788dfbf3f191e13b0ada9918377647ab3`. Profile validation, dispatcher publication, and profile-use deployment remain pending.

### Live4 unzip profile-use deployment (2026-09-18)

After correcting the invalid `funzip --help` workload to `/usr/bin/unzip -v`, the validated unzip profile was published through the live4 dispatcher and policy framework. The exact `app-arch/unzip-6.0_p31` rebuild entered `clang-ir-use`, completed install-QA and merge, and installed `/usr/bin/unzip` (SHA-256 `8502d158e0429abf2caeb302c5e32bbf100c154d2986805d01e435b60f0d9405`). The durable log records 39 `-fprofile-use` occurrences and zero new raw profiles. Receipt `profile-use-receipt-unzip.json` records the deployment hashes. No BOLT deployment is claimed.

### Live4 zstd late-payload sealing failure (2026-09-18)

The exact `app-arch/zstd-1.5.7-r1` generation transaction completed through install-QA and merge and all reviewed workloads ran, but the shared raw spool continued receiving late `.profraw` payloads after the runner completed. The initial receipt listed eight payloads; successive reconciliations observed ten, twelve, and fourteen payloads, and the independent merger correctly refused each receipt because the directory was still changing. No zstd profile was admitted or merged, and no profile-use deployment is claimed. The original receipt and attempt journal remain preserved for a runner process-group/quiescence repair.

### Profile-wave writer-quiescence repair and zstd regression (2026-09-18)

The runner's fixed post-transaction grace window did not prove that late instrumented helper writers had exited; zstd repeatedly produced new payloads after receipt sealing. `run-profile-wave.py` now scans `/proc/*/environ` for live processes carrying the exact package profile destination, waits up to five minutes for those writers to disappear, and only then performs a short flush grace and stable snapshot seal. Syntax compilation and the focused profile-wave guard test pass. A fresh zstd regression wave completed the package transaction and workload, but its shared spool still accumulated late helper payloads after the run; the independent merger refused the receipt. No zstd profile admission or profile-use deployment is claimed. The additional late-payload evidence is retained for the next runner refinement.

### Zstd process-session and orchestration-profile repair (2026-09-18)
The zstd late-payload failure was isolated further. A fresh live4 generation wave under an isolated profile leaf completed the exact `app-arch/zstd-1.5.7-r1` transaction and all three reviewed workloads. The runner now clears inherited `LLVM_PROFILE_FILE`/`GENTOO_OPT_PROFILE_PATH` before orchestration, executes each workload in an isolated process session, and waits for session teardown before sealing. The fresh receipt `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-wave-receipt-zstd-fresh3.json` remained stable at six payloads and passed `verify-wave-receipt.py`. Independent LLVM 22 merging accepted the exact receipt and produced `/var/lib/gentoo-optimization/merged-profiles/app-arch_zstd-1.5.7-r1-live4-fresh3.profdata`; merge evidence is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-merge-zstd-live4-fresh3.json`. An additional profile emitted by an un-suppressed verification process was quarantined under `zstd-unexpected-profile-writers/` and was not admitted. No profile-use deployment is claimed yet.

The fresh zstd merged profile was accepted by `validate-profile.py produce` under the exact live4 fingerprint and Clang/LLVM 22 identities. It was copied into the immutable live4 PGO cache at `/var/cache/gentoo-optimization/pgo/phase3-live-candidate-20260918-live4/app-arch_zstd-1.5.7-r1-fresh3/`; the generation-bound dispatcher publisher emitted candidate record `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/profile-use-candidate-zstd.json` (record SHA `dbaca1b63396f9559a8071361d68905829a2731e08f0eef463f8935207ac53e7`). Policy publication and the exact profile-use rebuild remain pending.

### Zstd profile-use validation-sidecar mismatch (2026-09-18)
The live4 zstd profile-use policy was activated and the exact CPV entered `clang-ir-use`; the durable build log shows the validated fresh5 profile path and repeated `-fprofile-use` compiler/linker flags through complete source compilation. Install-QA then refused before merge with `validation metadata no longer matches current complete identities and proof`. The failure is preserved in the zstd Portage build log and no profile-use deployment or installed replacement is claimed. Investigation shows the working-tree validator and the root-owned deployed validator differ; the next repair must align the metadata producer with the authoritative deployed verifier and rerun the exact transaction. No guard or metadata check was bypassed.

### Live4 zstd profile-use deployment after validator isolation (2026-09-18)
The validator was republished with an explicit `LLVM_PROFILE_FILE=/dev/null` subprocess environment and the exact `app-arch/zstd-1.5.7-r1` profile-use rebuild completed compilation, install-QA, qmerge, and merge under the fresh7 live4 profile. The installed CPV is therefore a successful profile-use deployment candidate. The transaction still exposed late LLVM runtime writes from previously instrumented helper binaries, including stale live2 bzip2 and fresh3 zstd destinations; these writes were non-fatal but remain a correctness/noise defect. Portage bashrc now clears both `LLVM_PROFILE_FILE` and the inherited `__LLVM_PROFILE_RT_INIT_ONCE` marker before orchestration. A follow-up harmless no-DSO regression is required before declaring validator/profile-output isolation closed. No BOLT deployment is claimed.

### Live4 no-DSO LLVM-output regression (2026-09-18)
After republishing the framework with `LLVM_PROFILE_FILE=/dev/null` exported at the Portage phase boundary, the exact `dev-dotnet/dotnet-runtime-nugets-8.0.30` reinstall completed in 15 seconds through install-QA and merge. The package has a 512.2 MiB data-only installed tree and no conventional staged DSO workload. Its durable log `/tmp/dotnet-nugets-no-dso.log` contains no `LLVM Profile Error` or ABI-guard failure, confirming the no-DSO path no longer triggers the prior expensive/profile-writing behavior during this regression. The raw spool file count changed only from 30,707 to 30,710 due to pre-existing asynchronous writers; no new profile errors were emitted by this transaction. The stale bzip2 binary remains historical evidence and is not used as a clean profile source.

### Live4 zip profile-use deployment (2026-09-18)
The exact `app-arch/zip-3.0_p16` profile-use rebuild entered `clang-ir-use` with the live4 fingerprint `84b75f3a0d0e50972c2ca08dadd4bdecff5a437070cefe653fd73cb1e9479889`, consumed the immutable cached profile `/var/cache/gentoo-optimization/pgo/phase3-live-candidate-20260918-live4/app-arch_zip-3.0_p16/profile.profdata`, completed compilation, install-QA, and merge, and installed `/usr/bin/zip`, `zipnote`, `zipcloak`, and `zipsplit`. The durable log is `/tmp/zip-profile-use.log`; it contains 168 `-fprofile-use` flag occurrences and no LLVM profile-write errors. Installed binary hashes were captured immediately after merge. No BOLT deployment is claimed.

### Live4 gzip profile-use deployment (2026-09-18)
The exact `app-arch/gzip-1.14_p20260901` rebuild entered `clang-ir-use` with fingerprint `f0a16fadb6579d31c529bb8464f1eea091febbd0a1bcd1f20af0103eab1c7f44`, consumed the live4 cached profile, completed compilation, install-QA, and merge, and installed the gzip reference and helper binaries. The durable log `/tmp/gzip-profile-use.log` records 176 `-fprofile-use` flag occurrences and no LLVM profile-write errors. Installed hashes were captured after merge. No BOLT deployment is claimed.

### Live4 cpio profile-use deployment (2026-09-18)
The exact `app-arch/cpio-2.15` rebuild entered `clang-ir-use` with fingerprint `fb70adec2af76eff875c2450e45f16e2e1bec2384812a0fc3df27be389e6bf22`, consumed the live4 cached profile, completed compilation, install-QA, and merge, and installed `/bin/gcpio`. The durable log `/tmp/cpio-profile-use.log` records 196 `-fprofile-use` flag occurrences, no LLVM profile-write errors, and a successful merge. Installed `/bin/gcpio` was hashed after merge. No BOLT deployment is claimed.

### Live4 tar profile-use deployment (2026-09-18)
The exact `app-arch/tar-1.35-r1` rebuild entered `clang-ir-use` with fingerprint `0d69bbda6fb82d7727e60f5d3dbc64bfa38e50194ce7f94ad953b01e3af00678`, consumed the live4 cached profile, completed compilation, install-QA, and merge, and installed `/bin/gtar`. The durable log `/tmp/tar-profile-use.log` records the exact profile-use compiler path and no LLVM profile-write errors; the installed binary was hashed after merge. No BOLT deployment is claimed.

### Live4 ncompress profile-use deployment (2026-09-18)
The exact `app-arch/ncompress-5.0-r2` rebuild entered `clang-ir-use` with fingerprint `1835dd4484815f6d391ab0860a437125dbbe132646864c0bf2413e752f27f3e2`, consumed the live4 cached profile, completed compilation, install-QA, and merge, and installed `/usr/bin/compress` plus its `uncompress` symlink. The durable log `/tmp/ncompress-profile-use.log` contains the profile-use compiler invocation and no LLVM profile-write errors. The installed executable was hashed after merge. No BOLT deployment is claimed.

### Live4 doas profile-use deployment (2026-09-18)
The exact `app-admin/doas-6.8.2` rebuild entered `clang-ir-use` with fingerprint `f42b79adee26541f72732db1728bdc226ad99521c8a1a57141a4e373df90674e`, consumed the live4 cached profile, completed compilation, install-QA, and merge, and installed the PAM configuration and setuid `/usr/bin/doas`. The durable log `/tmp/doas-profile-use.log` records the profile-use compiler/linker invocations and no LLVM profile-write errors. The installed executable was hashed after merge. No BOLT deployment is claimed.

### Live4 7zip profile-use deployment (2026-09-18)
The exact `app-arch/7zip-26.03` rebuild entered `clang-ir-use` with fingerprint `146f20cfa51ad69e9485bbd4a185ee70cc0058b7a76022d4fd2b26c64f71cd48`, consumed the live4 cached profile, completed its large Clang build, install-QA, qmerge, and merge, and installed `/usr/bin/7zz`, its 7z/7za/7zr symlinks, and `/usr/lib64/7z.so`. The durable log `/tmp/7zip-profile-use.log` records the profile-use compiler invocations; only expected incomplete-profile warnings were emitted, with no LLVM profile-write errors. Installed executable and DSO hashes were captured after merge. No BOLT deployment is claimed.

### Live4 libarchive profile-use deployment (2026-09-18)
The exact `app-arch/libarchive-3.8.9` rebuild entered `clang-ir-use` with fingerprint `5fb936c5e7abbb337ee27111b5b26fdf139e6564fb5f647a6d8d153246e255fb`, consumed the live4 cached profile, completed both multilib builds, install-QA, qmerge, and merge, and installed the libarchive shared library plus `bsdtar`, `bsdcpio`, `bsdcat`, and `bsdunzip`. The durable log `/tmp/libarchive-profile-use.log` contains no LLVM profile-write errors and the transaction completed successfully; installed library and executable hashes were captured after merge. No BOLT deployment is claimed.

### Live4 lz4 profile-use deployment (2026-09-18)
The exact `app-arch/lz4-1.10.0-r1` rebuild entered `clang-ir-use` with fingerprint `5b468d533f727c8fb86569bf6621aced57f6ddfa1cb43d1f22ba3f87c139621d`, consumed the live4 cached profile, completed both multilib builds, install-QA, qmerge, and merge, and installed `/usr/bin/lz4` with its compatibility symlinks plus 32-bit and 64-bit liblz4 DSOs. The durable log `/tmp/lz4-profile-use.log` contains no LLVM profile-write errors and the transaction completed successfully; installed executable and DSO hashes were captured after merge. No BOLT deployment is claimed.

### Phase-3 BOLT safety review decode-failure repair (2026-09-18)
The live4 BOLT safety review initially aborted on an uncaught `UnicodeDecodeError` while decoding `readelf` output from an ELF candidate. Both `review-bolt-safety.py` and `extract-elf-metadata.py` now classify Unicode decoding failures as explicit fail-closed tool-invocation errors rather than aborting or silently accepting the artifact. The focused review was rerun against the authoritative live4 metadata and eligibility records and produced `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-live4/bolt-safety-review.json` with 1,936 `bolt-ready-pending-profile` records and 590 terminal `not-applicable` records. No BOLT output is claimed yet.

### BOLT hook fixture bounded validation (2026-09-18)
The BOLT hook fixture was rerun under shell tracing with a 45-second bound. It progressed through all provenance, count, build-ID, `.text`, rollback, and deployment checks and reached fixture cleanup before the observation window returned; no live state was touched. The production command-policy test passes independently. The current installed doas binary has no GNU build ID, so it cannot be used as a production BOLT input; exact pre-strip capture must occur during a package rebuild with `bolt-capture` enabled. No BOLT deployment is claimed.

### Live4 profile-use receipt reconciliation (2026-09-18)
The newer successful live4 profile-use merges had durable logs and installed hashes but lacked formal deployment receipts. Receipts were generated additively from the root-owned logs, current framework target, immutable cached profiles, and live installed binaries for doas, 7zip, libarchive, lz4, zip, gzip, cpio, tar, ncompress, and zstd. A separate verifier reopened all 15 live4 `profile-use-receipt-*.json` records, recomputed each receipt digest, and confirmed every referenced log, profile, and installed binary exists. All 15 receipts verified successfully; no BOLT deployment is claimed.

### Live4 ABI-guard immediate-directory scaling regression (2026-09-18)

The ABI guard traversal repair was independently rechecked after the latest source change. The focused ABI suite passed all 11 cases and the Portage QA-hook suite passed all 13 cases, including explicit zero-DSO and non-recursive-provider regressions. The regression fixture now places 2,000 unrelated files beneath a nested descendant of a candidate DSO directory; the guard completes within the bounded timeout because provider discovery examines only immediate candidate-parent entries. The zero-DSO fixture likewise returns without traversing an unrelated 2,000-file tree. The candidate framework was republished and its root-owned strict `--check` passed. No ABI semantics were weakened and no boot/kernel state was touched.

### Phase-3 coverage authority join repair (2026-09-18)

The coverage verifier had been filtering the authoritative ELF census on a nonexistent `elf` field, producing a vacuous `elf_count: 0` result. It now joins the census records by their emitted `class` and `type` fields, rejects an empty authority set, and has a regression test for both the valid join and the former false-pass shape. The root-owned live4 coverage artifact was regenerated and independently hashed: `elf_count=16642`, `elf_missing_classification=0`, `coverage_pass=true`; 14,114 ELF records remain outside the current BOLT safety-review subset and are retained as pending safety work rather than treated as covered.

### Post-live4 repository synchronization drift (2026-09-18)

The Gentoo repository sync completed through the authenticated rsync quarantine path. The subsequent read-only `emerge -pvuDN --with-bdeps=y @world` was preserved at `/tmp/phase3-postsync-world-pretend.log` and returned a real resolver conflict: the project userspace set requests SPIR-V 1.4.357 while installed consumers still require the 1.4.350 ABI, and the current wlroots/Sway graph requests a changed `-x11-backend` setting. `emerge -p --depclean` was also preserved at `/tmp/phase3-postsync-depclean-pretend.log` and correctly refused before a complete update. The live4 inventory and derived manifests are therefore stale relative to the synchronized repository/package graph; no package mutation or SPIR-V ABI bypass was performed. The coordinated SPIR-V consumer-closure repair remains the next prerequisite before freezing a successor generation.

### SPIR-V successor closure staging after repository sync (2026-09-18)

A read-only explicit closure pretend for the synchronized tree resolved the proposed 1.4.357 provider set without forcing it through the ABI guard: `spirv-headers-1.4.357.0`, `spirv-tools-1.4.357.0`, `glslang-1.4.357.0`, `vulkan-layers-1.4.357.0`, the existing `shaderc-2026.2`, `mesa_clc-9999`, `wlroots-9999:0.21`, the retained `wlroots-0.20.2:0.20` consumer slot, and Hyprland were selected. The pretend also exposed the required dual-slot consumer rebuild and a Hyprutils downgrade/rebuild edge. No package was mutated; the closure remains a staged resolver result until every affected consumer is included and the transaction is independently ABI-guard-clean.

### Post-sync inventory successor regeneration (2026-09-18)

After repository synchronization, a fresh candidate `phase3-live-candidate-20260918-postsync-r1` was generated from `/var/db/pkg` using the repaired CONTENTS parser and canonical owner/path ordering. The strict inventory verifier accepted `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-postsync-r1/frozen-inventory.json`; an independent CPV comparison found exact equality with the live VDB: 1,301 packages. The candidate contains 684,938 owned paths and 79,947 directory records, with four absent runtime directories resolved by the previously authenticated directory-review report. The owned-artifact census contains 684,758 records, including 16,644 ELF records and 160 explicit missing-live-artifact records. ELF metadata extraction completed with 16,644 records and 3,178 build IDs. Preliminary eligibility is 2,528 candidate-BOLT-eligible, 3,095 not-applicable, and 11,021 pending eligibility review. Backend correlation covered all 1,301 packages; lane assignment is complete with 509 `pgo-clang-ir`, 1 `pgo-gcc`, 6 `pgo-go`, 28 `pgo-rust`, 228 unsupported, 516 not-applicable, and 13 kernel-policy exclusions. The refreshed coverage audit reports `coverage_pass=true`, `elf_count=16642`, and zero missing ELF classifications. This successor remains a candidate; no framework or package policy was activated from it.

### Post-sync workload accounting regeneration (2026-09-18)

The post-sync successor workload chain was regenerated from its fresh lane and ELF manifests. It covers 550 PGO-lane package records: 324 workload candidates and 220 packages without runnable entrypoints in the workload manifest; recipe generation produced 296 recipe-ready packages, 242 explicit no-runnable-entrypoint records, and 6 `no-profile-producing-workload` records. Every generated JSON artifact in the successor chain was independently reopened and its embedded SHA-256 recomputed successfully. These are accounting artifacts only; no workload or profile wave has been authorized from the stale pre-sync framework.

### Post-sync policy-binding regeneration (2026-09-18)

The successor lane set was bound to the current compiler identities and copied exact existing per-CPV fingerprint evidence into its generation-local identity directory. `build-policy-bindings.py` completed without missing or malformed supported-lane fingerprints and emitted binding digest `cbf912cc04546ff01161322d66bb3b3d1909387e82e9ea88594006ffa01390c2`. A candidate policy tree was materialized from the reviewed environment files and validated for its 1,301 exact CPV assignments. It remains a candidate only: the synchronized SPIR-V resolver conflict and stale framework authority still prohibit activation or profile-wave execution.

### Successor policy identity boundary (2026-09-18)

The successor policy tree was materialized and its content-addressed inventory was checked against the installer’s canonical tree hash. A production installer `--check` was then attempted with the new successor inventory and policy. The installer correctly refused before activation because `.identity` and the frozen-inventory binding are generated inside the framework candidate transaction, not supplied as mutable source inputs; the existing active framework identity remains bound to the older live4 generation. This refusal is retained as non-authorizing evidence. No active framework, Portage policy, package state, or boot/kernel state was changed.

### Post-sync bounded profile-wave planning (2026-09-18)

The successor policy bindings and workload recipes produced a new bounded two-package wave plan (`pgo-clang-ir` and `pgo-rust`, one package per lane). The independent readiness verifier accepted both inputs (`ready_count=2`, `invalid_inputs=0`) against the exact successor inventory, compiler identities, and root-owned fingerprint directory. The readiness state remains `pending-framework-terminal-check`; no package transaction or workload execution was started while the successor framework remains inactive.

### Post-sync workload-exclusion coverage repair (2026-09-18)

The fresh workload audit initially found six PGO-lane packages marked `no-profile-producing-workload` that were absent from the exclusion manifest, leaving six missing records. `classify-no-entrypoint.py` now emits explicit workload exclusions for both `no-runnable-entrypoint` and `no-profile-producing-workload`, preserving the exact terminal reason. The regenerated successor audit passes with 544 PGO packages, 296 recipe-ready packages, 248 exclusions, and zero overlap, missing, or extra records. No workload execution is claimed.

## 2026-09-18 bounded profile-wave checkpoint

The successor framework was republished and strict-checked after correcting the profile-wave runner's Rust compatibility probe to use the reviewed absolute Clang identity when no unversioned `clang` exists on `PATH` (commit `de7ee5d`). The exact `app-admin/doas-6.8.2` `pgo-clang-ir` wave completed with a sealed receipt and three profraw payloads. `dev-lang/ruby-4.0.6` and `dev-util/bindgen-0.72.1` were refused before profile collection because their Rust LLVM 23 lane is incompatible with the active LLVM 22 LTO toolchain; the current lane manifest records Ruby as `unsupported-by-upstream-toolchain` with exact mismatch evidence. No incompatible Rust profile was forced.

### Rust LLVM compatibility gate (2026-09-18)

A focused bindgen wave confirmed that the Rust lane mismatch is a real mixed-LTO ABI failure: Rust LLVM 23 objects were rejected by the active LLVM 22 linker (`Producer LLVM23.1.1-rust-1.100.0-nightly`, `Reader LLVM 22.1.8+libcxx`). The runner's fail-closed Rust/Clang compatibility probe is retained and restored in commit `7a879ad`; Ruby and bindgen remain terminal `unsupported-by-upstream-toolchain` until a matching Rust/Clang toolchain is available. The failed attempt and linker evidence are preserved; no incompatible package was merged.

### Rust-only PGO lane isolation (2026-09-18)

The confirmed LLVM23/LLVM22 linker mismatch was isolated to inherited system LTO flags rather than Rust instrumentation itself. The profile-wave runner now sets `RUSTFLAGS=-C lto=off -C linker-plugin-lto=no` plus minimal C/C++/linker flags only for the `pgo-rust` transaction, preserving rustc's own `-Cprofile-generate` payload. After republishing the framework, `dev-util/bindgen-0.72.1` completed its Rust PGO rebuild, merge, and workload path with one sealed profraw payload in `profile-wave-receipt-bindgen-rust-nolto2.json`. Mixed-LTO packages such as Ruby remain refused until their Rust and Clang LLVM versions can be aligned.

### Additional Rust PGO wave (2026-09-18)

With the lane-specific no-cross-LLVM-LTO policy active, `dev-util/bpf-linker-0.11.1` completed its Rust PGO rebuild, merge, and reviewed workload. The sealed receipt `profile-wave-receipt-rust-next.json` contains one profraw payload. The Rust lane now has independently verified successful bindgen and bpf-linker payload collection; mixed-LTO Ruby remains excluded with linker evidence.

### Cargo-audit Rust PGO correctness stop (2026-09-18)

`dev-util/cargo-audit-0.22.2` compiled and staged with the Rust no-cross-LLVM-LTO lane, but its reviewed `/usr/bin/cargo-audit --help` workload exited SIGSEGV. The installed instrumented binary also reproduced SIGSEGV under `LLVM_PROFILE_FILE=/dev/null`, while the preserved pre-wave binary package `cargo-audit-0.22.2-1.gpkg.tar` passed `cargo-audit --version`. The failed profile-wave receipt and build log are retained; the known-good binary package was restored immediately. This CPV remains a terminal correctness failure pending package-specific remediation and is not counted as an optimized payload.

### Cargo-c Rust PGO wave (2026-09-18)

`dev-util/cargo-c-0.10.25` completed the isolated Rust PGO transaction, merge, and workload execution. The sealed receipt `profile-wave-receipt-cargo-c.json` contains four profile payloads. This package is now independently verified as a successful Rust profile-generation result.

### Cbindgen Rust PGO wave (2026-09-18)

`dev-util/cbindgen-0.29.4` completed the isolated Rust PGO rebuild, merge, and workload. Its sealed receipt `profile-wave-receipt-cbindgen.json` contains one profraw payload and a completed transaction state.

### Maturin Rust PGO attempt (2026-09-18)

`dev-util/maturin-1.15.0` reached a completed Rust build but failed during the ebuild's multi-Python compile phase because the expected `target/release/maturin` artifact was absent after the lane-specific target/LTO environment. The package did not merge; the previously installed `maturin 1.15.0` remained intact. A binary-package restoration attempt was rejected before mutation by the framework-generation cross-boundary guard and is retained as evidence. This CPV remains pending package-specific Rust/Python build-path remediation.

### Maturin host-layout repair and correctness result (2026-09-18)

The Rust dispatcher now supports an authenticated `GENTOO_OPT_RUST_HOST_LAYOUT=1` exception for `dev-util/maturin-1.15.0`, preserving the explicit Rust target while leaving Cargo artifacts in `target/release` as required by the Python build backend (commit `5433eae`). The rerun reached the correct artifact path, but the instrumented `maturin` binary segfaulted during the ebuild's `maturin completions bash` step; the same binary reproduced SIGSEGV with `LLVM_PROFILE_FILE=/dev/null`. No merge occurred. This is retained as a package-specific correctness failure after the layout remediation.

### Rustup Rust PGO correctness stop (2026-09-18)

`dev-util/rustup-1.29.0` completed its Rust PGO compilation, but the install phase's generated completion command (`./rustup completions bash`) segfaulted. The package did not merge; the failed attempt and build log are retained. This is a package-specific correctness failure after successful profile instrumentation, analogous to maturin's completion-path failure.

### AMDGPU top Rust PGO wave (2026-09-18)

`sys-apps/amdgpu_top-0.11.5` completed the isolated Rust no-cross-LLVM-LTO profile wave. The sealed receipt `profile-wave-receipt-amdgpu-top.json` records one profraw payload, exact successor inventory binding, and completed transaction state. The wave used the authenticated Rust target and preserved the lane's `RUSTFLAGS=-C lto=off -C linker-plugin-lto=no` isolation; no kernel or boot artifacts were touched.

### Successor framework republish after AMDGPU top wave (2026-09-18)

The successor framework was republished from the committed source with its exact post-sync generated policy and frozen inventory. The root-owned production installer completed the atomic publication, and an independent strict `--check` passed. The active framework remains bound to the same exact successor generation and inventory; no package, profile policy, or boot/kernel state was changed by this publication.

### 7zip Clang IR PGO wave (2026-09-18)

`app-arch/7zip-26.03` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-7zip.json` records one profraw payload, the reviewed Clang identity, exact inventory binding, successful merge, and completed `7zz --help` workload execution. The transaction stayed within userspace; no kernel, boot, or EFI state was touched.

### Successor framework republish after 7zip wave (2026-09-18)

The successor framework was republished again after the 7zip transaction so the live source aggregate, generated policy, and frozen inventory are synchronized. The root-owned installer completed publication and a fresh strict `--check` passed against the exact successor artifacts. No boot, kernel, EFI, or initramfs state was touched.

### Bzip2 Clang IR PGO wave (2026-09-18)

`app-arch/bzip2-1.0.8-r5` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-bzip2.json` records two profraw payloads, successful merge, and the reviewed `/bin/bzip2-reference --help` workload. The existing LLVM profile-output handling was preserved; no kernel, boot, EFI, or initramfs state was touched.

### Successor framework republish after bzip2 wave (2026-09-18)

The exact successor framework was republished after the bzip2 profile transaction, and the root-owned strict installer check passed against the successor generated policy and frozen inventory. The active framework therefore remains synchronized with the committed source and current Phase-3 authority inputs.

### Cabextract Clang IR wave fetch failure (2026-09-18)

The bounded `app-arch/cabextract-9999` Clang IR wave reached the ebuild unpack phase but could not fetch its configured `kyz/libmspack` git source: the authenticated fetch timed out after 300 seconds with DNS resolution failure. No package merge or profile payload was produced, and the installed package state was not changed. The failed attempt and Portage build log remain preserved; this CPV stays pending for a later network-available retry rather than being classified as a package correctness exclusion.

### Cpio Clang IR PGO wave (2026-09-18)

`app-arch/cpio-2.15` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-cpio.json` records one profraw payload, successful merge, and completion of the reviewed cpio workload. No kernel, boot, EFI, or initramfs state was touched.

### Successor framework republish after cpio wave (2026-09-18)

The successor framework was republished after the cpio profile transaction. The root-owned installer completed publication and the independent strict check passed against the exact generated policy and frozen inventory. The earlier failed check invocation used a command-line policy basename typo; it was corrected and did not mutate framework state.

### Dpkg Clang IR PGO wave (2026-09-18)

`app-arch/dpkg-1.22.21` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-dpkg.json` records nine profraw payloads, successful merge, and completion of the reviewed dpkg workload. No kernel, boot, EFI, or initramfs state was touched.

### Gzip Clang IR PGO wave (2026-09-18)

`app-arch/gzip-1.14_p20260901` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-gzip.json` records one profraw payload, successful merge, and completion of the reviewed gzip workload. No kernel, boot, EFI, or initramfs state was touched.

### Libarchive Clang IR wave ABI-guard stop (2026-09-18)

`app-arch/libarchive-3.8.9` compiled and staged under the exact successor Clang IR lane, but the fail-closed install-QA ABI guard rejected the replacement for exported-symbol loss in `libarchive.so.13` (old provider 603 exported symbols, staged provider 453; the missing set includes `__archive_*` and `PPMD8_kExpEscape`). The package did not merge and no profile receipt was issued. The rejection and build log are preserved as package-specific ABI evidence; the guard was not bypassed.

### Lz4 Clang IR PGO wave (2026-09-18)

`app-arch/lz4-1.10.0-r1` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-lz4.json` records one profraw payload, successful merge, and completion of the reviewed lz4 workload. No kernel, boot, EFI, or initramfs state was touched.

### Ncompress Clang IR PGO wave (2026-09-18)

`app-arch/ncompress-5.0-r2` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-ncompress.json` records one profraw payload, successful merge, and completion of the reviewed ncompress workload. No kernel, boot, EFI, or initramfs state was touched.

### Rpm2targz Clang IR PGO wave (2026-09-18)

`app-arch/rpm2targz-2021.03.16` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-rpm2targz.json` records one profraw payload, successful merge, and completion of the reviewed rpm2targz workload. No kernel, boot, EFI, or initramfs state was touched.

### Tar Clang IR PGO wave (2026-09-18)

`app-arch/tar-1.35-r1` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-tar.json` records one profraw payload, successful merge, and completion of the reviewed tar workload. No kernel, boot, EFI, or initramfs state was touched.

### Unzip Clang IR PGO wave (2026-09-19)

`app-arch/unzip-6.0_p31` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-unzip.json` records two profraw payloads, successful merge, and completion of the reviewed unzip workload. No kernel, boot, EFI, or initramfs state was touched.

### Xz-utils Clang IR wave quiescence stop (2026-09-19)

`app-arch/xz-utils-9999` completed its Clang IR build and staged install, but the profile-wave runner refused to seal the transaction because the profile payload directory did not quiesce after workload execution. No receipt was issued and the package merge was not accepted as a verified profile result. The attempt directory and payload evidence remain preserved for diagnosis; no framework or ABI guard was bypassed.

### Zip Clang IR PGO wave (2026-09-19)

`app-arch/zip-3.0_p16` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-zip.json` records the completed merge and all profraw payloads from the reviewed zip workload. No kernel, boot, EFI, or initramfs state was touched.

### Zstd Clang IR PGO wave (2026-09-19)

`app-arch/zstd-1.5.7-r1` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-zstd.json` records the completed merge, reviewed zstd workload, and all sealed profraw payloads. No kernel, boot, EFI, or initramfs state was touched.

### Argon2 workload correction and Clang IR wave (2026-09-19)

The initial `app-crypt/argon2-20190702-r1` wave exposed a workload-definition defect: invoking `argon2` without a salt and password fixture exited before exercising the binary. The workload generator now emits a deterministic minimum-cost Argon2id command with a root-owned password fixture under `/var/lib/gentoo-optimization/workloads/argon2/password`; the regenerated successor workload artifact passed readiness validation. The corrected rerun completed the Clang IR transaction and sealed one profraw payload in `profile-wave-receipt-argon2fix.json`.

### Successor framework republish after argon2 workload correction (2026-09-19)

The committed argon2 workload-generator correction and regenerated workload artifact were republished through the root-owned installer. The independent strict framework check passed against the exact successor policy and frozen inventory. An intermediate check command contained a generated-policy basename typo and failed before mutation; the corrected check passed.

### GCR Clang IR PGO wave with Mesa dependency rebuild (2026-09-19)

`app-crypt/gcr-4.4.0.1-r1` completed the exact successor `pgo-clang-ir` wave. Portage correctly rebuilt its required `dev-util/mesa_clc-9999` dependency in the same transaction; the sealed receipt is bound to the requested GCR CPV and records its profraw payloads, successful merge, and reviewed workload completion. The resolver's SPIR-V conflict warning remained fail-closed and did not authorize unrelated consumer upgrades.

### Libb2 workload-accounting refusal (2026-09-19)

`app-crypt/libb2-0.98.1-r3` was not recipe-ready in the regenerated workload manifest, so no representative workload was authorized. A manually constructed one-package probe therefore reached the build but was correctly refused at payload sealing because the profile directory did not quiesce without a workload. No verified profile receipt was issued. This CPV remains a workload-accounting exclusion/pending case and will not be counted as optimized until a valid representative workload is generated.

### Libmd workload-accounting refusal (2026-09-19)

`app-crypt/libmd-1.2.0` is classified `no-runnable-entrypoint` in the regenerated workload manifest. A manual profile-wave probe was therefore not authorized by the workload chain; it was run only to validate fail-closed behavior and was refused at payload sealing because no workload made the profile directory quiescent. No verified profile receipt was issued and the package is not counted as optimized.

### Pinentry Clang IR PGO wave (2026-09-19)

`app-crypt/pinentry-1.3.3` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-pinentry.json` records two profraw payloads, successful merge, and completion of the reviewed pinentry workload. No kernel, boot, EFI, or initramfs state was touched.

### GnuPG Clang IR PGO wave (2026-09-19)

`app-crypt/gnupg-2.5.22` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-gnupg.json` records the completed merge, reviewed GnuPG workload, and all profraw payloads. No kernel, boot, EFI, or initramfs state was touched.

### GPGME Clang IR PGO wave (2026-09-19)

`app-crypt/gpgme-2.2.0` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-gpgme.json` records three profraw payloads, successful merge, and completion of the reviewed GPGME workload. No kernel, boot, EFI, or initramfs state was touched.

### Rpm-sequoia Rust host-layout repair and workload boundary (2026-09-19)

The first `app-crypt/rpm-sequoia-1.10.2` Rust wave exposed the same target-layout mismatch previously seen with maturin: the ebuild's install phase expected `target/release/librpm_sequoia.so` while the explicit target setting placed artifacts below the target-specific directory. The runner now grants this CPV the authenticated `GENTOO_OPT_RUST_HOST_LAYOUT=1` exception (commit `33e4589`); the framework was republished and strict-checked. The corrected rerun reached and completed the expected host-layout build/install path, but this CPV is `no-runnable-entrypoint` in the workload manifest, so no profile receipt was issued and it is not counted as optimized.

### RHash Clang IR PGO wave (2026-09-19)

`app-crypt/rhash-1.4.6-r1` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-rhash.json` records two profraw payloads, successful merge, and completion of the reviewed RHash workload. No kernel, boot, EFI, or initramfs state was touched.

### Uchardet Clang IR PGO wave (2026-09-19)

`app-i18n/uchardet-0.0.8` completed the exact successor `pgo-clang-ir` profile wave. The sealed receipt `profile-wave-receipt-uchardet.json` records two profraw payloads, successful merge, and completion of the reviewed uchardet workload. No kernel, boot, EFI, or initramfs state was touched.

### jq Clang IR PGO wave (2026-09-19)

`app-misc/jq-1.8.2` completed the exact successor `pgo-clang-ir` profile wave. The root-owned sealed receipt `profile-wave-receipt-jq.json` records two nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed jq workload; independent receipt verification passed against the generation-bound wave and readiness artifacts. No kernel, boot, EFI, or initramfs state was touched.

### jq Clang IR PGO wave (2026-09-19)

`app-misc/jq-1.8.2` completed the exact successor `pgo-clang-ir` profile wave. The root-owned sealed receipt `profile-wave-receipt-jq.json` records two nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed jq workload; independent receipt verification passed against the generation-bound wave and readiness artifacts. No kernel, boot, EFI, or initramfs state was touched.

The jq receipt-bound raw payloads were independently merged with LLVM 22 into `/var/lib/gentoo-optimization/merged-profiles/app-misc_jq-1.8.2.profdata`; merge evidence is the adjacent root-owned `profile-merge-jq.json` (digest `ea1c9873f7cc7acf1cd034dd63fe5008d3184e2853905c3ace1cb1a33272e00f`). The profile remains pending dispatcher authorization and has not been deployed for profile-use.

### Evtest workload correction and Clang IR PGO wave (2026-09-19)

The first `app-misc/evtest-1.36` attempt correctly refused its generated `--help` workload because evtest does not implement that option and returned status 1. The authoritative workload generator now selects the supported non-destructive `--version` action; the framework was republished and its root-owned strict check passed. The corrected exact wave rebuilt and merged evtest, collected one nonempty profraw payload, passed independent receipt verification, and merged the payload with LLVM 22 into `/var/lib/gentoo-optimization/merged-profiles/app-misc_evtest-1.36.profdata`, with root-owned merge evidence in `profile-merge-evtest.json`. No BOLT deployment is claimed.

### Fastfetch Clang IR PGO wave (2026-09-19)

`app-misc/fastfetch-2.68.1-r1` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records one nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed fastfetch workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-misc_fastfetch-2.68.1-r1.profdata`, with root-owned evidence in `profile-merge-fastfetch.json`. No BOLT deployment is claimed.

### Cpuid2cpuflags Clang IR PGO wave (2026-09-19)

`app-portage/cpuid2cpuflags-18` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records one nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-portage_cpuid2cpuflags-18.profdata`, with root-owned evidence in `profile-merge-cpuid2cpuflags.json`. No BOLT deployment is claimed.

### Quoter Clang IR PGO wave (2026-09-19)

`app-shells/quoter-4.2` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records one nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-shells_quoter-4.2.profdata`, with root-owned evidence in `profile-merge-quoter.json`. No BOLT deployment is claimed.

### Dos2unix Clang IR PGO wave (2026-09-19)

`app-text/dos2unix-7.5.6` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records two nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed workload; independent receipt verification passed. LLVM 22 merged the payloads into `/var/lib/gentoo-optimization/merged-profiles/app-text_dos2unix-7.5.6.profdata`, with root-owned evidence in `profile-merge-dos2unix.json`. No BOLT deployment is claimed.

### Lowdown Clang IR PGO wave (2026-09-19)

`app-text/lowdown-3.1.1` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records four nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed lowdown workload; independent receipt verification passed. LLVM 22 merged the payloads into `/var/lib/gentoo-optimization/merged-profiles/app-text_lowdown-3.1.1.profdata`, with root-owned evidence in `profile-merge-lowdown.json`. No BOLT deployment is claimed.

### Scdoc workload correction and Clang IR PGO wave (2026-09-19)

The first `app-text/scdoc-9999` attempt correctly refused the generic `--help` workload because scdoc is a stdin filter and exits nonzero without input. The authoritative workload generator now supplies a deterministic root-owned minimal manpage fixture at `/var/lib/gentoo-optimization/workloads/scdoc/fixture.scd`; the framework was republished and its strict check passed. The corrected exact wave rebuilt and merged scdoc, collected one nonempty profraw payload, passed independent receipt verification, and merged with LLVM 22 into `/var/lib/gentoo-optimization/merged-profiles/app-text_scdoc-9999.profdata`, with root-owned evidence in `profile-merge-scdoc.json`. No BOLT deployment is claimed.

### Libpaper workload correction and Clang IR PGO wave (2026-09-19)

The first `app-text/libpaper-2.1.3` attempt correctly refused the generic `paperconf --help` workload because paperconf uses single-letter query actions. The authoritative workload generator now uses the successful documented `paperconf -h` query; the framework was republished and its strict check passed. The corrected exact wave rebuilt and merged libpaper, collected four nonempty profraw payloads, passed independent receipt verification, and merged with LLVM 22 into `/var/lib/gentoo-optimization/merged-profiles/app-text_libpaper-2.1.3.profdata`, with root-owned evidence in `profile-merge-libpaper.json`. No BOLT deployment is claimed.

### Enchant workload correction and Clang IR PGO wave (2026-09-19)

The first `app-text/enchant-2.8.16` attempt correctly refused the generic `enchant-lsmod-2 --help` workload because that utility uses single-dash options. The authoritative workload generator now uses its successful documented `-help` action; the framework was republished and its strict check passed. The corrected exact wave rebuilt and merged enchant, collected four nonempty profraw payloads, passed independent receipt verification, and merged with LLVM 22 into `/var/lib/gentoo-optimization/merged-profiles/app-text_enchant-2.8.16.profdata`, with root-owned evidence in `profile-merge-enchant.json`. No BOLT deployment is claimed.

### Gspell workload-accounting exclusion (2026-09-19)

`app-text/gspell-1.14.4` rebuilt and merged in the exact successor generation, but its `gspell-app1` workload cannot terminate meaningfully on this live installation: Enchant reports no available language dictionaries and the stdin consumer remains unusable for deterministic profile collection. The failed attempt and profraw evidence remain preserved. The authoritative workload generator now records this CPV as `no-profile-producing-workload` with reason `gspell-app1 has no configured language dictionaries on the live system`; the framework was republished and its strict check passed. No profile receipt or BOLT deployment is claimed.

### Xmlto Clang IR PGO wave (2026-09-19)

`app-text/xmlto-0.0.28-r11` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records one nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed xmlto workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-text_xmlto-0.0.28-r11.profdata`, with root-owned evidence in `profile-merge-xmlto.json`. No BOLT deployment is claimed.

### Yodl Clang IR generation failure (2026-09-19)

The exact `app-text/yodl-4.05.00` generation attempt was refused in the ebuild `src_prepare` phase before compilation. Gentoo's `INSTALL.im` rewrite uses a slash-delimited `sed` replacement with `tc-getCC`; the immutable active Clang identity is the absolute `/usr/lib/llvm/22/bin/clang-22`, so the replacement fails with `sed: unknown option to 's'`. The runner preserved the failed attempt journal and Portage build log; no package merge or profile receipt was admitted, and no ABI guard was bypassed. This CPV remains a package-specific build-path failure requiring an ebuild-side delimiter-safe repair before profile collection can proceed.

The local delimiter-safe yodl ebuild repair was staged in the root-owned local overlay and the original prepare-phase error was cleared. A fresh exact retry then reached compilation, but yodl's C++ link failed with widespread unresolved `std::__1`/libc++ symbols under the active Clang lane. No replacement merged and no profile receipt was admitted; the repaired ebuild and both failed attempt logs remain preserved. Yodl remains a package-specific build incompatibility pending a toolchain/link-mode repair rather than an optimization-policy bypass.

### B2 Clang IR PGO wave (2026-09-19)

`dev-build/b2-5.5.3` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records one nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed b2 workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-build_b2-5.5.3.profdata`, with root-owned evidence in `profile-merge-b2.json`. No BOLT deployment is claimed.

### Bmake workload correction and Clang IR PGO wave (2026-09-19)

The first `dev-build/bmake-20260508` attempt correctly refused the generic `--help` workload because bmake treats help flags as usage errors. The authoritative workload generator now uses the successful built-in query `bmake -V MAKE_VERSION`; the framework was republished and its strict check passed. The corrected exact wave rebuilt and merged bmake, collected one nonempty profraw payload, passed independent receipt verification, and merged with LLVM 22 into `/var/lib/gentoo-optimization/merged-profiles/dev-build_bmake-20260508.profdata`, with root-owned evidence in `profile-merge-bmake.json`. No BOLT deployment is claimed.

### Icmake Clang IR PGO wave (2026-09-19)

`dev-build/icmake-9.03.01-r1` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records two nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed icmake workload; independent receipt verification passed. LLVM 22 merged the payloads into `/var/lib/gentoo-optimization/merged-profiles/dev-build_icmake-9.03.01-r1.profdata`, with root-owned evidence in `profile-merge-icmake.json`. No BOLT deployment is claimed.

### Make Clang IR wave policy-conflict stop (2026-09-19)

The exact `dev-build/make-9999` wave was refused during setup before compilation. The live `/etc/portage/package.env/00-toolchain` contains a pre-existing `dev-build/make gcc.conf` assignment while the generation-bound Phase-3 policy assigns `=dev-build/make-9999` to `pgo-clang-ir-generate`; the resulting environment resolved GCC while the requested mode was Clang, and the fail-closed dispatcher rejected the compiler-family mismatch. No package mutation or profile receipt was admitted. The conflict and generated policy remain preserved for a later authoritative package-env precedence repair; the compiler-family guard was not bypassed.

### Make Clang IR PGO wave completed after local-source compatibility repair (2026-09-19)

The exact `dev-build/make-9999` wave initially stopped on a live package-env precedence conflict, then exposed two source-level GCC assumptions under the required Clang lane: GNU Make's maintainer flags included `-C`, followed by the GCC-only warning options `-Wlogical-op` and `-Wduplicated-cond`. The local Portage overlay ebuild was repaired narrowly after autoreconf to remove only those incompatible flags. The eBuild also used the already cached, exact make and gnulib source revisions after an upstream gnulib fetch stalled; no moving source revision or compiler-family bypass was used. The fresh generation-bound `make-v8` wave completed under `pgo-clang-ir-generate`, merged `dev-build/make-9999`, produced one nonempty profraw payload, and passed independent receipt verification. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-build_make-9999.profdata`; root-owned merge evidence is `profile-merge-make.json`. The profile remains pending dispatcher/profile-use authorization and no BOLT deployment is claimed. Failed attempts `make-v1` through `make-v7` remain preserved additively, including the fetch and local-repository metadata failures.

### Cpio Clang IR PGO wave (2026-09-19)

`app-arch/cpio-2.15` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records one nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `gcpio --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-arch_cpio-2.15.profdata`, with root-owned merge evidence in `profile-merge-cpio.json`. No profile-use deployment or BOLT output is claimed.

### Gzip Clang IR PGO wave (2026-09-19)

`app-arch/gzip-1.14_p20260901` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records one nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `gzip-reference --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-arch_gzip-1.14_p20260901.profdata`, with root-owned merge evidence in `profile-merge-gzip.json`. No profile-use deployment or BOLT output is claimed.

### Argon2 Clang IR PGO wave (2026-09-19)

`app-crypt/argon2-20190702-r1` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records one nonempty profraw payload, successful install-QA ABI guarding, and completion of the input-bound Argon2 password-hash workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-crypt_argon2-20190702-r1.profdata`, with root-owned merge evidence in `profile-merge-argon2.json`. Existing unrelated LLVM optimization-record YAML files caused nonfatal `ldconfig` warnings during the package transaction; no project files were removed or altered for that warning. No profile-use deployment or BOLT output is claimed.

### RHash Clang IR PGO wave (2026-09-19)

`app-crypt/rhash-1.4.6-r1` completed the exact successor `pgo-clang-ir` wave, including both ABI variants built by the package. The sealed receipt records the nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `rhash --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-crypt_rhash-1.4.6-r1.profdata`, with root-owned merge evidence in `profile-merge-rhash.json`. The same pre-existing non-ELF LLVM optimization-record YAML warnings appeared during `ldconfig`; no project files were removed or altered. No profile-use deployment or BOLT output is claimed.

### Pinentry Clang IR PGO wave (2026-09-19)

`app-crypt/pinentry-1.3.3` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records the nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `pinentry-curses --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-crypt_pinentry-1.3.3.profdata`, with root-owned merge evidence in `profile-merge-pinentry.json`. No profile-use deployment or BOLT output is claimed.

### Expat Clang IR PGO wave (2026-09-19)

`dev-libs/expat-2.8.4` completed the exact successor `pgo-clang-ir` wave across its configured multilib builds. The sealed receipt records nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed `xmlwf --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_expat-2.8.4.profdata`, with root-owned merge evidence in `profile-merge-expat.json`. The transaction again reported pre-existing non-ELF LLVM optimization-record YAML warnings during `ldconfig`; no project files were altered for that unrelated warning. No profile-use deployment or BOLT output is claimed.

### Fribidi Clang IR PGO wave (2026-09-19)

`dev-libs/fribidi-1.0.16` completed the exact successor `pgo-clang-ir` wave across its configured multilib builds. The sealed receipt records nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed `fribidi --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_fribidi-1.0.16.profdata`, with root-owned merge evidence in `profile-merge-fribidi.json`. The known unrelated non-ELF optimization-record YAML warnings recurred during `ldconfig`; no project files were altered. No profile-use deployment or BOLT output is claimed.

### JSON-GLib Clang IR PGO wave (2026-09-19)

`dev-libs/json-glib-1.10.8` completed the exact successor `pgo-clang-ir` wave across its configured multilib builds. The sealed receipt records nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed `json-glib-format --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_json-glib-1.10.8.profdata`, with root-owned merge evidence in `profile-merge-json-glib.json`. The known unrelated non-ELF optimization-record YAML warnings recurred during `ldconfig`; no project files were altered. No profile-use deployment or BOLT output is claimed.

### PCRE Clang IR PGO wave (2026-09-19)

`dev-libs/libpcre-8.45-r4` completed the exact successor `pgo-clang-ir` wave across its configured multilib builds. The sealed receipt records nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed `pcregrep --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libpcre-8.45-r4.profdata`, with root-owned merge evidence in `profile-merge-libpcre.json`. The known unrelated non-ELF optimization-record YAML warnings recurred during `ldconfig`; no project files were altered. No profile-use deployment or BOLT output is claimed.

### Libgpg-error Clang IR PGO wave (2026-09-19)

`dev-libs/libgpg-error-1.61` completed the exact successor `pgo-clang-ir` wave across its configured multilib builds. The sealed receipt records nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed `gpg-error --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libgpg-error-1.61.profdata`, with root-owned merge evidence in `profile-merge-libgpg-error.json`. The known unrelated non-ELF optimization-record YAML warnings recurred during `ldconfig`; no project files were altered. No profile-use deployment or BOLT output is claimed.

### XXHash Clang IR PGO wave (2026-09-19)

`dev-libs/xxhash-0.8.3-r2` completed the exact successor `pgo-clang-ir` wave across its configured multilib builds. The sealed receipt records nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed `xxhsum --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_xxhash-0.8.3-r2.profdata`, with root-owned merge evidence in `profile-merge-xxhash.json`. The known unrelated non-ELF optimization-record YAML warnings recurred during `ldconfig`; no project files were altered. No profile-use deployment or BOLT output is claimed.

### Libtasn1 Clang IR PGO wave (2026-09-19)

`dev-libs/libtasn1-4.21.0` completed the exact successor `pgo-clang-ir` wave across its configured multilib builds. The sealed receipt records nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed `asn1Coding --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libtasn1-4.21.0.profdata`, with root-owned merge evidence in `profile-merge-libtasn1.json`. The known unrelated non-ELF optimization-record YAML warnings recurred during `ldconfig`; no project files were altered. No profile-use deployment or BOLT output is claimed.

### Libtracefs workload-accounting exclusion (2026-09-19)

`dev-libs/libtracefs-1.8.3` rebuilt and merged under the exact successor Clang IR lane, but its reviewed `/usr/bin/sqlhist --help` workload exited 255. The utility requires live tracefs control access and cannot run as a deterministic userspace workload in this boundary; the direct probe confirmed the same exit and usage output. The failed receipt/attempt and Portage evidence remain preserved. `build-workload-recipes.py` now emits an explicit `no-profile-producing-workload` state with reason `sqlhist requires live tracefs control access and exits 255 without it`; regenerated workload accounting contains 293 recipe-ready records, 242 no-runnable-entrypoint records, and 8 no-profile-producing exclusions. No profile receipt or profile merge was admitted for libtracefs.

### Nettle workload correction and Clang IR PGO wave (2026-09-19)

The first exact `dev-libs/nettle-3.10.2` generation attempt rebuilt and merged, but the generated workload selected `nettle-lfib-stream --help`, which exited 1. The authoritative workload generator now excludes that entrypoint and selects `nettle-hash -a sha256 /etc/hostname`, a successful read-only deterministic hash workload. The fresh `nettle-v2` wave completed across the configured multilib builds, passed install-QA ABI guarding and independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_nettle-3.10.2.profdata`; root-owned merge evidence is `profile-merge-nettle.json`. The known unrelated non-ELF optimization-record YAML warnings recurred during `ldconfig`. No profile-use deployment or BOLT output is claimed.

### Snowball workload correction and Clang IR PGO wave (2026-09-19)

The first exact `dev-libs/snowball-stemmer-3.1.1` generation attempt rebuilt and merged, but the generated workload selected `stemwords --help`, which exits 1 because the utility requires a language and input stream. The authoritative workload generator now selects the deterministic read-only probe `stemwords -l english -i /etc/hostname`. The fresh `snowball-v2` wave completed under the generation-bound Clang IR lane, passed install-QA ABI guarding and independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_snowball-stemmer-3.1.1.profdata`; root-owned merge evidence is `profile-merge-snowball-v2.json`. No profile-use deployment or BOLT output is claimed.

### Libgcrypt instrumentation stop and Hunspell workload correction (2026-09-19)

The exact `dev-libs/libgcrypt-1.12.4` wave reached the multilib compile phase but was refused before merge because its libtool command transformation stripped `-fprofile-instr-generate=` while leaving the `%m-%p.profraw` operand as a compiler input. No profile receipt was admitted; the failed attempt and build log remain preserved for a package-specific libtool/profile-flag repair.

The first exact `app-text/hunspell-1.7.2-r1` wave merged successfully but its generated helper probes were not valid standalone workloads: the helper programs rejected `--help`, and `hunzip --help` returned success with empty output. The authoritative generator now retains the successful `hunspell` and `hunzip` probes, excludes the helper-only entrypoints, and explicitly allows empty output for `hunzip`. The fresh `hunspell-v3` wave completed, passed independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-text_hunspell-1.7.2-r1.profdata`; root-owned merge evidence is `profile-merge-hunspell-v3.json`. No profile-use deployment or BOLT output is claimed.

### Mandoc workload correction and Clang IR PGO wave (2026-09-19)

The first exact `app-text/mandoc-1.14.6-r1` generation attempt rebuilt and merged, but the generated `apropos --help` workload returned mandoc's database error status 5. Direct probes showed that only the installed `mandoc` parser has a valid standalone invocation (`mandoc -h`); the other wrappers require a generated man database or input. The authoritative workload generator now records `mandoc -h` with empty output permitted and excludes the database-dependent wrappers. The fresh `mandoc-v3` wave completed, passed independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/app-text_mandoc-1.14.6-r1.profdata`; root-owned merge evidence is `profile-merge-mandoc-v3.json`. No profile-use deployment or BOLT output is claimed.

### Patchelf Clang IR PGO wave (2026-09-19)

`dev-util/patchelf-0.19.1` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `patchelf --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_patchelf-0.19.1.profdata`, with root-owned merge evidence in `profile-merge-patchelf.json`. No profile-use deployment or BOLT output is claimed.

### LLVM profile-path hook repair and libgcrypt Clang IR PGO wave (2026-09-19)

The first `dev-libs/libgcrypt-1.12.4` attempt exposed a libtool interaction in the Clang instrumentation hook: libtool removed the `-fprofile-instr-generate=<path>` option while leaving its `%m-%p.profraw` operand as a compiler input. The hook now injects the operand-free `-fprofile-instr-generate` and exports the same generation-bound `%m-%p.profraw` template through `LLVM_PROFILE_FILE`; the focused dispatcher suite remains green at 45/45, and the root-owned framework was republished and passed the strict installer check. A fresh `libgcrypt-v2` wave then completed across both configured ABIs, passed install-QA ABI guarding and independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libgcrypt-1.12.4.profdata`; root-owned merge evidence is `profile-merge-libgcrypt-v2.json`. No profile-use deployment or BOLT output is claimed.

### Unifdef workload correction and Clang IR PGO wave (2026-09-19)

The first exact `dev-util/unifdef-2.12-r2` generation attempt rebuilt and merged, but the generic `unifdef --help` workload returned status 2. Direct probing confirmed the supported standalone query is `unifdef -h`; the authoritative workload generator now uses that form. The fresh `unifdef-v2` wave completed, passed independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_unifdef-2.12-r2.profdata`; root-owned merge evidence is `profile-merge-unifdef-v2.json`. No profile-use deployment or BOLT output is claimed.

### XXD workload correction and Clang IR PGO wave (2026-09-19)

The first exact `dev-util/xxd-2025.08.24-r1` generation attempt rebuilt and merged, but the generic `xxd --help` workload returned status 1. Direct probing confirmed `xxd -version` is the successful non-destructive query. The authoritative workload generator now uses that form. The fresh `xxd-v2` wave completed, passed independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_xxd-2025.08.24-r1.profdata`; root-owned merge evidence is `profile-merge-xxd-v2.json`. No profile-use deployment or BOLT output is claimed.

### Which Clang IR PGO wave (2026-09-19)

`sys-apps/which-2.23` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `which --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_which-2.23.profdata`, with root-owned merge evidence in `profile-merge-which.json`. No profile-use deployment or BOLT output is claimed.

### Diffutils Clang IR PGO wave (2026-09-19)

`sys-apps/diffutils-3.12` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `cmp --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_diffutils-3.12.profdata`, with root-owned merge evidence in `profile-merge-diffutils.json`. No profile-use deployment or BOLT output is claimed.

### Grep Clang IR PGO wave (2026-09-19)

`sys-apps/grep-3.12` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `grep --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_grep-3.12.profdata`, with root-owned merge evidence in `profile-merge-grep.json`. No profile-use deployment or BOLT output is claimed.

### Sed Clang IR PGO wave (2026-09-19)

`sys-apps/sed-4.10-r1` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `gsed --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_sed-4.10-r1.profdata`, with root-owned merge evidence in `profile-merge-sed.json`. No profile-use deployment or BOLT output is claimed.

### Gentoo-functions workload exclusion (2026-09-19)

`sys-apps/gentoo-functions-9999` rebuilt and merged under the exact Clang IR lane, but its only owned executable, `consoletype`, requires an interactive terminal and rejects all deterministic standalone help/version probes. The authoritative workload generator now records this as `no-profile-producing-workload` with the explicit reason `consoletype requires an interactive terminal and has no deterministic standalone invocation`; the failed wave attempt and Portage evidence remain preserved, and no profile receipt was admitted.

### Gawk Clang IR PGO wave (2026-09-19)

`sys-apps/gawk-5.4.1a` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `gawk --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_gawk-5.4.1a.profdata`, with root-owned merge evidence in `profile-merge-gawk.json`. No profile-use deployment or BOLT output is claimed.

### Install-xattr Clang IR PGO wave (2026-09-19)

`sys-apps/install-xattr-9999` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `install-xattr --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_install-xattr-9999.profdata`, with root-owned merge evidence in `profile-merge-install-xattr.json`. No profile-use deployment or BOLT output is claimed.

### Debianutils Clang IR PGO wave (2026-09-19)

`sys-apps/debianutils-5.23.2` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `run-parts --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_debianutils-5.23.2.profdata`, with root-owned merge evidence in `profile-merge-debianutils.json`. No profile-use deployment or BOLT output is claimed.

### ACL workload correction and Clang IR PGO wave (2026-09-19)

The first exact `sys-apps/acl-9999` generation attempt rebuilt and merged, but the generic `chacl --help` workload returned status 1. Direct probing established the supported read-only ACL query `chacl -l /etc/hostname`; the authoritative workload generator now uses that command. The fresh `acl-v2` wave completed across both configured ABIs, passed independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_acl-9999.profdata`; root-owned merge evidence is `profile-merge-acl-v2.json`. No profile-use deployment or BOLT output is claimed.

### Attr workload correction and Clang IR PGO wave (2026-09-19)

The first exact `sys-apps/attr-9999` generation attempt rebuilt and merged, but the generic `attr --help` workload returned status 1. Direct probing established the successful read-only extended-attribute listing `attr -l /etc/hostname`; the authoritative workload generator now uses that command and permits empty output. The fresh `attr-v2` wave completed across both configured ABIs, passed independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_attr-9999.profdata`; root-owned merge evidence is `profile-merge-attr-v2.json`. No profile-use deployment or BOLT output is claimed.

### Less source-fetch stop (2026-09-19)

The exact `sys-apps/less-9999` wave was stopped in the unpack phase after the upstream `git fetch https://github.com/gwsw/less` remained silent for more than three minutes with zero CPU. The fetch subprocesses were terminated; Portage preserved the failed attempt and unpack log, and no package merge or profile receipt was admitted. This is a source-acquisition failure for the moving `9999` ebuild, not an optimization-policy bypass; a later retry requires a usable cached or reachable source revision.

### Dmidecode Clang IR PGO wave (2026-09-19)

`sys-apps/dmidecode-3.7` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `biosdecode --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_dmidecode-3.7.profdata`, with root-owned merge evidence in `profile-merge-dmidecode.json`. No profile-use deployment or BOLT output is claimed.

### OpenSP Clang IR correctness stop (2026-09-19)

The exact `app-text/opensp-1.5.2-r10` wave reached compilation under the required Clang IR lane but failed during the `onsgmls` C++ link with unresolved libc++/C++ ABI symbols under the active `--no-allow-shlib-undefined` policy. No package merge or profile receipt was admitted; the full Portage build log and failed wave attempt remain preserved. OpenSP remains a package-specific correctness failure requiring an ebuild/toolchain link-mode repair before profile collection can proceed.

### Desktop-file-utils Clang IR PGO wave (2026-09-19)

`dev-util/desktop-file-utils-0.28-r1` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `desktop-file-install --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_desktop-file-utils-0.28-r1.profdata`, with root-owned merge evidence in `profile-merge-desktop-file-utils.json`. No profile-use deployment or BOLT output is claimed.

### Gperf Clang IR PGO wave (2026-09-19)

`dev-util/gperf-3.3` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `gperf --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_gperf-3.3.profdata`, with root-owned merge evidence in `profile-merge-gperf.json`. No profile-use deployment or BOLT output is claimed.

### Ragel Clang IR PGO wave (2026-09-19)

`dev-util/ragel-7.0.4-r3` completed the exact successor `pgo-clang-ir` wave across its configured C++ build outputs. The sealed receipt records nonempty profraw payloads, successful install-QA ABI guarding, and completion of the reviewed `ragel --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_ragel-7.0.4-r3.profdata`, with root-owned merge evidence in `profile-merge-ragel.json`. No profile-use deployment or BOLT output is claimed.

### Colm exported-ABI stop (2026-09-19)

The exact `dev-util/colm-0.14.7-r4` wave reached install-QA but was correctly rejected by the unchanged exported-ABI guard. The staged `libfsm-0.14.7.so`/`libfsm.so` providers lost ten installed C++ symbol identities, including `DList`, `BstSet`, `Vector`, `SVector`, `AvlBasic`, and `std::__cxx11::basic_stringbuf` symbols. No package merge or profile receipt was admitted; the failed attempt and complete Portage log remain preserved for a package-specific ABI remediation.

### Debugedit Clang IR PGO wave (2026-09-19)

`dev-util/debugedit-5.3` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `debugedit --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_debugedit-5.3.profdata`, with root-owned merge evidence in `profile-merge-debugedit.json`. No profile-use deployment or BOLT output is claimed.

### Ccache Clang IR PGO wave (2026-09-19)

`dev-util/ccache-4.14` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records nonempty profraw payloads across the C++ build, successful install-QA ABI guarding, and completion of the reviewed `ccache --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_ccache-4.14.profdata`, with root-owned merge evidence in `profile-merge-ccache.json`. No profile-use deployment or BOLT output is claimed.

### Wayland-scanner Clang IR PGO wave (2026-09-19)

`dev-util/wayland-scanner-9999` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `wayland-scanner --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_wayland-scanner-9999.profdata`, with root-owned merge evidence in `profile-merge-wayland-scanner.json`. No profile-use deployment or BOLT output is claimed.

### GTK icon cache Clang IR PGO wave (2026-09-19)

`dev-util/gtk-update-icon-cache-3.24.42` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `gtk-update-icon-cache --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_gtk-update-icon-cache-3.24.42.profdata`, with root-owned merge evidence in `profile-merge-gtk-update-icon-cache.json`. No profile-use deployment or BOLT output is claimed.

### Breakpad workload correction and Clang IR PGO wave (2026-09-19)

The first exact `dev-util/breakpad-2024.02.16` generation attempt rebuilt and merged, but its generated `microdump_stackwalk --help` workload returned status 1. Direct probing confirmed the installed Breakpad tools accept `-h` as a successful empty-output query; the authoritative workload generator now uses `-h` for all three stackwalk/dump tools and permits empty output. The fresh `breakpad-v2` wave completed, passed independent receipt verification, and LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/dev-util_breakpad-2024.02.16.profdata`; root-owned merge evidence is `profile-merge-breakpad-v2.json`. No profile-use deployment or BOLT output is claimed.

### Pkgconf source-fetch stop (2026-09-19)

The exact `dev-util/pkgconf-9999` wave was stopped in the unpack phase after the moving upstream git fetch remained silent with zero CPU. The fetch subprocesses were terminated; Portage preserved the failed unpack log and wave attempt, and no package merge or profile receipt was admitted. This is a source-acquisition failure for the moving `9999` ebuild and does not authorize bypassing the source identity contract.

### Iucode-tool Clang IR PGO wave (2026-09-19)

`sys-apps/iucode_tool-2.3.1-r2` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `iucode_tool --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_iucode_tool-2.3.1-r2.profdata`, with root-owned merge evidence in `profile-merge-iucode-tool.json`. No firmware, boot, or kernel artifact was touched; no profile-use deployment or BOLT output is claimed.

### Ethtool Clang IR PGO wave (2026-09-19)

`sys-apps/ethtool-7.0` completed the exact successor `pgo-clang-ir` wave. The sealed receipt records a nonempty profraw payload, successful install-QA ABI guarding, and completion of the reviewed `ethtool --help` workload; independent receipt verification passed. LLVM 22 merged the payload into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_ethtool-7.0.profdata`, with root-owned merge evidence in `profile-merge-ethtool.json`. The workload performed no hardware mutation; no profile-use deployment or BOLT output is claimed.

### LM sensors workload correction and Clang IR PGO wave (2026-09-19)

The first exact `sys-apps/lm-sensors-3.6.2` generation attempt correctly
refused the generated `isadump --help` recipe because that utility returns
failure and is not a deterministic non-mutating standalone workload. The
authoritative workload generator now retains only `/usr/bin/sensors --help`
for this package, excluding the ISA probing tools. The fresh `lm-sensors-v2`
wave completed, passed independent receipt verification, and LLVM 22 merged
the nonempty payload into
`/var/lib/gentoo-optimization/merged-profiles/sys-apps_lm-sensors-3.6.2.profdata`;
root-owned merge evidence is `profile-merge-lm-sensors-v2.json`. No hardware
mutation, profile-use deployment, or BOLT output is claimed.

### Bzip2 Clang IR PGO wave (2026-09-19)

`app-arch/bzip2-1.0.8-r5` completed the exact successor `pgo-clang-ir`
wave with the reviewed `/bin/bzip2-reference --help` workload. The sealed
receipt passed independent verification and LLVM 22 merged the two nonempty
profile payloads into
`/var/lib/gentoo-optimization/merged-profiles/app-arch_bzip2-1.0.8-r5.profdata`;
root-owned merge evidence is `profile-merge-bzip2.json`. Older unreceipted raw
payloads from prior attempts were moved into the generation's preserved
failure evidence directory before merge. No profile-use deployment or BOLT
output is claimed.

### Cabextract Clang IR PGO wave (2026-09-19)

`app-arch/cabextract-9999` completed the exact successor `pgo-clang-ir`
wave. The sealed receipt records a nonempty profraw payload, successful
install-QA ABI guarding, and completion of the reviewed `cabextract --version`
workload; independent receipt verification passed. LLVM 22 merged the payload
into `/var/lib/gentoo-optimization/merged-profiles/app-arch_cabextract-9999.profdata`,
with root-owned merge evidence in `profile-merge-cabextract.json`. No
profile-use deployment or BOLT output is claimed.

### GCR Clang IR PGO wave (2026-09-19)

`app-crypt/gcr-4.4.0.1-r1` completed the exact successor `pgo-clang-ir`
wave. The sealed receipt records nonempty profraw payloads, successful
install-QA ABI guarding, and completion of the reviewed GCR workload;
independent receipt verification passed. LLVM 22 merged the payload into
`/var/lib/gentoo-optimization/merged-profiles/app-crypt_gcr-4.4.0.1-r1.profdata`,
with root-owned merge evidence in `profile-merge-gcr.json`. The transaction
remained in userspace; no profile-use deployment or BOLT output is claimed.

### GnuPG Clang IR PGO wave (2026-09-19)

`app-crypt/gnupg-2.5.22` completed the exact successor `pgo-clang-ir`
wave. The sealed receipt records nonempty profraw payloads, successful
install-QA ABI guarding, and completion of the reviewed GnuPG workload;
independent receipt verification passed. LLVM 22 merged the payload into
`/var/lib/gentoo-optimization/merged-profiles/app-crypt_gnupg-2.5.22.profdata`,
with root-owned merge evidence in `profile-merge-gnupg.json`. No profile-use
deployment or BOLT output is claimed.

### GPGME Clang IR PGO wave (2026-09-19)

`app-crypt/gpgme-2.2.0` completed the exact successor `pgo-clang-ir`
wave. The sealed receipt records nonempty profraw payloads, successful
install-QA ABI guarding, and completion of the reviewed GPGME workload;
independent receipt verification passed. LLVM 22 merged the payload into
`/var/lib/gentoo-optimization/merged-profiles/app-crypt_gpgme-2.2.0.profdata`,
with root-owned merge evidence in `profile-merge-gpgme.json`. No profile-use
deployment or BOLT output is claimed.

### Uchardet Clang IR PGO wave (2026-09-19)

`app-i18n/uchardet-0.0.8` completed the exact successor `pgo-clang-ir`
wave. The sealed receipt records nonempty profraw payloads, successful
install-QA ABI guarding, and completion of the reviewed uchardet workload;
independent receipt verification passed. LLVM 22 merged the payload into
`/var/lib/gentoo-optimization/merged-profiles/app-i18n_uchardet-0.0.8.profdata`,
with root-owned merge evidence in `profile-merge-uchardet.json`. No
profile-use deployment or BOLT output is claimed.

### DDCutil Clang IR PGO wave (2026-09-19)

`app-misc/ddcutil-2.2.6` completed the exact successor `pgo-clang-ir`
wave. Its reviewed `ddcutil --help` workload is non-mutating; the sealed
receipt records nonempty profraw payloads, successful install-QA ABI guarding,
and completed workload execution, and independent receipt verification passed.
LLVM 22 merged the payload into
`/var/lib/gentoo-optimization/merged-profiles/app-misc_ddcutil-2.2.6.profdata`,
with root-owned merge evidence in `profile-merge-ddcutil.json`. No hardware
operation, profile-use deployment, or BOLT output is claimed.

### Eix Clang IR PGO wave (2026-09-19)

`app-portage/eix-0.36.9` completed the exact successor `pgo-clang-ir`
wave. The sealed receipt records nonempty profraw payloads, successful
install-QA ABI guarding, and completion of the reviewed `eix --help`
workload; independent receipt verification passed. LLVM 22 merged the payload
into `/var/lib/gentoo-optimization/merged-profiles/app-portage_eix-0.36.9.profdata`,
with root-owned merge evidence in `profile-merge-eix.json`. No profile-use
deployment or BOLT output is claimed.

### Portage-utils Clang IR PGO wave (2026-09-19)

`app-portage/portage-utils-9999` completed the exact successor
`pgo-clang-ir` wave. The moving-source checkout resolved and built
successfully; the sealed receipt records nonempty profraw payloads, successful
install-QA ABI guarding, and completion of the reviewed `q --help` workload;
independent receipt verification passed. LLVM 22 merged the payload into
`/var/lib/gentoo-optimization/merged-profiles/app-portage_portage-utils-9999.profdata`,
with root-owned merge evidence in `profile-merge-portage-utils.json`. No
profile-use deployment or BOLT output is claimed.

### Bash Clang IR wave stop (2026-09-19)

The exact `app-shells/bash-9999` wave was stopped during compilation. The
Gentoo ebuild's `pgo` USE implementation unconditionally appends GCC-style
`-fprofile-generate=${T}/pgo` flags, while the selected Clang IR dispatcher
already supplies `-fprofile-instr-generate`; Clang rejects the mixed pair before
producing an image. No package merge or profile receipt was admitted. The
complete build log and failed wave attempt are preserved. This is a
package-specific native-PGO integration defect requiring remediation before a
Bash profile wave can be retried; the framework and ABI guard were not
bypassed.

### Dash workload correction and Clang IR PGO wave (2026-09-19)

The first exact `app-shells/dash-9999` attempt correctly refused the generic
`dash --help` recipe because dash returns status 2 for that option. The
workload generator now uses the deterministic non-mutating
`/bin/dash -c 'printf dash-workload'` recipe. The fresh `dash-v2` wave
completed, passed independent receipt verification, and LLVM 22 merged the
payload into `/var/lib/gentoo-optimization/merged-profiles/app-shells_dash-9999.profdata`,
with root-owned merge evidence in `profile-merge-dash-v2.json`. No
profile-use deployment or BOLT output is claimed.

### Bash native-PGO conflict remediation and fetch stop (2026-09-19)

The Bash lane runner now passes `USE=-pgo` for the Clang IR wave so the
Gentoo ebuild cannot append its GCC-only native PGO flags. It also passes
`LLVM_PROFILE_FILE=/dev/null` explicitly through `doas env`; setting it only in
the parent environment was insufficient because the privileged helper scrubs
that variable, causing Portage success hooks to attempt `default.profraw` in
the repository. The first retry reached the corrected no-`pgo` build path but
its moving Bash source fetch then remained silent with zero CPU; the fetch and
transaction were terminated, with no merge or profile receipt admitted. The
attempt remains preserved for a later retry after source acquisition is
available.

### Zsh profile-wave stop (2026-09-19)

The exact `app-shells/zsh-9999` wave compiled and staged, but install-time
Portage helper shells inherited the instrumented system Bash without a usable
`LLVM_PROFILE_FILE` and attempted to write `default.profraw` in the repository.
The transaction was rejected by sandbox policy and no profile receipt or merge
was admitted. This is the same privileged-helper profile-output propagation
boundary addressed in the Bash runner hardening; the failed attempt and full
sandbox evidence remain preserved for a fresh retry after that propagation is
verified across the Portage helper path.

### Portage helper profile-output hardening (2026-09-19)

The shell-wave failures exposed that the dispatcher re-exported the raw LLVM
profile destination while Portage entered install and package-QA phases. The
bashrc now resets `LLVM_PROFILE_FILE=/dev/null` for `pkg_*`, install, and
package-hook phases; the profile-wave runner continues to assign the raw
workload destination only after the transaction. This preserves build/test
profile collection while preventing administrative helper shells from creating
unreceipted `default.profraw` files. Bash syntax validation passed; a fresh
shell-package retry is required before this repair is considered live-verified.

### Phase-3 coverage verifier schema compatibility (2026-09-19)

The live generation uses `records` for lane, classification, and safety
artifacts and the ELF census uses `artifacts`; the verifier previously assumed
only the older fixture keys and failed before auditing. The verifier now accepts
both authenticated representations while retaining its non-empty ELF-authority
gate. Regression coverage passes, and an independent live audit reports 1,301
packages, 16,642 authoritative ELF records, zero missing lane records, zero
missing classifications, and zero missing safety joins. This is coverage
accounting only; profile-use and BOLT deployment remain unclaimed.

### Phase-less helper profile propagation repair (2026-09-19)

The active framework was republished after the dispatcher repair so the
installed Portage policy now contains the current helper handling. The
policy additionally resets `LLVM_PROFILE_FILE` when Portage invokes the
optimization dispatcher without `EBUILD_PHASE` but with a non-empty
`MISC_FUNCTIONS_ARGS`, covering phase-less administrative misc-function
helpers. The dispatcher regression suite remains 45/45 and the framework
publication check passes. A bounded live rebuild of
`dev-dotnet/dotnet-runtime-nugets-10.0.11` still produced a staged
`/default.profraw` and was rejected by collision QA; the failed transaction
and profile artifacts remain preserved for diagnosis. No package merge or
profile receipt was admitted.

### Saved-profile environment investigation (2026-09-19)

Removing the global `PORTAGE_SAVED_READONLY_VARS` declaration did not resolve
the live no-DSO regression: Portage's saved ebuild environment still records
`LLVM_PROFILE_FILE=/dev/null`, but the package transaction recreated LLVM raw
profiles at the build root, `build-info/default.profraw`, and
`image/default.profraw`, causing collision QA to reject the package. Direct
privileged Bash, Python, and dotnet probes honor `/dev/null`; the remaining
writer is therefore in Portage's phase/misc-functions execution path rather
than the ABI guard. The failed attempt remains preserved and no package merge
was admitted.


### Portage env-filter probe (2026-09-19)

The live Portage `phase-functions.sh` environment-filter probe was patched in
all installed Python slots to pass `LLVM_PROFILE_FILE=/dev/null` to its
`env -i` Bash census subprocess. The first edit used invalid `env` argument
ordering and was corrected to `env -i -- LLVM_PROFILE_FILE=/dev/null ...`; the
regression was rerun with the corrected form. The package still emitted
`default.profraw`, so this census subprocess is not the sole writer. The live
Portage edits are retained as diagnostic hardening, while the failed package
transaction remains unmerged and its artifacts preserved.

### Portage child-boundary syscall trace (2026-09-19)

A bounded `strace` run identified repeated LLVM runtime opens of relative
`default.profraw` from `/bin/bash -e -c` phase children and Portage QA helper
children. The raw destination is absent at that exec boundary even though the
later saved ebuild environment contains `LLVM_PROFILE_FILE=/dev/null`.
Exploratory edits to installed Portage phase scripts were tested and reverted;
none are retained as project state. The evidence confirms the remaining repair
belongs at Portage's child-environment construction boundary, not in the ABI
guard or package ebuild.

### Portage child environment profile sink (2026-09-19)

The syscall trace showed that `doebuild.py` built phase-child environments
before bashrc sourcing, so the instrumented Bash runtime started without an
LLVM destination. The live Portage Python 3.13, 3.14, and 3.15 `doebuild.py`
variants now default `LLVM_PROFILE_FILE` to `/dev/null` in both the EAPI-9
copied-environment branch and the pre-EAPI-9 direct-environment branch, while
preserving any explicit runner-selected profile path. The bounded live
`dev-dotnet/dotnet-runtime-nugets-10.0.11` reinstall then completed
successfully; no `default.profraw` collision remained. The focused ABI guard
suite also passes. This is a live Portage integration repair; no PGO receipt or
profile-use deployment is claimed for the maintenance reinstall.

### CMake Clang IR profile wave (2026-09-19)

The exact `dev-build/cmake-4.3.5` recipe-ready package was bound to the
current Phase-3 generation and entered the `pgo-clang-ir` lane with the
reviewed `/usr/bin/ccmake --help` workload. The compile transaction outlived
the bounded orchestration shell, so its durable attempt was first reconciled
as failed; the still-running package transaction was allowed to finish rather
than restarted. After independent inspection confirmed the installed package,
the reviewed workload was executed with the exact generation spool, producing
269 nonempty LLVM raw payloads. An independently verified receipt was created
and `llvm-profdata` merged them into
`merged-profiles/dev-build_cmake-4.3.5.profdata` with evidence digest
`e8480333ccd01720f3e7955b4a42b5bd249bc2b49e02d54375c5ede471f83272`.
This records profile collection and merge only; profile-use deployment and
BOLT output remain unclaimed.

### SQLite Clang IR profile wave (2026-09-19)

The exact `dev-db/sqlite-3.53.4` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active generation authority.
The reviewed `/usr/bin/sqlite3 --help` workload ran after the 32-bit and
64-bit instrumented build/install, and the runner emitted a completed
transaction receipt. Independent receipt verification passed. LLVM 22 merged
the authenticated raw payload set into
`merged-profiles/dev-db_sqlite-3.53.4.profdata`; merge evidence digest is
`7798f0cc2523ad561cae4e4fbe5ffb97d982f3454b086a72e89d3b96115e1734`.
No profile-use deployment or BOLT output is claimed.

### doas Clang IR profile wave (2026-09-19)

The exact `app-admin/doas-6.8.2` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed workload completed and the runner emitted a completed
transaction receipt; independent receipt verification passed after one profile
file flushed after receipt sealing was preserved outside the authoritative
spool. LLVM 22 merged the authenticated payload set into
`merged-profiles/app-admin_doas-6.8.2.profdata`; merge evidence digest is
`67f63041536492794c21915bd26fc93e0cef4422930c44ef4decc04e9e1a8de7`.
The late post-seal payload remains preserved separately for audit and was not
admitted to the merge. No profile-use deployment or BOLT output is claimed.

### 7zip Clang IR profile wave (2026-09-19)

The exact `app-arch/7zip-26.03` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed `/usr/bin/7zz --help` workload completed, the sealed
receipt passed independent verification, and LLVM 22 merged the authenticated
payload set into `merged-profiles/app-arch_7zip-26.03.profdata`; merge evidence
digest is `bb28ff6da72b2017e0e923eca02fade11dd0169f3429879990e543e2d366fc3e`.
No profile-use deployment or BOLT output is claimed.

### bzip2 Clang IR profile wave (2026-09-19)

The exact `app-arch/bzip2-1.0.8-r5` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority, including both configured ABIs. The reviewed
`/bin/bzip2-reference --help` workload completed, the sealed receipt passed
independent verification, and LLVM 22 merged the authenticated payload set
into `merged-profiles/app-arch_bzip2-1.0.8-r5.profdata`; merge evidence digest
is `b1b00fc8392873a520e1da1dc6cbc7a03c120d0e5ad405ef5aefe20b2393d3ae`.
The transaction emitted existing ldconfig warnings for non-ELF LLVM YAML
optimization records; install-QA and merge completed. No profile-use deployment
or BOLT output is claimed.

### cabextract Clang IR profile wave (2026-09-19)

The exact `app-arch/cabextract-9999` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed `/usr/bin/cabextract --version` workload completed,
the sealed receipt passed independent verification, and LLVM 22 merged the
authenticated payload set into `merged-profiles/app-arch_cabextract-9999.profdata`;
merge evidence digest is
`1dfb24dcd2d29ff41a02540a158ba61997bac0a8cae805a1f63de214ae787575`.
No profile-use deployment or BOLT output is claimed.

### cpio Clang IR profile wave (2026-09-19)

The exact `app-arch/cpio-2.15` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed `/bin/gcpio --help` workload completed, the sealed
receipt passed independent verification, and LLVM 22 merged the authenticated
payload set into `merged-profiles/app-arch_cpio-2.15.profdata`; merge evidence
digest is `6a3d2b438d98998004fcecf30bc810cf6755eed96e05a7c8a41d5dd8fec47ac9`.
No profile-use deployment or BOLT output is claimed.

### dpkg Clang IR profile wave (2026-09-19)

The stale live4 dpkg attempt was retained separately after its generation
mismatch was detected. A fresh `app-arch/dpkg-1.22.21` transaction was then
run against the active Phase-3 generation. The reviewed `/usr/bin/dpkg --help`
workload completed, the current-generation receipt passed independent
verification, and LLVM 22 merged its authenticated payload set into
`merged-profiles/app-arch_dpkg-1.22.21.profdata`; merge evidence digest is
`c57a15e471bd9da83c663addcc30f59f84c35450629bcac1b110bbabad98ffda`.
No profile-use deployment or BOLT output is claimed.

### gzip Clang IR profile wave (2026-09-19)

The exact `app-arch/gzip-1.14_p20260901` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed `/bin/gzip-reference --help` workload completed, the
sealed receipt passed independent verification, and LLVM 22 merged the
authenticated payload set into
`merged-profiles/app-arch_gzip-1.14_p20260901.profdata`; merge evidence digest
is `c2ac7a58db463ac614b5711118b8b7a52ae23c19f0ecf6ff230c336870f5c8ae`.
No profile-use deployment or BOLT output is claimed.

### libarchive successor-wave ABI rejection (2026-09-19)

The current-generation `app-arch/libarchive-3.8.9` Clang IR wave reached
staging twice, but the unchanged fail-closed ABI guard rejected the image before
merge. The staged `libarchive.so.13` provider exported 453 symbols versus 603
from the installed provider; the missing set includes public `__archive_*`
identities and `PPMD8_kExpEscape`. No profile receipt or merge was admitted.
The package-specific build logs and failed attempt records remain preserved.
This reproduces the earlier libarchive ABI boundary under the successor
inventory and requires the existing package-specific ABI-safe remediation
before its profile payload can be accepted; the guard was not bypassed and no
terminal exclusion has been claimed.

### lz4 Clang IR profile wave (2026-09-19)

The exact `app-arch/lz4-1.10.0-r1` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed `/usr/bin/lz4 --help` workload completed, the sealed
receipt passed independent verification, and LLVM 22 merged the authenticated
payload set into `merged-profiles/app-arch_lz4-1.10.0-r1.profdata`; merge
evidence digest is `40f21fa92192dbf62e5fe67129f5694a9eb5991df9cd70117a4daee531b7e098`.
No profile-use deployment or BOLT output is claimed.

### libarchive public-ABI lane repair and successor wave (2026-09-19)

The earlier libarchive ABI rejection was repaired through the existing
public-ABI architecture. The content-addressed policy generator now emits an
explicit `pgo-clang-ir-generate-public.conf` lane for the reviewed CPV, the
installer validates its quoted compiler-flag expressions only for that exact
lane, and a package patch prevents libarchive's generated configure script
from reintroducing hidden visibility when `GENTOO_OPT_PUBLIC_ABI=1`. The
bootstrap framework was republished and its strict check passed. A fresh
current-generation `app-arch/libarchive-3.8.9` wave then completed both
multilib builds, passed install-QA ABI validation, produced a sealed receipt,
and passed independent receipt verification. LLVM 22 merged the authenticated
payload set into `merged-profiles/app-arch_libarchive-3.8.9.profdata`; merge
evidence digest is
`4da86814f314626710ff70319a5267b624d5d66fe9f5cbb7ea34d1ab4370dee2`.
No profile-use deployment or BOLT output is claimed.

### ncompress Clang IR profile wave (2026-09-19)

The exact `app-arch/ncompress-5.0-r2` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed `/usr/bin/compress -V` workload completed, the sealed
receipt passed independent verification, and LLVM 22 merged the authenticated
payload set into `merged-profiles/app-arch_ncompress-5.0-r2.profdata`; merge
evidence digest is `349b01241f8b4e58dc967bf36d7cedbe5b860601be4f7045db15fed86c8b9bb3`.
No profile-use deployment or BOLT output is claimed.

### rpm2targz Clang IR profile wave (2026-09-19)

The exact `app-arch/rpm2targz-2021.03.16` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. Its reviewed input-bound `/usr/bin/rpmoffset` workload consumed the
root-owned RPM fixture, the sealed receipt passed independent verification, and
LLVM 22 merged the authenticated payload set into
`merged-profiles/app-arch_rpm2targz-2021.03.16.profdata`; merge evidence digest
is `4d01ffc70ea838a7eaa8989e1f7dab6651c7ba178311732b01907279b475ac6d`.
No profile-use deployment or BOLT output is claimed.

### tar Clang IR profile wave (2026-09-19)

The exact `app-arch/tar-1.35-r1` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed `/bin/gtar --help` workload completed, the sealed
receipt passed independent verification, and LLVM 22 merged the authenticated
payload set into `merged-profiles/app-arch_tar-1.35-r1.profdata`; merge
 evidence digest is `e76d61dcea05b4339b9265b2812d4d6ac3c70b8c013e2aff03d0b8c255d632c6`.
No profile-use deployment or BOLT output is claimed.

### unzip Clang IR profile wave (2026-09-19)

The exact `app-arch/unzip-6.0_p31` recipe-ready package completed its
`pgo-clang-ir` training transaction under the active Phase-3 generation
authority. The reviewed `/usr/bin/unzip -v` workload completed, the sealed
receipt passed independent verification, and LLVM 22 merged the authenticated
payload set into `merged-profiles/app-arch_unzip-6.0_p31.profdata`; merge
evidence digest is `1fb1ef987d82e10b09f78c65df362dd18eb449226f54fd3bea6f8bc3a4b72da3`.
No profile-use deployment or BOLT output is claimed.

### ABI guard and xz wave boundary (2026-09-19)

The focused ABI guard implementation now has explicit empty-directory semantics,
an immediate no-DSO return, and immediate-directory provider discovery. Its
regression fixture covers a staged package with no `.so` candidates, nested
unrelated providers, and a 2,000-file descendant tree; all 12 cases pass.
The attempted `app-arch/xz-utils-9999` successor profile wave did not reach
build or receipt creation: the upstream Git fetch timed out after 300007 ms and
the mirror fetch remained idle. The controller terminated that attempt after
5m51s, preserved the raw fetch/build log, and recorded a durable failed attempt
with the exact source-fetch timeout reason. No package merge, profile receipt,
or profile merge was admitted.

### zstd Clang IR profile wave (2026-09-19)

The exact current-generation `app-arch/zstd-1.5.7-r1` successor wave completed
both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/pzstd --help`, `/usr/bin/zstd --help`, and
`/usr/bin/zstd-frugal --help` workloads completed, install-QA passed, and the
sealed receipt passed independent verification. LLVM 22 merged the authenticated
raw payload set into
`merged-profiles/app-arch_zstd-1.5.7-r1.profdata`; merge evidence digest is
`5cf1225981243a988c6c3239ee27549a91a25c83e65ba45e71e85c13c1998cb9`.
No profile-use deployment or BOLT output is claimed.

### json-glib Clang IR profile wave (2026-09-19)

The exact current-generation `dev-libs/json-glib-1.10.8` successor wave
completed both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/json-glib-format --help` and
`/usr/bin/json-glib-validate --help` workloads completed, install-QA passed,
and the sealed receipt passed independent verification. LLVM 22 merged the
authenticated raw payload set into
`merged-profiles/dev-libs_json-glib-1.10.8.profdata`; merge evidence digest is
`f49c942cc7a8271b39ef5a6c19f2a6659220218f11d51f2923eff60f837ab483`.
No profile-use deployment or BOLT output is claimed.

### fribidi Clang IR profile wave (2026-09-19)

The exact current-generation `dev-libs/fribidi-1.0.16` successor wave
completed both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/fribidi --help` workload completed, install-QA passed,
and the sealed receipt passed independent verification. LLVM 22 merged the
authenticated raw payload set into
`merged-profiles/dev-libs_fribidi-1.0.16.profdata`; merge evidence digest is
`88c8258b856024797b3284b9013181cd91724f6b9429b7c0a673fce715449518`.
No profile-use deployment or BOLT output is claimed.

### expat Clang IR profile wave (2026-09-19)

The exact current-generation `dev-libs/expat-2.8.4` successor wave completed
both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/xmlwf --help` workload completed, install-QA passed,
and the sealed receipt passed independent verification. LLVM 22 merged the
authenticated raw payload set into
`merged-profiles/dev-libs_expat-2.8.4.profdata`; merge evidence digest is
`779a0aa41cea93ec700f99fdbfbc3585b8d1b9fd5dc0a496c859576e652e0af2`.
No profile-use deployment or BOLT output is claimed.

### libgpg-error Clang IR profile wave (2026-09-19)

The exact current-generation `dev-libs/libgpg-error-1.61` successor wave
completed both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/gpg-error --help` and `/usr/bin/gpgrt-config --help`
workloads completed, install-QA passed, and the sealed receipt passed
independent verification. LLVM 22 merged the authenticated raw payload set
into `merged-profiles/dev-libs_libgpg-error-1.61.profdata`; merge evidence
digest is `1aecbdd4f69e0e6a9eb4942ac53bebd233adad9d4b61ef68dbefb239d7c59b7f`.
No profile-use deployment or BOLT output is claimed.

### libtasn1 Clang IR profile wave (2026-09-19)

The exact current-generation `dev-libs/libtasn1-4.21.0` successor wave
completed both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/asn1Coding --help`, `/usr/bin/asn1Decoding --help`, and
`/usr/bin/asn1Parser --help` workloads completed, install-QA passed, and the
sealed receipt passed independent verification. LLVM 22 merged the authenticated
raw payload set into `merged-profiles/dev-libs_libtasn1-4.21.0.profdata`; merge
evidence digest is `65d198466b9722687e3cf909ed9e75cddbafd9153e672839d0006a68094060fb`.
No profile-use deployment or BOLT output is claimed.

### libpcre Clang IR profile wave (2026-09-19)

The exact current-generation `dev-libs/libpcre-8.45-r4` successor wave
completed both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/pcregrep --help` and `/usr/bin/pcretest --help`
workloads completed, install-QA passed, and the sealed receipt passed
independent verification. LLVM 22 merged the authenticated raw payload set
into `merged-profiles/dev-libs_libpcre-8.45-r4.profdata`; merge evidence
digest is `5ea21bb7dcc3c67d3238c961e71aee9c6ec9c45d76e77e47adc28642fe855f30`.
No profile-use deployment or BOLT output is claimed.

### xxhash Clang IR profile wave (2026-09-19)

The exact current-generation `dev-libs/xxhash-0.8.3-r2` successor wave
completed both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/xxhsum --help` workload completed, install-QA passed,
and the sealed receipt passed independent verification. LLVM 22 merged the
authenticated raw payload set into
`merged-profiles/dev-libs_xxhash-0.8.3-r2.profdata`; merge evidence digest is
`d894ccde89a8920b9f0a27479777f537c20d16b1e8b302382d850a583efeb2e3`.
No profile-use deployment or BOLT output is claimed.

### libtracefs workload rejection (2026-09-19)

The current-generation `dev-libs/libtracefs-1.8.3` training transaction rebuilt
and installed successfully and passed install-QA, but its reviewed workload
`/usr/bin/sqlhist --help` exited 255 because `sqlhist` does not implement a
`--help` option. A direct probe confirmed that even an empty SQL input attempts
to create a synthetic tracing event and exits 255 in the current tracefs
runtime. The runner correctly refused to seal a receipt or admit profiles.
The failed attempt remains preserved; no PGO result is claimed and no tracing
or kernel state was mutated.

### libgcrypt Clang IR profile wave and merge scalability repair (2026-09-19)

The exact current-generation `dev-libs/libgcrypt-1.12.4` successor wave
completed both configured ABIs under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `dumpsexp`, `hmac256`, `mpicalc`, and `mpicalc --help` workloads
completed, install-QA passed, and the sealed receipt passed independent
verification. The first merge attempt exposed `E2BIG` because the raw payload
list exceeded the operating system argument-vector limit. The merge tool was
repaired to merge deterministic batches of 256 raw profiles and then merge the
intermediate profiles. The authenticated payloads were merged successfully
with evidence digest
`16dbc4096b73237ce75586ff8239fff2c2134fc91d7d6ce4a39550ec8e09b246`.
No profile-use deployment or BOLT output is claimed.

### nettle Clang IR profile wave and workload correction (2026-09-19)

The first current-generation `dev-libs/nettle-3.10.2` attempt correctly
rejected the reviewed `/usr/bin/nettle-lfib-stream --help` recipe because that
program accepts a seed rather than a help option and exited 1. The recipe was
corrected by removing that invalid invocation; the valid `nettle-hash --help`
and `nettle-pbkdf2 --help` workloads were retained. A fresh current-generation
wave then rebuilt both configured ABIs, passed install-QA, completed both valid
workloads, and passed independent receipt verification. LLVM 22 merged the
authenticated raw payload set into `merged-profiles/dev-libs_nettle-3.10.2.profdata`;
merge evidence digest is
`9258f08329c349e29ef5503c37f4837dc431b9c8a140141d3a66ed22edfacc8c`.
No profile-use deployment or BOLT output is claimed.

### snowball-stemmer Clang IR profile wave and workload correction (2026-09-19)

The first current-generation `dev-libs/snowball-stemmer-3.1.1` attempt
correctly rejected the reviewed `/usr/bin/stemwords --help` recipe because the
program accepts `-h` and exits 1 for the long option. The recipe was corrected
to `/usr/bin/stemwords -h`; a fresh wave then rebuilt the package, passed
install-QA, completed the corrected workload, and passed independent receipt
verification. LLVM 22 merged the authenticated raw payload set into
`merged-profiles/dev-libs_snowball-stemmer-3.1.1.profdata`; merge evidence
digest is `387df02595b80331522b4c1982335e48faeee0bbbfdc6a1cdfa215f3f88eae4c`.
No profile-use deployment or BOLT output is claimed.

### OpenSP Clang IR wave failure (2026-09-19)

The current-generation `app-text/opensp-1.5.2-r10` training transaction was
preserved as a failed attempt during the link phase. Clang/LLD reported missing
C++ ABI and exception-runtime symbols (`__gxx_personality_v0`, C++ typeinfo
vtables, allocation operators, and related `libc++abi` identities) while
linking `onsgmls`; the package never reached install-QA or workload execution.
No receipt or profile merge was admitted, and no terminal exclusion was claimed.
The complete Portage build log and durable failed attempt remain preserved for
package-specific compiler/link remediation.

### lowdown Clang IR profile wave (2026-09-19)

The exact current-generation `app-text/lowdown-3.1.1` successor wave rebuilt
and passed install-QA under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/lowdown --help` workload completed, the sealed receipt
passed independent verification, and LLVM 22 merged the authenticated raw
payload set into `merged-profiles/app-text_lowdown-3.1.1.profdata`; merge
evidence digest is `ec27cb79ae668aa286b3ea32743be00ec382f14344203cc8add95ffc12c63dae`.
No profile-use deployment or BOLT output is claimed.

### enchant Clang IR profile wave (2026-09-19)

The exact current-generation `app-text/enchant-2.8.16` successor wave rebuilt
and passed install-QA under `phase3-live-candidate-20260918-postsync-r1`.
The reviewed `/usr/bin/enchant-2 --help` and `/usr/bin/enchant-lsmod-2 --help`
workloads completed, the sealed receipt passed independent verification, and
LLVM 22 merged the authenticated raw payload set into
`merged-profiles/app-text_enchant-2.8.16.profdata`; merge evidence digest is
`a930047b6d7d7f235ea8b0f60b601176bd08cc167b33320a538e6c5444d6a75f`.
No profile-use deployment or BOLT output is claimed.

### gspell Clang IR workload timeout (2026-09-19)

The current-generation `app-text/gspell-1.14.4` transaction rebuilt and
installed successfully and passed install-QA, but the reviewed
`/usr/bin/gspell-app1 --help` workload exceeded the runner's 30-second command
limit. The runner correctly refused to seal a receipt or admit profiles. The
failed attempt and package logs remain preserved; no terminal exclusion or PGO
result is claimed.

### scdoc Clang IR profile wave (2026-09-19)

The exact current-generation `app-text/scdoc-9999` successor wave rebuilt and
passed install-QA under `phase3-live-candidate-20260918-postsync-r1`. Its
input-bound `/usr/bin/scdoc` fixture workload completed, the sealed receipt
passed independent verification, and LLVM 22 merged the authenticated raw
payload set into `merged-profiles/app-text_scdoc-9999.profdata`; merge evidence
digest is `dc7f6b26f87f5d504e67998ae414729f4a44552453881fe4113695a785b2c9ff`.
No profile-use deployment or BOLT output is claimed.

### ABI guard fail-closed provider inspection correction (2026-09-19)

The focused ABI guard traversal repair is retained: an intentionally empty
`relative_dirs` set scans nothing, staged images with no `.so` candidates
return immediately, and installed-provider discovery uses `iterdir()` only in
the exact candidate parent directories. The provider path previously discarded
`readelf` inspection errors and could therefore turn unreadable established ELF
providers into an implicit pass; that exception is now propagated so inspection
failure remains fail-closed. `tests/optimization/test-abi-guard.sh` passed all 12
cases and `tests/optimization/test-portage-qa-hook.sh` passed all 11 cases after
the correction. No boot, kernel, initramfs, EFI, or firmware state was touched.

### libpaper Clang IR profile wave (2026-09-19)

The exact current-generation `app-text/libpaper-2.1.3` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/paper --help` and `/usr/bin/paperconf -h` workloads completed, the
package merged successfully through the repaired install-QA ABI guard, and the
sealed receipt passed independent verification. LLVM 22 merged the
authenticated raw payload into
`merged-profiles/app-text_libpaper-2.1.3.profdata`; merge evidence digest is
`1a20846d333e4a43662fa1b407075a143e88e0e36a4b339cc951ea8bfc2fba53`. No
profile-use deployment or BOLT output is claimed.

### uchardet Clang IR profile wave (2026-09-19)

The exact current-generation `app-i18n/uchardet-0.0.8` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/uchardet --help` workload completed, install-QA passed with the
repaired ABI guard, and independent receipt verification passed. LLVM 22
merged the authenticated raw payload into
`merged-profiles/app-i18n_uchardet-0.0.8.profdata`; merge evidence digest is
`038d8812601d89d16687b53deb31b474153e256196012fdba840fd7e941a4bd5`. No
profile-use deployment or BOLT output is claimed.

### pkgconf successor wave failure (2026-09-19)

The exact current-generation `dev-util/pkgconf-9999` wave was admitted through
readiness (`1/1`, zero invalid inputs) but failed during `src_prepare` before
compilation. The live Git HEAD fetched by the ebuild contains no
`configure.ac`, while the Gentoo `pkgconf-9999.ebuild` unconditionally runs
`eautoreconf` for the 9999 revision; `aclocal` therefore failed with
`configure.ac is required`. The runner preserved the failed attempt and
Portage build log; no package merge, receipt, or profile merge was admitted.
This is a package-source/ebuild mismatch to remediate separately, not an ABI
guard or profile-runner failure.

### patchelf Clang IR profile wave (2026-09-19)

The exact current-generation `dev-util/patchelf-0.19.1` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/patchelf --help` workload completed, install-QA passed, and the
sealed receipt passed independent verification. LLVM 22 merged the
authenticated raw payload into
`merged-profiles/dev-util_patchelf-0.19.1.profdata`; merge evidence digest is
`79ca4510b8a676c0a94ed1d0a9f9056707372fb5f35d511a9cc2388f5223439b`. No
profile-use deployment or BOLT output is claimed.

### rhash Clang IR profile wave (2026-09-19)

The exact current-generation `app-crypt/rhash-1.4.6-r1` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`, including its
configured 32-bit and 64-bit build outputs. The reviewed `/usr/bin/rhash
--help` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/app-crypt_rhash-1.4.6-r1.profdata`; merge evidence digest is
`585e571408b0f994adbafd00639fc1faca4c17f234edb32d5596d172aba3e7fb`. No
profile-use deployment or BOLT output is claimed.

### xmlto Clang IR profile wave (2026-09-19)

The exact current-generation `app-text/xmlto-0.0.28-r11` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/xmlif --help` workload completed, install-QA passed, and independent
receipt verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/app-text_xmlto-0.0.28-r11.profdata`; merge evidence digest is
`3cd01603fb71587e5a570259e93bdd592b229c60cefbf871668d1683a289632d`. No
profile-use deployment or BOLT output is claimed.

### which Clang IR profile wave (2026-09-19)

The exact current-generation `sys-apps/which-2.23` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/which --help` workload completed, install-QA passed, and independent
receipt verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_which-2.23.profdata`; merge evidence was published
for the current generation. No profile-use deployment or BOLT output is claimed.

### zip Clang IR profile wave (2026-09-19)

The exact current-generation `app-arch/zip-3.0_p16` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed `zip`,
`zipcloak`, `zipnote`, and `zipsplit` version workloads completed, install-QA
passed, and independent receipt verification passed. LLVM 22 merged the
authenticated raw payload into `merged-profiles/app-arch_zip-3.0_p16.profdata`;
merge evidence was published for the current generation. No profile-use
deployment or BOLT output is claimed.

### zsh Clang IR profile wave (2026-09-19)

The exact current-generation `app-shells/zsh-9999` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed `/bin/zsh
--help` and `/bin/zsh-9999 --help` workloads completed, install-QA passed,
and independent receipt verification passed. LLVM 22 merged the authenticated
raw payload into `merged-profiles/app-shells_zsh-9999.profdata`; merge evidence
digest is `bd661d984b154076286bdf6b004c49ad48a263416aef2cb1abd8347a77d6f7a9`.
LLVM reported counter-mismatch warnings during merge, which are preserved in
the merge output; no profile-use deployment or BOLT output is claimed.

### unifdef Clang IR profile wave and workload correction (2026-09-19)

The initial `dev-util/unifdef-2.12-r2` wave built and merged but its reviewed
`unifdef --help` recipe exited 2, so no receipt was admitted. Direct probing
confirmed the supported successful help form is `unifdef -h`. The wave was
corrected narrowly, rerun against the same current generation, and completed
with install-QA and independent receipt verification passing. LLVM 22 merged
the authenticated raw payload into
`merged-profiles/dev-util_unifdef-2.12-r2.profdata`; merge evidence digest is
`89a7db1313234fb086aac5bb14700f0163e5d30c1d7f2c56ec0be4cb9cfd0ccc`. No
profile-use deployment or BOLT output is claimed.

### xxd Clang IR profile wave and workload correction (2026-09-19)

The initial `dev-util/xxd-2025.08.24-r1` wave built and merged but its
reviewed `xxd --help` recipe exited 1. Direct probing showed that `xxd
--version` is the supported successful probe. The recipe was corrected
narrowly, rerun against the same current generation, and completed with
install-QA and independent receipt verification passing. LLVM 22 merged the
authenticated raw payload into
`merged-profiles/dev-util_xxd-2025.08.24-r1.profdata`; merge evidence digest is
`f318ace4e81461578ee37b49adabc4f298a9397ff4a9373e5f8b95e9d82751f9`. No
profile-use deployment or BOLT output is claimed.

### portage-utils Clang IR profile wave (2026-09-19)

The exact current-generation `app-portage/portage-utils-9999` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/q --help` workload completed, install-QA passed, and independent
receipt verification passed. The large raw payload was merged with the
batched LLVM 22 merger into
`merged-profiles/app-portage_portage-utils-9999.profdata`; merge evidence
digest is `0ec170f8c8f9600c69b30b4dec2e6bf61d673dd1c1ca37a37732332bf9499a69`.
No profile-use deployment or BOLT output is claimed.

### quoter Clang IR profile wave (2026-09-19)

The exact current-generation `app-shells/quoter-4.2` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/quoter --help` workload completed, install-QA passed, and independent
receipt verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/app-shells_quoter-4.2.profdata`; merge evidence digest is
`c31e2bb87ab29d522b1af2aa70380a1ed127d645200df1642f1eab4062fa5b25`. No
profile-use deployment or BOLT output is claimed.

### yodl successor wave failure (2026-09-19)

The exact current-generation `app-text/yodl-4.05.00` wave reached compilation
but failed at the C++ link stage. The instrumented build used libc++ and
reported unresolved `std::__1` runtime symbols while linking the `yodl`
programs. No package merge, workload execution, receipt, or profile merge was
admitted; the complete Portage log and runner failure attempt remain
preserved for a bounded toolchain/link remediation.

### rpm-sequoia Rust profile wave failure (2026-09-19)

The exact current-generation `app-crypt/rpm-sequoia-1.10.2` build completed
and installed successfully under the Rust instrumentation lane, but its
reviewed wave contains no workload recipes and produced no raw profile files.
The runner therefore refused receipt creation at the fail-closed profile
quiescence gate. No receipt or profile merge was admitted; the package build
log and failed runner attempt remain preserved. This requires a representative
Rust workload or an evidence-backed terminal accounting decision before the
package can leave the profile frontier.

### bash Clang IR profile wave (2026-09-19)

The exact current-generation `app-shells/bash-9999` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed `/bin/bash
--help` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/app-shells_bash-9999.profdata`; merge evidence digest is
`5218a436fe3c495d2062494035769da96aa2f44e40a10d39b12b45700a3995dc`. No
profile-use deployment or BOLT output is claimed.

### argon2 Clang IR profile wave (2026-09-19)

The exact current-generation `app-crypt/argon2-20190702-r1` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/argon2 12345678 -id -t 1 -m 5 -p 1` workload completed, install-QA
passed, and independent receipt verification passed. LLVM 22 merged the
authenticated raw payload into
`merged-profiles/app-crypt_argon2-20190702-r1.profdata`; merge evidence digest
is `6b6fe4d3b225ae64d7cc7fe98ca6a0737385cae0e99e10d3d07521845ddaf273`. No
profile-use deployment or BOLT output is claimed.

### acl Clang IR profile wave and workload correction (2026-09-19)

The initial `sys-apps/acl-9999` wave built and merged but its reviewed
`chacl --help` probe exited 1. Direct probing confirmed `chacl -l /tmp` as a
safe successful inspection, while the `getfacl` and `setfacl` help probes
passed unchanged. The corrected wave was rerun against the same current
generation, completed install-QA, and passed independent receipt verification.
LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_acl-9999.profdata`; merge evidence digest is
`1b236f26c077e08436c4be67295b506bdb14ca4905cd5bcf49e08751a02a2c17`. No
profile-use deployment or BOLT output is claimed.

### attr Clang IR profile wave and workload correction (2026-09-19)

The `sys-apps/attr-9999` wave required two narrow workload corrections.
`attr --help` exited 1; `attr -l /tmp` succeeded but legitimately produced
no output, so its recipe was changed to allow empty output. `getfattr
--version` and `setfattr --version` succeeded unchanged. The corrected wave
completed against the current generation with install-QA and independent
receipt verification passing. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_attr-9999.profdata`; merge evidence digest is
`3026a2f053b4386f405c522b6e04a530a08777b60f1bfb786abc09734c10d96e`. No
profile-use deployment or BOLT output is claimed.

### b2 Clang IR profile wave (2026-09-19)

The exact current-generation `dev-build/b2-5.5.3` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/b2 --help` workload completed after the package's restricted test
phase was skipped by its ebuild, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/dev-build_b2-5.5.3.profdata`; merge evidence digest is
`f64932cabd34e72a0036b49b90c8e713058422c34f7b996990f670d3f939ad55`. No
profile-use deployment or BOLT output is claimed.

### cpuid2cpuflags Clang IR profile wave (2026-09-19)

The exact current-generation `app-portage/cpuid2cpuflags-18` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/cpuid2cpuflags --help` workload completed, install-QA passed, and
independent receipt verification passed. LLVM 22 merged the authenticated raw
payload into `merged-profiles/app-portage_cpuid2cpuflags-18.profdata`; merge
evidence digest is `6de58874ac8dd96f908976dbb95cc9556a80e5bc2462849d797abff13ed70fdb`.
No profile-use deployment or BOLT output is claimed.

### dash Clang IR profile wave (2026-09-19)

The exact current-generation `app-shells/dash-9999` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed shell
workload `/bin/dash -c 'printf dash-workload'` completed, install-QA passed,
and independent receipt verification passed. LLVM 22 merged the authenticated
raw payload into `merged-profiles/app-shells_dash-9999.profdata`; merge
evidence digest is `8d0ae249b686aa41e0a5bbd70708b6c6f78f8201ec96b0121fd57f95cac8e958`.
No profile-use deployment or BOLT output is claimed.

### diffutils Clang IR profile wave (2026-09-19)

The exact current-generation `sys-apps/diffutils-3.12` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`cmp`, `diff`, `diff3`, and `sdiff` help workloads completed, install-QA
passed, and independent receipt verification passed. LLVM 22 merged the
authenticated raw payload into
`merged-profiles/sys-apps_diffutils-3.12.profdata`; merge evidence digest is
`de1eca53b7dbb3f36eaa5fdef27201ccba05d6574c776a3c5b93062e194be607`. No
profile-use deployment or BOLT output is claimed.

### make Clang IR profile wave (2026-09-19)

The exact current-generation `dev-build/make-9999` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/gmake --help` workload completed, install-QA passed, and independent
receipt verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/dev-build_make-9999.profdata`; merge evidence digest is
`3a41190d8b946b524a71a588e5a7cfb3119ffaa405cd769088af9bc89c448be8`. No
profile-use deployment or BOLT output is claimed.

### bmake Clang IR profile wave (2026-09-19)

The exact current-generation `dev-build/bmake-20260508` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/bmake -V MAKE_VERSION` workload completed, install-QA passed, and
independent receipt verification passed. LLVM 22 merged the authenticated raw
payload into `merged-profiles/dev-build_bmake-20260508.profdata`; merge
evidence digest is `daeb23d66a0e5a64e184e966119666a6e7f56a5960282dd31055a31049f82327`.
No profile-use deployment or BOLT output is claimed.

### less successor wave fetch failure (2026-09-19)

The exact current-generation `sys-apps/less-9999` wave was admitted with
readiness `1/1`, but its moving Git source fetch from `github.com/gwsw/less`
remained at zero CPU for nearly four minutes and produced no source/build
progress. The bounded attempt was terminated, preserving the runner failure
and no package merge, workload, receipt, or profile merge was admitted. This
is a source-fetch/network remediation item, separate from package build or
profile validation.

### pinentry Clang IR profile wave (2026-09-19)

The exact current-generation `app-crypt/pinentry-1.3.3` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`pinentry-curses --help` and `pinentry-tty --help` workloads completed,
install-QA passed, and independent receipt verification passed. LLVM 22 merged
the authenticated raw payload into
`merged-profiles/app-crypt_pinentry-1.3.3.profdata`; merge evidence digest is
`771ba004e5ecc237a8a33830df2d48381580d8d7ec73459a32b6792438b9c562`. No
profile-use deployment or BOLT output is claimed.

### evtest Clang IR profile wave (2026-09-19)

The exact current-generation `app-misc/evtest-1.36` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/evtest --version` workload completed, install-QA passed, and
independent receipt verification passed. LLVM 22 merged the authenticated raw
payload into `merged-profiles/app-misc_evtest-1.36.profdata`; merge evidence
digest is `0098549e5a0dfa9ef2d2ef34c63de186b74cd6f6a7736ad4506506c226f14f64`.
No profile-use deployment or BOLT output is claimed.

### fastfetch Clang IR profile wave (2026-09-19)

The exact current-generation `app-misc/fastfetch-2.68.1-r1` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`. The reviewed
`/usr/bin/fastfetch --help` workload completed, install-QA passed, and
independent receipt verification passed. LLVM 22 merged the authenticated raw
payload into `merged-profiles/app-misc_fastfetch-2.68.1-r1.profdata`; merge
evidence digest is `b68bed10b1482b51153c0be3d69559755700a3f213fed9ffe9379a45766a821b`.
No profile-use deployment or BOLT output is claimed.

### jq Clang IR profile wave (2026-09-19)

The exact current-generation `app-misc/jq-1.8.2` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. The reviewed `/usr/bin/jq
--help` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/app-misc_jq-1.8.2.profdata`; merge evidence digest is
`feea9003557bb8c907ed06206ed88bd79daffb5b733954e35b18fb9a32445eb8`. No
profile-use deployment or BOLT output is claimed.

### gentoo-functions Clang IR profile wave and TTY workload correction (2026-09-19)

The initial `sys-apps/gentoo-functions-9999` wave built and merged but the
direct `/bin/consoletype --help` probe exited 1 because `consoletype` requires
a terminal. A PTY-backed probe using `/usr/bin/script -q -c /bin/consoletype
/dev/null` was verified successful. The recipe path was rebound to the safe
`script` executable while retaining the consoletype invocation, and the wave
was rerun against the same generation. Install-QA and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_gentoo-functions-9999.profdata`; merge evidence
digest is `339a483ac333e5dba104b28d82e9e9d7fe74171e29ff1627808c88c61bf690f8`.
No profile-use deployment or BOLT output is claimed.

### libmd Clang IR profile wave (2026-09-19)

The exact current-generation `app-crypt/libmd-1.2.0` successor wave completed
under `phase3-live-candidate-20260918-postsync-r1`. This library-only wave
had no executable workload recipes; the instrumented build nevertheless
produced an authenticated raw profile payload, install-QA passed, and the
sealed receipt passed independent verification. LLVM 22 merged the payload into
`merged-profiles/app-crypt_libmd-1.2.0.profdata`; merge evidence digest is
`4778ea6490f95fe7e11322b983a61f53fe5dd36d2aa04cb228372f739d668abc`. No
profile-use deployment or BOLT output is claimed.

### libb2 Clang IR profile wave (2026-09-19)

The exact current-generation `app-crypt/libb2-0.98.1-r3` successor wave
completed under `phase3-live-candidate-20260918-postsync-r1`, including its
configured 32-bit and 64-bit outputs. This library-only wave had no executable
workload recipes; the instrumented build produced an authenticated raw profile
payload, install-QA passed, and independent receipt verification passed. LLVM
22 merged the payload into `merged-profiles/app-crypt_libb2-0.98.1-r3.profdata`;
merge evidence digest is `b68a526b78691823785b5eb0bfe897705e568519e4241ea5c307582042656cb1`.
No profile-use deployment or BOLT output is claimed.

### mandoc Clang IR profile wave (2026-09-19)

The corrected exact `app-text/mandoc-1.14.6-r1` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. Database-independent workload
recipes were validated (`apropos .`, direct mandoc parsers, `msoelim /dev/null`,
and `makewhatis -p /tmp`); install-QA and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/app-text_mandoc-1.14.6-r1.profdata`; merge evidence digest is
`4899da91b16cff33f4a2f2a9c52636d0ec154b6095df7d026ca660d828a1e850`. No
profile-use deployment or BOLT output is claimed.

### dos2unix Clang IR profile wave (2026-09-19)

The exact `app-text/dos2unix-7.5.6` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `dos2unix --help` and
`unix2dos --help` workloads completed, install-QA passed, and independent
receipt verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/app-text_dos2unix-7.5.6.profdata`; merge evidence digest is
`f94de9ebc89aba39757717aa9e0a186bf8a28e96def9a8a2f9c950e2547cd5a3`. No
profile-use deployment or BOLT output is claimed.

### hunspell Clang IR profile wave (2026-09-19)

The exact `app-text/hunspell-1.7.2-r1` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/hunspell -h`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/app-text_hunspell-1.7.2-r1.profdata`; merge evidence digest is
`dc771f21c5cfab1f02334c4e487cb074da720ee3fec69c4bfd841969cb9a02fb`. No
profile-use deployment or BOLT output is claimed.

### debianutils Clang IR profile wave (2026-09-19)

The exact `sys-apps/debianutils-5.23.2` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/bin/run-parts --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_debianutils-5.23.2.profdata`; merge evidence digest
is `030c946926b71d5524cb23a528faa73f7e3848bd8bdea8a1fbab99658065dd82`. No
profile-use deployment or BOLT output is claimed.

### dmidecode Clang IR profile wave (2026-09-19)

The exact `sys-apps/dmidecode-3.7` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/sbin/dmidecode
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_dmidecode-3.7.profdata`; merge evidence digest is
`383016f34c8125c75592266d74ce8a85c13d7b860053b06ea16c3ed33f8e582a`. No
profile-use deployment or BOLT output is claimed.

### grep Clang IR profile wave (2026-09-19)

The exact `sys-apps/grep-3.12` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/bin/grep --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_grep-3.12.profdata`; merge evidence digest is
`1fa35e1fcce11bd4df210cae5de28c6c5d95fbbaf2eabe57af0c706425fe102a`. No
profile-use deployment or BOLT output is claimed.

### sed Clang IR profile wave (2026-09-19)

The exact `sys-apps/sed-4.10-r1` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/bin/sed --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_sed-4.10-r1.profdata`; merge evidence digest is
`7da82a2421eaddc0235d35d13cf6dfc08fc19682daa58bde6761195a67471a15`. No
profile-use deployment or BOLT output is claimed.

### gawk Clang IR profile wave (2026-09-19)

The exact `sys-apps/gawk-5.4.1a` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/gawk --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_gawk-5.4.1a.profdata`; merge evidence digest is
`7aacc91da6d8ff3e789849c0837eff867b255c502677516adadf01b08b36051a`. No
profile-use deployment or BOLT output is claimed.

### bc Clang IR profile wave (2026-09-19)

The exact `sys-devel/bc-1.08.2` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/bc --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-devel_bc-1.08.2.profdata`; merge evidence digest is
`e6e402a96d7ef20eceae97c99ad0d407a9a3cef8b49809f7f98c45cf85ed2da7`. No
profile-use deployment or BOLT output is claimed.

### m4 Clang IR profile wave (2026-09-19)

The exact `sys-devel/m4-1.4.21` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/m4 --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-devel_m4-1.4.21.profdata`; merge evidence digest is
`a82f3feadfcbd35a640210e56ab3c4dcebe2ed9691376bf65547b88d1050db2c`. No
profile-use deployment or BOLT output is claimed.

### flex Clang IR profile wave (2026-09-19)

The exact `sys-devel/flex-2.6.4-r6` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/flex --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-devel_flex-2.6.4-r6.profdata`; merge evidence digest is
`c997a13d997a772444c011b0dce5c1670779d110d271046699edf997bc517a5e`. No
profile-use deployment or BOLT output is claimed.

### bison Clang IR profile wave (2026-09-19)

The exact `sys-devel/bison-3.8.2-r3` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/bison --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-devel_bison-3.8.2-r3.profdata`; merge evidence digest is
`f51807b4bbd32bd9257238c25b05b1c099ce495a9deb19ee2dd27d6e61e97abd`. No
profile-use deployment or BOLT output is claimed.

### patch Clang IR profile wave (2026-09-19)

The exact `sys-devel/patch-2.8-r1` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/patch --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-devel_patch-2.8-r1.profdata`; merge evidence digest is
`d400f5a5a62b4ad942462ed6e79f810c1c3936719feffefe42e9eb7e56047bbb`. No
profile-use deployment or BOLT output is claimed.

### ethtool Clang IR profile wave (2026-09-19)

The exact `sys-apps/ethtool-7.0` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/sbin/ethtool
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_ethtool-7.0.profdata`; merge evidence digest is
`a63b0abe3d953e7abe42858ebebaa859931fbf676ecc521c7bcccb3bb72cf2f5`. No
profile-use deployment or BOLT output is claimed.

### kmod Clang IR profile wave (2026-09-19)

The exact `sys-apps/kmod-34.2` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/bin/kmod --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_kmod-34.2.profdata`; merge evidence digest is
`9f5c960bf5a2ad97d43506f0511f7580327c492a6e9e1430f86c01772ca942fa`. No
profile-use deployment or BOLT output is claimed.

### dosfstools Clang IR profile wave (2026-09-19)

The exact `sys-fs/dosfstools-4.2` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The non-mutating
`/usr/sbin/fatlabel --help` workload completed, install-QA passed, and
independent receipt verification passed. LLVM 22 merged the authenticated raw
payload into `merged-profiles/sys-fs_dosfstools-4.2.profdata`; merge evidence
digest is `62609c0f48c12e33a762d4877af35fa289e4eb5aad6c620cb0694f07ab6f6e2f`.
No profile-use deployment or BOLT output is claimed.

### e2fsprogs Clang IR profile wave (2026-09-19)

The exact `sys-fs/e2fsprogs-1.47.4` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The non-mutating `/sbin/mke2fs
-V` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-fs_e2fsprogs-1.47.4.profdata`; merge evidence digest is
`1b9178bf08e7161f21a341fce1a8a0d1797eeefbdca651da3ded85f4238b594c`. No
profile-use deployment or BOLT output is claimed.

### gdbm Clang IR profile wave (2026-09-19)

The exact `sys-libs/gdbm-1.26` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/gdbmtool
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-libs_gdbm-1.26.profdata`; merge evidence digest is
`de0cde41f6782e5f934dc1b11f5008de9ac2cdda8cbb22570d51c234f7124236`. No
profile-use deployment or BOLT output is claimed.

### libcap Clang IR profile wave (2026-09-19)

The exact `sys-libs/libcap-2.78` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/sbin/capsh
--help` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-libs_libcap-2.78.profdata`; merge evidence digest is
`d7e7d0bf495d58eb5934c03f46434f88de54af5440f4a1375d8001a5c203a1f9`. No
profile-use deployment or BOLT output is claimed.

### slang Clang IR profile wave (2026-09-19)

The exact `sys-libs/slang-2.3.3-r2` wave completed under
`phase3-live-candidate-20260918-postsync-r1`, including its configured 32-bit
and 64-bit outputs. The `/usr/bin/slsh --version` workload completed,
install-QA passed, and independent receipt verification passed. LLVM 22 merged
the authenticated raw payload into
`merged-profiles/sys-libs_slang-2.3.3-r2.profdata`; merge evidence digest is
`07fe1985763d46776d75937c004c50e54ec29c726229a0c925a3b569f01173a7`. No
profile-use deployment or BOLT output is claimed.

### procps Clang IR profile wave (2026-09-19)

The exact `sys-process/procps-4.0.6` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/bin/ps --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-process_procps-4.0.6.profdata`; merge evidence digest is
`8fc325c8da69e4749b4d12768f653c734d5c5569d6c3060d1610796b0c1b93ab`. No
profile-use deployment or BOLT output is claimed.

### psmisc Clang IR profile wave (2026-09-19)

The exact `sys-process/psmisc-23.7` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/pstree --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-process_psmisc-23.7.profdata`; merge evidence digest is
`73bc254456e9e8bad5803dcb820afd14c6408193267b80d22138f62f4cae4f9`. No
profile-use deployment or BOLT output is claimed.

### time Clang IR profile wave (2026-09-19)

The exact `sys-process/time-1.10` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The `/usr/bin/time --version`
workload completed, install-QA passed, and independent receipt verification
passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-process_time-1.10.profdata`; merge evidence digest is
`134ef86caac7f35b6640ec85ba8cc1ddaf02fe45f833ddee819dfa5c56b3b42f`. No
profile-use deployment or BOLT output is claimed.

### iproute2 Clang IR profile wave (2026-09-19)

The exact `sys-apps/iproute2-7.2.0` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/sbin/ip
-Version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_iproute2-7.2.0.profdata`; merge evidence digest is
`0abc7c604b40b21e5f6616af67c75650777bfcd608d42d19b70e90a24ea28427`. No
profile-use deployment or BOLT output is claimed.

### fuse-overlayfs Clang IR profile wave (2026-09-19)

The exact `sys-fs/fuse-overlayfs-1.17` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only
`/usr/bin/fuse-overlayfs --version` workload completed, install-QA passed,
and independent receipt verification passed. LLVM 22 merged the authenticated
raw payload into `merged-profiles/sys-fs_fuse-overlayfs-1.17.profdata`; merge
evidence digest is `1b1050ef1b575b580fa83e946b174a16f9a71887782fa4e0b3e8a1531e44154c`.
No profile-use deployment or BOLT output is claimed.

### xfsprogs profile merge incompatibility (2026-09-19)

The exact `sys-fs/xfsprogs-7.1.1` wave built, installed, and produced a sealed
receipt whose workload and install-QA completed. Both merge attempts failed
closed because one generated raw payload (`6193553815871224956_0-1168.profraw`)
uses raw profile format version 11 while the LLVM 22 `llvm-profdata` merger
expects version 10. The transient raw directory was cleaned and the exact
wave rerun; the same file/version mismatch reproduced. No merged profile or
optimization claim is admitted for this CPV. The package remains pending
instrumentation-version remediation, with both receipts and merge failures
preserved under `/tmp` and the generation spool.

### parted Clang IR profile wave (2026-09-19)

The exact `sys-block/parted-3.7` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/sbin/parted
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-block_parted-3.7.profdata`; merge evidence digest is
`a804ba2603c4c7b5d649226e3dc5eb2801a468b5b9c55698598bb75896463d82`. No
profile-use deployment or BOLT output is claimed.

### cryptsetup Clang IR profile wave (2026-09-19)

The exact `sys-fs/cryptsetup-2.8.8` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/sbin/cryptsetup
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-fs_cryptsetup-2.8.8.profdata`; merge evidence digest is
`15a7e4aa48e310e807a473e02792e183a3c23972308767f2d4a5a6557b67be46`. No
profile-use deployment or BOLT output is claimed.

### gptfdisk Clang IR profile wave (2026-09-19)

The exact `sys-apps/gptfdisk-1.0.10-r1` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/usr/sbin/sgdisk
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_gptfdisk-1.0.10-r1.profdata`; merge evidence digest
is `c4d9458ae8da5726d118f36a13f871b965fdf3371c384653cad9085cec830c28`. No
profile-use deployment or BOLT output is claimed.

### lvm2 Clang IR profile wave (2026-09-19)

The exact `sys-fs/lvm2-2.03.39` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/sbin/lvm
version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-fs_lvm2-2.03.39.profdata`; merge evidence digest is
`8a88ecc4ac40cef25b725bec8115466c9edb8ba94958cbe615bb76e25e3ed37d`. No
profile-use deployment or BOLT output is claimed.

### openrc Clang IR profile wave (2026-09-19)

The exact `sys-apps/openrc-0.64` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/sbin/openrc
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_openrc-0.64.profdata`; merge evidence digest is
`9c562da8a507ff076d0c2f6ad35f389508cb2404ef4ea51bf6ea33121a67b32e`. No
profile-use deployment or BOLT output is claimed.

### busybox Clang IR profile wave (2026-09-19)

The exact `sys-apps/busybox-1.38.0` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/bin/busybox
--help` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_busybox-1.38.0.profdata`; merge evidence digest is
`b0d9f01e5a0f1a8f082f67407f9b9e6e0d49891f4746f94a46987f72b79f837d`. No
profile-use deployment or BOLT output is claimed.

### groff Clang IR profile wave (2026-09-19)

The exact `sys-apps/groff-1.23.0-r2` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/usr/bin/groff
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_groff-1.23.0-r2.profdata`; merge evidence digest is
`d643f54371e5494cae61224d320c6a029558f67b961f03ddf76ac78386be9297`. No
profile-use deployment or BOLT output is claimed.

### texinfo Clang IR profile wave (2026-09-19)

The exact `sys-apps/texinfo-7.3` wave completed under
`phase3-live-candidate-20260918-postsync-r1`. The read-only `/usr/bin/makeinfo
--version` workload completed, install-QA passed, and independent receipt
verification passed. LLVM 22 merged the authenticated raw payload into
`merged-profiles/sys-apps_texinfo-7.3.profdata`; merge evidence digest is
`4e62d21480454e88d8f446dc3a960bc70448491960fa0c87247e08ea79af9822`. No
profile-use deployment or BOLT output is claimed.

### lm-sensors Clang IR profile wave (2026-09-19)
The exact `sys-apps/lm-sensors-3.6.2` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/sensors --version` completed successfully; the merged profile is `merged-profiles/sys-apps-lm-sensors-3.6.2.profdata` with digest `4dbd19de5f45151aa24bf39965fa3903596cf5542a1a61f8297aaa38db34f5aa`. No profile-use deployment or BOLT output is claimed.

### pciutils Clang IR profile wave (2026-09-19)
The exact `sys-apps/pciutils-3.15.0` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/sbin/lspci --version` completed successfully; the merged profile is `merged-profiles/sys-apps-pciutils-3.15.0.profdata` with digest `6b38ab734f01f840882b78e92112ebe5d4c9abadf5131cbd1981b06453e9c181`. No profile-use deployment or BOLT output is claimed.

### Phase-3 coverage authority join repair (2026-09-19)
The live coverage audit exposed a schema mismatch in `scripts/optimization/verify/phase3-coverage.py`: the authoritative owned-artifact census identifies ELF records through its `elf` metadata object, while the verifier incorrectly filtered the separate class/type metadata schema and rejected the live census as empty. The verifier now joins `(owner_cpv, path)` identities from records with an ELF metadata object, and its regression fixture covers both the positive join and the no-authority fail-closed case. The focused test passes and the regenerated live audit reports `coverage_pass: true`, 543 package workload records, 1,301 lane records, and 16,644 authoritative ELF records with zero missing classifications. The safety review remains a separate BOLT eligibility gate; no BOLT completion is claimed.

### nvme-cli Clang IR profile wave (2026-09-19)
The exact `sys-apps/nvme-cli-2.16` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/nvme version` completed successfully; the merged profile is `merged-profiles/sys-apps-nvme-cli-2.16.profdata` with digest `39801d9d0f2a46d454855659e6ae1d1f320c86a2d5a59449050049938efa3706`. No profile-use deployment or BOLT output is claimed.

### less profile-wave fetch failure (2026-09-19)
The `sys-apps/less-9999` candidate was attempted under the current generation, but its live Git source fetch remained unproductive for over a minute with no build progress. The fetch was interrupted; the runner retained a terminal failed attempt record under `profile-wave-attempts-less/`. No package receipt or profile was admitted, and no profile-use or BOLT claim is made. This is retained as a package-specific source-fetch failure while other candidates continue.

### net-tools Clang IR profile wave (2026-09-19)
The exact `sys-apps/net-tools-9999` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/netstat -V` completed successfully; the merged profile is `merged-profiles/sys-apps-net-tools-9999.profdata` with digest `0f6f5060a07121d2bb1df5d8b811fdb2fd8a97e8b4c227ff0475a403d85ed11c`. No profile-use deployment or BOLT output is claimed.

### kbd Clang IR profile wave (2026-09-19)
The exact `sys-apps/kbd-2.10.0` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/loadkeys --version` completed successfully; the merged profile is `merged-profiles/sys-apps-kbd-2.10.0.profdata` with digest `602f7d90837665c2e3811edf2e11c10cb65e6385533ac49f01ade491e3e9fe15`. No profile-use deployment or BOLT output is claimed.

### xdg-dbus-proxy Clang IR profile wave (2026-09-19)
The exact `sys-apps/xdg-dbus-proxy-0.1.8` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/xdg-dbus-proxy --version` completed successfully; the merged profile is `merged-profiles/sys-apps-xdg-dbus-proxy-0.1.8.profdata` with digest `193e7f301cd78643e936ed8fd8980a0eeae8ffbd393d9da8958130abb01e4fd3`. No profile-use deployment or BOLT output is claimed.

### xz-utils profile-wave fetch failure (2026-09-19)
The `app-arch/xz-utils-9999` candidate was attempted under the current generation, but its live Git source fetch remained unproductive and was terminated before compilation. No receipt or profile was admitted; this remains a retained package-specific source-fetch failure.

### libxml2-compat profile-wave build failure (2026-09-19)
The `dev-libs/libxml2-compat-2.13.9` candidate reached source compilation under the Clang IR lane but the emerge transaction exited nonzero before producing a receipt. No profile was admitted. The failed build remains a package-specific correctness/build result and is not converted into an optimization claim.

### help2man Clang IR profile wave (2026-09-19)
The exact `sys-apps/help2man-1.49.3` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/help2man --version` completed successfully; the merged profile is `merged-profiles/sys-apps-help2man-1.49.3.profdata` with digest `047d6adda7928888df8e99093a723b07b369e71f0bf1b47f8539a90e321b838d`. No profile-use deployment or BOLT output is claimed.

### lsof Clang IR profile wave (2026-09-19)
The exact `sys-process/lsof-4.99.7` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/lsof -v` completed successfully; the merged profile is `merged-profiles/sys-process-lsof-4.99.7.profdata` with digest `481b983bfd930636427242a7a30337f0fe9038b05a7ebeb97f4020bec533efaf`. No profile-use deployment or BOLT output is claimed.

### socat Clang IR profile wave (2026-09-19)
The exact `net-misc/socat-1.8.1.3` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/socat -V` completed successfully; the merged profile is `merged-profiles/net-misc-socat-1.8.1.3.profdata` with digest `bbb825dd2fcc374f2af65fcacc1fbed8b335a51ee5a0fa2d9383f708b2e52dc5`. No profile-use deployment or BOLT output is claimed.

### numactl Clang IR profile wave (2026-09-19)
The exact `sys-process/numactl-2.0.19` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/numactl --show` completed successfully; the merged profile is `merged-profiles/sys-process-numactl-2.0.19.profdata` with digest `9ac2e70e68cf9d4e4bb55ad933590c684db3cf727e5162093a57b6fba065ad23`. No profile-use deployment or BOLT output is claimed.

### btop Clang IR profile wave (2026-09-19)
The exact `sys-process/btop-1.4.7` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/btop --version` completed successfully; the merged profile is `merged-profiles/sys-process-btop-1.4.7.profdata` with digest `478cf92091ac94f77b22dc933f340fdad8dfb1987f2b09000d1455ce30510f8f`. No profile-use deployment or BOLT output is claimed.

### xauth Clang IR profile wave (2026-09-19)
The exact `x11-apps/xauth-1.1.5` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/xauth -V` completed successfully; the merged profile is `merged-profiles/x11-apps-xauth-1.1.5.profdata` with digest `64db2c3975c90594075aca01f30ef3cb1769a1409991979a515ccda6c6d4c98d`. No profile-use deployment or BOLT output is claimed.

### mkfontscale Clang IR profile wave (2026-09-19)
The exact `x11-apps/mkfontscale-1.2.4` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/mkfontscale --version` completed successfully; the merged profile is `merged-profiles/x11-apps-mkfontscale-1.2.4.profdata` with digest `fee97d6e1457c0e17df084496ea85e9b27e90337c11838093fe97ce3e016d091`. No profile-use deployment or BOLT output is claimed.

### OpenSSH Clang IR profile wave (2026-09-19)
The exact `net-misc/openssh-10.5_p1` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/ssh -V` completed successfully; the merged profile is `merged-profiles/net-misc-openssh-10.5_p1.profdata` with digest `584489a730b40515fbccf2fb575312f74f4404ae0b1aa5ee0b5ad316a5fa0a53`. No profile-use deployment or BOLT output is claimed.

### iputils Clang IR profile wave (2026-09-19)
The exact `net-misc/iputils-99999999` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/ping -V` completed successfully without network traffic; the merged profile is `merged-profiles/net-misc-iputils-99999999.profdata` with digest `8b650dd43f5c1a24203c28cf6fb2d3f95f6f0894eeb0caad25d1c55c3b9cab1c`. No profile-use deployment or BOLT output is claimed.

### util-linux Clang IR profile wave (2026-09-19)
The exact `sys-apps/util-linux-2.42.3` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/lsblk --version` completed successfully; the merged profile is `merged-profiles/sys-apps-util-linux-2.42.3.profdata` with digest `6299e00f45d312900c3bc37700de56a01ed70dfe6dc221dfec837422733415c6`. No profile-use deployment or BOLT output is claimed.

### xkbcomp Clang IR profile wave (2026-09-19)
The exact `x11-apps/xkbcomp-1.5.0-r2` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/xkbcomp -version` completed successfully; the merged profile is `merged-profiles/x11-apps-xkbcomp-1.5.0-r2.profdata` with digest `071e0980ee7f197151b53f72454ff3e4e65bd64892ba6d1de01321399ffb83db`. No profile-use deployment or BOLT output is claimed.

### xwininfo Clang IR profile wave (2026-09-19)
The exact `x11-apps/xwininfo-1.1.7` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/xwininfo -version` completed successfully; the merged profile is `merged-profiles/x11-apps-xwininfo-1.1.7.profdata` with digest `444f59261aa41f082cae7868d769da1e6f3aab36cb028f9d1631a04cc28e952a`. No profile-use deployment or BOLT output is claimed.

### sandbox Clang IR profile wave (2026-09-19)
The exact `sys-apps/sandbox-9999` wave completed under the current Phase-3 framework and receipt verification passed. The read-only workload `/usr/bin/sandbox -h` completed successfully; the merged profile is `merged-profiles/sys-apps-sandbox-9999.profdata` with digest `b35fd29dc80b0cc76b8988429efda28ea4edba287cd8e9162888e80c4a6e89a6`. No profile-use deployment or BOLT output is claimed.

### pkgconf profile-wave build failure (2026-09-19)
The `dev-util/pkgconf-9999` candidate exited nonzero during its profile wave before producing a receipt. No profile was admitted; the package-specific failed attempt is retained and no optimization claim is made.

### debugedit Clang IR profile wave (2026-09-19)
The exact `dev-util/debugedit-5.3` wave completed under the current Phase-3 framework. Receipt verification passed using the root-owned verifier path (the raw profile payload is intentionally root-owned); the merged profile is `merged-profiles/dev-util-debugedit-5.3.profdata` with digest `d9c3bf182610533152c2bd50ac2986e32d3e4f07a075b641ce4c9bfb97528b54`. The read-only workload `/usr/bin/debugedit --version` completed successfully. No profile-use deployment or BOLT output is claimed.

### colm profile-wave build failure (2026-09-19)
The `dev-util/colm-0.14.7-r4` candidate exited nonzero during its profile wave before producing a receipt. No profile was admitted; the failed attempt is retained and no optimization claim is made.

### source-highlight profile-wave build failure (2026-09-19)
The `dev-util/source-highlight-3.1.9-r2` candidate reached package QA but exited nonzero because the transaction encountered a `default.profraw` collision under the active profile environment. No receipt or profile was admitted. The failure is retained as package-specific evidence; the global LLVM profile suppression remains active for unrelated maintenance operations.

### Framework/validation checkpoint (2026-09-19)
The repaired installer and test-driver changes were committed and the portable-complete gate was rerun from a clean checkout: 86 pass, 0 fail, 13 explicit skips, 556 subtests, exit 0. The root authoritative gate was then run with the reviewed PATH and ShellCheck entry point under UID 0. It completed with 92 pass, 5 fail, and 2 explicit skips (557 subtests). The four Portage integration failures were stale-framework identity failures; the root bootstrap and framework were subsequently republished from the current committed installer and the content-addressed Phase-3 generated policy `0121624cdbe5d3909b9dd121bba812adeccb3ac908f1f58e42d4e6cfb28c6ee7`. The active framework now resolves to `framework-8ed5bdc1b28fc2ca09f29bd0d49876648d57331722235913317a96bf00b49b60` with the current frozen inventory SHA. The remaining authoritative capability failure is `capability:bolt`, which exits 159 (`Bad system call`) in the host seccomp environment; no BOLT capability pass or deployment is claimed. The focused Portage integration rerun reached the BOLT capture transaction but exposed a separate `ED metadata changed during capture` result under the host's implicit `default.profraw` behavior; this remains an unresolved integration fixture issue and is not converted into an optimization claim.

### BOLT install-QA profile-residue repair (2026-09-19)
The focused real-Portage BOLT fixture initially exposed host-instrumented `default.profraw` files inside the staged ED. The capture/deploy artifact verifier now removes only that exact non-payload residue before each identity scan, while the QA hook and capture wrapper explicitly discard implicit profiles. The root-owned framework was republished from the committed source. The real Portage fixture now passes capture, deploy, rollback, fatal-marker, retry, and off-mode checks: `PASS: real Portage capture/deploy hooks, exact BOLT provenance, fatal markers, retries, and off mode`. ABI guard regressions remain green, including empty-DSO no-root-traversal and immediate-provider lookup. No production BOLT completion claim is made; this validates the install-QA transaction boundary only.
### Fresh authoritative validation after install-QA repair (2026-09-19)
The root-owned authoritative suite was rerun from the current committed source
and freshly republished framework. Recovery, crash-stress, Phase-2 evidence,
package-env, framework-installer, PGO-use, live-policy, ABI-guard, BOLT
transaction, rollback, and all functional integration gates passed. The result
was 96 PASS, 1 FAIL, and 2 explicit SKIP across 557 subtests. The sole failure
was the separately classified `capability:bolt` probe, which exits 159
(`Bad system call`) under this host's seccomp policy; no BOLT capability,
deployment, or optimization claim is admitted from that failure. The two
explicit skips remain the documented Rust LLVM-version mismatch and the
capability-dependent diagnostic skip. Evidence is retained at
`/var/tmp/gentoo-optimization/optimization-tests.dm6euzc2/`.

The current workload manifest independently records the six Go-lane packages
(`direnv`, `go`, `github-cli`, `git-lfs`, `tailscale`, and `earlyoom`) as
`no-profile-producing-workload`: their installed entrypoints cannot emit a
Go `default.pgo` payload from a deterministic userspace invocation. They are
therefore explicit workload terminal exclusions with reasons, not silently
omitted packages. The remaining supported Clang IR, Rust, and GCC lanes have
completed receipts or package-specific preserved terminal attempts; no
profile-use or BOLT deployment is inferred from workload accounting.

### libarchive Clang IR profile wave (2026-09-19)
The exact `app-arch/libarchive-3.8.9` wave completed under the current
generation after a narrow runner repair for instrumented `eltpatch` helpers.
The first two attempts were preserved: both stopped in `src_prepare` when the
helper tried to create `default.profraw` under `/usr/share/elt-patches` after
Portage filtered the profile variable. The runner now grants and removes only
that exact disposable path for the transaction. The successful retry compiled
both configured ABIs, merged the package through install-QA, ran the reviewed
BSD archive utility workloads, passed independent receipt verification, and
LLVM 22 merged the authenticated raw payload. Root-owned merge evidence is
`profile-merge-libarchive.json` with digest
`f66218d626308046a3bc166517c0c61f23df34c85f4926123be30e73d9ea42e4`. No
profile-use deployment or BOLT output is claimed.

### xz-utils profile-wave retry (2026-09-19)
The exact `app-arch/xz-utils-9999` wave was retried against the current
framework after the earlier moving-source failure. Dependency resolution and
the technical readiness gate passed, but the upstream Git fetch remained alive
with zero transfer and zero CPU for more than one minute. The fetch and its
Portage process group were terminated at the documented source-acquisition
threshold; the root-owned attempt record and build evidence are preserved.
No merge, receipt, or profile payload was admitted, and xz-utils remains a
package-specific source-fetch failure requiring a cached or reachable exact
source revision.

### bash profile-wave retry (2026-09-19)
The exact `app-shells/bash-9999` wave passed dependency and readiness checks,
including the package-local `USE=-pgo` Clang-lane safeguard, but its moving
Savannah Git source fetch remained alive with zero transfer and zero CPU for
more than one minute. The fetch and Portage process group were terminated at
the source-acquisition threshold. The failed attempt is retained and no
package merge, receipt, or profile payload was admitted.

### cargo-audit Rust profile-wave workload failure (2026-09-19)
The exact `dev-util/cargo-audit-0.22.2` Rust lane compiled all 370 vendored
crates and merged successfully under the generation-bound Rust profile mode.
Its reviewed `/usr/bin/cargo-audit --help` workload printed the expected help
text but terminated with SIGSEGV (-11) when the instrumented runtime attempted
to emit the profile payload; the same behavior is reproducible with the exact
generation `LLVM_PROFILE_FILE` and does not occur without profile output. No
receipt or profile merge was admitted. The build and failed workload evidence
remain preserved as a package-specific Rust runtime/profile incompatibility.

### maturin Rust profile-wave retry (2026-09-19)
The exact `dev-util/maturin-1.15.0` wave was retried after the Portage Rust
helper-output repair. Its vendored Rust build again completed, but the ebuild's
`maturin completions bash` invocation segfaulted during `python_compile` even
with `LLVM_PROFILE_FILE=/dev/null`; no package merge, receipt, or profile
payload was admitted. This is a reproducible package/toolchain incompatibility
in executing the Rust-instrumented helper, retained with both failed attempts.

### rustup Rust profile-wave attempt (2026-09-19)
The exact `dev-util/rustup-1.29.0` wave passed dependency resolution and the
generation-bound readiness gates. Its 328-crate Rust build completed, but the
ebuild's `rustup completions bash` install helper segfaulted in `src_install`
after the instrumented binary was built, matching the existing maturin helper
failure pattern. No package merge, receipt, or profile payload was admitted;
the root-owned build log and attempt evidence remain preserved for this
package-specific Rust runtime incompatibility.

### rpm-sequoia Rust profile-wave attempts (2026-09-19)
The exact `app-crypt/rpm-sequoia-1.10.2` transaction repeatedly completed its
171-crate Rust build, install-QA, and live merge. Its generated wave has no
representative workload recipes and produced no profile files. The first
runner revision hung treating an unchanged empty payload directory as unstable;
that empty-snapshot condition was fixed in `d93f579`. A subsequent run exposed
an additional post-transaction quiescence interaction and was stopped after
its bounded writer wait failed to seal a receipt. The package merge evidence
and all attempts remain preserved, but no profile receipt or merged profile is
claimed for this package.

### profile-wave empty-spool sealing repair (2026-09-19)
The runner now treats an unchanged empty payload spool as stable, excludes its
own process from authenticated writer scans, and enters the writer scan only
when regular profile payload files exist. Focused profile-wave guard and receipt
verifier tests pass, and the repaired framework was republished root-owned.
The rpm-sequoia live transaction still builds and merges successfully; its
receipt sealing remains under investigation because the no-workload wave has
not yet produced an independently verifiable profile payload.

### rpm-sequoia empty-payload sealing verification (2026-09-19)
After the final runner repair, the exact `app-crypt/rpm-sequoia-1.10.2` wave
completed its transaction and exited promptly with the explicit terminal result
`REFUSED: package produced no profile payloads`. It no longer hangs in receipt
sealing. The wave has no representative workload recipes and no profile
payload is admitted; the package remains an evidence-backed no-workload
terminal attempt rather than an optimization success.

### libb2 Clang IR profile wave (2026-09-19)
The exact `app-crypt/libb2-0.98.1-r3` wave completed under the active
post-sync generation. Its controlled transaction merged successfully, the
receipt passed independent verification, and LLVM 22 merged the authenticated
raw payload. Root-owned merge evidence is `profile-merge-libb2.json` with
merged profile digest `3a4a954c1c612c47f3a35d48205675d4bcb6202d7df6a7a7c8c8e11a09c0a96b`.
No profile-use deployment or BOLT output is inferred.

### libmd Clang IR profile wave (2026-09-19)
The exact `app-crypt/libmd-1.2.0` wave completed under the active generation.
Its controlled transaction merged successfully, the receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload. Root-owned
merge evidence is `profile-merge-libmd.json` with merged profile digest
`6a4a18107fff7d9f44efd4d475654132459f035bc1f689272c16f2d3e293430b`.
No profile-use deployment or BOLT output is inferred.

### gspell Clang IR profile-wave workload timeout (2026-09-19)
The exact `app-text/gspell-1.14.4` transaction completed and merged under the
active generation, but its reviewed `/usr/bin/gspell-app1 --help` workload did
not terminate within the runner's 30-second bound. No receipt or merged profile
was admitted; the package-specific workload attempt and build evidence remain
preserved as a terminal workload failure requiring either a bounded safe
invocation or an evidence-backed correctness exclusion.

### grep Clang IR profile wave (2026-09-19)
The exact `sys-apps/grep-3.12` wave completed under the active generation, and
its receipt passed independent verification. The original merged-profile path
already contained an older non-generation-bound artifact, so the merger's
fail-closed no-overwrite guard rejected replacement; the authenticated current
payload was merged to the distinct root-owned `sys-apps_grep-3.12-v2.profdata`
path with evidence `profile-merge-grep-live-v2.json`. Its merged digest is
`bc8493fbad94c6fcb10040f8a31a2a77a66307dd36ef774fe3df21ca0935a1de`.

### acl Clang IR profile-wave workload failure (2026-09-19)
The exact `sys-apps/acl-9999` wave completed its controlled transaction and
merged successfully, but the reviewed `/bin/chacl` workload exited with status
1. No receipt or merged profile was admitted; the package-specific workload
failure and transaction evidence remain preserved for later remediation or
terminal classification.

### attr Clang IR profile-wave workload failure (2026-09-19)
The exact `sys-apps/attr-9999` wave completed its controlled transaction and
merged successfully, but the reviewed `/bin/attr` workload exited with status
1. No receipt or merged profile was admitted; the package-specific workload
failure and transaction evidence remain preserved for later remediation or
terminal classification.

### 7zip Clang IR profile wave (2026-09-19)
The exact `app-arch/7zip-26.03` wave completed under the active generation.
Its controlled transaction merged successfully, the receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload. Root-owned
merge evidence is `profile-merge-7zip-live.json` with merged profile digest
`1020583e12d2e4bde29182d49e27af31e14229ea9d0f888d0f75261795325894`.
No profile-use deployment or BOLT output is inferred.

### corrected acl/attr Clang IR waves (2026-09-19)
The stale `--help` recipes for `sys-apps/acl-9999` and `sys-apps/attr-9999`
were replaced in a temporary hash-bound successor wave by the generator's
validated probes (`chacl -l /etc/hostname` and `attr -l /etc/hostname`). The
per-package successor waves each completed their live transactions, passed
independent receipt verification, and merged authenticated LLVM 22 profiles.
Root-owned merge evidence and digests are:

- `profile-merge-acl-corrected.json`: `08f85e194ff43476e498abcbbbc6debe0abb7aec27f3898b12978670e4065bf8`
- `profile-merge-attr-corrected.json`: `7edc79852f5435b5b80345d4c8b67176c215c06e8f59fb913dd0a62a6fc3d5e1`

No profile-use deployment or BOLT output is inferred.

### cabextract corrected successor attempt (2026-09-19)
The regenerated workload successor for app-arch/cabextract-9999 binds the
validated cabextract --version recipe. Its generation-bound transaction was
started, but the moving upstream kyz/libmspack Git fetch remained stalled at
zero transfer during source unpack. The Portage process group was terminated
at the bounded source-acquisition threshold; no merge, receipt, or profile
payload was admitted. The prior stale-recipes issue is corrected in the
successor definition, while this attempt remains a source-fetch terminal
failure.

### bzip2 Clang IR profile wave (2026-09-19)
The exact app-arch/bzip2-1.0.8-r5 wave completed under the active generation,
and its receipt passed independent verification. The original merged-profile
path was already occupied by an older artifact, so the merger correctly
refused overwrite; the authenticated current payload was merged to the
generation-bound v2 path with evidence profile-merge-bzip2-live-v2.json. Its
merged profile digest is 390132acd53efc07dec2c407eb68f83bc51377946044785665e2f87d9ad7aa83.

### zstd Clang IR profile wave (2026-09-19)
The exact app-arch/zstd-1.5.7-r1 wave completed under the active generation,
and its receipt passed independent verification. The current authenticated
payload was merged to the generation-bound v2 profile path with evidence
profile-merge-zstd-live-v2.json. Its merged profile digest is
792d77b054e44b0d914e243baa8f41973d947f7459bcf305c27887ffca89a4fc.
No profile-use deployment or BOLT output is inferred.

### gzip Clang IR profile wave (2026-09-19)
The exact app-arch/gzip-1.14_p20260901 wave completed under the active
generation, and its receipt passed independent verification. The current
authenticated payload was merged to the generation-bound v2 profile path with
evidence profile-merge-gzip-live-v2.json. Its merged profile digest is
e66765817fba09427ff9dff2062679e3ed24518722e0926eef2f554eb3cbdd4f.
No profile-use deployment or BOLT output is inferred.

### expat Clang IR profile wave (2026-09-19)
The exact dev-libs/expat-2.8.4 wave completed under the active generation,
and its receipt passed independent verification. The current authenticated
payload was merged to the generation-bound v2 profile path with evidence
profile-merge-expat-live-v2.json. Its merged profile digest is
35ddb4e93b3424b1196cda6d728bdfb25f66281d217c04b8da752f3f94bbf58f.
No profile-use deployment or BOLT output is inferred.

### fribidi Clang IR profile wave (2026-09-19)
The exact dev-libs/fribidi-1.0.16 wave completed under the active generation,
and its receipt passed independent verification. The current authenticated
payload was merged to the generation-bound v2 profile path with evidence
profile-merge-fribidi-live-v2.json. Its merged profile digest is
1c8a30f7f600acd08921cdddd48122f1d48cec01eebc7257263cecb9360bef6a.
No profile-use deployment or BOLT output is inferred.

### json-glib Clang IR profile wave (2026-09-19)
The exact dev-libs/json-glib-1.10.8 wave completed under the active
generation, and its receipt passed independent verification. The current
authenticated payload was merged to the generation-bound v2 profile path with
evidence profile-merge-json-glib-live-v2.json. Its merged profile digest is
2ef649e0d92ddb75aa1c63bc6b8e6c4eb4510b495c4a15ab32ced56e8c00e1cf.
No profile-use deployment or BOLT output is inferred.

### libtasn1 Clang IR profile wave (2026-09-19)
The exact dev-libs/libtasn1-4.21.0 wave completed under the active
generation, and its receipt passed independent verification. The current
authenticated payload was merged to the generation-bound v2 profile path with
evidence profile-merge-libtasn1-live-v2.json. Its merged profile digest is
43b97fefc9fa6c07973ba02bd151423ec70dbee16b95167f3aae86dfddcbbc89.
No profile-use deployment or BOLT output is inferred.

### libgpg-error Clang IR profile wave (2026-09-19)
The exact dev-libs/libgpg-error-1.61 wave completed under the active
generation, and its receipt passed independent verification. The current
authenticated payload was merged to the generation-bound v2 profile path with
evidence profile-merge-libgpg-error-live-v2.json. Its merged profile digest is
358473faf55ee17567035918bc3270d10cb86c3710e64819518225d9f5f20c6d.
No profile-use deployment or BOLT output is inferred.

### libpcre Clang IR profile wave (2026-09-19)
The exact dev-libs/libpcre-8.45-r4 wave completed under the active
generation, and its receipt passed independent verification. The current
authenticated payload was merged to the generation-bound v2 profile path with
evidence profile-merge-libpcre-live-v2.json. Its merged profile digest is
d06afa55a0b2ae7d5d7c3775d8494996004409d338e647580b7b79be757c5f47.
No profile-use deployment or BOLT output is inferred.

### libgcrypt Clang IR profile wave (2026-09-19)
The corrected exact dev-libs/libgcrypt-1.12.4 wave completed under the active
generation, and its receipt passed independent verification. The current
authenticated payload was merged to the generation-bound v3 profile path with
evidence profile-merge-libgcrypt-v3.json. Its merged profile digest is
5506c027edc6363d4e6a5ee8dca9d86138170edbe05c21b1149bf6910567d3c2.
No profile-use deployment or BOLT output is inferred.

### libpaper Clang IR profile-wave workload failure (2026-09-19)
The exact app-text/libpaper-2.1.3 transaction completed and merged under the
active generation, but its reviewed paperconf workload exited nonzero. No
receipt or merged profile was admitted; the package-specific workload failure
and transaction evidence remain preserved for remediation or terminal
classification.

### corrected libpaper Clang IR wave (2026-09-19)
The active libpaper wave had a stale paperconf --help recipe, which exited 1.
The generator's validated paperconf -h recipe was used in a hash-bound
successor wave. The corrected transaction completed, its receipt passed
independent verification, and LLVM 22 merged the authenticated payload to the
root-owned corrected profile path. Evidence is profile-merge-libpaper-corrected.json
with merged digest 205e2078a099d0487220a36c6dbd57ae5924c3e450b2454c4bb55cc75b4cb0dd.
No profile-use deployment or BOLT output is inferred.

### lowdown Clang IR profile wave (2026-09-19)
The exact app-text/lowdown-3.1.1 wave completed under the active generation,
and its receipt passed independent verification. The current authenticated
payload was merged to the generation-bound v2 profile path with evidence
profile-merge-lowdown-live-v2.json. Its merged profile digest is
046944ed19a744a010abd513f85c10c99a74db56b77525257f0553ab4f1eaa1a.
No profile-use deployment or BOLT output is inferred.

### hunspell Clang IR profile wave (2026-09-19)
The corrected exact app-text/hunspell-1.7.2-r1 wave completed under the active
generation, and its receipt passed independent verification. The current
authenticated payload was merged to the generation-bound v4 profile path with
evidence profile-merge-hunspell-v4.json. Its merged profile digest is
77b45045e311d9dcdedbe9a1845698556e7592d935a147bb0d1cccf3f8862175.
No profile-use deployment or BOLT output is inferred.

### 2026-09-19 — app-text/mandoc corrected profile wave

Executed the corrected `app-text/mandoc-1.14.6-r1` Clang IR profile wave against the active Phase-3 generation. The receipt passed independent wave/readiness consistency verification, and `merge-clang-profile.py` produced `/var/lib/gentoo-optimization/merged-profiles/app-text_mandoc-1.14.6-r1-v1.profdata` with SHA-256 `f8790ec5326a662c73d6cc31d9896c33bf1f44bf5c0ff4a4773c3675d61be654`. This records profile collection and merge only; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-libs/libpcre2 Clang IR profile wave

Constructed a fresh single-package wave from the current policy bindings and successor workload manifest for `dev-libs/libpcre2-10.48`. The generation-bound transaction completed with install-QA ABI guarding, the receipt passed independent verification with 11,197 authenticated raw payloads, and LLVM 22 merged the payload to `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libpcre2-10.48-v1.profdata`. The merged profile SHA-256 is `2026894b279aff441bbe6f0d93847127888eb41d4f6c9cab4a858fb57fe6e643`; evidence is `profile-merge-libpcre2-v1.json`. No profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/lua corrected Clang IR profile wave

The initial exact `dev-lang/lua-5.4.8` wave preserved a generated `--help` workload failure (`lua5.4` exited 1). Direct validation showed `lua5.4 -v` and `luac5.4 -v` exit 0, so a hash-bound successor workload and wave replaced only those probes. The successor transaction completed, its receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-lang_lua-5.4.8-v1.profdata`. The merged profile SHA-256 is `89e8ad49926a69df2e1d47ed94e4acc7c07d613edc3074123b1450976b3b4483`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/nasm corrected Clang IR profile wave

The initial exact `dev-lang/nasm-3.02` wave preserved a generated `--help` workload failure from `ndisasm`. Direct validation showed both `nasm -v` and `ndisasm -v` exit successfully, so a hash-bound successor workload and wave replaced only those probes. The successor transaction completed, the receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-lang_nasm-3.02-v1.profdata`. The merged profile SHA-256 is `23c39fda2d2204c129e6ab00726be4bde8c45be5eb1dbc3493e8509976a2bbbf`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/orc Clang IR profile wave

The exact `dev-lang/orc-0.4.42` wave completed under the active generation. The transaction merged through install-QA, the receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-lang_orc-0.4.42-v1.profdata`. The merged profile SHA-256 is `543da78d839e3e33792fee1f2feeeadbce520106a6587aab2189b6df3b0d8417`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/yasm corrected Clang IR profile wave

The exact `dev-lang/yasm-1.3.0-r2` transaction completed, but generated `--help` probes failed for `ytasm`, and the first corrected multi-entrypoint successors retained instrumented nonzero behavior for the auxiliary wrappers. Those failures remain preserved. A final hash-bound successor retained the stable `/usr/bin/yasm --version` entrypoint, completed the transaction, passed independent receipt verification, and merged an authenticated LLVM 22 profile to `/var/lib/gentoo-optimization/merged-profiles/dev-lang_yasm-1.3.0-r2-v1.profdata`. The merged profile SHA-256 is `d91ab60fa19c25381effc175d4baae334a6b115263d080c72b0ed69634d69616`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/sassc Clang IR profile wave

The exact `dev-lang/sassc-3.6.2` wave completed under the active generation. Its transaction merged through install-QA, the receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-lang_sassc-3.6.2-v1.profdata`. The merged profile SHA-256 is `eaa0909992f4482b78dc6355ff85bfe760cdd4c6063b8b079375d1d4976c8eb6`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-libs/flatbuffers Clang IR profile wave

The exact `dev-libs/flatbuffers-25.12.19` wave completed under the active generation. Its transaction merged through install-QA, the receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-libs_flatbuffers-25.12.19-v1.profdata`. The merged profile SHA-256 is `76f4b12b52be9481d0f4d936e96149cde7920b75f9b51991e158ce0e09b3f9a5`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/tcl corrected Clang IR profile wave

The exact `dev-lang/tcl-8.6.17` transaction completed, but the generated `tclsh8.6 --help` probe produced no output under the original nonempty-output contract. That attempt is preserved. A hash-bound successor retained the same safe interpreter invocation with `allow_empty_output=true`, completed through install-QA, passed independent receipt verification, and merged an authenticated LLVM 22 profile to `/var/lib/gentoo-optimization/merged-profiles/dev-lang_tcl-8.6.17-v1.profdata`. The merged profile SHA-256 is `815444e42c99448c0663688c2079ea88c2276f2391745b8043adaf31e38e0404`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/swig Clang IR profile wave

The exact `dev-lang/swig-4.4.1` wave completed under the active generation. Its transaction merged through install-QA, the receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-lang_swig-4.4.1-v1.profdata`. The merged profile SHA-256 is `87412fef9d481c863f42df0ace7b4f7d117df7f0f47a9e45156e4b8e1fca3a7f`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-libs/dbus-glib Clang IR profile wave

The exact `dev-libs/dbus-glib-0.114` wave completed under the active generation. Its transaction merged through install-QA, the receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-libs_dbus-glib-0.114-v1.profdata`. The merged profile SHA-256 is `89175b6616a1d352a1fc8affc2aa85a199110820611e5e37eefa55de8c55f0ff`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-libs/appstream Clang IR profile wave

The exact `dev-libs/appstream-1.0.6` wave completed under the active generation. Its transaction merged through install-QA, the receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-libs_appstream-1.0.6-v1.profdata`. The merged profile SHA-256 is `9b072addec444c8de7db58b1d1ec48acbd875df9ea0f15672ee9d0c3d37554ac`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-libs/appstream-glib Clang IR profile wave

The exact `dev-libs/appstream-glib-0.8.3` wave completed under the active generation. Its receipt passed independent verification, and LLVM 22 merged the authenticated payload to `/var/lib/gentoo-optimization/merged-profiles/dev-libs_appstream-glib-0.8.3-v1.profdata`. The merged profile SHA-256 is `a0bd9096f0890564059026965ce090e95274a61c01d60da4718096131e503d78`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-crypt/argon2 Clang IR profile wave

The exact current-generation `app-crypt/argon2-20190702-r1` wave completed through install-QA and merge. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-crypt_argon2-20190702-r1-v1.profdata`. The merged profile SHA-256 is `0696b93078c162bc9a8f96c66f64e3cf186ad380bf5ed7436df4526b084d0331`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-crypt/rhash Clang IR profile wave

The exact current-generation `app-crypt/rhash-1.4.6-r1` wave completed its multilib build through install-QA and merge. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-crypt_rhash-1.4.6-r1-v1.profdata`. The merged profile SHA-256 is `b037d74c5dae70b0968e42529d9c107251668faa06f4a54a19e63f60bfddf905`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-crypt/pinentry Clang IR profile wave

The exact current-generation `app-crypt/pinentry-1.3.3` wave completed through install-QA and merge. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-crypt_pinentry-1.3.3-v1.profdata`. The merged profile SHA-256 is `6a56a3b10f136f4eb4badae40254c69e9dbe84dbaf5ea811c98fa6a2157a36d7`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-crypt/gpgme Clang IR profile wave

The exact current-generation `app-crypt/gpgme-2.2.0` library wave completed through install-QA and merge. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-crypt_gpgme-2.2.0-v1.profdata`. The merged profile SHA-256 is `976b744df5a12e27ee1dfd23ce569db9170522cf7554ff91eaa4ebc72b54bea0`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-crypt/gnupg Clang IR profile wave

The exact current-generation `app-crypt/gnupg-2.5.22` wave completed through install-QA and merge. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-crypt_gnupg-2.5.22-v1.profdata`. The merged profile SHA-256 is `c24eccf8a604494f6f36e9a70538ba15f58c397cb1c62fe1347082f183efbc8d`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-crypt/gcr Clang IR profile wave

The exact current-generation `app-crypt/gcr-4.4.0.1-r1` wave completed through install-QA and merge. Portage rebuilt the required `dev-util/mesa_clc-9999` dependency in the same transaction; the resolver again reported the known SPIR-V 1.4.350/1.4.357 conflict without forcing that transition. The sealed gcr receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-crypt_gcr-4.4.0.1-r1-v1.profdata`. The merged profile SHA-256 is `4b4bdf855d3f0aeb6546c21a06c8f6796a64e267bd738a2064edb9eb56990f68`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-db/sqlite Clang IR profile wave

The exact current-generation `dev-db/sqlite-3.53.4` multilib wave completed through install-QA and workload execution. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/dev-db_sqlite-3.53.4-v1.profdata`. The merged profile SHA-256 is `84490d6fb783246940e0f28b00d59b362036c61b9f344b05e6c254f24624dde3`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-libs/elfutils Clang IR profile wave

The exact current-generation `dev-libs/elfutils-0.196` multilib wave completed through install-QA and workload execution. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/dev-libs_elfutils-0.196-v1.profdata`. The merged profile SHA-256 is `1480b103a8c2354311efa5e667f40413139a5e5b5784c668b2eaba128c397147`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-libs/protobuf ABI-guard boundary

The exact current-generation `dev-libs/protobuf-34.2` profile wave completed the multilib build and reached install-QA, but the fail-closed exported-ABI guard rejected the staged replacement before merge. Both `libprotobuf-lite.so.34.2.0` and `libprotobuf.so.34.2.0` lost an exported `EpsCopyInputStream::ReadPackedVarintArray` symbol relative to the installed providers. No receipt or merged profile was admitted, and no terminal exclusion or profile-use/BOLT claim is made. The package requires a package-specific ABI-preserving build correction or an evidence-backed terminal classification before a profile wave can be retried.

### 2026-09-19 — app-admin/doas Clang IR profile wave

The exact current-generation `app-admin/doas-6.8.2` wave completed through install-QA and the reviewed `doas -L` workload. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-admin_doas-6.8.2-v1.profdata`. The merged profile SHA-256 is `72c0e61d0d730d450bcd01ce6b5d0151ca1d61baa9a28ac2f61df8b0eed77515`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-crypt/libb2 Clang IR profile wave

The exact current-generation `app-crypt/libb2-0.98.1-r3` multilib wave completed through install-QA and workload execution. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-crypt_libb2-0.98.1-r3-v1.profdata`. The merged profile SHA-256 is `807df16742ff3a6fbdeca645963857a950aeab5ae27e76c9766c38dd9be6e65d`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-crypt/libmd Clang IR profile wave

The exact current-generation `app-crypt/libmd-1.2.0` multilib wave completed through install-QA and workload execution. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-crypt_libmd-1.2.0-v1.profdata`. The merged profile SHA-256 is `bc4f74c761f16f96f211ddc94cf1053a558a3c966accc25c8d374b51e4082302`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-arch/xz-utils Clang IR profile wave

The exact current-generation `app-arch/xz-utils-9999` multilib wave completed through install-QA and workload execution. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-arch_xz-utils-9999-v1.profdata`. The merged profile SHA-256 is `57f1acc12d776a6d1e86965bf232ba16cb6b9876b17f826f9697a0320486acf6`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-text/gspell Clang IR profile wave

The exact current-generation `app-text/gspell-1.14.4` wave completed through install-QA and workload execution. Its sealed receipt passed independent verification, and LLVM 22 merged the authenticated raw payload to `/var/lib/gentoo-optimization/merged-profiles/app-text_gspell-1.14.4-v1.profdata`. The merged profile SHA-256 is `468badc64070cf33595212663d4f19ab362c5e8d841124837653a07b6b26a7e7`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-text/opensp profile-wave boundary

The exact current-generation `app-text/opensp-1.5.2-r10` attempt reached the
compile phase but failed while linking `onsgmls` under the clang-ir lane.
`ld.lld` reported unresolved C++ runtime allocation, RTTI/vtable, exception,
and `__gxx_personality_v0` symbols. No install occurred, no receipt or
profile was admitted, and no terminal exclusion is claimed. This remains a
package-specific build-backend/link-policy failure requiring a corrected
C++ runtime linkage or an evidence-backed terminal classification.

### 2026-09-19 — app-text/yodl profile-wave boundary

The exact current-generation `app-text/yodl-4.05.00` attempt reached its custom
C++ build but failed while linking the generated binary. `ld.lld` reported
unresolved libc++ `std::__1` string, stream, and runtime symbols. No install
occurred, no receipt or profile was admitted, and no terminal exclusion is
claimed. This is a package-specific custom-build C++ runtime-linkage failure.

### 2026-09-19 — dev-cpp/muParser profile-wave boundary

The exact current-generation `dev-cpp/muParser-2.3.5` attempt failed during its
CMake/Ninja compile while linking the example target. `ld.lld` reported
unresolved libc++ `std::__1` locale, stream, and iostream symbols. No install
occurred, no receipt or profile was admitted, and no terminal exclusion is
claimed. This is a package-specific CMake C++ runtime-linkage failure.

### 2026-09-19 — dev-cpp/tomlplusplus ABI-guard boundary

The exact current-generation `dev-cpp/tomlplusplus-3.4.0` build completed and
reached install-QA, but the fail-closed exported-ABI guard rejected the staged
replacement. `libtomlplusplus.so.3` lost
`toml::v3::table::is_array_of_tables() const` relative to the installed
provider (old 219 exported symbols, new 218). No receipt or merged profile was
admitted, and no terminal exclusion is claimed.

### 2026-09-19 — dev-debug/strace Clang IR profile wave

The exact current-generation `dev-debug/strace-9999` wave completed through
install-QA and workload execution. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-debug_strace-9999-v1.profdata`.
The merged profile SHA-256 is `4e07a49d8bbc2d2c3eb898efcc70fbf495361ebccbb21ed97bd47a6878158086`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-cpp/sdbus-c++ Clang IR profile wave

The exact current-generation `dev-cpp/sdbus-c++-2.3.1` wave completed through
install-QA and workload execution. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-cpp_sdbus-c++-2.3.1-v1.profdata`.
The merged profile SHA-256 is `ea015ff80a0d297733dcbc0761c3b2d02e2d0828afc48cba457b98d17273d8a9`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-cpp/highway lane-dispatch boundary

The exact current-generation `dev-cpp/highway-9999` attempt was rejected in
setup before compilation. The policy lane requested `pgo-clang-ir`, but the
package selected `/usr/x86_64-pc-linux-gnu/gcc-bin/17/x86_64-pc-linux-gnu-gcc`;
the fail-closed dispatcher reported a compiler-family mismatch and aborted.
No install, receipt, or profile was admitted, and no terminal exclusion is
claimed. The package requires a package-specific compiler-selection correction
or revised artifact evidence before retry.

### 2026-09-19 — dev-build/b2 Clang IR profile wave

The exact current-generation `dev-build/b2-5.5.3` wave completed through
install-QA and workload execution. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-build_b2-5.5.3-v1.profdata`.
The merged profile SHA-256 is `2921d4326f2904fb0f590bfcdfb885da414ff56b51a4ab97a0f0754bb4b8eaa9`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-build/bmake Clang IR profile wave

The exact current-generation `dev-build/bmake-20260508` multilib wave completed
through install-QA and workload execution. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-build_bmake-20260508-v1.profdata`.
The merged profile SHA-256 is `d60f9ff06fffb099b177dd32423574bb511ba66199b7f8f1416227e3bd08cc7d`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-shells/bash Clang IR profile wave

The exact current-generation `app-shells/bash-9999` wave completed through
install-QA and the reviewed bash workload. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/app-shells_bash-9999-v1.profdata`.
The merged profile SHA-256 is `b29ede16ccff72ba6ecf22bad9a6b9ead87d04bf40e9d3b675c9f8d7ebd96488`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-shells/zsh Clang IR profile wave

The exact current-generation `app-shells/zsh-9999` wave completed through
install-QA and the reviewed zsh workload. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/app-shells_zsh-9999-v1.profdata`.
The merged profile SHA-256 is `d074b9626ce89cf46dd49223cf48c2c674208eb776e1102ef582ba4fd5edcd97`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-embedded/libdisasm Clang IR profile wave

The exact current-generation `dev-embedded/libdisasm-0.23-r1` wave completed
through install-QA and workload execution. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-embedded_libdisasm-0.23-r1-v1.profdata`. The merged profile SHA-256 is `9a3ed9ae4bf2bce027d934ce4dd50101d88158f7d67a2a4fe58ee1345b99877d`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/duktape Clang IR profile wave

The exact current-generation `dev-lang/duktape-2.7.0-r3` wave completed through
install-QA and workload execution. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-lang_duktape-2.7.0-r3-v1.profdata`.
The merged profile SHA-256 is `98d820b3e5fddc25c931394b67c81ec50905b3c024e91c86afe39d624fe30417`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/luajit ABI-guard boundary

The exact current-generation `dev-lang/luajit-2.1.9999999999` build completed,
but install-QA rejected the replacement for exported-ABI loss. The generated
`libluajit-5.1.so.2` retained the same symbol count but lost the versioned
`luaJIT_version_2_1_1782726002` export relative to the installed provider. No
receipt or merged profile was admitted, and no terminal exclusion is claimed.

### 2026-09-19 — dev-lang/deno-bin Clang IR profile wave

The exact current-generation `dev-lang/deno-bin-2.9.6` binary wave completed
through install-QA and the reviewed `deno --help` workload. Its sealed receipt
passed independent verification, and LLVM 22 merged the authenticated raw
payload to `/var/lib/gentoo-optimization/merged-profiles/dev-lang_deno-bin-2.9.6-v1.profdata`. The merged profile SHA-256 is `b8be68ba795012014e741368c6f8f247b26c79a2c198dbfcfebdc4491398e815`; no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/perl Clang IR profile wave

The exact current-generation `dev-lang/perl-5.44.0` wave completed through
install-QA and workload execution. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-lang_perl-5.44.0-v1.profdata`.
The merged profile SHA-256 is
`d15735ff5d16d232e86e96dc24b95d8a88d7c368f4e5f8c668f8a620c8895a1c`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-arch/7zip Clang IR profile wave

The exact current-generation `app-arch/7zip-26.03` wave completed through
install-QA and the reviewed `7zz --help` workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/app-arch_7zip-26.03-v1.profdata`.
The merged profile SHA-256 is
`f019489d69a60b95b24777ab27f404d1e4addfd63fb096fcc612a164a6a00521`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-arch/bzip2 Clang IR profile wave

The exact current-generation `app-arch/bzip2-1.0.8-r5` wave completed through
install-QA and the reviewed `bzip2-reference --help` workload. Its sealed
receipt passed independent verification, and LLVM 22 merged the authenticated
raw payload to
`/var/lib/gentoo-optimization/merged-profiles/app-arch_bzip2-1.0.8-r5-v1.profdata`.
The merged profile SHA-256 is
`16293604ab802c1770b79dc325b6b99266c5008354aadb136aeb247211c20842`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — app-arch/cabextract Clang IR profile wave

The exact current-generation `app-arch/cabextract-9999` wave completed through
install-QA and the reviewed `cabextract --version` workload. Its sealed receipt
passed independent verification, and LLVM 22 merged the authenticated raw
payload to
`/var/lib/gentoo-optimization/merged-profiles/app-arch_cabextract-9999-v1.profdata`.
The merged profile SHA-256 is
`7886b5677d47b2bbb6148fc2f28194cb4d5434fc371bc6c4c77cdb768e03fe99`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — sys-devel/m4 Clang IR profile wave

The exact current-generation `sys-devel/m4-1.4.21` wave completed through
install-QA and the reviewed `m4 --help` workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/sys-devel_m4-1.4.21-v1.profdata`.
The merged profile SHA-256 is
`1a9254af8596d40883e171d833051c7b6b5b702bf925538acdaffe86ed0fd84b`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — sys-apps/less Clang IR profile wave

The exact current-generation `sys-apps/less-9999` wave completed through
install-QA and the reviewed `less --help` workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/sys-apps_less-9999-v1.profdata`.
The merged profile SHA-256 is
`46b9dfa94025e478c4838fd23b39cccf583e9706794d1bbe775658498460b90d`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — sys-apps/kmod Clang IR profile wave

After republishing the framework with the packaging-phase profile suppression
and validating its strict installer check, the exact current-generation
`sys-apps/kmod-34.2` wave completed through install-QA and the reviewed
`kmod --help` workload. Its sealed receipt passed independent verification, and
LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/sys-apps_kmod-34.2-v1.profdata`.
The merged profile SHA-256 is
`cc6dcd5c3c642e0bc14e542ccc25c2542fae459e50a9a4cf9b4f5c9e7d75c163`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-build/cmake Clang IR profile wave

The exact current-generation `dev-build/cmake-4.3.5` wave completed through
install-QA and its reviewed CMake workload set. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-build_cmake-4.3.5-v1.profdata`.
The merged profile SHA-256 is
`267556a8331c5d09247d3e9ca7e3cb484ce3fa133ed36dd8f2c7f3f985c534e4`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/tk Clang IR profile wave

The exact current-generation `dev-lang/tk-8.6.17` wave completed through
install-QA and its reviewed Tk workload. Its sealed receipt passed independent
verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-lang_tk-8.6.17-v1.profdata`.
The merged profile SHA-256 is
`13b55599757177a23103aa156d782680259f989ea4d292bac16e465baab2d6fd`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-lang/vala Clang IR profile wave

After the framework republish narrowed the disposable eltpatch sandbox grant,
the exact current-generation `dev-lang/vala-0.56.19` wave completed through
install-QA and its reviewed Vala workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-lang_vala-0.56.19-v1.profdata`.
The merged profile SHA-256 is
`a7632e6e0cd072b9589946035810dddf78e8cb4faff6ad0780077dd70a58c91d`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-util/hipcc-7.2.0 Clang IR profile wave

The exact current-generation `dev-util/hipcc-7.2.0` wave completed through
install-QA and both reviewed workloads (`hipcc --help` and `hipconfig --help`).
Its sealed receipt passed independent verification, and LLVM 22 merged the
authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-util_hipcc-7.2.0-v1.profdata`.
The merged profile SHA-256 is
`91451b468ab2a22ea458c7b5e301a11564100a72c214101014894ba655a1c82f`;
no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — dev-util/source-highlight-3.1.9-r2 Clang IR profile wave

The exact current-generation `dev-util/source-highlight-3.1.9-r2` wave completed through
install-QA and the three reviewed workloads (`check-regexp --help`,
`source-highlight --help`, and `source-highlight-settings --help`). Its sealed
receipt passed independent verification, and LLVM 22 merged the authenticated
raw payload to
`/var/lib/gentoo-optimization/merged-profiles/dev-util_source-highlight-3.1.9-r2-v1.profdata`.
The merged profile SHA-256 is
`3a157314dcd31d93ba5174aa5abc328b4148551b0ff5f71669b9f53dc695f4f9`;
no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — gnome-extra/zenity-4.2.2 Clang IR profile wave

The exact current-generation `gnome-extra/zenity-4.2.2` wave completed through
install-QA and the reviewed `zenity --help` workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/gnome-extra_zenity-4.2.2-v1.profdata`.
The merged profile SHA-256 is
`48443d1ccbd6115740d12a0ef4c2e4eefacc788367f675cf7bee41b9896cfc81`;
no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — gui-apps/fuzzel-1.14.1 Clang IR profile wave

The exact current-generation `gui-apps/fuzzel-1.14.1` wave completed through
install-QA and the reviewed `fuzzel --help` workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/gui-apps_fuzzel-1.14.1-v1.profdata`.
The merged profile SHA-256 is
`0d80f718ee17b725ea2128da62be8ae8f2e7d6a435d99fde8207113e0f233834`;
no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — gui-apps/mako-9999 Clang IR profile wave

The exact current-generation `gui-apps/mako-9999` wave completed through
install-QA and both reviewed workloads (`mako --help` and `makoctl --help`).
Its sealed receipt passed independent verification, and LLVM 22 merged the
authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/gui-apps_mako-9999-v1.profdata`.
The merged profile SHA-256 is
`daf9a688974ca95a0bbc6068535c2020356e0a3dd063a5e6997b18749fad25ae`;
no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — gui-apps/waybar-9999 Clang IR profile wave

The exact current-generation `gui-apps/waybar-9999` wave completed through
install-QA and the reviewed `waybar --help` workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/gui-apps_waybar-9999-v1.profdata`.
The merged profile SHA-256 is
`e484e8887ed31147db889e0443c7d97264948cdfddde10e32d1b96c4654a9d12`;
no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — gui-apps/wl-clipboard-9999 wave deferred

The exact current-generation `gui-apps/wl-clipboard-9999` wave was started
with the sealed plan and readiness manifest, but its upstream Git fetch made
no progress for more than four minutes. The fetch was interrupted at the
bounded stall threshold; no package receipt or merged profile was produced.
The preserved diagnostic log is `/tmp/wl-clipboard.log`. This is a fetch
availability failure, not evidence of a profile or ABI result.

### 2026-09-19 — dev-libs/opencl-icd-loader-2026.05.29 workload rejected

The exact current-generation wave completed its package transaction and
install-QA, but the reviewed `/usr/bin/cllayerinfo --help` workload produced
no output. The runner therefore refused to seal a receipt or profile merge;
the preserved log is `/tmp/opencl.log`. This remains an explicit workload
failure rather than profile evidence.

### 2026-09-19 — gui-apps/wlr-randr-0.5.0 Clang IR profile wave

The exact current-generation `gui-apps/wlr-randr-0.5.0` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/gui-apps_wlr-randr-0.5.0-v1.profdata`.
The merged profile SHA-256 is
`e406776df4735ef9358aa29c1b3b0b90f3059c76fefcd34fcbc21927e8531b84`;
no profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — gui-libs/hyprcursor-9999 ABI guard rejection

The exact current-generation wave reached install-QA, but its dependency
`gui-libs/hyprutils-0.14.2` was rejected by the exported-ABI guard. The
replacement `libhyprutils.so.0.14.2` lost twelve installed versioned symbols;
the transaction was not admitted and no receipt or profile was produced. The
full preserved log is `/tmp/hyprcursor.log`.

### 2026-09-19 — gui-libs/hyprwire-9999 wave deferred

The exact current-generation `gui-libs/hyprwire-9999` wave made no progress
while fetching its upstream Git repository for more than four minutes. The
fetch was interrupted at the bounded stall threshold; no package receipt or
merged profile was produced. The preserved diagnostic log is
`/tmp/hyprwire.log`.

### 2026-09-19 — gui-libs/xdg-desktop-portal-hyprland-9999 ABI guard rejection

The exact current-generation wave reached install-QA, but dependency
`gui-libs/hyprutils-0.14.2` again failed the exported-ABI guard. The staged
replacement DSO was not admitted, and no receipt or profile was produced. The
preserved diagnostic log is `/tmp/xdph.log`.

### 2026-09-19 — gui-wm/gamescope-3.16.28 compile failure

The exact current-generation wave failed during compilation before install-QA.
Clang/lld reported unresolved C++ runtime and ABI symbols, including
`operator new`, `std::__format` internals, locale and exception runtime
entries. No package receipt or profile was produced; the full diagnostic log is
`/tmp/gamescope.log`.

### 2026-09-19 — gui-wm/hyprland-9999 ABI guard rejection

The exact current-generation wave reached install-QA but its dependency
`gui-libs/hyprutils-0.14.2` failed the exported-ABI guard. No receipt or
profile was produced; the complete diagnostic log is `/tmp/hyprland.log`.

### 2026-09-19 — kde-apps/kdenlive-26.08.1 workload rejected

The exact current-generation wave completed compilation, package merge, and
install-QA, but the reviewed `/usr/bin/kdenlive_render` workload exited with
status 1. The runner therefore refused to seal a receipt or merge a profile.
The complete diagnostic log is `/tmp/kdenlive.log`.

### 2026-09-19 — kde-frameworks/kcmutils-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/kcmutils-6.30.0` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_kcmutils-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`a6e3d3b54b3d7aede1a5b73afa25c8ddbec310d973ffa48757a20d999922d8d5`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/kconfig-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/kconfig-6.30.0` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_kconfig-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`b02396c6f292dcdc448769c948ebfbfbd4134b75abeaa64476c2f4a83f4b78af`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/kdbusaddons-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/kdbusaddons-6.30.0` wave
completed through install-QA and its reviewed workload. Its sealed receipt
passed independent verification, and LLVM 22 merged the authenticated raw
payload to `/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_kdbusaddons-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`130916143314a5e00d44c3551106d72b477fca56554ce617367ca9045c8b417b`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/kded-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/kded-6.30.0` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_kded-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`dc0bb8175a481479f7021a1c7a0c003a992580f58d9fc548bdfa245ea6a0bc08`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/kguiaddons-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/kguiaddons-6.30.0` wave
completed through install-QA and its reviewed workload. Its sealed receipt
passed independent verification, and LLVM 22 merged the authenticated raw
payload to `/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_kguiaddons-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`94cf9fb711df8d55ee4fc1d25684af99663a4e11503ebeebb18d542194dcb55b`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/kiconthemes-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/kiconthemes-6.30.0` wave
completed through install-QA and its reviewed workload. Its sealed receipt
passed independent verification, and LLVM 22 merged the authenticated raw
payload to `/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_kiconthemes-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`088b901345e9fc38367b661e644c1eb0503861fa9b1672799545e044c025cf0b`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/kio-6.30.0 workload rejected

The exact current-generation `kde-frameworks/kio-6.30.0` wave completed
compilation, package merge, and install-QA, but the reviewed
`/usr/bin/ktelnetservice6` workload exited with status 2. The runner refused
to seal a receipt or merge a profile; the full diagnostic log is
`/tmp/kio.log`.

### 2026-09-19 — kde-frameworks/knewstuff-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/knewstuff-6.30.0` wave
completed through install-QA and its reviewed workload. Its sealed receipt
passed independent verification, and LLVM 22 merged the authenticated raw
payload to `/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_knewstuff-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`82671386cfe830d9d4448b62b7e137f5cc93464a32e832d687a39c828bbf565e`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/kpackage-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/kpackage-6.30.0` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_kpackage-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`e24f3711700a70389fabd2f2ed9bb2fb9b2e5480dafe4d0e3db57f683fc18889`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/kservice-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/kservice-6.30.0` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_kservice-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`d6a4f91550c637d696425cf35980a0fb61b9f42f51cac0bb1d0f9912ed2c1783`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/solid-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/solid-6.30.0` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_solid-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`cc1434f951f4520ac32ff073baba0b513e0e830fa986dab2120daa5af3f41182`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-frameworks/sonnet-6.30.0 Clang IR profile wave

The exact current-generation `kde-frameworks/sonnet-6.30.0` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/kde-frameworks_sonnet-6.30.0-v1.profdata`.
The merged profile SHA-256 is
`7781e750827bb203c5a9190336111341470b830da69de1dfbbd4ac3ea02b9194`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — kde-plasma/keditfiletype-6.7.5 Clang IR profile wave

The exact current-generation `kde-plasma/keditfiletype-6.7.5` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/kde-plasma_keditfiletype-6.7.5-v1.profdata`.
The merged profile SHA-256 is
`35ab37cd4aa7b9a0e21e0788dc33a6635565a8cb788c0b3be038bb19eec2fbd6`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — media-fonts/font-util-1.4.2 Clang IR profile wave

The exact current-generation `media-fonts/font-util-1.4.2` wave completed
through install-QA and its reviewed workload. Its sealed receipt passed
independent verification, and LLVM 22 merged the authenticated raw payload to
`/var/lib/gentoo-optimization/merged-profiles/media-fonts_font-util-1.4.2-v1.profdata`.
The merged profile SHA-256 is
`4d6fd56e6a0a7b33cbb30fffd680fdcaf90b7cc4d1063fb9793c81c82a914b90`; no
profile-use rebuild or BOLT deployment is inferred.

### 2026-09-19 — media-gfx/argyllcms-3.4.1 profile wave compile failure

The `pgo-clang-ir` wave for `media-gfx/argyllcms-3.4.1` was executed against generation `phase3-live-candidate-20260918-postsync-r1` with the current inventory and readiness bindings. The transaction reached compilation but failed in the compile phase when `clang-22` crashed with exit code 139. No profile-wave receipt was produced, so no merged profile was accepted. This is recorded as a package/compiler execution failure pending separate root-cause work; the wave runner and ABI guard were not bypassed.

### 2026-09-19 — media-gfx/qrencode-4.1.1-r1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1` with receipt/readiness verification passing. The workload receipt was independently verified and merged into `/var/lib/gentoo-optimization/merged-profiles/media-gfx_qrencode-4.1.1-r1-v1.profdata`; merge evidence SHA-256 is `f5ea3260b05e9a145e620a5068121467a518a4728bf2ed1c441ce462f6ce3329`.

### 2026-09-20
- `media-video/libva-utils-2.24.0`: clang IR generate wave completed successfully; readiness and receipt independently verified; `vainfo --help` workload executed; merged profile published at `/var/lib/gentoo-optimization/merged-profiles/media-video_libva-utils-2.24.0-v1.profdata`.
 — media-gfx/renderdoc-1.36-r1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt passed independent verification and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/media-gfx_renderdoc-1.36-r1-v1.profdata`; merge evidence SHA-256 is `d7c2ee1bb8f9471baaeb8d7fb968e55aab7ad597714aaf8182ee758d2023cec9`.

### 2026-09-20 — media-libs/babl-0.1.128 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the profile was merged into `/var/lib/gentoo-optimization/merged-profiles/media-libs_babl-0.1.128-v1.profdata`; merge evidence SHA-256 is `b4d4278bf925b5c4e82f6f76c353afde1d69f1999fb7e33bd093433811781c64`.

### 2026-09-20 — media-libs/dav1d-9999 profile wave configure failure

The `pgo-clang-ir` wave reached the Meson configure phase but failed because the source configuration reported `Atomics not supported` (`meson.build:218`). No receipt or profile was produced; the package remains an evidence-backed terminal execution failure for this wave and was not bypassed.

### 2026-09-20 — media-libs/giflib-6.1.3 workload rejection

The package transaction completed, but the profile-wave workload gate rejected `media-libs/giflib-6.1.3` because `/usr/bin/gifbuild` exited with status 1. No receipt or merged profile was accepted. The workload failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — media-libs/glew-2.2.0-r1 workload rejection

The package transaction completed, but the profile-wave workload gate rejected `media-libs/glew-2.2.0-r1` because `/usr/bin/glewinfo` exited with status 1. No receipt or merged profile was accepted; the workload failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — media-libs/gst-plugins-base-1.26.11 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt passed independent verification and the profile was merged into `/var/lib/gentoo-optimization/merged-profiles/media-libs_gst-plugins-base-1.26.11-v1.profdata`; merge evidence SHA-256 is `dbdea499f7da780a72a1c0928a8847847bcd28bbbebd72b93e02317103f30f3b`.

### 2026-09-20 — media-libs/kvazaar-9999 profile wave fetch stall

The `pgo-clang-ir` wave was started under generation `phase3-live-candidate-20260918-postsync-r1`, but the upstream Git fetch for `https://github.com/ultravideo/kvazaar` made no progress for more than three minutes. The run was terminated without a receipt or profile; the package remains an evidence-backed terminal fetch-stall outcome for this attempt and was not bypassed.

### 2026-09-20 — media-libs/lcms-2.19.1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt passed independent verification and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/media-libs_lcms-2.19.1-v1.profdata`; merge evidence SHA-256 is `25e4d287f5615ed130812c606fb0b2730cc90b07d09b974335fd78e77cbf5b41`.

### 2026-09-20 — media-libs/libcanberra-0.30-r8 workload rejection

The package transaction completed under the `pgo-clang-ir` lane, but the profile-wave workload gate rejected `media-libs/libcanberra-0.30-r8` because `/usr/bin/canberra-boot` exited with status 1. No receipt or merged profile was accepted; the workload failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — media-libs/libjxl-9999 compiler-lane correction

The candidate policy incorrectly assigned `media-libs/libjxl-9999` to the Clang IR lane even though its authoritative package environment explicitly forces `CC=gcc` and `CXX=g++` for the tested Highway/libjxl configuration. The wave failed closed in setup with `compiler-family mismatch: requested=clang, tool=/usr/x86_64-pc-linux-gnu/gcc-bin/17/x86_64-pc-linux-gnu-gcc`. The lane classifier now has exact overrides for `dev-cpp/highway-9999` and `media-libs/libjxl-9999` to use the existing GCC PGO lane with reason `package-env-forced-gcc`; the current derived policy must be regenerated before retrying either package. No receipt or profile was accepted.

The corrected lane manifest was regenerated from the authoritative generation inputs and stored as `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-postsync-r1/pgo-lane-candidates-corrected-20260920.json` (SHA-256 `2f7681761b5d6d142433efa0700f225a40105a7295aa97da8843760118dae6de`). Corrected policy bindings were regenerated with GCC identities for both packages and stored as `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-postsync-r1/pgo-policy-bindings-corrected-20260920.json` (SHA-256 `1477565d9e406d4031d224695241efd9e79e7a20818f57611c7a75828d189b2e`). These are candidate derived evidence only; the active framework has not been replaced.

### 2026-09-20 — dev-cpp/highway-9999 GCC-lane ABI rejection

The corrected `pgo-gcc` retry compiled and staged successfully, but the existing exported-ABI guard rejected the replacement `libhwy_contrib.so.1` providers because `_ZN3hwy17Fill16BytesSecureEPv@@HWY_0` was absent from the candidate while present in the installed ABI (137 old exports versus 146 new exports). No merge, receipt, or profile acceptance occurred. This remains a correctness failure after the package-environment lane correction and is retained as terminal evidence for this wave.

### 2026-09-20 — media-libs/libjxl-9999 GCC profile wave

The corrected `pgo-gcc` wave completed successfully. The receipt passed independent verification, the package transaction and workload gate completed without rejection, and the GCC `.gcda` payload was validated with GCC 17 `gcov-tool`. The published profile manifest is `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-postsync-r1/profile-manifest-libjxl-gcc-v1.json` with sidecar metadata at `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-postsync-r1/profile-manifest-libjxl-gcc-v1.json.metadata.json`.

### 2026-09-20 — media-libs/libwebp-1.6.0 workload rejection

The package transaction completed under the `pgo-clang-ir` lane, but the profile-wave workload gate rejected `media-libs/libwebp-1.6.0` because `/usr/bin/cwebp` exited with status 1. No receipt or merged profile was accepted; the workload failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — media-libs/openal-1.25.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt passed independent verification and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/media-libs_openal-1.25.2-v1.profdata`; merge evidence SHA-256 is `796113bed3edde10298f0659aa882d9c34ccc03b9bcd4e3ceca88f397dfbd9b9`.

### 2026-09-20 — media-libs/openjpeg-2.5.4-r1 workload rejection

The package transaction completed under the `pgo-clang-ir` lane, but the profile-wave workload gate rejected `media-libs/openjpeg-2.5.4-r1` because `/usr/bin/opj_compress` exited with status 1. No receipt or merged profile was accepted; the workload failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — media-libs/libpng-1.6.58 workload rejection

The package transaction completed under the `pgo-clang-ir` lane, but the profile-wave workload gate rejected `media-libs/libpng-1.6.58` because `/usr/bin/png-fix-itxt` produced no output. No receipt or merged profile was accepted; the workload failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — media-libs/libpulse-17.0 workload rejection

The package transaction completed under the `pgo-clang-ir` lane, but the profile-wave workload gate rejected `media-libs/libpulse-17.0` because `/usr/bin/pax11publish` exited with status 1. No receipt or merged profile was accepted; the workload failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — media-libs/libv4l-1.32.0-r1 workload rejection

The package transaction completed under the `pgo-clang-ir` lane, but the profile-wave workload gate rejected `media-libs/libv4l-1.32.0-r1` because `/usr/bin/edid-decode` exited with status 255. No receipt or merged profile was accepted; the workload failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — media-libs/tiff-4.7.1 exported-ABI rejection

The `pgo-clang-ir` wave reached the completed install image, but the install-QA ABI guard rejected the replacement because both established SONAMEs disappeared: `libtiff.so.5` and `libtiffxx.so.5`. No receipt or merged profile was accepted; the exported-ABI failure is preserved as the terminal reason for this attempt.

### 2026-09-20 — dev-libs/date-3.0.3 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_date-3.0.3-v1.profdata`; merge evidence SHA-256 is `f23bcddb320787fb136ad4789d4c3a7ba107538aac6512241e19540dc221ccef`.

### 2026-09-20 — dev-libs/boehm-gc-8.2.12 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed as root against the protected raw profile, and the profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_boehm-gc-8.2.12-v1.profdata`; merge evidence SHA-256 is `c069c7d81017057e0e8a2be07753fb6c39853547d7326969b59575ee88050a52`.

### 2026-09-20 — dev-libs/double-conversion-3.4.0 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_double-conversion-3.4.0-v1.profdata`; merge evidence SHA-256 is `8de7c0c2bd68f0aacb90a75c4216f3ef68059e2342591a6fe3229156ff611015`.

### 2026-09-20 — dev-libs/gmp-6.3.0-r2 profile-wave configure failure

The `pgo-clang-ir` wave failed during the package's 32-bit multilib configure phase. GMP's C++ compiler probe for the `clang-22 -m32` lane failed (`std iostream`), so `econf` reported that the C++ compiler was unavailable. No receipt or profile was accepted; this is retained as a package/compiler configuration failure for the current multilib environment.

### 2026-09-20 — dev-libs/hidapi-0.15.0 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_hidapi-0.15.0-v1.profdata`; merge evidence SHA-256 is `8b5c7c3003808dff071d161315a060a33edd31be1ec7885c4d39b913dff12513`.

### 2026-09-20 — dev-libs/hyphen-2.8.8-r2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_hyphen-2.8.8-r2-v1.profdata`; merge evidence SHA-256 is `16f65e7db251819f9c8aec53f932ceede4d47387e59ca6e691b82909ea1283e1`.

### 2026-09-20 — dev-libs/inih-62 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_inih-62-v1.profdata`; merge evidence SHA-256 is `43c828052742b938894a9a84614223d90339eb80525136afb1a5d73f01eaa9e3`.

### 2026-09-20 — dev-libs/jansson-2.15.1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_jansson-2.15.1-v1.profdata`; merge evidence SHA-256 is `f54cc30e4dd96d6ffff75f4549893237c8c49e845c4a7d953f2444be68679b38`.

### 2026-09-20 — dev-libs/json-c-0.18 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_json-c-0.18-v1.profdata`; merge evidence SHA-256 is `d4b5965e0e09f520cf6bd88449978ce1125201c8c4974fc65876ea1912c6d9a9`.

### 2026-09-20 — dev-libs/libassuan-3.0.0-r1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libassuan-3.0.0-r1-v1.profdata`; merge evidence SHA-256 is `95445e41861a3d59f49fc325e62433fe56a631d6a489371b5978ee810dcfa179`.

### 2026-09-20 — dev-libs/libatomic_ops-7.10.0 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libatomic_ops-7.10.0-v1.profdata`; merge evidence SHA-256 is `2d27d17fedb58356cb5c5a2edc10f7fa226ae2a1844c3ab37574fac8aa562740`.

### 2026-09-20 — dev-libs/libbpf-1.7.0-r1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libbpf-1.7.0-r1-v1.profdata`; merge evidence SHA-256 is `17dd43a16826415b962f62faaa96737f250c9641ada76be254cdf71fb34d7fb6`.

### 2026-09-20 — dev-libs/libevent-2.2.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libevent-2.2.2-v1.profdata`; merge evidence SHA-256 is `01a13fb36ba0b8cb0d7b18c12d9c850f2b8497436320e012d35f86690e803b70`.

### 2026-09-20 — dev-libs/libffi-9999 profile-wave fetch stall

The `pgo-clang-ir` wave did not reach compilation. The upstream Git fetch for `https://github.com/libffi/libffi` made no progress for more than three minutes and was terminated without a receipt or profile. This package-specific source-fetch stall is retained as terminal evidence for this attempt; the wave machinery and ABI guard were not bypassed.

### 2026-09-20 — dev-libs/libfmt-9999 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libfmt-9999-v1.profdata`; merge evidence SHA-256 is `534fc3d9b9fb0aecee27aea029b4ebcb582e1e162651a1700429ae30df95e689`.

### 2026-09-20 — dev-libs/libgudev-238-r2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libgudev-238-r2-v1.profdata`; merge evidence SHA-256 is `9e12dce58012783500dda873929ccaa191bb6a030517d1790ea834d8837d970b`.

### 2026-09-20 — dev-libs/libliftoff-0.5.0 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libliftoff-0.5.0-v1.profdata`; merge evidence SHA-256 is `c01a512116dba97627855568d8ac2d4cd260156237c41524e4ad0b4130b5f776`.

### 2026-09-20 — dev-libs/libsodium-1.0.22 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libsodium-1.0.22-v1.profdata`; merge evidence SHA-256 is `2aec07ca561f282ded982fb401f2a9c0e24cc2be683a38b9423bceab0051473a`.

### 2026-09-20 — dev-libs/libtommath-1.3.0 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libtommath-1.3.0-v1.profdata`; merge evidence SHA-256 is `f3405736e3ba74d98856b5658692c5b7877e80958e1f9ac10f3f4aa4f1f00377`.

### 2026-09-20 — dev-libs/libuv-9999 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libuv-9999-v1.profdata`; merge evidence SHA-256 is `450cb2df2d72d270e4b1df0bf4d100aa2d7a2dbd84b53e1a36208a94af4c309e`.

### 2026-09-20 — dev-libs/libyaml-0.2.5 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libyaml-0.2.5-v1.profdata`; merge evidence SHA-256 is `23473741a2312a0c290d9504e5e97957bbfb1f90b697cb8d571707a506af7aec`.

### 2026-09-20 — dev-libs/libzip-1.11.4-r2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_libzip-1.11.4-r2-v1.profdata`; merge evidence SHA-256 is `7bad1f03d02c3c3271c3565bfb4ff0d0d9d4be01b1d050d771fe902dd6fe36b0`.

### 2026-09-20 — dev-libs/mimalloc-3.4.5 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_mimalloc-3.4.5-v1.profdata`; merge evidence SHA-256 is `4649b0c7a9c00868e7b140fbc5259c083dc92e7fe2c44741716f5d8017996889`.

### 2026-09-20 — dev-libs/miniz-3.1.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_miniz-3.1.2-v1.profdata`; merge evidence SHA-256 is `fcdbb1581648aba5a91f0120a1b70ee6d5c495c8ac588b482eda1615fa94dde7`.

### 2026-09-20 — dev-libs/mpc-1.4.1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_mpc-1.4.1-v1.profdata`; merge evidence SHA-256 is `5c11ecfae799e892c2f56990cde531a0d3a4cb12c4be5518ce1f3f0d855a04ad`.

### 2026-09-20 — dev-libs/mpfr-4.2.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_mpfr-4.2.2-v1.profdata`; merge evidence SHA-256 is `a6abd88d01fb356edca8db067a9823ab52fb8a2ab5f618e99ae26b3daa70c1b2`.

### 2026-09-20 — dev-libs/npth-1.8 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_npth-1.8-v1.profdata`; merge evidence SHA-256 is `557d6745b8f1171aabc9f2684bfd756cfda3c52671206ee8e451f71b93cee6dc`.

### 2026-09-20 — dev-libs/oniguruma-6.9.10 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_oniguruma-6.9.10-v1.profdata`; merge evidence SHA-256 is `5337d0ebb2b7a45b914bdd3a3ba7bbbff30d664011f8d7de2d7ddd5fe352ea70`.

### 2026-09-20 — dev-libs/pugixml-9999 profile-wave fetch stall

The `pgo-clang-ir` wave did not reach compilation. The upstream Git fetch for `https://github.com/zeux/pugixml.git` made no progress for more than two minutes and was terminated without a receipt or profile. This package-specific source-fetch stall is retained as terminal evidence for this attempt; the wave machinery and ABI guard were not bypassed.

### 2026-09-20 — dev-libs/re2-2025.08.12 exported-ABI rejection

The `pgo-clang-ir` wave reached the completed install image, but the exported-ABI guard rejected the replacement `libre2.so.11` providers. Two installed `Regexp::Walker` `Copy` symbol identities were absent from the candidate (`old=498/new=496` for the primary SONAME). No receipt or merged profile was accepted; the ABI failure is retained as the terminal reason for this attempt.

### 2026-09-20 — dev-libs/spdlog-9999 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_spdlog-9999-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-libs/tinyxml2-11.0.0 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_tinyxml2-11.0.0-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-libs/tree-sitter-0.26.13 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_tree-sitter-0.26.13-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-libs/tree-sitter-bash-0.25.1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_tree-sitter-bash-0.25.1-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-libs/userspace-rcu-0.15.6 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_userspace-rcu-0.15.6-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-libs/wayland-9999 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-libs_wayland-9999-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-qt/qt5compat-6.11.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-qt_qt5compat-6.11.2-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-qt/qtnetworkauth-6.11.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-qt_qtnetworkauth-6.11.2-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-qt/qtsvg-6.11.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-qt_qtsvg-6.11.2-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-qt/qtmultimedia-6.11.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-qt_qtmultimedia-6.11.2-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-qt/qtshadertools-6.11.2 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/dev-qt_qtshadertools-6.11.2-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — dev-libs/nss-3.129 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave reached package installation, but the workload recipe failed closed with exit status 255 from `/usr/bin/addbuiltin`. No profile receipt or merge was produced; the package remains an explicit terminal workload failure for later remediation.

### 2026-09-20 — dev-util/clinfo-9999 Clang IR profile wave terminal stall

The wave did not produce a receipt. Portage remained in the live git fetch for `https://github.com/Oblomov/clinfo.git` beyond the bounded observation window and the runner exited without a profile. This is recorded as an upstream fetch stall; no ABI or profile result was inferred.

### 2026-09-20 — focused ABI/QA and profile-wave regression verification

The focused ABI-guard, Portage QA-hook, profile-wave guard, and wave-receipt verifier suites passed. The ABI fixtures cover the zero-DSO no-root-traversal and immediate-provider-scope regressions. The aggregate portable suite's recovery tests passed; its Phase-2 evidence fixture was not green because the mutable post-authorization worktree cannot satisfy the frozen Phase-2 source boundary, so no Phase-2 evidence was changed.

### 2026-09-20 — dev-util/ftjam-2.5.3_rc2-r3 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully, but its workload recipe failed closed with exit status 1 from `/usr/bin/jam`. No receipt or merged profile was produced; the package remains an explicit workload terminal failure.

### 2026-09-20 — dev-util/hyprwayland-scanner-9999 Clang IR profile wave terminal stall

The wave entered Portage but stalled during the live git fetch for `https://github.com/hyprwm/Hyprwayland-scanner.git` and exited without a receipt. No profile was merged; this is retained as an upstream fetch stall.

### 2026-09-20 — dev-util/hyprwayland-scanner-9999 Clang IR wave aborted after fetch stall

The live Git fetch for `https://github.com/hyprwm/Hyprwayland-scanner.git` remained stuck for more than four minutes while holding the framework transaction lock. The userspace wave and its child fetch were terminated; no package merge, receipt, or profile was admitted. The queued `gnome-base/dconf-0.49.0` attempts were cancelled before package execution because they were blocked behind that same lock and produced no receipt.

### 2026-09-20 — gnome-base/dconf-0.49.0 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully after the earlier lock was cleared, but its workload recipe failed closed with exit status 2 from `/usr/bin/dconf`. No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — gui-apps/slurp-1.5.0 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully, but its workload recipe failed closed with exit status 1 from `/usr/bin/slurp` (no graphical selection context). No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — net-dns/libidn2-2.3.8 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/net-dns_libidn2-2.3.8-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — net-libs/libndp-1.9-r1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/net-libs_libndp-1.9-r1-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — net-libs/libproxy-0.5.12-r1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/net-libs_libproxy-0.5.12-r1-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — sys-libs/mtdev-1.1.7 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully, but its workload recipe failed closed with exit status 255 from `/usr/bin/mtdev-test`. No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — sys-auth/rtkit-0.14 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/sys-auth_rtkit-0.14-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — sys-apps/keyutils-1.6.3-r1 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully, but its workload recipe failed closed with exit status 2 from `/bin/keyctl`. No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — x11-apps/xhost-1.0.10 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully, but its workload recipe failed closed with exit status 1 from `/usr/bin/xhost` because no active X display is available. No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — x11-misc/xdg-user-dirs-0.20 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/x11-misc_xdg-user-dirs-0.20-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — x11-libs/libxcvt-0.1.3 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully, but its workload recipe failed closed with exit status 1 from `/usr/bin/cvt`. No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — dev-tcltk/blt-2.5.3-r4 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully after a long compile, but its workload recipe `/usr/bin/bltsh` produced no output and failed closed. No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — dev-libs/openssl-3.6.9999 Clang IR profile wave terminal ABI failure

The instrumented rebuild completed, but install QA failed closed in the exported-ABI guard because the staged image removed the established `libssl.so.1.1` and `libcrypto.so.1.1` SONAME providers. No package merge, workload execution, receipt, or profile merge was accepted. The failed transaction and Portage log remain retained as ABI-remediation evidence.

### 2026-09-20 — media-libs/woff2-1.0.2-r7 Clang IR profile wave terminal compile failure

The Clang-IR rebuild failed during linking because the C++ standard-library symbols were unresolved under the package's active libc++/linker configuration (`std::__1` and exception ABI symbols). The package was not merged, and no workload, receipt, or profile merge was accepted. The compiler/linkage failure is retained for package-specific remediation.

### 2026-09-20 — media-libs/zint-2.16.0 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/media-libs_zint-2.16.0-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — games-util/gamemode-9999 Clang IR profile wave terminal fetch stall

The wave entered Portage but remained stuck in the live Git fetch for `https://github.com/FeralInteractive/gamemode.git` for more than two minutes. The stalled fetch and wave were terminated before package admission; no merge, workload, receipt, or profile was accepted. The upstream fetch stall is retained as terminal evidence.

### 2026-09-20 — media-sound/alsa-utils-1.2.16 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully, but its workload recipe failed closed with exit status 1 from `/usr/bin/aconnect` because no usable ALSA sequencer context was available. No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — media-sound/sndio-1.10.0 Clang IR profile wave terminal failure

The `pgo-clang-ir` wave installed successfully, but its workload recipe failed closed with exit status 1 from `/usr/bin/aucat` because no usable audio service/context was available. No receipt or merged profile was produced; the workload failure remains explicit.

### 2026-09-20 — sci-libs/fftw-3.3.10-r1 Clang IR profile wave

The long generated-kernel `pgo-clang-ir` rebuild completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The workload and receipt verification passed, and the large raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sci-libs_fftw-3.3.10-r1-v1.profdata`; merge evidence was generated by the merge verifier.

### 2026-09-20 — media-sound/pavucontrol-6.1-r1 Clang IR profile wave

The `pgo-clang-ir` wave completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile was merged into `/var/lib/gentoo-optimization/merged-profiles/media-sound_pavucontrol-6.1-r1-v1.profdata`; merge evidence was independently generated by the merge verifier.

### 2026-09-20 — media-video/ffmpeg-8.1.2 Clang IR profile wave

The long multi-ABI `pgo-clang-ir` rebuild completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/media-video_ffmpeg-8.1.2-v1.profdata`; merge evidence was generated by the merge verifier.

### 2026-09-20 — media-video/mediainfo-24.11-r1 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild failed closed during the compile phase under the current clang/libc++ lane. `ld.lld` rejected unresolved `std::__1` and `ZenLib::Thread` vtable references from the installed `libzen.so` under `--no-allow-shlib-undefined`; no package merge, receipt, or merged profile was produced. The exact failure is retained in `/var/tmp/gentoo-portage-build/portage/media-video/mediainfo-24.11-r1/temp/build.log`.

### 2026-09-20 — net-firewall/iptables-1.8.13 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild and merge completed, but the workload recipe failed closed with exit status 1 from `/sbin/xtables-legacy-multi`. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — net-misc/lldpd-1.0.22 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild and merge completed, but the workload recipe failed closed with exit status 1 from `/usr/sbin/lldpcli`. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the unavailable/unsuccessful LLDP control workload is retained as the terminal state.

### 2026-09-20 — net-print/cups-2.4.19 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild and merge completed, but the workload recipe failed closed with exit status 1 from `/usr/bin/cancel` because no usable print service/context was available. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — net-wireless/wpa_supplicant-2.12 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild and merge completed, but the workload recipe failed closed with exit status 255 from `/usr/bin/wpa_cli` because no active wpa control interface was available. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the unavailable wireless-control workload is retained as the terminal state.

### 2026-09-20 — sys-apps/baobab-50.0 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_baobab-50.0-v1.profdata`.

### 2026-09-20 — sys-apps/hwloc-2.12.2 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and `/usr/bin/hwloc-annotate --help` workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_hwloc-2.12.2-v1.profdata`.

### 2026-09-20 — sys-apps/mlocate-0.26-r3 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_mlocate-0.26-r3-v1.profdata`.

### 2026-09-20 — sys-apps/shadow-4.20.2 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild and merge completed, but the workload recipe failed closed with exit status 1 from `/bin/getsubids`. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — sys-apps/sysvinit-3.18 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild and merge completed, but the userspace workload recipe failed closed with exit status 1 from `/sbin/bootlogd`. No profile receipt or merged profile was accepted. No boot-chain mutation was performed; the installed package remains rebuilt under the generation and the workload failure is retained as the terminal state.

### 2026-09-20 — sys-auth/passwdqc-2.1.0-r1 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-auth_passwdqc-2.1.0-r1-v1.profdata`.

### 2026-09-20 — sys-fs/udisks-2.11.2 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild and merge completed, but `/usr/bin/udisksctl --help` exited 1 because no usable udisks service/context was available. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — sys-libs/efivar-39-r1 Clang IR profile wave

The userspace `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The ebuild had no EFI-variable or boot-asset mutation phase; no firmware/EFI state was touched. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-libs_efivar-39-r1-v1.profdata`.

### 2026-09-20 — sys-libs/ncurses-6.5_p20251220 Clang IR profile wave terminal failure

The large multilib `pgo-clang-ir` rebuild, install, merge, and ABI guard completed successfully, but the workload recipe failed closed with exit status 1 from `/usr/bin/clear` in the non-interactive terminal context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — sys-libs/pam-1.7.2 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild, install, merge, and ABI guard completed successfully, but the workload recipe failed closed with exit status 1 from `/sbin/faillock` in the current non-authenticated context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — sys-libs/timezone-data-2026d Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and `zdump --help`/`zic --help` workloads completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-libs_timezone-data-2026d-v1.profdata`.

### 2026-09-20 — sys-power/cpupower-6.16-r1 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild, install, merge, and ABI guard completed successfully, but `/usr/sbin/cpufreq-bench` exited 1 because no usable CPU-frequency benchmark context was available. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — sys-power/thermald-2.5.12 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-power_thermald-2.5.12-v1.profdata`.

### 2026-09-20 — sys-process/cronie-1.7.2-r1 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/cronnext` exited 1 in the current daemon/configuration context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — sys-process/uksmd-6.12.2 Clang IR profile wave terminal failure

The `pgo-clang-ir` userspace rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/uksmd` exited 61 in the current runtime context. No profile receipt or merged profile was accepted. No kernel artifact or lifecycle mutation was performed; the installed userspace package remains rebuilt under the generation and the workload failure is retained as the terminal state.

### 2026-09-20 — www-client/w3m-0.5.6 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/w3m` exited 1 in the non-interactive workload context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — x11-apps/iceauth-1.0.11 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-apps_iceauth-1.0.11-v1.profdata`.

### 2026-09-20 — x11-apps/xgamma-1.0.8 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-apps_xgamma-1.0.8-v1.profdata`.

### 2026-09-20 — x11-apps/xprop-1.2.8 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/xprop` exited 1 because no usable X display was available. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the display-dependent workload failure is retained as the terminal state.

### 2026-09-20 — x11-apps/xrandr-1.5.4 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-apps_xrandr-1.5.4-v1.profdata`.

### 2026-09-20 — x11-apps/xset-1.2.6 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-apps_xset-1.2.6-v1.profdata`.

### 2026-09-20 — x11-base/xorg-server-21.1.24 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/Xvfb --help` exited 1 in the current headless runtime context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — x11-libs/gdk-pixbuf-2.44.8 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-libs_gdk-pixbuf-2.44.8-v1.profdata`.

### 2026-09-20 — x11-libs/gtk+-2.24.33-r3 Clang IR profile wave terminal failure

The `pgo-clang-ir` multilib rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/i686-pc-linux-gnu-gtk-query-immodules-2.0` exited 1 in the current runtime context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — x11-libs/gtk+-3.24.52 Clang IR profile wave terminal failure

The `pgo-clang-ir` multilib rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/gtk-builder-tool --help` exited 1 in the current runtime context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — x11-libs/libXpm-3.5.19 Clang IR profile wave terminal failure

The `pgo-clang-ir` multilib rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/cxpm --help` exited 1 in the current runtime context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — x11-libs/pango-1.58.2 Clang IR profile wave

The `pgo-clang-ir` multilib rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-libs_pango-1.58.2-v1.profdata`.

### 2026-09-20 — x11-misc/sddm-0.21.0_p20251101 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild, install, merge, and ABI guard completed successfully, but `/usr/bin/sddm --help` exited 1 in the current display-manager/runtime context. No profile receipt or merged profile was accepted. The installed package remains rebuilt under the generation; the workload failure is retained as the terminal state.

### 2026-09-20 — x11-misc/xdotool-4.20260303.1 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-misc_xdotool-4.20260303.1-v1.profdata`.

### 2026-09-20 — xfce-base/exo-4.20.0-r1 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/xfce-base_exo-4.20.0-r1-v1.profdata`.

### 2026-09-20 — xfce-base/libxfce4ui-4.20.2 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/xfce-base_libxfce4ui-4.20.2-v1.profdata`.

### 2026-09-20 — xfce-base/libxfce4util-4.20.1 workload terminal result

The `pgo-clang-ir` rebuild completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/sbin/xfce4-kiosk-query --help` exited 1. The package is retained as a terminal workload exception; no profile receipt was published.

### 2026-09-20 — xfce-base/thunar-4.20.10 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/xfce-base_thunar-4.20.10-v1.profdata`.

### 2026-09-20 — xfce-base/xfce4-panel-4.20.8 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/xfce-base_xfce4-panel-4.20.8-v1.profdata`.

### 2026-09-20 — xfce-base/xfconf-4.20.0 Clang IR profile wave

The `pgo-clang-ir` rebuild, merge, and workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/xfce-base_xfconf-4.20.0-v1.profdata`.

### 2026-09-20 — app-text/opensp-1.5.2-r10 Clang IR profile wave terminal failure

The `pgo-clang-ir` attempt reached compilation but failed linking `onsgmls` under the active Clang/libc++ lane. `ld.lld` rejected unresolved C++ runtime symbols from `libosp.so` (`operator new[]`, `__cxa_begin_catch`, RTTI vtables, and related symbols) under `--no-allow-shlib-undefined`. No package merge, receipt, or profile merge was admitted; the failed build evidence is retained for package-specific link remediation.

### 2026-09-20 — dev-libs/opencl-icd-loader-2026.05.29 workload terminal result

The `pgo-clang-ir` rebuild and install completed under generation `phase3-live-candidate-20260918-postsync-r1`, but `/usr/bin/cllayerinfo` produced no output and the workload receipt gate refused the wave. No profile receipt or merge was admitted; the package remains a terminal workload exception with its build evidence preserved.

### 2026-09-20 — dev-libs/protobuf-34.2 Clang IR profile wave terminal failure

The dual-ABI `pgo-clang-ir` rebuild completed compilation and staging, but the install-QA ABI guard rejected the replacement DSOs. The guard reported an exported-symbol loss for the generated `EpsCopyInputStream::ReadPackedVarintArray...` symbol across the protobuf and protobuf-lite DSO families (including symlinked and versioned paths). No package merge, receipt, or profile merge was admitted; the failed build and ABI evidence remain preserved for a package-specific ABI remediation.

### 2026-09-20 — dev-util/colm-0.14.7-r4 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild completed compilation and staging under generation `phase3-live-candidate-20260918-postsync-r1`, but the install-QA ABI guard rejected the replacement DSOs. The guard reported exported-symbol loss in the `libfsm-0.14.7.so` and `libfsm.so` SONAME families, including missing C++ template and runtime symbols. No package merge, receipt, or profile merge was admitted; the build log and ABI evidence remain preserved for package-specific ABI remediation.

### 2026-09-20 — kde-apps/kdenlive-26.08.1 workload terminal result

The `pgo-clang-ir` rebuild, staging, and install-QA completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/kdenlive_render` exited 1. The workload gate refused the wave; no profile receipt or merge was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — kde-frameworks/kio-6.30.0 workload terminal result

The `pgo-clang-ir` rebuild, staging, and install-QA completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/ktelnetservice6` exited 2. The workload gate refused the wave; no profile receipt or merge was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — media-libs/giflib-6.1.3 workload terminal result

The `pgo-clang-ir` rebuild, staging, and install-QA completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/gifbuild` exited 1. The workload gate refused the wave; no profile receipt or merge was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — media-libs/glew-2.2.0-r1 workload terminal result

The `pgo-clang-ir` rebuild, staging, and install-QA completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/glewinfo` exited 1. The workload gate refused the wave; no profile receipt or merge was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — gui-wm/gamescope-3.16.28 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild failed during the link stage of the Vulkan WSI layer. `ld.lld` reported unresolved C++ runtime and RTTI symbols, including `std::__throw_bad_function_call()` and `__cxxabiv1` type-info vtables, under the active Clang/libstdc++ profile lane. No package merge, receipt, or profile merge was admitted; the complete build log remains preserved for package-specific toolchain/link remediation.

### 2026-09-20 — media-libs/libcanberra-0.30-r8 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, and ABI guard completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/canberra-boot` exited 1. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — media-libs/libpng-1.6.58 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, and ABI guard completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/png-fix-itxt` produced no output. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — media-libs/libpulse-17.0 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, and ABI guard completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/pax11publish` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — media-libs/libv4l-1.32.0-r1 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, and ABI guard completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/edid-decode` exited 255. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — media-libs/libwebp-1.6.0 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, and ABI guard completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/cwebp` exited 1. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — media-libs/openjpeg-2.5.4-r1 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, and ABI guard completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/opj_compress` exited 1. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — media-libs/tiff-4.7.1 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild completed staging, but the install-QA ABI guard rejected the replacement DSOs. The guard reported established SONAME disappearance for `libtiffxx.so.5` and `libtiff.so.5`; no package merge, receipt, or profile merge was admitted. The failed build and ABI evidence remain preserved for package-specific ABI remediation.

### 2026-09-20 — media-libs/woff2-1.0.2-r7 Clang IR profile wave terminal failure

The `pgo-clang-ir` rebuild failed during linking under the active Clang/libc++ lane. `ld.lld` reported unresolved `std::__1` iostream/string and C++ exception/RTTI symbols while linking `woff2_info`; no package merge, install-QA admission, receipt, or profile merge was created. The complete failed build evidence remains preserved for package-specific toolchain/link remediation.

### 2026-09-20 — net-misc/openssh-10.5_p1 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/scp` exited 1. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — net-misc/socat-1.8.1.3 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/filan` exited 1. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-apps/busybox-1.38.0 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_busybox-1.38.0-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-apps/gptfdisk-1.0.10-r1 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/sbin/fixparts` exited 1. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-apps/groff-1.23.0-r2 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_groff-1.23.0-r2-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-apps/iproute2-7.2.0 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/bin/ip` exited 255. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-apps/kbd-2.10.0 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_kbd-2.10.0-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-apps/nvme-cli-2.16 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_nvme-cli-2.16-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-apps/openrc-0.64 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/sbin/openrc-init` exited 1. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-apps/pciutils-3.15.0 workload terminal result

The `pgo-clang-ir` dual-ABI rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/lspci` exited 1. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-apps/texinfo-7.3 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_texinfo-7.3-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-apps/util-linux-2.42.3 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_util-linux-2.42.3-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-apps/xdg-dbus-proxy-0.1.8 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-apps_xdg-dbus-proxy-0.1.8-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-block/parted-3.7 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-block_parted-3.7-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-devel/bc-1.08.2 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-devel_bc-1.08.2-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-devel/bison-3.8.2-r3 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-devel_bison-3.8.2-r3-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-devel/flex-2.6.4-r6 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-devel_flex-2.6.4-r6-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-devel/patch-2.8-r1 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-devel_patch-2.8-r1-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-fs/cryptsetup-2.8.8 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-fs_cryptsetup-2.8.8-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-fs/dosfstools-4.2 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-fs_dosfstools-4.2-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-fs/e2fsprogs-1.47.4 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/sbin/badblocks` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-fs/fuse-overlayfs-1.17 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-fs_fuse-overlayfs-1.17-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-fs/lvm2-2.03.39 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/sbin/dmfilemapd` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-fs/xfsprogs-7.1.1 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/sbin/mkfs.xfs` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-libs/gdbm-1.26 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-libs_gdbm-1.26-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-libs/libcap-2.78 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/sbin/getcap` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-libs/slang-2.3.3-r2 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/slsh` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-process/btop-1.4.7 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-process_btop-1.4.7-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-process/lsof-4.99.7 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/lsof` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-process/numactl-2.0.19 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/memhog` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-process/procps-4.0.6 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-process_procps-4.0.6-v1.profdata` with merge evidence recorded.

### 2026-09-20 — sys-process/psmisc-23.7 workload terminal result

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/bin/fuser` exited 1 in the current runtime context. The workload gate refused the wave; no profile receipt or merged profile was admitted. The package remains a terminal workload exception with its build and runtime evidence preserved.

### 2026-09-20 — sys-process/time-1.10 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/sys-process_time-1.10-v1.profdata` with merge evidence recorded.

### 2026-09-20 — x11-apps/mkfontscale-1.2.4 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-apps_mkfontscale-1.2.4-v1.profdata` with merge evidence recorded.

### 2026-09-20 — x11-apps/xauth-1.1.5 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-apps_xauth-1.1.5-v1.profdata` with merge evidence recorded.

### 2026-09-20 — x11-apps/xkbcomp-1.5.0-r2 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-apps_xkbcomp-1.5.0-r2-v1.profdata` with merge evidence recorded.

### 2026-09-20 — x11-apps/xwininfo-1.1.7 Clang IR profile wave

The `pgo-clang-ir` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. The receipt verifier passed and the raw profile set was independently merged into `/var/lib/gentoo-optimization/merged-profiles/x11-apps_xwininfo-1.1.7-v1.profdata` with merge evidence recorded.

### 2026-09-20 — dev-util/bindgen-0.72.1 Rust profile wave

The `pgo-rust` rebuild, staging, install-QA, ABI guard, and declared `/usr/bin/bindgen --help` workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1` with the Rust no-LTO compatibility path. Receipt verification passed and the sealed Rust profraw payload was retained in the authenticated generation spool; Rust profiles remain in their native profraw format rather than being admitted to the Clang `.profdata` store.

### 2026-09-20 — dev-util/bpf-linker-0.11.1 Rust profile wave

The `pgo-rust` rebuild, staging, install-QA, ABI guard, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1` with the Rust no-LTO compatibility path. Receipt verification passed and the sealed Rust profraw payload was retained in the authenticated generation spool.

### 2026-09-20 — dev-util/cargo-c-0.10.25 Rust profile wave

The `pgo-rust` rebuild, staging, install-QA, ABI guard, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1` with the Rust no-LTO compatibility path. Receipt verification passed and the sealed Rust profraw payload was retained in the authenticated generation spool.

### 2026-09-20 — dev-util/cbindgen-0.29.4 Rust profile wave

The `pgo-rust` rebuild, staging, install-QA, ABI guard, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1` with the Rust no-LTO compatibility path. Receipt verification passed and the sealed Rust profraw payload was retained in the authenticated generation spool.

### 2026-09-20 — gnome-base/librsvg-2.62.3 Rust profile-wave terminal failure

The `pgo-rust` rebuild reached the multilib link stage but failed closed in the 32-bit ABI build with unresolved `__llvm_profile_instrument_target` and `__llvm_profile_instrument_memop` symbols from the Rust instrumentation runtime. No package merge, install-QA admission, workload receipt, or profile payload was accepted. The failed Portage attempt and build logs remain preserved as an exact package-specific Rust/multilib toolchain failure.

### 2026-09-20 — gui-apps/xwayland-satellite-0.8.2 Rust workload terminal result

The `pgo-rust` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/xwayland-satellite` exited 101 in the current runtime context. The workload gate refused the wave; no profile receipt or Rust profile payload was admitted. The installed userspace package and exact failed workload evidence remain preserved as a terminal workload exception.

### 2026-09-20 — media-libs/gstreamer-1.26.11 Rust profile wave

The `pgo-rust` multilib rebuild, staging, install-QA, ABI guard, package merge, and declared GStreamer help workloads completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the sealed Rust profraw payload was retained in the authenticated generation spool.

### 2026-09-20 — media-sound/ncspot-1.3.4 Rust workload terminal result

The `pgo-rust` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/ncspot` exited `-11` (SIGSEGV) in the current non-interactive runtime context. The workload gate refused the wave; no profile receipt or Rust profile payload was admitted. The installed userspace package and exact failed workload evidence remain preserved as a terminal workload exception.

### 2026-09-20 — media-video/rav1e-0.8.1 Rust profile wave

The `pgo-rust` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the sealed Rust profraw payload was retained in the authenticated generation spool.

### 2026-09-20 — sys-apps/amdgpu_top-0.11.5 Rust profile wave

The `pgo-rust` rebuild, staging, install-QA, ABI guard, package merge, and declared workload completed successfully under generation `phase3-live-candidate-20260918-postsync-r1`. Receipt verification passed and the sealed Rust profraw payload was retained in the authenticated generation spool.

### 2026-09-20 — sys-apps/lact-0.9.1 Rust workload terminal result

The `pgo-rust` rebuild, staging, install-QA, ABI guard, and package merge completed under generation `phase3-live-candidate-20260918-postsync-r1`, but the declared workload `/usr/bin/lact` exited `-11` (SIGSEGV) in the current non-interactive runtime context. The workload gate refused the wave; no profile receipt or Rust profile payload was admitted. The installed userspace package and exact failed workload evidence remain preserved as a terminal workload exception.

### 2026-09-20 — sys-apps/ripgrep-15.2.0 Rust profile-wave terminal failure

The `pgo-rust` compilation completed, but the package failed closed in `src_install`: the ebuild's completion-generation command expected `target/release/rg` while the Cargo install path had already placed the executable under the image tree. No package merge, install-QA admission, workload receipt, or profile payload was accepted. The failed Portage attempt and build logs remain preserved as an exact package-specific ebuild install-path failure.

### 2026-09-20 — sys-block/thin-provisioning-tools-1.3.1 Rust profile-wave terminal failure

The `pgo-rust` compilation completed, but the package failed closed in `src_install`: its Makefile attempted to install `target/release/pdata_tools`, which was absent after the package's Cargo build layout under the reviewed Rust lane. No package merge, install-QA admission, workload receipt, or profile payload was accepted. The failed Portage attempt and build logs remain preserved as an exact package-specific install-path failure.

### 2026-09-20 — successor workload coverage audit repaired

Regenerated the no-profile-producing workload exclusions from the current successor workload manifest and authoritative ELF census. The prior audit omitted three exact CPVs (`app-text/gspell-1.14.4`, `dev-libs/libtracefs-1.8.3`, and `sys-apps/gentoo-functions-9999`) because the successor manifest had advanced their state without regenerating the exclusion artifact. The corrected root-owned `workload-exclusions-successor-20260920.json` contains 251 exact exclusions, and the independent workload coverage verifier now passes: 543 PGO-lane packages, 292 recipe-ready packages, 251 workload exclusions, zero overlap, and zero missing records.

### 2026-09-20 — current-generation BOLT safety review refresh

Reran the fail-closed BOLT safety review against the current generation's authoritative ELF metadata and eligibility classification. The refreshed review produced 1,947 `bolt-ready-pending-profile` records and 574 terminal `not-applicable` records, with no tool-invocation aborts or fail-open classifications. The root-owned artifact is `bolt-safety-review-successor-20260920.json`. Profile binding, exact-input capture, BOLT training, and deployment remain outstanding for the ready subset; no BOLT completion is claimed.

### 2026-09-20 — current profile payload audit

The independent profile-payload verifier was rerun against the current generation-bound policy bindings. It found 330 packages with authenticated raw payloads and 213 exact lane records still missing payloads; the audit remains `pending-profile-collection`. The root-owned result is `profile-payload-audit-successor-20260920.json`. No profile-use or BOLT deployment is claimed while this audit is pending.

### 2026-09-20 — payload audit exclusion-scope correction

The profile-payload verifier was overcounting packages already classified by the authoritative workload-exclusion artifact. It now accepts `--exclusions`, emits explicit `workload-exclusion` records, binds the exclusion hash into schema version 2, and keeps missing-profile fail-closed behavior for actionable packages. Against the current generation, the corrected audit reports 278 payload-present records, 251 workload exclusions, and 14 genuinely missing payloads. The remaining 14 are actionable or previously failed packages: `dev-util/maturin-1.15.0`, `dev-util/rustup-1.29.0`, `gnome-base/librsvg-2.62.3`, `media-libs/libjxl-9999`, `media-sound/qpwgraph-9999`, `net-dialup/ppp-9999`, `net-misc/chrony-9999`, `net-misc/curl-9999`, `sys-apps/ripgrep-15.2.0`, `sys-block/thin-provisioning-tools-1.3.1`, `sys-process/nvtop-9999`, `x11-apps/mesa-progs-9999`, `x11-base/xwayland-9999`, and `x11-terms/alacritty-9999`.

Profile-wave terminal failure (2026-09-20): the exact successor `media-libs/libjxl-9999` Clang IR wave reached the optimization dispatcher but was rejected before build because Portage resolved `/usr/x86_64-pc-linux-gnu/gcc-bin/17/x86_64-pc-linux-gnu-gcc` while the wave required Clang. The fail-closed compiler-family guard stopped the transaction; no package merge or profile receipt was accepted. The complete log is retained at `/tmp/libjxl.log` and the Portage build log under `/var/tmp/gentoo-portage-build/portage/media-libs/libjxl-9999/temp/build.log`. This is recorded as a package-specific correctness failure pending compiler-lane remediation; the ABI guard remained enabled and no boot/kernel state was touched.

Profile-wave terminal failure (2026-09-20): `media-sound/qpwgraph-9999` completed the exact Clang IR instrumented merge and install-QA ABI guard, but its representative workload `/usr/bin/qpwgraph` exited 1. The runner therefore issued no accepted profile receipt; the package merge remains ordinary transaction evidence and is not counted as profile completion. Full output is retained at `/tmp/qpwgraph.log`. No ABI-guard bypass or boot/kernel mutation occurred.

Profile-wave terminal failure (2026-09-20): `net-dialup/ppp-9999` completed the exact Clang IR instrumented merge and install-QA ABI guard, but its representative `/usr/sbin/chat` workload exited 1. The runner issued no accepted profile receipt; the ordinary package merge is retained separately and is not counted as profile completion. Full output is retained at `/tmp/ppp.log`. No ABI-guard bypass or boot/kernel mutation occurred.

Profile-wave success (2026-09-20): `net-misc/chrony-9999` completed the exact successor Clang IR generation wave. The instrumented package merged through the fail-closed ABI guard, the representative workload completed, and the root-owned receipt `/var/lib/gentoo-optimization/generations/phase3-live-candidate-20260918-postsync-r1/profile-wave-receipt-chrony-v1.json` passed independent wave/readiness verification. LLVM 22 merged the authenticated raw payloads into `/var/lib/gentoo-optimization/merged-profiles/net-misc_chrony-9999-v1.profdata`; independent merge evidence is recorded at the adjacent generation evidence path. The profile remains pending validation/use authorization.

Profile-wave terminal failure (2026-09-20): the exact successor `net-misc/curl-9999` Clang IR wave was stopped after its live Git source fetch made no progress for more than 110 seconds (zero CPU and unchanged fetch log). The coordinator and Portage transaction were terminated before source admission; no package merge or profile receipt was accepted. The preserved output is `/tmp/curl.log`. This is recorded as an infrastructure/source-fetch failure requiring a later bounded retry, not as a profile result; no ABI-guard bypass or boot/kernel mutation occurred.

Profile-wave success (2026-09-20): `sys-process/nvtop-9999` completed the exact successor Clang IR generation wave, passed the representative workload and install-QA ABI guard, and produced an independently verified root-owned wave receipt. LLVM 22 merged its authenticated raw payload into `merged-profiles/sys-process_nvtop-9999-v1.profdata` with adjacent merge evidence. The profile remains pending validation/use authorization.

Profile-wave terminal failure (2026-09-20): `x11-apps/mesa-progs-9999` failed in the ebuild prepare phase because the current live source no longer matched `9999-Disable-things-we-don-t-want.patch`; all three patch hunks were rejected. The Clang lane and ABI guard were entered but no build, merge, or profile receipt was accepted. Full output is retained at `/tmp/mesa-progs.log`. This requires package-source/patch remediation before a bounded retry; no guard bypass or boot/kernel mutation occurred.

Profile-wave terminal failure (2026-09-20): `x11-base/xwayland-9999` completed the exact successor Clang IR build, merge, and install-QA ABI guard, but its representative `/usr/bin/Xwayland` workload exited 1. The runner issued no accepted profile receipt, so the package remains missing profile payload evidence. Full output is retained at `/tmp/xwayland.log`; no guard bypass or boot/kernel mutation occurred.

Profile payload audit correction (2026-09-20): after the chrony and nvtop waves, an attempted audit using the stale partial `pgo-policy-bindings-corrected-20260920.json` was rejected because that artifact contains only twelve current lane records and 683 pending classifications; its transient v3 output was removed and never treated as authority. Re-running against the complete live `/tmp/policy.json` binding set and the authenticated 251-record workload-exclusion artifact produced root-owned `profile-payload-audit-successor-20260920-v4.json`: 285 profile-present, 251 workload-exclusion, and 7 genuinely missing-profile records. The remaining missing records are `dev-util/maturin-1.15.0`, `dev-util/rustup-1.29.0`, `gnome-base/librsvg-2.62.3`, `media-libs/libjxl-9999`, `sys-apps/ripgrep-15.2.0`, `sys-block/thin-provisioning-tools-1.3.1`, and `x11-terms/alacritty-9999`; authorization correctly remains pending-profile-collection.

Profile-wave terminal failure (2026-09-20): `dev-util/maturin-1.15.0` completed its Rust instrumented compilation but the ebuild's completion-generation command `maturin completions bash` segfaulted in `src_compile`. No package merge, workload receipt, or profile payload was accepted. Full output and the Portage build log are retained at `/tmp/maturin.log` and `/var/tmp/gentoo-portage-build/portage/dev-util/maturin-1.15.0/temp/build.log`; no guard bypass or boot/kernel mutation occurred.

Profile-wave terminal failure (2026-09-20): `dev-util/rustup-1.29.0` completed the Rust instrumented build but segfaulted in `src_install` while generating shell completions (`./rustup completions bash`). No package merge, workload receipt, or profile payload was accepted. Full output and build log are retained at `/tmp/rustup.log` and `/var/tmp/gentoo-portage-build/portage/dev-util/rustup-1.29.0/temp/build.log`; no guard bypass or boot/kernel mutation occurred.

Profile-wave success (2026-09-20): `x11-terms/alacritty-9999` completed the exact successor Rust generation wave, passed install-QA ABI guarding and its representative `/usr/bin/alacritty --help` workload, and produced a root-owned receipt that passed independent wave/readiness verification. The Rust raw profile payloads remain sealed under the generation-bound spool for the native Rust validation path; no Clang merger was used. The profile remains pending native profile validation/use authorization.

Profile payload audit refresh (2026-09-20): after the successful Alacritty Rust wave, the complete live binding audit was rerun against `/tmp/policy.json` and the authenticated workload exclusions. Root-owned v5 evidence reports 286 profile-present, 251 workload-exclusion, and 6 missing-profile records; authorization remains pending-profile-collection. The remaining six are the previously failed `dev-util/maturin-1.15.0`, `dev-util/rustup-1.29.0`, `gnome-base/librsvg-2.62.3`, `media-libs/libjxl-9999`, `sys-apps/ripgrep-15.2.0`, and `sys-block/thin-provisioning-tools-1.3.1`.

Profile-wave terminal failure (2026-09-20): bounded retry `net-misc/curl-9999` passed the previously stalled Git fetch and reached configure, but the 120-second execution bound expired during the very large generated documentation/configure workload before merge or profile receipt. No package merge or profile payload was accepted. The retry output is preserved at `/tmp/curl2.log`; a future retry must use a longer but still bounded package-specific limit or a prepared source snapshot. No ABI-guard bypass or boot/kernel mutation occurred.

Profile-wave success (2026-09-20): bounded retry `net-misc/curl-9999` (`curl3`) completed the exact successor Clang IR generation wave after the earlier source-fetch and short-timeout failures. The instrumented package merged through the fail-closed install-QA ABI guard, its representative workload completed, and the root-owned receipt `profile-wave-receipt-curl3-v1.json` passed independent wave/readiness verification. LLVM 22 merged the authenticated raw payloads into `/var/lib/gentoo-optimization/merged-profiles/net-misc_curl-9999-v1.profdata`; merge evidence is recorded at `profile-merge-curl3-v1.json`. The profile remains pending validation/use authorization.

Profile payload audit refresh (2026-09-20): after the successful bounded `curl3` retry, the complete live binding audit was rerun against `/tmp/policy.json` and the authenticated workload exclusions. Root-owned v6 evidence reports 286 profile-present, 251 workload-exclusion, and 6 missing-profile records; the curl payload is authenticated but the remaining six are the previously failed `dev-util/maturin-1.15.0`, `dev-util/rustup-1.29.0`, `gnome-base/librsvg-2.62.3`, `media-libs/libjxl-9999`, `sys-apps/ripgrep-15.2.0`, and `sys-block/thin-provisioning-tools-1.3.1`. Authorization remains pending-profile-collection.

Profile-wave terminal failure (2026-09-20): bounded retry `sys-apps/ripgrep-15.2.0` again completed the Rust instrumented build but failed in `src_install` while the ebuild generated bash completion through `rg complete-bash`; no merge, workload receipt, or profile payload was accepted. Output is retained at `/tmp/ripgrep2.log` and the Portage build log. This confirms a package/eBuild completion-path defect rather than a profile-wave timeout; no ABI-guard bypass or boot/kernel mutation occurred.

Profile-wave terminal failure (2026-09-20): bounded retry `sys-block/thin-provisioning-tools-1.3.1` again completed the Rust instrumented compilation but failed in `src_install` because the ebuild attempted to install missing `target/release/pdata_tools`; no merge, workload receipt, or profile payload was accepted. Output is retained at `/tmp/thin2.log` and the Portage build log. This confirms the package's build/install path defect; no ABI-guard bypass or boot/kernel mutation occurred.

Repository validation repair (2026-09-20): the JSON-schema bootstrap and real-Git materialization fixtures were creating implicit `default.profraw` files because their controlled Git/publisher environments did not set `LLVM_PROFILE_FILE`. The publisher and both test helpers now bind that variable to `/dev/null`; the stale staged-worktree expectation was aligned with the current fail-closed exact-clean-commit rejection. Focused bootstrap and materialization suites pass (11 bootstrap tests plus the affected real-Git cases). A fresh portable-complete run is still executing its long recovery subsection; no authorization state is inferred from the partial run.

Portable validation repair (2026-09-20): the full recovery subsection passed, but the following Phase-2 evidence contract tests exposed the same implicit-profile contamination in the verifier's controlled Git inspection environment. `phase2-evidence.py` now binds `LLVM_PROFILE_FILE=/dev/null` for those subprocesses; the previously failing provenance fixture passes in isolation. The portable run's terminal Phase-2 evidence failures were caused by this environment defect and require a fresh full rerun after this correction.

### 2026-09-20 — portable-complete validation repaired and green

After binding `LLVM_PROFILE_FILE=/dev/null` consistently in the controlled
prerequisite, evidence-verifier, and phase-2 fixture environments, the fresh
`PATH=/usr/bin:/bin /usr/bin/bash tests/run-optimization-tests.sh --mode
portable-complete` run completed with `PASS=87`, `FAIL=0`, `SKIP=12`,
`TOTAL=99`, and `EXIT=0`. The recovery subsection independently reported
79 tests with 3 required skips and no failures; the phase-2 evidence contract
reported 47 tests with no failures. The strict framework installer, ABI guard,
BOLT command, transaction, and pre-strip fixture gates also passed. This
validates the repository's portable gate after the environment repair; it does
not authorize profile use, BOLT deployment, or completion of the remaining
Phase-3 profile payload collection.

### 2026-09-20 — libjxl Clang-lane compiler override repair

The `media-libs/libjxl-9999` profile wave was correctly rejected because the
package-specific `highway-noavx512.conf` forced `CC=gcc` even when the lane
requested Clang IR instrumentation. The env file now selects the reviewed
`clang-22`/`clang++-22` pair for `clang-ir-generate` and `clang-ir-use`, while
retaining the established GCC defaults for ordinary builds. The change passes
shell syntax validation and must be included in the next authenticated
framework publication before retrying the exact libjxl wave; no profile or
package mutation was claimed by the failed attempt.

Further libjxl retries preserved the next failure frontier. Removing the
package-env `LDFLAGS` replacement restored the global libc++ runtime link set,
and adding the existing no-hidden-visibility policy let the Clang IR build
complete both ABIs. The fail-closed ABI guard still rejected the staged DSOs:
the mutable `9999` source currently removes established `JXL_0` and C++/gcov
exports relative to the installed providers. No merge or profile receipt was
accepted. This remains an exact package-specific ABI/source-drift failure to
resolve or classify with separate evidence; the guard was not bypassed.

The subsequent libjxl retry used a final package-specific ABI policy and
reached the same guard with concrete `JXL_0` and C++ export loss. The failure
is therefore not a compiler-family or missing-runtime-link problem; it is a
replacement ABI/source-drift mismatch against the installed `9999` provider.
The failed receipt and build log remain preserved, and the package has not
been admitted as optimized.

The installed VDB metadata confirms libjxl was built with GCC 17 and a GCC
profile path, so the lane override now records `pgo-gcc` with reason
`installed-provider-abi-requires-gcc-lane`. A custom GCC wave reached the
correct dispatcher identity (`gcc`/`g++` 17), but its mutable `9999` Git source
fetch made no progress for nearly three minutes (zero CPU and unchanged fetch
output). The transaction was terminated before source admission; no merge,
receipt, or payload was accepted. The fetch output and prior ABI failures are
preserved for a future prepared-source retry.

The first framework publication after this source edit was intentionally
replaced with the exact inventory-bearing generation after the generic
installer's empty-policy fallback was detected. The active framework now binds
the current generation, inventory SHA, and clean commit. A libjxl retry then
proved the dispatcher selected `/usr/lib/llvm/22/bin/clang-22` and reached the
multilib link stage, but failed closed on unresolved `std::__1`/libc++ symbols
because the authenticated generated-policy copy still carried the prior mixed
GCC/C++ environment. No merge, receipt, or profile payload was admitted. The
generated policy must be regenerated from the corrected source before retrying.

The corrected successor libjxl retry used the installed GCC 17 provider lane,
the cached exact source commit `7741c8ce`, and the generation-bound GCC profile
path. The multilib build, install-QA ABI guard, package merge, and declared
wave completed successfully on 2026-09-20. The root-owned receipt
`profile-wave-receipt-libjxl-gcc-v3.json` passed independent wave/readiness
verification, and the sealed GCC payload contains 2.2 MiB of nonempty `.gcda`
files. This is profile-generation evidence only; validation and profile-use
authorization remain pending. The focused ABI/QA hook suite is green at 13/13.

Successor profile audit (2026-09-20): the verified GCC libjxl receipt was
bound into a generation-successor copy of the complete policy bindings, with
the prior Clang record left immutable. The independent payload verifier, run
against that successor and the authenticated workload exclusions, reports
287 `profile-present`, 251 `workload-exclusion`, and 5 `missing-profile`
records. The root-owned audit is
`profile-payload-audit-successor-20260920-v7.json` (SHA-256
`cda9b4bb0115048f7dd10a918361affd8fdf43b460c737db94990df9ad0d3bde`);
remaining missing records are maturin, rustup, librsvg, ripgrep, and
thin-provisioning-tools. The successor policy is not yet profile-use
authorized; the five package-specific failures remain subject to remediation
or exact terminal exclusion evidence.

The Rust mixed-link repair was exercised against `gnome-base/librsvg-2.62.3`
on 2026-09-20. Adding the generation path to the Rust lane's native linker
flags resolved the prior 32-bit `__llvm_profile_instrument_*` link failures,
but the instrumented 64-bit introspection helper then crashed with SIGSEGV
during Meson's `g-ir-scanner` step, and the wave produced no receipt. The
failure remains package-specific correctness evidence; the linker repair is
retained for mixed Rust/C packages, and librsvg remains one of the five
missing-profile records pending a safe package-specific profiling path.

The Rust host-layout repair was exercised successfully on
`sys-apps/ripgrep-15.2.0` (2026-09-20). Its Cargo target-qualified output was
made compatible with the ebuild's expected `target/release` layout; the
instrumented build, completion generation, install-QA, merge, and declared
workload completed. Receipt `profile-wave-receipt-ripgrep-v3.json` passed
independent verification. Successor payload audit v8 now reports 288
`profile-present`, 251 `workload-exclusion`, and 4 `missing-profile` records;
its root-owned SHA-256 is
`329f7b8f103c4ea837e935766d46b55c354761ad07c0c3e7fc505ee22a3fbefb`.

The Rust host-layout and workload corrections completed the
`sys-block/thin-provisioning-tools-1.3.1` wave on 2026-09-20. Cargo output was
made compatible with the ebuild install layout, and the invalid aggregate
`pdata_tools --help` recipe was replaced by the valid representative
`/usr/sbin/thin_check --help` command. The rebuilt package merged through
install-QA and the corrected receipt `profile-wave-receipt-thin-v5.json`
passed independent verification. Successor payload audit v9 reports 289
`profile-present`, 251 `workload-exclusion`, and 3 `missing-profile` records;
its root-owned SHA-256 is
`6c05ff872a44059fce5522e2db3447ee913fe026f01601946a2d3f25849472d0`.

Profile terminal-state machinery (2026-09-20): `verify-profile-payloads.py`
now accepts a separately hashed terminal-exclusion artifact and emits explicit
`terminal-exclusion` records with evidence references, rather than conflating
package correctness failures with workload exclusions. The successor artifact
`profile-terminal-exclusions-successor-20260920-v1.json` records the repeated
maturin and rustup completion-generation SIGSEGV failures and the librsvg
instrumented introspection-helper SIGSEGV after its mixed-link remediation.
The resulting root-owned payload audit v10 reports 289 `profile-present`, 251
`workload-exclusion`, and 3 evidence-backed `terminal-exclusion` records, with
zero missing profiles and authorization state `ready-for-profile-validation`.
Audit SHA-256: `d1b9692d514344cf6bf435e00870f5613c2a658d65d751ef3e8a8ad8e2f5096b`.
This clears profile collection accounting only; profile validation and BOLT
eligibility/deployment remain outstanding.

Profile validation checkpoint (2026-09-20): the prior libjxl GCC manifest was
correctly rejected as stale/noncanonical because its payload digest and
fingerprint predated the final GCC retry. Regenerating the canonical
`gentoo-optimization-profile-v1` manifest from the verified GCC payload and
running the independent validator as root now passes. The accepted successor
artifacts are `profile-manifest-libjxl-gcc-v2.json` and its metadata sidecar in
the root-owned generation directory. This validates one GCC profile only; the
full profile set and profile-use publication remain outstanding.

Clang profile validation batch (2026-09-20): the canonical profile producer was
run against the current generation bindings, exact Clang 22/compiler and
`llvm-profdata` identities, and authenticated merged payloads. It produced
184 canonical manifests; the independent verifier revalidated all 184 with
zero failures. Ninety additional Clang payload-present records currently have
raw payloads but no authenticated merged `.profdata` artifact, so they remain
outside the validated set rather than being inferred as valid. Root-owned
summaries are `profile-validation-clang-results-20260920.json` (SHA-256
`bb9e059c52eb32c3499c56e652bfe4f5f412af890626c312ecf73e26fe6e9b07`) and
`profile-validation-clang-verify-20260920.json` (SHA-256
`e7f9c25351f34f570dbb1a2395b7a80713d24ce9af32327584f0bcfd90176c58`).
Profile-use activation remains pending until the remaining raw payloads are
merged and validated, and Rust validation is separately required.

Clang raw-payload reconciliation (2026-09-20): the 90 current binding records
without merged `.profdata` were matched against the authoritative plan's
retained wave outcomes. Forty-nine have explicit terminal failure, bounded
fetch-stall, ABI-rejection, configure-failure, or workload-terminal entries;
together with the three previously hashed package correctness exclusions,
they are now represented in successor terminal-exclusion artifact v3. The
payload verifier reports 240 `profile-present`, 251 `workload-exclusion`, and
52 `terminal-exclusion`, with zero missing-profile records. Artifact SHA-256:
`4cdd19e6d954cc47cfd8810eab9e636aa8a99068785e9b7cbec9c862ace4c197`.
Thirty-eight raw-only records have no matching terminal evidence and remain
unclassified for validation; no profile-use authorization is inferred.

Raw-only disposition pass (2026-09-20): thirteen additional records were
matched to explicit retained plan entries documenting a rejected workload,
ABI/configure/compile failure, or bounded fetch abort. They were added to the
successor terminal artifact v6 with plan-entry hashes; successful narrative
entries and four packages with no explicit current disposition remain outside
that artifact. The resulting payload audit v13 reports 227
`profile-present`, 251 `workload-exclusion`, and 65 `terminal-exclusion`, with
zero missing-profile records. Artifact SHA-256:
`7db45c4781b8dc43c66d9808a89dd030a6c241412b576d6599ddde036821a78d`.
The four remaining raw-only records require fresh merge/receipt recovery or a
separate exact terminal record.

Rust profile-wave success (2026-09-20): `dev-util/bindgen-0.72.1` completed the
exact successor Rust generation wave under the current post-sync inventory,
passed install-QA and its representative workload, and produced
`profile-wave-receipt-bindgen-v1.json`. Independent receipt/readiness
verification passed. The receipt is bound to inventory SHA
`2d1408c587668cdca9e0138a07ea45af8201c71390054da9eec00693f756e701` and wave
SHA `90cb9552b3269cc87f68995c686d83367f383daa965627d491290b6e7b3b693c`.
The native Rust raw payload remains sealed for the required Rust-specific
validation path; no Clang merger or profile-use authorization is inferred.

Rust validation environment checkpoint (2026-09-20): the bindgen wave's native
payload is present and receipt-verified, but this host currently exposes only
LLVM `llvm-profdata` 20/21/22 while the exact Rust compiler identity is
bundled LLVM 23.1.1. Attempting to inspect the payload with LLVM 22 fails
closed with raw profile format version 11 versus expected version 10. The
payload is retained; no incompatible tool was used to merge or validate it,
and this evidence does not authorize profile use. A matching LLVM 23 profile
consumer must be made available before Rust payload validation can proceed.

Portable recovery-suite validation (2026-09-20): the real-host recovery unit
subsuite completed independently under the repaired test driver with 79 tests,
0 failures, and 3 explicitly expected portable skips in 1,043.933 seconds.
The suite covered checkpoint crash/reconciliation, selector and witness
binding, lock ownership, process-group teardown, tamper rejection, and
idempotent offline finalization. This result is recorded as a recovery-suite
result only; it does not by itself establish the terminal result of the parent
portable-complete run.

Portable-complete gate (2026-09-20): after the recovery suite completed, the
parent run finished successfully with 87 passes, 0 failures, 12 explicit
capability skips, 530 required subtest passes, 25 required subtest skips, and
exit status 0. The authoritative run remained separate; no Phase-2 or
profile-use authorization is inferred from this portable result.

Profile-use deployment (2026-09-20): `app-arch/lz4-1.10.0-r1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed successfully with `mode=clang-ir-use active_mode=clang-ir-use` and `>>> app-arch/lz4-1.10.0-r1 merged.` The shell pipeline's zsh status capture was malformed (`use_rc` printed empty), but the emerge process completed and the required log assertions passed; this is recorded as a command-wrapper reporting defect, not a package failure. The receipt and merge evidence remain bound to the current generation and inventory.

Profile-use deployment (2026-09-20): `app-arch/ncompress-5.0-r2` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, `mode=clang-ir-use active_mode=clang-ir-use`, and `>>> app-arch/ncompress-5.0-r2 merged.`

Profile generation checkpoint (2026-09-20): `app-arch/rpm2targz-2021.03.16` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild remains the next execution step for this package.

Profile-use deployment (2026-09-20): `app-arch/rpm2targz-2021.03.16` completed the exact successor Clang IR profile-use rebuild with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use` markers, and a successful package merge. The generation, receipt, merged profile, validation, dispatcher, and live profile-use evidence are all bound to the current generation and inventory.

Profile-use deployment (2026-09-20): `app-arch/tar-1.35-r1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and `>>> app-arch/tar-1.35-r1 merged.`

Profile-use deployment (2026-09-20): `app-arch/unzip-6.0_p31` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-arch/xz-utils-9999` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The first upstream GitHub fetch stalled and was interrupted; the ebuild's configured git.tukaani.org fallback completed the fetch and the source was checked out at commit `3b1efb04d17c3a9ef7f473d73af13f1531428ffe`. The exact profile-use rebuild then completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-arch/zip-3.0_p16` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-arch/zstd-1.5.7-r1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-crypt/argon2-20190702-r1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-crypt/gcr-4.4.0.1-r1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge. The resolver again reported the pre-existing SPIR-V 1.4.357 versus installed 1.4.350 consumer conflict; it was not altered by this isolated package deployment.

Profile-use deployment (2026-09-20): `app-crypt/gnupg-2.5.22` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-crypt/gpgme-2.2.0` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-crypt/pinentry-1.3.3` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-crypt/rhash-1.4.6-r1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-i18n/uchardet-0.0.8` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-misc/ddcutil-2.2.6` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge. Its pre-merge check only inspected the existing `/usr/src/linux` source read-only; no kernel lifecycle mutation was performed.

Profile-use deployment (2026-09-20): `app-misc/evtest-1.36` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-misc/fastfetch-2.68.1-r1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-misc/jq-1.8.2` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-wave success (2026-09-20): `app-portage/cpuid2cpuflags-18` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, and produced authenticated raw payloads. The fresh merge completed with LLVM 22 and is retained as generation-bound evidence; dispatcher publication/profile-use remains pending because the canonical validator requires the merged-profile path and merge-evidence generation binding to be regenerated together under the trusted cache root.

Profile-use deployment (2026-09-20): `app-portage/cpuid2cpuflags-18` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge. The initial publication retry exposed and corrected a trusted-cache path binding error; the final manifest, metadata, merge evidence, and dispatcher all now point to the same immutable cache-root profile.

Profile-use deployment (2026-09-20): `app-portage/eix-0.36.9` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge. The first use attempt exposed a root-only merge-evidence file mode; correcting the evidence to authenticated readable state allowed the fail-closed verifier to proceed.

Profile-use deployment (2026-09-20): `app-portage/portage-utils-9999` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge. The first use attempt exposed root-only merge-evidence permissions; correcting the authenticated evidence mode allowed the rebuild to proceed.

Profile-use deployment (2026-09-20): `app-shells/bash-9999` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The first profile-use attempt correctly failed closed because the package-specific `pgo` USE path added `-fprofile-generate` alongside the optimizer's `-fprofile-use`; the retry used the narrow package-specific `USE=-pgo` state, completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-shells/dash-9999` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-shells/quoter-4.2` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-shells/zsh-9999` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. LLVM reported expected counter-mismatch warnings while merging this large multi-module profile; validation and dispatcher publication still completed. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use deployment (2026-09-20): `app-text/dos2unix-7.5.6` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use terminal failure (2026-09-20): `app-text/enchant-2.8.16` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild reached install-QA but the fail-closed ABI guard rejected the replacement `usr/lib64/enchant-2/enchant_hunspell.so` for exported ABI loss: old symbol count 14, new count 13, missing `_ZZNSt3__16vectorINS_12basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEEENS4_IS6_EEE12emplace_backIJS6_EEERS6_DpOT_ENKUlvE0_clEv`. The package remains terminally excluded from profile-use deployment with the Portage log and ABI-guard evidence retained; no guard bypass was attempted.

Profile-use terminal failure (2026-09-20): `app-text/hunspell-1.7.2-r1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild reached install-QA but the fail-closed ABI guard rejected `usr/lib64/libhunspell-1.7.so.0.0.1` and its SONAME symlinks for exported ABI loss: old=104, new=103, missing `_ZNSt3__118basic_stringstreamIcNS_11char_traitsIcEENS_9allocatorIcEEED1Ev`. The package remains terminally excluded from profile-use deployment with the complete Portage log and ABI-guard evidence retained; no guard bypass was attempted.

Profile-use deployment (2026-09-20): `app-text/libpaper-2.1.3` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild completed with `use_rc=0`, authenticated `mode=clang-ir-use active_mode=clang-ir-use`, and a successful package merge.

Profile-use terminal failure (2026-09-20): `app-text/lowdown-3.1.1` completed the exact successor Clang IR generation wave, passed readiness and receipt verification, merged and independently validated its authenticated profile, and was published through the dispatcher. The exact profile-use rebuild failed in the package compile phase at `compats.c:3272` with `#error No getprogname available.` The complete Portage build log is retained as evidence; no profile or ABI gate was bypassed.

### Yad profile-use generation fetch stall (2026-09-20)
The exact `gnome-extra/yad-9999` Clang-IR generation wave passed readiness and entered the authenticated replacement transaction, but the live ebuild stalled during the upstream `v1cont/yad.git` fetch for more than three minutes. The fetch and owning wave were terminated with signal 15 before unpack completed; no package merge, profile receipt, or ABI decision was admitted. This is retained as a source-fetch execution failure requiring a cached-source or fresh-fetch retry, not an optimization-policy bypass.

### Grim profile-wave workload failure (2026-09-20)
The exact `gui-apps/grim-9999` Clang-IR generation transaction fetched, built, passed install-QA and the ABI guard, and merged successfully. Its reviewed `/usr/bin/grim --help` workload then exited 1 in the live environment, so the runner refused to seal an authoritative receipt or publish a profile. No profile-use rebuild was attempted; the workload failure is retained as terminal execution evidence pending a compositor-capable retry.

### Swayidle profile-wave workload failure (2026-09-20)
The exact `gui-apps/swayidle-9999` Clang-IR generation transaction fetched, built, passed install-QA and the ABI guard, and merged successfully. Its reviewed `/usr/bin/swayidle --help` workload exited 255 in the live session, so the runner refused to seal an authoritative receipt or publish a profile. No profile-use rebuild was attempted; the workload failure is retained as terminal execution evidence pending a session-capable retry.

### Wayland workload classification repair (2026-09-20)
The live `grim --help` and `swayidle --help` waves both built and merged successfully but exited nonzero because no compositor/socket session exists in the automated userspace boundary. The workload derivation now classifies `gui-apps/grim-*`, `gui-apps/swayidle-*`, and `gui-apps/swaylock-*` as explicit `no-profile-producing-workload` records until a deterministic compositor-backed fixture is available, instead of emitting recipes that are known to fail. Regenerated workload state reports 288 recipe-ready, 238 no-runnable-entrypoint, and 12 explicit no-profile-producing-workload records; no failed workload is treated as a successful profile.

### Mandoc Clang-IR generation wave (2026-09-20)
The exact `app-text/mandoc-1.14.6-r1` wave passed readiness, rebuilt and merged through install-QA and the ABI guard, and its `/usr/bin/mandoc -h` workload completed. The receipt passed independent verification and LLVM 22 merged the authenticated payload; merge evidence digest is `1f48b3150cc6a77045081115527f5ee5c2853d881fbeac6a507ef88fcec769d1`. Profile validation, dispatcher publication, and exact profile-use deployment remain pending.

### Mandoc profile-use deployment (2026-09-20)
The validated `app-text/mandoc-1.14.6-r1` profile was published through the generation-bound dispatcher with record SHA-256 `31ba281f3ac7b79aad6a98374d550d03df0c25cc3cffd00a3d7f5f41a39df78a`. The exact rebuild completed with `use_rc=0`, reported `clang-ir-use` active and the authenticated profile path, passed install-QA and the ABI guard, and merged successfully. No BOLT deployment is claimed.

### OpenSP profile-wave compile failure (2026-09-20)
The exact `app-text/opensp-1.5.2-r10` Clang-IR generation wave passed readiness but failed during the compile/link phase before install-QA. `ld.lld` reported unresolved C++ runtime symbols including `__gxx_personality_v0`, `operator new/delete`, and C++ ABI vtables while linking `onsgmls`; the complete root-owned Portage log is retained at `/var/tmp/gentoo-portage-build/portage/app-text/opensp-1.5.2-r10/temp/build.log`. No package merge, workload receipt, profile, or dispatcher publication was admitted.

### OpenSP C++ driver remediation boundary (2026-09-20)
The first OpenSP retry showed the generated C++ link still used the C driver because the live `/etc/portage/bashrc` was an older framework copy. A source repair now derives the matching versioned `clang++` driver, but activating that repair requires a complete successor framework publication and strict check; the active framework was restored immediately after the unbound-policy refusal, and no package mutation was admitted in that attempt. The original OpenSP compile failure and both retry logs remain preserved.

### OpenSP successor-framework retry result (2026-09-20)
The source C++ driver repair was published through the normal root-owned framework installer and the independent strict `--check` passed. A fresh OpenSP retry then reached the same C++ link failure (`__gxx_personality_v0` and C++ ABI references unresolved) before install-QA, so the driver repair does not yet resolve this legacy libtool link contract. No package merge or profile receipt was admitted; the repeated compile evidence remains retained for a package-specific link remediation rather than another blind retry.

### OpenSP package-specific link remediation (2026-09-20)
The successor-framework retry confirmed that OpenSP's legacy libtool link rules omit the C++ runtime even when the CXX tag is selected. A narrow package environment was added: `app-text/opensp` now uses `clang++-22` explicitly and appends `-lstdc++` to its link flags. This source change is committed as `6002ee7`; it requires successor framework publication and strict checking before another live retry.

### OpenSP package-environment retry (2026-09-20)
The successor framework containing the OpenSP package environment was installed and independently strict-checked. The fresh retry still rendered the libtool CXX link as `/usr/lib/llvm/22/bin/clang-22` with no `-lstdc++`, proving the generated-policy package-environment tree did not yet consume the new source mapping. The compile failed before install-QA again; no package or profile was admitted. The next implementation step is to regenerate the content-addressed generated policy itself, republish it, and then retry once under that policy.

### OpenSP generated-policy remediation boundary (2026-09-20)
The generated policy was rebuilt with a canonical CPV mapping for OpenSP, but the framework installer correctly rejected the attempted environment because project policy forbids assigning `CXX` and custom compiler/link flags outside the reviewed public-ABI lane. The package-specific workaround therefore cannot be activated through the current architecture without a broader policy redesign. The source-level and generated-policy attempts are preserved; OpenSP remains an evidence-backed correctness failure after remediation, with no package/profile admission from the failed retries.

### scdoc Clang-IR generation wave (2026-09-20)
The exact `app-text/scdoc-9999` wave passed readiness, rebuilt and merged through install-QA and the ABI guard, and completed its deterministic fixture workload. The receipt passed independent verification and LLVM 22 merged the authenticated payload; merge evidence digest is `9478281bb9856904d278e98190fd826bb75479594f152c1551c9913d3700e53a`. Profile validation, dispatcher publication, and exact profile-use deployment remain pending.

### scdoc profile-use deployment (2026-09-20)
The validated `app-text/scdoc-9999` profile was published through the generation-bound dispatcher with record SHA-256 `61e6750809f2414ad18d305f8b2ef434d32702a5478a86cd299673a8df0948d7`. The exact rebuild completed with `use_rc=0`, reported `clang-ir-use` active and the authenticated profile path, passed install-QA and the ABI guard, and merged successfully. No BOLT deployment is claimed.

### xmlto Clang-IR generation wave (2026-09-20)
The exact `app-text/xmlto-0.0.28-r11` wave passed readiness, rebuilt and merged through install-QA and the ABI guard, and completed its reviewed `/usr/bin/xmlif --help` workload. The receipt passed independent root verification and LLVM 22 merged the authenticated payload; merge evidence digest is `57e5db6bf101a1a15559b0db675425ea4732976a96b04121aa39e8f0cc15e715`. Profile validation, dispatcher publication, and exact profile-use deployment remain pending.

### xmlto profile-use deployment (2026-09-20)
The validated `app-text/xmlto-0.0.28-r11` profile was published through the generation-bound dispatcher with record SHA-256 `f0de3c2ce716ac1e42910d05190ae1c3ff60ce164b03ee811e3921fdb0497eff`. The exact rebuild completed with `use_rc=0`, reported `clang-ir-use` active and the authenticated profile path, passed install-QA and the ABI guard, and merged successfully. No BOLT deployment is claimed.

### Yodl profile-wave compile failure (2026-09-20)
The exact `app-text/yodl-4.05.00` Clang-IR wave passed readiness but failed in the package compile/link phase before install-QA. `ld.lld` reported unresolved libc++ `std::__1` stream symbols and the build terminated with `programs failed`; the complete root-owned log is retained at `/var/tmp/gentoo-portage-build/portage/app-text/yodl-4.05.00/temp/build.log`. No package merge, workload receipt, profile, or dispatcher publication was admitted.

### b2 Clang-IR generation wave (2026-09-20)
The exact `dev-build/b2-5.5.3` wave passed readiness, rebuilt and merged through install-QA and the ABI guard, and completed its `/usr/bin/b2 --help` workload. The receipt passed independent root verification and LLVM 22 merged the authenticated payload; merge evidence digest is `c99db5918dd6d04c089ff6887d92f79cc844073d9154fbb10790c955ade9340f`. Profile validation, dispatcher publication, and exact profile-use deployment remain pending.

### b2 profile-use deployment (2026-09-20)
The validated `dev-build/b2-5.5.3` profile was published through the generation-bound dispatcher with record SHA-256 `e60c964a3e9ef415f93ac75269f2dd78ee6086836396d54dc2f60c9e71a29c42`. The exact rebuild completed with `use_rc=0`, reported `clang-ir-use` active and the authenticated profile path, passed install-QA and the ABI guard, and merged successfully. No BOLT deployment is claimed.

### bmake Clang-IR generation wave (2026-09-20)
The exact `dev-build/bmake-20260508` wave passed readiness, rebuilt and merged through install-QA and the ABI guard, and completed its reviewed `bmake -V MAKE_VERSION` workload. The receipt passed independent root verification and LLVM 22 merged the authenticated payload; merge evidence digest is `5d44bc12f2784c80072ebbcc126c83f93677d0802e1d5351fd1fea16616f3e24`. Profile validation, dispatcher publication, and exact profile-use deployment remain pending.

### bmake profile-use deployment (2026-09-20)
The validated `dev-build/bmake-20260508` profile was published through the generation-bound dispatcher with record SHA-256 `7ff82447d711ebfb69215890789e9b0dc31822a42e2183b413c1b610a0d418e1`. The exact rebuild completed with `use_rc=0`, reported `clang-ir-use` active and the authenticated profile path, passed install-QA and the ABI guard, and merged successfully. No BOLT deployment is claimed.

### CMake profile-use terminal failure (2026-09-20)
The exact `dev-build/cmake-4.3.5` Clang-IR generation wave passed readiness, completed the replacement build and workload, passed independent receipt verification, merged under LLVM 22, validated against the exact compiler/tool identities, and was published through the generation-bound dispatcher with record SHA-256 `d414d491928fb0a5dff8d13f6041a41217203348cde2b3cefb15dd29d4368722`. The exact profile-use rebuild authenticated the dispatcher and reached the configure phase, but CMake rejected the profile-use compiler as not supporting C++11 (`std::unique_ptr`) and exited before compilation. The complete log is retained at `/var/tmp/gentoo-portage-build/portage/dev-build/cmake-4.3.5/temp/build.log`; no profile-use merge is claimed.

### icmake Clang-IR generation and publication (2026-09-20)
The exact `dev-build/icmake-9.03.01-r1` wave passed readiness, rebuilt and merged through install-QA and the ABI guard, and completed both reviewed help workloads. Receipt verification and LLVM 22 profile merge passed; validation accepted the exact identity and the generation-bound dispatcher published record SHA-256 `3a14a1a32efb761efcca4adeb59ad15ac93924a63ec4c7311c5cd3ffd7b2ac94`. Exact profile-use deployment remains pending.

### icmake profile-use deployment (2026-09-20)
After correcting the retained merge-evidence permissions, the exact `dev-build/icmake-9.03.01-r1` profile-use rebuild authenticated `clang-ir-use`, emitted the expected unprofiled-file warnings for uncovered translation units, passed install-QA and the ABI guard, and merged successfully with `use_rc=0`.

### make generation-wave terminal compile failure (2026-09-20)
The exact `dev-build/make-9999` Clang-IR generation wave passed readiness and entered the authenticated replacement build, but GNU Make's bootstrap/compile phase failed before install-QA. The retained Portage log reports the profile-use instrumentation warning for `arscan.c` as an error under the package's strict compile flags; no merge, workload receipt, profile, or dispatcher publication was admitted. The failure log remains at `/var/tmp/gentoo-portage-build/portage/dev-build/make-9999/temp/build.log`.

### SQLite profile-use deployment (2026-09-20)
The exact `dev-db/sqlite-3.53.4` Clang-IR wave passed readiness, rebuilt and merged through install-QA and the ABI guard, passed independent receipt verification, merged under LLVM 22, validated, and was published through the generation-bound dispatcher with record SHA-256 `40a93c704dbfb70991c2d207c6fa3ae25093b76e8a2cf48bf5388edce2e8c07b`. The exact profile-use rebuild authenticated `clang-ir-use`, completed the multilib build and install, and merged successfully with `use_rc=0`; expected `default.profraw` warnings were non-fatal.

### strace profile-wave source-fetch terminal failure (2026-09-20)
The exact `dev-debug/strace-9999` Clang-IR generation wave entered the authenticated replacement transaction but stalled during the live `git fetch https://github.com/strace/strace.git` for more than four minutes with zero CPU and no output. The owning fetch and wave were terminated with signal 15 before source unpack completed; no package merge, receipt, workload, profile, or dispatcher publication was admitted. The fetch failure is retained in the wave and Portage logs for a cached-source or fresh-fetch retry.

### libdisasm profile-use deployment (2026-09-20)
The exact `dev-embedded/libdisasm-0.23-r1` profile-use rebuild authenticated `clang-ir-use`, completed successfully, passed install-QA and the ABI guard, and merged with `use_rc=0`. The dispatcher record was `5e9625bd5936693db7bbd83b4049553d1c022f6de97f00d39b121745440225ad`; ldconfig's existing non-ELF optimization-record warnings were non-fatal.

### Deno profile-use deployment (2026-09-20)
The exact `dev-lang/deno-bin-2.9.6` profile-use rebuild authenticated `clang-ir-use`, completed the binary package replacement, passed install-QA and ABI checks, and merged successfully with `use_rc=0`. Dispatcher record SHA-256 was `ff0a711c4f8381bcfd54ff1ae9b7219b6eeea51130582b3fd4ad1b1d0b3d849e`.

### Lua profile-wave workload terminal failure (2026-09-20)
The exact `dev-lang/lua-5.4.8` Clang-IR generation transaction rebuilt and merged through install-QA and the ABI guard, but its reviewed `/usr/bin/lua5.4` workload exited 1. The runner therefore refused to seal a profile receipt or publish a profile; no profile-use rebuild was attempted. The package remains terminally excluded pending a corrected deterministic workload fixture.

### LuaJIT generation terminal ABI failure (2026-09-20)
The exact `dev-lang/luajit-2.1.9999999999` generation build completed, but install-QA's fail-closed ABI guard rejected the replacement DSO and SONAME symlinks for exported ABI loss: old/new symbol counts were both 148, but `luaJIT_version_2_1_1782726002` was missing from the staged provider. No merge, workload receipt, profile, or dispatcher publication was admitted.

### NASM profile-wave workload terminal failure (2026-09-20)
The exact `dev-lang/nasm-3.02` Clang-IR generation transaction rebuilt and merged through install-QA and the ABI guard, but its reviewed `/usr/bin/ndisasm` workload exited 1. The runner refused to seal the authoritative receipt or publish a profile; no profile-use rebuild was attempted.

### ORC profile-use deployment (2026-09-20)
The exact `dev-lang/orc-0.4.42` profile-use rebuild authenticated `clang-ir-use`, completed both ABI variants, passed install-QA and the ABI guard, and merged successfully with `use_rc=0`. Dispatcher record SHA-256 was `3e4e53fe8fd6df4cd2c55fe3842f9cd0fb5803a67f6a67a91950b59598dd35c4`; existing ldconfig warnings about non-ELF optimization records were non-fatal.

### sassc profile-use deployment (2026-09-20)
The exact `dev-lang/sassc-3.6.2` profile-use rebuild authenticated `clang-ir-use`, completed successfully, passed install-QA and the ABI guard, and merged with `use_rc=0`. Dispatcher record SHA-256 was `bb0b472d0d76b69a5ed4f6f1f05dd5eba15bf5a9be0ed0dbb36e815ee0fe53b2`.

### SWIG profile-use deployment (2026-09-20)
The exact `dev-lang/swig-4.4.1` profile-use rebuild authenticated `clang-ir-use`, completed successfully, passed install-QA and the ABI guard, and merged with `use_rc=0`. Dispatcher record SHA-256 was `5e3cea854473acd40f783116b0436f6e622b26d11effee435cbcf78bdbd63568`.

### Tcl profile-wave workload terminal failure (2026-09-20)
The exact `dev-lang/tcl-8.6.17` Clang-IR generation transaction rebuilt and merged through install-QA and the ABI guard, but its reviewed `/usr/bin/tclsh8.6` workload produced no output. The runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted.

### Tk profile-use deployment (2026-09-20)
The exact `dev-lang/tk-8.6.17` profile-use rebuild authenticated `clang-ir-use`, completed both ABI variants and the extensive install step, passed install-QA and the ABI guard, and merged with `use_rc=0`. Dispatcher record SHA-256 was `762b95989dea6a24ceb9a3ab1a764a3c24b5baa7d0a6f6052caf0dd22c6302e4`.

### Vala profile-use deployment (2026-09-20)
The exact `dev-lang/vala-0.56.19` profile-use rebuild authenticated `clang-ir-use`, completed successfully, passed install-QA and the ABI guard, and merged with `use_rc=0`. Dispatcher record SHA-256 was `70c7bb3e0723e92ac71f4e6a85ec0f7eff1011dd8c45218b0a4b871d7b70ba48`.

### Yasm profile-wave workload terminal failure (2026-09-20)
The exact `dev-lang/yasm-1.3.0-r2` Clang-IR generation transaction rebuilt and merged through install-QA and the ABI guard, but its reviewed `/usr/bin/ytasm` workload exited 1. The runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted.

### AppStream profile-use deployment (2026-09-20)
The exact `dev-libs/appstream-1.0.6` profile-use rebuild authenticated `clang-ir-use`, completed successfully, passed install-QA and the ABI guard, and merged with `use_rc=0`. Dispatcher record SHA-256 was `352498878481b3e47e47f3b6721dc6474e940a824746f3122c5ec72b36e6a28c`.

### AppStream GLib profile-use deployment (2026-09-20)
The exact `dev-libs/appstream-glib-0.8.3` profile-use rebuild authenticated `clang-ir-use`, completed successfully, passed install-QA and the ABI guard, and merged with `use_rc=0`. Dispatcher record SHA-256 was `eb5e90748b5e1e791be2edfeb1725cd4302e4a4e4c963daf6f9b7d7705c86134`.

### D-Bus GLib profile-use deployment (2026-09-20)
The exact `dev-libs/dbus-glib-0.114` profile-use rebuild authenticated `clang-ir-use`, completed both ABI variants, passed install-QA and the ABI guard, and merged with `use_rc=0`. Dispatcher record SHA-256 was `f9e1ebefb22ccfd2d9e017d20c1b1b7f35a66e57733162561d00c9d45537b286`.

### 2026-09-20 — elfutils clang-IR generation and profile-use

`dev-libs/elfutils-0.196` completed the active clang-IR generation wave under inventory `phase3-live-candidate-20260920-sway-mesa-abi-reviewed-v1`. The authenticated wave receipt passed `verify-wave-receipt.py`; llvm-profdata merge produced evidence digest `b1d0a69d4f968fb4c1b9f2d0a88561e89e2e430c1053d883fc36a09226677dc4`; the profile manifest and metadata were validated and published through the dispatcher. A real profile-use reinstall completed successfully with `use_rc=0`. The package remains userspace-only; no boot, kernel, firmware, or initramfs state was touched.

### 2026-09-20 — doas generation receipt reconciliation

`app-admin/doas-6.8.2` already had an immutable completed generation receipt and published candidate profile-use dispatcher artifacts for the active generation (`app-admin_doas-6.8.2-final2.json`, inventory `dac34ff8f1a63460ded44fda9d0879c8e04d33ca3fe5aaac891ba7a8fa4a9a6a`). A repeated execution correctly refused to overwrite the completed receipt; the package transaction itself completed successfully. Existing dispatcher artifacts remain authoritative and were not overwritten.

### 2026-09-20 — expat clang-IR generation and profile-use

`dev-libs/expat-2.8.4` completed the authenticated clang-IR generation wave for inventory `phase3-live-candidate-20260920-sway-mesa-abi-reviewed-v1`; receipt verification passed, llvm-profdata merge produced evidence digest `67f844e1c061f7ee6659e9cc83c187ca888497802ea05428a1e8772ebeb01bd6`, and the validated dispatcher record was published with SHA-256 `3aff2869c29b1124cd32d694c83c5bbd4450baf8d7a2a16aec657e8a44d8bf86`. The authenticated profile-use reinstall completed successfully (`use_rc=0`).

### 2026-09-20 — flatbuffers profile-use correctness failure

`dev-libs/flatbuffers-25.12.19` completed authenticated Clang-IR generation, receipt verification, profile merge, manifest validation, and dispatcher publication (record SHA-256 `650ba517ad117fa9b2e4457d29c569155250f2761d2277dd504544d85cc17051`). Its exact `clang-ir-use` rebuild reached install-QA but the fail-closed ABI guard rejected the staged `libflatbuffers.so.25.12.19` replacement: five exported symbols disappeared (`old=212`, `new=207`). The package was not admitted and no profile-use success is claimed; the complete failure log is `/tmp/dev-libs_flatbuffers-25.12.19-profile-use.log`. This is a correctness-failure-after-remediation candidate requiring package-specific ABI remediation before deployment.

### 2026-09-20 — fribidi clang-IR generation and profile-use

`dev-libs/fribidi-1.0.16` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM 22 merge evidence digest was `57b779a3483556f30d6e73926c7ecc4ad3299934ce1477754825f3112788a743`, and dispatcher publication succeeded with record SHA-256 `a6aeb6da25159bec7a126d983e219f06ca3f317d8e6d35daaf22173b02f9173d`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and the ABI guard, and merged with `use_rc=0`.

### 2026-09-20 — json-glib clang-IR generation and profile-use

`dev-libs/json-glib-1.10.8` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `578137dfd39606f315d72a3ad6149c3be2638faed853bd9fd60fe18993b8ae07`, and dispatcher publication succeeded with record SHA-256 `fa75f6e26e735e863d7ca5641c492517d829e6ddc9534367b9cb98e5ddbdd583`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-20 — libgcrypt profile-use 32-bit compile failure

`dev-libs/libgcrypt-1.12.4` completed authenticated Clang-IR generation, receipt verification, profile merge (digest `9a281d7d3c14f5e30fe0579b24bc74bdb0073875d4ea5bc44c8004bfac2d2407`), manifest validation, and dispatcher publication (record SHA-256 `458bb3398f477fefab0b8e1a16685d3d504e76c3afa728b4152b65002fbfcb12`). Its exact profile-use rebuild authenticated `clang-ir-use` but failed during the package's 32-bit ABI compile: `fips.c` reported that libgcrypt requires thread-local storage for FIPS mode, followed by undeclared `the_tc` errors. No profile-use merge was admitted; the complete log is `/tmp/dev-libs_libgcrypt-1.12.4-profile-use.log`. This is a package/toolchain correctness failure requiring remediation, with the generated profile retained.

### 2026-09-20 — libgpg-error clang-IR generation and profile-use

`dev-libs/libgpg-error-1.61` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `e7d98e3730e42b9d2406546a59161e473963e8e1a2352b37b125fd8cf889006e`, and dispatcher publication succeeded with record SHA-256 `d1ab7b32a62b27467965d55a1a0422b66f24a294b455eb49e6358bada3d7a5ef`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-20 — libpcre clang-IR generation and profile-use

`dev-libs/libpcre-8.45-r4` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `38d8cf9a5d56cc2756e490a6581f50df9394fafd7fb09848e9b0333a3f4955a7`, and dispatcher publication succeeded with record SHA-256 `2ac82bbcd3bcb8a572989410b1b38539376c59c50a15397440c48e09108743d6`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-20 — libpcre2 clang-IR generation and profile-use

`dev-libs/libpcre2-10.48` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `6d6bdf9486e4491d638d51332dacd28b53e038764f2f6f99dd399d9711d03bdd`, and dispatcher publication succeeded with record SHA-256 `d91bdca956b505c97b0cd8033e6c41b1f023578ea34740f2d5a8dc642270716d`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-20 — libtasn1 clang-IR generation and profile-use

`dev-libs/libtasn1-4.21.0` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `46b5cb09cc9f0fccf0486c08953982b92c6aa8603c34879d121d3c383f9ea422`, and dispatcher publication succeeded with record SHA-256 `5eff638c6be6f0095b93e53a2b7bc3745c38dbceca521c05adb25d753303b394`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-20 — nettle clang-IR generation and profile-use

`dev-libs/nettle-3.10.2` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `f001cab29d0bf946c28aed2c85a970ce14e24cd355b6b8177f6ee8eb7413332a`, and dispatcher publication succeeded with record SHA-256 `6b7c7e189989dce6d77810907f8cd70afe921e4080dd2cf4f6128f196c84e42f`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — NSS workload terminal failure

`dev-libs/nss-3.129` completed its authenticated Clang-IR generation build and install-QA path, but the reviewed workload recipe `/usr/bin/addbuiltin` exited `255`. The wave runner therefore refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The failure is preserved in the generation runner output and remains a workload-specific terminal exclusion pending a corrected deterministic workload fixture. No optimization or ABI gate was bypassed.

### 2026-09-21 — OpenCL ICD loader workload terminal failure

`dev-libs/opencl-icd-loader-2026.05.29` completed its authenticated Clang-IR generation build and install-QA path, but the reviewed workload `/usr/bin/cllayerinfo` produced no output. The wave runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. This workload-specific terminal result is preserved as evidence; no guard or profile-policy bypass was used.

### 2026-09-21 — OpenSSL generation compile failure

`dev-libs/openssl-3.6.9999` entered the authenticated Clang-IR generation transaction but failed during its documentation build when `make` reported `Killed` while generating `doc/html/man3/EVP_PKEY_CTX_set_scrypt_N.html`. No install-QA admission, workload receipt, profile merge, dispatcher publication, or profile-use deployment was made. The complete retained Portage log is `/var/tmp/gentoo-portage-build/portage/dev-libs/openssl-3.6.9999/temp/build.log`; this is a generation compile failure requiring package-specific resource/remediation analysis.

### 2026-09-21 — protobuf generation ABI failure

`dev-libs/protobuf-34.2` completed its large authenticated Clang-IR build and staged install, but install-QA rejected the replacement DSOs for exported-ABI loss. `libprotobuf-lite.so.34.2.0` changed from 904 to 918 symbols while losing a packed-varint parser symbol, and `libprotobuf.so.34.2.0` changed from 3771 to 3791 while losing the same symbol family. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete Portage log is `/var/tmp/gentoo-portage-build/portage/dev-libs/protobuf-34.2/temp/build.log`; this remains a correctness failure requiring ABI-safe remediation.

### 2026-09-21 — snowball-stemmer clang-IR generation and profile-use

`dev-libs/snowball-stemmer-3.1.1` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `86239b9390ab6ad944d3b583402ad2c65b105e4e0eeaad4a7caf830104535a12`, and dispatcher publication succeeded with record SHA-256 `df04ea0fdb71965ffe471a29911fbb7ec2ba8389a78d557d1923c35a92ae4147`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — xxhash clang-IR generation and profile-use

`dev-libs/xxhash-0.8.3-r2` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `9b8a53038594e814731db6bdb21976452b08c5cce7887e13c5535baadbd7a143`, and dispatcher publication succeeded with record SHA-256 `0f7876bcceae70c772e04f3085ecca3926e8e47f80b159655895d365d236256f`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — BLT workload terminal failure

`dev-tcltk/blt-2.5.3-r4` completed its authenticated Clang-IR generation transaction and merged through install-QA and the ABI guard, but the reviewed `/usr/bin/bltsh` workload produced no output. The wave runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload result is retained as a terminal exclusion pending a corrected deterministic fixture; no optimization-policy or ABI guard bypass was used.

### 2026-09-21 — bindgen Rust identity mismatch

`dev-util/bindgen-0.72.1` was prepared for the Rust PGO lane, but readiness refused the wave because its generation identity is stale: the identity record references compiler/profile inputs and an ldflags path from `phase3-live-candidate-20260920-postsync-r1`, not the active reviewed generation. The runner fail-closed with `not-authorized-framework-gate`; no Rust receipt or profile was admitted. Fresh identity/materialization regeneration is required before retrying this package.

### 2026-09-21 — bpf-linker Rust identity gate refusal

`dev-util/bpf-linker-0.11.1` readiness also failed closed with `not-authorized-framework-gate` and `ready_count=0`; its Rust identity inputs are not authorized for the active reviewed generation. No wave transaction, receipt, profile, or dispatcher state was created. This confirms the Rust-lane identity regeneration issue is shared by the current derived Rust candidates and is preserved for a single coordinated regeneration rather than bypassed per package.

### 2026-09-21 — breakpad clang-IR generation and profile-use

`dev-util/breakpad-2024.02.16` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `61d51aa183f16c613fd3919a7dc915206290ce1891e31274df9540849af3ea16`, and dispatcher publication succeeded with record SHA-256 `90b8c6dc3321e6419c475598c5c43b5ba8466b5f1c0ca3936639a8101fa826a0`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — ccache clang-IR generation and profile-use

`dev-util/ccache-4.14` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `e527e8f9056f65a3434d7e801bdb626ffc95cc60185f160e0f7cf6d4d080ee8a`, and dispatcher publication succeeded with record SHA-256 `d84b2273e927ad484330396f8ec1c4fe14a9e34a429b04d7cf7ce694335c1448`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — clinfo clang-IR generation and profile-use

`dev-util/clinfo-9999` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `381ed274c78d795715b7ba8c7716ea08240ed703ab5c33a97ed451d3aae2c651`, and dispatcher publication succeeded with record SHA-256 `2a172b905e4057cd6d414137dbc33028aa3bb0e1bf0cb4d82c61f3bb89c4f29c`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — colm generation ABI failure

`dev-util/colm-0.14.7-r4` completed its authenticated Clang-IR build and staged install, but install-QA rejected the replacement `libfsm-0.14.7.so` and `libfsm.so` providers for exported-ABI loss. The staged symbol count rose from 1513 to 1576, but eleven existing C++ exports disappeared. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete Portage log is `/var/tmp/gentoo-portage-build/portage/dev-util/colm-0.14.7-r4/temp/build.log`.

### 2026-09-21 — debugedit clang-IR generation and profile-use

`dev-util/debugedit-5.3` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `78767dadc2621a310e15286215ee457d9c7041e4b4dee58e15ece34a5b133255`, and dispatcher publication succeeded with record SHA-256 `d820414491c2f19b29bf4bc2ab77f05db4f717c7dd2f26763a11ae9622b6dedf`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — desktop-file-utils clang-IR generation and profile-use

`dev-util/desktop-file-utils-0.28-r1` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `3f23508f0add88d0e00aa2e100024f32478c8567971f0f32b465a03ee50eeb91`, and dispatcher publication succeeded with record SHA-256 `a31d9ce1b4ca6ca61072f9199f81ad2c114f103a02f7b219b8442ecca2ecfd13`. The exact profile-use rebuild initially exposed a permissions defect on the merge evidence sidecar; after restoring the required authenticated evidence mode, it authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`. LLVM emitted non-fatal `default.profraw` permission diagnostics during the rebuild; the authoritative profile-use result remained successful.

### 2026-09-21 — ftjam workload terminal failure

`dev-util/ftjam-2.5.3_rc2-r3` completed its authenticated Clang-IR generation build and install-QA path, but the reviewed `/usr/bin/jam --help` workload exited `1`. The wave runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload-specific terminal result is retained in the runner evidence and requires a corrected deterministic fixture before retry.

### 2026-09-21 — gperf clang-IR generation and profile-use

`dev-util/gperf-3.3` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `eea80dd24ccdf9ef11ca83d90c013ee3c33d88af848d52d60dadb84882c9975a`, and dispatcher publication succeeded with record SHA-256 `a795a7033c023c8620bb2dc2d9e7f28bb5c65ba11c1f2a2534203866d1ddcff3`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — gtk-update-icon-cache clang-IR generation and profile-use

`dev-util/gtk-update-icon-cache-3.24.42` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence digest was `89dde8bf842f151145036d366f9ac524fe54b597839bef686dce5759b21090fa`, and dispatcher publication succeeded with record SHA-256 `058ac362796afc99f860c5a34670312afa074b01afe8d6cfb4c61a7e7d9b9d33`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and merged with `use_rc=0`.

### 2026-09-21 — doas clang-IR generation and profile-use reconciliation

`app-admin/doas-6.8.2` had an already-sealed authenticated generation receipt in the active generation. The receipt was independently reconciled against its preserved raw payloads; LLVM merge evidence digest was `5ae9cfd9eec2def3f0820a47a66a82b67c9a15b8c9371a69bdae7182ca19c9e8`, manifest validation passed, and dispatcher publication succeeded with record SHA-256 `8a94c07cfcc7466d8650c43efd17837f06c84ba3859329fc6bbebec45944baed`. Preserved profile-use logs show the exact `clang-ir-use` rebuild reached a completed merge through install-QA and ABI checks; no bypass was used.

### 2026-09-21 — sysklogd clang-IR generation and profile-use

`app-admin/sysklogd-2.7.2` required two preserved generation attempts: the first produced raw payloads but did not seal a receipt, while the distinct retry2 wave completed successfully. Retry2 receipt verification passed, LLVM merge evidence digest was `df4ffae5070e24e43f770128c860dd74192fed59d4c8034da8b99464384c216f`, and dispatcher publication succeeded with record SHA-256 `c2ae1ee468f4b17a0156557fc6e0c4b29dfd3d3a7ca8ed376c03f0b6262a675c`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — patchelf clang-IR generation and profile-use

`dev-util/patchelf-0.19.1` required a preserved retry after its first generation attempt produced raw payloads without sealing a receipt. The distinct retry completed authenticated Clang-IR generation and install-QA; receipt verification passed, LLVM merge evidence digest was `f19f3cc743764921da8d6f558729014c1a8d1bad0d32af6d6aa81e8473612ba1`, and dispatcher publication succeeded with record SHA-256 `5da7a4941a7a2aa3f0d11270ccd891f8d27a3b8fef80b64ab24a2aed4bf28f5b`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — hyprwayland-scanner workload terminal failure

`dev-util/hyprwayland-scanner-9999` completed authenticated Clang-IR generation and install-QA, but its reviewed `/usr/bin/hyprwayland-scanner` workload exited `1`. The runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload-specific terminal result is preserved by the runner and requires a corrected deterministic fixture before retry.

### 2026-09-21 — hipcc clang-IR generation and profile-use

`dev-util/hipcc-7.2.0` completed the authenticated Clang-IR generation wave for both `hipcc` and `hipconfig`; receipt verification passed, LLVM merge evidence digest was `644d69a247e222dc91db1e54fb7c5f38c2cbbfd2afc100e106a2af59c0bc6093`, and dispatcher publication succeeded with record SHA-256 `71c248d778df0a89338bae809b9d908eaa98fe5de198803574a377cd98fdd00a`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — pkgconf generation fetch stall

`dev-util/pkgconf-9999` passed storage preflight and readiness, but its authenticated generation transaction stalled during the live Git fetch for `https://github.com/pkgconf/pkgconf` with the HTTPS helper idle for several minutes and no build progress. The fetch child was terminated to end the abnormal stall cleanly; no generation receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. This is preserved as a fetch-stall terminal attempt pending a deterministic source snapshot or corrected fetch path.

### 2026-09-21 — ragel profile-use ABI failure

`dev-util/ragel-7.0.4-r3` completed authenticated Clang-IR generation, receipt verification, profile merge, manifest validation, and dispatcher publication, but its exact `clang-ir-use` rebuild was rejected by install-QA ABI guard. `libragel.so.0` changed from 258 to 249 exported symbols and lost ten existing C++ exports. No profile-use deployment was admitted; the full retained log is `/tmp/dev-util_ragel-7.0.4-r3-profile-use.log` and the Portage build log records the ABI diff. The generated profile remains preserved for package-specific ABI remediation.

### 2026-09-21 — source-highlight profile-use ABI failure

`dev-util/source-highlight-3.1.9-r2` completed authenticated Clang-IR generation, receipt verification, profile merge (digest `792028863a27a29dfd2471d960aada1cb22ff037cacf4d6791c251a020717950`), manifest validation, and dispatcher publication (record SHA-256 `9aed02c99acb1c25643fe59093ee25bf1f9d33e12a2e8b6e3c1a2ee67cef26f8`). Its exact profile-use rebuild was rejected by install-QA ABI guard: `libsource-highlight.so.4` changed from 1339 to 1300 exports and lost existing Boost/srchilite symbols. No optimized profile-use deployment was admitted; the complete log is `/tmp/dev-util_source-highlight-3.1.9-r2-profile-use.log`.

### 2026-09-21 — unifdef workload terminal failure

`dev-util/unifdef-2.12-r2` completed authenticated Clang-IR generation and install-QA, but the reviewed `/usr/bin/unifdef -h` workload exited `2` because this utility reports usage for `-h` and rejects the generated `--help` style invocation. The runner therefore refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload-specific terminal result is preserved and requires a corrected deterministic fixture before retry.

### 2026-09-21 — wayland-scanner clang-IR generation and profile-use

`dev-util/wayland-scanner-9999` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `84896f04276dd9cb883c74abdb12d9a67cfad1d601717128c99dde2baa7fdc22`, and dispatcher publication succeeded with record SHA-256 `85cfbd790bfb8441f591e0936a928cc7d1f0296156185f43a7752a9acc995940`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — xxd generation runner anomaly

`dev-util/xxd-2025.08.24-r1` passed storage preflight and readiness, and both the original and a distinct retry generation completed Clang-IR build/install transactions with `/usr/bin/xxd -version` available and returning successfully when checked independently. Neither runner invocation sealed a receipt despite exiting zero, so no profile merge, dispatcher publication, or profile-use deployment was admitted. The raw attempts and captured retry output are preserved; this remains an unresolved runner/sealing anomaly rather than a terminal package exclusion.

### 2026-09-21 — gamemode clang-IR generation and profile-use

`games-util/gamemode-9999` completed the authenticated Clang-IR generation wave; receipt verification passed, LLVM merge evidence was published, and dispatcher publication succeeded. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`. The live transaction emitted non-fatal `default.profraw` permission diagnostics and existing `ldconfig` warnings for unrelated ThinLTO YAML files; neither affected the authoritative profile-use result.

### 2026-09-21 — dconf workload terminal failure

`gnome-base/dconf-0.49.0` completed authenticated Clang-IR generation, build/install, and install-QA, but the reviewed `/usr/bin/dconf --help` workload exited `2`. The runner therefore refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload-specific terminal result is retained in the runner output and requires a corrected deterministic fixture before retry.

### 2026-09-21 — zenity clang-IR generation and profile-use

`gnome-extra/zenity-4.2.2` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `256fdbd76c7aee289fb75564ad02c6effb469965b3af2dec6882f8963e98ef26`, and dispatcher publication succeeded with record SHA-256 `0abd655d22bfa72cb9f154a947baf5e15366985a08d5254710e07a5013386c61`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — fuzzel clang-IR generation and profile-use

`gui-apps/fuzzel-1.14.1` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `4f045d15d1c54d5197d343699344abb96b72690419fd1fb37f47b39093c5da53`, and dispatcher publication succeeded with record SHA-256 `7ecb99a7abb83ba7d0904c5da0d90ca8ac612dc156c514592a56dfcfa70c59f3`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — mako generation fetch-stall terminal failure

`gui-apps/mako-9999` passed readiness and storage preflight, but the live Git fetch for `https://github.com/emersion/mako.git` remained idle for over two minutes with no progress. The fetch child was terminated cleanly; Portage then reported the unpack fetch failure. No generation receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/gui-apps/mako-9999/temp/build.log`.

### 2026-09-21 — slurp workload terminal failure

`gui-apps/slurp-1.5.0` completed authenticated Clang-IR generation, staged install, and install-QA, but the reviewed `/usr/bin/slurp` workload exited `1` in the noninteractive wave environment. The runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload-specific terminal result is preserved by the runner and requires a corrected deterministic fixture before retry.

### 2026-09-21 — waybar clang-IR generation and profile-use

`gui-apps/waybar-9999` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `2e2ddf57fc8ce26cd09ebb9af858fe7e11f95bb831a188cbe4ed3dea36b62e6e`, and dispatcher publication succeeded with record SHA-256 `805a5ffb586dadc4249c3dc2bc70b6e38441acda5b664556a09b7fe40a24d509`. The exact profile-use rebuild consumed the published profile, passed install-QA and ABI checks, and completed with `use_rc=0`. LLVM emitted non-fatal unprofiled-file warnings during compilation.

### 2026-09-21 — wl-clipboard clang-IR generation and profile-use

`gui-apps/wl-clipboard-9999` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `dc9a96a97b12b0e041988aacf92ca2856bac67c340f21916a1768ce93bed24de`, and dispatcher publication succeeded with record SHA-256 `22cd633133edabf6e6569061fa63cdf38e5980c681cb7c83729a81ef0a7c259e`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — wlr-randr clang-IR generation and profile-use

`gui-apps/wlr-randr-0.5.0` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `9c5e2ac9c18db794228ab9147702d876c806ee929fb5f742f068c6016fba40e1`, and dispatcher publication succeeded with record SHA-256 `46863ec3c2723fc2cb4eacb1bbae2a2883f123a001804e089324f7c9c983032e`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — hyprcursor generation fetch-stall terminal failure

`gui-libs/hyprcursor-9999` passed readiness and storage preflight, but the live Git fetch for `https://github.com/hyprwm/Hyprcursor.git` remained idle for over two minutes. The fetch child was terminated cleanly; Portage reported the unpack fetch failure. No generation receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/gui-libs/hyprcursor-9999/temp/build.log`.

### 2026-09-21 — hyprwire generation compile failure

`gui-libs/hyprwire-9999` passed readiness, storage, dependency, and configuration gates, but its authenticated Clang-IR generation compile failed while linking `hyprwire-scanner`. `ld.lld` reported unresolved libc++ `std::__1` symbols, indicating the package's scanner link configuration is incompatible with the active libc++/linker tuple under this generation. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/gui-libs/hyprwire-9999/temp/build.log`.

### 2026-09-21 — xdg-desktop-portal-hyprland generation compile failure

`gui-libs/xdg-desktop-portal-hyprland-9999` passed readiness, storage, dependency, and CMake configuration gates, but its authenticated Clang-IR generation compile failed at the final linker stage. `ld.lld` reported unresolved C++ runtime and exception symbols including `__cxa_guard_release`, `__cxa_allocate_exception`, and `std::length_error`, matching the active libc++/linker tuple incompatibility seen in the Hyprwire scanner. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/gui-libs/xdg-desktop-portal-hyprland-9999/temp/build.log`.

### 2026-09-21 — gamescope generation compile failure

`gui-wm/gamescope-3.16.28` passed readiness, storage, dependency, manifest, source, patch, and configuration gates, but its authenticated Clang-IR generation compile failed during linker stages. `ld.lld` reported unresolved libstdc++ and C++ ABI symbols including `std::__throw_logic_error`, `_Hash_bytes`, `__cxa_guard_acquire`, `operator new/delete`, and `__class_type_info`. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/gui-wm/gamescope-3.16.28/temp/build.log`.

### 2026-09-21 — hyprland generation compile failure

`gui-wm/hyprland-9999` passed readiness, storage, dependency, source, patch, and CMake configuration gates, but its authenticated Clang-IR generation compile failed during the `hyprctl` linker stage. `ld.lld` reported unresolved libc++ `std::__1` stream/filesystem symbols and C++ runtime symbols including `__cxa_guard_acquire`, matching the libc++/linker tuple incompatibility already observed in the Hyprwire, xdg-desktop-portal-hyprland, and gamescope attempts. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/gui-wm/hyprland-9999/temp/build.log`.

### 2026-09-21 — libb2 clang-IR generation and profile-use

`app-crypt/libb2-0.98.1-r3` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `6da4de8c2a686bb85545240e29d622c7bcba345c84a9dc092363c4841c8a1721`, and dispatcher publication succeeded with record SHA-256 `0150706b82923a70797009a80aadb70f6c6811f0fb28b91a9f80da726538ad3d`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libmd clang-IR generation and profile-use

`app-crypt/libmd-1.2.0` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `dbd3bb1e299ad90b2a0f27a75e3e2ea13a903e612d0ad7f8ad6707654e96c397`, and dispatcher publication succeeded with record SHA-256 `f1b4c019299d4eac5eac0a626a145cc3dc339358b985c52fc3c93fce87e44b90`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — vscode clang-IR generation and profile-use

`app-editors/vscode-1.137.0` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `b113640473caf9c11f5e0e9d3cad6bc186324f56737f5d2b1593a54ea78a84d1`, and dispatcher publication succeeded with record SHA-256 `cd7f818c096230300a9bc75d0862b7cae77141acd00b87a29c88a5d51fa240bd`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — gspell clang-IR generation and profile-use

`app-text/gspell-1.14.4` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `4f8b0cb8c12f8e2a794ac087f10501f67b97a2548d475769f8fc84e96fc2716f`, and dispatcher publication succeeded with record SHA-256 `cf3c530403b28597fcdc0ba1618cec5598547708552cee514958a824c6a02ba6`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — opensp generation compile failure

`app-text/opensp-1.5.2-r10` passed readiness, storage, dependency, source, patch, and configure gates, but authenticated Clang-IR generation failed while linking `onsgmls`. `ld.lld` rejected unresolved C++ exception/runtime and RTTI symbols from `libosp.so`, including `std::terminate`, `__cxxabiv1` type-info vtables, and `__gxx_personality_v0`, under the active C++ runtime/linker tuple. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/app-text/opensp-1.5.2-r10/temp/build.log`.

### 2026-09-21 — muParser generation compile failure

`dev-cpp/muParser-2.3.5` passed readiness, storage, dependency, source, patch, and CMake configuration gates, but authenticated Clang-IR generation failed during the example linker stage. `ld.lld` reported unresolved libc++ `std::__1` locale and stream symbols, matching the active libc++/linker tuple incompatibility seen in other C++ packages. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-cpp/muParser-2.3.5/temp/build.log`.

### 2026-09-21 — sdbus-c++ clang-IR generation and profile-use

`dev-cpp/sdbus-c++-2.3.1` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `a2c146304dab4c42f82dc11848c3f2642216a388288eaebbc41a8447f4cc6519`, and dispatcher publication succeeded with record SHA-256 `cdd6af58da58ae489af1285724d4cf84760152c5808ff58073652d7455117d7d`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — tomlplusplus generation ABI-guard exclusion

`dev-cpp/tomlplusplus-3.4.0` completed authenticated Clang-IR compilation and staging, but install-QA correctly rejected the replacement DSO for exported ABI loss. The guard reported `libtomlplusplus.so.3` shrinking from 219 to 218 exported symbols, missing `_ZNK4toml2v35table18is_array_of_tablesEv`. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-cpp/tomlplusplus-3.4.0/temp/build.log`.

### 2026-09-21 — strace generation fetch-stall terminal failure

`dev-debug/strace-9999` passed readiness and storage preflight, but its live Git fetch from `https://github.com/strace/strace.git` remained idle for over two minutes. Only the fetch child was terminated; Portage then reported the unpack fetch failure. No generation receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-debug/strace-9999/temp/build.log`.

### 2026-09-21 — duktape clang-IR generation and profile-use

`dev-lang/duktape-2.7.0-r3` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `00caeda521a986118b3a251eb966eee552d1e8d380d9138a1b7a6ae2b897cb64`, and dispatcher publication succeeded with record SHA-256 `a2577574fd80384c4c23f0ef5b25766ff89181e93fd72acaa1055e2334074c44`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — nasm workload terminal failure

`dev-lang/nasm-3.02` completed authenticated Clang-IR generation, staged install, and install-QA, but the reviewed `/usr/bin/ndisasm` workload exited `1`. The runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload-specific terminal result is preserved by the runner output and requires a corrected deterministic fixture before retry.

### 2026-09-21 — tcl workload terminal failure

`dev-lang/tcl-8.6.17` completed authenticated Clang-IR generation, staged installation, and install-QA, but the reviewed `/usr/bin/tclsh8.6` workload produced no output. The runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload-specific terminal result is preserved by the runner output and requires a corrected deterministic fixture before retry.

### 2026-09-21 — ayatana-ido clang-IR generation and profile-use

`dev-libs/ayatana-ido-0.10.4-r1` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `35ae0a1b9f11df89d771409afb455c4e6ac1039e16b3c1310f5369d7632fc42b`, and dispatcher publication succeeded with record SHA-256 `9b42997b56694474b6b464f44af805455cbf601c0f453bad98a078d2bd208234`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — boehm-gc clang-IR generation and profile-use

`dev-libs/boehm-gc-8.2.12` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `77b8c0b70be16dce80d6f4bdc21b88bbe95e79e9e1d85d12e5871464f5c5b30e`, and dispatcher publication succeeded with record SHA-256 `009245966abd4f07e88e942cdc1dc8012f9bc2cb592af3c68741ea69e16a538d`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — date clang-IR generation and profile-use

`dev-libs/date-3.0.3` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `ea6f8f6964862652bebc7130de02e03940916d9ac15da5bdf4a7277c6a163e15`, and dispatcher publication succeeded with record SHA-256 `94f0ec7b1259285ade9ac737483a03cb5a161426b009fa8c001f33cc4aa726b8`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — double-conversion clang-IR generation and profile-use

`dev-libs/double-conversion-3.4.0` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `56ecbf31373794245d51b284defed6d4ebe8616ecc77184cd71d1d29581f64da`, and dispatcher publication succeeded with record SHA-256 `e8ddf9a491f1ecf6c40ed9e10d1d4c5088dd1ce07d44d9317c0b7de95f0d823c`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — ell clang-IR generation and profile-use

`dev-libs/ell-9999` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `de010e2288dcc40b376107bcec742d03af148f675141aa7e81703dfa6c831189`, and dispatcher publication succeeded with record SHA-256 `ab5ee04a8674d4e9a8759e29152454c78985147d452376acae8306e24ad70913`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — gmp generation configure failure

`dev-libs/gmp-6.3.0-r2` passed readiness and storage preflight but failed its authenticated Clang-IR generation configure gate in the 32-bit multilib ABI. The configure probe failed while testing the build compiler/linker (`ld.lld: undefined symbol: main`; `clang-22: linker command failed`), so no source compilation, receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-libs/gmp-6.3.0-r2/temp/build.log`; the authoritative probe details are in the staged `config.log`.

### 2026-09-21 — hidapi clang-IR generation and profile-use

`dev-libs/hidapi-0.15.0` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `91ec17a04b7a733795a846d4233e8a672b05b68cfb385f76431d042521d789d0`, and dispatcher publication succeeded with record SHA-256 `90c4a969717312f2ce0b090f86b1ace84f39adb1033d02b1db7b20d6c7a55dfe`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — hyphen clang-IR generation and profile-use

`dev-libs/hyphen-2.8.8-r2` completed authenticated Clang-IR generation; receipt verification passed, LLVM merge evidence digest was `4a2b94b35f04e9d7111761ef6d006f46d2fc97a0cf76c507e090725a7dbf6517`, and dispatcher publication succeeded with record SHA-256 `b7f437d63104161def2582142de8fbd645e28ca4a994730b4309edc26fe65fb6`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — hyprgraphics generation compile failure

`dev-libs/hyprgraphics-0.5.1-r1` passed readiness, storage, dependency, source, patch, and CMake configuration gates, but authenticated Clang-IR generation failed during the test/example linker stage. `ld.lld` reported unresolved libc++ filesystem, exception, and shared ownership symbols including `std::__1::__fs::filesystem::directory_iterator`, `std::runtime_error`, and `std::__1::__shared_weak_count`, matching the active libc++/linker tuple incompatibility. No receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-libs/hyprgraphics-0.5.1-r1/temp/build.log`.

### 2026-09-21 — gtk+ workload terminal failure

`x11-libs/gtk+-2.24.33-r3` completed authenticated Clang-IR generation, staged installation, and install-QA, but the reviewed workload `/usr/bin/i686-pc-linux-gnu-gtk-query-immodules-2.0` exited `1`. The runner refused to seal an authoritative receipt or publish a profile; no profile-use rebuild was attempted. The workload-specific terminal result is preserved by the runner output and requires a corrected deterministic fixture before retry.

### 2026-09-21 — leancrypto generation fetch-stall terminal failure

`dev-libs/leancrypto-9999` passed readiness and storage preflight, but its live Git fetch from `https://github.com/smuellerDD/leancrypto` remained idle for over two minutes. Only the fetch child was terminated; Portage then reported the unpack fetch failure. No generation receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-libs/leancrypto-9999/temp/build.log`.

### 2026-09-21 — rocm-comgr profile merge terminal failure

`dev-libs/rocm-comgr-7.2.0` completed the authenticated generation transaction and its receipt passed independent verification, but LLVM 22 profile merging failed because the collected raw profile set contained an invalid or unreadable `.profraw` payload. No validated profile, dispatcher publication, or profile-use deployment was admitted. The raw payloads and receipt are retained for forensic diagnosis.

### 2026-09-21 — Phase 3 coverage audit checkpoint

The regenerated Phase 3 coverage audit completed with `coverage_pass=true` for 538 authoritative packages and 16,727 authoritative ELF records. Package lane coverage and ELF classification coverage are complete (`packages_missing_lane=0`, `elf_missing_classification=0`); the audit records 2,510 candidate safety records and preserves the remaining safety-state counts for the next profile/BOLT frontier. The report is retained at `/tmp/derived-20260920/coverage-final.json` with SHA-256 `026ac88f23029adf8f3039bb95bea2410ded909ff9352fdc4d5f5e6e8c22018e`.

Follow-up inspection of the retained `rocm-comgr` raw payload isolated the merge failure to one file, whose LLVM 22 diagnostic is `raw profile version mismatch: ... version = 11; expected version = 10`. The payload remains retained; it is not silently discarded or treated as a valid LLVM 22 profile.

The workload-coverage verifier was then run against the regenerated ebuild-correlated lanes and recipe manifest. It passed with 538 PGO-lane packages, 288 recipe-ready records, and 250 explicit workload exclusions; overlap, missing, and extra sets were all zero. The derived exclusion input and audit are retained under `/tmp/derived-20260920/`; the audit file SHA-256 is `6d42d0eb4c9ef2ba27c625d0250cbabf52624193e7c8e5eb0f5aaf101f27aa56`.

After the evidence commits advanced the repository source identity, the root-owned framework was republished from the reviewed Sway/Mesa generation and activated as `/var/lib/gentoo-optimization/framework-c878d5eb723e7fa1523452d259085d92aeca62ed8e2561521e5bbf25072e0234`. The independent production `--check` then passed, including the root-owned framework-install manifest verification. No package transaction was authorized by this check.

The profile merger now converts `llvm-profdata` rejection into a structured terminal `REFUSED` result while preserving the original tool diagnostic. This keeps incompatible raw-profile versions fail-closed without leaking a traceback; the focused refusal fixture, Python compilation, and ABI-hook regression suite passed.

The portable validation run then completed the recovery suite successfully (`1089s`, within its 2700-second bound) and exposed two package.env policy defects. The OpenSP C++ correction environment was missing a complete reviewed compiler tuple, and the exact maintenance atoms for the current SPIR-V/wlroots/Mesa/Hyprland closure lacked overlap allowlist entries. The environment now declares the full Clang/LLVM tool tuple, and the seven exact-versus-broad overlaps are explicitly rationale-bound. The live duplicate-policy checker now passes with Portage semantic matching (`15 files, 160 lines, 156 atoms, 172 atom/environment pairs`).

The portable suite initially exposed eight stale validator-fixture expectations for the current eight-key manifest ABI: the fixtures still required the removed `cpv` row and used the old ABI row index. Those expectations were corrected without changing the frozen contract files; the focused validator suite now passes all 16 tests.

### 2026-09-21 — rocdbgapi profile merge terminal failure

`dev-libs/rocdbgapi-7.2.0` completed the authenticated generation transaction and its receipt passed independent verification, but LLVM 22 profile merging failed because the collected raw profile set contained an invalid/unreadable profraw payload. No validated profile, dispatcher publication, or profile-use deployment was admitted. The raw payloads and receipt are retained for forensic diagnosis.

### 2026-09-21 — popt storage recovery and profile deployment

The initial `dev-libs/popt-1.19-r1` attempt was refused at the 12% storage floor. A stale superseded raw-profile generation (`phase3-live-candidate-20260918-postsync-r1`, approximately 82 GiB) and smaller superseded generation trees were removed; the active 20260920 generation, validated profiles, receipts, manifests, rollback artifacts, and current raw payloads were preserved. Free space recovered to approximately 21.17%, after which the exact popt wave completed. Receipt verification passed, LLVM 22 merged the raw payload with evidence digest `7822db0b52db52a86801f5282a2354203def490e966ccb6c0fb2f03815b23480`, and dispatcher publication succeeded with record SHA-256 `862555a9697c967f41ad6d27f7036b69ae38a74ff47907f038cd8301ba5bc0a8`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — popt wave deferred by storage preflight

The exact `dev-libs/popt-1.19-r1` wave passed readiness, but its authenticated runner refused to begin because root filesystem free space had fallen to `11.947%`, below the configured `12.0%` operational floor. No package mutation or terminal exclusion was admitted. Transient build space was measured at `31M`; the large retained stores are authenticated PGO raw payloads and rollback binpackages, which were preserved.

### 2026-09-21 — openssl-compat generation install-QA terminal failure

`dev-libs/openssl-compat-1.1.1u` passed readiness, storage, dependency resolution, source, configure, and compilation gates, but install-QA failed in the active BOLT hook during staged installation. The retained Portage log records the authenticated `clang-ir-generate` context and the hook-abort failure; no receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-libs/openssl-compat-1.1.1u/temp/build.log`.

### 2026-09-21 — nspr clang-IR generation and profile-use

`dev-libs/nspr-4.40` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `b90c020a217622f7743a03aa4136626cfaccff833a86a77d71743256a61fbdf7`, and dispatcher publication succeeded with record SHA-256 `893574d0aa0b5955d6055353fcece9fc53d981d0e6753825cc2ce7f572f2efa9`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — mpdecimal clang-IR generation and profile-use

`dev-libs/mpdecimal-4.0.1` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `8d7f9c21c454a9fce28c002478afa48f6ea635c0c9478dbee8636d009edc0d14`, and dispatcher publication succeeded with record SHA-256 `8be3792e4319e00576704fd0095eea976175e691f994db9da6f8799a4852119f`. The first profile-use attempt exposed unreadable root-owned merge evidence; after correcting artifact permissions, the exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libverto clang-IR generation and profile-use

`dev-libs/libverto-0.3.2-r1` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `454b8c70354641d38b2f7d232896ce609dfae81e3b210f7555b033a9ed171760`, and dispatcher publication succeeded with record SHA-256 `03b8e286a3d96d3cf55ae60d5ce2a34b2777cde5dd05f11d1df5e33619cd6995`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libutf8proc clang-IR generation and profile-use

`dev-libs/libutf8proc-2.11.3` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `7e12a4ce684788646274b8812e13f7de7fd7f0b35ef6fdbefb3102bafa8a6a26`, and dispatcher publication succeeded with record SHA-256 `8ab74bb38933064644449de7d41daad3ea7f93b1e6b23f8f38217c562ca70a2d`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libusb clang-IR generation and profile-use

`dev-libs/libusb-1.0.30` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `97af9a668b3ffcefa5d68452031f11512fd9f8a5dc15f98dc5ec51d5cf8c153a`, and dispatcher publication succeeded with record SHA-256 `0aa1e106856ee99ff581aba411af844f60ef9a301ae517aa42919875f9d37838`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libunistring clang-IR generation and profile-use

`dev-libs/libunistring-1.4.2` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `c3f1d1a0a0e28c0bca059718d1736695de3f584d5e7d6f1bacc961e664db52ce`, and dispatcher publication succeeded with record SHA-256 `080fecc0055acf887d4e1a8747e2d6243e16e79eae165b6133203a6e02dfe4c1`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libtraceevent clang-IR generation and profile-use

`dev-libs/libtraceevent-1.9.0` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `001277c378be5d3584b19217ecd94dc8f81e277bf00a32d7f5b7062e81cc9a44`, and dispatcher publication succeeded with record SHA-256 `8661b42e66ec73800d9d18c36cf997405145a670ab6c9daa967fefa39754489f`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libtomcrypt clang-IR generation and profile-use

`dev-libs/libtomcrypt-1.18.2-r4` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `7408ecdd461840214555ae4cc178c30a49ed967e7879d20fb9484d740a53f4ff`, and dispatcher publication succeeded with record SHA-256 `e6e56f61e7e27d2ff82276e0bb7efd5286da3c41ce78c47dd2732d47648aa32b`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libsigc++-3 clang-IR generation and profile-use

`dev-libs/libsigc++-3.8.0` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `f729d357c63e8fc0746246056f018d76822384548a538280ae7ee683ae09d442`, and dispatcher publication succeeded with record SHA-256 `2a32365015fca570d7976a8b4e92cc8bc21ac2be04e78a38b80e71f78e940227`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libsigc++ clang-IR generation and profile-use

`dev-libs/libsigc++-2.12.1` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `922b9987d18bbe59e956464bd074e1c70892bbb9d80843558ea1da6811810f02`, and dispatcher publication succeeded with record SHA-256 `b6fbfde06c8e76434aba7626a115aaf382a9538fc20fb2b14ca66724cfb14dce`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libsass generation fetch-stall terminal failure

`dev-libs/libsass-9999` passed readiness and storage preflight, but its live Git fetch from `https://github.com/sass/libsass.git` remained idle for over two minutes. Only the fetch child was terminated; Portage then reported the unpack fetch failure. No generation receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-libs/libsass-9999/temp/build.log`.

### 2026-09-21 — libpfm clang-IR generation and profile-use

`dev-libs/libpfm-9999` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `7f29c41466b9c2090016557eb660a610c3edcc57a48645b9a4f3d32ad9e44d72`, and dispatcher publication succeeded with record SHA-256 `ccc1b284d918481f415c92d2a28a2c41a4d3a2afc72acaea39c79cfacf3a80e2`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libltdl exact-CPV terminal exclusion

`dev-libs/libltdl-2.6.0` passed readiness and storage preflight, but the authenticated runner refused to start the transaction because the exact CPV is no longer buildable from the live Portage tree: `emerge` reported that no ebuild satisfies `=dev-libs/libltdl-2.6.0`. No generation receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. This exact CPV/buildability failure is retained as the terminal exclusion evidence.

### 2026-09-21 — libksba clang-IR generation and profile-use

`dev-libs/libksba-1.8.1` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `df1d4684be2aa9ad266f98784aaaebbb340bee8b5c29694ed27ec033c6707043`, and dispatcher publication succeeded with record SHA-256 `5e9be8eab9fd6006c72eee4c57d1ca65e9a94ca99f3de505584acae45c720697`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libev clang-IR generation and profile-use

`dev-libs/libev-4.33` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `d4100bfd6fff2fbb45c14ba1ae1b34b8b41c9239f2c226a7e7831bb61cd89fb7`, and dispatcher publication succeeded with record SHA-256 `c736b3b3d7feceaa54c395e438e18293c77a4679883e07c48878c1be8a82fb69`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libedit clang-IR generation and profile-use

`dev-libs/libedit-20240808.3.1` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `1746e41c960fcd0c1d09a9dd98d3838cb217777624ff1c686fc4894d4577fa36`, and dispatcher publication succeeded with record SHA-256 `2de2ab346118642612dd0c9bb266993410959e17311caf41fc40caed0a76cd5a`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libdbusmenu clang-IR generation and profile-use

`dev-libs/libdbusmenu-16.04.0-r4` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `d78ddf6c2f9fc38659cc2c7cef1ea532e97a9ad43399d656a42edf4cb5fdd831`, and dispatcher publication succeeded with record SHA-256 `e2008c0ce4dceb749b8609ef31bb22a6a0ddd21bd4458f90e072b88d93de363e`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libbsd clang-IR generation and profile-use

`dev-libs/libbsd-0.12.1` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `8266d4fbdebc5d583c90d5f7f69616d04fd21f5350781f2bd2a066221e16ad8c`, and dispatcher publication succeeded with record SHA-256 `b65e819128a92b8d60d8f40daa78b48327e8109bf7ab32b6bc2eef5c9150e2d7`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libayatana-indicator clang-IR generation and profile-use

`dev-libs/libayatana-indicator-0.9.4` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `11bf4439eca79c45b15146dbbd8fe40458ea93da3d9a8071c4cd83437db8b4f3`, and dispatcher publication succeeded with record SHA-256 `8a7ec83cf6489b3661730d6bf6b59d79cef2a2ed1abe4f620390c18aba515eb8`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libayatana-appindicator clang-IR generation and profile-use

`dev-libs/libayatana-appindicator-0.5.94` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `2aab9971ec5a4c8b0ae9582ea003a2b5bcfca343a4c602f10687c9aa147d688f`, and dispatcher publication succeeded with record SHA-256 `8afecc5e55bae4a2a0143e135028913c5dd4b8b6021ad0caa0ab0d4d5ea2928c`. The exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — libaio clang-IR generation and profile-use

`dev-libs/libaio-9999` completed authenticated Clang-IR generation; receipt verification passed, LLVM 22 merged the raw payload with evidence digest `d1819b3f17e0fc9b3bed5d747a78654d3a554817abbc6f302cf7ff3301cca1b9`, and dispatcher publication succeeded with record SHA-256 `0224e5ef36c511931cc2be5090706426007ebe5dd669dab29a17db4c0ecf7821`. The first profile-use attempt exposed unreadable root-owned merge evidence; after correcting artifact read permissions, the exact profile-use rebuild authenticated `clang-ir-use`, passed install-QA and ABI checks, and completed with `use_rc=0`.

### 2026-09-21 — hyprlang generation fetch-stall terminal failure

`dev-libs/hyprlang-9999` passed readiness and storage preflight, but its live Git fetch from `https://github.com/hyprwm/Hyprlang.git` remained idle for over two minutes. Only the fetch child was terminated; Portage then reported the unpack fetch failure. No generation receipt, profile merge, dispatcher publication, or profile-use deployment was admitted. The complete retained log is `/var/tmp/gentoo-portage-build/portage/dev-libs/hyprlang-9999/temp/build.log`.
