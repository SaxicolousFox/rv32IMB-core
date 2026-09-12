// ============================================================================
// The stall attribution instrument.
//
// Runs the benchmark image on rvntt_soc_sim_top exactly as tb/unit/tb_bench.cpp
// does, and additionally counts, every cycle, where the cycles went.  A
// separate testbench because reading the core's internals needs Verilator's
// --public-flat-rw, which slows the regression's bench_sim build.
//
// The region boundaries are the software's own: every `csrr rd, mcycle` that
// retires is recorded with the value the core returned, and
// tb/perf/run_stall_profile.py matches those against the values the benchmark
// printed over the UART.
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

// The core's own Zihpm counters, read straight out of the CSR block so the
// hardware counters and this instrument's counters compare over the same
// region of the same run.  The event selectors are forced from the testbench
// so the instruction stream is not perturbed.
#define CSRSIG(n) (dut->rootp->rvntt_soc_sim_top__DOT__u_soc__DOT__u_core__DOT__u_csr__DOT__##n)

// rv32i_pkg::HPM_EV_* minus one -- the index into the counter array this
// testbench programs each counter to.  Kept in the same order as the enum.
static const int HPM_N = 6;

// How many cycles after a snapshot the counters are read, derived: reading
// during cycle T sees the value latched at the end of T-1, and the event bus
// is registered, so that value counts pulses through T-2.  This instrument's
// counters at the same instant already include T.  Two cycles of skew.
static const int HPM_SAMPLE_DELAY = 2;

// `csrr rd, mcycle` is CSRRS rd, 0xB00, x0.  Everything but rd is fixed.
static const unsigned MCYCLE_INSN = 0xB0002073u;
static const unsigned RD_MASK     = 0x00000F80u;

struct Snap {
    unsigned long long mcycle;
    long long cycle, retired, id_stall, ex_stall, redirect, redirect_raw;
    long long br_taken, br_ntaken, jal, jalr;
    unsigned long long hpm[HPM_N];      // the core's own counters
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);

    const char* out_path  = "bench_sim.log";
    const char* prof_path = "prof.csv";
    const char* trace_path = 0;
    long max_cycles = 400L * 1000 * 1000;
    int  want_blocks = 1;
    // ex_redirect at cycle t clears IF/ID and ID/EX, so the two cycles in
    // which nothing retires are later than the pulse; charging them to the
    // pulse's cycle makes the per-region identity wrong by exactly 2 whenever
    // a redirect fires near a region boundary.  The delay was swept and
    // measured.
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
    // The same event counted at a different instant: n_redir is delayed by
    // redir_delay so the lost cycles land in the right region; the hardware
    // counter increments on the pulse.  This raw count asks the hardware
    // counter's question, so the cross-validation can be an exact match.
    long long n_redir_raw = 0;
    std::vector<int> redir_pipe(redir_delay > 0 ? redir_delay : 1, 0);
    long long n_bt = 0, n_bn = 0, n_jal = 0, n_jalr = 0;
    // The previous retirement, so a branch can be classified taken or
    // not-taken without reaching into EX: a branch is taken exactly when the
    // next instruction to retire is not the one after it.
    bool      prev_valid = false;
    unsigned  prev_pc = 0, prev_insn = 0;
    long long prev_cycle = 0, prev_fetch = -1;
    std::vector<Snap> snaps;
    // (cycle at which to sample, index into snaps)
    std::vector<std::pair<long long,int> > hpm_pending;

    // The retired control-transfer trace for the predictor model.  Retired,
    // not fetched: the predictor updates only when a transfer resolves in EX,
    // so the retired stream is the complete input to its state machine.
    FILE* tr = trace_path ? fopen(trace_path, "w") : 0;
    if (trace_path && !tr) { fprintf(stderr, "cannot write %s\n", trace_path); return 1; }
    if (tr) fprintf(tr, "cycle,fetch,pc,insn,next_pc\n");

    // ---- dating each instruction by its fetch, not its retirement ---------
    // A predictor lookup happens one cycle before the instruction is fetched,
    // and `retire - fetch` is not constant (a load-use interlock holds an
    // instruction in ID after its prediction was made).  This mirrors only
    // the two front-end registers' load conditions, and it is checked: the
    // FIFO must be non-empty at every retirement.
    long long if_id_fc = -1, id_ex_fc = -1;
    std::deque<long long> ex_fifo;
    long long fifo_underflows = 0;

    dut->ck_rst      = 0;
    dut->CLK100MHZ   = 0;
    dut->uart_txd_in = 1;
    dut->sw          = 0x0;
    dut->btn         = 0x0;
    dut->eval();

    static const char END[] = "=== end bench ===\r\n";
    const size_t ENDN = sizeof(END) - 1;

    // Arm the six counters once, after reset, by writing the event selectors
    // directly.  Counter N is programmed to event N+1, matching
    // rv32i_pkg::HPM_EV_* in order.
    bool hpm_armed = false;

    long c;
    for (c = 0; c < max_cycles; c++) {
        if (c == 50) dut->ck_rst = 1;
        if (!hpm_armed && SIG(rst_n)) {
            // Both the selector and the one-hot watch mask: the counter
            // enable reads hpm_watch_q, a registered mask maintained alongside
            // mhpmevent_q, so forcing the selector alone arms nothing.
            for (int k = 0; k < HPM_N; k++) {
                CSRSIG(mhpmevent_q)[k] = k + 1;
                CSRSIG(hpm_watch_q)[k] = 1u << k;
            }
            hpm_armed = true;
        }

        // Settle on the CURRENT register state, then sample: this is what the
        // cycle looks like to the logic when the coming edge fires, which is
        // the same instant a hardware counter would increment.
        dut->CLK100MHZ = 0; dut->eval();

        if (SIG(rst_n)) {
            n_cycle++;
            // Fill in any snapshot whose HPM sample is due this cycle.
            for (size_t q = 0; q < hpm_pending.size(); ) {
                if (hpm_pending[q].first == n_cycle) {
                    Snap& t = snaps[hpm_pending[q].second];
                    for (int k = 0; k < HPM_N; k++)
                        t.hpm[k] = CSRSIG(mhpmcounter_q)[k];
                    hpm_pending.erase(hpm_pending.begin() + q);
                } else {
                    q++;
                }
            }
            // The two stalls can be asserted together -- a load-use interlock
            // in ID behind a multiply in EX -- and that is ONE lost cycle, not
            // two.  ex_stall wins the attribution because it is the one
            // actually holding ID/EX; id_stall would have bubbled it anyway.
            if (SIG(ex_stall))       n_ex++;
            else if (SIG(id_stall))  n_id++;
            if (SIG(ex_redirect)) n_redir_raw++;
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
                    s.redirect = n_redir;   s.redirect_raw = n_redir_raw;
                    s.br_taken = n_bt;      s.br_ntaken = n_bn;
                    s.jal      = n_jal;     s.jalr      = n_jalr;
                    // The HPM fields are filled in HPM_SAMPLE_DELAY cycles
                    // from now; see the constant's derivation above.
                    for (int k = 0; k < HPM_N; k++) s.hpm[k] = 0;
                    snaps.push_back(s);
                    hpm_pending.push_back(
                        std::make_pair(n_cycle + HPM_SAMPLE_DELAY,
                                       (int)snaps.size() - 1));
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
    fprintf(p, "mcycle,cycle,retired,id_stall,ex_stall,redirect,redirect_raw,"
               "br_taken,br_ntaken,jal,jalr,"
               "hpm_loaduse,hpm_exstall,hpm_redirect,"
               "hpm_mispredict,hpm_btbhit,hpm_xfertaken\n");
    for (const Snap& s : snaps) {
        fprintf(p, "%llu,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld",
                s.mcycle, s.cycle, s.retired, s.id_stall, s.ex_stall,
                s.redirect, s.redirect_raw,
                s.br_taken, s.br_ntaken, s.jal, s.jalr);
        for (int k = 0; k < HPM_N; k++) fprintf(p, ",%llu", s.hpm[k]);
        fprintf(p, "\n");
    }
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
