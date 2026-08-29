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
| `rvntt_decode.sv` | A3 | `cocotb_decode`, `formal_decode`, Vivado elaboration |
| `rvntt_forward.sv` | A6 | `formal_forward`, `cosim_directed`, `cosim_commit_log`, Vivado elaboration |
| `rvntt_hazard.sv` | A7 | `formal_hazard`, `cosim_directed`, `cosim_commit_log`, Vivado elaboration |
| `rvntt_branch.sv` | A8 | `formal_branch`, `cosim_directed`, `cosim_commit_log`, Vivado elaboration |
| `rvntt_core.sv` | A4, A6–A8 | `core_a4_checksum`, `cosim_commit_log`, Vivado elaboration |
| `../soc/rvntt_ram.sv`, `../soc/rvntt_core_sim_top.sv` | A4 | `core_a4_checksum` |
| `tb/unit/rvntt_trace.sv` + `rvntt_trace_top.sv` | A5 | `cosim_commit_log` |
| `tb/cosim/commit_diff.py`, `gen_random_prog.py` | A5 | `cosim_commit_log` |
| `sw/tests/a6_forward.S` | A6 | `cosim_directed` |
| `sw/tests/a7_loaduse.S` | A7 | `cosim_directed` |
| `sw/tests/a8_control.S` | A8 | `cosim_directed` |
| `tb/cosim/cycle_model.py` | A7 | `cosim_directed`, `cosim_commit_log` |
| `tb/mutate/run_mutation.py` | A6 | run by hand; see below |

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

### `rvntt_decode.sv`

RV32I + Zicsr + Xkntt, combinational, producing `ctrl_t` plus the four register
addresses. Compared against `model/rv32i_ref.py::decode` over 10⁶ random words.

**Three different legality rules, and they are genuinely different:**

1. **Xkntt reserved fields are strict.** A register field an instruction does
   not use is reserved, and a nonzero value there is an *illegal instruction*
   (`docs/isa-spec.md` decode rule 3). `kntt.wait rd` with a nonzero rs1 is
   illegal. This is the rule the root `CLAUDE.md` warns about — a lax and a
   strict decoder disagree on exactly the random words this test generates.
2. **Base RV32I FENCE fields are *not* strict.** The base ISA says FENCE's
   fm/pred/succ/rs1/rd are reserved for future fences and base implementations
   *shall ignore* them. Ignoring is spec-mandated, so nonzero there is legal.
   Copying rule 1 onto FENCE would diverge from Spike, which is A5's reference.
3. **Anything outside `rv32i_zicsr_zicntr_xkntt0p1` is illegal.** No M, so OP
   with `funct7=0000001` is illegal. No Zifencei, so FENCE.I is illegal.

**The Python side delegates custom-0/custom-1 to `model/isa/xkntt.py`** rather
than reimplementing the rules. That is deliberate: the four-way agreement is
*defined* on the frozen contract, and a second hand-written copy could agree
with the RTL while both drifted from it.

**An illegal instruction produces exactly the reset bundle** — the whole
`ctrl_t`, not just the side-effect flags. Several decode arms set `result_sel`
or `imm_fmt` before legality is known, and leaving those at whatever the arm
assigned makes "illegal" mean something slightly different per opcode. This is
not a stylistic preference: the partial version *shipped*, and the 10⁶-word
comparison caught it — an illegal custom-0 word left `result_sel = RES_XKNTT`
in the RTL and `RES_ALU` in the model.

**`uses_rs1` is low for CSRRWI/CSRRSI/CSRRCI.** That field is a uimm, not a
register. A phantom dependency there never shows up as a wrong answer — only as
unexplained stalls and a worse IPC number, which is far harder to find later.

`tb/cocotb/rvntt_decode_flat.sv` is a testbench-only wrapper flattening `ctrl_t`
to scalar ports, because the simulator gives cocotb no member access into a
packed struct. It duplicates the field list by hand, so the fault-injection
table carries **one mutation per `ctrl_t` field** — if a field were wired to the
wrong port, its mutation would escape.

### `rvntt_core.sv` — what the pipeline does *not* have yet

Plan A4's bring-up strategy was to build the datapath with hazard handling
absent, verify against NOP-padded code, then add each layer. All three layers
are in: forwarding (A6), the load-use interlock (A7) and control flow (A8).
What is left is **A9** — CSRs, traps, `ECALL`/`EBREAK` — and the coprocessor
interface. `dbg_unsupported` now flags only illegal instructions, Xkntt, and a
CSR access that writes a register.

That last one is dangerous, so it is not left to a comment. **`dbg_unsupported`
pulses whenever an instruction retires that this core cannot execute
faithfully** — illegal, Xkntt, branch, jump, or a CSR access that writes a
register — and the testbench treats it as failure. ECALL is excluded (it is the
stop marker), and so is a CSR access with `rd == x0`: `csrw mtvec, t0` has no
register-file effect, so the core and Spike agree on architectural state even
with no CSR file, which is what lets the test program arm Spike's trap handler.

**Memory timing is the load-bearing structural decision.** `rvntt_ram` registers
each port's address, so its output register *is* a pipeline register. Port A's
address comes from the PC register in IF, so the instruction arrives in ID.
Port B's address comes from the **combinational** ALU result in EX, not from
the EX/MEM register — driving it from the registered result pushes load data
into WB and adds a second load-use bubble the plan's timing does not have.

### A6 — forwarding, and where the three distances are handled

`rvntt_forward.sv` resolves EX/MEM→EX and MEM/WB→EX for both operands. What is
easy to miss is that **the forwarding network alone does not cover every
distance** — it covers 1 and 2, and distance 3 is the register file's
*write-through*. Those two mechanisms are in different files, and the boundary
between them is exactly where the random generator's `RAW_DISTANCE = 3` sits.
A mutation that deletes write-through leaves the forwarding unit provably
correct and the pipeline broken.

**Loads are excluded from `FWD_MEM` on purpose.** A load in MEM does have its
data by then — `rvntt_ram` registers the address back in EX — so forwarding it
would work functionally. It would also put the BRAM output register on the path
`BRAM → sign-extend → forward mux → ALU → BRAM address` inside one cycle, which
is the worst path in the design and the one A-FPGA will have to close at
100 MHz. A7's one-cycle interlock exists to avoid that path, not because the
data is unavailable. The exclusion lives in `rvntt_forward`'s `mem_mem_read`
input; the interlock is what makes it *safe*.

**Forwarding is gated on `uses_rs1` / `uses_rs2`, and that gate is shared with
A7's interlock.** Forwarding into an operand the instruction does not read is
harmless on its own — the source mux would not select it — but the same
predicate decides whether the pipeline *stalls*, and a stall on a register field
that is really part of an immediate (LUI's `rs1` bits) is a phantom stall:
invisible in a functional test, visible only as an IPC discrepancy much later.
One predicate, so the two cannot disagree.

**`rs3` is deliberately absent** from both the forwarding unit and the
interlock. It is read only by the Xkntt R4-type instructions, which no stage
executes yet, so a path for it could not be tested — and untestable logic that
looks verified is worse than no logic. It goes in with the coprocessor
interface, with its own tests.

**The MEM-stage forward source is not `mem_result`.** It is a separate mux over
`alu_result` and `pc_plus4` only. `mem_result` includes the load path, so using
it would reintroduce the BRAM-to-ALU path through the back door while looking
like a simplification.

### A7 — the interlock, and the register nobody expects to need

The interlock itself is four lines and matches the plan exactly. Two things
around it are not obvious.

**A stall needs an instruction holding register**, and the reason is specific to
this pipeline. The IF/ID instruction word is *not* flopped in `rvntt_core` — it
arrives from `rvntt_ram`'s own output register, which *is* the IF/ID insn
register. So holding `pc_q` and `if_id_q` does not hold the instruction:
the RAM's output register was already loaded, at the edge into the stalled
cycle, with the word at whatever address was on `imem_addr`. On the next cycle
ID would carry the *previous* pc alongside the *next* instruction word. The fix
is a 32-bit hold register plus a mux, captured from `if_id.insn` (the already
muxed value, so a multi-cycle stall keeps replaying it — A9's traps and the
coprocessor's `kntt.wait` will need that). Rewinding `pc_q` instead would work
and cost **two** cycles per stall, because the re-fetch takes a cycle of its
own.

**One stall cycle is enough here because of the memory timing, not because of
the textbook.** Without the stall the consumer reaches EX while the load is in
MEM, and `FWD_MEM` deliberately does not carry load data. With one stall the
load is in WB and `FWD_WB` does. **The interlock and the `FWD_MEM` exclusion are
two halves of one decision** — the exclusion without the interlock silently
reads a stale register, and the interlock without the exclusion is a stall that
buys nothing.

### A8 — control hazards

Branches resolve in EX, so a redirect always has **two** younger instructions in
flight: one in ID and one whose fetch is in flight. Both must be squashed. The
directed test puts a taken branch immediately behind a taken branch precisely
because a one-slot flush lets the second one redirect too, and the program ends
up somewhere it was never meant to go — a failure that looks nothing like
"the flush is one slot short".

**`funct3` for the branch condition comes from the instruction word, not from a
decoder output.** That is a deliberate call, not an oversight. `ctrl_t` has no
`funct3` field — `mem_op` carries it, but only for loads and stores — and
widening the decoder's contract would mean changing `model/isa`'s frozen ctrl
bundle to suit the RTL. The instruction word is already carried down the
pipeline for the commit trace, so three bits from it cost nothing. What makes it
safe is that the decoder has already **rejected** the reserved encodings (`010`
and `011` are illegal for BRANCH) and `ctrl.branch` gates the comparator, so
only the six blessed values can matter. `rvntt_branch` still drives the reserved
values to *not taken* rather than leaving them a don't-care, and the proof checks
it — defence in depth that nothing checks is decoration.

**JALR's bit-0 rule is applied where the spec states it**, not folded into a
blanket `& ~1` on every target. B and J immediates already encode bit 0 as zero.
Note what a missing bit-0 clear looks like here: the *fetch* is unaffected,
because `rvntt_ram` ignores the low address bits by design, so the core executes
exactly the right instruction at a pc that is off by one. Only the commit log's
pc column shows it.

**A redirect and a stall cannot coincide** — both are properties of the single
instruction in EX, and no instruction is both a load and a taken branch. The
priority is written down anyway, because "these are mutually exclusive" is
exactly the reasoning that stops holding when a later step adds a third case;
A9's traps will redirect from MEM.

**The link register is never forwarded, and cannot be.** A jump always flushes
two slots, so the instruction at the target is three pipeline slots behind it —
the register file's write-through, not the forwarding network. `ex_mem_fwd_data`
still has its `RES_PC4` arm, and that arm is currently unreachable. It is kept
because A9's `RES_CSR` needs the same mux and because the unreachability depends
on the flush depth, which is not a property anything in the RTL asserts.

### Mutation anchors go stale, and the harness says so

Two A7 mutations turned into `NO-OP` the moment A8 edited the lines they
anchored to. That is the harness working: an anchor that no longer matches is
reported as a problem rather than silently skipped, so a mutation cannot quietly
stop testing anything. When a step edits `rvntt_core.sv`, expect to re-anchor
the previous step's mutations — and treat a `NO-OP` as a failure, never as
noise.

### A commit-log diff cannot see timing

This is the A7 lesson worth carrying forward. A **phantom stall** — a bubble
inserted where none was needed — changes no architectural state whatsoever. The
commit log is byte-identical. The program is simply slower, and the first
symptom appears much later as an IPC number that disagrees with the LLVM
`SchedMachineModel`, at a point where nothing points back at `rvntt_hazard.sv`.

`tb/cosim/cycle_model.py` closes that. It predicts the **span** — the cycle
distance from the first retirement to the last — as

    span = (retired - 1) + stalls + 2 x redirects

and the testbench reports the measured span. Using the span rather than a total
cycle count means the model needs to know neither the reset length nor the
pipeline fill depth; both cancel. Hazards are found by decoding the *dynamic*
retired instruction stream with `model/rv32i_ref.py` — the frozen spec-derived
model, not the RTL and not `rvntt_hazard`'s own predicate. Redirects are not
decoded at all: an instruction redirected iff the next retired pc is not its
own plus four, which is a property of the trace.

Two of the A7 mutations are invisible to everything except this check:
`lw x0, ...` becoming a stall source (nothing can read its result, and every
`nop` is `addi x0, x0, 0`), and the interlock keying on the `rs1` *field*
instead of on `uses_rs1`.

### When a mutation escapes, look at the stimulus first

Said at A5 about value entropy; A7 produced the same shape again. The mutation
that ties `uses_rs1` high was caught by the directed test and escaped the random
suite. The reason was reachability, not weakness: the shape needs a LUI or AUIPC
whose immediate bits 7:3 — which land in `insn[19:15]` — happen to name the
register a load just wrote, about 0.5% per load. The fix went into the
*generator*: the immediate's non-source field is now filled with the most
recently written register half the time. That is deliberately generic — nothing
in it knows the interesting predecessor is a load — so it makes the whole class
of non-source-field hazards reachable rather than this one mutation.

### Formal properties must not be written in terms of the code they check

`rvntt_forward`'s properties originally reused the module's own
`mem_supplies` / `wb_supplies` wires. Every priority, completeness and soundness
property was therefore checking those wires against themselves, and a mutation
*inside* them sailed straight through. The mutation harness found it. The
properties now rebuild the predicates from the raw input ports, in De Morgan
form so they are not the same text twice.

This generalises: **a property that reuses an intermediate signal from the
design under test cannot detect a bug in that signal.** It is the assertion
equivalent of testing a function by calling it.

### A5 — the cosimulation harness

`tb/cosim/test_cosim_a5.py` runs the hand-written checksum plus generated random
programs on both Spike and the RTL and diffs the commit logs line by line.
**500 programs × 400 instructions have been run byte-identical**; the regression
runs 100.

**Equality is defined by one renderer.** Both sides are parsed to
`(pc, insn, [(rd, value)])` and rendered back through `commit_diff.render()`, so
"byte-identical" is literally true but cannot be defeated by a formatting
difference, and there is one place to teach about a new annotation. The RTL's
own log is *re-parsed* rather than trusted, which is what caught `rvntt_trace.sv`
emitting a malformed line.

**Spike's format, taken from real output, not from prose.** The register field
is left-justified in three columns (`x5 `, `x11`). Verilator's `-` flag leaks
from `%-3s` into the following `%08x`, rendering the value space-filled instead
of zero-padded — so the field is padded explicitly in the monitor instead.

**The generator's value entropy is load-bearing.** `--raw-density`,
`--load-use-density` and `--branch-density` are all 0 at A5 and rise at A6/A7/A8.
But the subtler property is that register values must not collapse: `slt`/`sltu`
produce 0 or 1, and drawing x0 as a source too often compounds it. At 10% x0 the
programs ended up shifting 0, 1 or all-ones almost everywhere, and a shift-amount
bug (`b[5:0]` instead of `b[4:0]`) was invisible. x0 is now 5% and LUI/AUIPC are
more frequent. Scratch memory is likewise pre-filled with a generated pattern
rather than `.space` zeros, because an all-zero scratch makes a byte load that
forgets to sign-extend indistinguishable from a correct one.

### The A4/A5 offsets between Spike and the RTL

Two, and they compound. For the same program Spike reported 481 commits where
the RTL retired 477:

- **Spike's bootrom is 5 instructions at `0x1000`** before the jump to
  `0x80000000`. Filter on `pc >= 0x80000000` rather than hardcoding 5.
- **Spike's `--log-commits` prints no line at all for a trapping instruction.**
  ECALL traps, so it never appears — while the RTL retires it as its stop
  marker. Looking for the ECALL in Spike's trace finds nothing in a log where
  everything else is present, which is a confusing way to learn this.
  `spike_asm.skipped_traps()` is built around the same behaviour. The
  consequence for the differ: the ECALL must be **excluded** from the RTL side,
  not included — otherwise every otherwise-identical program fails with a
  one-line length difference at the very end.
- **Spike also annotates `mem 0x<addr>` on loads, `mem 0x<addr> 0x<data>` on
  stores, and `c<n>_<name> 0x<val>` on CSR writes.** None are part of the A5
  format; `render()` drops them on both sides.

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
- **A partial illegal-instruction reset in the decoder** (A3), where the RTL and
  the model disagreed on a don't-care field. Found by the 10⁶-word comparison
  rather than by mutation, which is the point of running it at that scale: a
  10⁴-word run would very likely have missed it.
- **A missing reserved-field test for the SYSTEM opcode** (A3). Dropping the
  `rs1 == 0` half of the ECALL/EBREAK/MRET/WFI check escaped *every* test in the
  file, 10⁶ random words included — such a word has probability ~7.1 × 10⁻⁸, so
  0.07 expected hits per million draws. The Xkntt reserved fields had an
  exhaustive sweep from the start; these did not. **Generalise this:** a rule
  that constrains a handful of specific encodings out of 2³² needs a directed
  sweep. Random testing covers the common case and never the rare constraint.

- **Formal properties written in terms of the design's own intermediate
  signals** (A6). `rvntt_forward`'s priority, completeness and soundness
  properties all went through `mem_supplies` / `wb_supplies`, so a mutation
  inside those wires was invisible to every one of them. See the section above.
- **A directed test that could not reach the path it padded around** (A6).
  `a4_checksum.S` pads every RAW with three NOPs, putting its dependencies at
  distance 4 — so it never exercises the register file's write-through at
  distance 3, and was wrongly credited with covering it. The manifest's
  "which test must catch this" field is what turned that into a `PARTIAL`
  verdict instead of a silent over-estimate.

The general lesson: fault-inject *each* checking mechanism separately, against
the same mutation table. Of the 15 A2 mutations, 14 were caught by both cocotb
and formal — and the one that was not is precisely the one that found a real
hole. Running only the mechanism that happened to be stronger would have left
the proof quietly incomplete.

### `tb/mutate/run_mutation.py`

Through A5 this was a throwaway script per step, which made every result
unreproducible the moment the session ended — the wrong property for the habit
that has caught the most here. It is now a committed harness with a manifest.

Two things about it are worth keeping:

- **Every mutation declares *which* tests must catch it**, and a declared
  catcher that does not catch is a failure (`PARTIAL`), not a footnote.
  Requiring only that *something* catches each mutation lets a manifest decay
  until one broad test is credited with everything.
- **A mutation that fails to build is an error, not a catch.** Deleting an
  expression's only use makes Verilator's `UNUSEDSIGNAL` the detector rather
  than the test. Two A4 mutations and two A6 ones had to be reformulated so
  every signal stayed referenced.

A baseline run against unmutated RTL comes first, so a stuck-at-fail test
cannot appear to catch everything.

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
