# Transcend SSD230S (TS4TSSD230S) — Firmware Update & Recovery

End-to-end reference for the Transcend SSD230S / Silicon Motion **SM2259**
firmware tool: extract it from the Transcend bootable ISO, **update** firmware on
a running Linux box (e.g. Unraid), **recover** a drive that has been bricked into
ROM mode, and **restore** its serial number and WWN. Also documents how the
updater works internally (from disassembly) so future work doesn't start from
scratch.

| | |
|---|---|
| **Drive model** | Transcend SSD230S — `TS4TSSD230S` (4 TB) |
| **Controller** | Silicon Motion **SM2259** — boot ROM reports `SM2259AC-70-0010M101` (silicon-authoritative); production firmware reports **SM2259AB** |
| **NAND** | WD / SanDisk BiCS5 112L; 4 CH, CH map `0x0F`, CE map `0xFF`, 32 dies |
| **Normal update mode** | `keepsn_1` (keeps SN / Model / WWN, applies new FW) |
| **Recovery mode** | `initial` (mode 0) with a 4-byte-patched flasher — see Part J |
| **Old firmware** | `22Z4W14B` (or older) |
| **New firmware** | `22Z4X4IA` |
| **Updater binary** | `SM2258TLC_3D_LinuxTool_64` (static x86-64, no deps; V1.1.6 / Lib 239) |

> 🧨 **FLASHING WIPES ALL DATA ON THE DRIVE — ALWAYS.** This applies to **every**
> mode, including `keepsn_1` and `initial` (mode 0). The update rebuilds the
> controller's flash-translation layer, so the drive comes back **blank**. Modes
> like `keepsn_1` preserve the drive's *identity* (serial / model / WWN) so the
> host still recognizes the same disk — they do **not** preserve its *contents*.
> **Back up everything first**, and expect to reformat / rebuild afterwards.

> ⚠️ **This is a firmware flash on live drives.** Read the whole document once
> before starting. Only apply this to drives that report model `TS4TSSD230S` — a
> mismatched controller/model can be bricked.

> 💡 **If your SSDs are on an AMD / ASMedia SATA controller** (e.g. Gigabyte
> B550I AORUS PRO AX — AMD 500-series chipset SATA, ASMedia silicon), you **must
> disable NCQ** (`echo 1 > /sys/block/sdX/device/queue_depth`) before flashing,
> or the `Force ROM Mode` step fails. This is baked into Part G below. Verified
> working: the update completed on exactly this board once NCQ was disabled.

> 🧰 **`setup.sh`** (repo root) builds a ready-to-use `ts-ssd230s-fw-update/`
> folder in one command — see Part A. Its building blocks live in
> [`recovery/`](recovery/): `patch_flasher.py` (rebuild + hash-verify the Patch-A
> flasher), `patch_identity.py` (restore serial/WWN into `ISP2259.bin`), and the
> case log `RECOVERY_SUMMARY.md`. The proprietary vendor flasher/firmware are
> **not** included — `setup.sh` fetches them from the ISO.

---

## What you need

There are two roles here, which **may be the same machine or two different
machines**:

- **Flashing host** — a **Linux** box, running as **root**, with the target SSD
  on a **real motherboard SATA port**. This is where the tool actually runs. Any
  Linux distro works (Unraid, Ubuntu, a live USB, the Transcend ISO itself…).
  - 🛑 **The target SSD must NOT be the disk you booted from.** The flash forces
    the controller into ROM mode and rewrites it, so it cannot be the running
    system/boot drive. Boot from a *different* disk (another SSD, a USB Linux, or
    the Transcend live ISO) and flash the SSD as a **secondary** drive.
  - 🛑 **No USB-to-SATA adapters/docks.** The tool talks to the drive over SATA
    **ATA pass-through** (`SG_IO`); USB bridges don't pass the vendor commands.
    Use a native motherboard SATA port.
- **Extraction host** — any machine (macOS, Linux, Windows) used only to pull the
  `initial/` payload out of the Transcend ISO. **This can simply be the flashing
  host itself** — in which case you do everything locally and skip the network
  transfer (Part B).

You also need the Transcend SSD230S firmware-update **ISO** (it contains
`fossapup64save.4fs`). Everything the updater needs lives **only** inside that
save file — a plain ext4 image; the rest of the ISO is stock Puppy Linux.

> **Get the vendor ISO** (the flasher + firmware images are Transcend/Silicon
> Motion proprietary and are **not** redistributed in this repo — extract them
> yourself): archived at
> <https://github.com/leopard-archives/Transcend-SATA-SSD-230S-4TB/releases/tag/22Z4X4IA>.

> **Two-machine setup?** Run `setup.sh` on whichever machine is convenient
> (Part A), then copy the whole `ts-ssd230s-fw-update/` folder to the Linux
> flashing host (Part B). If you run `setup.sh` directly on the flashing host,
> skip Part B.

---

## Quick reference — inspecting drives

Use these any time to find drives and read their current identity. **Always
derive `/dev/sdX` fresh from `by-id`; never trust a remembered letter.**

```sh
# List every SSD230S by model → /dev/sdX (ignore -part entries)
ls -l /dev/disk/by-id/ | grep -i TS4TSSD230S

# All drives at a glance: node, model, serial, firmware
lsblk -d -o NAME,MODEL,SERIAL,REV,SIZE

# Full identity of one drive: model, serial, WWN, firmware, capacity
smartctl -i /dev/sdX | grep -iE 'device model|serial|LU WWN|firmware|user capacity'

# Just serial + WWN (what you check after an identity restore)
smartctl -a /dev/sdX | grep -iE 'Serial|WWN'
hdparm -I  /dev/sdX | grep -iE 'Serial Number|WWN'

# Raw ATA IDENTIFY as 256 hex words (serial=words 10-19, WWN=words 108-111)
hdparm --Istdout /dev/sdX

# Loop over all SSD230S: node, sticker-id, firmware
for l in /dev/disk/by-id/ata-TS4TSSD230S_*; do
  case "$l" in *-part*) continue;; esac
  dev=$(basename "$(readlink -f "$l")")
  printf '%-4s %-28s ' "$dev" "${l##*/}"
  smartctl -i "/dev/$dev" | grep -i 'firmware version'
done
```

A drive that reports model `SM2259AC-70-0010M101`, firmware `20200324`, and a
capacity of **exactly ~1 GB (1024 MB)** instead of 4 TB is **in ROM mode
(bricked)** — go to Part J.

### The tool's flash-ID / Card Information readout — healthy vs ROM mode

The vendor tool prints "Card Information" at the start of every run (and you can
read it non-destructively with `./SM2258TLC_3D_LinuxTool_64 /dev/sdX showIDinfo`).
**The same physical SSD reports completely different info depending on its mode** —
because in ROM mode the loader hasn't initialised the NAND translator, so it can't
enumerate the flash or read the real identity:

| Field | Healthy (production FW running) | ROM mode (bricked) |
|---|---|---|
| `ISPMode` (`g_FlashIDTable[+4]`) | **2** | **0** |
| Model Name | `TS4TSSD230S` | `SM2259AC-70-0010M101` (ROM loader) |
| Firmware | `22Z4W14B` / `22Z4X4IA` | `20200324` (ROM loader build) |
| User capacity | 4 TB | **~1 GB (1024 MB)** |
| Serial Number | real (matches sticker) | generic/garbage ROM-loader default (not the sticker serial) |
| LU WWN | real | `0` |
| Flash ID / CE-CH map | **full**: 4 CH, CH map `0x0F`, CE map `0xFF`, **32 dies**, WD/SanDisk BiCS5 112L, Samsung DRAM | **not populated** → `CheckFlashID` prints `flash error, Please Check you flash` |

So a healthy `showIDinfo` enumerates all 32 dies and the CE/CH map; the *same drive*
in ROM mode can't, which is precisely why `CheckFlashID` (the tool's first step)
aborts and why recovery needs the Patch-A flasher (Part J) to get past that gate.
Once recovered to production firmware, the full readout returns.

---

## Part A — Build the tool folder (`setup.sh`)

`setup.sh` (repo root) does everything automatically: downloads the vendor ISO
(SHA-1/MD5-verified, reused if already present), extracts the flasher + firmware,
builds the Patch-A recovery flasher (SHA-256/MD5-verified against the known
build), and assembles one **self-contained** working folder.

```sh
./setup.sh                       # → ./ts-ssd230s-fw-update/
#   custom location: ./setup.sh /path/to/outdir
#   needs: 7z  (or, on Linux without 7z, root for a loop-mount fallback)
#          python3, and curl or wget
```

The result — **everything the flash/recovery steps need is in this one folder:**

```
ts-ssd230s-fw-update/
├── SM2258TLC_3D_LinuxTool_64          # vendor flasher      (normal updates, Part G)
├── SM2258TLC_3D_LinuxTool_64.patched  # Patch-A flasher     (ROM-mode recovery, Part J)
├── patch_identity.py                  # serial/WWN restorer (Part K)
└── REGBIN/2259/…                       # firmware images (ISP2259.bin, MP2259.bin, Shim2259.bin, …)
```

Runs on **macOS or Linux** (extraction uses `7z`, no root needed; a Linux host
without `7z` falls back to loop-mounting as root). If you run it via `sudo`, it
hands the folder back to your user so it stays accessible. Prefer not to use the
script? See "Manual extraction" in the appendix.

> Always run the flasher from **inside** `ts-ssd230s-fw-update/` — it builds its
> file paths as `./REGBIN/2259/<name>.bin` **relative to the current directory**
> and writes logs there. `patch_identity.py` likewise defaults to
> `./REGBIN/2259/ISP2259.bin`. Run them from anywhere else and they won't find the
> firmware.

---

## Part B — Get the folder onto the flashing host *(skip if local)*

The flasher is a **Linux x86-64** binary and must run on the host that has the
drive. If you ran `setup.sh` on a different machine (e.g. your desktop), copy the
**entire `ts-ssd230s-fw-update/` folder** to the Linux flashing host — it is
self-contained (both flashers + `patch_identity.py` + all firmware), so nothing
else needs to go with it.

```sh
# from the machine where you ran setup.sh:
rsync -a ts-ssd230s-fw-update/ root@FLASHHOST:/root/ts-ssd230s-fw-update/
#   or:
scp -r ts-ssd230s-fw-update root@FLASHHOST:/root/
```

(SMB or any file share works too — just copy the whole folder.) Land it on
**persistent storage, not `/tmp`** (Unraid and others wipe `/tmp` on reboot). If
`setup.sh` already ran on the flashing host, skip this.

> On the flashing host, make sure the binaries are executable after the copy
> (`chmod +x ts-ssd230s-fw-update/SM2258TLC_3D_LinuxTool_64*`) — some transfer
> methods (SMB, zip) drop the exec bit.

---

## Part D — Identify the target drives

> 🛑 **Never pass the booted system disk.** Confirm the target is a *secondary*
> drive — `lsblk` shows what's mounted, and `findmnt /` / `lsblk -no PKNAME $(findmnt -no SOURCE /)`
> shows which disk you booted from. The flash rewrites the controller; doing that
> to the running OS disk will crash the machine mid-write.

> ℹ️ **Don't bother with the tool's `-list` option — it does not work for SATA
> drives.** This build only enumerates NVMe devices via `-list`; for a SATA
> SSD230S it just prints the help banner. Identify the disk with `by-id` and pass
> the `/dev/sdX` node directly (the tool reaches it via ATA pass-through).

```sh
ls -l /dev/disk/by-id/ | grep -i TS4TSSD230S
```

Example (yours will differ — **always derive from this, never a remembered
`sdX`**):

```
ata-TS4TSSD230S_SERIAL0001 -> ../../sdc
ata-TS4TSSD230S_SERIAL0002 -> ../../sdd
ata-TS4TSSD230S_SERIAL0003 -> ../../sde
ata-TS4TSSD230S_SERIAL0004 -> ../../sdb
```

So here: `sdb`, `sdc`, `sdd`, `sde`. (Ignore the `-part1` entries.)

---

## Part E — Check current firmware (before)

```sh
for d in b c d e; do
  echo "== /dev/sd$d =="
  smartctl -i /dev/sd$d | grep -iE 'device model|serial|firmware'
done
```

Any drive reporting `22Z4W14B` (or older) is a candidate. If one already reads
`22Z4X4IA`, **skip it**. Adjust the drive-letter list to match Part D.

---

## Part F — Take the target drive offline

The target SSDs must be **idle and unmounted** during the flash — nothing may be
reading/writing them, and they must not be part of a running array/pool.

- **Generic Linux:** `umount` any filesystems on the drive; stop anything using it
  (`lsof`, a `mdadm`/LVM/ZFS array, containers, `smartd`). `lsblk` should show no
  mountpoints for the target `sdX`.
- **Unraid:** GUI → **Main → Array Operation → Stop** (stops the array *and* any
  pool containing these SSDs).

Do not proceed until the target drive is fully idle and unmounted.

> 🧨 **The flash wipes all data on every drive you flash — no exceptions.**
> `keepsn_1` preserves each drive's *serial/model/WWN*, so Unraid still
> recognizes them as the same disk slots afterward — **but the contents are
> gone** (the controller rebuilds its translation layer and the drive comes back
> blank). Back up anything you need off these SSDs **before** flashing, and plan
> to rebuild/reformat them afterwards. In a redundant array you can flash and
> rebuild one member at a time; a single unmirrored drive loses its data outright.

---

## Part F½ — Redundancy strategy for arrays & pools

> 🧨 **The flash wipes every drive it touches.** In a multi-drive array or pool,
> treat each flash as **pulling that drive and returning it blank**. Plan around
> your redundancy level so you never take down more members at once than the array
> can survive — and **update one drive at a time** unless you have a specific
> reason not to.

| Layout | Rule |
|---|---|
| **Single drive / RAID 0 / striped pool / JBOD** | **No redundancy — back up all data first.** Losing any one member of a stripe loses the whole set. Flash, then restore from backup. |
| **Mirror — RAID 1 / 2-way mirror / mirrored pool (btrfs/zfs)** | Flash **one** member, **let the mirror fully resync/rebuild** onto it and confirm healthy, **then** the next. Never flash both halves at once. |
| **Parity / RAID-Z / RAID 5-6 / Unraid parity array** | Flash **one drive at a time**; let the array **rebuild that member** to a clean state before the next. |

You *may* flash up to **as many drives simultaneously as the profile tolerates
being down** — RAID 5 / RAID-Z1 = 1, RAID 6 / RAID-Z2 = 2, RAID-Z3 = 3, 3-way
mirror = 2 — but the simplest, safest approach is always **one at a time: flash →
rebuild/resync → verify → next**.

General rules, every level:

1. Take the target drive **out of the active array / offline** first (Part F) —
   you can't flash a member while it's in use.
2. After flashing (and identity restore, Part K, if needed), **re-add the drive
   and wait for the resync/rebuild to complete** before touching the next one.
   **Check array health between drives.**
3. `keepsn_1` keeps the serial so the host still recognizes the slot, but the
   **contents are gone** — the array sees a replaced/empty member to rebuild.

**Unraid specifics:** the main array is parity-protected (1 or 2 parity drives →
tolerate 1 or 2 down). A flashed data drive comes back blank, so let Unraid
**rebuild it from parity** (normal replace flow) before doing the next. Unraid
**pools** (cache) use a btrfs/zfs profile — apply the matching row above (e.g. a
RAID1 cache: flash one device, let it resync, then the other).

---

## Part G — Flash the firmware (normal update)

> 🛑 **Disable NCQ first — mandatory on AMD/ASMedia SATA controllers.**
> With NCQ enabled, `Force ROM Mode` fails with `Force ROM Error` / hangs and
> drops the drive off the bus. Setting queue depth to `1` disables NCQ and lets
> the flash proceed. **This was the single fix that made the update work on the
> Gigabyte B550I (AMD/ASMedia chipset SATA).** See Troubleshooting for the story.
>
> `queue_depth` is **per-device and resets on reboot / re-link** — set it again
> for each drive, and again if a drive drops and comes back under a new letter.

**Step 1 — disable NCQ on all SSD230S drives at once** (finds them by model, sets
`queue_depth=1`, prints each to confirm):

```sh
for l in /dev/disk/by-id/ata-TS4TSSD230S_*; do
  case "$l" in *-part*) continue;; esac              # skip partition entries
  dev=$(basename "$(readlink -f "$l")")              # ata-...SERIAL0004 -> sdb
  echo 1 > "/sys/block/$dev/device/queue_depth"
  printf '%-4s %-28s queue_depth=%s\n' \
    "$dev" "${l##*/}" "$(cat /sys/block/$dev/device/queue_depth)"
done
```

Every line must read `queue_depth=1`. To **re-check** later, run the same loop
with the `echo 1 > ...` line removed.

**Step 2 — flash, one drive at a time.** Watch each finish with
`Total Process finished, the result is Pass` before starting the next. Derive
each `sdX` fresh from the Part D lookup:

```sh
cd /root/ts-ssd230s-fw-update
./SM2258TLC_3D_LinuxTool_64 /dev/sdb keepsn_1
./SM2258TLC_3D_LinuxTool_64 /dev/sdc keepsn_1
# ... one per drive
```

Each run walks the same sequence (matches the known-good log):

```
Card Information → Dump ISP for keep data → Force ROM Mode → Done
Download ShimMPISP → Download MPISP → Do pretest → Success
Check Pretest Flash → Download ISP → Reset CPU → Done
Card Information (Firmware Ver: 22Z4X4IA) → result is Pass
```

> ⚠️ **Point of no interruption.** Once a run passes `Force ROM Mode → Done` and
> starts `Download ShimMPISP`, it is actively writing the controller. **Do not
> Ctrl-C, power off, or unplug** until you see `result is Pass`. `Jump to
> Pretest!` may pause for up to ~2 minutes — that is normal (a 120 s ATA
> timeout), let it run.
>
> If the **first** drive errors or stalls *before* any download stage, **stop**
> and see Troubleshooting before touching the others. Failures at `Force ROM
> Mode` happen before any write and are recoverable (cold power cycle).

---

## Part H — Verify (after)

```sh
for l in /dev/disk/by-id/ata-TS4TSSD230S_*; do
  case "$l" in *-part*) continue;; esac
  dev=$(basename "$(readlink -f "$l")")
  printf '%-4s %-28s ' "$dev" "${l##*/}"
  smartctl -i "/dev/$dev" | grep -i 'firmware version'
done
```

Every flashed drive should now report `Firmware Version: 22Z4X4IA`.

---

## Part I — Bring the drive back online

Once all drives verify:

- **Generic Linux:** re-mount / re-assemble as needed (the drives are **blank** —
  see the wipe warning; expect to reformat or rebuild them).
- **Unraid:** GUI → **Main → Array Operation → Start**.

Confirm the SSDs appear with correct serials. Clean up when satisfied:

```sh
rm -rf /root/ts-ssd230s-fw-update
# optionally also remove /mnt/user/isos/fossapup64save.4fs
```

---

## Part J — Recover a drive stuck in ROM mode

Use this when a drive has **bricked into ROM mode** and a cold power cycle does
**not** bring it back (i.e. the failure happened *during/after* the download
stages of a `keepsn*` run, not before).

### Symptoms

- Enumerates as model `SM2259AC-70-0010M101`, firmware `20200324`, capacity
  **exactly ~1 GB (1024 MB)** (not 4 TB), `LU WWN Device Id: 0`.
- A normal `keepsn_1` (or any mode) run stops at **`Checking Flash ID … flash
  error, Please Check you flash`** with `ErrorCode: 0001`, before `Force ROM
  Mode`.
- The original brick log ended `Check Running MPISP Mode Fail` → `Initial card
  failed`.

### Why the normal tool can't recover it

The updater assumes **production firmware is running** when it starts:

- Its first step, `CheckFlashID`, reads the live NAND flash-ID map and compares
  it to `FLASHID.bin`. In ROM mode the loader doesn't populate that map, so the
  compare fails → `flash error` and the run aborts **before** the recovery
  download engine ever runs. (This is a *mode* problem, not a real flash fault.)
- The `keepsn*` modes additionally need the old firmware alive to read
  per-drive keep-data (`Dump ISP for keep data`). They can't run from ROM.

The fix is a **2-byte patch** that neutralizes the `CheckFlashID` gate, plus
**mode 0 (`initial`)**, which is the only mode that needs no keep-data. The
download engine (`Force ROM → Shim → MPISP → Pretest → Program ISP`) *is*
ROM-capable — the tool forces ROM mode itself before every download.

### Step 1 — the patched flasher (already built by `setup.sh`)

`setup.sh` already produced **`SM2258TLC_3D_LinuxTool_64.patched`** in the folder,
so you can jump to Step 2. Patch A makes `CheckFlashID` return "pass" immediately
(`xor eax,eax; ret`), clearing the ROM-mode gate for every caller — it changes
**4 bytes** and writes nothing extra to the drive.

If you assembled the folder manually (no `setup.sh`), build it with the
hash-verifying reproducer [`recovery/patch_flasher.py`](recovery/patch_flasher.py):

```sh
cd ts-ssd230s-fw-update
python3 /path/to/recovery/patch_flasher.py SM2258TLC_3D_LinuxTool_64
#  -> Output ... sha256 8c08bedce733...  -> MATCHES the reference patched binary. ✔
```

Known-good hashes (what `setup.sh`/`patch_flasher.py` verify against):

| File | SHA-256 |
|---|---|
| original `SM2258TLC_3D_LinuxTool_64` | `a89f11aed20a3020bbf874386efba371ffc93b307d2f5399603893c06bffdb63` |
| patched `SM2258TLC_3D_LinuxTool_64.patched` | `8c08bedce733f67c088ea14a7b4b3b71838d0e30c8aa24056f512fd5f742b526` |

<details><summary>Manual alternative without the script (offset <code>0x4d8c</code> = 19852)</summary>

```sh
cp SM2258TLC_3D_LinuxTool_64 SM2258TLC_3D_LinuxTool_64.patched
od -An -tx1 -j 19852 -N 4 SM2258TLC_3D_LinuxTool_64.patched   # MUST print: 55 48 89 e5
printf '\x31\xc0\xc3\x90' | dd of=SM2258TLC_3D_LinuxTool_64.patched bs=1 seek=19852 count=4 conv=notrunc
od -An -tx1 -j 19852 -N 4 SM2258TLC_3D_LinuxTool_64.patched   # MUST print: 31 c0 c3 90
chmod +x SM2258TLC_3D_LinuxTool_64.patched
```
If the first `od` prints anything other than `55 48 89 e5`, **stop** — the binary
differs from the analyzed build; do not patch blindly.
</details>

### Step 2 — recover with mode 0

```sh
cd ts-ssd230s-fw-update                # the folder from setup.sh (has REGBIN/2259/*.bin)
# (disable NCQ if on AMD/ASMedia — see Part G Step 1)
./SM2258TLC_3D_LinuxTool_64.patched /dev/sdX initial
```

Expected sequence (this is the known-good recovery log):

```
ISPMode:0
Force ROM Mode ........................ Done
Download ShimMPISP ................... Success   (shim → SRAM 0x40020000)
Download MPISP ...................... Success   (MP  → SRAM 0x40040000)
Do pretest .......................... Jump to Pretest! Success
Check Pretest Flash ................. Success
Tran Sys block fail                 (non-fatal warning)
ISPMode:1
Download ISP ........................ Success
Reset CPU ........................... Done
ISPMode:2                            ← production firmware running again
Firmware Ver : 22Z4X4IA
Total Process finished, the result is Pass
```

`Jump to Pretest!` may sit for up to ~2 minutes (DRAM training + flash self-test,
120 s ATA timeout) — **do not interrupt.** If it dies at the pretest stage, that
points to a real hardware fault (DRAM/NAND); check SMART `159 DRAM_1_Bit_Error`
after any partial recovery. In the verified case, hardware was fine and mode 0
succeeded.

### Step 3 — verify + power-cycle

```sh
smartctl -a /dev/sdX | grep -iE 'Model|Serial|Firmware|WWN'
```

Model `TS4TSSD230S`, FW `22Z4X4IA`, full 4 TB. The **serial is now a generic
default** (e.g. `0028522400000080001E`) and **WWN is `0`** — mode 0 flashes the
stock ISP with no keep-data. Restore the real identity in Part K. Then full
power-cycle and run a short self-test (`smartctl -t short`).

> The drive is effectively factory-reset (FTL rebuilt) — treat it as **blank**.
> Whatever was on it (already unreachable in ROM mode) is gone.

---

## Part K — Restore Serial & WWN after a mode-0 recovery

A mode-0 recovery leaves a generic serial and zero WWN. If the drive's **own**
serial and WWN are on its sticker, you can restore them by patching those fields
into `ISP2259.bin` and re-flashing with mode 0.

> 🛑 **WWN must stay globally unique.** Use the values from **this drive's own
> sticker** — never copy another drive's WWN (that creates a duplicate WWN and
> breaks Unraid/ZFS/multipath disk identity). The `keepsn*` modes can't help here
> because they copy the card's *current* (now-generic) identity.

### Easiest: the Python restorer

`patch_identity.py` is already in the folder (`setup.sh` put it there). It backs
up `ISP2259.bin` (pristine `.orig` + timestamped), verifies the image by
identity-masked SHA-256, prompts for serial + WWN, writes the fields with the
verified encoding, and confirms by decoding back.

```sh
cd ts-ssd230s-fw-update
./patch_identity.py                        # defaults to ./REGBIN/2259/ISP2259.bin
#   ISP image  : verified vendor ISP2259.bin (22Z4X4IA).
#   Serial Number (from sticker): SERIAL0002
#   WWN          (from sticker) : 57C3548000000002
#   Proceed? y

./SM2258TLC_3D_LinuxTool_64 /dev/sdX initial           # re-flash with real identity
smartctl -a /dev/sdX | grep -iE 'Serial|WWN'           # after power-cycle
```

> ℹ️ **Use the normal (unpatched) flasher here — not the Part J `.patched` one.**
> By now the drive is back on production firmware (`ISPMode:2`), so `CheckFlashID`
> takes its mode-2 "extended" branch and **passes on its own** — there's no
> ROM-mode gate left to bypass. The unpatched tool is preferable because it keeps
> that flash-ID validation as a safety check. (Keep the `.patched` binary handy
> only in case a reflash ever drops the drive back into ROM mode — then you're in
> Part J again.)

Expected:

```
Serial Number:    SERIAL0002
LU WWN Device Id: 5 7c3548 000000002
```

To revert to the generic-but-known-good ISP at any time:
`cp REGBIN/2259/ISP2259.bin.orig REGBIN/2259/ISP2259.bin`.

### Manual alternative (`dd`)

The three identity fields in `ISP2259.bin` (see Appendix for the derivation):

| Field | Offset (hex / dec) | Bytes |
|---|---|---|
| Serial copy #1 (config block) | `0x0000051C` / `1308` | 20 |
| Serial copy #2 (IDENTIFY mirror) | `0x00020834` / `133172` | 20 |
| WWN (IDENTIFY words 108–111) | `0x000208F8` / `133368` | 8 |

Encoding (verified against a healthy drive's `hdparm --Istdout`):

- **Serial**: ASCII, left-justified, space-padded to 20 bytes, stored
  little-endian per 16-bit word → for each char pair the file bytes are
  `[char_odd, char_even]`.
- **WWN**: the 16 hex digits as four u16 words, little-endian per word
  (`57C3 5480 0000 0002` → bytes `c3 57 80 54 00 00 02 00`).

Worked example for serial `SERIAL0002`, WWN `57C3548000000002`:

```sh
cd /root/ts-ssd230s-fw-update/REGBIN/2259
cp ISP2259.bin ISP2259.bin.orig
SER='\x45\x53\x49\x52\x4c\x41\x30\x30\x32\x30\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20'
printf "$SER" | dd of=ISP2259.bin bs=1 seek=1308   count=20 conv=notrunc
printf "$SER" | dd of=ISP2259.bin bs=1 seek=133172 count=20 conv=notrunc
printf '\xc3\x57\x80\x54\x00\x00\x02\x00' | dd of=ISP2259.bin bs=1 seek=133368 count=8 conv=notrunc
# verify
od -An -tx1 -j 1308   -N 20 ISP2259.bin    # 45 53 49 52 4c 41 30 30 32 30 20 ...
od -An -tx1 -j 133368 -N 8  ISP2259.bin    # c3 57 80 54 00 00 02 00
```

The identity fields are **not** covered by any boot signature (the stock ISP
boots with its default serial), so hand-patching them is safe — it's exactly what
the tool's own `ModifyISPFile` does at manufacturing.

---

## Troubleshooting

- **`-list` just prints the usage/help banner.** Expected — NVMe-only. Target the
  `/dev/sdX` node directly (ATA pass-through).

- **`Force ROM Error` / `ErrorCode: 0001`, or hang at `Force ROM Mode`.** On
  **AMD/ASMedia SATA controllers** (AMD 500-series chipset SATA `[1022:43eb]`,
  ASMedia silicon, e.g. Gigabyte B550I) the `Force ROM Mode` vendor command fails
  while **NCQ is enabled**. **Fix: disable NCQ before flashing:**
  ```sh
  echo 1 > /sys/block/sdX/device/queue_depth   # queue depth 1 = NCQ off
  cat  /sys/block/sdX/device/queue_depth        # confirm it reads 1
  ```
  Per-device, **resets on reboot/re-link** — re-apply for every drive and after
  any drop. (Link power management / ALPM is *not* the cause.)

- **Drive drops off the bus / shows `SM2259AC-…`, exactly ~1 GB (1024 MB), no SMART, after a failed
  `Force ROM Mode`.** Controller is in ROM/bootloader mode. If the failure was
  *before* the download stage, no firmware was written — try a bus rescan
  (`for h in /sys/class/scsi_host/host*/scan; do echo "- - -" > "$h"; done`), then
  a **full cold power cycle** (off 30–60 s; warm reboot is not enough). It should
  come back with its real identity. Then disable NCQ and retry.

- **`Checking Flash ID … flash error` on a `SM2259AC` drive, or a brick that a
  cold power cycle won't fix.** The drive is stuck in ROM mode and the normal tool
  can't get past its flash-ID gate — **go to Part J** (patched flasher + mode 0).

- **`Check Running MPISP Mode Fail` during a `keepsn*` run.** The keep-data dump
  hit an unreadable system block (`Tran Sys block fail`) and aborted at the
  pretest stage, leaving the drive in ROM mode. Recover with Part J (mode 0 skips
  the keep-data path), then restore identity with Part K.

- **Failure/hang during/after `Download ShimMPISP`.** This stage writes the
  controller — do **not** interrupt. If it dies here the drive may land in ROM
  mode; recover with Part J.

- **Drive letters changed after a reboot.** Re-run the Part D `by-id` lookup and
  map serials → current `sdX`. Re-apply `queue_depth=1` to the new node.

- **`mount` complains about ext4 features.** Add an explicit type:
  `mount -t ext4 -o loop,ro <file> /tmp/tsfw`.

- **Last-resort fallback if a controller still won't cooperate.** Native **Intel
  chipset SATA** ports run these Silicon Motion tools most reliably. If NCQ-off
  doesn't fix a particular controller, flash on an Intel machine's motherboard
  SATA port. Avoid USB-to-SATA adapters — they don't pass the vendor commands.

---

## Reference — what's in the bundle

**This repo (your original work — the only things committed):**

```
.
├── setup.sh                # bootstrap: download ISO → extract → build → assemble the folder
├── TS-SSD230S-Firmware-Update.md
├── .gitignore              # excludes the ISO + ts-ssd230s-fw-update/ (proprietary/large)
└── recovery/
    ├── patch_flasher.py    # rebuild + hash-verify the Patch-A flasher from the vendor binary
    ├── patch_identity.py   # serial/WWN restorer for ISP2259.bin (identity-masked hash check)
    └── RECOVERY_SUMMARY.md  # detailed case log of the un-brick + identity restore
```

**`setup.sh` produces (git-ignored — proprietary vendor bits + the patched build):**

```
ts-ssd230s-fw-update/
├── SM2258TLC_3D_LinuxTool_64          # vendor flasher (static x86-64 ELF, not stripped)
├── SM2258TLC_3D_LinuxTool_64.patched  # Patch-A recovery flasher (built + hash-verified)
├── patch_identity.py                  # copied in by setup.sh
└── REGBIN/2259/
    ├── ISP2259.bin      # production firmware image (789504 B) — flashed to SRAM 0x80000000
    ├── MP2259.bin       # MPISP / manufacturing engine (262048 B) → SRAM 0x40040000
    ├── Shim2259.bin     # SRAM first-stage loader (107536 B) → SRAM 0x40020000, entry +0x410
    ├── SWISP.bin, SelfTest2259.bin, DLMicroCode.bin
    ├── FLASHID.bin      # reference flash-ID / CE-CH map (1024 B)
    └── DumpISP1File.bin, DumpISP2File.bin   # keep-data dumps written during a keepsn run
```

> **Proprietary bits are not redistributed here.** The vendor flasher and firmware
> `.bin` images come from Transcend/Silicon Motion — `setup.sh` fetches them from
> the archived ISO
> (<https://github.com/leopard-archives/Transcend-SATA-SSD-230S-4TB/releases/tag/22Z4X4IA>).
> The patched flasher is reproduced locally by `patch_flasher.py` (hash-verified),
> not shipped. The `.gitignore` keeps the ISO and `ts-ssd230s-fw-update/` out of git.

**Command modes** (from the tool's own help; `argv[1]`=device, `argv[2]`=command):

| Command | Mode | Meaning |
|---|---|---|
| `keepsn_1` | 3 | Keep IDtable/VendorSpecific + **SN/Model/WWN**, apply new FW. ← *Transcend's own updater* |
| `keepsn_2` | 4 | Keep SN/WWN (not model). |
| `keepsn`   | 2 | Keep serial number. |
| `initial`  | 0 | Init card, **no** keep-data → generic identity. ← *used for ROM recovery (Part J)* |
| `initial <SN>` | 1 | Init card, write the given serial (≤20 chars). |
| `update` / `update_1` / `swap` / `swap_1` | 5–8 | **In-place ISP swap** from running FW (no ROM cycle / no pretest) — different keep-data mixes. See appendix "Two flash paths". |
| `-list` | — | NVMe-only enumeration (useless for SATA). |
| `showIDinfo`, `dump_log` | — | Print card info / dump event log. |

> Only **`initial` (mode 0)** skips the keep-data step (`ModifyISPFile` →
> `DumpISPBlock`) — every other mode reads the on-NAND system blocks, which is the
> step that bricked a drive here (`Tran Sys block fail`). The **model name is a
> constant in `ISP2259.bin`** (not per-drive data), so it's always correct after
> flashing the matching-model image — no restore needed.

The Transcend ISO's own config (`/root/Startup/FWUpdateInfo.ini`) specifies
`Command=keepsn_1` for this model — that (mode 3) is what an orchestrated update
runs, and it's the path that bricked a drive here when it couldn't read a system
block.

---

## Appendix — How the updater works (disassembly reference)

From reverse-engineering `SM2258TLC_3D_LinuxTool_64` (unstripped C++ symbols;
`objdump`). Kept so future work doesn't restart from scratch. VA→file offset:
**file_off = VA − 0x400000**.

### Run-mode byte — the heart of everything

Every `ReadFlashID` response byte **`[+4]`** (in `g_FlashIDTable` @ `0x857780`) is
the controller mode. The whole state machine gates on it:

| `[+4]` | Meaning |
|---|---|
| `0` | ROM loader / blank (bricked state) |
| `1` | MPISP running |
| `2` | Production firmware running (what the tool expects at start) |
| `3` | Shim-MPISP running |

### State machine — `InitCard(mode)` @ `0x403fb7`

```
CheckFlashID()             0x404d8c   "Checking Flash ID"  (gate; fails in ROM mode)
  if mode∈{2,3,4}: require [+4]==2, else "It is not ISP mode!"
  ModifyISPFile(mode)      0x407e53   "Dump ISP for keep data" + patch SN/WWN/config
  if [+4]==2: ReadPar(...)            read keep parameters
ForceToROM()               0x4066ee   ResetCPU, confirm [+4]==0  → "Force ROM Mode Done"
DL_SHIM_MPISP()            0x405685   Shim2259.bin → SRAM 0x40020000, JumpCode 0x40020410, want [+4]==3
DLMPISP()                  0x40598f   MP2259.bin  → SRAM 0x40040000, ActiveMpIspIspCode, want [+4]==1
DoPretest()                0x405610   RunPretestCode()          "Jump to Pretest!"
CheckPretestFlash()        0x405c8f   want [+4]==1, else "Check Running MPISP Mode Fail!"
CheckCardMode()            0x403897   enumerate CE/CH map, die count
WriteISPFirmware(file,0)   0x4060fb   ISP → SRAM 0x80000000, save/rewrite LBA0, ResetCpu, want [+4]==2
```

Key functions: `CheckFlashID` 0x404d8c, `ForceToROM` 0x4066ee, `DL_SHIM_MPISP`
0x405685, `DLMPISP` 0x40598f, `DoPretest` 0x405610, `CheckPretestFlash` 0x405c8f,
`WriteISPFirmware` 0x4060fb, `ModifyISPFile` 0x407e53, `DumpISPBlock` 0x404329.
The SM2259 ROM-auth functions (`ReadFlashID_ROM`, `WriteMPISPData_ROM`,
`JumpCode_AuthRSA`, `DownloadISPToRAMTSB3D`, `TriggerISPSelfUpdate`) exist but
have **0 call sites** — this tool never uses the RSA-signed ROM path.

### Two flash paths — `InitCard` (keepsn/initial) vs `SwapISP` (update/swap)

The command keyword selects one of **two structurally different** paths:

| | `keepsn` / `initial` — `InitCard()` @ `0x403fb7` | `update` / `swap` — `SwapISP()` @ `0x4078e6` |
|---|---|---|
| Modes | 0–4 | 5–8 |
| Force ROM (`ForceToROM`) | ✅ | ❌ |
| Download Shim + MPISP | ✅ | ❌ |
| Pretest — DRAM train + flash test (`DoPretest`/`CheckPretestFlash`) | ✅ | ❌ |
| ISP version-compat gate (`CheckBinISPVerWithFlash` @ `0x4095a8`) | ❌ | ✅ |
| Keep-data patch (`ModifyISPFile`) | modes 1–4 | ✅ |
| Write firmware (`WriteISPFirmware`) | ✅ | ✅ |

So **`update`/`swap` are an in-place ISP swap from the *running* production
firmware** — no ROM re-init, no bootstrap, no pretest — whereas `keepsn`/`initial`
do the heavy full re-initialization. `SwapISP` flow is:
`ReadPar → CheckFlashID → CheckBinISPVerWithFlash → ModifyISPFile → WriteISPFirmware`.

Per-mode keep-data (tool's own `--help`; all four `update`/`swap` route through
`ModifyISPFile`):

| Mode | Keeps from card | From new bin |
|---|---|---|
| `update` (5) | IDtable + VendorSpecific + SN + Model | CID, FW ver |
| `update_1` (6) | IDtable + VendorSpecific + SN | Model + FW ver |
| `swap` (7) | CID + IDtable(ATA-EC) + SN | FW ver |
| `swap_1` (8) | CID + IDtable(ISP_Block) + SN | FW ver |

> **Data:** `update`/`swap` skip the full re-init/pretest, so they *might* preserve
> user data (firmware-only swap) — but this is **unverified**; don't rely on it.

### `ModifyISPFile` — the keep-data patcher (internal, not a CLI command)

`_Z13ModifyISPFilei` @ `0x407e53`. **Not** a command you can run directly — it's
called by `InitCard` (modes **1–4**) and `SwapISP` (modes **5–8**). Plain `initial`
(mode 0) is the **only** mode that never calls it — and therefore the only mode
that skips `DumpISPBlock` (the on-NAND "Dump ISP for keep data" read that failed
with `Tran Sys block fail` and bricked the drive). What it does:

1. `system("cp -rp ISP2259.bin UpdateISPDataFile")` — patches a copy, not the original.
2. `DumpISPBlock` @ `0x404329` → "Dump ISP for keep data" (reads current ISP from NAND).
3. `ReadIdentify` (SN/WWN/model) + `ReadConfigureTable` — **reads identity *from the card*.**
4. Writes those fields into the working ISP (offsets `0x51C`, `0x20834`, `0x208F8`, …).
5. `CalSHA256` verify (`Download ISP Check Data Fail!` on mismatch).

Because step 3 reads identity **from the card**, `ModifyISPFile` can only *keep*
whatever the drive currently reports — it cannot restore a lost original. That is
exactly why `recovery/patch_identity.py` exists: it replicates step 4 (same
offsets/encoding) but writes **hand-chosen** serial/WWN, so it works on a wiped
drive where `ModifyISPFile` would only re-bake the generic identity.

### `CheckFlashID` — the ROM-mode gate (why recovery needs Patch A)

Reads `FLASHID.bin`, compares to the live table. If `[+4]==2` it does the
production extended check (passes with FW running). Otherwise it takes the
**CE/CH-map** path and `memcmp`s the live per-die IDs against `FLASHID.bin`; in
ROM mode the live map is empty → `flash error,Please Check you flash` @ `0x5b0e40`.
`FLASHID.bin` layout: flash ID `45 48 98 03 76 6C` (SanDisk/WD, mfr 0x45) at
+0x20; CE map `0xFF`, CH map `0x0F` at +0x28.

### ATA vendor protocol

Transport = Linux `SG_IO` **SCSI ATA PASS-THROUGH** (`SG_IO_Command` 0x40d0f4),
CDB opcode **`0x85`** (16-byte; `0xA1` for 12), **PIO**, timeout **120 s**
(`0x1d4c0` ms). Two-phase (`SmiReadATA` 0x40f65a / `SmiWriteATA` 0x40f472):

- **Phase 1** — 512-byte command descriptor written via ATA cmd `0xF0`, Feature
  `0x55`, LBA `0x0000_55AA`, Dev `0xE0`. Descriptor `byte0`/`byte1` = opcode /
  sub-function; `byte[6:9]` = 32-bit address for load/jump/active.
- **Phase 2** — data in/out via ATA cmd `0xF0`, Feature = descriptor `byte1`,
  sector count = `(len+0x1ff)>>9`.

| Operation | Wrapper | b0 | b1 (Feature) | addr | dir |
|---|---|---|---|---|---|
| Read Flash ID | `ReadFlashID` 0x413a34 | F0 | 20 | – | in |
| Write RAM/ISP | `WriteMPISPData` 0x4144f6 | F1 | 27 | yes | out |
| Jump code | `JumpCode` 0x414786 | F1 | 28 | yes | out |
| Active MPISP | `ActiveMpIspIspCode` 0x414fc8 | F0 | 60 | yes | in |
| Run pretest | `RunPretestCode` 0x41507a | F0 | 61 | – | in |
| Download microcode | `DownloadMicrocode` 0x40e8c4 | standard ATA `0x92` | | | out |

(An older `SmiPreVendorCommand` 0x40ef32 uses a `READ SECTOR` LBA "knock"
sequence `55,AA,AA00,55,5500,55AA` — used by the SMART-passthrough variants, not
the 2259 ISP flow.)

### Firmware blobs

Shared header magic `0x00000010` at file `+0x400`; vector table at `+0x410`;
signed CSS blob at `0x290–0x400`.

- **Shim2259.bin** — SRAM loader → `0x40020000`, entry `0x40020410` via JumpCode;
  success = `[+4]==3`.
- **MP2259.bin** — MPISP → `0x40040000` via ActiveMpIspIspCode (not a raw jump);
  success = `[+4]==1`.
- **ISP2259.bin** — production FW, version `22Z4X4IA` at file `0x418`/`0x818`;
  identity placeholders (`0x11`/`0x22` fill) patched by `ModifyISPFile`.

### ISP identity field map (for Part K)

`ModifyISPFile` writes a raw 512-byte ATA IDENTIFY mirror at `0x20820`
(verified word0=`0x0040`, word2=`0xc837`), plus a second serial copy at `0x51c`:

| Field | ISP offset | Notes |
|---|---|---|
| Serial (config copy) | `0x0000051C` | ATA string, 20 B, LE per word |
| Serial (IDENTIFY word 10) | `0x00020834` | = mirror `0x20820 + 0x14` |
| WWN (IDENTIFY words 108–111) | `0x000208F8` | = mirror `+ 0xD8`; served WWN source |
| WWN-supported flag (words 84/87 bit 8) | `0x208C8`/`0x208CE` | already `0x4160` (set) |
| Model (IDENTIFY words 27–46) | `0x20856` | ATA string `STT4SS2D03 S` → `TS4TSSD230S` |
| Model (config copies) | `0x00538`, `0x2084e` | `0x2084e` pairs it with FW ver: `22Z4X4IA` |

Stock template default serial = `0028522400000080001E`; template WWN = `0`
(→ why a mode-0 recovery yields a generic serial and zero WWN). The **model name
is a fixed constant** in the ISP (same for every SSD230S), so it is always correct
after flashing the matching-model image — `patch_identity.py` does not touch it.

### `LBA0.bin` — output artifact, not an input

A 512-byte file the tool **writes** (never reads) into its working directory
during the firmware-write steps — `WriteISPFirmware` @ `0x4060fb` (every flash
mode) and `DownloadMicroCode_Main` @ `0x406e48`. Both `fopen("LBA0.bin", "wb")`.

Inside `WriteISPFirmware`, right after `ActiveMpIspIspCode`:

```
ReadLBA(0,1,buf)         # read sector 0 from the drive   ("Read LBA 0 Fail!" on error)
fopen("LBA0.bin","wb"); fwrite(buf,512); fclose   # dump it ("Open LBA0.bin Fail!" on error)
WriteLBA(0,1,buf)        # write the same sector 0 back
ResetCpu
```

So it reads LBA 0 → backs it up to `LBA0.bin` → writes it back, then resets — an
apparent attempt to **preserve the boot sector / partition table across the ISP
activation**. Because the flash rebuilds the FTL (data wiped), sector 0 read back
is typically blank, so in practice this is effectively a no-op + diagnostic dump.
The file is **never read back** by the tool; it's a harmless leftover — safe to
ignore or delete, contains no user data, and is not a recoverable partition table.
(A captured example held only zeros plus the ASCII controller ID `SM2259AB` at
offset `0x2e`, with no `0x55AA` MBR signature.)

### Patch catalog

`file_off = VA − 0x400000`.

| Patch | VA / file off | Bytes (orig → new) | Effect | Risk |
|---|---|---|---|---|
| **A** (used) | `0x404d8c` / `0x4d8c` | `55 48 89 e5` → `31 c0 c3 90` (`xor eax,eax;ret;nop`) | `CheckFlashID` always passes → clears ROM-mode gate | Low — gate only, no drive write |
| A-alt | `0x40411d` / `0x411d` | `74 0a` → `eb 0a` | Same, InitCard call site only | Low |
| B (no patch) | — | run `initial` (mode 0) | Skip keep-data path | Med — generic identity |
| C (avoid) | `0x405ce7` / `0x5ce7` | `74 1b` → `eb 1b` | Skip MPISP-ready check | **High** — masks real fault, can deepen brick |
| E | rd `0x40f6d4`/`0xf6d4`, wr `0x40f4ec`/`0xf4ec` | `c0 d4 01 00` (120000) → larger | Raise ATA timeout | Low |

For the full narrative case log, see [`recovery/RECOVERY_SUMMARY.md`](recovery/RECOVERY_SUMMARY.md).

---

## Appendix — Manual extraction (without `setup.sh`)

`setup.sh` (Part A) automates all of this; use these steps only if you can't or
won't run it. Goal: end up with a `ts-ssd230s-fw-update/` folder containing
`SM2258TLC_3D_LinuxTool_64` + `REGBIN/`, then build the patched flasher (Part J
Step 1) and copy in `recovery/patch_identity.py`.

Vendor ISO (SHA-1 `0a7a670cb39e0d318081ee355a0e78ac79f8218f`,
MD5 `08fd08b0c543cf2d472059f99d54487e`):
<https://github.com/leopard-archives/Transcend-SATA-SSD-230S-4TB/releases/tag/22Z4X4IA>

**With `7z` (macOS or Linux, no root):**

```sh
7z x -y -o. TS4TSSD230S_41-6440_22Z4X4IA.iso fossapup64save.4fs        # → ./fossapup64save.4fs
7z x -y -ots-ssd230s-fw-update-raw fossapup64save.4fs \
      root/Startup/22Z4W14B/TS4TSSD230S/initial
mkdir -p ts-ssd230s-fw-update
cp -a ts-ssd230s-fw-update-raw/root/Startup/22Z4W14B/TS4TSSD230S/initial/SM2258TLC_3D_LinuxTool_64 \
      ts-ssd230s-fw-update-raw/root/Startup/22Z4W14B/TS4TSSD230S/initial/REGBIN \
      ts-ssd230s-fw-update/
chmod +x ts-ssd230s-fw-update/SM2258TLC_3D_LinuxTool_64
```

**With loop-mount (Linux, root):**

```sh
mkdir -p /tmp/iso /tmp/fs
mount -o loop,ro TS4TSSD230S_41-6440_22Z4X4IA.iso /tmp/iso
mount -o loop,ro /tmp/iso/fossapup64save.4fs /tmp/fs      # nested ext4 mount
cp -a /tmp/fs/root/Startup/22Z4W14B/TS4TSSD230S/initial/SM2258TLC_3D_LinuxTool_64 \
      /tmp/fs/root/Startup/22Z4W14B/TS4TSSD230S/initial/REGBIN \
      ./ts-ssd230s-fw-update/
umount /tmp/fs; umount /tmp/iso
```

The payload lives at `root/Startup/22Z4W14B/TS4TSSD230S/initial/` inside the
`.4fs`. The extracted `SM2258TLC_3D_LinuxTool_64` must hash to
`a89f11aed20a3020bbf874386efba371ffc93b307d2f5399603893c06bffdb63` (SHA-256).
