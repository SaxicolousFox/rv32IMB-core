#!/usr/bin/env python3
"""
Build the A12 SoC memory image: C + crt0 -> ELF -> $readmemh .mem.

The .mem is what both Verilator and Vivado load, so the simulated program and
the programmed one cannot diverge -- a class of bug that is otherwise very hard
to see, because the symptom is "it works in simulation".

Placement is taken from the ELF's PROGRAM HEADERS, not from `objcopy -O binary`.
objcopy flattens from the lowest LMA and would silently produce a correct-looking
image if a section were linked somewhere unexpected; walking p_paddr means an
address outside the array is an error here rather than an alias in the RAM.
"""
import argparse, os, struct, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SW   = os.path.join(ROOT, "sw", "soc")

CC   = "riscv-none-elf-gcc"
OBJ  = "riscv-none-elf-objcopy"


def build_elf(out_elf, delay_cycles, opt, extra_defs):
    cmd = [CC, "-march=rv32i_zicsr", "-mabi=ilp32", opt, "-Wall", "-Wextra",
           "-ffreestanding", "-nostdlib", "-nostartfiles",
           "-DDELAY_CYCLES=%uu" % delay_cycles]
    cmd += ["-D" + d for d in extra_defs]
    cmd += ["-T", os.path.join(SW, "link.ld"), "-o", out_elf,
            os.path.join(SW, "crt0.S"), os.path.join(SW, "hello.c")]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        sys.stderr.write(r.stdout.decode("utf-8", "replace"))
        raise SystemExit("compile failed")
    return out_elf


def load_segments(elf):
    """[(paddr, bytes)] for every PT_LOAD segment, read straight out of the ELF."""
    with open(elf, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise SystemExit("not a 32-bit ELF: %s" % elf)
    e_phoff, = struct.unpack_from("<I", data, 0x1C)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x2A)
    segs = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz = \
            struct.unpack_from("<IIIIII", data, off)
        if p_type != 1 or p_filesz == 0:      # PT_LOAD only
            continue
        segs.append((p_paddr, data[p_offset:p_offset + p_filesz]))
    if not segs:
        raise SystemExit("no PT_LOAD segments in %s" % elf)
    return segs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay-cycles", type=int, default=75_000_000)
    ap.add_argument("--opt", default="-O2")
    ap.add_argument("-D", dest="defs", action="append", default=[])
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0x8000_0000)
    ap.add_argument("--words", type=int, default=32768)
    ap.add_argument("--out", required=True, help="path of the .mem to write")
    ap.add_argument("--elf", default=None)
    a = ap.parse_args()

    outdir = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(outdir, exist_ok=True)
    elf = a.elf or os.path.join(outdir, "soc_image.elf")
    build_elf(elf, a.delay_cycles, a.opt, a.defs)

    mem = bytearray(a.words * 4)
    used = 0
    for paddr, blob in load_segments(elf):
        off = paddr - a.base
        if off < 0 or off + len(blob) > len(mem):
            raise SystemExit(
                "segment at 0x%08X (%d bytes) does not fit in %d words at 0x%08X"
                % (paddr, len(blob), a.words, a.base))
        mem[off:off + len(blob)] = blob
        used = max(used, off + len(blob))

    # Trim to the last used word.  $readmemh leaves untouched addresses alone,
    # and rvntt_ram zeroes the whole array first, so writing 32768 lines of
    # zeroes would only make synthesis slower and the diff unreadable.
    nwords = (used + 3) // 4
    with open(a.out, "w") as f:
        for i in range(nwords):
            f.write("%08x\n" % struct.unpack_from("<I", mem, i * 4)[0])

    print("%s: %d words (%d bytes used of %d)"
          % (a.out, nwords, used, a.words * 4))
    print("elf: %s" % elf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
