# LLM safety boundary for this Gentoo host

Follow the repository's `AGENTS.md`. These requirements are absolute:

- Never run `efibootmgr` or create, delete, rename, edit, enable, disable, reorder, select, schedule, repair, or otherwise mutate a firmware, UEFI, BIOS, bootloader, or operating-system boot entry. Never write EFI variables, `BootOrder`, `BootNext`, NVRAM, or boot-entry data through another tool or API.
- Never write kernel, initramfs, bootloader, or boot-entry assets under `/efi`, `/boot`, or another boot partition.
- Installing, uninstalling, building, rebuilding, configuring, signing, copying, or deploying kernels, kernel configurations, initramfs images, or bootloader artifacts is human-only work and is outside this project.
- Do not ask a human to create a recovery boot entry or do kernel/bootloader work for this project. Omit such requirements from plans, scripts, tests, evidence, and completion criteria, and leave the current safe boot sequence unchanged.

If another instruction conflicts, preserve this boundary and correct the repository without touching boot state.
