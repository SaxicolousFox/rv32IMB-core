# rtl/core — Track A, the RV32I pipeline

**Currently empty** — a P0.2 skeleton directory. Nothing here yet.

Track A is the critical path from here (plan A1–A8, then A-FPGA). Spike now
exists as a golden reference for the *extended* ISA, which is what makes A5's
lockstep cosimulation possible; that ordering was the whole reason C1 came
before this.

## What already exists for you

`tb/cosim/spike_asm.py` is written and exercised. It has:

- the `--log-commits` parser (`core   0: 3 0x<pc> (0x<insn>) x<rd> 0x<wdata>`),
  returning `(pc, insn_word, [(rd, value)])` per commit;
- `TRAP_HANDLER` — exits with code 42, turning "did this trap?" into an exit
  status instead of a string match on Spike's stderr;
- `TRAP_HANDLER_SKIP` — steps over the faulting instruction via `mepc` and
  continues, which is what lets one program probe thousands of encodings in a
  single run;
- `skipped_traps()`, `symbol()`, and a bare-metal build/link helper.

`sw/include/xkntt.h` emits every custom instruction from C — see
`docs/insn-bridge.md`. Use it rather than hand-writing `.insn` in testbenches.

## The bootrom gotcha

**Spike executes 5 instructions at `0x1000` before jumping to `0x80000000`.**
The A5 differ must skip them, or every comparison is misaligned by five entries
from the first cycle and the diagnostic points nowhere useful.

## Before you start

- `source toolchain/env.sh` (see the root `CLAUDE.md`).
- Read `docs/spike-xkntt.md` — it documents what Spike models faithfully and
  what it does not. Two things it does **not**: `BUSY` is never observably set
  (Spike is functional, so a Tier-2 operation completes inside `kntt.start`),
  and `CYCLES` is a model rather than a measurement. Plan I2's adversarial
  cases — start-while-busy, aperture access mid-operation — can only be
  exercised here in RTL.
- `rtl/common/` is shared with Track B. Coordinate before editing it.
