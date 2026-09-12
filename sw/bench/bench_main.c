/*
 * Benchmark program: Dhrystone then CoreMark, repeated forever.
 *
 * One image holds both benchmarks because the program is baked into the BRAM at
 * synthesis time.  There is no cache and no interrupt source, so running
 * Dhrystone first does not perturb CoreMark.  The report block repeats so a
 * capture started at any moment contains a whole one; `iter` distinguishes a
 * live capture from a stale file.
 *
 * Only run counts and raw mcycle/minstret deltas are printed.  Every rate is
 * computed by tb/fpga/parse_bench_uart.py, because Dhrystone's own
 * Microseconds/Dhrystones_Per_Second overflow 32-bit long at these counts.
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

#ifndef CORE_HZ
#error "CORE_HZ must be defined -- it comes from fpga/generated/soc_clk.svh"
#endif
#ifndef BENCH_FLAGS
#define BENCH_FLAGS "unknown"
#endif
#ifndef ITERATIONS
#define ITERATIONS 1
#endif

/* Quiet cycles between blocks, so a capture can tell block boundaries apart. */
#ifndef BENCH_GAP_CYCLES
#define BENCH_GAP_CYCLES 2000000u
#endif

/* 0 = forever (the board).  Host and Verilator runs set 1 so they terminate. */
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
    /* Armed once, before the first block; every block's deltas come from freely
     * running counters. */
    bench_hpm_arm();
#endif

    for (;;) {
        unsigned int bad;

        bench_printf("=== rvntt A13 ===\r\n");
        bench_printf("iter=0x%08x\r\n", iter);
        bench_printf("clk_hz=%u\r\n", (unsigned int)CORE_HZ);
#ifdef BENCH_HOST
        /* The host run has no cycle counter; the parser refuses to score a
         * capture carrying this line. */
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

        /* The "A13" markers name the report FORMAT; the parser keys on them. */
        bench_printf("=== end A13 ===\r\n");

        iter++;
        if (BENCH_BLOCKS && iter >= (unsigned int)BENCH_BLOCKS)
            return 0;
        delay_cycles(BENCH_GAP_CYCLES);
    }
}
