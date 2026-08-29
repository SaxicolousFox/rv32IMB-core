#!/usr/bin/env python3
"""
Prove the Xkntt instruction set is SUFFICIENT and CORRECT for the real algorithm.

Encoding tests prove the four implementations will agree on bit patterns.  They
say nothing about whether the instructions actually compute Kyber's NTT.  This
test builds ntt(), invntt() and basemul() out of *nothing but* the Tier-1
instruction semantics and compares against the P0.3 golden model, which is itself
bit-exact against the pq-crystals C reference.

Getting this wrong is expensive: an instruction-set flaw discovered during Track
B or C means re-spinning the RTL, Spike and LLVM together.
"""
import os, random, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "model", "isa"))

from modarith import Q, _i16                      # noqa: E402
import ntt_ref                                     # noqa: E402
from xkntt import (exec_kbfct, exec_kbfgs, exec_kmm,               # noqa: E402
                   exec_kbmul0, exec_kbmul1, pack, lo16, hi16)

ZETAS = ntt_ref.ZETAS
F_INVNTT = 1441


def ntt_via_isa(poly):
    """Forward NTT using only kbfct."""
    r = list(poly)
    k = 1
    length = 128
    while length >= 2:
        start = 0
        while start < 256:
            z = ZETAS[k] & 0xFFFF
            k += 1
            for j in range(start, start + length):
                rd = exec_kbfct(pack(r[j + length], r[j]), z)
                r[j] = lo16(rd)
                r[j + length] = hi16(rd)
            start = start + 2 * length
        length >>= 1
    return r


def invntt_via_isa(poly):
    """Inverse NTT using kbfgs, with the final mont^2/128 scaling via kmm."""
    r = list(poly)
    k = 127
    length = 2
    while length <= 128:
        start = 0
        while start < 256:
            z = ZETAS[k] & 0xFFFF
            k -= 1
            for j in range(start, start + length):
                rd = exec_kbfgs(pack(r[j + length], r[j]), z)
                r[j] = lo16(rd)
                r[j + length] = hi16(rd)
            start = start + 2 * length
        length <<= 1
    for j in range(256):
        r[j] = lo16(exec_kmm(r[j] & 0xFFFF, F_INVNTT))
    return r


def basemul_via_isa(a, b):
    """poly_basemul_montgomery using kbmul0 / kbmul1."""
    r = [0] * 256
    for i in range(64):
        for half, zeta in ((0, ZETAS[64 + i]), (2, -ZETAS[64 + i])):
            base = 4 * i + half
            rs1 = pack(a[base + 1], a[base])
            rs2 = pack(b[base + 1], b[base])
            r[base]     = lo16(exec_kbmul0(rs1, rs2, zeta & 0xFFFF))
            r[base + 1] = lo16(exec_kbmul1(rs1, rs2))
    return r


def ref_basemul(a, b):
    """Reference basemul, straight transcription of pq-crystals poly.c."""
    fq = ntt_ref.fqmul
    r = [0] * 256
    for i in range(64):
        for half, zeta in ((0, ZETAS[64 + i]), (2, -ZETAS[64 + i])):
            o = 4 * i + half
            a0, a1 = a[o], a[o + 1]
            b0, b1 = b[o], b[o + 1]
            c0 = fq(a1, b1)
            c0 = fq(c0, zeta)
            c0 = _i16(c0 + fq(a0, b0))
            c1 = _i16(fq(a0, b1) + fq(a1, b0))
            r[o], r[o + 1] = c0, c1
    return r


def main() -> int:
    rng = random.Random(0x5EED)
    fails = []
    N = int(os.environ.get("ISA_SEM_TRIALS", "200"))

    for t in range(N):
        p = [rng.randrange(0, Q) for _ in range(256)]

        got = ntt_via_isa(p)
        want = ntt_ref.ntt(p)
        if got != want:
            bad = next(i for i in range(256) if got[i] != want[i])
            fails.append(f"trial {t}: ntt via kbfct differs at coeff {bad}: "
                         f"{got[bad]} != {want[bad]}")
            break

        got_i = invntt_via_isa(want)
        want_i = ntt_ref.invntt(want)
        if got_i != want_i:
            bad = next(i for i in range(256) if got_i[i] != want_i[i])
            fails.append(f"trial {t}: invntt via kbfgs differs at coeff {bad}: "
                         f"{got_i[bad]} != {want_i[bad]}")
            break

        # round-trip: invntt(ntt(x)) == x * R mod q
        R = (1 << 16) % Q
        if [v % Q for v in got_i] != [(v * R) % Q for v in p]:
            fails.append(f"trial {t}: ISA round-trip != x*R mod q")
            break

    for t in range(min(N, 50)):
        a = [rng.randrange(0, Q) for _ in range(256)]
        b = [rng.randrange(0, Q) for _ in range(256)]
        na, nb = ntt_ref.ntt(a), ntt_ref.ntt(b)
        if basemul_via_isa(na, nb) != ref_basemul(na, nb):
            fails.append(f"trial {t}: basemul via kbmul0/kbmul1 differs")
            break

    for f in fails:
        print("  FAIL:", f)
    if fails:
        print(f"ISA_SEMANTICS_FAIL ({len(fails)})")
        return 1
    print(f"ISA_SEMANTICS_OK ({N} polynomials: ntt via kbfct, invntt via kbfgs+kmm, "
          f"basemul via kbmul0/kbmul1 -- all bit-exact vs the P0.3 golden model)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
