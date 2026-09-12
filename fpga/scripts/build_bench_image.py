#!/usr/bin/env python3
"""
Build the benchmark image: Dhrystone + CoreMark -> ELF -> $readmemh .mem.

Dhrystone is compiled in place from toolchain/riscv-tests (port: sw/bench/
include/util.h); CoreMark from toolchain/coremark (port: sw/bench/coremark_port).
Both checkouts stay pristine.  -std=gnu89 is required for Dhrystone's K&R C,
and -Dmain= renames each benchmark's main so bench_main.c can call them.
"""
import argparse, os, struct, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_soc_image import load_segments

BENCH = os.path.join(ROOT, "sw", "bench")
SOC   = os.path.join(ROOT, "sw", "soc")
DHRY  = os.path.join(ROOT, "toolchain", "riscv-tests", "benchmarks", "dhrystone")
CM    = os.path.join(ROOT, "toolchain", "coremark")

CC = "riscv-none-elf-gcc"

# -march per build variant.  rv32imb is spelled out as Z-extensions because that
# is what GCC and riscv-config accept (misa.B is not asserted; see rvntt_csr.sv).
# Zkr/Zkt change no code generation but are named so isa_consistency's sources
# agree.  rv32imzb (B without Zicond) exists to attribute Zicond's effect.
ARCH_ALIASES = {
    "rv32i":    "rv32i",
    "rv32im":   "rv32im",
    "rv32imb":  "rv32im_zba_zbb_zbs_zbkb_zicond_zkr_zkt",
    "rv32imzb": "rv32im_zba_zbb_zbs_zbkb",
}


def arch_flags(arch):
    return ["-march=%s_zicsr" % ARCH_ALIASES.get(arch, arch), "-mabi=ilp32"]

ARCH  = arch_flags("rv32i")

# Dhrystone 2.1 predates prototypes; silence those warnings for its two units only.
DHRY_W = ["-Wno-implicit-int", "-Wno-implicit-function-declaration",
          "-Wno-return-type", "-Wno-old-style-definition"]

BASE  = ["-O2", "-ffreestanding", "-nostdlib", "-nostartfiles",
         "-fno-common", "-Wall"]


def incs():
    return ["-I" + BENCH, "-I" + os.path.join(BENCH, "include"),
            "-I" + os.path.join(BENCH, "coremark_port"),
            "-I" + CM, "-I" + DHRY]


def build(out_elf, objdir, core_hz, dhry_runs, iterations, gap_cycles,
          host=False, arch="rv32i", hpm=False):
    os.makedirs(objdir, exist_ok=True)
    ARCH = arch_flags(arch)
    # The flags string goes into the `flags=` line of every capture; the mul/div
    # note is derived from the resolved -march, not from the alias.
    flags_str = (" ".join(ARCH + BASE) +
                 " | dhrystone: -std=gnu89 + upstream no-inline pragma"
                 " | mul/div: " + ("hardware M extension"
                                   if "m" in ARCH_ALIASES.get(arch, arch)[4:].split("_")[0]
                                   else "libgcc software (no M extension)"))
    common = ARCH + BASE + incs() + [
        "-DCORE_HZ=%uu" % core_hz,
        "-DDHRY_RUNS=%d" % dhry_runs,
        "-DITERATIONS=%d" % iterations,
        "-DBENCH_GAP_CYCLES=%uu" % gap_cycles,
        '-DBENCH_FLAGS="%s"' % flags_str,
    ]
    cm_defs = ["-DPERFORMANCE_RUN=1", "-DTOTAL_DATA_SIZE=2000",
               '-DCOMPILER_FLAGS="%s"' % flags_str]

    units = [
        # (source, extra flags)
        (os.path.join(BENCH, "bench_main.c"),  []),
        (os.path.join(BENCH, "bench_io.c"),    []),
        # Without these two flags GCC turns the memcpy body into a call to memcpy.
        (os.path.join(BENCH, "bench_lib.c"),
         ["-fno-builtin", "-fno-tree-loop-distribute-patterns"]),
        (os.path.join(BENCH, "dhry_glue.c"),   ["-std=gnu89"]),
        (os.path.join(DHRY,  "dhrystone_main.c"), ["-std=gnu89", "-Dmain=dhry_main"] + DHRY_W),
        (os.path.join(DHRY,  "dhrystone.c"),      ["-std=gnu89"] + DHRY_W),
        (os.path.join(BENCH, "coremark_port", "core_portme.c"), cm_defs),
        (os.path.join(CM, "core_main.c"),        cm_defs + ["-Dmain=coremark_main"]),
        (os.path.join(CM, "core_list_join.c"),   cm_defs),
        (os.path.join(CM, "core_matrix.c"),      cm_defs),
        (os.path.join(CM, "core_state.c"),       cm_defs),
        (os.path.join(CM, "core_util.c"),        cm_defs),
    ]

    # Off by default: arming the Zihpm counters adds CSR accesses outside the
    # timed regions, and the default image should stay byte-reproducible.
    if hpm:
        common = common + ["-DBENCH_HPM"]

    objs = []
    for src, extra in units:
        obj = os.path.join(objdir, os.path.basename(src)[:-2] + ".o")
        cmd = [CC] + common + extra + ["-c", "-o", obj, src]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        txt = r.stdout.decode("utf-8", "replace")
        if r.returncode != 0:
            sys.stderr.write(" ".join(cmd) + "\n" + txt)
            raise SystemExit("compile failed: %s" % src)
        if txt.strip():
            sys.stderr.write(txt)
        objs.append(obj)

    crt = os.path.join(objdir, "crt0.o")
    r = subprocess.run([CC] + ARCH + ["-c", "-o", crt, os.path.join(SOC, "crt0.S")],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        sys.stderr.write(r.stdout.decode("utf-8", "replace"))
        raise SystemExit("crt0 failed")

    # -lgcc last: the rv32i build calls __mulsi3/__divsi3 for every multiply.
    link = [CC] + ARCH + BASE + ["-T", os.path.join(SOC, "link.ld"),
                                 "-o", out_elf, crt] + objs + ["-lgcc"]
    r = subprocess.run(link, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        sys.stderr.write(" ".join(link) + "\n" + r.stdout.decode("utf-8", "replace"))
        raise SystemExit("link failed")
    return flags_str


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core-hz", type=int, default=None,
                    help="default: read SOC_CORE_HZ from fpga/generated/soc_clk.svh")
    ap.add_argument("--dhry-runs", type=int, default=50000)
    ap.add_argument("--iterations", type=int, default=1,
                    help="CoreMark ITERATIONS; must give >=10 s for a valid score")
    ap.add_argument("--gap-cycles", type=int, default=2_000_000)
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0x8000_0000)
    ap.add_argument("--words", type=int, default=32768)
    ap.add_argument("--out", required=True)
    ap.add_argument("--elf", default=None)
    ap.add_argument("--arch", choices=["rv32i", "rv32im", "rv32imzb", "rv32imb"],
                    default="rv32i",
                    help="which ISA Dhrystone and CoreMark are built for; "
                         "rv32imb is rv32im + Zba+Zbb+Zbs+Zbkb+Zicond")
    ap.add_argument("--hpm", action="store_true",
                    help="arm and report the six Zihpm counters")
    a = ap.parse_args()

    core_hz = a.core_hz
    if core_hz is None:
        svh = os.path.join(ROOT, "fpga/generated/soc_clk.svh")
        with open(svh) as f:
            for line in f:
                if line.startswith("`define SOC_CORE_HZ"):
                    core_hz = int(line.split()[-1])
        if core_hz is None:
            raise SystemExit("no SOC_CORE_HZ in %s" % svh)

    outdir = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(outdir, exist_ok=True)
    elf = a.elf or os.path.join(outdir, "bench_image.elf")
    objdir = os.path.join(outdir, "bench_obj")
    flags = build(elf, objdir, core_hz, a.dhry_runs, a.iterations, a.gap_cycles,
                  arch=a.arch, hpm=a.hpm)

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

    nwords = (used + 3) // 4
    with open(a.out, "w") as f:
        for i in range(nwords):
            f.write("%08x\n" % struct.unpack_from("<I", mem, i * 4)[0])

    print("%s: %d words (%d bytes used of %d)"
          % (a.out, nwords, used, a.words * 4))
    print("elf:   %s" % elf)
    print("clock: %d Hz   dhry_runs=%d   coremark ITERATIONS=%d"
          % (core_hz, a.dhry_runs, a.iterations))
    print("flags: %s" % flags)
    return 0


if __name__ == "__main__":
    sys.exit(main())
