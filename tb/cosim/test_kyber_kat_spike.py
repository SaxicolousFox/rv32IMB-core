#!/usr/bin/env python3
"""
C1 acceptance: Spike runs ML-KEM-768 with the custom instructions and produces
KAT-matching output.

The chain of evidence, weakest link first:

  1. sw/kyber/kat_main.c is a faithful reimplementation of the reference's
     test/test_vectors.c -- proved by running it natively for the full 10000
     vectors and matching the SHA-256 in the reference's own SHA256SUMS.
     Without this step, "matches my own program" would be worth nothing.
  2. The TIER1 backend, built from sw/include/xkntt.h, reproduces the official
     vectors on the host over all 10000 records.  That isolates the algorithm
     restructuring from the instruction implementation.
  3. Spike, with the SW backend, reproduces the vectors on RV32I.  Positive
     control for the bare-metal runtime.
  4. Spike, with the TIER1 backend, reproduces the vectors using kbfct, kbfgs,
     kbmul0, kbmul1 and kmm.  This is the step C1 actually asks for.

Set KYBER_KAT_FULL=1 to run the full 10000 vectors on Spike; that takes about
an hour and is not part of the routine regression.
"""
import hashlib, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KYBER = os.path.join(ROOT, "sw", "kyber")
TVECS = os.path.join(ROOT, "toolchain", "kyber", "tvecs768")
SUMS  = os.path.join(ROOT, "toolchain", "kyber", "SHA256SUMS")
ISA   = "rv32i_zicsr_zicntr_xkntt0p1"

LINES_PER_RECORD = 6      # public key, secret key, ciphertext, ss B, ss A, pseudorandom ss

fails = []
def fail(m): fails.append(m)


def make(target, backend, ntests):
    out = subprocess.run(
        ["make", f"TARGET={target}", f"BACKEND={backend}", f"NTESTS={ntests}"],
        cwd=KYBER, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if out.returncode != 0:
        raise RuntimeError(f"build {target}/{backend} failed:\n"
                           + out.stdout.decode()[-3000:])
    name = "kat" if target == "host" else "kat.elf"
    return os.path.join(KYBER, "build", f"{target}-{backend}-n{ntests}-d0", name)


def run(binary, target, timeout=7200):
    cmd = [binary] if target == "host" else ["spike", f"--isa={ISA}", binary]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def expected_prefix(n):
    with open(TVECS) as f:
        return "".join(next(f) for _ in range(n * LINES_PER_RECORD))


def check(label, target, backend, n):
    binary = make(target, backend, n)
    rc, out = run(binary, target)
    if rc != 0:
        fail(f"{label}: exited {rc}")
        return
    want = expected_prefix(n)
    if out != want:
        got_lines, want_lines = out.splitlines(), want.splitlines()
        for i, (g, w) in enumerate(zip(got_lines, want_lines)):
            if g != w:
                fail(f"{label}: line {i+1} differs\n     got  {g[:80]}\n"
                     f"     want {w[:80]}")
                break
        else:
            fail(f"{label}: produced {len(got_lines)} lines, expected {len(want_lines)}")
        return
    print(f"  {label}: {n} records byte-identical to the official tvecs768")


def check_full_native():
    """Pin our driver to the reference's published SHA-256."""
    binary = make("host", "SW", 10000)
    rc, out = run(binary, "host")
    if rc != 0:
        fail(f"native full KAT exited {rc}")
        return
    digest = hashlib.sha256(out.encode()).hexdigest()
    want = None
    for line in open(SUMS):
        if line.strip().endswith("tvecs768"):
            want = line.split()[0]
    if want is None:
        fail("could not find tvecs768 in SHA256SUMS")
    elif digest != want:
        fail(f"native full KAT sha256 {digest}, SHA256SUMS says {want}")
    else:
        print(f"  native SW, 10000 records: sha256 {digest[:16]}... "
              f"matches the reference's own SHA256SUMS")
        return True
    return False


def check_full_tier1_native():
    binary = make("host", "TIER1", 10000)
    rc, out = run(binary, "host")
    digest = hashlib.sha256(out.encode()).hexdigest()
    want = [l.split()[0] for l in open(SUMS) if l.strip().endswith("tvecs768")][0]
    if rc != 0 or digest != want:
        fail(f"native TIER1 full KAT: exit {rc}, sha256 {digest[:16]}..., "
             f"expected {want[:16]}...")
    else:
        print(f"  native TIER1, 10000 records: same sha256 -- the Tier-1 "
              f"restructuring of ntt/invntt/basemul is exact")


def main():
    for tool in ("spike", "riscv-none-elf-gcc", "cc", "make"):
        if shutil.which(tool) is None:
            print(f"SKIP: {tool} not on PATH")
            return 0
    if not os.path.exists(TVECS):
        print("SKIP: reference tvecs768 not present")
        return 0

    n = int(os.environ.get("KYBER_KAT_N", "3"))
    full = os.environ.get("KYBER_KAT_FULL") == "1"

    if check_full_native():
        check_full_tier1_native()
    check("Spike SW", "rv32", "SW", n)
    check("Spike TIER1", "rv32", "TIER1", n)
    if full:
        check("Spike TIER1 (full)", "rv32", "TIER1", 10000)

    if fails:
        print("\nKYBER_KAT_FAIL:")
        for f in fails:
            print("  " + f)
        return 1
    print("kyber KAT on Spike OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
