/*
 * The Keccak dual baseline (MODS_A2 A21/A23).
 *
 * WHY THIS EXISTS, AND WHY IT IS THE MEASUREMENT A21 WAS TAKEN FOR.  Plan 10 M3
 * says that on a small core with no Keccak acceleration "the SHAKE128/SHAKE256/
 * SHA3 work dominates ML-KEM", and asks for the NTT/Keccak breakdown before and
 * after.  There is no ratified RISC-V Keccak extension -- but a Keccak round is
 * built out of rotates and and-not, which is `rori` and `andn`, which is Zbb.
 * That is the whole argument for A21, and this file is the number that either
 * supports it or does not.
 *
 * SHAKE128 SQUEEZE, NOT A ROUND FUNCTION.  ML-KEM-768's matrix expansion is
 * nine polynomials of rejection sampling from SHAKE128, so squeezeblocks is the
 * shape of the real workload.  Timing KeccakF1600 alone would measure the
 * permutation and miss the absorb/squeeze bookkeeping around it, which is where
 * a byte-oriented instruction like `rev8` earns or fails to earn its place.
 *
 * THE SAME SOURCE, COMPILED TWICE, MEASURED ON ONE MACHINE IN ONE RUN -- the
 * identical argument ntt_bench.c makes at length.  Two bitstreams would measure
 * two placements; two runs would measure two clocks.  fips202.c is compiled
 * once -march=rv32im_zicsr and once with B and Zbkb added, the 16 exported
 * symbols renamed on the command line, and toolchain/kyber/ stays pristine.
 *
 * THE OUTPUTS ARE COMPARED, and that is not a formality: two builds of one
 * source are only a comparison if they compute the same thing.  A -march that
 * changed the arithmetic, or a rename that left one variant calling the
 * other's helpers, would show up as a speedup rather than as an error.
 */
#include "bench_io.h"

typedef unsigned char  kc_u8;
typedef unsigned int   kc_u32;

/* keccak_state is 26 uint64_t in the reference (25 lanes + pos).  Declared
 * here as a byte array of the right size so this file does not include
 * fips202.h -- which would namespace the symbols back and defeat the rename. */
#define KC_STATE_BYTES (26 * 8)
#define SHAKE128_RATE  168
#define KC_BLOCKS      16          /* 2688 bytes, ~= one ML-KEM matrix column */

/* Both builds of the reference SHAKE128, renamed at compile time. */
void bm_shake128_absorb_once(void *s, const kc_u8 *in, unsigned int inlen);
void bm_shake128_squeezeblocks(kc_u8 *out, unsigned int n, void *s);
void bb_shake128_absorb_once(void *s, const kc_u8 *in, unsigned int inlen);
void bb_shake128_squeezeblocks(kc_u8 *out, unsigned int n, void *s);

kc_u32 kc_cyc_m, kc_cyc_b, kc_ins_m, kc_ins_b;
kc_u32 kc_check, kc_sum;

static unsigned long long kc_state_m[KC_STATE_BYTES / 8];
static unsigned long long kc_state_b[KC_STATE_BYTES / 8];
static kc_u8 kc_out_m[SHAKE128_RATE * KC_BLOCKS];
static kc_u8 kc_out_b[SHAKE128_RATE * KC_BLOCKS];

void keccak_bench(void)
{
    /* A fixed 34-byte seed: 32 bytes of key plus the two matrix indices, which
     * is exactly what ML-KEM's XOF input looks like. */
    kc_u8 seed[34];
    kc_u32 c0, c1, i0, i1;
    int i;

    for (i = 0; i < 34; i++)
        seed[i] = (kc_u8)(i * 7 + 1);

    i0 = bench_minstret(); c0 = bench_mcycle();
    bm_shake128_absorb_once(kc_state_m, seed, 34);
    bm_shake128_squeezeblocks(kc_out_m, KC_BLOCKS, kc_state_m);
    c1 = bench_mcycle(); i1 = bench_minstret();
    kc_cyc_m = c1 - c0; kc_ins_m = i1 - i0;

    i0 = bench_minstret(); c0 = bench_mcycle();
    bb_shake128_absorb_once(kc_state_b, seed, 34);
    bb_shake128_squeezeblocks(kc_out_b, KC_BLOCKS, kc_state_b);
    c1 = bench_mcycle(); i1 = bench_minstret();
    kc_cyc_b = c1 - c0; kc_ins_b = i1 - i0;

    /* Every output byte must match, and the sum is reported so a capture can be
     * checked against a known value rather than only against itself. */
    kc_check = 0;
    kc_sum = 0;
    for (i = 0; i < (int)sizeof(kc_out_m); i++) {
        if (kc_out_m[i] != kc_out_b[i])
            kc_check |= 1u;
        kc_sum += kc_out_m[i];
    }
}
