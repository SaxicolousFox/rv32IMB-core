/*
 * randombytes() from Zkr's `seed` CSR -- MODS_A2 A29.
 *
 * THIS FILE IS NEVER LINKED INTO THE KAT BUILD, and that is the whole design.
 * MODS_A2 A29: "Keep them structurally separate -- a build-time selection
 * between 'seeds from `seed`' and 'seeds from the KAT vector', with the KAT
 * build unable to reach the CSR -- rather than a runtime flag."
 *
 * The failure this prevents is silent in BOTH directions and neither direction
 * has a test that can catch it after the fact:
 *
 *   * A KAT that passes because the entropy source was bypassed at runtime
 *     proves nothing about the source.
 *   * A keygen that is deterministic in the field is a catastrophic bug that
 *     NO known-answer test can ever detect -- a KAT's whole job is to be
 *     deterministic.
 *
 * So the selection is which OBJECT FILE is linked.  `make ENTROPY=1` builds
 * this one and does not compile kat_main.c's deterministic generator;
 * everything else builds kat_main.c and never mentions CSR 0x015.
 * tb/unit/test_kat_no_seed.py disassembles the KAT binary and requires zero
 * references to it, so the separation is checked rather than asserted.
 *
 * ---------------------------------------------------------------------------
 * HOW TO READ `seed`, and every part of this is a way to get it wrong.
 * ---------------------------------------------------------------------------
 *   * The access must be a read-WRITE.  `csrrs a0, seed, x0` -- the ordinary
 *     way to read a CSR -- raises an illegal instruction, because reading
 *     `seed` DESTROYS the entropy it returns and the architecture refuses to
 *     let that happen by accident.  `csrrw` with any source is the read.
 *   * The value written is ignored.  x0 is used to make that obvious.
 *   * [31:30] is the status: 00 BIST, 01 WAIT, 10 ES16, 11 DEAD.
 *   * Only ES16 carries entropy, and only in [15:0].
 *   * DEAD is terminal.  There is no retry that helps and pretending otherwise
 *     would spin forever; this returns failure and lets the caller decide.
 */
#include <stddef.h>
#include <stdint.h>

#define SEED_CSR 0x015

#define SEED_BIST 0u
#define SEED_WAIT 1u
#define SEED_ES16 2u
#define SEED_DEAD 3u

static inline uint32_t seed_read(void)
{
  uint32_t v;
  /* csrrw rd, seed, x0 -- a read-write with a zero source.  Not csrrs: that
   * would be a read-only access and would trap. */
  __asm__ volatile ("csrrw %0, %1, x0" : "=r"(v) : "i"(SEED_CSR));
  return v;
}

/* Non-zero on success.  A DEAD source is reported, never worked around. */
int randombytes_entropy(uint8_t *x, size_t xlen)
{
  size_t i = 0;
  while (i < xlen) {
    uint32_t v = seed_read();
    switch (v >> 30) {
      case SEED_ES16:
        x[i++] = (uint8_t)(v & 0xFF);
        if (i < xlen) x[i++] = (uint8_t)((v >> 8) & 0xFF);
        break;
      case SEED_BIST:
      case SEED_WAIT:
        /* Poll.  The source is gated on needing to refill, so a poll is cheap
         * and does not consume entropy: only an ES16 read does. */
        break;
      default:                       /* SEED_DEAD */
        return 0;
    }
  }
  return 1;
}

void randombytes(uint8_t *x, size_t xlen)
{
  /* A caller that ignores the failure gets zeros, which is the WORST possible
   * fallback -- so it does not get zeros.  There is nothing safe to return
   * from a dead entropy source, and a bare-metal image has nowhere to raise an
   * error to, so it stops.  A key generated from a failed source is worse than
   * a machine that visibly hung. */
  if (!randombytes_entropy(x, xlen))
    for (;;) { }
}
