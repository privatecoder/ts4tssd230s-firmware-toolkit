#!/usr/bin/env python3
"""
patch_identity.py

Restore the Serial Number and WWN into a Silicon Motion SM2259 ISP image
(REGBIN/2259/ISP2259.bin) so a mode-0 flash ("initial") brings the drive up
with its real factory identity instead of the generic default.

  - Backs up ISP2259.bin (keeps a pristine .orig, plus a timestamped copy).
  - Prompts for Serial and WWN (values are on the drive's own sticker).
  - Writes the three identity fields and verifies them by decoding back.

Verified for Transcend TS4TSSD230S / SM2259AB, ISP2259.bin = 789504 bytes.
The offsets below are exactly the fields the vendor tool's ModifyISPFile()
writes, so this is mechanically identical to the tool's own keepsn flow.

  0x0000051C  Serial copy #1 (config block)     20 bytes, ATA string (LE words)
  0x00020834  Serial copy #2 (IDENTIFY mirror)  20 bytes, ATA string (LE words)
  0x000208F8  WWN  (IDENTIFY words 108-111)      8 bytes, LE per 16-bit word

USAGE:
  ./patch_identity.py                      # uses ./REGBIN/2259/ISP2259.bin
  ./patch_identity.py /path/to/ISP2259.bin
"""

import os
import sys
import struct
import shutil
import time
import hashlib

# --- field layout (see module docstring) ---------------------------------
OFF_SERIAL_1 = 0x0000051C
OFF_SERIAL_2 = 0x00020834
OFF_WWN      = 0x000208F8
SERIAL_LEN   = 20            # ATA serial number field width (bytes)
WWN_LEN      = 8
EXPECTED_SIZE = 789504       # ISP2259.bin size for this model

# Verify the ISP is the known vendor image WITHOUT rejecting a file we've already
# patched: hash the image with the three identity fields (serial x2 + WWN) zeroed.
# This is stable across the pristine ISP and any serial/WWN we write, so re-running
# to change the serial still validates. (A plain full checksum would falsely fail.)
TEMPLATE_MASKED_SHA256 = "da08375b3f31f4603757618495476257d8ca35dd563f3afb2df70385bfe98615"
#   full pristine ISP2259.bin (22Z4X4IA) SHA256, for reference only:
#   552b57afd7ff158f89629e258f5c669d879be623e555d5b33fc4657bd25f8508


def masked_sha256(data: bytes) -> str:
    """SHA-256 of the image with the identity fields zeroed (version-stable)."""
    d = bytearray(data)
    for off, n in ((OFF_SERIAL_1, SERIAL_LEN), (OFF_SERIAL_2, SERIAL_LEN), (OFF_WWN, WWN_LEN)):
        for i in range(off, off + n):
            d[i] = 0
    return hashlib.sha256(bytes(d)).hexdigest()


def encode_serial(serial: str) -> bytes:
    """20-byte ATA serial: left-justified, space-padded, little-endian per word."""
    s = serial.ljust(SERIAL_LEN)[:SERIAL_LEN]
    out = bytearray()
    for i in range(0, SERIAL_LEN, 2):
        hi, lo = ord(s[i]), ord(s[i + 1])   # ATA word = (hi<<8)|lo
        out += bytes([lo, hi])              # stored little-endian in the image
    return bytes(out)


def decode_serial(b: bytes) -> str:
    out = ''
    for i in range(0, len(b), 2):
        out += chr(b[i + 1]) + chr(b[i])
    return out.rstrip()


def encode_wwn(wwn64: int) -> bytes:
    """8-byte WWN as IDENTIFY words 108-111, little-endian per word."""
    words = [(wwn64 >> 48) & 0xFFFF, (wwn64 >> 32) & 0xFFFF,
             (wwn64 >> 16) & 0xFFFF, wwn64 & 0xFFFF]
    return b''.join(struct.pack('<H', w) for w in words)


def decode_wwn(b: bytes) -> str:
    return '%04x%04x%04x%04x' % struct.unpack('<4H', b)


def ask_serial() -> str:
    while True:
        raw = input("Serial Number (from sticker, e.g. SERIAL0002): ").strip()
        s = ''.join(raw.split()).replace('-', '')      # drop spaces/dashes
        if not s:
            print("  ! empty, try again")
            continue
        if not all(32 <= ord(c) < 127 for c in s):
            print("  ! serial must be printable ASCII")
            continue
        if len(s) > SERIAL_LEN:
            print("  ! max %d characters (got %d)" % (SERIAL_LEN, len(s)))
            continue
        return s


def ask_wwn() -> int:
    while True:
        raw = input("WWN (from sticker, e.g. 57C3548000000002): ").strip()
        h = raw.lower().replace('0x', '').replace(':', '').replace(' ', '').replace('-', '')
        if len(h) != 16 or any(c not in '0123456789abcdef' for c in h):
            print("  ! WWN must be 16 hex digits (64-bit)")
            continue
        val = int(h, 16)
        naa = (val >> 60) & 0xF
        if naa != 0x5:
            print("  ! warning: NAA nibble is %X, expected 5 for a standard SATA WWN" % naa)
            if input("    continue anyway? [y/N]: ").strip().lower() != 'y':
                continue
        oui = (val >> 36) & 0xFFFFFF
        if oui != 0x7c3548:
            print("  ! note: OUI is %06X (Transcend/SMI drives use 7C3548)." % oui)
            print("          Make sure this WWN is unique and really belongs to THIS drive.")
        return val


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join('REGBIN', '2259', 'ISP2259.bin')
    path = os.path.abspath(path)

    if not os.path.isfile(path):
        sys.exit("ERROR: %s not found. Run from the tool folder or pass the path." % path)

    blob = open(path, "rb").read()
    size = len(blob)

    # 1) quick size guard
    if size != EXPECTED_SIZE:
        print("WARNING: %s is %d bytes (expected %d). Offsets may not match this image."
              % (path, size, EXPECTED_SIZE))
        if input("Continue anyway? [y/N]: ").strip().lower() != 'y':
            sys.exit("aborted")

    # 2) verify it's the known vendor ISP (identity fields excluded, so an
    #    already-patched file still validates)
    if size == EXPECTED_SIZE:
        got = masked_sha256(blob)
        if got == TEMPLATE_MASKED_SHA256:
            print("ISP image  : verified vendor ISP2259.bin (22Z4X4IA).")
        else:
            print("WARNING: this is not the verified vendor ISP2259.bin.")
            print("  identity-masked sha256: %s" % got)
            print("  expected              : %s" % TEMPLATE_MASKED_SHA256)
            print("  The serial/WWN offsets may not apply — patching could corrupt it.")
            if input("Continue anyway? [y/N]: ").strip().lower() != 'y':
                sys.exit("aborted")

    print("Target ISP : %s" % path)
    serial = ask_serial()
    wwn = ask_wwn()

    ser_bytes = encode_serial(serial)
    wwn_bytes = encode_wwn(wwn)

    print("\nAbout to write:")
    print("  Serial : %-20s  @0x%X and @0x%X   (%s)" %
          (serial, OFF_SERIAL_1, OFF_SERIAL_2, ser_bytes.hex()))
    print("  WWN    : %016X  @0x%X            (%s)" %
          (wwn, OFF_WWN, wwn_bytes.hex()))
    if input("\nProceed? [y/N]: ").strip().lower() != 'y':
        sys.exit("aborted")

    # --- backup: keep a pristine .orig, plus a timestamped copy every run ---
    orig = path + '.orig'
    if not os.path.exists(orig):
        shutil.copy2(path, orig)
        print("Backup (pristine): %s" % orig)
    stamp = path + '.bak-' + time.strftime('%Y%m%d-%H%M%S')
    shutil.copy2(path, stamp)
    print("Backup (this run): %s" % stamp)

    # --- patch ---
    with open(path, 'r+b') as f:
        f.seek(OFF_SERIAL_1); f.write(ser_bytes)
        f.seek(OFF_SERIAL_2); f.write(ser_bytes)
        f.seek(OFF_WWN);      f.write(wwn_bytes)

    # --- verify by reading back ---
    with open(path, 'rb') as f:
        d = f.read()
    ok = True
    s1 = decode_serial(d[OFF_SERIAL_1:OFF_SERIAL_1 + SERIAL_LEN])
    s2 = decode_serial(d[OFF_SERIAL_2:OFF_SERIAL_2 + SERIAL_LEN])
    w  = decode_wwn(d[OFF_WWN:OFF_WWN + 8]).upper()
    print("\nVerify:")
    print("  serial @0x%X -> %r %s" % (OFF_SERIAL_1, s1, 'OK' if s1 == serial else 'MISMATCH'))
    print("  serial @0x%X -> %r %s" % (OFF_SERIAL_2, s2, 'OK' if s2 == serial else 'MISMATCH'))
    print("  WWN    @0x%X -> %s %s" % (OFF_WWN, w, 'OK' if w == '%016X' % wwn else 'MISMATCH'))
    ok = (s1 == serial and s2 == serial and w == '%016X' % wwn)

    if not ok:
        sys.exit("\nERROR: verification failed. Restore from %s before flashing." % orig)

    print("\nDone. Patched %s" % path)
    print("Flash with:  ./SM2258TLC_3D_LinuxTool_64.patched /dev/sdX initial")
    print("Then check:  smartctl -a /dev/sdX | grep -iE 'Serial|WWN'")
    print("Revert with: cp '%s' '%s'" % (orig, path))


if __name__ == '__main__':
    main()
