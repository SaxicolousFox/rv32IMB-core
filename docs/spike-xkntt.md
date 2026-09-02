# Spike and the Xkntt extension

How the ISS fork implements `docs/isa-spec.md`, what is provisional in it, and
how to rebuild it. Plan step C1.

Spike is on the critical path for Track A: lockstep cosimulation against it is
the core's strongest verification technique (plan A5, I1), and after the
coprocessor lands there is no other golden reference for the *extended* ISA.

## Where things live

The fork is `toolchain/spike-src` on branch `xkntt`, rebased onto the pin in
`toolchain/upstream-pins.txt`. Patches are exported to `patches/spike/` by
`toolchain/patches.sh export spike`; `make regress` checks the two stay in sync.

| File | Role |
|---|---|
| `riscv/xkntt_encoding.h` | `MATCH_`/`MASK_`/`DECLARE_INSN` for all ten instructions |
| `riscv/encoding.h` | one added `#include` — that generated file is otherwise untouched |
| `riscv/xkntt.h` | Montgomery/Barrett primitives, packing helpers, `kntt_t` |
| `riscv/xkntt.cc` | Tier-2 state machine, NTT/INTT/basemul, zeta generation |
| `riscv/insns/{kmm,kbfct,kbfgs,kbmul0,kbmul1,kmac}.h` | Tier-1 semantics |
| `riscv/insns/kntt_{cfg,start,wait,stat}.h` | Tier-2 control plane |
| `riscv/insn_template.h` | includes `xkntt.h` so every insn body sees the helpers |
| `riscv/processor.h` | the hart owns a `kntt_t` |
| `riscv/sim.cc` | maps that `kntt_t` onto the bus at `KNTT_BASE` |
| `disasm/disasm.cc` | disassembly, so `--log-commits` stays readable for A5 |
| `disasm/isa_parser.cc` | accepts `xkntt` / `xkntt0p1` in the ISA string |

## Running it

```sh
source toolchain/env.sh
spike --isa=rv32im_zicsr_zicntr_xkntt0p1 prog.elf   # m is A14's; the fork is unchanged
```

Omit `xkntt` and every custom instruction becomes an illegal instruction —
`require_extension(EXT_XKNTT)` in each body — which the regression checks in
both directions.

Rebuilding after editing the fork:

```sh
cd toolchain/spike-build && make -j"$(nproc)" && make install
```

Editing `riscv/xkntt_encoding.h`, `riscv/xkntt.h` or `riscv/insn_template.h`
rebuilds all ~1600 generated instruction files, which takes several minutes.
Editing a single `riscv/insns/*.h` or `riscv/xkntt.cc` takes seconds.

## Decode

Reserved encoding fields are strict, per `docs/isa-spec.md` Deviation 3: they
are folded into the `MASK_*` values, so a nonzero reserved field decodes to no
instruction and traps. This matters because plan A3 compares the RTL decoder
against a Python decoder over 10^6 random words — a lax Spike and a strict RTL
would disagree on exactly those words, and the divergence would surface during
cosimulation rather than here.

`tb/cosim/test_spike_xkntt.py` runs that comparison against Spike directly:
20000 random words on the two custom opcodes, checking that Spike accepts
exactly the set `model/isa/xkntt.py` accepts.

## Tier 2 — what is real and what is provisional

Frozen by the spec and implemented exactly: the mode word (5.1), the status
word (5.2), and the error policy (5.4) — an invalid mode or a start while busy
sets `ERR` and returns 0 rather than trapping.

**Provisional, pending Track B**, and marked as such in `riscv/xkntt.h`:

- **Aperture layout.** `KNTT_BASE = 0x50000000`, two 512-byte polynomial
  buffers A and B at offsets `0x000` and `0x200`, 256 coefficients each,
  packed two per 32-bit word with the low-index coefficient in the low half.
  Spec section 9 defers the register map to plan B7.
- **`kntt.cfg`'s operand descriptor** (`rs1`) is latched and otherwise unused;
  its layout depends on the coprocessor's buffer organisation, decided in B3.
- **Basemul operands.** `OP=2` multiplies buffer A by buffer B, with `SRC`
  selecting which is first. The spec fixes no descriptor for a two-operand
  instruction yet.
- **Cycle counts.** `CYCLES` is a model — `P = 4` butterfly lanes, one
  butterfly per lane per cycle, plus a fixed fill/drain — not a measurement.
  Spike is functional, not cycle-accurate. The RTL replaces it with real
  counts, and B8's constant-time assertion must use those, not these.

Two consequences of Spike being functional rather than timed:

- `BUSY` is never observably set: the operation completes inside `kntt.start`.
  `kntt.wait` therefore always returns immediately, and the adversarial cases
  in plan I2 — start while busy, load from the aperture mid-operation — can
  only be exercised in RTL.
- Spec section 5.7 requires that an aperture access while busy stall rather
  than return stale data. Spike satisfies this vacuously; the obligation is
  entirely on the RTL.

## What the regression checks

`tb/cosim/test_spike_xkntt.py`

- Tier-1 semantics against `model/isa/xkntt.py`, read out of `--log-commits`
  (the same artifact the A5 differ will consume).
- Reserved-field strictness, both directed and over 20000 random words.
- Extension gating, with a positive control so a trap-everywhere Spike cannot
  pass by accident.
- Tier-2 forward NTT, inverse NTT and basemul through the MMIO aperture,
  against `model/ntt_ref.py`.
- The Tier-2 error policy.

`tb/cosim/test_kyber_kat_spike.py` — ML-KEM-768 known-answer tests, described
in `docs/kyber-backends.md`.

## Fault injection

Every check above was validated by breaking something and confirming detection:

| Injected fault | Caught by |
|---|---|
| `kbfgs` multiplies by `(a - b)` — the plan's wrong sign | Tier-1 vector comparison |
| `xkntt.cc` forward butterfly computes `t - r[j]` | Tier-2 forward NTT |
| `require_extension` removed from `kmm.h` | extension gating |
| `MASK_KNTT_STAT` widened to ignore `rs1`/`rs2` | strictness *and* the random sweep (1229 of 20000 words) |
