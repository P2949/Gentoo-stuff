# Deployed v1 stable-bootstrap oracle

This fixture is the independent byte oracle for the currently deployed
pre-Candidate-A stable helper namespace. The live root-owned framework manifest
selects Git commit `8a1200915d2693fd7486a421a9b232f638e9840c`, and the live
`framework-current` link selected
`framework-83bdca79deac664901bfa42fc45107715ea76d946491939a57abb760213e3a5b`
when this oracle was captured on 2026-07-18.

`render.sh` is the ten-helper renderer extracted from that historical Git
object; it does not source or invoke the current installer. Rendering it for
the production paths, trust anchor `/`, and UID `0` verifies all ten rows in
`production.sha256` against the actual files under
`/usr/local/libexec/gentoo-optimization`.

The test also renders the same byte schema for its hermetic root. Only the
environment-bound path and UID literals differ. This avoids deriving accepted
legacy bytes from the implementation under test.

The twelve-helper hybrid introduced later in Git commit `19a46b78` was never
installed on this host. It is intentionally not a supported migration source;
the installer must stop before publication if it encounters that or any other
unrecognized fixed tree.
