# Agent safety boundary for this system

These instructions apply to every automated or LLM-directed action in this
repository and on the Gentoo host that uses it. They override any older project
text, script, test, runbook, or recorded recovery procedure that conflicts with
them.

## Never touch the boot chain

- Never create, edit, delete, rename, enable, disable, reorder, select, or arm
  a firmware, EFI, BIOS, bootloader, or recovery boot entry.
- Never set or clear `BootNext`, change `BootOrder`, write EFI variables, or
  invoke `efibootmgr`, `bootctl`, or another boot-management tool on this
  system. Do not write to `/sys/firmware/efi/efivars`.
- Never modify `/efi`, `/boot`, bootloader configuration, kernel command lines,
  EFI executables, kernel images, or initramfs images.
- Do not add a project test, preflight, recovery gate, acceptance condition, or
  evidence requirement involving boot-entry state. Existing boot configuration
  is outside this project's authority and must remain unchanged.
- Do not ask a human operator to create or alter a recovery entry on behalf of
  this project. Boot-entry work is not a project prerequisite or fallback.

Custom boot-entry names have previously left this machine unable to POST. There
is no exception for a supposedly temporary, inactive, one-shot, rescue,
known-good, or `BootOrder`-neutral entry.

## Kernel lifecycle is human-only and outside project scope

- Never configure, build, install, replace, remove, or deploy a kernel.
- Never generate, install, replace, or modify an initramfs or kernel boot asset.
- Do not run a project package transaction that would perform one of those
  actions. Stop before mutation and exclude the affected kernel lifecycle item
  from the automated project with the machine-valid terminal reason
  `kernel-policy-exclusion`, which denotes this human-only system boundary.
- Do not ask a human to perform kernel work merely to satisfy this project's
  roadmap. Kernel and boot-chain optimization are not project goals.
- Read-only observation of the currently running kernel may be used only when
  needed to identify the userspace execution environment; it must never lead to
  kernel or boot-chain mutation.

The optimization project is a userspace project. Reboots used for userspace
runtime validation must use the already configured boot path unchanged and do
not authorize any boot or kernel action.

Outside this explicit boot/kernel/EFI/initramfs human-only boundary, project
references to an operator, reviewer, supervised operation, approval,
attestation, or independent review mean autonomous producer/verifier separation
backed by immutable evidence; they do not create a human-intervention
requirement.
