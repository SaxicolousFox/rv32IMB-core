/*
 * Dhrystone's platform header for the rvntt SoC.
 *
 * riscv-tests' dhrystone_main.c includes "util.h" after "dhrystone.h", which
 * is the seam this port uses so that toolchain/riscv-tests/ stays pristine.
 * Two overrides:
 *
 *   NUMBER_OF_RUNS   fixed here so the count printed is the count run; the
 *                    benchmark's own scaling loop leaves the final count in a
 *                    local nothing outside main() can read.
 *   Too_Small_Time   left at 1 cycle so that loop terminates on the first
 *                    pass; mcycle is exact.
 *
 * Dhrystone's own Microseconds/Dhrystones_Per_Second overflow 32-bit long at
 * these counts; nothing reads them, and every rate is computed in
 * tb/fpga/parse_bench_uart.py.
 */
#ifndef __UTIL_H
#define __UTIL_H

#include <stdint.h>

extern void setStats(int enable);

/* Dhrystone's Start_Timer/Stop_Timer expand to read_csr(mcycle).  On the build
 * host there is no such CSR, so it routes to bench_mcycle(), which returns a
 * real monotonic count there -- returning a constant would make User_Time zero
 * and spin dhrystone's `while (User_Time < Too_Small_Time)` forever. */
#ifdef BENCH_HOST
extern unsigned int bench_mcycle(void);
#define read_csr(reg) ((unsigned long)bench_mcycle())

/* On the build host __riscv is not defined, so dhrystone.h falls into its
 * times(2) branch and emits a file-scope `struct tms time_info;`.  -DPASS2
 * suppresses the definition and these overrides route the timer to
 * bench_mcycle() like the target build. */
#undef  Start_Timer
#undef  Stop_Timer
#undef  Too_Small_Time
#define Start_Timer()  Begin_Time = (long)bench_mcycle()
#define Stop_Timer()   End_Time   = (long)bench_mcycle()
#define Too_Small_Time 1
#else
#define read_csr(reg) ({ unsigned long __tmp;                   \
      __asm__ volatile ("csrr %0, " #reg : "=r"(__tmp));        \
      __tmp; })
#endif

#ifndef DHRY_RUNS
#define DHRY_RUNS 50000
#endif

#undef  NUMBER_OF_RUNS
#define NUMBER_OF_RUNS DHRY_RUNS

#endif
