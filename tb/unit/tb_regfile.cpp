// ============================================================================
// Directed + randomised testbench for rvntt_regfile (plan A1).
//
// A1's "Verify" names three cases: x0 writes must be discarded, same-cycle
// read/write on one address must read through, and two ports must be able to
// read the same register at once.  Those are all here as directed cases, but
// the load-bearing part is the shadow model at the end: 200k random operations
// compared against a C++ copy of the architectural contract.  A three-read-port
// array's most likely defect is a copy-paste slip in the port replication, and
// a directed test only catches that if it happens to drive the ports
// differently at the right moment.  Random traffic catches it in milliseconds.
// ============================================================================
#include "Vrvntt_regfile.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>

static Vrvntt_regfile* dut;
static int failures = 0;

// Shadow model of the architectural state.  model[0] is never written.
static uint32_t model[32];

static void check(bool cond, const char* what) {
    if (!cond) {
        if (failures < 20) printf("  FAIL: %s\n", what);
        failures++;
    }
}

// Present a set of inputs and let the combinational read paths settle, WITHOUT
// taking the clock edge.  This is the state the ID stage actually observes.
static void settle(int ra1, int ra2, int ra3, int we, int wa, uint32_t wd) {
    dut->ra1 = ra1; dut->ra2 = ra2; dut->ra3 = ra3;
    dut->we  = we;  dut->wa  = wa;  dut->wd  = wd;
    dut->clk = 0;
    dut->eval();
}

// Take the posedge, committing any pending write.
static void commit() { dut->clk = 1; dut->eval(); }

// One full cycle: settle, verify the three read ports against the model, then
// commit and advance the model.  Returns nothing -- failures are counted.
static void step(int ra1, int ra2, int ra3, int we, int wa, uint32_t wd,
                 const char* what) {
    settle(ra1, ra2, ra3, we, wa, wd);

    const bool wr = we && (wa != 0);
    const int  ra[3] = {ra1, ra2, ra3};
    const uint32_t got[3] = {dut->rd1, dut->rd2, dut->rd3};

    for (int p = 0; p < 3; p++) {
        // Write-through: a read of the address being written sees the NEW data.
        uint32_t exp = (wr && wa == ra[p]) ? wd : model[ra[p]];
        if (got[p] != exp) {
            if (failures < 20)
                printf("  FAIL: %s: port%d ra=%d got 0x%08x expected 0x%08x "
                       "(we=%d wa=%d wd=0x%08x)\n",
                       what, p + 1, ra[p], got[p], exp, we, wa, wd);
            failures++;
        }
    }

    commit();
    if (wr) model[wa] = wd;
}

// A cheap deterministic PRNG.  Deterministic on purpose: a failing run must be
// reproducible without plumbing a seed through the regression harness.
static uint32_t rnd_state = 0x12345678u;
static uint32_t rnd() {
    rnd_state ^= rnd_state << 13;
    rnd_state ^= rnd_state >> 17;
    rnd_state ^= rnd_state << 5;
    return rnd_state;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vrvntt_regfile;
    for (int i = 0; i < 32; i++) model[i] = 0;

    // ---------------------------------------------------------------------
    // 1. Power-on state.  Every register must read zero before anything is
    //    written.  This is what keeps A5's cosim aligned with Spike, whose
    //    architectural state at reset is all-zero.
    // ---------------------------------------------------------------------
    for (int r = 0; r < 32; r++) {
        settle(r, r, r, 0, 0, 0);
        check(dut->rd1 == 0 && dut->rd2 == 0 && dut->rd3 == 0,
              "power-on state must be zero on every register and every port");
        commit();
    }

    // ---------------------------------------------------------------------
    // 2. x0 writes are discarded -- during the write cycle AND afterwards.
    //    The "during" half matters: a write-through mux that forgets to check
    //    wa != 0 passes the "afterwards" half and still corrupts x0 reads for
    //    exactly one cycle, which in a pipeline is one wrong instruction.
    // ---------------------------------------------------------------------
    settle(0, 0, 0, 1, 0, 0xDEADBEEFu);
    check(dut->rd1 == 0 && dut->rd2 == 0 && dut->rd3 == 0,
          "x0 must read zero during a write to x0 (write-through must not fire)");
    commit();
    settle(0, 0, 0, 0, 0, 0);
    check(dut->rd1 == 0 && dut->rd2 == 0 && dut->rd3 == 0,
          "x0 must still read zero after a write to x0");
    commit();

    // ---------------------------------------------------------------------
    // 3. Ordinary write, then read back on all three ports.
    // ---------------------------------------------------------------------
    step(0, 0, 0, 1, 5, 0xA5A5A5A5u, "write x5");
    settle(5, 5, 5, 0, 0, 0);
    check(dut->rd1 == 0xA5A5A5A5u && dut->rd2 == 0xA5A5A5A5u &&
          dut->rd3 == 0xA5A5A5A5u,
          "all three ports must read back a written register");
    commit();

    // ---------------------------------------------------------------------
    // 4. Simultaneous reads of the same register (A1 names this explicitly).
    //    Also the mixed case: two ports on one register, the third elsewhere.
    // ---------------------------------------------------------------------
    step(0, 0, 0, 1, 9, 0x0BADF00Du, "write x9");
    settle(9, 9, 5, 0, 0, 0);
    check(dut->rd1 == 0x0BADF00Du && dut->rd2 == 0x0BADF00Du &&
          dut->rd3 == 0xA5A5A5A5u,
          "two ports on one register, third independent");
    commit();

    // ---------------------------------------------------------------------
    // 5. Write-through, per port and then on all three at once.
    // ---------------------------------------------------------------------
    settle(7, 0, 0, 1, 7, 0x11111111u);
    check(dut->rd1 == 0x11111111u, "write-through on port 1");
    check(dut->rd2 == 0 && dut->rd3 == 0, "non-matching ports unaffected");
    commit(); model[7] = 0x11111111u;

    settle(0, 7, 0, 1, 7, 0x22222222u);
    check(dut->rd2 == 0x22222222u, "write-through on port 2");
    commit(); model[7] = 0x22222222u;

    settle(0, 0, 7, 1, 7, 0x33333333u);
    check(dut->rd3 == 0x33333333u, "write-through on port 3");
    commit(); model[7] = 0x33333333u;

    settle(7, 7, 7, 1, 7, 0x44444444u);
    check(dut->rd1 == 0x44444444u && dut->rd2 == 0x44444444u &&
          dut->rd3 == 0x44444444u, "write-through on all three ports at once");
    commit(); model[7] = 0x44444444u;

    // ---------------------------------------------------------------------
    // 6. we == 0 must not write, even with a plausible address and data.
    // ---------------------------------------------------------------------
    step(0, 0, 0, 0, 7, 0xFFFFFFFFu, "we=0 must not write");
    settle(7, 0, 0, 0, 0, 0);
    check(dut->rd1 == 0x44444444u, "register unchanged when we is low");
    commit();

    // ---------------------------------------------------------------------
    // 7. Address decode: give all 31 writable registers distinct values, then
    //    read every one back.  Catches a stuck or swapped address bit, which a
    //    single-register test cannot see.
    // ---------------------------------------------------------------------
    for (int r = 1; r < 32; r++)
        step(0, 0, 0, 1, r, 0xC0DE0000u + r, "distinct-value fill");
    for (int r = 0; r < 32; r++) {
        settle(r, r, r, 0, 0, 0);
        uint32_t exp = (r == 0) ? 0u : (0xC0DE0000u + r);
        check(dut->rd1 == exp && dut->rd2 == exp && dut->rd3 == exp,
              "distinct-value read-back (address decode)");
        commit();
    }

    // ---------------------------------------------------------------------
    // 8. Randomised comparison against the shadow model.  Address 0 is drawn
    //    more often than uniform so the x0 and write-through-to-x0 corners keep
    //    getting hit rather than appearing once in 32 draws.
    // ---------------------------------------------------------------------
    const int N = 200000;
    for (int i = 0; i < N; i++) {
        uint32_t r = rnd();
        int ra1 = (r & 0x1F);
        int ra2 = ((r >> 5) & 0x1F);
        int ra3 = ((r >> 10) & 0x1F);
        int we  = ((r >> 15) & 3) != 0;        // write 75% of cycles
        int wa  = ((r >> 17) & 0x1F);
        if (((r >> 22) & 7) == 0) wa = 0;      // bias toward x0
        if (((r >> 25) & 7) == 0) ra1 = wa;    // bias toward write-through
        if (((r >> 28) & 7) == 0) ra2 = wa;
        step(ra1, ra2, ra3, we, wa, rnd(), "random");
        if (failures > 20) break;
    }

    delete dut;
    if (failures) { printf("REGFILE_TB_FAIL (%d)\n", failures); return 1; }
    printf("REGFILE_TB_OK (%d directed checks + %d random ops)\n", 32 * 3 + 20, N);
    return 0;
}
