#!/usr/bin/env python3
"""
patch_flasher.py

Reproduce the "Patch A" recovery flasher from the ORIGINAL vendor tool and verify
it byte-for-byte against the known-good patched build — so nobody has to
redistribute Silicon Motion's proprietary binary.

WHAT IT DOES
  1. Hashes your original  SM2258TLC_3D_LinuxTool_64  (SHA-256 + MD5) and checks
     it against the known vendor build.
  2. Applies Patch A at file offset 0x4d8c:
         55 48 89 e5   (push rbp; mov rbp,rsp)
       → 31 c0 c3 90   (xor eax,eax; ret; nop)
     This makes CheckFlashID() return "pass", so the tool gets past its ROM-mode
     flash-ID gate (see TS-SSD230S-Firmware-Update.md, Part J).
  3. Hashes the patched output and confirms it matches the reference patched
     binary  SM2258TLC_3D_LinuxTool_64.patched  documented here.

WHERE TO GET THE ORIGINAL
  Extract  SM2258TLC_3D_LinuxTool_64  from the official Transcend firmware ISO
  (Parts A–C of the guide). The ISO is archived here:
  https://github.com/leopard-archives/Transcend-SATA-SSD-230S-4TB/releases/tag/22Z4X4IA

USAGE
  ./patch_flasher.py [path/to/SM2258TLC_3D_LinuxTool_64] [output_path]
  # defaults: input  ./SM2258TLC_3D_LinuxTool_64
  #           output ./SM2258TLC_3D_LinuxTool_64.patched

EXIT CODES
  0 = patched output matches the reference hash
  1 = produced a patched file, but it does NOT match the reference
  2 = refused to patch (wrong/altered input, missing file, etc.)
"""

import sys, os, hashlib, stat

# --- Patch A ---------------------------------------------------------------
OFFSET     = 0x4d8c
ORIG_BYTES = bytes.fromhex("554889e5")   # push rbp; mov rbp,rsp
NEW_BYTES  = bytes.fromhex("31c0c390")   # xor eax,eax; ret; nop
SIZE       = 2945112

# --- known-good hashes (this repo) -----------------------------------------
ORIG_SHA256    = "a89f11aed20a3020bbf874386efba371ffc93b307d2f5399603893c06bffdb63"
ORIG_MD5       = "60d128f167146e470faecc168e4ffd82"
PATCHED_SHA256 = "8c08bedce733f67c088ea14a7b4b3b71838d0e30c8aa24056f512fd5f742b526"
PATCHED_MD5    = "6b82a8d7efee763e0ec6590906bb4b21"

VENDOR_ISO = ("https://github.com/leopard-archives/"
              "Transcend-SATA-SSD-230S-4TB/releases/tag/22Z4X4IA")


def hashes(b):
    return hashlib.sha256(b).hexdigest(), hashlib.md5(b).hexdigest()


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "SM2258TLC_3D_LinuxTool_64"
    out = sys.argv[2] if len(sys.argv) > 2 else src + ".patched"

    if not os.path.isfile(src):
        print("ERROR: '%s' not found." % src)
        print("Extract SM2258TLC_3D_LinuxTool_64 from the Transcend ISO:")
        print("  %s" % VENDOR_ISO)
        sys.exit(2)

    data = bytearray(open(src, "rb").read())
    o_sha, o_md5 = hashes(data)

    print("Input : %s" % os.path.abspath(src))
    print("  size   : %d bytes" % len(data))
    print("  sha256 : %s" % o_sha)
    print("  md5    : %s" % o_md5)

    # verify we're patching the exact vendor build
    strict = (o_sha == ORIG_SHA256 and o_md5 == ORIG_MD5 and len(data) == SIZE)
    if strict:
        print("  -> matches the known vendor build. OK.")
    else:
        print("  -> WARNING: does NOT match the known vendor build.")
        print("     expected sha256 %s" % ORIG_SHA256)
        print("     Re-extract a clean copy from the ISO: %s" % VENDOR_ISO)

    # sanity-check the bytes we're about to change
    have = bytes(data[OFFSET:OFFSET + 4])
    if have != ORIG_BYTES:
        print("REFUSING: bytes at 0x%04x are %s, expected %s (already patched or "
              "wrong file)." % (OFFSET, have.hex(), ORIG_BYTES.hex()))
        sys.exit(2)
    if not strict:
        ans = input("Bytes at the patch site are correct but the file is an "
                    "unknown build.\nPatch anyway (output will not match the "
                    "reference hash)? [y/N]: ").strip().lower()
        if ans != "y":
            sys.exit(2)

    # apply Patch A
    data[OFFSET:OFFSET + 4] = NEW_BYTES
    open(out, "wb").write(data)
    os.chmod(out, os.stat(out).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    p_sha, p_md5 = hashes(data)
    print("\nOutput: %s" % os.path.abspath(out))
    print("  sha256 : %s" % p_sha)
    print("  md5    : %s" % p_md5)

    if p_sha == PATCHED_SHA256 and p_md5 == PATCHED_MD5:
        print("  -> MATCHES the reference patched binary "
              "(SM2258TLC_3D_LinuxTool_64.patched). ✔")
        print("\nUse it for ROM-mode recovery (Part J):")
        print("  ./%s /dev/sdX initial" % os.path.basename(out))
        sys.exit(0)
    else:
        print("  -> does NOT match the reference patched binary.")
        print("     reference sha256 %s" % PATCHED_SHA256)
        if strict:
            print("     (Unexpected — input matched but output didn't. Do not use.)")
        else:
            print("     (Expected: your input was a different build.)")
        sys.exit(1)


if __name__ == "__main__":
    main()
