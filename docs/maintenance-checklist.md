# Gentoo-stuff Maintenance Checklist

## After LLVM/Clang Update

- [ ] Run `emerge --info` and verify `CC`, `CXX`, and `LLVM_SLOT`.
- [ ] Build a small C package.
- [ ] Build a small C++ package.
- [ ] Build one package using Polly.
- [ ] Build one package using ThinLTO.
- [ ] Build one package using libc++.
- [ ] Check existing no-polly demotions; retry one or two if likely fixed.
- [ ] Check existing no-lto demotions; retry one or two if likely fixed.

## After ROCm Update

- [ ] Confirm `AMDGPU_TARGETS`.
- [ ] Confirm `rocminfo` still reports expected gfx target.
- [ ] Rebuild `rocm-device-libs`.
- [ ] Rebuild one HIP package.
- [ ] Rebuild one OpenCL package.
- [ ] Check whether `rocm-extra-econf.conf` is still needed.

## After Kernel Update

- [ ] Confirm GPU firmware loads.
- [ ] Confirm amdgpu initializes cleanly.
- [ ] Confirm ZFS module status.
- [ ] Confirm Gamescope starts.
- [ ] Confirm Steam launches.
- [ ] Confirm MangoHud/Mangoapp still work.

## After Python Target Update

- [ ] Check global `PYTHON_TARGETS`.
- [ ] Check `python-single-target` demotions.
- [ ] Retry one demoted package if dependency closure improved.

## After Large World Update

- [ ] Save emerge log.
- [ ] Record packages newly demoted.
- [ ] Record packages promoted back to aggressive default.
- [ ] Check `/var/cache/binpkgs`.
- [ ] Clean stale binpkgs only after the system is known-good.

## After Fallback Env Profile Changes

- [ ] Verify no-forced-libs profiles do not use active `FORCED_LIBS`.
- [ ] Verify libc++ fallback profiles include `${LIB_FLAGS}` in `CXXFLAGS`
      when they use `${RUNTIME_LINK_FLAGS}` in `LDFLAGS`.
- [ ] Check `grep -R '^FEATURES=' -n portage/env` and confirm every entry
      preserves inherited global features.
- [ ] Check `find portage -name '._cfg*' -print` returns no config-protect
      residue files.
- [ ] Check `grep -R '^sys-apps/hwloc' -n portage/package.env` returns one
      ROCm-aware entry only.

## After Global Performance Expansion Changes

- [ ] Confirm new performance flags are global in `make.conf`.
- [ ] Confirm every new global axis has a demotion env file.
- [ ] Confirm no demotion env file overwrites `FEATURES` from scratch.
- [ ] Run `bash -n` on every `portage/env/*.conf`.
- [ ] Run `emerge --info` and verify CFLAGS/CXXFLAGS/LDFLAGS/RUSTFLAGS contain the expected global expansion flags.
- [ ] Run pretend merges for the first-wave test set:
      - `app-arch/zstd`
      - `app-arch/xz-utils`
      - `sys-apps/coreutils`
      - `dev-libs/libffi`
      - `dev-libs/openssl`
      - `gui-wm/gamescope`
- [ ] Build the small first-wave test set:
      - `app-arch/zstd`
      - `app-arch/xz-utils`
      - `sys-apps/coreutils`
- [ ] If a package fails, create the narrowest package.env demotion.
- [ ] Record the exact failure reason in the package.env comment.
- [ ] Do not remove global flags from make.conf unless the entire system cannot bootstrap.

## After PGO Profile Changes

- [ ] Confirm only generated exact package assignments select an explicit backend mode.
- [ ] Confirm an active use mode fails closed without its exact validated manifest and payload.
- [ ] Confirm Clang IR, Clang sample, GCC, Rust, and Go profiles remain in separate identity paths.
- [ ] Confirm build logs show the intended backend/profile path and no compiler-family leakage.
- [ ] Invalidate and retrain stale fingerprints; never suppress or silently fall back from a mismatch.

## After BOLT Sweeps

- [ ] Confirm binaries were built with `--emit-relocs`.
- [ ] Confirm binaries were built with `--build-id`.
- [ ] Confirm the exact unstripped input was captured from `${ED}` before Portage stripping.
- [ ] Confirm every registered output matches the captured full hash, build ID, and `.text` hash.
- [ ] Confirm deployment replaces files only inside `${ED}` and retains `.note.bolt_info` after Portage processing.
- [ ] Keep original binpkg rollback available.
