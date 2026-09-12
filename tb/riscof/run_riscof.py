#!/usr/bin/env python3
"""
Drive RISCOF over the riscv-arch-test suites.

Reference model: Spike, which is already this project's golden model.
tb/riscof/spike_ref is a thin plugin derived from riscv-arch-test's
`spike_simple`, which reads `ispec['PMP']` unconditionally and riscv-config
3.18 has no such key.

Upstream has deprecated RISCOF in favour of ACT4 (Sail plus a UDB config);
toolchain/riscv-arch-test is pinned to the maintained `old-framework-3.x`
branch, which is what this runs.
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
REPORT_DEST = os.path.join(ROOT, "build/riscof-report.html")

# 2 MB of simulated memory: the branch and jump tests walk the whole immediate
# range (jal-01 links to 0x801af18c), and a smaller array silently truncates
# the image.
WORDS = 524288

# Suites RISCOF selects for this ISA but which the core cannot support,
# excluded by name with a reason.
EXCLUDED_SUITES = {
    "pmp": "physical memory protection. Plan 1.5 excludes PMP and rvntt_csr.sv "
           "implements no pmpcfg*/pmpaddr*. These tests carry "
           "`verify (PMP['implemented'])` in their selection clause, but riscof "
           "1.25.3 does not implement `verify` at all -- it filters on the ISA "
           "regex alone -- so they are selected for any RV32I core and must be "
           "excluded here instead.",
}

# Individual tests excluded from suites that are otherwise kept.  The
# arch-test `B` directory is the old grouping and ships clmul, clmulh and
# clmulr, which are Zbc; the result is reported as 29/29 of the ratified-B
# tests with 3 Zbc tests excluded, never as "B passes".
EXCLUDED_TESTS = {
    ("B", "clmul-01"):  "Zbc, not ratified B. The arch-test B directory is the "
                        "pre-split grouping; clmul/clmulh/clmulr are Zbc and "
                        "this core implements Zba+Zbb+Zbs only.",
    ("B", "clmulh-01"): "Zbc, not ratified B -- see clmul-01.",
    ("B", "clmulr-01"): "Zbc, not ratified B -- see clmul-01.",
}

# Suites where only a named subset is kept.  The arch-test `K` directory has
# 55 tests, of which five (pack, packh, brev8, zip, unzip) are the Zbkb
# instructions Zbb does not already cover; the rest are AES/SHA/SM3/SM4.
SUITE_KEEP_ONLY = {
    "K": ({"pack-01", "packh-01", "brev8_32-01", "zip-01", "unzip-01"},
          "the Zbkb subset. The rest of K is AES/SHA/SM3/SM4, which this core "
          "does not implement -- see rvntt_isa.yaml's ISA string."),
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
    Build the simulator.  `rtl_dir` points at a mirrored copy of the RTL tree,
    which is how the mutation harness fault-injects this runner.
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
    # YAML with one top-level key per test, each holding a `test_path`.
    # Split on the top-level keys rather than round-trip through a YAML library.
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
        m = re.search(r"rv32i_m/([A-Za-z0-9_]+)/src/([A-Za-z0-9_.\-]+)\.S", body)
        if m:
            suite, test = m.group(1), m.group(2)
            if suite in EXCLUDED_SUITES:
                continue
            if (suite, test) in EXCLUDED_TESTS:
                continue
            keep = SUITE_KEEP_ONLY.get(suite)
            if keep is not None and test not in keep[0]:
                continue
        else:
            # A block whose path does not parse is not silently kept: the whole
            # point of this filter is that what runs is known by name.
            m2 = re.search(r"rv32i_m/([A-Za-z0-9_]+)/src/", body)
            if m2 and m2.group(1) in EXCLUDED_SUITES:
                continue
        out.append(body)
    text_out = "".join(out)
    open(path, "w").write(text_out)
    # Counted by `test_path:` rather than by block.
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


ISA_YAML = os.path.join(HERE, "rvntt/rvntt_isa.yaml")
CSR_SV   = os.path.join(ROOT, "rtl/core/rvntt_csr.sv")


def check_isa_consistency():
    """The ISA this core claims is written in two files.  Make them agree.

    The yaml's ISA/misa and rvntt_csr.sv's MISA_VALUE cannot be derived from
    each other, so they are compared.  A narrower reference ISA does not fail:
    Spike traps to an unset handler and spins until the plugin's timeout.
    """
    with open(ISA_YAML) as f:
        yaml_text = f.read()
    m = re.search(r"^\s*ISA:\s*(\S+)", yaml_text, re.M)
    if not m:
        raise SystemExit("RISCOF_FAIL: no `ISA:` in %s" % ISA_YAML)
    isa = m.group(1)
    m = re.search(r"^\s*reset-val:\s*(0x[0-9a-fA-F]+)", yaml_text, re.M)
    if not m:
        raise SystemExit("RISCOF_FAIL: no misa `reset-val:` in %s" % ISA_YAML)
    yaml_misa = int(m.group(1), 16)

    # misa: bits 31:30 are MXL (1 = RV32), and bit (letter - 'A') is set for
    # each single-letter extension.  Z* and X* extensions have no bit.
    letters = re.match(r"RV32([A-WY]*)", isa.upper())
    want = 0x4000_0000
    for c in (letters.group(1) if letters else "I"):
        want |= 1 << (ord(c) - ord("A"))
    if yaml_misa != want:
        raise SystemExit(
            "RISCOF_FAIL: %s says ISA %s but misa reset-val 0x%08x; the "
            "extension letters imply 0x%08x" % (ISA_YAML, isa, yaml_misa, want))

    with open(CSR_SV) as f:
        m = re.search(r"MISA_VALUE\s*=\s*32'h([0-9a-fA-F_]+)", f.read())
    if not m:
        raise SystemExit("RISCOF_FAIL: no MISA_VALUE in %s" % CSR_SV)
    rtl_misa = int(m.group(1).replace("_", ""), 16)
    if rtl_misa != yaml_misa:
        raise SystemExit(
            "RISCOF_FAIL: misa disagrees between the compliance description and "
            "the hardware -- %s says 0x%08x, %s says 0x%08x.  RISCOF selects "
            "tests from the first and the core answers with the second, so a "
            "mismatch either runs tests the core cannot execute or silently "
            "drops coverage." % (ISA_YAML, yaml_misa, CSR_SV, rtl_misa))
    print("ISA claim: %s, misa 0x%08x -- yaml and rvntt_csr.sv agree"
          % (isa, yaml_misa))
    return isa, yaml_misa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", default=None,
                    help="keep the RISCOF work directory here")
    ap.add_argument("--no-save-report", action="store_true",
                    help="do not copy the HTML report into build/")
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

    check_isa_consistency()

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
        # The per-test exclusions are printed too, so "B 29 passed" says what
        # was not run.
        for (suite, test), why in sorted(EXCLUDED_TESTS.items()):
            print("  %-12s EXCLUDED  %s -- %s" % (suite, test, why))
        for suite, (keep, why) in sorted(SUITE_KEEP_ONLY.items()):
            print("  %-12s KEPT ONLY %s -- %s"
                  % (suite, ", ".join(sorted(keep)), why))
        print("riscof: %d passed, %d failed" % (passed, failed))
        # Stated in the form the claim will be made in.
        if "B" in per_suite:
            print("  NOTE: B is 29/29 of the RATIFIED-B tests (Zba+Zbb+Zbs). "
                  "The arch-test B directory also ships 3 Zbc tests "
                  "(clmul/clmulh/clmulr), which are NOT part of ratified B and "
                  "are excluded above.  Report this as 29/29 ratified-B, never "
                  "as 32/32 and never as \"B passes\".")
        if not a.no_save_report and a.rtl_dir is None:
            print("report saved to " + os.path.relpath(REPORT_DEST, ROOT))

        # riscof's exit code is not the verdict: it returns 0 for a run in
        # which tests failed.  The report is the verdict.
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
