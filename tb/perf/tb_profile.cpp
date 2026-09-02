// ============================================================================
// A18 -- the stall attribution instrument.
//
// MODS_A A18 asks for "a profiling harness over the RTL", and this is the RTL
// half of it: it runs the A13/A16 benchmark image on rvntt_soc_sim_top exactly
// as tb/unit/tb_bench.cpp does, and additionally counts, every cycle, where the
// cycles went.
//
// WHY A SEPARATE TESTBENCH RATHER THAN A FLAG ON tb_bench.cpp.  Reading the
// core's internal signals from C++ requires Verilator's --public-flat-rw, which
// suppresses the optimisations that make the regression's bench_sim fast.  A
// flag would not help: the C++ cannot reference a symbol that the flag did not
// create, so the flag would have to be on for every build.  This file is the
// price of keeping bench_sim at its current runtime.
//
// WHY IT DOES NOT ADD COUNTERS TO THE CORE.  Hardware counters would perturb
// the very thing A17 just spent four implementation runs improving, and they
// are not needed: this SoC is deterministic to the cycle and the simulation
// reproduces the board's mcycle and minstret exactly -- MODS_A section 1's own
// baseline table was produced this way.  The instrument prints the mcycle value
// it observed at each region boundary so that claim is checked, not assumed.
//
// THE REGION BOUNDARIES ARE THE SOFTWARE'S OWN.  sw/bench reads mcycle before
// and after each timed region, so every `csrr rd, mcycle` that retires is a
// point the software can name.  This records the mcycle VALUE the core returned
// at each one, alongside the instrument's counters; tb/perf/run_stall_profile.py
// then matches the values the benchmark printed over the UART.  Nothing has to
// guess where a region starts, and no software or RTL change was needed to say
// so.
// ============================================================================
#include "Vrvntt_soc_sim_top.h"
#include "Vrvntt_soc_sim_top___024root.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <deque>

static const int BIT_CYCLES = 34;     // CORE_HZ / BAUD = 4e6 / 115200 -> 34

#define SIG(n) (dut->rootp->rvntt_soc_sim_top__DOT__u_soc__DOT__u_core__DOT__##n)

// `csrr rd, mcycle` is CSRRS rd, 0xB00, x0.  Everything but rd is fixed.
static const unsigned MCYCLE_INSN = 0xB0002073u;
static const unsigned RD_MASK     = 0x00000F80u;

struct Snap {
    unsigned long long mcycle;
    long long cycle, retired, id_stall, ex_stall, redirect;
    long long br_taken, br_ntaken, jal, jalr;
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);

    const char* out_path  = "bench_sim.log";
    const char* prof_path = "prof.csv";
    const char* trace_path = 0;
    long max_cycles = 400L * 1000 * 1000;
    int  want_blocks = 1;
    // WHERE A REDIRECT'S COST ACTUALLY LANDS.  ex_redirect at cycle t clears
    // IF/ID and ID/EX, so the two cycles in which nothing retires are LATER
    // than the pulse.  Charging them to the pulse's cycle makes the per-region
    // identity wrong by exactly 2 whenever a redirect fires within a few
    // cycles of a region boundary -- which never happened before A19 and does
    // now, because a not-taken branch predicted taken is a redirect at a pc
    // that never had one.  The delay is swept and MEASURED rather than
    // reasoned about; see docs/a18-stalls.md.
    int  redir_delay = 3;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--out") && i + 1 < argc)         out_path = argv[++i];
        else if (!strcmp(argv[i], "--prof") && i + 1 < argc)   prof_path = argv[++i];
        else if (!strcmp(argv[i], "--trace") && i + 1 < argc)  trace_path = argv[++i];
        else if (!strcmp(argv[i], "--max") && i + 1 < argc)    max_cycles = atol(argv[++i]);
        else if (!strcmp(argv[i], "--blocks") && i + 1 < argc) want_blocks = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--redir-delay") && i + 1 < argc) redir_delay = atoi(argv[++i]);
    }

    Vrvntt_soc_sim_top* dut = new Vrvntt_soc_sim_top;
    std::string rx;
    int state = 0, countdown = 0, bit_i = 0, prev_tx = 1;
    unsigned ch = 0;
    int blocks = 0;
    bool saw_err = false;
    long last_progress = 0;
    size_t last_len = 0;

    long long n_cycle = 0, n_retired = 0, n_id = 0, n_ex = 0, n_redir = 0;
    std::vector<int> redir_pipe(redir_delay > 0 ? redir_delay : 1, 0);
    long long n_bt = 0, n_bn = 0, n_jal = 0, n_jalr = 0;
    // The previous retirement, kept so a branch can be classified taken or
    // not-taken WITHOUT reaching into the EX stage: a branch is taken exactly
    // when the next instruction to retire is not the one after it.  That works
    // off two signals the core already exports and needs no knowledge of the
    // predictor that A19 is about to put in front of it -- which matters,
    // because this instrument has to keep meaning the same thing afterwards.
    bool      prev_valid = false;
    unsigned  prev_pc = 0, prev_insn = 0;
    long long prev_cycle = 0, prev_fetch = -1;
    std::vector<Snap> snaps;

    // The retired control-transfer trace, for A19's predictor model.
    //
    // RETIRED, not fetched, and that is a design decision rather than a
    // convenience: the predictor A19 specifies updates its state only when a
    // control transfer RESOLVES in EX, and an instruction fetched down a
    // mispredicted path never gets there.  So the retired stream is the
    // complete input to the predictor's state machine, which is what lets the
    // model be driven by a trace taken BEFORE the predictor exists -- the
    // projection and the implementation then share no code at all.
    FILE* tr = trace_path ? fopen(trace_path, "w") : 0;
    if (trace_path && !tr) { fprintf(stderr, "cannot write %s\n", trace_path); return 1; }
    if (tr) fprintf(tr, "cycle,fetch,pc,insn,next_pc\n");

    // ---- dating each instruction by its FETCH, not by its retirement -------
    // A predictor lookup happens one cycle before the instruction it describes
    // is fetched, so a rule about when an update becomes visible is a rule
    // about FETCH cycles.  `retire - fetch` is NOT a constant: a load-use
    // interlock holds an instruction in ID, and its prediction was already
    // made.  Expressing the rule on retire cycles is therefore wrong by exactly
    // the number of cycles the instruction was held -- which is what made
    // model/bpred.py disagree with the RTL on 39 of Dhrystone's returns while
    // agreeing on every one of CoreMark's: Dhrystone's callees are reached
    // through load-use stalls and CoreMark's are not.
    //
    // This mirrors ONLY the two front-end registers' load conditions, which are
    // four lines of rvntt_core.sv, and it is checked rather than trusted: the
    // FIFO must be non-empty at every retirement and empty-ish at the end.
    long long if_id_fc = -1, id_ex_fc = -1;
    std::deque<long long> ex_fifo;
    long long fifo_underflows = 0;

    dut->ck_rst      = 0;
    dut->CLK100MHZ   = 0;
    dut->uart_txd_in = 1;
    dut->sw          = 0x0;
    dut->btn         = 0x0;
    dut->eval();

    static const char END[] = "=== end A13 ===\r\n";
    const size_t ENDN = sizeof(END) - 1;

    long c;
    for (c = 0; c < max_cycles; c++) {
        if (c == 50) dut->ck_rst = 1;

        // Settle on the CURRENT register state, then sample: this is what the
        // cycle looks like to the logic when the coming edge fires, which is
        // the same instant a hardware counter would increment.
        dut->CLK100MHZ = 0; dut->eval();

        if (SIG(rst_n)) {
            n_cycle++;
            // The two stalls can be asserted together -- a load-use interlock
            // in ID behind a multiply in EX -- and that is ONE lost cycle, not
            // two.  ex_stall wins the attribution because it is the one
            // actually holding ID/EX; id_stall would have bubbled it anyway.
            if (SIG(ex_stall))       n_ex++;
            else if (SIG(id_stall))  n_id++;
            if (redir_delay == 0) {
                if (SIG(ex_redirect)) n_redir++;
            } else {
                n_redir += redir_pipe[0];
                for (int k = 0; k + 1 < redir_delay; k++)
                    redir_pipe[k] = redir_pipe[k + 1];
                redir_pipe[redir_delay - 1] = SIG(ex_redirect) ? 1 : 0;
            }

            // An instruction leaves EX -- and therefore retires two cycles
            // later, since MEM and WB never stall -- when it is in EX, is not
            // held there, and does not trap.
            if (id_ex_fc >= 0 && !SIG(ex_stall) && !SIG(ex_trap))
                ex_fifo.push_back(id_ex_fc);

            long long fetch_cycle = -1;
            if (SIG(commit_valid)) {
                if (ex_fifo.empty()) fifo_underflows++;
                else { fetch_cycle = ex_fifo.front(); ex_fifo.pop_front(); }
                n_retired++;
                unsigned pc   = SIG(commit_pc);
                unsigned insn = SIG(commit_insn);
                if (prev_valid) {
                    unsigned op = prev_insn & 0x7Fu;
                    if      (op == 0x63u) { if (pc == prev_pc + 4) n_bn++; else n_bt++; }
                    else if (op == 0x6Fu) n_jal++;
                    else if (op == 0x67u) n_jalr++;
                    if (tr && (op == 0x63u || op == 0x6Fu || op == 0x67u))
                        fprintf(tr, "%lld,%lld,%08x,%08x,%08x\n",
                                prev_cycle, prev_fetch, prev_pc, prev_insn, pc);
                }
                prev_valid = true; prev_pc = pc; prev_insn = insn;
                prev_cycle = n_cycle; prev_fetch = fetch_cycle;

                if ((insn & ~RD_MASK) == MCYCLE_INSN) {
                    Snap s;
                    s.mcycle   = SIG(commit_wdata);
                    s.cycle    = n_cycle;   s.retired  = n_retired;
                    s.id_stall = n_id;      s.ex_stall = n_ex;
                    s.redirect = n_redir;
                    s.br_taken = n_bt;      s.br_ntaken = n_bn;
                    s.jal      = n_jal;     s.jalr      = n_jalr;
                    snaps.push_back(s);
                }
            }
        }

        if (SIG(rst_n)) {
            // rvntt_core.sv's ID/EX and IF/ID load conditions, mirrored.
            long long nx_id_ex, nx_if_id;
            if      (SIG(ex_stall))                        nx_id_ex = id_ex_fc;
            else if (SIG(id_stall) || SIG(ex_redirect))    nx_id_ex = -1;
            else                                           nx_id_ex = if_id_fc;
            if      (SIG(ex_redirect))                     nx_if_id = -1;
            else if (!SIG(front_stall))                    nx_if_id = n_cycle;
            else                                           nx_if_id = if_id_fc;
            id_ex_fc = nx_id_ex; if_id_fc = nx_if_id;
        }

        dut->CLK100MHZ = 1; dut->eval();

        if (dut->led0_r & 1) saw_err = true;

        int tx = dut->uart_rxd_out & 1;
        if (state == 0) {
            if (prev_tx == 1 && tx == 0) {
                state = 1;
                countdown = BIT_CYCLES + BIT_CYCLES / 2;   // sample mid-bit
                bit_i = 0; ch = 0;
            }
        } else if (--countdown == 0) {
            if (bit_i < 8) {
                ch |= (unsigned)(tx & 1) << bit_i;
                bit_i++;
                countdown = BIT_CYCLES;
            } else {
                rx.push_back((char)(ch & 0xFF));
                state = 0;
                if (rx.size() >= ENDN &&
                    rx.compare(rx.size() - ENDN, ENDN, END) == 0) {
                    blocks++;
                    fprintf(stderr, "  block %d complete at cycle %ld\n", blocks, c);
                    if (blocks >= want_blocks) { c++; break; }
                }
            }
        }
        prev_tx = tx;

        if (rx.size() != last_len) { last_len = rx.size(); last_progress = c; }
    }
    delete dut;

    FILE* f = fopen(out_path, "wb");
    if (!f) { fprintf(stderr, "cannot write %s\n", out_path); return 1; }
    fwrite(rx.data(), 1, rx.size(), f);
    fclose(f);

    FILE* p = fopen(prof_path, "w");
    if (!p) { fprintf(stderr, "cannot write %s\n", prof_path); return 1; }
    fprintf(p, "mcycle,cycle,retired,id_stall,ex_stall,redirect,"
               "br_taken,br_ntaken,jal,jalr\n");
    for (const Snap& s : snaps)
        fprintf(p, "%llu,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld\n",
                s.mcycle, s.cycle, s.retired, s.id_stall, s.ex_stall,
                s.redirect, s.br_taken, s.br_ntaken, s.jal, s.jalr);
    fclose(p);
    if (tr) fclose(tr);

    printf("cycles=%ld bytes=%zu blocks=%d snaps=%zu\n",
           c, rx.size(), blocks, snaps.size());
    if (fifo_underflows) {
        printf("PROF_TB_FAIL: the fetch-cycle FIFO underflowed %lld time(s) -- "
               "the front-end mirror does not match rvntt_core.sv\n",
               fifo_underflows);
        return 1;
    }
    if (saw_err) {
        printf("PROF_TB_FAIL: led0_r latched -- the core retired an instruction "
               "it reports as unsupported (dbg_unsupported)\n");
        return 1;
    }
    if (blocks < want_blocks) {
        printf("PROF_TB_FAIL: %d of %d blocks after %ld cycles; last UART byte "
               "at cycle %ld (%s)\n", blocks, want_blocks, c, last_progress,
               last_progress + 2 * BIT_CYCLES * 12 < c ? "output stalled"
                                                       : "still running, raise --max");
        return 1;
    }
    printf("PROF_TB_OK\n");
    return 0;
}
