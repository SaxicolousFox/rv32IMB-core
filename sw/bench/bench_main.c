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

        bench_printf("--- coremark ---\r\n");
        (void)coremark_main();
        bench_printf("cm_iterations=%u\r\n", (unsigned int)ITERATIONS);
        bench_printf("cm_cycles=%u\r\n", cm_cycles_stop - cm_cycles_start);
        bench_printf("cm_instret=%u\r\n", cm_instret_stop - cm_instret_start);

        bench_printf("=== end A13 ===\r\n");

        iter++;
        if (BENCH_BLOCKS && iter >= (unsigned int)BENCH_BLOCKS)
            return 0;
        delay_cycles(BENCH_GAP_CYCLES);
    }
}
