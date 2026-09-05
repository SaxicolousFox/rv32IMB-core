/*
 * Dhrystone's platform header for the rvntt SoC.
 *
 * riscv-tests' dhrystone_main.c includes "util.h" AFTER "dhrystone.h", which is
 * the seam this port uses: everything below either supplies something the
 * benchmark expects the platform to provide, or overrides a macro dhrystone.h
 * has just defined.  That is why toolchain/riscv-tests/ stays pristine -- the
 * same rule that keeps toolchain/kyber/ pristine, and for the same reason: it is
 * also the checkout the riscv_tests regression runs against.
 *
 * Two overrides, both load-bearing:
 *
 *   NUMBER_OF_RUNS   dhrystone.h fixes it at 500.  The run count has to be known
 *                    to the reporting code, and the alternative -- letting the
 *                    benchmark's own `while (User_Time < Too_Small_Time)` loop
 *                    scale it by 10 -- leaves the final count in a LOCAL that
 *                    nothing outside main() can read.  Fixing it here means the
 *                    count printed is the count run, by construction.
 *
 *   Too_Small_Time   left at the riscv default of 1 cycle, deliberately, so that
 *                    loop terminates on the first pass and the run count is
 *                    exactly NUMBER_OF_RUNS.  The 2-second convention exists to
 *                    beat a coarse wall clock; mcycle is exact, so it buys
 *                    nothing here and would only make the count unpredictable.
 *
 * Do not "fix" dhrystone's own Microseconds/Dhrystones_Per_Second arithmetic
 * either -- it overflows 32-bit long at these cycle counts (cycles-per-run times
 * 10^6 is about 2.5e9).  Nothing here reads those two values; the raw cycle
 * counts go out over the UART and every rate is computed in Python.  See
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
 * times(2) branch: it emits a file-scope `struct tms time_info;` into every
 * translation unit (which the linker rejects -- build_bench_image.py compiles
 * with -fno-common, and so does modern GCC by default) and times its loop with
 * times().  -DPASS2 in tb/unit/test_bench_host.py suppresses the definition, and
 * these three overrides make the timer route to bench_mcycle() like the target
 * build, so time_info is never referenced either.  The host run is functional
 * only; nothing here is a measurement. */
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
