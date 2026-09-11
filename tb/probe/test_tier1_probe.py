#!/usr/bin/env python3
"""
MODS_A2 A24 -- build and run the Tier-1 probe's correctness testbench.

The probe is a timing artefact, not a design, but its number is only worth
having if it computes the frozen contract: a datapath that gets `kbfgs` wrong
is very likely a datapath the synthesiser made SMALLER, and a smaller datapath
reports a frequency the real unit cannot reach.

Vectors come from model/isa/xkntt.py, which is frozen and validated bit-exactly
against the C reference.  This script only compares, and it checks every
configuration the frozen latency table permits:

    STAGES=4  ->  kmm 4, kbfct/kbfgs 5   <- THE FROZEN TABLE, exactly
    STAGES=3  ->  kmm 3, kbfct/kbfgs 4   <- what fusing segments would cost
    STAGES=2  ->  kmm 2, kbfct/kbfgs 3   <- margin probe, nothing needs it

`kmm` finishes one edge earlier than the other two in every configuration
because it taps out of the Montgomery segment rather than passing through the
butterfly, so STAGES=4 satisfies BOTH of the contract's numbers with one
pipeline.  The testbench checks the occupancy per operation, which is the only
place that early tap is observable at all.
"""
import os, random, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model"))
RTL = os.path.join(ROOT, "rtl/probe/rvntt_tier1_probe.sv")
TB  = os.path.join(ROOT, "tb/probe/tb_tier1_probe.cpp")

from isa import xkntt   # noqa: E402  -- the frozen model

OPS = [(0, "kmm"), (1, "kbfct"), (2, "kbfgs")]
N   = 2000


def vectors(seed=20260903):
    rng = random.Random(seed)
    out = []
    # Deliberately biased towards the edges of the lazy output range.  The
    # reference is NOT fully reduced, so +-q, +-2q and the int16 extremes are
    # where a sign-extension or a truncation error shows and uniform random
    # words mostly do not.
    edges = [0, 1, -1, 3329, -3329, 1664, -1664, 32767, -32768, 6658, -6658]
    for i in range(N):
        op, name = OPS[i % 3]
        if i < len(edges) * len(edges) * 3:
            a = edges[(i // 3) % len(edges)]
            b = edges[(i // (3 * len(edges))) % len(edges)]
            z = rng.randrange(-3329, 3329)
        else:
            a = rng.randrange(-32768, 32768)
            b = rng.randrange(-32768, 32768)
            z = rng.randrange(-32768, 32768)
        rs1 = ((b & 0xFFFF) << 16) | (a & 0xFFFF)
        rs2 = z & 0xFFFF
        exp = xkntt.EXEC[name](rs1, rs2)
        out.append((op, rs1, rs2, exp & 0xFFFFFFFF))
    return out


def main() -> int:
    vs = vectors()
    vecfile = os.path.join(tempfile.gettempdir(), "rv32imb_core_tier1_vectors.txt")
    with open(vecfile, "w") as fh:
        for op, rs1, rs2, exp in vs:
            fh.write(f"{op} {rs1:08x} {rs2:08x} {exp:08x}\n")

    rc = 0
    for stages in (4, 3, 2):
        build = os.path.join(tempfile.gettempdir(), f"rv32imb_core_obj_tier1_{stages}")
        cmd = ["verilator", "--cc", RTL, "--exe", TB, "--build", "-j", "4",
               "-Wall", f"-GSTAGES={stages}", "--Mdir", build,
               "--prefix", "Vrvntt_tier1_probe",
               "--top-module", "rvntt_tier1_probe"]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(r.stdout.decode("utf-8", "replace")); return 1
        exe = os.path.join(build, "Vrvntt_tier1_probe")
        r = subprocess.run([exe, "--vectors", vecfile, "--stages", str(stages)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = r.stdout.decode("utf-8", "replace").strip()
        print(out)
        if r.returncode != 0 or "TIER1_TB_OK" not in out:
            rc = 1
    if rc == 0:
        print(f"TIER1_PROBE_OK: {len(vs)} vectors x 3 configurations")
    return rc


if __name__ == "__main__":
    sys.exit(main())
