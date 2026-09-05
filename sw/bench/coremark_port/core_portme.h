/*
 * CoreMark port for the rvntt SoC.  Derived from toolchain/coremark/barebones/,
 * which is EEMBC's own template for a machine with no OS; the two #error stubs
 * it ships (barebones_clock and portable_init) are filled in in core_portme.c.
 *
 * Choices that affect the reported number, and why:
 *
 *   HAS_FLOAT 0     There is no FPU and no soft-float library linked, so
 *                   CoreMark's own "Iterations/Sec" is printed as an integer.
 *                   The score reported by this project is NOT that integer --
 *                   it is computed from the raw mcycle count in Python, exactly,
 *                   and CoreMark/MHz turns out not to depend on the clock at all
 *                   (iterations * 1e6 / cycles).  Keeping HAS_FLOAT off avoids
 *                   dragging in software floating point that would appear in the
 *                   image and in nothing else.
 *
 *   MEM_METHOD      MEM_STACK, the barebones default: TOTAL_DATA_SIZE bytes come
 *                   off the stack, so no malloc and no _sbrk.  128 KB of BRAM
 *                   holds the program, its data and a 2000-byte block with room
 *                   to spare.
 *
 *   SEED_VOLATILE   The seeds are volatile globals the compiler cannot fold, and
 *                   PERFORMANCE_RUN fixes them at 0/0/0x66.  This is what stops
 *                   CoreMark being computed at compile time, which is the whole
 *                   reason it is harder to game than Dhrystone.
 *
 *   ITERATIONS      Compile-time, because barebones has no way to auto-scale.
 *                   CoreMark's run rules require at least 10 seconds of wall
 *                   time and core_main.c itself flags the run as an error below
 *                   that, so this is calibrated against a measured cycles/
 *                   iteration on the board rather than guessed.
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
/* ee_ptr_int MUST be wide enough to hold a pointer -- CoreMark's matrix
 * algorithm rounds addresses through it, and a truncating typedef shows up as a
 * wrong CRC, i.e. exactly like a broken core.  It is ee_u32 on the target and
 * pointer-width on the 64-bit build host, which is the only difference between
 * the two builds that CoreMark can see. */
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
