/*
 * The dual baseline: the SAME reference NTT, compiled twice, measured on ONE
 * machine in one run (MODS_A A16).
 *
 * WHY THIS EXISTS.  A13 measured this core with no M extension and found the
 * reference ML-KEM NTT taking 148,645 dynamic instructions where an RV32IM
 * build takes 23,795 -- a factor of 6.25, and plan B6's own estimate of
 * "15,000-30,000 cycles" for the software baseline only lands inside that band
 * for the RV32IM number.  So B6 was written assuming a hardware multiplier
 * while 1.5 scoped the core without one, and every headline speedup the
 * coprocessor eventually claims depends on which of those two numbers it is
 * measured against.
 *
 * MEASURING BOTH ON THE SAME SILICON IS THE POINT.  Two bitstreams would
 * measure two placements; two runs would measure two clocks.  Here the two
 * builds sit side by side in one image, share a register file and a BRAM, and
 * are timed by the same counter within microseconds of each other, so the ratio
 * is a property of the instruction set and of nothing else.
 *
 * HOW THE TWO BUILDS COEXIST.  fpga/scripts/build_bench_image.py compiles
 * toolchain/kyber/ref/ntt.c and reduce.c twice -- once -march=rv32i_zicsr, once
 * -march=rv32im_zicsr -- renaming the six exported symbols on the command line
 * with -D.  The checkout stays pristine, which is the same rule that protects
 * the reference's own KATs, and nothing here is a copy of the reference that
 * could drift from it.
 *
 * THE OUTPUTS ARE COMPARED, and that is not a formality.  Two builds of the
 * same source measured against each other are only a comparison if they compute
 * the same thing: a -march that quietly changed the arithmetic, or a symbol
 * rename that left one variant calling the other's helpers, would show up as a
 * speedup rather than as an error.  ntt_check is the guard, and the parser
 * requires it to be zero.
 */
#include "bench_io.h"

typedef signed short   ntt_i16;
typedef unsigned int   ntt_u32;

/* Both builds of the reference ntt(), renamed at compile time. */
void bi_ntt(ntt_i16 *p);        /* -march=rv32i_zicsr  : libgcc __mulsi3     */
void bm_ntt(ntt_i16 *p);        /* -march=rv32im_zicsr : hardware MUL/MULH   */

static ntt_i16 ntt_poly_i[256];
static ntt_i16 ntt_poly_m[256];

/* Reported so the parser can see what was measured, and so a run that somehow
 * transformed a different polynomial is distinguishable from one that did not. */
unsigned int ntt_cyc_i, ntt_cyc_m;
unsigned int ntt_ins_i, ntt_ins_m;
unsigned int ntt_check;
unsigned int ntt_sum;

/* A fixed, non-trivial polynomial.  Coefficients are spread across the full
 * signed range mod q rather than being small: the reference's Montgomery
 * multiply is data-independent in cycle count, but a poly of zeros would make a
 * multiplier that returns zero look correct. */
static void ntt_seed(ntt_i16 *p)
{
    ntt_u32 x = 0x12345678u;
    int i;
    for (i = 0; i < 256; i++) {
        x = x * 1103515245u + 12345u;
        p[i] = (ntt_i16)(((x >> 9) % 3329u) - 1664u);
    }
}

void ntt_bench(void)
{
    unsigned int c0, c1, i0, i1;
    int i;

    ntt_seed(ntt_poly_i);
    ntt_seed(ntt_poly_m);

    /* minstret first and mcycle second on entry, the other way round on exit --
     * the same ordering dhry_glue.c uses, so the cycle window encloses the
     * instruction window rather than crossing it. */
    i0 = bench_minstret();
    c0 = bench_mcycle();
    bi_ntt(ntt_poly_i);
    c1 = bench_mcycle();
    i1 = bench_minstret();
    ntt_cyc_i = c1 - c0;
    ntt_ins_i = i1 - i0;

    i0 = bench_minstret();
    c0 = bench_mcycle();
    bm_ntt(ntt_poly_m);
    c1 = bench_mcycle();
    i1 = bench_minstret();
    ntt_cyc_m = c1 - c0;
    ntt_ins_m = i1 - i0;

    /* Bit-for-bit, all 256 coefficients.  A checksum alone could cancel. */
    ntt_check = 0;
    ntt_sum = 0;
    for (i = 0; i < 256; i++) {
        if (ntt_poly_i[i] != ntt_poly_m[i])
            ntt_check++;
        ntt_sum = ntt_sum * 31u + (ntt_u32)(unsigned short)ntt_poly_i[i];
    }
}
