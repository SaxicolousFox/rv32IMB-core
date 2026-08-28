#!/usr/bin/env python3
"""Self-test for model/modarith.py.  Run by `make regress`."""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modarith import (Q, QINV, BARR_V, montgomery_reduce, barrett_reduce,
                      to_mont, from_mont, _i16)

def main() -> int:
    fails = []

    # 1. The constant identity the plan states: 3329 * 62209 == 3160*65536 + 1.
    if (Q * QINV) % (1 << 16) != 1:
        fails.append(f"QINV wrong: Q*QINV mod 2^16 = {(Q*QINV)%(1<<16)}")
    if (Q * QINV) != 3160 * 65536 + 1:
        fails.append("Q*QINV != 3160*65536+1")
    if BARR_V != ((1 << 26) + Q // 2) // Q:
        fails.append("Barrett constant wrong")

    # 2. montgomery_reduce: congruence + the lazy range (-Q, Q).
    rinv = pow(1 << 16, -1, Q)
    random.seed(0xC0FFEE)
    for _ in range(20000):
        a = random.randrange(-Q * (1 << 15), Q * (1 << 15))
        r = montgomery_reduce(a)
        if (r - a * rinv) % Q != 0:
            fails.append(f"montgomery_reduce({a}) = {r}: wrong residue"); break
        if not (-Q < r < Q):
            fails.append(f"montgomery_reduce({a}) = {r}: outside (-Q,Q)"); break

    # 3. barrett_reduce: congruence + range.
    for _ in range(20000):
        a = random.randrange(-32768, 32768)
        r = barrett_reduce(a)
        if (r - a) % Q != 0:
            fails.append(f"barrett_reduce({a}) = {r}: wrong residue"); break
        if not (-(Q - 1) // 2 - 1 <= r <= (Q - 1) // 2 + 1):
            fails.append(f"barrett_reduce({a}) = {r}: outside expected range"); break

    # 4. Montgomery domain round-trip.
    for x in range(0, Q, 7):
        if from_mont(to_mont(x)) != x:
            fails.append(f"mont round-trip failed at {x}"); break

    # 5. Known reference value: montgomery_reduce(1) must be R^-1 mod Q.
    if montgomery_reduce(1) % Q != rinv % Q:
        fails.append("montgomery_reduce(1) != R^-1 mod Q")

    for f in fails:
        print("  FAIL:", f)
    if fails:
        print(f"MODEL_SELFTEST_FAIL ({len(fails)})")
        return 1
    print("MODEL_SELFTEST_OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
