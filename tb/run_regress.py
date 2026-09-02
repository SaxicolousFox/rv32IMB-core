#!/usr/bin/env python3
"""
Project regression runner.

Design notes (this is deliberately simple and dependency-free):
  * It must run with the SYSTEM python3, with no venv and no cocotb installed,
    so that `make regress` never fails for reasons unrelated to the DUT.
    Tests whose tools are missing are reported SKIP, not FAIL -- but a SKIP is
    still visible in the table so it can never quietly rot into "we have no tests".
  * Exit code is nonzero if ANY test FAILs (P0.2 acceptance criterion).
  * Every test is a subprocess with a timeout, so a hung simulator cannot wedge
    the whole regression.
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL, SKIP, XFAIL = "PASS", "FAIL", "SKIP", "XFAIL"

@dataclass
class Test:
    name: str
    group: str
    argv: list            # command to run
    cwd: str = ROOT
    timeout: int = 600
    requires: list = field(default_factory=list)   # executables that must exist
    expect_fail: bool = False   # harness self-check: the command MUST return nonzero
    env: dict = field(default_factory=dict)

@dataclass
class Result:
    test: Test
    status: str
    seconds: float
    detail: str = ""
    output: str = ""

def discover() -> list:
    """Test registry. Add tests here as the project grows."""
    py = sys.executable
    t = []

    # ---- P0.2 placeholders that nonetheless exercise the real toolchain ----
    t.append(Test("verilator_lint", "rtl",
                  [py, os.path.join(ROOT, "tb/lint_all.py")],
                  requires=["verilator"], timeout=300))

    t.append(Test("verilator_sim_sync_reset", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_sync_reset_verilator.py")],
                  requires=["verilator"], timeout=300))

    t.append(Test("verilator_sim_blinky", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_blinky_verilator.py")],
                  requires=["verilator"], timeout=600))

    t.append(Test("formal_sync_reset", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_sync_reset"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("cocotb_sync_reset", "cocotb",
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                   "--design", "sync_reset"],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=600))

    t.append(Test("spike_smoke", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/run_spike_smoke.py")],
                  requires=["spike", "riscv-none-elf-gcc"], timeout=300))

    t.append(Test("model_selftest", "model",
                  [py, os.path.join(ROOT, "model/selftest.py")], timeout=120))

    t.append(Test("patches_in_sync", "toolchain",
                  [py, os.path.join(ROOT, "tb/cosim/check_patches.py")], timeout=120))

    t.append(Test("isa_encoding", "isa",
                  [py, os.path.join(ROOT, "tb/unit/test_isa_encoding.py")],
                  requires=["riscv-none-elf-gcc"], timeout=300))

    t.append(Test("isa_semantics", "isa",
                  [py, os.path.join(ROOT, "tb/unit/test_isa_semantics.py")],
                  timeout=900))

    # ---- Track C ----
    t.append(Test("insn_bridge", "toolchain",
                  [py, os.path.join(ROOT, "tb/unit/test_insn_bridge.py")],
                  requires=["riscv-none-elf-gcc", "cc"], timeout=300))

    t.append(Test("spike_xkntt", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_spike_xkntt.py")],
                  requires=["spike", "riscv-none-elf-gcc"], timeout=900))

    # Set KYBER_KAT_FULL=1 in the environment for the full 10000 vectors on
    # Spike (about an hour); the default is a short prefix plus two
    # full-length native runs.
    t.append(Test("kyber_kat_spike", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_kyber_kat_spike.py")],
                  requires=["spike", "riscv-none-elf-gcc", "cc", "make"],
                  timeout=1800))

    t.append(Test("kyber_ref_kats", "model",
                  [py, os.path.join(ROOT, "model/run_kyber_kats.py")], timeout=900))

    t.append(Test("ntt_golden_compare", "model",
                  [py, os.path.join(ROOT, "model/compare_ntt.py"), "-n", "1000"],
                  timeout=900))

    # ---- Track A: the RV32I pipeline ----
    # Kept in its own block so Track B can append its own section without a
    # merge conflict in the middle of this list.
    t.append(Test("verilator_sim_regfile", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_regfile_verilator.py")],
                  requires=["verilator"], timeout=300))

    # Depth 8, not the run_formal.py default of 20.  Every regfile property is
    # combinational except the storage-stability one, which spans two cycles, so
    # 8 is already 4x margin.  Depth matters a lot here: each BMC step adds
    # another symbolic write to a 32x32 memory, and the solve time blows up
    # superlinearly -- depth 8 proves in ~3s, while depth 20 was still grinding
    # on step 13 after eight minutes with nothing further to find.
    t.append(Test("formal_regfile", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_regfile", "--depth", "8"],
                  requires=["sby", "yosys"], timeout=600))

    # A2.  The ALU and immgen are purely combinational, so BMC depth 2 is
    # already more than the properties need -- there is no state to unroll.
    t.append(Test("cocotb_alu", "cocotb",
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                   "--design", "alu"],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=900))

    # Set IMMGEN_EXHAUSTIVE=1 for the additional 2^20 sweeps of the U and J
    # immediate fields (about 25s); the default run covers those formats with
    # walking-bits, which is complete for a wire permutation, plus 10^5 random
    # words.
    t.append(Test("cocotb_immgen", "cocotb",
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                   "--design", "immgen"],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=900))

    t.append(Test("formal_alu", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_alu", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("formal_immgen", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_immgen", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    # A3.  The 10^6-word comparison against the Python decoder dominates the
    # runtime (~40s) and is the acceptance test; it is not sampled down, because
    # the legal/illegal boundary is exactly what a smaller run would under-cover.
    t.append(Test("cocotb_decode", "cocotb",
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                   "--design", "decode"],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=1200))

    t.append(Test("formal_decode", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_decode", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    # A6.  The forwarding unit is combinational, so depth 2 is already more than
    # its properties need.  The properties that matter are priority (the younger
    # producer wins) and completeness (a stale register is never read when a
    # producer is in flight); the rest are soundness and the x0 rule.
    t.append(Test("formal_forward", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_forward", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    # A7.  The interlock is combinational too.  Its two interesting properties
    # are completeness (every real load-use hazard stalls -- the only one whose
    # failure corrupts data) and soundness (nothing else does -- whose failures
    # are phantom stalls, invisible to a commit-log diff).
    t.append(Test("formal_hazard", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_hazard", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    # A8.  The branch comparator's whole content is the signed/unsigned
    # distinction, and the proof attacks exactly that: BLT and BLTU on the same
    # operands must disagree whenever the sign bits differ.  It also covers the
    # two reserved BRANCH encodings, which the decoder rejects and no test can
    # therefore reach -- defence in depth that nothing checks is decoration.
    t.append(Test("formal_branch", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_branch", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    # A9.  The CSR file's proof is about ACCESS RULES rather than storage --
    # read-only enforcement, mepc/mtvec alignment, MPP being fixed, and the
    # mstatus swap on trap and MRET.  Storage is what riscv-tests covers, far
    # better than a property could.  Depth 6 because the trap/MRET properties
    # span two cycles and $past needs a valid history before them.
    t.append(Test("formal_csr", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_csr", "--depth", "6"],
                  requires=["sby", "yosys"], timeout=600))

    # A14/A15.  The multi-cycle unit, standing alone.  DEPTH 37 -- the first
    # proof here whose depth is set by a LATENCY rather than by a dependency
    # distance: a divide presents its result on its 34th EX cycle, so nothing
    # about it is observable before step 34.  This is MODS_A 3.2's route 2, and
    # it exists because riscv-formal cannot reach the divider at all: that check
    # set runs at depth 14 and abstracts this unit's arithmetic away.
    #
    # It proves the MAGNITUDE LOOP completely -- via a per-iteration invariant
    # carried in ghost registers, because the direct algebraic statement asks
    # the solver for multiplier equivalence and does not return.  The sign
    # fixup and the quotient/remainder selection are covered by rv32um,
    # a14_muldiv.S and cosimulation instead; rvntt_muldiv.sv states that
    # boundary.  About three minutes, which is the most expensive proof here.
    t.append(Test("formal_muldiv", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_muldiv", "--depth", "37"],
                  requires=["sby", "yosys"], timeout=1800))

    # A4.  Builds sw/tests/a4_checksum.S, runs it on Spike for the reference,
    # then on the RTL.  Needs the RISC-V toolchain and Spike as well as
    # Verilator, so all three are listed -- a missing one must SKIP loudly
    # rather than silently checking the RTL against nothing.
    t.append(Test("core_a4_checksum", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_core_verilator.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=900))

    # A5.  Lockstep commit-log cosimulation against Spike: the hand-written
    # checksum program plus generated random ones, all diffed line by line.
    #
    # The densities track what the pipeline can execute.  --raw-density 1.0 is
    # A6: no padding at all between a producer and its consumer, so essentially
    # every instruction reads a forwarded operand.  --load-use-density 1.0 is
    # A7: a load's result may be used by the very next instruction, which the
    # interlock covers.  --branch-density is A8, and is the one knob NOT run at
    # 1.0: a program made entirely of branches executes almost nothing, so 0.12
    # is the setting that maximises what actually retires.
    #
    # Each program is checked twice: the commit log against Spike, and the
    # CYCLE SPAN against tb/cosim/cycle_model.py.  A phantom stall produces a
    # byte-identical log, so the first check alone cannot see one.
    #
    # --mul-density is A14 (MODS_A): 0.10 rather than 1.0, because a 34-cycle
    # divide is 34 cycles in which nothing else is exercised -- a denser stream
    # would trade away the hazard coverage the other three knobs buy.  The span
    # check covers M too, via cycle_model.py's Sum(latency-1) term, which is
    # what makes the LATENCY a checked property and not an assumption.
    #
    # 100 programs here; the step's acceptance run is 1000, which takes about
    # two and a half minutes and is not something to pay for on every regress.
    t.append(Test("cosim_commit_log", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_cosim_a5.py"),
                   "-n", "100", "--len", "300",
                   "--raw-density", "1.0", "--load-use-density", "1.0",
                   "--branch-density", "0.12", "--mul-density", "0.10"],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=1800))

    # A6+.  Hand-written programs whose hazard distances are exact.  The random
    # generator covers hazards by volume; these cover the ones that are too
    # specific to appear by chance -- the priority case, the store-data operand,
    # rd == x0 -- and are diffed against Spike by the same code path.
    t.append(Test("cosim_directed", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_cosim_directed.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=900))

    # A9.  Directed CSR and trap cases, and plan A9's own done-when: minstret
    # after a known program equals the instruction count Spike reports for it.
    t.append(Test("csr_traps_minstret", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_csr_traps.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=900))

    # A9, plus A14's rv32um.  The first externally authored suite the core has
    # faced: everything before it was written alongside the design and shares
    # its blind spots.  A14 added the eight M tests, and fault injection has
    # already found two things they cannot see -- a duplicated retirement (the
    # last one writes the right value) and a divide-by-zero remainder returned
    # as a magnitude (their only negative dividend over zero is -2^31, whose
    # magnitude is itself).  See tb/mutate/run_mutation.py.
    # SKIPs itself with an explanatory message if toolchain/riscv-tests is not
    # checked out, rather than failing -- it is a third-party checkout, like
    # spike-src, and is not in the repository.
    t.append(Test("riscv_tests", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_riscv_tests.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=1800))

    # A10.  RISCOF over riscv-arch-test: the official compliance suite, with
    # Spike as the reference model.  SKIPs itself with instructions if either
    # the checkout or the riscof package is missing -- both are third-party and
    # neither is in the repository (toolchain/test-suite-pins.txt reproduces
    # them).  Slow: it compiles and runs every test twice, once per model.
    t.append(Test("riscof_arch_test", "cosim",
                  [py, os.path.join(ROOT, "tb/riscof/run_riscof.py"),
                   "--no-save-report"],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=3600))

    # A11.  riscv-formal: bounded model checking of the whole pipeline against a
    # formal model of the ISA, through the RVFI port in rtl/core/rvntt_rvfi.sv.
    # 43 checks -- 36 instruction models plus reg, pc_fwd, pc_bwd, causal,
    # liveness and unique -- at depth 14, which is about 40s wall on 8 jobs and
    # four minutes of solver time.  That is cheap enough to run every time, and
    # it is the only mechanism here that covers EVERY instruction sequence of
    # its length rather than the ones a generator happened to emit: it found a
    # forwarding/writeback disagreement that no RV32I program can reach.
    #
    # SKIPs itself with a clone command if toolchain/riscv-formal is absent --
    # it is a third-party checkout like riscv-tests and riscv-arch-test, and
    # toolchain/test-suite-pins.txt reproduces it.
    t.append(Test("riscv_formal", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_riscv_formal.py")],
                  requires=["sby", "yosys"], timeout=1800))

    # A12.  The SoC in simulation: rvntt_core + rvntt_ram + memory-mapped UART
    # and GPIO, running the same hello.c image that goes into the bitstream, with
    # the UART decoded off the pin.  This is the gate the board work sits behind
    # -- a memory-mapped UART is testable here long before it is testable on
    # hardware, and getting "Hello" out of Verilator first is what stops a bench
    # session being spent on a software bug.
    t.append(Test("soc_sim", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_soc_verilator.py")],
                  requires=["verilator", "riscv-none-elf-gcc"], timeout=900))

    # The hardware capture parser, checked against itself.  It is the mechanism
    # that decides whether the BOARD passed, so it gets the same fault injection
    # as everything else here: nine deliberately-wrong captures, each of which it
    # must reject.  A parser that has only ever seen good input is `return 0`.
    t.append(Test("soc_uart_parser", "meta",
                  [py, os.path.join(ROOT, "tb/fpga/parse_soc_uart.py"), "--selftest"],
                  timeout=120))

    # Every constraint file's pin assignments, against a pinout extracted
    # mechanically from the vendor's master XDC.  This is here because a
    # hand-read pin table put A12's RGB LED red and blue on each other's pins:
    # it elaborated, met timing, programmed, ran, and produced byte-perfect UART,
    # because a swapped OUTPUT pin is invisible to everything upstream of the
    # pad.  A person looking at the board was the only thing that caught it.
    # Runs its own fault injection first, so it cannot rot into a no-op.
    t.append(Test("xdc_pin_map", "fpga",
                  [py, os.path.join(ROOT, "tb/fpga/check_xdc_pins.py")],
                  timeout=120))

    # A12 on real hardware: program the Arty over JTAG, capture its UART, parse
    # it.  OPT-IN, because running it reconfigures the FPGA and `make regress`
    # should not do that behind your back; it SKIPs with the command to run
    # otherwise, and also SKIPs if the board is unplugged or no bitstream has
    # been built.  Never FAILs for either -- a missing board must not look like a
    # broken design, and must not look like a pass either.
    t.append(Test("soc_hardware", "fpga",
                  [py, os.path.join(ROOT, "fpga/scripts/hw_bringup.py"), "--regress"],
                  timeout=900))

    # A13.  The benchmark port, checked three ways before anything reaches the
    # board, because each way can fail on its own and the symptoms are identical
    # from the outside -- a wrong score.
    #
    #   bench_printf   the formatter, diffed against glibc over 340 cases.  This
    #                  is the only path from a cycle counter to a printed number,
    #                  so a formatting bug and a slow core produce the same
    #                  artefact and nothing else here can separate them.
    #   bench_host     Dhrystone and CoreMark compiled NATIVELY, checking
    #                  CoreMark's own CRCs and Dhrystone's published final values
    #                  in about a second.  If those are wrong on the board and
    #                  right here the core is at fault; if wrong in both, the port
    #                  is.  Without this run those two are one symptom.
    #   bench_sim      the same image on the RTL under Verilator, which is also
    #                  where mcycle is compared against ground truth: Verilator
    #                  counted the clock edges itself, and Spike cannot be the
    #                  reference because its mcycle advances per instruction.
    t.append(Test("bench_printf", "meta",
                  [py, os.path.join(ROOT, "tb/unit/test_bench_printf.py"),
                   "--inject"],
                  timeout=300))

    t.append(Test("bench_host", "sw",
                  [py, os.path.join(ROOT, "tb/unit/test_bench_host.py")],
                  timeout=600))

    t.append(Test("bench_sim", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_bench_verilator.py"),
                   "--blocks", "2"],
                  requires=["verilator", "riscv-none-elf-gcc"], timeout=1800))

    # The benchmark capture parser against itself: twenty deliberately-broken
    # captures it must reject, including two that RELAX a check (--allow-short
    # and --functional-only), because an escape hatch that does not actually
    # open is a second way to pass vacuously.
    t.append(Test("bench_uart_parser", "meta",
                  [py, os.path.join(ROOT, "tb/fpga/parse_bench_uart.py"),
                   "--selftest"],
                  timeout=120))

    # A13 on real hardware, and the only place the reported scores come from.
    # Same opt-in and same SKIP rules as soc_hardware, and the same reason: this
    # reconfigures the FPGA.  --min-blocks 3 is the plan's "reproducible across
    # three runs", enforced rather than eyeballed -- the parser requires the
    # cycle counts to be EXACTLY equal across the three, which on a machine with
    # no cache and no interrupts is the right bar.
    t.append(Test("bench_hardware", "fpga",
                  [py, os.path.join(ROOT, "fpga/scripts/hw_bringup.py"),
                   "--regress",
                   "--bit", os.path.join(ROOT, "fpga/build/bench/rvntt_soc_top.bit"),
                   "--seconds", "60", "--send-byte", "-1",
                   "--out-name", "bench_uart.log",
                   "--parser", os.path.join(ROOT, "tb/fpga/parse_bench_uart.py"),
                   # `--parser-arg=--flag` rather than `--parser-arg --flag`:
                   # argparse reads a value beginning with `-` as the next
                   # option and rejects the separated form.
                   "--parser-arg=--min-blocks", "--parser-arg=3",
                   "--parser-arg=--json",
                   "--parser-arg=" + os.path.join(ROOT, "fpga/build/bench/a13.json")],
                  timeout=1800))

    # Mutation testing (A6+).  ON by default, at about 3m45s -- it rebuilds the
    # simulator once per mutation, and A11's entries add a riscv-formal check
    # each on top, so it is the most expensive thing here by a wide margin.  It is on anyway because it is the only test that checks the
    # OTHER tests, and an unrun mutation manifest rots silently: A6's forwarding
    # proof was checking itself, and A8's directed test never touched the
    # comparator's rs2 port, and neither was visible any other way.  Set
    # RVNTT_NO_MUTATE=1 to skip it during a tight edit loop.
    if os.environ.get("RVNTT_NO_MUTATE") != "1":
        t.append(Test("mutation_pipeline", "meta",
                      [py, os.path.join(ROOT, "tb/mutate/run_mutation.py")],
                      requires=["verilator", "riscv-none-elf-gcc", "spike",
                                "sby", "yosys"],
                      timeout=3600))

    # ---- harness self-check: proves FAIL is actually detected (see P0.2) ----
    t.append(Test("harness_detects_failure", "meta",
                  [py, "-c", "import sys; sys.exit(3)"],
                  expect_fail=True, timeout=30))
    return t

def run_one(tst: Test, verbose: bool) -> Result:
    missing = [r for r in tst.requires if shutil.which(r) is None]
    if missing:
        return Result(tst, SKIP, 0.0, f"missing tool: {','.join(missing)}")
    if not os.path.exists(tst.argv[-1]) and tst.argv[-1].endswith((".sv", ".py")):
        return Result(tst, SKIP, 0.0, f"missing file: {os.path.relpath(tst.argv[-1], ROOT)}")

    env = dict(os.environ); env.update(tst.env)
    t0 = time.time()
    try:
        p = subprocess.run(tst.argv, cwd=tst.cwd, env=env, timeout=tst.timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        rc, out = p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return Result(tst, FAIL, time.time() - t0, f"TIMEOUT after {tst.timeout}s")
    except FileNotFoundError as e:
        return Result(tst, SKIP, time.time() - t0, str(e))
    dt = time.time() - t0

    if tst.expect_fail:
        ok = rc != 0
        detail = "" if ok else "expected nonzero exit, got 0"
        return Result(tst, XFAIL if ok else FAIL, dt, detail, out)
    # A test that exits 0 having decided it could not run is a SKIP, not a pass.
    # Several tests already report this way in their output -- RVTESTS_SKIP,
    # RISCOF_SKIP, RVFORMAL_SKIP, SOC_HW_SKIP -- because a missing third-party
    # checkout or an unplugged board must never FAIL.  Until now the table said
    # PASS for all of them, which is the exact failure mode the docstring at the
    # top of this file warns about: a green row that tested nothing.  Detecting
    # the convention here makes the table match what those tests already say.
    #
    # SHARED WITH TRACK B: this is a change to reporting for every test, not
    # only Track A's.  It is additive -- a test that does not print a *_SKIP:
    # line is unaffected -- but it is worth knowing about.
    for line in out.splitlines():
        line = line.strip()
        if line.split(":", 1)[0].endswith("SKIP") and line.split(":", 1)[0].isupper():
            return Result(tst, SKIP, dt, line.split(":", 1)[-1].strip()[:60], out)

    ok = rc == 0
    return Result(tst, PASS if ok else FAIL, dt, "" if ok else f"exit {rc}", out)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", dest="filter", default=None, help="substring filter on test name")
    ap.add_argument("-v", dest="verbose", action="store_true", help="show output of every test")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    tests = discover()
    if a.filter:
        tests = [t for t in tests if a.filter in t.name or a.filter == t.group]
    if a.list:
        for t in tests:
            print(f"{t.group:8s} {t.name}")
        return 0
    if not tests:
        print("ERROR: no tests matched", file=sys.stderr)
        return 2

    print(f"=== regression: {len(tests)} test(s) ===\n")
    results = []
    for t in tests:
        print(f"  running {t.name} ...", flush=True)
        r = run_one(t, a.verbose)
        results.append(r)
        if a.verbose or r.status == FAIL:
            if r.output:
                print("    " + "\n    ".join(r.output.rstrip().splitlines()[-40:]))

    w = max(len(r.test.name) for r in results)
    print("\n" + "=" * (w + 34))
    print(f"{'TEST'.ljust(w)}  {'GROUP':8s} {'STATUS':7s} {'TIME':>7s}  DETAIL")
    print("-" * (w + 34))
    for r in results:
        print(f"{r.test.name.ljust(w)}  {r.test.group:8s} {r.status:7s} "
              f"{r.seconds:6.2f}s  {r.detail}")
    print("=" * (w + 34))

    n = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP, XFAIL)}
    print(f"passed {n[PASS]}   xfail {n[XFAIL]}   skipped {n[SKIP]}   FAILED {n[FAIL]}")
    if n[SKIP]:
        print("note: SKIPs are missing tools, not passes -- see status above.")
    if n[FAIL]:
        print("\nREGRESSION FAILED")
        return 1
    print("\nREGRESSION PASSED")
    return 0

if __name__ == "__main__":
    sys.exit(main())
