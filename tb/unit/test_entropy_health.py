#!/usr/bin/env python3
"""
MODS_A2 A29 -- the entropy path's health tests, the `seed` state machine, and
the cutoffs the tests use.

TWO THINGS ARE CHECKED HERE AND THEY ARE DIFFERENT KINDS OF CHECK.

1. THE CUTOFFS ARE RECOMPUTED from SP 800-90B's own definitions and compared
   against the RTL's parameters.  Health-test cutoffs are exactly the constant
   this project has learned not to take from memory: the widely-quoted "821"
   for a 1024-sample adaptive-proportion window belongs to a different assumed
   entropy rate, and using it here would have made the test four sigma looser
   than intended while looking authoritative.

2. THE TESTS ARE SHOWN TO FIRE, on a stuck-at-0, a stuck-at-1 and two biased
   sources, and NOT to fire on a good one.  A29's own words: a health test that
   has never been observed to fire is not a health test.
"""
import os, re, subprocess, sys, tempfile
from math import comb, ceil
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RTL = [os.path.join(ROOT, "rtl/core", f) for f in
       ("rvntt_seed.sv", "rvntt_entropy.sv", "rvntt_entropy_health.sv")]
TB = os.path.join(ROOT, "tb/unit/tb_entropy_health.cpp")

ALPHA_LOG2 = 20      # SP 800-90B's recommended false-positive rate, alpha = 2^-alpha_log2
H_BITS     = 1.0     # assumed entropy per sample; see the note below


def repetition_cutoff(h=H_BITS, alpha_log2=ALPHA_LOG2):
    """SP 800-90B 4.4.1:  C = 1 + ceil(-log2(alpha) / H)."""
    return 1 + ceil(alpha_log2 / h)


def adaptive_cutoff(w, h=H_BITS, alpha_log2=ALPHA_LOG2):
    """SP 800-90B 4.4.2: the smallest C with P[Bin(W, 2^-H) >= C] <= alpha.

    Computed exactly with Fractions rather than floats: the tail is about
    2^-20, which is where double precision starts to matter and where being
    quietly off by one would move the cutoff without anything noticing.
    """
    assert h == 1.0, "only the binary case is derived here"
    p = Fraction(1, 2)
    alpha = Fraction(1, 1 << alpha_log2)
    tail = Fraction(0)
    for c in range(w, 0, -1):
        tail += Fraction(comb(w, c)) * p**c * (1 - p)**(w - c)
        if tail > alpha:
            return c + 1
    return 1


def rtl_param(name):
    src = open(os.path.join(ROOT, "rtl/core/rvntt_entropy_health.sv")).read()
    m = re.search(r"parameter\s+int\s+%s\s*=\s*(\d+)" % name, src)
    if not m:
        raise SystemExit("ENTROPY_FAIL: no %s parameter in rvntt_entropy_health.sv "
                         "-- the check cannot be vacuous, so this is a failure" % name)
    return int(m.group(1))


def main() -> int:
    rc = 0

    # ---- 1. the cutoffs -------------------------------------------------
    w = rtl_param("AP_WINDOW")
    want_rep = repetition_cutoff()
    want_ap = adaptive_cutoff(w)
    got_rep = rtl_param("REP_CUTOFF")
    got_ap = rtl_param("AP_CUTOFF")
    print("SP 800-90B cutoffs, recomputed at H=%.1f, alpha=2^-%d, W=%d:"
          % (H_BITS, ALPHA_LOG2, w))
    print("  repetition (4.4.1)          RTL %4d   derived %4d   %s"
          % (got_rep, want_rep, "ok" if got_rep == want_rep else "MISMATCH"))
    print("  adaptive proportion (4.4.2) RTL %4d   derived %4d   %s"
          % (got_ap, want_ap, "ok" if got_ap == want_ap else "MISMATCH"))
    if got_rep != want_rep or got_ap != want_ap:
        print("ENTROPY_FAIL: the RTL's cutoffs do not match SP 800-90B's own "
              "definitions.  Do not 'fix' this by copying the RTL value here -- "
              "the derivation is the authority and the parameter is the copy.")
        rc = 1

    # ---- 2. the tests fire ----------------------------------------------
    build = os.path.join(tempfile.gettempdir(), "rvntt_obj_entropy")
    cmd = ["verilator", "--cc"] + RTL + ["--exe", TB, "--build", "-j", "4",
           "-Wall", "--Mdir", build, "--prefix", "Vrvntt_seed",
           "--top-module", "rvntt_seed",
           "-I" + os.path.join(ROOT, "rtl/core")]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")); return 1
    exe = os.path.join(build, "Vrvntt_seed")
    r = subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    print(out.strip())
    if r.returncode != 0 or "ENTROPY_TB_OK" not in out:
        rc = 1

    if rc == 0:
        print("\nENTROPY_OK: cutoffs derived and matched; the health tests were "
              "observed to fire on four bad sources and to stay quiet on a good one")
    return rc


if __name__ == "__main__":
    sys.exit(main())
