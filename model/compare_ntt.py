#!/usr/bin/env python3
"""
P0.3 acceptance test.

Done-when (plan):
  "for 1000 random polynomials, python_model.ntt(p) == c_reference.ntt(p)
   bit-exactly, and per-layer dumps exist."

What this actually checks, which is more than the letter of that line:

  1. C reference  ==  ntt_ref.py            bit-exact, INCLUDING all 7 layer
                                            snapshots and the unreduced values'
                                            exact sign and magnitude.
  2. C reference  ==  ntt_math.py  (mod q)  agreement with a model that shares
                                            no code, no butterflies, no
                                            Montgomery domain, no zeta table.
  3. invntt: same two checks, per layer.
  4. invntt(ntt(x)) == x * R mod q          the reference's invntt is really
                                            invntt_tomont; asserting the exact
                                            factor stops a Montgomery-domain
                                            slip from hiding here.

Beyond the 1000 random polynomials it also runs directed cases (zero, extremes,
and a unit impulse at each of the 256 positions) because impulses isolate the
butterfly-network topology, which random inputs mask.
"""
from __future__ import annotations
import argparse, os, random, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from modarith import Q
import ntt_ref, ntt_math

DUMPDIR = os.path.join(HERE, "dumps")
CREF    = os.path.join(HERE, "cref", "ntt_dump")
R_MONT  = (1 << 16) % Q


def s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def write_inputs(path, polys):
    with open(path, "w") as f:
        for p in polys:
            f.write(" ".join("%04x" % (v & 0xFFFF) for v in p) + "\n")


def read_dumps(path):
    out = {}
    with open(path) as f:
        for line in f:
            t = line.split()
            idx, tag, layer = int(t[0]), t[1], int(t[2])
            out.setdefault(idx, {})[(tag, layer)] = [s16(int(x, 16)) for x in t[3:]]
    return out


def run_cref(inp, dmp, mode):
    r = subprocess.run([CREF, inp, dmp, mode],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("cref failed: " + r.stderr.decode())


def build_testset(n_random, seed):
    """Returns (polys, labels)."""
    rng = random.Random(seed)
    polys, labels = [], []

    for i in range(n_random):
        polys.append([rng.randrange(0, Q) for _ in range(256)])
        labels.append(f"random[{i}]")

    polys.append([0] * 256);            labels.append("all_zero")
    polys.append([Q - 1] * 256);        labels.append("all_q_minus_1")
    polys.append([1] * 256);            labels.append("all_ones")
    # signed extremes -- still within the reference's int16 growth budget
    polys.append([-(Q - 1)] * 256);     labels.append("all_neg_q_minus_1")
    polys.append([(Q - 1) if i % 2 else -(Q - 1) for i in range(256)])
    labels.append("alternating_extremes")

    for pos in range(256):                     # impulse response
        p = [0] * 256
        p[pos] = 1
        polys.append(p)
        labels.append(f"impulse[{pos}]")

    return polys, labels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--num-random", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0xA5A5)
    ap.add_argument("--keep", action="store_true", default=True)
    a = ap.parse_args()

    if not os.path.exists(CREF):
        print(f"ERROR: {CREF} not built.  Run: make -C model/cref", file=sys.stderr)
        return 2

    os.makedirs(DUMPDIR, exist_ok=True)
    polys, labels = build_testset(a.num_random, a.seed)
    print(f"test set: {len(polys)} polynomials "
          f"({a.num_random} random + {len(polys)-a.num_random} directed)")

    fails = []

    # ---------------- forward ----------------
    inp = os.path.join(DUMPDIR, "ntt_inputs.hex")
    dmp = os.path.join(DUMPDIR, "ntt_fwd_dumps.txt")
    write_inputs(inp, polys)
    run_cref(inp, dmp, "fwd")
    fwd = read_dumps(dmp)

    c_fwd_out = []
    for i, (p, lab) in enumerate(zip(polys, labels)):
        d = fwd[i]
        if d[("ntt_in", -1)] != [s16(v & 0xFFFF) for v in p]:
            fails.append(f"{lab}: C ntt_in does not echo the input"); continue

        layers = []
        py = ntt_ref.ntt(p, layers)
        for L in range(7):
            if d[("ntt_layer", L)] != layers[L]:
                bad = [k for k in range(256) if d[("ntt_layer", L)][k] != layers[L][k]]
                fails.append(f"{lab}: layer {L} mismatch at {len(bad)} coeff(s), "
                             f"first idx {bad[0]}: C={d[('ntt_layer',L)][bad[0]]} "
                             f"py={layers[L][bad[0]]}")
                break
        else:
            if d[("ntt_out", -1)] != py:
                fails.append(f"{lab}: final ntt output mismatch")
        c_fwd_out.append(d[("ntt_out", -1)])

        m = ntt_math.ntt([v % Q for v in p])
        if [v % Q for v in d[("ntt_out", -1)]] != m:
            fails.append(f"{lab}: C output disagrees with INDEPENDENT math model")

    # ---------------- inverse ----------------
    inp_i = os.path.join(DUMPDIR, "invntt_inputs.hex")
    dmp_i = os.path.join(DUMPDIR, "invntt_dumps.txt")
    write_inputs(inp_i, c_fwd_out)
    run_cref(inp_i, dmp_i, "inv")
    inv = read_dumps(dmp_i)

    for i, (p, lab) in enumerate(zip(polys, labels)):
        d = inv[i]
        layers = []
        py = ntt_ref.invntt(c_fwd_out[i], layers)
        for L in range(7):
            if d[("invntt_layer", L)] != layers[L]:
                fails.append(f"{lab}: invntt layer {L} mismatch"); break
        else:
            if d[("invntt_out", -1)] != py:
                fails.append(f"{lab}: final invntt output mismatch")

        # invntt(ntt(x)) must equal x * R mod q, exactly.
        got = [v % Q for v in d[("invntt_out", -1)]]
        want = [(v * R_MONT) % Q for v in [w % Q for w in p]]
        if got != want:
            fails.append(f"{lab}: invntt(ntt(x)) != x*R mod q")

    # ---------------- report ----------------
    for f_ in fails[:20]:
        print("  FAIL:", f_)
    if len(fails) > 20:
        print(f"  ... and {len(fails)-20} more")

    if fails:
        print(f"\nP0.3 COMPARE FAILED ({len(fails)} failures)")
        return 1

    nlines = sum(1 for _ in open(dmp)) + sum(1 for _ in open(dmp_i))
    print(f"forward: C == ntt_ref bit-exact incl. all 7 layers")
    print(f"forward: C == independent math model (mod q)")
    print(f"inverse: C == ntt_ref bit-exact incl. all 7 layers")
    print(f"inverse: invntt(ntt(x)) == x * R mod q")
    print(f"per-layer dumps: {dmp} + {dmp_i} ({nlines} lines)")
    print(f"\nP0.3 COMPARE OK ({len(polys)} polynomials)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
