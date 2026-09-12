/*
 * CoreMark port for the rvntt SoC, derived from toolchain/coremark/barebones/.
 *
 *   HAS_FLOAT 0     No FPU and no soft-float; the score is computed from the
 *                   raw mcycle count in Python (CoreMark/MHz =
 *                   iterations * 1e6 / cycles).
 *   MEM_METHOD      MEM_STACK: no malloc and no _sbrk.
 *   SEED_VOLATILE   PERFORMANCE_RUN fixes the seeds at 0/0/0x66 and the
 *                   compiler cannot fold them.
 *   ITERATIONS      Compile-time; CoreMark's run rules require at least 10 s
 *                   of wall time, so it is calibrated against measured
 *                   cycles/iteration on the board.
 */
#ifndef CORE_PORTME_H
#define CORE_PORTME_H

#include <stddef.h>

/* HAS_PRINTF is 1 and coremark.h turns ee_printf into printf, so every CoreMark
 * translation unit needs the declaration.  coremark.h includes THIS header and
 * nothing else, so this is the only place it can come from. */
#include "bench_io.h"

#define HAS_FLOAT   0
#define HAS_TIME_H  0
#define USE_CLOCK   0
#define HAS_STDIO   0
#define HAS_PRINTF  1      /* sw/bench/bench_io.c provides printf */

#ifndef COMPILER_VERSION
#define COMPILER_VERSION "GCC" __VERSION__
#endif
#ifndef COMPILER_FLAGS
#define COMPILER_FLAGS "unknown"    /* build_bench_image.py passes the real one */
#endif
#ifndef MEM_LOCATION
#define MEM_LOCATION "BRAM"
#endif

typedef signed short   ee_s16;
typedef unsigned short ee_u16;
typedef signed int     ee_s32;
typedef double         ee_f32;
typedef unsigned char  ee_u8;
typedef unsigned int   ee_u32;
/* ee_ptr_int must be wide enough to hold a pointer: ee_u32 on the target and
 * pointer-width on the 64-bit build host. */
#ifdef BENCH_HOST
typedef unsigned long  ee_ptr_int;
#else
typedef ee_u32         ee_ptr_int;
#endif
typedef size_t         ee_size_t;
#ifndef NULL
#define NULL ((void *)0)
#endif

#define align_mem(x) (void *)(4 + (((ee_ptr_int)(x)-1) & ~3))

#define CORETIMETYPE ee_u32
typedef ee_u32 CORE_TICKS;

#ifndef SEED_METHOD
#define SEED_METHOD SEED_VOLATILE
#endif
#ifndef MEM_METHOD
#define MEM_METHOD MEM_STACK
#endif
#ifndef MULTITHREAD
#define MULTITHREAD 1
#define USE_PTHREAD 0
#define USE_FORK    0
#define USE_SOCKET  0
#endif
#ifndef MAIN_HAS_NOARGC
#define MAIN_HAS_NOARGC 1
#endif
#ifndef MAIN_HAS_NORETURN
#define MAIN_HAS_NORETURN 0
#endif

extern ee_u32 default_num_contexts;

typedef struct CORE_PORTABLE_S
{
    ee_u8 portable_id;
} core_portable;

void portable_init(core_portable *p, int *argc, char *argv[]);
void portable_fini(core_portable *p);

/* The instruction counts bracketing the timed region, sampled in start_time()
 * and stop_time() -- i.e. the SAME window CoreMark times, not an approximation
 * of it.  sw/bench/bench_main.c prints them; the IPC in the report is this pair
 * over that pair. */
extern ee_u32 cm_cycles_start, cm_cycles_stop;
extern ee_u32 cm_instret_start, cm_instret_stop;

#endif
