# rtl/core — Track A, the RV32I pipeline

Track A is the critical path from here (plan A1–A13, with A12 being the
"A-FPGA" milestone). Spike exists as a golden reference for the *extended*
ISA, which is what makes A5's lockstep cosimulation possible; that ordering
was the whole reason C1 came before this.

## What is here

| File | Step | Verified by |
|---|---|---|
| `rv32i_pkg.sv` | A1 | lint, Vivado elaboration |
| `rvntt_regfile.sv` | A1 | `verilator_sim_regfile`, `formal_regfile`, Vivado elaboration |
| `rvntt_alu.sv` | A2 | `cocotb_alu`, `formal_alu`, Vivado elaboration |
| `rvntt_immgen.sv` | A2 | `cocotb_immgen`, `formal_immgen`, Vivado elaboration |

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
- **Package-free and parameterised.** Nothing in it needs `rv32i_pkg`. (At A1
  this was also a workaround: `run_formal.py` passed exactly one file to `sby`.
  A2 taught it to resolve package dependencies via `tb/rtl_deps.py`, so that
  constraint is gone — but the module is still simpler for not having one.)

### `rvntt_alu.sv` / `rvntt_immgen.sv`

Both purely combinational, both checked two ways: cocotb against
`model/rv32i_ref.py` (written from the ISA spec, not transcribed from the RTL)
and a formal proof that restates each result in a second, different idiom.

- **Shifts use `b[4:0]`, never all of `b`.** A shift by 0x21 must equal a shift
  by 1. An ALU that feeds the whole operand into the shifter passes every test
  where `b < 32`; `test_alu_shift_amounts` is what fails it.
- **`a >>> b` is not arithmetic on its own.** `>>>` on an unsigned `logic`
  vector is a plain logical shift — the left operand has to be `$signed`.
- **B and J immediates hardwire bit 0 to zero.** Taking it from the instruction
  makes every branch go exactly twice as far, which reads as wild control-flow
  corruption rather than an immediate bug.
- **`model/rv32i_ref.py` duplicates `alu_op_e` and `imm_fmt_e` encodings**, so
  it carries `check_pkg_agreement()`, which parses the package and compares.
  Every cocotb test calls it first. Without it, renumbering an enum would leave
  every test passing while testing the wrong operation.

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
  That is a property of linting a library in isolation, not a defect. The
  suppression is a scoped `lint_off` around the constant block *inside*
  `rv32i_pkg.sv`, not a command-line waiver: a waiver would have had to be
  applied to every file that imports the package too, switching the check off
  for those modules as well.
- **Yosys rejects `import` entirely** — both `module foo import pkg::*; (...)`
  and a module-body `import`, with `syntax error, unexpected TOK_IMPORT`. The
  only form Verilator, Yosys and Vivado all accept is a **fully-qualified
  package reference with no import at all**: `rv32i_pkg::alu_op_e` in the port
  list and `rv32i_pkg::ALU_ADD` in the body. Write core RTL that way from the
  start. `tb/rtl_deps.py` finds these dependencies (it matches qualified
  references, not just `import` lines) and orders the package first for both
  Verilator and Yosys, which need it declared before use.

## What fault injection has actually caught

Not hypothetical — these are defects the practice found in this directory:

- **A hole in the immgen proof.** The `IMM_S` properties only checked sign
  extension above bit 11, so replacing `insn[11:7]` with `insn[19:15]` — taking
  a store offset's low bits from rs1's field instead of rd's — passed formal
  cleanly. The cocotb comparison caught it, so the RTL was never at risk, but
  the proof was weaker than it looked. Every source bit is now pinned, for the
  unscrambled formats as well as B and J.
- **A vacuous spec-drift regex.** `_parse_enum` used a non-greedy `.*?` with
  `re.S`, which let the match start at the *first* enum in the package and run
  to the requested one — reporting `opcode_e`'s width as `alu_op_e`'s.
- **An elaboration wrapper that could only report failure.** `-log` after
  `-tclargs` is swallowed as a script argument, so Vivado wrote to the default
  log and the grep found nothing. Correct polarity, wrong argument order.

The general lesson: fault-inject *each* checking mechanism separately, against
the same mutation table. Of the 15 A2 mutations, 14 were caught by both cocotb
and formal — and the one that was not is precisely the one that found a real
hole. Running only the mechanism that happened to be stronger would have left
the proof quietly incomplete.

## Formal depth

`formal_regfile` runs at **depth 8, not the `run_formal.py` default of 20**.
Every regfile property is combinational except storage-stability, which spans
two cycles. Depth matters a lot here: each BMC step adds another symbolic write
to a 32×32 memory and solve time blows up superlinearly — depth 8 proves in
~3 s, while depth 20 was still grinding on step 13 after eight minutes with
nothing further to find. Apply the same reasoning to future core proofs: pick
depth from the deepest property, not from the default. `formal_alu` and
`formal_immgen` run at **depth 2** for the same reason — both are purely
combinational, so there is no state to unroll at all.

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
