/*
 * xkntt.h -- the `.insn` bridge for the Xkntt vendor extension (plan step C0).
 *
 * This header lets ordinary C emit every Xkntt instruction using stock GNU as
 * and stock LLVM, with no toolchain patch.  It exists so RTL bring-up (Track A)
 * and the accelerated software stack (C6) are not blocked on the LLVM backend
 * (C2-C5).  When the real intrinsics land, only this file changes.
 *
 * The encodings here are the ones frozen in docs/isa-spec.md.  They are checked
 * against that document, against the Python model, and against GNU as by
 * tb/unit/test_insn_bridge.py -- so this file cannot silently drift.
 *
 * Two build modes:
 *   default          -- real instructions via .insn (RV32 target only)
 *   XKNTT_EMULATE=1  -- portable C with identical semantics, so the same
 *                       algorithm sources can be built and tested on the host.
 *                       The emulation is a transcription of the pq-crystals
 *                       reference reduction routines; docs/isa-spec.md section 3
 *                       is the normative definition and model/isa/xkntt.py the
 *                       executable one.
 */
#ifndef XKNTT_H
#define XKNTT_H

#include <stdint.h>

#define XKNTT_Q      3329
#define XKNTT_QINV   (-3327)          /* q^-1 mod 2^16, as the reference writes it */

/* ------------------------------------------------------------------ packing */
/* Coefficient pairs travel in one 32-bit register: low half first.  See
 * docs/isa-spec.md section 2.3.  Callers use these rather than open-coding the
 * shifts, so a change to the packing order is a one-line change here. */
static inline uint32_t xk_pack(int16_t lo, int16_t hi)
{
  return ((uint32_t)(uint16_t)hi << 16) | (uint32_t)(uint16_t)lo;
}
static inline int16_t xk_lo(uint32_t w) { return (int16_t)(uint16_t)(w & 0xFFFFu); }
static inline int16_t xk_hi(uint32_t w) { return (int16_t)(uint16_t)(w >> 16); }

#if defined(XKNTT_EMULATE) && XKNTT_EMULATE

/* --------------------------------------------------- portable C emulation -- */
static inline int16_t xk_mont(int32_t a)
{
  int16_t t = (int16_t)((int16_t)a * XKNTT_QINV);
  return (int16_t)((a - (int32_t)t * XKNTT_Q) >> 16);
}
static inline int16_t xk_barrett(int16_t a)
{
  const int16_t v = ((1 << 26) + XKNTT_Q / 2) / XKNTT_Q;   /* 20159 */
  int16_t t = (int16_t)(((int32_t)v * a + (1 << 25)) >> 26);
  return (int16_t)(a - (int16_t)(t * XKNTT_Q));
}

static inline uint32_t xk_kmm(uint32_t rs1, uint32_t rs2)
{
  return (uint32_t)(int32_t)xk_mont((int32_t)xk_lo(rs1) * (int32_t)xk_lo(rs2));
}
static inline uint32_t xk_kbfct(uint32_t rs1, uint32_t rs2)
{
  int16_t a = xk_lo(rs1), b = xk_hi(rs1), z = xk_lo(rs2);
  int16_t t = xk_mont((int32_t)z * b);
  /* low half is the low-index coefficient: r[j] = a + t, r[j+len] = a - t */
  return xk_pack((int16_t)(a + t), (int16_t)(a - t));
}
static inline uint32_t xk_kbfgs(uint32_t rs1, uint32_t rs2)
{
  int16_t a = xk_lo(rs1), b = xk_hi(rs1), z = xk_lo(rs2);
  int16_t an = xk_barrett((int16_t)(a + b));
  int16_t bn = xk_mont((int32_t)z * (int16_t)(b - a));
  return xk_pack(an, bn);
}
static inline uint32_t xk_kbmul0(uint32_t rs1, uint32_t rs2, uint32_t rs3)
{
  int16_t a0 = xk_lo(rs1), a1 = xk_hi(rs1);
  int16_t b0 = xk_lo(rs2), b1 = xk_hi(rs2);
  int16_t z  = xk_lo(rs3);
  int16_t t  = xk_mont((int32_t)a1 * b1);
  t = xk_mont((int32_t)t * z);
  return (uint32_t)(int32_t)(int16_t)(t + xk_mont((int32_t)a0 * b0));
}
static inline uint32_t xk_kbmul1(uint32_t rs1, uint32_t rs2)
{
  int16_t a0 = xk_lo(rs1), a1 = xk_hi(rs1);
  int16_t b0 = xk_lo(rs2), b1 = xk_hi(rs2);
  return (uint32_t)(int32_t)(int16_t)(xk_mont((int32_t)a0 * b1) +
                                      xk_mont((int32_t)a1 * b0));
}
static inline uint32_t xk_kmac(uint32_t rs1, uint32_t rs2, uint32_t rs3)
{
  int16_t a = xk_lo(rs1), b = xk_lo(rs2), c = xk_lo(rs3);
  return (uint32_t)(int32_t)xk_barrett((int16_t)(c + xk_mont((int32_t)a * b)));
}

/* Tier 2 has no host emulation: it is a coprocessor, not arithmetic. */

#else  /* real instructions */

#if !defined(__riscv) || __riscv_xlen != 32
# error "xkntt.h needs an RV32 target; build with -DXKNTT_EMULATE=1 for the host"
#endif

/*
 * Tier 1 is deliberately NOT `asm volatile`.  These are pure functions of their
 * register inputs with no side effects, so the compiler must be free to CSE
 * them, hoist them out of loops and schedule around their latency -- that
 * freedom is most of the point of putting the arithmetic in the ISA.  The
 * plan's C0 snippet shows `asm volatile`; using it here would pessimise exactly
 * the code whose speedup we are trying to measure.  Tier 2 below IS volatile,
 * because those instructions do have side effects.
 */
#define XK_R(op, f3, f7, out, in1, in2) \
  __asm__ (".insn r " #op ", " #f3 ", " #f7 ", %0, %1, %2" \
           : "=r"(out) : "r"(in1), "r"(in2))

#define XK_R4(op, f3, f2, out, in1, in2, in3) \
  __asm__ (".insn r4 " #op ", " #f3 ", " #f2 ", %0, %1, %2, %3" \
           : "=r"(out) : "r"(in1), "r"(in2), "r"(in3))

static inline uint32_t xk_kmm(uint32_t rs1, uint32_t rs2)
{ uint32_t rd; XK_R(0x0B, 0, 0x00, rd, rs1, rs2); return rd; }

static inline uint32_t xk_kbfct(uint32_t rs1, uint32_t rs2)
{ uint32_t rd; XK_R(0x0B, 1, 0x00, rd, rs1, rs2); return rd; }

static inline uint32_t xk_kbfgs(uint32_t rs1, uint32_t rs2)
{ uint32_t rd; XK_R(0x0B, 2, 0x00, rd, rs1, rs2); return rd; }

static inline uint32_t xk_kbmul0(uint32_t rs1, uint32_t rs2, uint32_t rs3)
{ uint32_t rd; XK_R4(0x0B, 3, 0, rd, rs1, rs2, rs3); return rd; }

static inline uint32_t xk_kmac(uint32_t rs1, uint32_t rs2, uint32_t rs3)
{ uint32_t rd; XK_R4(0x0B, 4, 0, rd, rs1, rs2, rs3); return rd; }

static inline uint32_t xk_kbmul1(uint32_t rs1, uint32_t rs2)
{ uint32_t rd; XK_R(0x0B, 5, 0x00, rd, rs1, rs2); return rd; }

/* ------------------------------------------------------------------ Tier 2 --
 * Volatile, and start/wait carry a "memory" clobber: they order against the
 * ordinary lw/sw traffic to the coprocessor aperture, which is the whole data
 * plane.  Without the clobber the compiler may sink the stores that fill the
 * input buffer past kntt.start, or hoist the loads that drain the output buffer
 * above kntt.wait.  Both are silent wrong answers -- the exact failure mode the
 * plan's risk register flags for Tier-2 intrinsics.
 */
static inline void xk_ntt_cfg(uint32_t desc, uint32_t mode)
{ __asm__ volatile (".insn r 0x2B, 0, 0x00, x0, %0, %1" :: "r"(desc), "r"(mode)); }

static inline uint32_t xk_ntt_start(uint32_t mode)
{ uint32_t rd;
  __asm__ volatile (".insn r 0x2B, 1, 0x00, %0, %1, x0"
                    : "=r"(rd) : "r"(mode) : "memory");
  return rd; }

static inline uint32_t xk_ntt_wait(void)
{ uint32_t rd;
  __asm__ volatile (".insn r 0x2B, 2, 0x00, %0, x0, x0" : "=r"(rd) :: "memory");
  return rd; }

static inline uint32_t xk_ntt_stat(void)
{ uint32_t rd;
  __asm__ volatile (".insn r 0x2B, 3, 0x00, %0, x0, x0" : "=r"(rd));
  return rd; }

#endif /* XKNTT_EMULATE */

/* ------------------------------------------------ Tier-2 mode/status fields */
/* docs/isa-spec.md sections 5.1 and 5.2. */
#define XK_OP_NTT      0u
#define XK_OP_INTT     1u
#define XK_OP_BASEMUL  2u
#define XK_MODE(op, src, dst)  ((uint32_t)(op) | ((uint32_t)(src) << 2) | \
                                ((uint32_t)(dst) << 3))
#define XK_ST_BUSY     0x1u
#define XK_ST_DONE     0x2u
#define XK_ST_ERR      0x4u
#define XK_ST_CYCLES(s) ((s) >> 16)

#endif /* XKNTT_H */
