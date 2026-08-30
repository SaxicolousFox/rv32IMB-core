#!/usr/bin/env python3
"""
Drive RISCOF over the riscv-arch-test RV32I suite (plan A10).

Reference model: SPIKE.  Sail is the framework's default, building it is a
large detour, and the plan names Spike as the alternative -- which is also
already this project's golden model, for A5's lockstep cosimulation and for the
whole C track.  One reference across the project beats a second opinion nobody
else uses.

riscv-arch-test ships a `spike_simple` plugin that would have done, but it reads
`ispec['PMP']` unconditionally and riscv-config 3.18 has no such key, so using
it would mean writing this core's ISA description in an older schema than the
installed validator accepts.  tb/riscof/spike_ref is a thin replacement derived
from it; see that file for the two deliberate differences.

ON RISCOF ITSELF.  Upstream deprecated it: riscv-arch-test's default branch has
moved to the "ACT4" framework, which replaces RISCOF and requires the Sail model
plus a UDB configuration.  The RISCOF-era suite is still maintained on the
`old-framework-3.x` branch, which is what toolchain/riscv-arch-test is pinned to
and what this runs.  That is a deliberate choice, not an oversight: the plan's
A10 asks for RISCOF specifically and for its HTML report, and ACT4 produces
neither.  Moving to ACT4 is a real piece of work -- a Sail build and a UDB
config -- and it belongs to whoever wants the current certification flow rather
than to A10.
"""
import argparse
import collections
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb/unit"))
import test_core_verilator as t4   # noqa: E402

ARCHTEST = os.path.join(ROOT, "toolchain/riscv-arch-test")
PLUGINS = os.path.join(ARCHTEST, "riscof-plugins/rv32")
HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DEST = os.path.join(ROOT, "docs/riscof-report.html")

# 2 MB of simulated memory, against 64 KB everywhere else in this repo.
# THE BRANCH AND JUMP TESTS NEED IT: they walk the whole immediate range, so
# beq-01 links to 0x8003aa28 and jal-01 -- exercising JAL's plus or minus 1 MB
# -- to 0x801af18c.  With a 64 KB array the image is silently truncated and the
# core runs off into unwritten memory, which looks like a branch bug and is not
# one.
WORDS = 524288

# Suites RISCOF selects for an RV32I+Zicsr core but which this core cannot
# support.  Excluded by name, with a reason, rather than left to fail: a
# compliance report with 43 known failures in it is a report nobody reads.
EXCLUDED_SUITES = {
    "pmp": "physical memory protection. Plan 1.5 excludes PMP and rvntt_csr.sv "
           "implements no pmpcfg*/pmpaddr*. These tests carry "
           "`verify (PMP['implemented'])` in their selection clause, but riscof "
           "1.25.3 does not implement `verify` at all -- it filters on the ISA "
           "regex alone -- so they are selected for any RV32I core and must be "
           "excluded here instead.",
}

CONFIG = """[RISCOF]
ReferencePlugin=spike_ref
ReferencePluginPath={here}/spike_ref
DUTPlugin=rvntt
DUTPluginPath={here}/rvntt

[rvntt]
pluginpath={here}/rvntt
ispec={here}/rvntt/rvntt_isa.yaml
pspec={here}/rvntt/rvntt_platform.yaml
simexe={simexe}
image={image}
target_run=1

[spike_ref]
pluginpath={here}/spike_ref
jobs=4
"""


def build_sim(tmp, image_path, rtl_dir=None):
    """
    Build the simulator.  `rtl_dir` points at a MIRRORED copy of the RTL tree,
    which is how this harness is fault-injected: run it against a deliberately
    broken core and it must report RISCOF_FAIL.  A compliance runner that cannot
    fail is the most expensive kind of green tick there is -- the first version
    of this script printed RISCOF_OK over 50 real failures because it trusted
    riscof's exit code.
    """
    build = os.path.join(tmp, "obj")
    srcs = t4.RTL if rtl_dir is None else [
        os.path.join(rtl_dir, os.path.relpath(p, ROOT)) for p in t4.RTL]
    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "--top-module", "rvntt_core_sim_top",
           "--Mdir", build, "--prefix", "Vrvntt_core_sim_top",
           '-GINIT_FILE="%s"' % image_path,
           "-GWORDS=%d" % WORDS] + srcs + [t4.TB]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError("building the simulator failed")
    return os.path.join(build, "Vrvntt_core_sim_top")


def filter_testlist(path):
    """Drop the excluded suites from riscof's generated test list, in place."""
    text = open(path).read()
    # The list is YAML with one top-level key per test, each holding a
    # `test_path`.  Splitting on the top-level keys is enough and avoids
    # depending on a YAML library's round-tripping of riscof's own formatting.
    blocks, cur = [], []
    for line in text.splitlines(True):
        if line and not line[0].isspace() and cur:
            blocks.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)

    out = []
    for b in blocks:
        body = "".join(b)
        m = re.search(r"rv32i_m/([A-Za-z0-9_]+)/src/", body)
        if m and m.group(1) in EXCLUDED_SUITES:
            continue
        out.append(body)
    text_out = "".join(out)
    open(path, "w").write(text_out)
    # Counted by `test_path:` rather than by block, because a block is whatever
    # the splitter above decided and one test is exactly one test_path.  The
    # numbers are printed, so a wrong one is a wrong claim.
    return text_out.count("test_path"), text.count("test_path") - text_out.count("test_path")


def parse_report(path):
    """(passed, failed, {suite: (passed, failed)}) from RISCOF's HTML report."""
    h = open(path).read()
    per = collections.defaultdict(lambda: [0, 0])
    passed = failed = 0
    for m in re.finditer(r">\s*(Passed|Failed)\s*<", h):
        seg = h[max(0, m.start() - 3000):m.start()]
        names = re.findall(r"rv32i_m/([A-Za-z0-9_]+)/src/", seg)
        suite = names[-1] if names else "?"
        if m.group(1) == "Passed":
            passed += 1
            per[suite][0] += 1
        else:
            failed += 1
            per[suite][1] += 1
    return passed, failed, {k: tuple(v) for k, v in per.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", default=None,
                    help="keep the RISCOF work directory here")
    ap.add_argument("--no-save-report", action="store_true",
                    help="do not copy the HTML report into docs/")
    ap.add_argument("--rtl-dir", default=None,
                    help="build from this mirrored RTL tree instead of rtl/ "
                         "(used to fault-inject this harness)")
    a = ap.parse_args()

    if not os.path.isdir(ARCHTEST):
        print("RISCOF_SKIP: toolchain/riscv-arch-test is not checked out.\n"
              "  git clone --depth 1 --branch old-framework-3.x \\\n"
              "      https://github.com/riscv-non-isa/riscv-arch-test.git \\\n"
              "      toolchain/riscv-arch-test")
        return 0
    if shutil.which("riscof") is None:
        print("RISCOF_SKIP: riscof is not installed in the venv.\n"
              "  toolchain/opt/uv-*/uv pip install --python .venv/bin/python riscof")
        return 0

    tmp = a.keep or tempfile.mkdtemp(prefix="riscof_")
    os.makedirs(tmp, exist_ok=True)
    try:
        image = os.path.join(tmp, "image.hex")
        open(image, "w").write("00000000\n")
        exe = build_sim(tmp, image, a.rtl_dir)

        cfg = os.path.join(tmp, "config.ini")
        open(cfg, "w").write(CONFIG.format(plugins=PLUGINS, here=HERE,
                                           simexe=exe, image=image))

        work = os.path.join(tmp, "riscof_work")
        suite = os.path.join(ARCHTEST, "riscv-test-suite/")
        env = os.path.join(ARCHTEST, "riscv-test-suite/env")
        common = ["--config", cfg, "--suite", suite, "--env", env,
                  "--work-dir", work]

        # Two steps rather than one, so the excluded suites can be dropped
        # between them.  `riscof testlist` applies the ISA filtering and writes
        # test_list.yaml; `riscof run --testfile` consumes it.
        r = subprocess.run(["riscof", "testlist"] + common, cwd=tmp,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(r.stdout.decode("utf-8", "replace")[-4000:])
            print("RISCOF_FAIL: could not generate the test list")
            return 1

        testfile = os.path.join(work, "test_list.yaml")
        kept, dropped = filter_testlist(testfile)
        print("selected %d tests; %d excluded by suite" % (kept, dropped))

        r = subprocess.run(["riscof", "run"] + common +
                           ["--testfile", testfile, "--no-browser"],
                           cwd=tmp, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        out = r.stdout.decode("utf-8", "replace")

        report = os.path.join(work, "report.html")
        if not os.path.exists(report):
            print(out[-4000:])
            print("RISCOF_FAIL: no report was generated")
            return 1

        # A run against mutated RTL must never overwrite the saved report.
        if not a.no_save_report and a.rtl_dir is None:
            os.makedirs(os.path.dirname(REPORT_DEST), exist_ok=True)
            shutil.copy(report, REPORT_DEST)

        passed, failed, per_suite = parse_report(report)
        for name in sorted(per_suite):
            p, f = per_suite[name]
            print("  %-12s %3d passed, %3d failed" % (name, p, f))
        for name, why in sorted(EXCLUDED_SUITES.items()):
            print("  %-12s EXCLUDED  %s" % (name, why))
        print("riscof: %d passed, %d failed" % (passed, failed))
        if not a.no_save_report and a.rtl_dir is None:
            print("report saved to " + os.path.relpath(REPORT_DEST, ROOT))

        # riscof's own exit code is NOT the verdict: it returns 0 for a run in
        # which tests failed, and the first version of this script reported
        # RISCOF_OK over 50 failures because of it.  The report is the verdict.
        if failed or passed == 0 or r.returncode != 0:
            print("\nRISCOF_FAIL")
            if passed == 0:
                print(out[-3000:])
            return 1
        print("RISCOF_OK")
        return 0
    finally:
        if a.keep is None:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
