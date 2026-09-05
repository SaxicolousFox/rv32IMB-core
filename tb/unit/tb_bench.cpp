// Runs the A13 benchmark image on rvntt_soc_sim_top and writes what comes out
// of the UART to a file, for tb/fpga/parse_bench_uart.py to check.
//
// The point of decoding to a FILE rather than checking here is that the sim
// capture and the board capture then go through the SAME parser.  That parser is
// fault-injected (--selftest, 20 cases); duplicating its checks in C++ would
// mean a second set that is not.
//
// Two things are checked here and nowhere else, because they are not visible in
// the UART text:
//
//   led0_r is dbg_unsupported latched -- the core retiring something it does not
//   implement.  Dhrystone and CoreMark are the first RV32I workloads on this SoC
//   large enough to reach parts of the ISA the cosimulation's random programs
//   only sampled, and libgcc's __divsi3/__mulsi3 are hand-written assembly that
//   nothing else here has ever executed.  If any of it decodes as illegal, this
//   is what says so.
//
//   Progress.  A benchmark that hangs produces the same empty capture as one
//   that never started, so a stalled UART is reported as a stall rather than as
//   a timeout with no explanation.
#include "Vrvntt_soc_sim_top.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

static const int BIT_CYCLES = 34;     // CORE_HZ / BAUD = 4e6 / 115200 -> 34

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);

    const char* out_path = "bench_sim.log";
    long max_cycles = 400L * 1000 * 1000;
    int  want_blocks = 1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--out") && i + 1 < argc)         out_path = argv[++i];
        else if (!strcmp(argv[i], "--max") && i + 1 < argc)    max_cycles = atol(argv[++i]);
        else if (!strcmp(argv[i], "--blocks") && i + 1 < argc) want_blocks = atoi(argv[++i]);
    }

    Vrvntt_soc_sim_top* dut = new Vrvntt_soc_sim_top;
    std::string rx;
    int state = 0, countdown = 0, bit_i = 0, prev_tx = 1;
    unsigned ch = 0;
    int blocks = 0;
    bool saw_err = false;
    long last_progress = 0;
    size_t last_len = 0;

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
        dut->CLK100MHZ = 0; dut->eval();
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

    printf("cycles=%ld bytes=%zu blocks=%d\n", c, rx.size(), blocks);
    if (saw_err) {
        printf("BENCH_TB_FAIL: led0_r latched -- the core retired an instruction "
               "it reports as unsupported (dbg_unsupported)\n");
        return 1;
    }
    if (blocks < want_blocks) {
        printf("BENCH_TB_FAIL: %d of %d blocks after %ld cycles; last UART byte "
               "at cycle %ld (%s)\n", blocks, want_blocks, c, last_progress,
               last_progress + 2 * BIT_CYCLES * 12 < c ? "output stalled"
                                                       : "still running, raise --max");
        return 1;
    }
    printf("BENCH_TB_OK\n");
    return 0;
}
