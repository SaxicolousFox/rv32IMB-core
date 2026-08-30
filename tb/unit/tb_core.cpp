// ============================================================================
// A4 testbench: run a program image on rvntt_core_sim_top and check the result.
//
// The core exposes a retirement trace (the same port A5's commit-log differ
// uses), so this testbench never reaches into the register file.  It maintains a
// shadow copy of the architectural registers from the commit stream and compares
// one register against a value supplied on the command line -- computed
// independently by test_core_verilator.py from the program's own data literals,
// and cross-checked against Spike.
//
// HOW IT STOPS, and why that changed at A9.  Until A9 the ECALL retired like
// any other instruction and was the stop marker.  Now it TRAPS: it is squashed
// in EX and never reaches WB, so the old condition can never fire.  There are
// two replacements, and between them they cover everything:
//
//   --stop-pc <addr>  stop at the first commit at <addr>, WITHOUT counting it.
//                     Used with the trap handler's address, which is exactly
//                     where Spike's own trace is truncated -- so the two sides
//                     count the same instructions with no offset to remember.
//   --tohost <addr>   stop on a STORE to <addr>, and report the value written.
//                     This is the riscv-tests protocol, and it works because a
//                     store is issued from EX and nothing past EX is squashed.
//
// Four ways this run can fail, and all four are checked:
//   1. the answer is wrong;
//   2. an instruction retires that this core cannot execute faithfully
//      (dbg_unsupported -- see rvntt_core.sv);
//   3. the program never stops, i.e. it ran off into nothing;
//   4. the RETIRED INSTRUCTION COUNT differs from Spike's.
// A testbench that only checked (1) would report "wrong answer" for all four.
//
// (4) is here because fault injection found (1) insufficient: an off-by-one
// instruction fetch skipped the program's leading `auipc`, and the truncated
// pointer still aliased to the right word because rvntt_ram ignores the high
// address bits by design.  The answer was correct and the instruction count was
// not.  Counting retirements is the cheapest possible shadow of what A5's
// commit-log differ will do properly.
//
// The reported `span` -- the cycle distance from the first retirement to the
// last -- is what tb/cosim/cycle_model.py compares against an independently
// predicted stall and flush count.  Nothing here interprets it; this testbench
// only has to report it honestly, because a phantom stall changes no
// architectural state and a commit-log diff can never see one.
//
// A fifth check has no flag: commit_reg_write must never be asserted with
// commit_rd == 0.  Spike never reports a write to x0, so a trace that did could
// not be compared against it -- and the shadow register file below would hide
// the discrepancy by filtering x0 a second time.
// ============================================================================
// The top module is a compile-time choice: rvntt_core_sim_top for the A4
// checksum run, rvntt_trace_top (the same design plus rvntt_trace) for A5's
// cosimulation.  Their port lists are identical, so one testbench serves both
// and the two runs cannot drift apart.  Select with -CFLAGS -DVTOP=<name>.
#ifndef VTOP
#define VTOP Vrvntt_core_sim_top
#endif
#define VTOP_STR2(x) #x
#define VTOP_STR(x) VTOP_STR2(x)
#include VTOP_STR(VTOP.h)
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>

static VTOP* dut;

static uint32_t xreg[32];       // shadow architectural registers
static long     retired = 0;

// The cycle of the first and last retirement.  Their DIFFERENCE is the useful
// number: it is independent of how long reset is held and of how many cycles
// the pipeline takes to fill, so a cycle model does not have to know either.
// In a pipeline with no stalls the span is exactly retired-1; every stall and
// every flush adds to it, which is what makes it a check on the hazard logic
// rather than on the datapath.  See tb/cosim/cycle_model.py.
static long     first_commit = -1;
static long     last_commit  = -1;

static void tick() {
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);

    // --expect <hex>  : required final value
    // --reg <n>       : which register holds it (default 9 = s1)
    // --max-cycles <n>
    uint32_t expect = 0;
    int      expect_reg = 9;               // s1
    long     max_cycles = 200000;
    long     expect_retired = -1;
    bool     no_check = false;      // trace-only: A5's differ does the checking
    bool     have_expect = false;
    bool     trace = false;
    uint32_t stop_pc = 0;    bool have_stop_pc = false;
    uint32_t tohost  = 0;    bool have_tohost  = false;
    long     expect_tohost = -1;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--expect") && i + 1 < argc) {
            expect = (uint32_t)strtoul(argv[++i], nullptr, 0); have_expect = true;
        } else if (!strcmp(argv[i], "--reg") && i + 1 < argc) {
            expect_reg = atoi(argv[++i]);
        } else if (!strcmp(argv[i], "--expect-retired") && i + 1 < argc) {
            expect_retired = atol(argv[++i]);
        } else if (!strcmp(argv[i], "--max-cycles") && i + 1 < argc) {
            max_cycles = atol(argv[++i]);
        } else if (!strcmp(argv[i], "--no-check")) {
            no_check = true;
        } else if (!strcmp(argv[i], "--trace")) {
            trace = true;
        } else if (!strcmp(argv[i], "--stop-pc") && i + 1 < argc) {
            stop_pc = (uint32_t)strtoul(argv[++i], nullptr, 0);
            have_stop_pc = true;
        } else if (!strcmp(argv[i], "--tohost") && i + 1 < argc) {
            tohost = (uint32_t)strtoul(argv[++i], nullptr, 0);
            have_tohost = true;
        } else if (!strcmp(argv[i], "--expect-tohost") && i + 1 < argc) {
            expect_tohost = atol(argv[++i]);
        }
    }
    if (!have_expect && !no_check && expect_tohost < 0) {
        printf("CORE_TB_FAIL: no --expect or --expect-tohost given\n"); return 1;
    }
    if (!have_stop_pc && !have_tohost) {
        printf("CORE_TB_FAIL: no stop condition (--stop-pc or --tohost)\n");
        return 1;
    }

    dut = new VTOP;
    memset(xreg, 0, sizeof(xreg));

    // Reset: hold for a few cycles, then release.
    dut->rst_n = 0;
    for (int i = 0; i < 5; i++) tick();
    dut->rst_n = 1;

    bool     stopped = false;
    uint32_t tohost_val = 0;
    long cycle = 0;
    for (; cycle < max_cycles && !stopped; cycle++) {
        dut->clk = 0; dut->eval();      // settle combinational outputs

        // The tohost write is checked BEFORE the commit stream, so the store
        // that ends a riscv-tests program stops the run on the cycle it is
        // issued rather than three cycles later when its instruction retires.
        if (have_tohost && dut->dbg_store_be != 0 &&
            (dut->dbg_store_addr & ~3u) == (tohost & ~3u)) {
            tohost_val = dut->dbg_store_data;
            stopped = true;
        }

        if (dut->commit_valid) {
            // --stop-pc is exclusive: the marker instruction is not counted,
            // so `retired` and the span match a Spike trace truncated at the
            // same address with no offset to remember on either side.
            if (have_stop_pc && dut->commit_pc == stop_pc) {
                stopped = true;
                dut->clk = 1; dut->eval();
                break;
            }
            if (dut->dbg_unsupported) {
                printf("CORE_TB_FAIL: unsupported instruction retired at "
                       "pc=0x%08x insn=0x%08x (cycle %ld)\n"
                       "  The A4 core has no control flow, CSRs or coprocessor; "
                       "see rvntt_core.sv.\n",
                       dut->commit_pc, dut->commit_insn, cycle);
                delete dut;
                return 1;
            }
            if (dut->commit_reg_write && dut->commit_rd == 0) {
                printf("CORE_TB_FAIL: commit_reg_write asserted with rd=x0 at "
                       "pc=0x%08x insn=0x%08x\n"
                       "  Spike never reports a write to x0; this trace could "
                       "not be diffed against it.\n",
                       dut->commit_pc, dut->commit_insn);
                delete dut;
                return 1;
            }
            retired++;
            if (first_commit < 0) first_commit = cycle;
            last_commit = cycle;
            if (trace)
                printf("  %6ld  pc=0x%08x insn=0x%08x%s\n", retired,
                       dut->commit_pc, dut->commit_insn,
                       dut->commit_reg_write ? "" : "  (no write)");
            if (dut->commit_reg_write && dut->commit_rd != 0)
                xreg[dut->commit_rd] = dut->commit_wdata;
        }

        dut->clk = 1; dut->eval();      // take the edge
    }

    // --no-check: A5 runs the simulator purely to produce a commit log, and
    // tb/cosim/commit_diff.py is what decides whether it is right.  The
    // structural checks below would need an expected value the differ has not
    // computed, so they are skipped -- but dbg_unsupported and the x0 invariant
    // above still apply, because those are properties of the core rather than
    // of any particular program.
    if (no_check) {
        printf("CORE_TB_TRACE_OK  (%ld instructions retired, %ld cycles, "
               "stopped=%d, span=%ld)\n", retired, cycle, (int)stopped,
               (first_commit < 0) ? -1 : last_commit - first_commit);
        delete dut;
        return stopped ? 0 : 1;
    }

    int rc = 0;
    if (!stopped) {
        printf("CORE_TB_FAIL: the program did not stop within %ld cycles "
               "(%ld instructions retired)\n", max_cycles, retired);
        rc = 1;
    } else if (expect_tohost >= 0 && (long)tohost_val != expect_tohost) {
        // riscv-tests encodes its result here: 1 is pass, and any other odd
        // value is (failing_test_number << 1) | 1.
        printf("CORE_TB_FAIL: tohost = %u, expected %ld", tohost_val,
               expect_tohost);
        if (tohost_val & 1u)
            printf("  (riscv-tests: test case %u failed)", tohost_val >> 1);
        printf("\n");
        rc = 1;
    } else if (expect_retired >= 0 && retired != expect_retired) {
        printf("CORE_TB_FAIL: retired %ld instructions, Spike retired %ld\n"
               "  The answer may still be right -- an instruction stream that is\n"
               "  shifted or truncated can land on the same value by accident.\n",
               retired, expect_retired);
        rc = 1;
    } else if (have_expect && xreg[expect_reg] != expect) {
        printf("CORE_TB_FAIL: x%d = 0x%08x, expected 0x%08x  (xor 0x%08x)\n",
               expect_reg, xreg[expect_reg], expect,
               xreg[expect_reg] ^ expect);
        rc = 1;
    }

    if (rc == 0)
        printf("CORE_TB_OK  x%d = 0x%08x  tohost=%u  (%ld instructions "
               "retired, %ld cycles, span=%ld)\n", expect_reg,
               xreg[expect_reg], tohost_val, retired, cycle,
               (first_commit < 0) ? -1 : last_commit - first_commit);

    delete dut;
    return rc;
}
