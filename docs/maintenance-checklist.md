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
