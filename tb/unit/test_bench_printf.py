#!/usr/bin/env python3
"""
Diff sw/bench/bench_io.c's printf against glibc's over a corpus.

The benchmark's console is the only path from a cycle counter to a reported
number, so a formatting bug and a slow core produce the same artefact: a capture
with a wrong figure in it.  Nothing else in A13 can tell those apart.

--inject breaks one thing in the formatter and re-runs, to show the diff can
actually fail.  Zero padding is the one that would silently corrupt a result:
CoreMark prints its CRCs as 0x%04x, and a dropped pad turns 0x0123 into 0x123.

Dropping the `l` modifier is deliberately NOT in the list.  It escaped when it
was, and correctly so: on RV32 long and int are both 32 bits, so `l` is a no-op
there, and the only reason the host build can tell the difference at all is that
its long is 64-bit.  A mutation that changes nothing on the target is not an
escape to be plugged -- forcing it to "fail" would mean testing a property the
target does not have.
"""
import argparse, os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "sw", "bench")

# (name, pattern, replacement) applied to a COPY of bench_io.c
INJECTIONS = [
    ("zero_pad_ignored",
     "if (!left && zero) {", "if (0) {"),
    ("negative_sign_dropped",
     "out += emit_num(u, 10, 0, v < 0, width, zero, left);",
     "out += emit_num(u, 10, 0, 0, width, zero, left);"),
    ("width_off_by_one",
     "pad = width - n;", "pad = width - n - 1;"),
    ("hex_digits_uppercase_always",
     "const char *digits = upper ? up : lo;",
     "const char *digits = up;"),
]


def run_once(io_src, workdir):
    exe = os.path.join(workdir, "printf_diff")
    cmd = ["cc", "-O1", "-o", exe, "-DBENCH_HOST=1", "-I" + BENCH,
           os.path.join(ROOT, "tb/unit/printf_diff.c"), io_src,
           "-Wall", "-Wno-format-zero-length"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        return None, r.stdout.decode("utf-8", "replace")
    r = subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.stdout.decode("utf-8", "replace"), None


def compare(out):
    """Returns the list of (case, bench, glibc) that disagree."""
    lines = out.split("\n")
    bad, n = [], 0
    for i in range(0, len(lines) - 1):
        if lines[i].startswith("B|") and lines[i + 1].startswith("G|"):
            n += 1
            if lines[i][2:] != lines[i + 1][2:]:
                bad.append((n, lines[i][2:], lines[i + 1][2:]))
    return n, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true",
                    help="also run the injected-fault cases")
    a = ap.parse_args()

    src = os.path.join(BENCH, "bench_io.c")
    with tempfile.TemporaryDirectory() as td:
        out, err = run_once(src, td)
        if out is None:
            print(err)
            print("PRINTF_FAIL: build failed")
            return 1
        n, bad = compare(out)
        if n == 0:
            print("PRINTF_FAIL: the corpus produced no comparable pairs")
            return 1
        if bad:
            for case, b, g in bad[:20]:
                print("  case %-5d bench |%s|  glibc |%s|" % (case, b, g))
            print("PRINTF_FAIL: %d of %d cases disagree with glibc" % (len(bad), n))
            return 1
        print("printf: %d cases agree with glibc" % n)

        if a.inject:
            text = open(src).read()
            escaped = 0
            for name, pat, rep in INJECTIONS:
                if pat not in text:
                    print("  ESCAPED %-28s : anchor not found in bench_io.c"
                          % name)
                    escaped += 1
                    continue
                mut = os.path.join(td, "bench_io_%s.c" % name)
                with open(mut, "w") as f:
                    f.write(text.replace(pat, rep, 1))
                mout, merr = run_once(mut, td)
                if mout is None:
                    print("  caught  %-28s : does not compile" % name)
                    continue
                _, mbad = compare(mout)
                if mbad:
                    print("  caught  %-28s : %d cases diverge" % (name, len(mbad)))
                else:
                    print("  ESCAPED %-28s : still agrees with glibc" % name)
                    escaped += 1
            if escaped:
                print("PRINTF_FAIL: %d injected fault(s) escaped" % escaped)
                return 1
            print("injection: %d/%d caught" % (len(INJECTIONS), len(INJECTIONS)))

    print("PRINTF_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
