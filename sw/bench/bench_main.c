/*
 * A13's program: Dhrystone then CoreMark, on the A12 SoC, forever.
 *
 * Two benchmarks in ONE image rather than two bitstreams, because the program is
 * baked into the BRAM by $readmemh at synthesis time -- a second benchmark would
 * otherwise mean a second twelve-minute implementation run and a second
 * programming pass, for no measurement benefit.  There is no cache, no DRAM
 * refresh and no interrupt source on this SoC, so running Dhrystone first cannot
 * perturb CoreMark: the machine CoreMark starts on is bit-identical to the one it
 * would have started on alone.  That is worth stating rather than assuming,
 * because on any machine with a cache it would not be true.
 *
 * The block REPEATS, like A12's, so a capture started at any moment catches a
 * whole one and `iter` distinguishes a live capture from a stale file.  It also
 * means the plan's "reproducible across three runs" is satisfiable from a single
 * capture -- three consecutive blocks, byte-for-byte comparable -- as well as
 * across three separate programming passes.
 *
 * Every rate is left to the parser.  This program prints run counts and RAW
 * mcycle/minstret deltas and nothing derived, because Dhrystone's own
 * Microseconds and Dhrystones_Per_Second overflow 32-bit long at these cycle
 * counts (cycles-per-run * 10^6 is about 2.5e9) and would print a plausible
 * wrong number.  Python has no such limit.  See tb/fpga/parse_bench_uart.py.
 */
#include "bench_io.h"
#include "dhrystone.h"
#include "util.h"

extern int  dhry_main(int argc, char **argv);      /* dhrystone_main.c, -Dmain= */
extern int  coremark_main(void);                   /* core_main.c,      -Dmain= */
extern unsigned int dhry_verify(int runs);
extern unsigned int cm_cycles_start, cm_cycles_stop;
extern unsigned int cm_instret_start, cm_instret_stop;
extern long Begin_Time, End_Time;

/* A16's dual baseline.  Behind BENCH_NTT because it needs two RISC-V builds of
 * the reference NTT side by side, which the build-host and the pre-A14 image
 * cannot have -- and because A13's numbers must stay reproducible from A13's
 * image, byte for byte, rather than becoming "A13's numbers plus a section". */
#ifdef BENCH_KECCAK
/* A21/A23.  The half of ML-KEM a hardware NTT never touches -- plan 10 M3's
 * other term, and the reason B was added at all. */
extern void keccak_bench(void);
extern unsigned int kc_cyc_m, kc_cyc_b, kc_ins_m, kc_ins_b;
extern unsigned int kc_check, kc_sum;
#endif

#ifdef BENCH_NTT
extern void ntt_bench(void);
extern unsigned int ntt_cyc_i, ntt_cyc_m, ntt_ins_i, ntt_ins_m;
extern unsigned int ntt_check, ntt_sum;
#endif

#ifndef CORE_HZ
#error "CORE_HZ must be defined -- it comes from fpga/generated/soc_clk.svh"
#endif
#ifndef BENCH_FLAGS
#define BENCH_FLAGS "unknown"
#endif
#ifndef ITERATIONS
#define ITERATIONS 1
#endif

/* Cycles of quiet between blocks.  Long enough that a capture can tell block
 * boundaries apart, short enough not to dominate a 30-second block. */
#ifndef BENCH_GAP_CYCLES
#define BENCH_GAP_CYCLES 2000000u
#endif

/* 0 = forever, which is what the board runs.  The functional host run and the
 * Verilator run set it to 1 so they terminate on their own rather than relying
 * on a timeout to decide they are finished -- a timeout cannot tell "finished"
 * from "hung in the last block". */
#ifndef BENCH_BLOCKS
#define BENCH_BLOCKS 0
#endif

static void delay_cycles(unsigned int n)
{
    unsigned int t0 = bench_mcycle();
    while ((unsigned int)(bench_mcycle() - t0) < n)
        ;
}

int main(void)
{
    unsigned int iter = 0;

#ifdef BENCH_HPM
    /* A20.  Armed once, before the first block, and never touched again --
     * mcountinhibit stays clear and the selectors stay programmed, so every
     * block's deltas are taken from freely running counters.  Arming inside the
     * loop would reprogram them between blocks and make the reproducibility
     * check across blocks weaker rather than stronger. */
    bench_hpm_arm();
#endif

    for (;;) {
        unsigned int bad;

        bench_printf("=== rvntt A13 ===\r\n");
        bench_printf("iter=0x%08x\r\n", iter);
        bench_printf("clk_hz=%u\r\n", (unsigned int)CORE_HZ);
#ifdef BENCH_HOST
        /* The build-host functional run has no cycle counter, so every timing
         * field below is meaningless there.  It says so IN THE OUTPUT rather
         * than relying on whoever reads it to remember: tb/fpga/parse_bench_uart.py
         * refuses to compute a score from a capture carrying this line, which is
         * the one way a host result could otherwise be mistaken for a measured
         * one. */
        bench_printf("host=1\r\n");
#endif
        bench_printf("flags=%s\r\n", BENCH_FLAGS);

        bench_printf("--- dhrystone ---\r\n");
        (void)dhry_main(0, 0);
        bad = dhry_verify(DHRY_RUNS);
        bench_printf("dhry_runs=%d\r\n", (int)DHRY_RUNS);
        bench_printf("dhry_cycles=%u\r\n",
                     (unsigned int)((unsigned long)End_Time - (unsigned long)Begin_Time));
        bench_printf("dhry_stat_cycles=%u\r\n", bench_stat_cyc1 - bench_stat_cyc0);
        bench_printf("dhry_stat_instret=%u\r\n", bench_stat_ins1 - bench_stat_ins0);
        bench_printf("dhry_check=0x%08x\r\n", bad);
#ifdef BENCH_HPM
        /* A20.  Deltas only, and raw -- nothing derived, for the same reason
         * every other counter here is printed raw: the parser owns the
         * arithmetic because 32-bit overflow in this program has already once
         * produced a plausible wrong number. */
        bench_printf("dhry_hpm_loaduse=%u\r\n",    bench_hpm_dhry1[0] - bench_hpm_dhry0[0]);
        bench_printf("dhry_hpm_exstall=%u\r\n",    bench_hpm_dhry1[1] - bench_hpm_dhry0[1]);
        bench_printf("dhry_hpm_redirect=%u\r\n",   bench_hpm_dhry1[2] - bench_hpm_dhry0[2]);
        bench_printf("dhry_hpm_mispredict=%u\r\n", bench_hpm_dhry1[3] - bench_hpm_dhry0[3]);
        bench_printf("dhry_hpm_btbhit=%u\r\n",     bench_hpm_dhry1[4] - bench_hpm_dhry0[4]);
        bench_printf("dhry_hpm_xfertaken=%u\r\n",  bench_hpm_dhry1[5] - bench_hpm_dhry0[5]);
#endif

        bench_printf("--- coremark ---\r\n");
        (void)coremark_main();
        bench_printf("cm_iterations=%u\r\n", (unsigned int)ITERATIONS);
        bench_printf("cm_cycles=%u\r\n", cm_cycles_stop - cm_cycles_start);
        bench_printf("cm_instret=%u\r\n", cm_instret_stop - cm_instret_start);
#ifdef BENCH_HPM
        bench_printf("cm_hpm_loaduse=%u\r\n",    bench_hpm_cm1[0] - bench_hpm_cm0[0]);
        bench_printf("cm_hpm_exstall=%u\r\n",    bench_hpm_cm1[1] - bench_hpm_cm0[1]);
        bench_printf("cm_hpm_redirect=%u\r\n",   bench_hpm_cm1[2] - bench_hpm_cm0[2]);
        bench_printf("cm_hpm_mispredict=%u\r\n", bench_hpm_cm1[3] - bench_hpm_cm0[3]);
        bench_printf("cm_hpm_btbhit=%u\r\n",     bench_hpm_cm1[4] - bench_hpm_cm0[4]);
        bench_printf("cm_hpm_xfertaken=%u\r\n",  bench_hpm_cm1[5] - bench_hpm_cm0[5]);
#endif

#ifdef BENCH_NTT
        bench_printf("--- ntt ---\r\n");
        ntt_bench();
        bench_printf("ntt_cycles_rv32i=%u\r\n",   ntt_cyc_i);
        bench_printf("ntt_instret_rv32i=%u\r\n",  ntt_ins_i);
        bench_printf("ntt_cycles_rv32im=%u\r\n",  ntt_cyc_m);
        bench_printf("ntt_instret_rv32im=%u\r\n", ntt_ins_m);
        bench_printf("ntt_check=0x%08x\r\n",      ntt_check);
        bench_printf("ntt_sum=0x%08x\r\n",        ntt_sum);
#endif

        /* The A13 markers are the report FORMAT's name, not the step's, and the
         * parser keys on them.  A16 adds a section inside the block and does not
         * rename the block. */
#ifdef BENCH_KECCAK
        bench_printf("--- keccak ---\r\n");
        keccak_bench();
        bench_printf("kc_cycles_rv32im=%u\r\n",   kc_cyc_m);
        bench_printf("kc_instret_rv32im=%u\r\n",  kc_ins_m);
        bench_printf("kc_cycles_rv32imb=%u\r\n",  kc_cyc_b);
        bench_printf("kc_instret_rv32imb=%u\r\n", kc_ins_b);
        bench_printf("kc_check=0x%08x\r\n",       kc_check);
        bench_printf("kc_sum=0x%08x\r\n",         kc_sum);
#endif

        bench_printf("=== end A13 ===\r\n");

        iter++;
        if (BENCH_BLOCKS && iter >= (unsigned int)BENCH_BLOCKS)
            return 0;
        delay_cycles(BENCH_GAP_CYCLES);
    }
}
