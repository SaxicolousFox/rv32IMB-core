# rtl/core — Track A, the RV32I pipeline

Track A is the critical path from here (plan A1–A13, with A12 being the
"A-FPGA" milestone). Spike exists as a golden reference for the *extended*
ISA, which is what makes A5's lockstep cosimulation possible; that ordering
was the whole reason C1 came before this.

## What is here

| File | Step | Verified by |
|---|---|---|
| `rv32i_pkg.sv` | A1 | lint (waived `UNUSEDPARAM`), Vivado elaboration |
| `rvntt_regfile.sv` | A1 | `verilator_sim_regfile`, `formal_regfile`, Vivado elaboration |

### `rv32i_pkg.sv`

Opcode/funct3/funct7 constants and the four pipeline-register packed structs.
Two conventions in it are load-bearing:

- **Never cast a raw instruction field to one of these enums.** `opcode_e'(x)`
  on an arbitrary word both hides illegal encodings and trips Verilator's
  ENUMVALUE check. Decoders *compare* against members, so an unlisted encoding
  falls through to the illegal-instruction path — which is exactly what A3 has
  to detect, and what the strict-reserved-field rule in the root `CLAUDE.md`
  depends on.
- **`ctrl_t.mem_op` is a plain `logic [2:0]`, deliberately not an enum.** It
  carries funct3 verbatim including the reserved load/store widths, and those
  must stay representable so the LSU can reject them rather than silently
  aliasing onto a legal width.

`insn` is carried the full length of the pipe on purpose: A5's commit tracer
and A11's RVFI port both need `(pc, insn, rd, wdata)` at WB. It costs 128 FFs
of 126,800, which is not worth a conditional-compile knob.

`IMM_Z` and the `is_csr` / `is_mret` control bits are present from A1 even
though A9 is what uses them, so that A9 does not have to widen `imm_fmt_e` and
re-verify every downstream case statement.

### `rvntt_regfile.sv`

32×32, **three** read ports (plan §1.1 took the R4-type recommendation, so
`kbmul0` and `kmac` read rs3), one write port, write-through, no reset port.

- **Write-through is a correctness simplification, not an optimisation.** It
  makes a WB-stage write visible to an ID-stage read in the same cycle, which
  deletes the entire WB→ID forwarding case from A6.
- **Power-on zero instead of a reset.** Matches Spike's architectural state at
  reset so A5's cosim starts aligned, and does not block distributed-RAM
  inference the way a 31×32 synchronous reset would.
- **Package-free and parameterised on purpose.** `tb/formal/run_formal.py`
  passes exactly one source file to `sby`; staying self-contained means the
  regfile can be proven without touching that script.

## Tool disagreements found the hard way

These cost a cycle each; they will recur as more core RTL lands.

- **Yosys rejects `'{default: '0}`** as an unpacked-array declaration
  initialiser — `syntax error, unexpected TOK_DEFAULT` — while Verilator
  accepts it. The formal flow could not read the file at all. Use an `initial`
  loop instead, which is also the idiom Vivado infers RAM initialisation from.
  This is the concrete reason A1's "Done when" names *both* Verilator and
  Vivado: they disagree, and Yosys is a third opinion again.
- **Verilator parses `// Verilator …` at the start of a comment as a pragma**
  and fails with `BADVLTPRAGMA` on ordinary prose. Keep that word out of the
  first position after `//`.
- **A package linted standalone reports every localparam as `UNUSEDPARAM`.**
  That is a property of linting a library in isolation, not a defect.
  `tb/lint_all.py` has a narrowly-scoped `STANDALONE_WAIVERS` entry for it;
  every other `-Wall` check stays on, and the constants get real coverage from
  the elaborated-design pass once a decoder consumes them.

## Formal depth

`formal_regfile` runs at **depth 8, not the `run_formal.py` default of 20**.
Every regfile property is combinational except storage-stability, which spans
two cycles. Depth matters a lot here: each BMC step adds another symbolic write
to a 32×32 memory and solve time blows up superlinearly — depth 8 proves in
~3 s, while depth 20 was still grinding on step 13 after eight minutes with
nothing further to find. Apply the same reasoning to future core proofs: pick
depth from the deepest property, not from the default.

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

`fpga/scripts/elab_core.sh <top>` runs Vivado elaboration (`synth_design -rtl`)
on everything in `rtl/core` + `rtl/common`, and fails on CRITICAL WARNING as
well as ERROR — Vivado reports many front-end problems as the former and a
batch run otherwise continues straight past them.

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
