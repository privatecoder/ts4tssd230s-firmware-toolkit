# TS4TSSD230S ROM-Mode Recovery — Patch & Result Summary

**Drive:** Transcend TS4TSSD230S, 4 TB, Silicon Motion SM2259 controller (boot ROM: `SM2259AC-70-0010M101`; production firmware reports SM2259AB), WD/SanDisk BiCS5 112L NAND
**Original firmware:** `22Z4W14B` → target `22Z4X4IA`
**Failure state before recovery:** controller stuck in ROM loader — enumerated as
`SM2259AC-70-0010M101`, firmware `20200324`, `ISPMode:0`
**Tool:** `SM2258TLC_3D_LinuxTool_64` (Linux Tool V1.1.6 / Library 239) — from the vendor ISO:
<https://github.com/leopard-archives/Transcend-SATA-SSD-230S-4TB/releases/tag/22Z4X4IA>
**Patched flasher:** rebuild + hash-verify with `patch_flasher.py` (not redistributed).
**Date recovered:** 2026-07-25

> 🧨 **Any flash of this tool wipes all user data — every mode, including
> `keepsn_1` and `initial` (mode 0).** The controller rebuilds its flash-
> translation layer, so the drive comes back blank. `keepsn_1` preserves the
> drive's *identity* (SN/model/WWN), not its *contents*. In this case the drive
> was already unreadable in ROM mode, so no recoverable data was lost — but on a
> working drive, back up first.

---

## 1. Root cause (from disassembly)

The updater's master sequence is `InitCard(mode)`:

```
CheckFlashID → [keep-data: ModifyISPFile/DumpISP] → ForceToROM
   → DL_SHIM_MPISP → DLMPISP → DoPretest → CheckPretestFlash
   → CheckCardMode → WriteISPFirmware
```

The controller run-mode byte is `g_FlashIDTable[+4]`:

| value | meaning |
|------:|---------|
| 0 | ROM loader / blank |
| 1 | MPISP running |
| 2 | Production firmware running |
| 3 | Shim-MPISP running |

Two distinct problems were identified:

1. **Original brick** — the recovery USB ran `keepsn_1` (mode 3) via `linux-fw-update-cli`
   (`FWUpdateInfo.ini: Command=keepsn_1`). That path must read the on-NAND ISP/system
   blocks to preserve per-drive keep-data. It hit a system-block translation error
   (`Tran Sys block fail`), aborted at the pretest/MPISP stage, and left the controller
   in ROM mode.

2. **Cannot re-run any normal flow** — once in ROM mode, `CheckFlashID()` (step 1) takes
   the "CE/CH map" branch (because mode byte `!= 2`) and the live flash-ID table isn't
   populated by the ROM loader, so the compare against `FLASHID.bin` fails:
   `flash error, Please Check you flash`. The tool aborts before reaching the download
   engine — even though `ForceToROM → Shim → MPISP` is fully ROM-capable by design.

**Key insight:** the SM2259 ROM-authenticated functions (`ReadFlashID_ROM`,
`WriteMPISPData_ROM`, `JumpCode_AuthRSA`, `DownloadISPToRAMTSB3D`, `ActiveISPTSB3D`,
`TriggerISPSelfUpdate`) exist in the binary but have **zero call sites** — this tool never
uses them. Recovery therefore requires (a) bypassing the ROM-mode flash-ID gate and
(b) using `mode 0` (`initial`, no keep-data), the only path that doesn't need the old
firmware alive.

---

## 2. Suggested patches (all considered)

`file_offset = VA − 0x400000` for all `.text` sites.

| # | Patch | VA / file off | Original → Patched | Effect | Risk |
|---|-------|---------------|--------------------|--------|------|
| **A** | Make `CheckFlashID` always return success | `0x404d8c` / `0x4d8c` | `55 48 89 e5` → `31 c0 c3 90` (`xor eax,eax; ret; nop`) | Neutralizes the flash-ID gate at **both** call sites | **Low** — pure gate; writes nothing to drive; correct FLASHID.bin already present |
| **A-alt** | Force `InitCard` to ignore the result | `0x40411d` / `0x411d` | `74 0a` (`je`) → `eb 0a` (`jmp`) | Same effect, InitCard call site only | Low |
| **B** | Run `mode 0` instead of `keepsn_1` (no code patch) | — | menu keyword `initial` | Skips keep-data path (needs production FW); flashes generic `ISP2259.bin` | Medium — generic serial/WWN (cosmetic) |
| **C** | Skip MPISP-ready check after pretest | `0x405ce7` / `0x5ce7` | `74 1b` (`je`) → `eb 1b` (`jmp`) | Accept any mode byte in `CheckPretestFlash` | **HIGH** — masks the real failure; can write into a dead controller and deepen the brick. **Not used.** |
| **D** | Skip first CE/CH memcmp abort inside `CheckFlashID` | `0x4054cb` / `0x54cb` | `74 1b` → `eb 1b` | Partial flash-ID bypass (per-die loop still active) | Low–Medium — inferior to A |
| **E** | Increase ATA timeout (120 s → larger) | read `0x40f6d4`/`0xf6d4`, write `0x40f4ec`/`0xf4ec` | `c0 d4 01 00` (120000) → e.g. `80 a9 03 00` (240000) | More time for slow pretest/flash ops | Low — not the issue here |

---

## 3. Implemented patch

**Patch A + Mode B** were used together:

- **Patch A** applied to `CheckFlashID` (VA `0x404d8c`, file offset `0x4d8c = 19852`):

  ```
  original:  55 48 89 e5      push rbp; mov rbp,rsp   (+ push rbx; sub rsp,0x8b8 ...)
  patched:   31 c0 c3 90      xor eax,eax; ret; nop
  ```

  Only **4 bytes** changed; the rest of the binary is byte-identical. The early `ret`
  is stack-balanced (no frame was set up before it), so it cleanly returns `0` = "pass"
  to every caller.

- Patched binary: `SM2258TLC_3D_LinuxTool_64.patched`

- **Reproduce the patch in place** (Linux, no Python needed):
  ```sh
  cp SM2258TLC_3D_LinuxTool_64 SM2258TLC_3D_LinuxTool_64.orig
  od -An -tx1 -j 19852 -N 4 SM2258TLC_3D_LinuxTool_64.orig   # must print: 55 48 89 e5
  printf '\x31\xc0\xc3\x90' | dd of=SM2258TLC_3D_LinuxTool_64 bs=1 seek=19852 count=4 conv=notrunc
  od -An -tx1 -j 19852 -N 4 SM2258TLC_3D_LinuxTool_64        # must print: 31 c0 c3 90
  ```

- **Command run** (mode 0 = `initial`, no serial argument, so no keep-data path):
  ```sh
  cd /root/Startup/22Z4W14B/TS4TSSD230S/initial
  ./SM2258TLC_3D_LinuxTool_64.patched /dev/sdb initial
  ```

**Patch C was deliberately NOT applied** — the pretest turned out to pass on its own, so
masking it was unnecessary and would have been dangerous.

---

## 4. Result — SUCCESS

Recovery run output (abridged):

```
ISPMode:0
Force ROM Mode ........................ Done
Download ShimMPISP ................... Success
Download MPISP ...................... Success
Do pretest .......................... Jump to Pretest! Success
Check Pretest Flash ................. Success
Tran Sys block fail                 (non-fatal warning)
ISPMode:1
Download ISP ........................ Success
Reset CPU ........................... Done
ISPMode:2                            ← production firmware running
Model Name : TS4TSSD230S
Firmware Ver : 22Z4X4IA
Total Process finished, the result is Pass
```

Post-recovery SMART:

| Field | Value |
|-------|-------|
| Model | `TS4TSSD230S` |
| Firmware | `22Z4X4IA` |
| Capacity | **4,096,805,658,624 bytes (4.09 TB)** — full NAND mapped |
| SMART overall health | **PASSED** |
| DRAM_1_Bit_Error_Count (159) | 0 — DRAM proven healthy |
| Uncorrectable_Error_Cnt (160) | 0 |
| Program/Erase Fail (181/182) | 0 / 0 |
| UDMA_CRC_Error_Count (199) | 0 |
| Remaining_Lifetime (169) | 100% |
| Initial_Bad_Block_Count (163) | 54 (normal factory count) |

**Conclusion:** hardware was never faulty — the brick was purely the `keepsn_1` keep-data
path choking on a system block. Bypassing the ROM-mode flash-ID gate and running the
keep-data-free `mode 0` install restored the drive to a healthy 4 TB SSD on firmware
`22Z4X4IA`.

### Known cosmetic side effects of mode 0
- Serial Number reset to generic `0028522400000080001E` (sticker value `SERIAL0002` not applied)
- LU WWN Device Id = all zeros

Restoring the sticker serial would require `mode 1` (`initial <SN>`), which re-enters the
`keepsn`-style keep-data + reflash path. Recommendation: **leave as-is** — a cosmetic
serial is not worth re-entering the flow that originally bricked the drive.

---

## 5. Recommended follow-up

1. Full power-cycle, re-enumerate.
2. `smartctl -t short /dev/sdb` then `smartctl -l selftest /dev/sdb`.
3. Optional full-surface write/verify (`badblocks -wsv` or `f3write`/`f3read`) before
   trusting with data — the drive is blank (FTL was rebuilt).
4. Unraid identifies disks by serial; this drive now presents a new ID and will be added
   as a fresh disk.

---

## 6. Identity restore — SUCCESS (done after §4)

The generic serial/WWN from §4 were **not** left as-is. Because the drive's real
serial **and WWN are printed on its own sticker**, restoration was genuine (not a clone):

- Sticker serial: `SERIAL0002`
- Sticker WWN:    `57C3548000000002`

Reference (healthy) drive for cross-checking format/OUI: serial `SERIAL0003`,
WWN `57c3548000000003`. Both WWNs share NAA+OUI `5 7c3548 …`; only the vendor suffix
differs → confirms same-vendor, and confirms the two drives stay **unique** (no collision).

### Where the identity lives in `ISP2259.bin`

From `ModifyISPFile()` (the exact fields the tool itself writes). The mirror at
`0x20820` is a raw 512-byte ATA IDENTIFY (verified: word0=0x0040, word2=0xc837):

| Field | Offset | Encoding |
|---|---|---|
| Serial copy #1 (config block) | `0x0000051C` | ATA string, 20 B, little-endian per word |
| Serial copy #2 (IDENTIFY mirror, word 10) | `0x00020834` | ATA string, 20 B, LE per word |
| WWN (IDENTIFY words 108–111) | `0x000208F8` | 4×u16, LE per word |
| WWN-supported flag (word 84/87 bit 8) | `0x208C8` / `0x208CE` | already set (`0x4160`) — no change needed |

Encoding rule (verified against reference drive's raw IDENTIFY via `hdparm --Istdout`):
serial word = `(char_even<<8)|char_odd`, stored little-endian → file bytes `[char_odd, char_even]`;
left-justified, space-padded to 20 bytes. WWN word `0x57c3` → file bytes `c3 57`.

For `SERIAL0002` + `57C3548000000002` the patched bytes are:
```
serial @0x51C and @0x20834 : 45 53 49 52 4c 41 30 30 32 30 20 20 20 20 20 20 20 20 20 20
WWN    @0x208F8            : c3 57 80 54 00 00 02 00
```

### Method & result

- Patched a copy of `ISP2259.bin` at the three offsets (via `patch_identity.py`),
  then flashed with **mode 0** using the patched binary — the same proven-safe path as §4,
  no keep-data dump. Identity fields are **not** signature-gated (template default serial
  boots fine), so hand-patching is safe.
- Flash output ended `result is Pass`, `ISPMode:2`.
- Post-flash verification:
  ```
  Serial Number:    SERIAL0002
  LU WWN Device Id: 5 7c3548 000000002
  ```

**Both fields restored correctly on the first attempt.** The WWN is served from the
IDENTIFY mirror at `0x208f8` as predicted — no iteration needed. Drive now fully
recovered *and* carrying its correct factory identity.
