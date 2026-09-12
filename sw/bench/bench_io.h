/*
 * Console for the benchmarks: UART on hardware, stdout on the build host, so
 * the same printf can be diffed against glibc (tb/unit/test_bench_printf.py).
 */
#ifndef BENCH_IO_H
#define BENCH_IO_H

void bench_putc(char c);
void bench_puts(const char *s);
int  bench_printf(const char *fmt, ...);

/* Dhrystone and CoreMark both call printf by name (via <stdio.h> and via
 * coremark.h's `#define ee_printf printf`), so the symbol has to BE printf. */
int  printf(const char *fmt, ...);

/* debug_printf is not defined here: riscv-tests' dhrystone.c defines it as an
 * empty function.  The expectations it would print are checked by
 * dhry_verify() in sw/bench/dhry_glue.c instead. */

/* Architectural counters.  32-bit reads of the low halves: every timed region
 * here is far under 2^32 cycles (61 s at 70 MHz), and a 32-bit unsigned
 * difference is correct across one wrap, so the high halves buy nothing. */
unsigned int bench_mcycle(void);
unsigned int bench_minstret(void);

/* The six Zihpm counters, read at exactly the points where mcycle and
 * minstret are already read.  BENCH_HPM defaults off; arming adds CSR writes
 * to the startup path and six csrr's to each timer hook, none inside a timed
 * region. */
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
