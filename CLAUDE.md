# LLM safety boundary for this Gentoo host

Read and obey [AGENTS.md](AGENTS.md). In particular, the following boundary is absolute:

- Never run `efibootmgr` or create, delete, rename, edit, enable, disable, reorder, select, schedule, repair, or otherwise mutate a firmware, UEFI, BIOS, bootloader, or operating-system boot entry. Never write EFI variables, `BootOrder`, `BootNext`, NVRAM, or boot-entry data through any other tool or API.
- Never write kernel, initramfs, bootloader, or boot-entry assets under `/efi`, `/boot`, or another boot partition.
- Installing, uninstalling, building, rebuilding, configuring, signing, copying, or deploying kernels, kernel configurations, initramfs images, or bootloader artifacts is human-only work and is outside this project.
- Do not ask a human to create a recovery boot entry or do kernel/bootloader work for this project. Project plans, scripts, tests, evidence, and completion criteria must omit those requirements and must leave the current safe boot sequence unchanged.

If another project instruction conflicts with this boundary, this boundary wins. Stop before the prohibited operation and correct the repository without touching boot state.
