#!/usr/bin/env python3
"""
Spike smoke test.

  1. the toolchain builds a bare-metal RV32I ELF,
  2. Spike executes it and exits 0 via HTIF,
  3. Spike reports FAILURE for a deliberately wrong program,
  4. --log-commits emits the commit-log format the differ consumes.
"""
import os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC  = os.path.join(ROOT, "sw/tests/spike_smoke.S")
LD   = os.path.join(ROOT, "sw/tests/link.ld")
ISA  = "rv32i_zicsr"

# `core   0: 3 0x80000000 (0x00500513) x10 0x00000005`
COMMIT_RE = re.compile(r"^core\s+\d+:\s+\d+\s+0x[0-9a-f]+\s+\(0x[0-9a-f]+\)")


def build(src, out):
    r = subprocess.run(
        ["riscv-none-elf-gcc", "-march=rv32i", "-mabi=ilp32",
         "-nostdlib", "-nostartfiles", "-T", LD, "-o", out, src],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="spike_smoke_")
    fails = []

    good = os.path.join(tmp, "good.elf")
    rc, out = build(SRC, good)
    if rc != 0:
        print(out); print("SPIKE_SMOKE_FAIL: build"); return 1

    # 1/2. positive control
    r = subprocess.run(["spike", f"--isa={ISA}", good],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        fails.append(f"positive control exited {r.returncode}: "
                     f"{r.stdout.decode('utf-8','replace').strip()}")

    # 3. negative control -- a wrong program MUST be reported as failing.
    bad_src = os.path.join(tmp, "bad.S")
    with open(SRC) as f:
        text = f.read()
    with open(bad_src, "w") as f:
        f.write(text.replace("li      a3, 12", "li      a3, 99"))
    bad = os.path.join(tmp, "bad.elf")
    rc, out = build(bad_src, bad)
    if rc != 0:
        print(out); print("SPIKE_SMOKE_FAIL: negative-control build"); return 1
    r = subprocess.run(["spike", f"--isa={ISA}", bad],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode == 0:
        fails.append("negative control PASSED on spike -- failure detection is broken")

    # 4. commit log format
    r = subprocess.run(["spike", f"--isa={ISA}", "--log-commits", good],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    commits = [l for l in lines if COMMIT_RE.match(l)]
    if len(commits) < 5:
        fails.append(f"--log-commits produced only {len(commits)} parseable lines")
    if not any("0x80000000" in l for l in commits):
        fails.append("no commit at the program entry 0x80000000")

    for f_ in fails:
        print("  FAIL:", f_)
    if fails:
        print(f"SPIKE_SMOKE_FAIL ({len(fails)})")
        return 1
    print(f"SPIKE_SMOKE_OK ({len(commits)} commit-log lines parsed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
