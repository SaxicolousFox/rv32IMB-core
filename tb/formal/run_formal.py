#!/usr/bin/env python3
"""
Drive SymbiYosys on one RTL design.

Solver choice: bitwuzla is primary (maintained successor to boolector, strongest
on the QF_ABV queries riscv-formal will generate in A11).  --solver lets you
cross-check with z3, which is an independent codebase -- useful when a proof
result is surprising.
"""
import argparse, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SEARCH = ["rtl/common", "rtl/core", "rtl/ntt", "rtl/soc"]

def find_rtl(design):
    for d in SEARCH:
        p = os.path.join(ROOT, d, design + ".sv")
        if os.path.exists(p):
            return p
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", required=True)
    ap.add_argument("--solver", default="bitwuzla", choices=["bitwuzla", "boolector", "z3"])
    ap.add_argument("--depth", type=int, default=20)
    ap.add_argument("--mode", default="bmc", choices=["bmc", "prove"])
    a = ap.parse_args()

    rtl = find_rtl(a.design)
    if rtl is None:
        print(f"ERROR: no RTL found for design '{a.design}' under {SEARCH}", file=sys.stderr)
        return 2

    workdir = os.path.join(ROOT, "tb/formal", a.design)
    shutil.rmtree(workdir, ignore_errors=True)
    sby = os.path.join(ROOT, "tb/formal", f"{a.design}.sby")

    with open(sby, "w") as f:
        f.write(f"""[options]
mode {a.mode}
depth {a.depth}

[engines]
smtbmc {a.solver}

[script]
read -define FORMAL
read -formal {os.path.basename(rtl)}
prep -top {a.design}

[files]
{rtl}
""")
    r = subprocess.run(["sby", "-f", sby], cwd=os.path.join(ROOT, "tb/formal"),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    tail = [l for l in out.splitlines() if "summary:" in l or "DONE" in l]
    print("\n".join(tail) if tail else out)
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
