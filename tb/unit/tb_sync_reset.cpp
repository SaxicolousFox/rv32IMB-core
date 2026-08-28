// Directed testbench for rvntt_sync_reset.
// Checks: (1) async assert drives rst_n low, (2) release is synchronous and takes
// exactly STAGES clock edges, (3) rst_n stays high afterwards.
#include "Vrvntt_sync_reset.h"
#include "verilated.h"
#include <cstdio>

static Vrvntt_sync_reset* dut;
static int failures = 0;

static void tick() { dut->clk = 0; dut->eval(); dut->clk = 1; dut->eval(); }

static void check(bool cond, const char* what) {
    if (!cond) { printf("  FAIL: %s\n", what); failures++; }
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vrvntt_sync_reset;

    const int STAGES = 2;   // must match the RTL default

    // 1. Assert reset asynchronously (no clock edge at all).
    dut->arst_n = 0;
    dut->clk = 0;
    dut->eval();
    check(dut->rst_n == 0, "rst_n must be low immediately on async assert (no clock)");

    for (int i = 0; i < 5; i++) { tick(); check(dut->rst_n == 0, "rst_n low while arst_n low"); }

    // 2. Release: rst_n must stay low for STAGES more edges, then rise.
    dut->arst_n = 1;
    dut->eval();
    check(dut->rst_n == 0, "release must be synchronous: rst_n low until a clock edge");

    for (int i = 0; i < STAGES; i++) {
        check(dut->rst_n == 0, "rst_n must remain low during sync pipeline fill");
        tick();
    }
    check(dut->rst_n == 1, "rst_n must be high after STAGES edges");

    // 3. Stays high.
    for (int i = 0; i < 5; i++) { tick(); check(dut->rst_n == 1, "rst_n stays high"); }

    // 4. Re-assert asynchronously mid-operation.
    dut->arst_n = 0;
    dut->eval();
    check(dut->rst_n == 0, "async re-assert takes effect with no clock edge");

    delete dut;
    if (failures) { printf("SYNC_RESET_TB_FAIL (%d)\n", failures); return 1; }
    printf("SYNC_RESET_TB_OK\n");
    return 0;
}
