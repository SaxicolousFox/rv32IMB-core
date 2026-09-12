// ============================================================================
// Runs a program image on rvntt_core_sim_top and checks the result.
//
// The core exposes a retirement trace, so this never reaches into the
// register file: it keeps a shadow copy of the architectural registers from
// the commit stream and compares one register against a value supplied on the
// command line (computed by test_core_verilator.py and cross-checked against
// Spike).
//
// Stop conditions (the ECALL traps, so it never retires):
//   --stop-pc <addr>  stop at the first commit at <addr>, without counting it
//                     (the trap handler's address, where Spike's trace is also
//                     truncated, so both sides count the same instructions).
//   --tohost <addr>   stop on a store to <addr> and report the value written
//                     (the riscv-tests protocol; sound because a store issues
//                     from EX and nothing past EX is squashed).
//
// Checked: the answer, dbg_unsupported, that the program stops, the retired
// instruction count against Spike's, and that commit_reg_write is never
// asserted with commit_rd == 0.  The reported `span` (first to last
// retirement, in cycles) is what tb/cosim/cycle_model.py compares against an
// independent stall and flush prediction.
//
// The top module is a compile-time choice (-CFLAGS -DVTOP=<name>):
// rvntt_core_sim_top, or rvntt_trace_top for cosimulation.  Their port lists
// are identical.
// ============================================================================
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

// The cycle of the first and last retirement.  Their difference is
// independent of reset length and pipeline fill; with no stalls it is exactly
// retired-1, and every stall and flush adds to it.
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
    const char* store_log = nullptr;
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
        } else if (!strcmp(argv[i], "--store-log") && i + 1 < argc) {
            store_log = argv[++i];
        }
    }
    if (!have_expect && !no_check && expect_tohost < 0) {
        printf("CORE_TB_FAIL: no --expect or --expect-tohost given\n"); return 1;
    }
    if (!have_stop_pc && !have_tohost) {
        printf("CORE_TB_FAIL: no stop condition (--stop-pc or --tohost)\n");
        return 1;
    }

    // --store-log records every committed store as "<addr> <be> <data>";
    // RISCOF replays them onto the program's image to reconstruct the
    // signature without reaching inside the RAM.
    FILE* slog = nullptr;
    if (store_log) {
        slog = fopen(store_log, "w");
        if (!slog) {
            printf("CORE_TB_FAIL: cannot open store log %s\n", store_log);
            return 1;
        }
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

        // The tohost write is checked before the commit stream, so the run
        // stops on the cycle the store is issued.
        if (slog && dut->dbg_store_be != 0)
            fprintf(slog, "%08x %x %08x\n", dut->dbg_store_addr,
                    dut->dbg_store_be, dut->dbg_store_data);

        if (have_tohost && dut->dbg_store_be != 0 &&
            (dut->dbg_store_addr & ~3u) == (tohost & ~3u)) {
            tohost_val = dut->dbg_store_data;
            stopped = true;
        }

        if (dut->commit_valid) {
            // --stop-pc is exclusive: the marker instruction is not counted,
            // matching a Spike trace truncated at the same address.
            if (have_stop_pc && dut->commit_pc == stop_pc) {
                stopped = true;
                dut->clk = 1; dut->eval();
                break;
            }
            if (dut->dbg_unsupported) {
                printf("CORE_TB_FAIL: unsupported instruction retired at "
                       "pc=0x%08x insn=0x%08x (cycle %ld)\n"
                       "  see dbg_unsupported in rvntt_core.sv.\n",
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

    // --no-check: the run only produces a commit log for tb/cosim/commit_diff.py.
    // dbg_unsupported and the x0 invariant above still apply.
    if (slog) fclose(slog);

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
