/*
 * Console for the A13 benchmarks: UART on hardware, stdout on the build host.
 *
 * The dual target is not a convenience.  bench_printf is the only thing between
 * a correct cycle count and a wrong printed number, and a formatting bug there
 * is indistinguishable from a slow core -- the benchmark would "measure" a
 * number that was simply printed wrong.  Compiling the SAME printf natively and
 * diffing it against glibc over a corpus (tb/unit/test_bench_printf.py) is the
 * only check that can tell those two apart, and it costs a #ifdef.
 */
#ifndef BENCH_IO_H
#define BENCH_IO_H

void bench_putc(char c);
void bench_puts(const char *s);
int  bench_printf(const char *fmt, ...);

/* Dhrystone and CoreMark both call printf by name (via <stdio.h> and via
 * coremark.h's `#define ee_printf printf`), so the symbol has to BE printf. */
int  printf(const char *fmt, ...);

/* debug_printf is NOT defined here.  riscv-tests' own dhrystone.c defines it as
 * an empty function, which is what suppresses the sixty lines of "final values
 * of the variables ... should be" prose; that is upstream's configuration and
 * this port does not change it.  Those expectations are checked programmatically
 * instead -- see dhry_verify() in sw/bench/dhry_glue.c, which is strictly
 * stronger than printing them and hoping someone reads the capture. */

/* Architectural counters.  32-bit reads of the low halves: every timed region
 * here is far under 2^32 cycles (61 s at 70 MHz), and a 32-bit unsigned
 * difference is correct across one wrap, so the high halves buy nothing. */
unsigned int bench_mcycle(void);
unsigned int bench_minstret(void);

/* A20 (MODS_A2).  The six Zihpm counters, read at exactly the points where
 * mcycle and minstret are already read, so a region's counter deltas describe
 * the same window its cycle delta does.
 *
 * BENCH_HPM is a build-time switch and defaults OFF, for the reason A13's
 * numbers are still reproducible from A13's image: arming the counters adds CSR
 * writes to the startup path and reading them adds six csrr's to each timer
 * hook.  Neither lands inside a timed region -- setStats is called immediately
 * OUTSIDE Dhrystone's own timer, which is the whole reason that hook exists --
 * but "outside the timed region" is a claim that should be checkable rather
 * than asserted, and the way to check it is to build both ways and diff the
 * cycle counts.  A23 does exactly that. */
#define BENCH_HPM_N 6
unsigned int bench_mhpmcounter(int n);        /* n = 0..5 -> mhpmcounter3..8 */
void bench_hpm_arm(void);                     /* program the six selectors */
void bench_hpm_read(unsigned int *dst);       /* snapshot all six */
extern unsigned int bench_hpm_dhry0[BENCH_HPM_N], bench_hpm_dhry1[BENCH_HPM_N];
extern unsigned int bench_hpm_cm0[BENCH_HPM_N],   bench_hpm_cm1[BENCH_HPM_N];

/* Dhrystone's platform hook, called immediately outside its own timer. */
void setStats(int enable);
extern unsigned int bench_stat_cyc0, bench_stat_cyc1;
extern unsigned int bench_stat_ins0, bench_stat_ins1;

#endif
