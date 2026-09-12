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
sys.path.insert(0, os.path.join(ROOT, "tb"))
from rtl_deps import with_deps   # noqa: E402

SEARCH = ["rtl/common", "rtl/core", "rtl/soc"]

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
    # A19.  rvntt_bpred's properties are about the MECHANISM -- an aligned
    # target, a saturating counter, a bounded return stack -- and none of them
    # says anything about a particular entry, so the proof runs the module with
    # eight BTB entries instead of 256 rather than making the solver carry
    # 14 kbit of array it will never look at.  Shrinking a design for a proof is
    # only honest when the property does not depend on the size; that argument
    # is in rvntt_bpred.sv's FORMAL header, next to the properties it excuses.
    # A29.  Every design proved here before rvntt_seed was a LEAF module, so
    # `with_deps` -- which resolves package imports and nothing else -- was
    # enough.  rvntt_seed instantiates two submodules, and Yosys's error for a
    # missing one ("is not part of the design") arrives as sby rc=16 rather than
    # as a failed assertion, i.e. as an ERROR and not a FAIL.  Worth knowing:
    # the runner already treats a nonzero return as failure, so this could not
    # have passed vacuously -- but it could easily have been read as "the proof
    # is broken" rather than "a file is missing".
    ap.add_argument("--extra", action="append", default=[],
                    help="additional RTL sources the top instantiates "
                         "(repeatable; paths relative to the repo root)")
    ap.add_argument("--param", action="append", default=[],
                    metavar="NAME=VALUE",
                    help="override a module parameter for the proof")
    a = ap.parse_args()

    rtl = find_rtl(a.design)
    if rtl is None:
        print(f"ERROR: no RTL found for design '{a.design}' under {SEARCH}", file=sys.stderr)
        return 2

    workdir = os.path.join(ROOT, "tb/formal", a.design)
    shutil.rmtree(workdir, ignore_errors=True)
    sby = os.path.join(ROOT, "tb/formal", f"{a.design}.sby")

    # Any package the design imports must be read BEFORE it -- Yosys, like
    # Verilator, requires a package to be declared before it is referenced, and
    # a module whose port list uses a package type otherwise fails to parse.
    srcs = with_deps(rtl)
    for e in a.extra:
        p = e if os.path.isabs(e) else os.path.join(ROOT, e)
        if not os.path.exists(p):
            raise SystemExit("FORMAL_FAIL: --extra %s does not exist" % e)
        if p not in srcs:
            srcs.append(p)
    reads = "\n".join(f"read -formal {os.path.basename(s)}" for s in srcs)
    params = ""
    if a.param:
        sets = " ".join("-set %s %s" % tuple(p.split("=", 1)) for p in a.param)
        params = f"chparam {sets} {a.design}\n" 

    with open(sby, "w") as f:
        f.write(f"""[options]
mode {a.mode}
depth {a.depth}

[engines]
smtbmc {a.solver}

[script]
read -define FORMAL
{reads}
{params}prep -top {a.design}

[files]
{chr(10).join(srcs)}
""")
    r = subprocess.run(["sby", "-f", sby], cwd=os.path.join(ROOT, "tb/formal"),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    tail = [l for l in out.splitlines() if "summary:" in l or "DONE" in l]
    print("\n".join(tail) if tail else out)
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
