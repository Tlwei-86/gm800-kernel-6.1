#!/usr/bin/env python3
"""Extract GM800 exact Silead/GSLX6801 touchscreen firmware from factory 4.19 recovery.img.

Outputs:
  1. firmware/silead/gslx680-gm800.fw  (standard mainline silead.c 8-byte LE <offset, val> binary)
  2. firmware/silead/gslx6801_gm800_fw.h (C header with GSLX680_FW[] + gsl_config_data_id[512])
"""
import hashlib
import os
import struct
import sys

# Verified via factory 4.19.219 kallsyms (delta=0):
#   0x0134aaf0 r gsl_ts_id           = "gslX6801"
#   0x0134ab30 r GSLX680_FW          (41976 bytes = 5247 entries of {u32 offset; u32 val;})
#   0x01c93c38 d gsl_config_data_id  (2048 bytes  = 512 u32 entries)
FW_OFFSET = 0x0134AB30
FW_SIZE = 41976
CFG_OFFSET = 0x01C93C38
CFG_SIZE = 2048


def extract(recovery_img_path, out_dir):
    data = open(recovery_img_path, "rb").read()
    assert data[:8] == b"ANDROID!", "Not an Android boot/recovery image"
    ks, _, _, _, _, _, _, ps, hv, _ = struct.unpack_from("<10I", data, 8)
    hsz, = struct.unpack_from("<I", data, 1644)
    pg = lambda n: (n + ps - 1) // ps * ps
    k_off = pg(hsz) or ps
    kernel = data[k_off : k_off + ks]

    # Sanity check banner and gslX6801 marker
    assert b"Linux version 4.19.219" in kernel, "Unexpected factory kernel version"
    assert kernel[0x0134AAF0 : 0x0134AAF8] == b"gslX6801", "gslX6801 symbol offset mismatch"

    fw_raw = kernel[FW_OFFSET : FW_OFFSET + FW_SIZE]
    cfg_raw = kernel[CFG_OFFSET : CFG_OFFSET + CFG_SIZE]

    entries = [struct.unpack_from("<II", fw_raw, i) for i in range(0, len(fw_raw), 8)]
    assert entries[0] == (0xF0, 0x02), f"Unexpected first FW entry: {entries[0]}"
    assert all(off <= 0xFF for off, _ in entries), "Invalid register offset > 0xFF in FW"

    cfg_u32 = struct.unpack("<512I", cfg_raw)

    os.makedirs(out_dir, exist_ok=True)
    fw_path = os.path.join(out_dir, "gslx680-gm800.fw")
    with open(fw_path, "wb") as f:
        f.write(fw_raw)

    hdr_path = os.path.join(out_dir, "gslx6801_gm800_fw.h")
    with open(hdr_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("/* Auto-extracted from GM800 factory 4.19.219 kernel (gslX6801) */\n")
        f.write("#ifndef _GSLX6801_GM800_FW_H_\n#define _GSLX6801_GM800_FW_H_\n\n")
        f.write(f"/* {len(entries)} entries, sha256={hashlib.sha256(fw_raw).hexdigest()} */\n")
        f.write("static const struct fw_data GSLX680_FW[] = {\n")
        for off, val in entries:
            f.write(f"\t{{ 0x{off:02x}, 0x{val:08x} }},\n")
        f.write("};\n\n")
        f.write("static unsigned int gsl_config_data_id[512] = {\n")
        for i in range(0, 512, 8):
            row = ", ".join(f"0x{x:08x}" for x in cfg_u32[i : i + 8])
            f.write(f"\t{row},\n")
        f.write("};\n\n#endif /* _GSLX6801_GM800_FW_H_ */\n")

    print(f"Extracted {fw_path} ({len(fw_raw)} bytes, {len(entries)} entries, sha256={hashlib.sha256(fw_raw).hexdigest()[:16]}...)")
    print(f"Extracted {hdr_path} (512 config words, first=0x{cfg_u32[0]:08x})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <factory_recovery.img> <out_silead_dir>")
        sys.exit(1)
    extract(sys.argv[1], sys.argv[2])
