/*
 * Host-side check of the XKNTT_EMULATE path in sw/include/xkntt.h.
 * Reads "<mnemonic-index> <rs1> <rs2> <rs3>" lines on stdin, prints the result
 * as hex.  tb/unit/test_insn_bridge.py feeds it random vectors and compares
 * against model/isa/xkntt.py, so the emulation used for fast host KAT runs is
 * held to the same golden model as the real instructions.
 */
#include <stdio.h>
#include "xkntt.h"

int main(void)
{
  unsigned op, a, b, c;
  while (scanf("%u %x %x %x", &op, &a, &b, &c) == 4) {
    uint32_t r = 0;
    switch (op) {
      case 0: r = xk_kmm(a, b);        break;
      case 1: r = xk_kbfct(a, b);      break;
      case 2: r = xk_kbfgs(a, b);      break;
      case 3: r = xk_kbmul0(a, b, c);  break;
      case 4: r = xk_kmac(a, b, c);    break;
      case 5: r = xk_kbmul1(a, b);     break;
      default: return 2;
    }
    printf("%08x\n", r);
  }
  return 0;
}
