#!/usr/bin/env bash
#
# setup.sh — one-shot bootstrap for the SM2259 / Transcend SSD230S flash tools.
#
# Downloads the official Transcend firmware ISO, extracts the vendor flasher and
# firmware images out of the embedded Puppy save file, and assembles a ready-to-use
# working folder:
#
#   ./ts-ssd230s-fw-update/
#   ├── SM2258TLC_3D_LinuxTool_64          # vendor flasher (from the ISO)
#   ├── SM2258TLC_3D_LinuxTool_64.patched  # Patch-A recovery flasher (built + hash-verified)
#   ├── patch_identity.py                  # serial/WWN restorer (copied from recovery/)
#   └── REGBIN/2259/…                       # firmware images (from the ISO)
#
# The proprietary bits (flasher + firmware) come straight from the vendor ISO and
# are NOT redistributed by this repo. The patched flasher is rebuilt locally via
# recovery/patch_flasher.py, which verifies it byte-for-byte against known hashes.
#
# Works on macOS and Linux. Extraction uses 7z (no root); if 7z is absent it falls
# back to loop-mounting on Linux (needs root). Also needs: curl or wget, python3.
# NOTE: the flasher itself is a Linux x86-64 binary — you can PREPARE the folder on
# macOS, but you FLASH from the Linux host that has the drive.
#
# Usage:  ./setup.sh [OUTPUT_DIR]        # default OUTPUT_DIR = ./ts-ssd230s-fw-update
#
set -euo pipefail

ISO_URL="https://github.com/leopard-archives/Transcend-SATA-SSD-230S-4TB/releases/download/22Z4X4IA/TS4TSSD230S_41-6440_22Z4X4IA.iso"
ISO_NAME="TS4TSSD230S_41-6440_22Z4X4IA.iso"
ISO_SIZE=265152512                                             # fallback check if no hash tool
ISO_SHA1="0a7a670cb39e0d318081ee355a0e78ac79f8218f"           # authoritative integrity check
ISO_MD5="08fd08b0c543cf2d472059f99d54487e"
SAVE_FS="fossapup64save.4fs"                         # ext4 image inside the ISO
PAYLOAD="root/Startup/22Z4W14B/TS4TSSD230S/initial"  # payload path inside the .4fs
TOOL="SM2258TLC_3D_LinuxTool_64"

OUT="${1:-ts-ssd230s-fw-update}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECOVERY="$SCRIPT_DIR/recovery"

msg() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# portable file size (GNU vs BSD/macOS stat)
fsize() { stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0; }

# portable hashing (Linux coreutils vs macOS)
sha1_of() {
  if   command -v sha1sum >/dev/null; then sha1sum "$1" | awk '{print $1}';
  elif command -v shasum  >/dev/null; then shasum -a 1 "$1" | awk '{print $1}'; fi
}
md5_of() {
  if   command -v md5sum >/dev/null; then md5sum "$1" | awk '{print $1}';
  elif command -v md5    >/dev/null; then md5 -q "$1"; fi
}
# verify $ISO_NAME against known hashes (falls back to size if no hash tool)
verify_iso() {
  local s m; s="$(sha1_of "$ISO_NAME")"; m="$(md5_of "$ISO_NAME")"
  if [ -n "$s" ]; then [ "$s" = "$ISO_SHA1" ] || return 1; fi
  if [ -n "$m" ]; then [ "$m" = "$ISO_MD5" ]  || return 1; fi
  if [ -z "$s" ] && [ -z "$m" ]; then [ "$(fsize "$ISO_NAME")" = "$ISO_SIZE" ] || return 1; fi
  return 0
}

# cleanup (unmount + temp) on any exit
FS_MNT=""; ISO_MNT=""; TMP=""
cleanup() {
  [ -n "$FS_MNT"  ] && umount "$FS_MNT"  2>/dev/null || true
  [ -n "$ISO_MNT" ] && umount "$ISO_MNT" 2>/dev/null || true
  [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null || true
}
trap cleanup EXIT

# --- preflight -----------------------------------------------------------------
[ -f "$RECOVERY/patch_flasher.py" ]  || die "recovery/patch_flasher.py not found next to this script."
[ -f "$RECOVERY/patch_identity.py" ] || die "recovery/patch_identity.py not found next to this script."
command -v python3 >/dev/null || die "python3 is required."

if   command -v curl >/dev/null; then DL() { curl -fL --retry 3 -o "$1" "$2"; }
elif command -v wget >/dev/null; then DL() { wget -O "$1" "$2"; }
else die "need curl or wget to download the ISO."; fi

# pick a 7z with the Ext/Iso handlers (7z or 7zz — NOT the reduced 7za/7zr)
SEVENZ=""
for b in 7z 7zz; do command -v "$b" >/dev/null && { SEVENZ="$b"; break; }; done

# --- 1. get the vendor ISO: reuse if present & valid, else download; verify hash ---
if [ -f "$ISO_NAME" ]; then
  msg "ISO already present ($ISO_NAME) — verifying hash (no download)…"
  verify_iso || die "existing $ISO_NAME failed integrity check (want sha1 $ISO_SHA1 / md5 $ISO_MD5). Delete it and re-run to re-download."
  msg "ISO verified. Skipping download."
else
  msg "Downloading vendor ISO (~253 MiB)…"
  DL "$ISO_NAME" "$ISO_URL"
  verify_iso || die "downloaded ISO failed integrity check (want sha1 $ISO_SHA1 / md5 $ISO_MD5). Delete '$ISO_NAME' and retry."
  msg "ISO verified (sha1 $ISO_SHA1)."
fi

# --- 2. extract the payload (prefer 7z; fall back to loop-mount on Linux) -------
TMP="$(mktemp -d)"
SRC=""
if [ -n "$SEVENZ" ]; then
  msg "Extracting with $SEVENZ (no mount needed)…"
  "$SEVENZ" x -y -o"$TMP" "$ISO_NAME" "$SAVE_FS" >/dev/null \
    || die "$SEVENZ could not read $SAVE_FS from the ISO."
  [ -f "$TMP/$SAVE_FS" ] || die "$SAVE_FS not found in the ISO (wrong/updated ISO?)."
  "$SEVENZ" x -y -o"$TMP/x" "$TMP/$SAVE_FS" "$PAYLOAD" >/dev/null \
    || die "$SEVENZ could not extract $PAYLOAD from $SAVE_FS."
  SRC="$TMP/x/$PAYLOAD"
elif [ "$(uname -s)" = "Linux" ]; then
  [ "$(id -u)" = 0 ] || die "no 7z found and not root. Install 7z (apt install p7zip-full / brew install sevenzip) or run as root for loop-mount."
  ISO_MNT="$TMP/iso"; FS_MNT="$TMP/fs"; mkdir -p "$ISO_MNT" "$FS_MNT"
  msg "Extracting via loop-mount…"
  mount -o loop,ro "$ISO_NAME" "$ISO_MNT" 2>/dev/null \
    || mount -t iso9660 -o loop,ro "$ISO_NAME" "$ISO_MNT" || die "could not loop-mount the ISO."
  [ -f "$ISO_MNT/$SAVE_FS" ] || die "$SAVE_FS not found in the ISO (wrong/updated ISO?)."
  mount -o loop,ro "$ISO_MNT/$SAVE_FS" "$FS_MNT" 2>/dev/null \
    || mount -t ext4 -o loop,ro "$ISO_MNT/$SAVE_FS" "$FS_MNT" || die "could not loop-mount $SAVE_FS (ext4 support missing?)."
  SRC="$FS_MNT/$PAYLOAD"
else
  die "no 7z found. Install it:  brew install sevenzip   (macOS)  /  apt install p7zip-full  (Linux)."
fi

[ -f "$SRC/$TOOL" ]  || die "$TOOL not found in the extracted payload."
[ -d "$SRC/REGBIN" ] || die "REGBIN/ not found in the extracted payload."

# --- 3. copy the payload into the working folder -------------------------------
msg "Assembling $OUT …"
mkdir -p "$OUT"
rm -rf "$OUT/$TOOL" "$OUT/$TOOL.patched" "$OUT/REGBIN" "$OUT/patch_identity.py"
cp -a "$SRC/$TOOL"  "$OUT/"
cp -a "$SRC/REGBIN" "$OUT/"
chmod +x "$OUT/$TOOL"

# release mounts (if any) now that copying is done
[ -n "$FS_MNT"  ] && { umount "$FS_MNT";  FS_MNT=""; }
[ -n "$ISO_MNT" ] && { umount "$ISO_MNT"; ISO_MNT=""; }

# --- 4. build the Patch-A recovery flasher (hash-verified) ---------------------
msg "Building the Patch-A recovery flasher (hash-verified)…"
python3 "$RECOVERY/patch_flasher.py" "$OUT/$TOOL" "$OUT/$TOOL.patched" \
  || die "patch_flasher.py did not produce a verified patched binary (see output above)."

# --- 5. drop in the identity restorer ------------------------------------------
cp -a "$RECOVERY/patch_identity.py" "$OUT/"
chmod +x "$OUT/patch_identity.py"

# --- 6. make the output usable by the invoking user ----------------------------
# extracted dirs (e.g. REGBIN) carry the image's restrictive 0700 mode; ensure the
# owner can traverse/read them, and if we ran under sudo hand everything back to
# the real user (otherwise the files stay root-owned and inaccessible).
chmod -R u+rwX "$OUT"
if [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ]; then
  uid="${SUDO_UID:-$(id -u "$SUDO_USER" 2>/dev/null || echo)}"
  gid="${SUDO_GID:-$(id -g "$SUDO_USER" 2>/dev/null || echo)}"
  if [ -n "$uid" ] && [ -n "$gid" ]; then
    msg "Restoring ownership to $SUDO_USER ($uid:$gid)…"
    chown -R "$uid:$gid" "$OUT" 2>/dev/null || true
    [ -f "$ISO_NAME" ] && chown "$uid:$gid" "$ISO_NAME" 2>/dev/null || true
  fi
fi

# --- done ----------------------------------------------------------------------
msg "Done. Contents of $OUT:"
ls -la "$OUT"
cat <<EOF

Next steps (see TS-SSD230S-Firmware-Update.md) — run these on the LINUX host with the drive:
  cd $OUT
  ./$TOOL /dev/sdX keepsn_1            # normal firmware update
  ./$TOOL.patched /dev/sdX initial     # recover a drive stuck in ROM mode (Part J)
  ./patch_identity.py && ./$TOOL /dev/sdX initial   # restore serial+WWN (Part K)

You can delete "$ISO_NAME" now if you don't need it again.
EOF
