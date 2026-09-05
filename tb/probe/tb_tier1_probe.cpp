// ============================================================================
// tb_tier1_probe -- MODS_A2 A24.  Checks rvntt_tier1_probe against the frozen
// model, and checks its EX occupancy against the frozen latency table.
//
// WHY A CORRECTNESS TESTBENCH FOR A THING THAT WILL NEVER SHIP.  A24's entire
// value is one frequency, and a datapath that computes the wrong answer is
// very likely a SMALLER datapath than the right one -- a dropped Barrett
// reduction, a multiply the synthesiser folded away because its result was
// never read.  That would report a frequency the real unit cannot reach, which
// is precisely the failure the step exists to prevent, arriving through the
// step meant to prevent it.  So: prove it computes the contract, then time it.
//
// Vectors and expected results come from model/isa/xkntt.py, which is frozen
// and validated bit-exactly against the C reference.  This file only compares.
// ============================================================================
#include "Vrvntt_tier1_probe.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

static Vrvntt_tier1_probe *dut;
static vluint64_t main_time = 0;

static void tick() {
  dut->clk = 0; dut->eval();
  dut->clk = 1; dut->eval();
  main_time++;
}

struct Vec { unsigned op, rs1, rs2, expect; };

int main(int argc, char **argv) {
  Verilated::commandArgs(argc, argv);

  const char *vecfile = nullptr;
  int stages = 4;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--vectors") && i + 1 < argc) vecfile = argv[++i];
    else if (!strcmp(argv[i], "--stages") && i + 1 < argc) stages = atoi(argv[++i]);
  }
  if (!vecfile) { printf("TIER1_TB_FAIL: no --vectors\n"); return 1; }

  std::vector<Vec> vs;
  FILE *f = fopen(vecfile, "r");
  if (!f) { printf("TIER1_TB_FAIL: cannot open %s\n", vecfile); return 1; }
  unsigned o, a, b, e;
  while (fscanf(f, "%u %x %x %x", &o, &a, &b, &e) == 4) vs.push_back({o, a, b, e});
  fclose(f);
  if (vs.empty()) {
    // A vector file that is empty makes every check below vacuous and the run
    // still exits 0.  This project has shipped that shape six times; not here.
    printf("TIER1_TB_FAIL: %s held no vectors\n", vecfile); return 1;
  }

  dut = new Vrvntt_tier1_probe;
  dut->rst_n = 0; dut->req = 0; dut->op = 0; dut->rs1 = 0; dut->rs2 = 0;
  for (int i = 0; i < 4; i++) tick();
  dut->rst_n = 1;

  // OCCUPANCY IS PER-OPERATION, because the frozen table is.  `kmm` taps out
  // of segment C and finishes one edge earlier than `kbfct`/`kbfgs`; at
  // STAGES=4 that is the contract's own 4 and 5.  Checking a single number
  // here would have made the early tap invisible to this testbench, which is
  // the one place it could be checked at all.
  auto want_occ_for = [&](unsigned op) { return op == 0 ? stages : stages + 1; };
  int bad = 0, checked = 0, occ_bad = 0;

  for (size_t i = 0; i < vs.size(); i++) {
    const Vec &v = vs[i];
    dut->req = 1; dut->op = v.op; dut->rs1 = v.rs1; dut->rs2 = v.rs2;

    // `req` is held for every cycle the instruction is in EX, exactly as the
    // multi-cycle handshake requires.  Count the cycles until `done`; that
    // count IS the EX occupancy the frozen latency table constrains.
    //
    // THE OPERANDS ARE CORRUPTED AFTER THE START CYCLE, ON PURPOSE.  In the
    // core they come from the forwarding muxes, which are re-evaluated every
    // cycle: while the pipeline is stalled the instructions behind this one
    // drain out of MEM and WB, so the mux stops matching and falls back to a
    // stale register value.  rvntt_muldiv records exactly this.  Holding them
    // steady here made the testbench blind -- a mutation that tapped `mont_c`
    // (combinational) instead of `s3_m` (registered) ESCAPED, because with
    // constant inputs the two carry the same value.  With the inputs moving,
    // any stage that re-reads them late is caught immediately.  `op` is held,
    // because in the core it comes from the ID/EX register and does not drift.
    int occ = 0;
    while (!dut->done && occ < 32) {
      dut->eval();
      if (dut->done) break;
      tick(); occ++;
      dut->rs1 = ~v.rs1; dut->rs2 = ~v.rs2;   // the forwarding mux, drifting
    }
    dut->eval();
    if (!dut->done) { printf("TIER1_TB_FAIL: vector %zu never asserted done\n", i); bad++; break; }
    occ++;   // the done cycle itself

    const int want_occ = want_occ_for(v.op);
    if (occ != want_occ) {
      if (occ_bad < 5)
        printf("OCCUPANCY vector %zu: op=%u got %d cycles, want %d\n",
               i, v.op, occ, want_occ);
      occ_bad++;
    }
    unsigned got = dut->result;
    if (got != v.expect) {
      if (bad < 10)
        printf("MISMATCH vector %zu: op=%u rs1=%08x rs2=%08x got=%08x want=%08x\n",
               i, v.op, v.rs1, v.rs2, got, v.expect);
      bad++;
    }
    checked++;
    tick();                 // retire: `done` was high, so busy_q clears
    dut->req = 0; dut->eval(); tick();
  }

  dut->final();
  delete dut;

  if (bad || occ_bad) {
    printf("TIER1_TB_FAIL: %d/%d wrong result(s), %d wrong occupancy\n",
           bad, checked, occ_bad);
    return 1;
  }
  printf("TIER1_TB_OK: %d vector(s), STAGES=%d, EX occupancy %d (kmm) / %d "
         "(kbfct, kbfgs) cycles\n", checked, stages,
         want_occ_for(0), want_occ_for(1));
  return 0;
}
