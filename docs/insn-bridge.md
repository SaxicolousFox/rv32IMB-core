# The `.insn` bridge

`sw/include/xkntt.h` emits every Xkntt instruction from ordinary C using stock
GNU `as` and stock LLVM, with no toolchain patch. Plan step C0.

It exists so two schedules do not couple: RTL bring-up (Track A) and the
accelerated software stack (C6) need to *emit* these instructions long before
the LLVM backend (C2–C5) can *understand* them. When the real intrinsics land,
this is the only file that changes.

```c
#include "xkntt.h"

uint32_t pair = xk_pack(r[j], r[j + len]);      /* low half = low index */
uint32_t res  = xk_kbfct(pair, zeta);
r[j]       = xk_lo(res);
r[j + len] = xk_hi(res);
```

## Two build modes

Default: real instructions via `.insn`, RV32 only.

`-DXKNTT_EMULATE=1`: portable C with identical semantics, so the same algorithm
sources build and run on the host. This is what makes a full-length 10000-vector
KAT run take six seconds instead of an hour — see `docs/kyber-backends.md`.

## Volatile, and where it isn't

Tier-1 wrappers are deliberately **not** `asm volatile`. They are pure functions
of their register inputs with no side effects, so the compiler must stay free to
CSE them, hoist them out of loops and schedule around their latency — that
freedom is most of the point of putting the arithmetic in the ISA. The plan's C0
snippet shows `asm volatile`; using it would pessimise exactly the code whose
speedup the project exists to measure.

Tier-2 wrappers **are** volatile, and `kntt.start` / `kntt.wait` additionally
carry a `"memory"` clobber. They order against the ordinary `lw`/`sw` traffic to
the coprocessor aperture, which is the entire data plane. Without the clobber
the compiler may sink the stores that fill the input buffer past `kntt.start`,
or hoist the loads that drain the output buffer above `kntt.wait` — both silent
wrong answers, and the exact failure the plan's risk register flags for Tier-2
intrinsics.

## What the test checks

`tb/unit/test_insn_bridge.py` compiles `sw/tests/insn_bridge.c` at **-O0 and
-O2**, disassembles it, and decodes every custom-opcode word with
`model/isa/xkntt.py`, then re-encodes the decoded fields back to the exact bit
pattern. Optimisation is included on purpose: an inline-asm template with a
mis-declared constraint tends to survive -O0 and break at -O2, and C6 is only
ever built optimised.

It also checks `XKNTT_EMULATE` against the same golden model over 2000 random
32-bit inputs, including garbage in the halves an instruction ignores.

Fault-injected three ways — a wrong `funct3`, an R4 instruction emitted as
R-type, and a dropped term in the emulation. All three were caught.

Phase 1's `tb/unit/test_isa_encoding.py` covers the complementary case:
hand-written assembly against the hex literals published in
`docs/isa-spec.md`.
