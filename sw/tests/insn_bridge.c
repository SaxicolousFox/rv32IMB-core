/*
 * C0 acceptance driver: every Xkntt instruction, emitted from ordinary C
 * through sw/include/xkntt.h.  Each wrapper gets external linkage so the
 * instruction survives -O2; tb/unit/test_insn_bridge.py disassembles the
 * result and checks it against the frozen encodings.
 */
#include "xkntt.h"

uint32_t drv_kmm(uint32_t a, uint32_t b)               { return xk_kmm(a, b); }
uint32_t drv_kbfct(uint32_t a, uint32_t b)             { return xk_kbfct(a, b); }
uint32_t drv_kbfgs(uint32_t a, uint32_t b)             { return xk_kbfgs(a, b); }
uint32_t drv_kbmul0(uint32_t a, uint32_t b, uint32_t z){ return xk_kbmul0(a, b, z); }
uint32_t drv_kmac(uint32_t a, uint32_t b, uint32_t c)  { return xk_kmac(a, b, c); }
uint32_t drv_kbmul1(uint32_t a, uint32_t b)            { return xk_kbmul1(a, b); }

void     drv_cfg(uint32_t d, uint32_t m)               { xk_ntt_cfg(d, m); }
uint32_t drv_start(uint32_t m)                         { return xk_ntt_start(m); }
uint32_t drv_wait(void)                                { return xk_ntt_wait(); }
uint32_t drv_stat(void)                                { return xk_ntt_stat(); }
