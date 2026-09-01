#!/usr/bin/env python3
"""
Build the A13 benchmark image: Dhrystone + CoreMark -> ELF -> $readmemh .mem.

Same shape as build_soc_image.py, and it reuses that script's ELF walk, so the
simulated image and the synthesised one cannot diverge.  What is different is the
source list, and every entry in it is here for a reason:

  toolchain/riscv-tests/benchmarks/dhrystone/*   COMPILED IN PLACE, never copied.
      The plan calls for the riscv-tests port specifically because it is already
      adapted; keeping the checkout pristine is the same rule that applies to
      toolchain/kyber/, and for the same reason -- it is also what the riscv_tests
      regression runs against.  The port lives entirely in sw/bench/include/util.h,
      which dhrystone_main.c includes after dhrystone.h.

  toolchain/coremark/*.c                          likewise pristine; the port is
      sw/bench/coremark_port/, which is exactly the file EEMBC expects you to
      write.

-std=gnu89 for the Dhrystone translation units is not cosmetic.  Dhrystone is
K&R C throughout (`Proc_1 (Ptr_Val_Par) REG Rec_Pointer Ptr_Val_Par; { ... }`),
GCC 15 defaults to C23, and C23 removed both K&R definitions and the empty
parameter list -- `Enumeration Func_1 ();` would become a prototype taking no
arguments and the two-argument call would be a hard error.

-Dmain= renames each benchmark's main so sw/bench/bench_main.c can call them in
sequence.  It is applied per translation unit, not globally.

The reported flags string is built from the SAME list that is passed to the
compiler, so "compiled -O2" cannot drift from what was compiled.
"""
import argparse, os, struct, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_soc_image import load_segments          # one ELF walk, not two

BENCH = os.path.join(ROOT, "sw", "bench")
SOC   = os.path.join(ROOT, "sw", "soc")
DHRY  = os.path.join(ROOT, "toolchain", "riscv-tests", "benchmarks", "dhrystone")
CM    = os.path.join(ROOT, "toolchain", "coremark")

CC = "riscv-none-elf-gcc"

# The flags that define the measurement.  Anything here appears verbatim in the
# `flags=` line of the UART output and in CoreMark's own "Compiler flags".
ARCH  = ["-march=rv32i_zicsr", "-mabi=ilp32"]
# Dhrystone 2.1 predates prototypes: implicit int, implicit function
# declarations and old-style definitions are what the benchmark IS.  Silencing
# them for those two translation units only keeps the build output readable
# without hiding a warning from any file this project wrote.
DHRY_W = ["-Wno-implicit-int", "-Wno-implicit-function-declaration",
          "-Wno-return-type", "-Wno-old-style-definition"]

BASE  = ["-O2", "-ffreestanding", "-nostdlib", "-nostartfiles",
         "-fno-common", "-Wall"]


def incs():
    return ["-I" + BENCH, "-I" + os.path.join(BENCH, "include"),
            "-I" + os.path.join(BENCH, "coremark_port"),
            "-I" + CM, "-I" + DHRY]


def build(out_elf, objdir, core_hz, dhry_runs, iterations, gap_cycles, host=False):
    os.makedirs(objdir, exist_ok=True)
    # What goes into `flags=` on the wire and into CoreMark's own "Compiler
    # flags" line.  The plan is explicit that the flags are part of the result --
    # "do not use -O3 with LTO and full inlining and then report the number
    # without saying so" -- so the string carries the two facts that are NOT
    # visible in the flag list itself and that change how the score should be
    # read: Dhrystone is built with GCC's no-inline pragma (upstream
    # riscv-tests', not ours), and there is no M extension, so every multiply,
    # divide and modulo in both benchmarks is a call into libgcc.
    flags_str = (" ".join(ARCH + BASE) +
                 " | dhrystone: -std=gnu89 + upstream no-inline pragma"
                 " | mul/div: libgcc software (no M extension)")
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
        # -fno-builtin: without it GCC compiles the body of memcpy into a call to
        # memcpy.  -fno-tree-loop-distribute-patterns: without it the byte loop is
        # recognised as memcpy and replaced by one, same infinite recursion.
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

    # -lgcc last, and it is not optional: there is no M extension, so every
    # multiply, divide and modulo in both benchmarks becomes a call to
    # __mulsi3/__divsi3/__umodsi3.  That is a real property of the measurement
    # and is reported alongside the score, not hidden.
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
    flags = build(elf, objdir, core_hz, a.dhry_runs, a.iterations, a.gap_cycles)

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
