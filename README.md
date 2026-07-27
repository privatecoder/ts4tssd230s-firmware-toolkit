# ts4tssd230s-firmware-toolkit

Update, un-brick (ROM-mode recovery), and restore serial/WWN on Transcend 4 TB
SSD230S SATA SSDs (**TS4TSSD230S**; shows as `SM2259AC-70-0010M101` in ROM mode)
— includes a full disassembly of the Silicon Motion vendor flash tool.

> 🧨 **Flashing wipes ALL data on the drive — every mode, no exceptions.** The
> controller rebuilds its flash-translation layer, so the drive comes back blank.
> `keepsn_1` preserves *identity* (serial/model/WWN), **not** contents. Back up
> first, and expect to reformat/rebuild. This touches drive firmware — used
> carelessly it can brick a drive. Read
> [`TS-SSD230S-Firmware-Update.md`](TS-SSD230S-Firmware-Update.md) fully first.

> ⚠️ **No proprietary files are included here.** The Silicon Motion / Transcend
> flash tool and firmware images are **not** redistributed — `setup.sh` downloads
> the official vendor ISO and extracts them locally (and rebuilds the 4-byte
> recovery patch, hash-verified). Vendor ISO archive:
> <https://github.com/leopard-archives/Transcend-SATA-SSD-230S-4TB/releases/tag/22Z4X4IA>

## Scope

Specific to the **4 TB TS4TSSD230S** (Silicon Motion **SM2259** — boot ROM
reports `SM2259AC-70-0010M101`, production firmware reports SM2259AB; WD/SanDisk
BiCS5 112L NAND; firmware `22Z4W14B` → `22Z4X4IA`). The scripts hash-verify
against *this exact* ISO, flasher build, and firmware image — they are **not**
generic to other SM2259 drives or other SSD230S capacities (e.g. the 1 TB
`TS1TSSD230S`, which uses a different controller/firmware). The reverse-
engineering appendix, however, is broadly applicable to SM2259.

## Quick start

Requires a **Linux** host with the target SSD on a native SATA port (not the boot
disk, no USB adapters), run as root. You can *prepare* the folder on macOS/Linux
and copy it over.

```sh
# 1. Build the self-contained tool folder (downloads ISO, extracts, builds the
#    hash-verified recovery flasher). Needs: 7z (or Linux+root), python3, curl/wget.
./setup.sh                       # → ./ts-ssd230s-fw-update/

# 2. (Only if the drive is on another machine) copy the whole folder there:
#    rsync -a ts-ssd230s-fw-update/ root@FLASHHOST:/root/ts-ssd230s-fw-update/

# 3. On the Linux host with the drive:
cd ts-ssd230s-fw-update
#    NOTE: on AMD/ASMedia SATA, disable NCQ first: echo 1 > /sys/block/sdX/device/queue_depth

./SM2258TLC_3D_LinuxTool_64 /dev/sdX keepsn_1            # normal firmware update
./SM2258TLC_3D_LinuxTool_64.patched /dev/sdX initial     # un-brick a ROM-mode drive
./patch_identity.py && ./SM2258TLC_3D_LinuxTool_64 /dev/sdX initial   # restore serial+WWN
```

Full walkthrough, diagnostics, redundancy/array strategy, troubleshooting, and
the vendor-tool disassembly: **[`TS-SSD230S-Firmware-Update.md`](TS-SSD230S-Firmware-Update.md)**.

## What's in this repo

```
setup.sh                     # bootstrap: download ISO → extract → build → assemble ts-ssd230s-fw-update/
TS-SSD230S-Firmware-Update.md # the complete guide + disassembly reference
recovery/
├── patch_flasher.py         # rebuild + hash-verify the 4-byte Patch-A recovery flasher
├── patch_identity.py        # restore serial/WWN into ISP2259.bin (identity-masked hash check)
└── RECOVERY_SUMMARY.md       # detailed case log of the un-brick + identity restore
```

The vendor ISO, the extracted flasher/firmware, and the assembled
`ts-ssd230s-fw-update/` folder are **git-ignored** (proprietary / built locally).

## How it works (short version)

The vendor tool assumes production firmware is running: its first step
(`CheckFlashID`) reads the live NAND map and fails in ROM mode, so a bricked
drive can't be recovered normally. A **4-byte patch** neutralizes that gate, and
`initial` (mode 0) — the only mode needing no keep-data — drives the ROM →
Shim → MPISP → Pretest → Program-ISP chain that *is* ROM-capable. Serial/WWN are
then written directly into the ISP image at known offsets. See the disassembly
appendix in the guide for the full state machine, ATA vendor protocol, and offsets.

## Disclaimer

Provided as-is, no warranty (see [LICENSE](LICENSE)). Firmware flashing is
inherently risky and destroys data. You are responsible for verifying the target
drive and for any outcome. Not affiliated with Transcend or Silicon Motion.

## License

[MIT](LICENSE) — covers the documentation and scripts in this repo only, not the
vendor's proprietary tool/firmware (which are not included).
