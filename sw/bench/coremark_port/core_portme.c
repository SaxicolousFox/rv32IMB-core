/*
 * The two functions barebones/core_portme.c leaves as #error, plus the timing
 * hooks, for the rvntt SoC.  See core_portme.h for the configuration choices.
 */
#include "coremark.h"
#include "core_portme.h"
#include "bench_io.h"

#if VALIDATION_RUN
volatile ee_s32 seed1_volatile = 0x3415;
volatile ee_s32 seed2_volatile = 0x3415;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PERFORMANCE_RUN
volatile ee_s32 seed1_volatile = 0x0;
volatile ee_s32 seed2_volatile = 0x0;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PROFILE_RUN
volatile ee_s32 seed1_volatile = 0x8;
volatile ee_s32 seed2_volatile = 0x8;
volatile ee_s32 seed3_volatile = 0x8;
#endif
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;

/* TIMER_RES_DIVIDER stays at 1: mcycle is the finest and the only clock here,
 * and dividing it would throw away the exactness that makes CoreMark/MHz a
 * computed number rather than an estimate. */
#define TIMER_RES_DIVIDER 1
#define EE_TICKS_PER_SEC  ((CORETIMETYPE)(CORE_HZ / TIMER_RES_DIVIDER))

ee_u32 cm_cycles_start, cm_cycles_stop;
ee_u32 cm_instret_start, cm_instret_stop;

void
start_time(void)
{
    cm_instret_start = bench_minstret();
#ifdef BENCH_HPM
    bench_hpm_read(bench_hpm_cm0);
#endif
    cm_cycles_start  = bench_mcycle();
}

void
stop_time(void)
{
    cm_cycles_stop  = bench_mcycle();
#ifdef BENCH_HPM
    bench_hpm_read(bench_hpm_cm1);
#endif
    cm_instret_stop = bench_minstret();
}

CORE_TICKS
get_time(void)
{
    return (CORE_TICKS)(cm_cycles_stop - cm_cycles_start);
}

secs_ret
time_in_secs(CORE_TICKS ticks)
{
    /* HAS_FLOAT is 0, so secs_ret is ee_u32 and this truncates.  CoreMark uses
     * it only for its own "at least 10 seconds" gate and its own integer
     * Iterations/Sec; the reported score is computed from get_time()'s raw
     * cycles.  Truncation therefore makes the 10-second gate STRICTER, never
     * looser, which is the safe direction. */
    return (secs_ret)(ticks / EE_TICKS_PER_SEC);
}

ee_u32 default_num_contexts = 1;

void
portable_init(core_portable *p, int *argc, char *argv[])
{
    (void)argc;
    (void)argv;

    /* The UART needs no initialisation -- rvntt_uart_tx comes out of reset
     * ready -- but the size assertions below are the reason this function is
     * not empty, and they are worth keeping: ee_ptr_int too narrow for a
     * pointer corrupts the matrix algorithm in a way that shows up as a wrong
     * CRC and looks like a core bug. */
    if (sizeof(ee_ptr_int) != sizeof(ee_u8 *))
        ee_printf("ERROR! ee_ptr_int does not hold a pointer!\n");
    if (sizeof(ee_u32) != 4)
        ee_printf("ERROR! ee_u32 is not a 32b unsigned type!\n");
    p->portable_id = 1;
}

void
portable_fini(core_portable *p)
{
    p->portable_id = 0;
}
