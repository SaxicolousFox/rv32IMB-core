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

/* Dhrystone's platform hook, called immediately outside its own timer. */
void setStats(int enable);
extern unsigned int bench_stat_cyc0, bench_stat_cyc1;
extern unsigned int bench_stat_ins0, bench_stat_ins1;

#endif
