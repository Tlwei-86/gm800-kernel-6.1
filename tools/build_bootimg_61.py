#!/usr/bin/env python3
"""GM800 Android Boot Image v2 + Rockchip RSCE (second) synchronized builder.

Key improvement over legacy build_bootimg.py:
  - Replaces kernel Image, ramdisk (initrd.cpio.gz), v2 DTB, AND updates
    `rk-kernel.dtb` inside the `second` (Rockchip RSCE resource) image!
  - Re-packs RSCE block layout dynamically (handles arbitrarily larger 6.1 DTB),
    recomputes full 20-byte SHA-1 hashes for all RSCE ENTR entries (offset 0xE0),
    and recomputes the Android Boot v2 header SHA-1 `id` (offset 576).

Self-test:
  python build_bootimg_61.py selftest <factory_recovery.img>
  Must reproduce the original factory RSCE and boot image byte-for-byte!
"""
import argparse
import hashlib
import os
import struct
import sys


BLOCK = 512


def align_up(n, a):
    return (n + a - 1) // a * a


class RsceImage:
    """Parser and byte-exact rebuilder for Rockchip RSCE resource images."""

    def __init__(self, header, entries):
        self.header = header  # 512-byte header block
        self.entries = entries  # list of (raw_entry_512b, name, payload_bytes)

    @classmethod
    def parse(cls, data):
        assert data[:4] == b"RSCE", f"Not an RSCE image (magic={data[:4]!r})"
        magic, rver, cver, hblks, tbl_off, eblks, ecnt = struct.unpack_from("<4sHHBBHI", data, 0)
        assert hblks == 1 and tbl_off == 1 and eblks == 1, (
            f"Unsupported RSCE geometry: hblks={hblks} tbl_off={tbl_off} eblks={eblks}"
        )
        header = bytes(data[:BLOCK])
        entries = []
        for i in range(ecnt):
            eo = (tbl_off + i * eblks) * BLOCK
            raw_e = bytearray(data[eo : eo + BLOCK])
            assert raw_e[:4] == b"ENTR", f"Bad RSCE entry[{i}] magic"
            name = bytes(raw_e[4:260]).split(b"\x00", 1)[0].decode("ascii", "replace")
            blk_off, dsz = struct.unpack_from("<II", raw_e, 260)
            payload = bytes(data[blk_off * BLOCK : blk_off * BLOCK + dsz])
            entries.append((raw_e, name, payload))
        return cls(header, entries)

    def replace_entry(self, target_name, new_payload):
        replaced = False
        for idx, (raw_e, name, _) in enumerate(self.entries):
            if name == target_name:
                self.entries[idx] = (bytearray(raw_e), name, bytes(new_payload))
                replaced = True
                break
        if not replaced:
            raise KeyError(f"RSCE entry {target_name!r} not found")

    def build(self):
        ecnt = len(self.entries)
        # Layout: 1 header block + ecnt entry blocks, then payloads block-aligned
        cur_blk = 1 + ecnt
        out_entries = []
        out_payloads = []
        for raw_e, name, payload in self.entries:
            e = bytearray(raw_e)
            dsz = len(payload)
            sha1 = hashlib.sha1(payload).digest()
            # Update 20-byte SHA-1 at +0xE0..+0xF4, blk_off at +0x104, data_size at +0x108
            e[0xE0:0xF4] = sha1
            struct.pack_into("<II", e, 260, cur_blk, dsz)
            out_entries.append(bytes(e))
            padded_len = align_up(dsz, BLOCK)
            out_payloads.append(payload + b"\x00" * (padded_len - dsz))
            cur_blk += padded_len // BLOCK

        hdr = bytearray(self.header)
        struct.pack_into("<I", hdr, 12, ecnt)
        return bytes(hdr) + b"".join(out_entries) + b"".join(out_payloads)


class BootImgV2:
    @classmethod
    def parse(cls, path):
        d = open(path, "rb").read()
        assert d[:8] == b"ANDROID!", f"{path}: not ANDROID! magic"
        self = cls()
        self.data, self.path = d, path
        (
            self.ks,
            self.ka,
            self.rs,
            self.ra,
            self.ss,
            self.sa,
            self.ta,
            self.ps,
            self.hv,
            self.ov,
        ) = struct.unpack_from("<10I", d, 8)
        self.rds, = struct.unpack_from("<I", d, 1632)
        self.rdo, = struct.unpack_from("<Q", d, 1636)
        self.hsz, = struct.unpack_from("<I", d, 1644)
        self.ds, self.da = struct.unpack_from("<II", d, 1648)
        assert self.ps in (2048, 4096) and self.hv == 2, f"Unsupported boot hdr v{self.hv} ps={self.ps}"
        pg = lambda n: align_up(n, self.ps)
        off = pg(self.hsz) or self.ps
        self.o_kernel = off; off += pg(self.ks)
        self.o_ramdisk = off; off += pg(self.rs)
        self.o_second = off; off += pg(self.ss)
        self.o_dtbo = off; off += pg(self.rds)
        self.o_dtb = off
        self.image_len = self.o_dtb + pg(self.ds)
        self.kernel = d[self.o_kernel : self.o_kernel + self.ks]
        self.ramdisk = d[self.o_ramdisk : self.o_ramdisk + self.rs]
        self.second = d[self.o_second : self.o_second + self.ss]
        self.dtbo = d[self.o_dtbo : self.o_dtbo + self.rds]
        self.dtb = d[self.o_dtb : self.o_dtb + self.ds]
        self.cmdline = d[64:576].rstrip(b"\x00").decode("ascii", "replace")
        return self

    @staticmethod
    def compute_id(kernel, ramdisk, second, dtbo, dtb):
        h = hashlib.sha1()
        for blob in (kernel, ramdisk, second, dtbo, dtb):
            h.update(blob)
            h.update(struct.pack("<I", len(blob)))
        return h.digest()

    def build(self, kernel, ramdisk, dtb, cmdline_str, out_path, sync_rsce=True, drop_dtbo=False):
        ps = self.ps
        pg = lambda n: align_up(n, ps)

        if sync_rsce and self.second[:4] == b"RSCE":
            rsce = RsceImage.parse(self.second)
            rsce.replace_entry("rk-kernel.dtb", dtb)
            second = rsce.build()
        else:
            second = self.second

        dtbo = b"" if drop_dtbo else self.dtbo
        cmdline_b = cmdline_str.encode("ascii")
        assert len(cmdline_b) < 512, f"cmdline too long ({len(cmdline_b)} >= 512)"

        # Verify ARM64 Image header sanity (warn if EFI stub present)
        if len(kernel) >= 64:
            c0, c1, toff, isz, flg, _, _, _, mag = struct.unpack("<IIQQQQQQ4s", kernel[:60])
            if mag != b"ARMd":
                print(f"WARN: kernel magic is {mag!r}, expected b'ARMd'", file=sys.stderr)
            if (c0 & 0xFFFF) == 0x5A4D:
                print(
                    "WARN: kernel has MZ/EFI stub header! Ensure CONFIG_EFI is disabled for Rockchip U-Boot.",
                    file=sys.stderr,
                )

        hdr_size_aligned = pg(self.hsz) or ps
        o_dtbo = hdr_size_aligned + pg(len(kernel)) + pg(len(ramdisk)) + pg(len(second))

        parts = [
            self.data[:hdr_size_aligned],
            kernel, b"\x00" * (pg(len(kernel)) - len(kernel)),
            ramdisk, b"\x00" * (pg(len(ramdisk)) - len(ramdisk)),
            second, b"\x00" * (pg(len(second)) - len(second)),
            dtbo, b"\x00" * (pg(len(dtbo)) - len(dtbo)),
            dtb, b"\x00" * (pg(len(dtb)) - len(dtb)),
        ]
        img = bytearray(b"".join(parts))

        # Patch sizes & offsets in Android Boot v2 header
        struct.pack_into("<I", img, 8, len(kernel))
        struct.pack_into("<I", img, 16, len(ramdisk))
        struct.pack_into("<I", img, 24, len(second))
        struct.pack_into("<I", img, 1632, len(dtbo))
        struct.pack_into("<Q", img, 1636, o_dtbo if len(dtbo) else 0)
        struct.pack_into("<I", img, 1648, len(dtb))

        # Patch cmdline (offset 64, 512 bytes)
        img[64:576] = cmdline_b.ljust(512, b"\x00")

        # Patch SHA-1 id (offset 576: 20 bytes + 12 zero bytes)
        img[576:608] = b"\x00" * 32
        img[576:596] = self.compute_id(kernel, ramdisk, second, dtbo, dtb)

        with open(out_path, "wb") as f:
            f.write(img)

        total = len(img)
        md5_all = hashlib.md5(img).hexdigest()
        blk4k = total // 4096
        rem = total % 4096
        md5_main = hashlib.md5(img[: blk4k * 4096]).hexdigest()
        md5_tail = hashlib.md5(img[blk4k * 4096 :]).hexdigest() if rem else ""
        print(f"Built: {out_path} ({total} bytes = {blk4k}*4096 + {rem})")
        print(f"  kernel={len(kernel)} ramdisk={len(ramdisk)} second(RSCE)={len(second)} dtbo={len(dtbo)} dtb={len(dtb)}")
        print(f"  md5_full={md5_all}")
        print(f"  verify_dd: count={blk4k} main_md5={md5_main}" + (f" tail({rem}B)_md5={md5_tail}" if rem else ""))
        return bytes(img)


def cmd_selftest(template_path):
    b = BootImgV2.parse(template_path)
    rsce = RsceImage.parse(b.second)
    rebuilt_rsce = rsce.build()
    # Note: factory second may have trailing alignment padding after last entry; compare up to rebuilt length
    if rebuilt_rsce == b.second[: len(rebuilt_rsce)] and not b.second[len(rebuilt_rsce) :].strip(b"\x00"):
        print(f"RSCE rebuild selftest: PASS (exact match {len(rebuilt_rsce)}B, stock pad {len(b.second)-len(rebuilt_rsce)}B)")
    else:
        diff_i = next(i for i in range(min(len(rebuilt_rsce), len(b.second))) if rebuilt_rsce[i] != b.second[i])
        print(f"RSCE rebuild selftest: FAIL at offset 0x{diff_i:x}")
        sys.exit(1)

    stored_id = b.data[576:596]
    calc_id = b.compute_id(b.kernel, b.ramdisk, b.second, b.dtbo, b.dtb)
    assert stored_id == calc_id, "Stock boot id SHA-1 mismatch!"
    print(f"Boot v2 SHA-1 id selftest: PASS ({calc_id.hex()})")


def main():
    ap = argparse.ArgumentParser(description="Build GM800 RK3566 6.1 Android Boot v2 image with synced RSCE DTB")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_t = sub.add_parser("selftest", help="Verify RSCE and boot v2 header math against factory recovery.img")
    sp_t.add_argument("template", help="Factory recovery.img (or gm800_linux_boot.img)")

    sp_b = sub.add_parser("build", help="Pack new kernel + DTB + ramdisk into p8 boot image")
    sp_b.add_argument("--template", required=True, help="Template boot/recovery image (e.g. release/gm800_linux_boot.img)")
    sp_b.add_argument("--kernel", required=True, help="New Linux 6.1 ARM64 Image")
    sp_b.add_argument("--dtb", required=True, help="New rk3566-gm800.dtb (written to BOTH v2 dtb and RSCE rk-kernel.dtb)")
    sp_b.add_argument("--ramdisk", help="Optional initrd.cpio.gz (defaults to template's ramdisk)")
    sp_b.add_argument("--cmdline", help="Full custom cmdline (overrides template cmdline)")
    sp_b.add_argument("--extra-cmdline", default="", help="Extra args appended to template cmdline")
    sp_b.add_argument("--drop-dtbo", action="store_true", help="Drop Android recovery_dtbo (recommended for pure Linux 6.1)")
    sp_b.add_argument("-o", "--output", required=True, help="Output boot image path")

    args = ap.parse_args()
    if args.cmd == "selftest":
        cmd_selftest(args.template)
    elif args.cmd == "build":
        tpl = BootImgV2.parse(args.template)
        kernel = open(args.kernel, "rb").read()
        dtb = open(args.dtb, "rb").read()
        assert dtb[:4] == b"\xd0\x0d\xfe\xed", "Invalid DTB magic"
        ramdisk = open(args.ramdisk, "rb").read() if args.ramdisk else tpl.ramdisk
        if args.cmdline:
            cmdline = args.cmdline
        else:
            cmdline = tpl.cmdline + args.extra_cmdline
        tpl.build(kernel, ramdisk, dtb, cmdline, args.output, sync_rsce=True, drop_dtbo=args.drop_dtbo)


if __name__ == "__main__":
    main()
