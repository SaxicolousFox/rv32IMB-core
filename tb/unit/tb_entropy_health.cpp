// ============================================================================
// tb_entropy_health -- MODS_A2 A29's central obligation.
//
// "A HEALTH TEST THAT HAS NEVER BEEN OBSERVED TO FIRE IS NOT A HEALTH TEST."
//
// The noise source itself cannot be simulated -- it is a combinational ring
// oscillator, which is why rvntt_entropy has a STUB arm and why this testbench
// drives that stub directly.  Everything else in the entropy path IS ordinary
// synchronous logic, and this exercises all of it: the two SP 800-90B
// continuous tests, the `seed` state machine, the consuming read, and DEAD.
//
// Each scenario states what it expects BEFORE it runs, so a scenario that
// silently stopped exercising anything shows up as a wrong expectation rather
// than as a pass.
// ============================================================================
#include "Vrvntt_seed.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstring>

static Vrvntt_seed *dut;

static void tick() { dut->clk = 0; dut->eval(); dut->clk = 1; dut->eval(); }

static void reset() {
  dut->rst_n = 0; dut->rd_en = 0; dut->stub_bit = 0;
  for (int i = 0; i < 4; i++) tick();
  dut->rst_n = 1;
}

// A cheap xorshift, so the "ideal" source is reproducible and not the host's
// rand().  A health test that only ever sees one random stream is a health test
// tuned to that stream.
static uint32_t rng_state = 0x1234567u;
static int rng_bit() {
  rng_state ^= rng_state << 13; rng_state ^= rng_state >> 17;
  rng_state ^= rng_state << 5;
  return rng_state & 1;
}

enum Src { STUCK0, STUCK1, BIASED, IDEAL, PERIODIC };

// Run `n` CYCLES from `src` and return the status field after them.
//
// `drain` makes the driver consume a seed whenever one is ready, which is what
// keeps the sampler running: the source is gated on needing to refill, so a
// scenario that never reads takes 16 samples and then stops.  That is the
// design working as intended and it makes "run a bad source until the health
// tests catch it" a scenario that has to CONSUME, exactly as software would.
static unsigned run(Src src, int n, bool drain = false) {
  for (int i = 0; i < n; i++) {
    if (drain) {
      dut->eval();
      dut->rd_en = (((dut->rdata >> 30) & 3u) == 2u);   // ES16
    }
    switch (src) {
      case STUCK0: dut->stub_bit = 0; break;
      case STUCK1: dut->stub_bit = 1; break;
      // 7:1 towards 1 -- biased enough that the adaptive proportion test must
      // see it, but NOT stuck, so the repetition test alone cannot explain a
      // catch.  A run of 21 identical samples has probability (7/8)^20 = 0.07
      // per position, so repetition will also fire here; the biased-source
      // scenario below therefore uses a bias that cannot trip repetition.
      case BIASED: dut->stub_bit = (rng_bit() || rng_bit() || rng_bit()) ? 1 : 0; break;
      case IDEAL:  dut->stub_bit = rng_bit(); break;
      // SEVEN ONES THEN A ZERO, forever.  This is the scenario the ADAPTIVE
      // PROPORTION test exists for and the repetition test structurally cannot
      // catch: the longest run is seven, well under the cutoff of 21, while
      // 7/8 of every window is the same value against a cutoff of 589/1024.
      //
      // It is also the realistic failure mode of the hardware.  A ring
      // oscillator sampled by a clock it is asynchronous to INJECTION-LOCKS to
      // that clock and emits exactly this: a short, perfectly periodic
      // sequence that carries no entropy and looks nothing like "stuck".  It is
      // why rvntt_entropy uses several rings of coprime length rather than one,
      // and it is why removing the adaptive test has to be caught HERE -- the
      // first version of this testbench had four bad sources and every one of
      // them was catchable by repetition alone, so deleting the adaptive test
      // entirely changed no verdict.
      case PERIODIC: { static int ph = 0; dut->stub_bit = (ph++ % 8) != 7; break; }
    }
    tick();
  }
  dut->rd_en = 0;
  dut->eval();
  return (dut->rdata >> 30) & 3u;
}

static int fails = 0;
static void expect(bool ok, const char *what) {
  printf("  %-58s %s\n", what, ok ? "ok" : "FAIL");
  if (!ok) fails++;
}

// The status encoding, from the Zkr specification.
enum { ST_BIST = 0, ST_WAIT = 1, ST_ES16 = 2, ST_DEAD = 3 };

int main(int argc, char **argv) {
  Verilated::commandArgs(argc, argv);
  dut = new Vrvntt_seed;

  // ---- 1. an IDEAL source reaches ES16 and never dies ----------------------
  reset();
  unsigned st = run(IDEAL, 64);
  expect(st == ST_BIST, "ideal: still BIST after 64 samples (start-up test)");
  st = run(IDEAL, 2000);
  expect(st == ST_ES16 || st == ST_WAIT,
         "ideal: BIST completes and the source goes live");
  // NOT "survives forever", because it does not and cannot.  SP 800-90B's
  // repetition cutoff has a 2^-20 false-positive rate PER SAMPLE by
  // construction, so a perfect source trips it about once per 2^20 samples --
  // the first version of this line asserted survival over 42 000 samples,
  // which is a 4% coin flip, and it duly came up tails on a run of 23.  What
  // is asserted instead is that the source stays alive while IDLE, which is
  // the property gating the sampler actually buys: 40 000 cycles with a full
  // buffer and no reads consume no samples at all.
  st = run(IDEAL, 40000);
  expect(st != ST_DEAD,
         "ideal: 40k IDLE cycles consume no entropy and cannot trip a test");

  // ---- 2. a consuming read returns FRESH bits ------------------------------
  // Two ES16 reads must not hand back the same buffer.  This is the failure
  // that makes an RNG look like it works: every call succeeds, every call
  // returns the same number.
  // Bounded, always.  An unbounded wait for a status that never arrives is a
  // hang, and a hang in a health-test testbench looks exactly like a slow
  // machine until someone waits twenty minutes for it.
  int guard = 0;
  while (((dut->rdata >> 30) & 3u) != ST_ES16 && guard++ < 1000) run(IDEAL, 1);
  expect(guard < 1000, "read: ES16 is reachable at all");
  unsigned first = dut->rdata & 0xFFFFu;
  dut->rd_en = 1; tick(); dut->rd_en = 0; dut->eval();
  expect(((dut->rdata >> 30) & 3u) != ST_ES16,
         "read: the buffer is empty immediately after a consuming read");
  int differ = 0;
  for (int trial = 0; trial < 8; trial++) {
    guard = 0;
    while (((dut->rdata >> 30) & 3u) != ST_ES16 && guard++ < 1000) run(IDEAL, 1);
    unsigned v = dut->rdata & 0xFFFFu;
    if (v != first) differ++;
    first = v;
    dut->rd_en = 1; tick(); dut->rd_en = 0; dut->eval();
  }
  expect(differ >= 6, "read: consecutive reads return different values");

  // ---- 3. reserved bits, and no entropy outside ES16 -----------------------
  reset();
  run(IDEAL, 8);
  expect(((dut->rdata >> 30) & 3u) == ST_BIST, "BIST: status is BIST from reset");
  expect((dut->rdata & 0xFFFFu) == 0, "BIST: no entropy is returned");
  expect(((dut->rdata >> 16) & 0x3FFFu) == 0, "BIST: reserved bits [29:16] are zero");

  // ---- 4. STUCK AT 0 -- the repetition count test must fire ----------------
  reset();
  st = run(STUCK0, 64);
  expect(st == ST_DEAD, "stuck-at-0: DEAD within 64 samples");
  st = run(IDEAL, 5000);
  expect(st == ST_DEAD, "stuck-at-0: DEAD LATCHES -- a good source cannot revive it");

  // ---- 5. STUCK AT 1 -------------------------------------------------------
  reset();
  st = run(STUCK1, 64);
  expect(st == ST_DEAD, "stuck-at-1: DEAD within 64 samples");

  // ---- 6. BIASED 7:1 -- the adaptive proportion test's job ------------------
  // The repetition test may also catch this; what matters is that a source
  // which is NOT stuck and is obviously not uniform does not reach ES16.
  reset();
  st = run(BIASED, 12000, /*drain=*/true);
  expect(st == ST_DEAD, "biased 7:1: DEAD while being consumed");

  // ---- 6b. PERIODIC -- only the adaptive proportion test can see this ------
  reset();
  st = run(PERIODIC, 12000, /*drain=*/true);
  expect(st == ST_DEAD,
         "periodic 7-in-8 (max run 7): DEAD -- the adaptive test's own case");

  // ---- 7. the failure must be attributable, not just present ---------------
  // A 3:1 bias survives the repetition test far longer -- (3/4)^20 = 0.003 per
  // position -- so reaching DEAD here is much more likely to be the adaptive
  // proportion test doing its job.  It is stated as a separate expectation so
  // that if only one of the two tests were implemented, one of these two lines
  // would go red rather than both staying green.
  reset();
  rng_state = 0xC0FFEEu;
  int dead3 = 0;
  for (int i = 0; i < 40000 && !dead3; i++) {
    dut->eval();
    dut->rd_en = (((dut->rdata >> 30) & 3u) == ST_ES16);
    dut->stub_bit = (rng_bit() || rng_bit()) ? 1 : 0;   // 3:1
    tick();
    dut->eval();
    dead3 = (((dut->rdata >> 30) & 3u) == ST_DEAD);
  }
  dut->rd_en = 0;
  expect(dead3 != 0, "biased 3:1: DEAD while being consumed");

  dut->final();
  delete dut;
  if (fails) { printf("ENTROPY_TB_FAIL: %d expectation(s)\n", fails); return 1; }
  printf("ENTROPY_TB_OK\n");
  return 0;
}
