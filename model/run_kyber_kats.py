#!/usr/bin/env python3
"""
Verify the pq-crystals ML-KEM reference passes its OWN KATs, unmodified.

This guards the foundation of every later comparison: if the reference checkout
is ever perturbed (a stray edit, a bad rebase, a branch switch), every downstream
"bit-exact" claim silently becomes a claim about the wrong thing.

Note the reference tree is deliberately left pristine -- the DUMP_NTT
instrumentation lives in model/cref/ntt_dump.c, generated from ref/ntt.c, so
these KATs still test exactly what upstream ships.
"""
import hashlib, os, subprocess, sys

ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KYBER = os.path.join(ROOT, "toolchain", "kyber")
REF   = os.path.join(KYBER, "ref")


def main() -> int:
    if not os.path.isdir(REF):
        print("SKIP: kyber reference not present")
        return 0

    fails = []

    # Build (idempotent).
    r = subprocess.run(["make", "-j", str(os.cpu_count() or 4), "-C", REF],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")[-2000:])
        print("KYBER_KAT_FAIL: build")
        return 1

    # 1. Functional round-trip self-test for ML-KEM-768.
    exe = os.path.join(REF, "test", "test_kyber768")
    r = subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        fails.append(f"test_kyber768 exited {r.returncode}: "
                     f"{r.stdout.decode('utf-8','replace')[-400:]}")

    # 2. Deterministic test vectors must hash to upstream's published SHA256SUMS.
    sums = {}
    with open(os.path.join(KYBER, "SHA256SUMS")) as f:
        for line in f:
            h, name = line.split()
            sums[name.strip()] = h.strip()

    for alg in ("512", "768", "1024"):
        name = f"tvecs{alg}"
        exe = os.path.join(REF, "test", f"test_vectors{alg}")
        r = subprocess.run([exe], stdout=subprocess.PIPE)
        if r.returncode != 0:
            fails.append(f"test_vectors{alg} exited {r.returncode}"); continue
        got = hashlib.sha256(r.stdout).hexdigest()
        want = sums.get(name)
        if want is None:
            fails.append(f"{name}: not listed in SHA256SUMS")
        elif got != want:
            fails.append(f"{name}: sha256 {got} != expected {want}")

    for f_ in fails:
        print("  FAIL:", f_)
    if fails:
        print(f"KYBER_KAT_FAIL ({len(fails)})")
        return 1
    print("KYBER_KAT_OK (test_kyber768 + tvecs{512,768,1024} match upstream SHA256SUMS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
