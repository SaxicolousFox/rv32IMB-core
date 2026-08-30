#!/usr/bin/env python3
"""
Run one riscv-arch-test ELF on the RTL and emit its RISCOF signature.

RISCOF's contract with a DUT is narrow: for each test, produce a file of the
memory contents between `begin_signature` and `end_signature` after the program
has run, one 32-bit word per line in lowercase hex.  It then diffs that against
the reference model's file.  Everything else -- test selection, the report -- is
RISCOF's.

THE SIGNATURE IS RECONSTRUCTED FROM THE STORE BUS, not read out of the RAM.
Peeking inside the memory would mean marking `mem` public for Verilator, i.e.
putting a simulator-specific annotation on synthesisable RTL for the benefit of
a test.  Replaying the program's committed stores onto its own load image gives
the same answer from information the core already exposes, and it is sound for
the same reason the tohost watch is: a store is issued from EX, and nothing past
EX is ever squashed (rvntt_core's trap invariant).

Words the test never writes come from the ELF image, which is why the replay
starts from the image rather than from zeros.
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
sys.path.insert(0, os.path.join(ROOT, "tb/unit"))
import spike_asm                   # noqa: E402
import test_core_verilator as t4   # noqa: E402

BASE = 0x80000000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True, help="the built Verilator simulator")
    ap.add_argument("--image", required=True,
                    help="the hex image path the simulator was BUILT with")
    ap.add_argument("--elf", required=True)
    ap.add_argument("--sig", required=True)
    ap.add_argument("--log", default=None)
    ap.add_argument("--max-cycles", type=int, default=4000000)
    a = ap.parse_args()

    workdir = os.path.dirname(os.path.abspath(a.sig)) or "."

    tohost = spike_asm.symbol(a.elf, "tohost")
    sig_lo = spike_asm.symbol(a.elf, "begin_signature")
    sig_hi = spike_asm.symbol(a.elf, "end_signature")
    for name, val in (("tohost", tohost), ("begin_signature", sig_lo),
                      ("end_signature", sig_hi)):
        if val is None:
            print(f"rvntt_run: {a.elf} has no {name} symbol", file=sys.stderr)
            return 1

    hexf, nwords = t4.elf_to_hex(a.elf, workdir)
    shutil.copy(hexf, a.image)

    store_log = os.path.join(workdir, "stores.log")
    cmd = [a.exe, "--tohost", "0x%08x" % tohost, "--expect-tohost", "1",
           "--store-log", store_log, "--max-cycles", str(a.max_cycles)]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    if a.log:
        open(a.log, "w").write(out)
    if r.returncode != 0:
        print("rvntt_run: the DUT did not finish the test\n" + out,
              file=sys.stderr)
        # Still emit whatever signature the run produced: an empty or short one
        # makes RISCOF report a mismatch, which is a better failure than a
        # missing file (which it reports as an infrastructure error).

    # ---- replay the committed stores onto the load image ----------------
    mem = [int(line, 16) for line in open(hexf)]
    if len(mem) < nwords:
        mem += [0] * (nwords - len(mem))

    if os.path.exists(store_log):
        for line in open(store_log):
            f = line.split()
            if len(f) != 3:
                continue
            addr, be, data = int(f[0], 16), int(f[1], 16), int(f[2], 16)
            idx = (addr - BASE) >> 2
            if idx < 0 or idx >= len(mem):
                continue        # outside the modelled RAM; it aliases by design
            w = mem[idx]
            for lane in range(4):
                if be & (1 << lane):
                    mask = 0xFF << (8 * lane)
                    w = (w & ~mask) | (data & mask)
            mem[idx] = w & 0xFFFFFFFF

    with open(a.sig, "w") as f:
        for addr in range(sig_lo, sig_hi, 4):
            idx = (addr - BASE) >> 2
            f.write("%08x\n" % (mem[idx] if 0 <= idx < len(mem) else 0))
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
