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
| `rvntt_bitmanip.sv` | **A21** | `formal_bitmanip`, `riscof_arch_test` (B, Zbkb), `riscv_formal`, `cosim_commit_log`, `cocotb_decode` |
| `rvntt_csr.sv` | A9, **A20** | `formal_csr`, `riscv_tests`, `csr_traps_minstret`, **`hpm_counters`**, Vivado elaboration |
| `rvntt_muldiv.sv` | A14, A15 | `formal_muldiv`, `riscv_tests` (`rv32um`), `cosim_directed`, `cosim_commit_log`, `synth_ooc.sh` |
| `rvntt_rvfi.sv` | A11, A15 | `riscv_formal` (43 checks), Vivado elaboration |
| `rvntt_core.sv` | A4, A6–A9, A11, A14 | `core_a4_checksum`, `cosim_commit_log`, `riscv_tests`, `riscv_formal`, Vivado elaboration |
| `../soc/rvntt_ram.sv`, `../soc/rvntt_core_sim_top.sv` | A4 | `core_a4_checksum` |
| `tb/unit/rvntt_trace.sv` + `rvntt_trace_top.sv` | A5 | `cosim_commit_log` |
| `tb/cosim/commit_diff.py`, `gen_random_prog.py` | A5 | `cosim_commit_log` |
| `sw/tests/a6_forward.S` | A6 | `cosim_directed` |
| `sw/tests/a7_loaduse.S` | A7 | `cosim_directed` |
| `sw/tests/a8_control.S` | A8 | `cosim_directed` |
| `sw/tests/a9_csr.S`, `a9_minstret.S` | A9, A14 | `csr_traps_minstret` |
| `sw/tests/a20_hpm.S` | **A20** | `hpm_counters` |
| `sw/tests/a14_muldiv.S` | A14 | `cosim_directed` |
| `tb/cosim/test_riscv_tests.py` | A9 | `riscv_tests` |
| `tb/riscof/` (plugins, env, runner) | A10 | `riscof_arch_test` |
| `tb/formal/rvntt_rvfi_wrapper.sv`, `run_riscv_formal.py` | A11 | `riscv_formal` |
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
  aliasing onto a legal width. `ctrl_t.muldiv_op` (A14) is a plain vector for a
  different reason: all eight of *its* values are legal, so an enum would be a
  second name for a field that is already total.
- **`MULDIV_MUL_CYCLES` and `MULDIV_DIV_CYCLES` are the latency contract**, in
  cycles of EX *occupancy*. `model/rv32i_ref.py` duplicates both and
  `check_pkg_agreement()` compares them, so retuning the divider without
  retuning `tb/cosim/cycle_model.py` fails a test instead of silently making the
  independent cycle model agree by construction.
- **A25 made the multiplier's pipeline depth DERIVED from that contract**:
  `MUL_PIPE = MUL_CYCLES - 2`, product-side register levels. Before it, three
  named registers sat beside a hardcoded 4 and were related only by a comment.
  `MUL_CYCLES` is now a module **parameter** (defaulted from the package) purely
  so `fpga/scripts/synth_ooc.sh` can sweep it against real post-route timing —
  **nothing instantiates the module with a different value**, and
  `tb/unit/test_isa_consistency.py` checks both that the default is the package
  constant and that nothing in `rtl/` passes the parameter by name. An override
  would leave the RTL retiring `MUL` at a cycle the independent cycle model does
  not predict, and the failure would surface nowhere near the parameter.

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

RV32IM + B + Zbkb + Zicond + Zicsr + Xkntt, combinational, producing `ctrl_t`
plus the four register addresses. Compared against `model/rv32i_ref.py::decode` over 10⁶ random words.

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
3. **Anything outside
   `rv32im_zba_zbb_zbs_zbkb_zicond_zkr_zkt_zicsr_zicntr_xkntt0p1` is
   illegal** (`ISA_XKNTT` in `tb/cosim/spike_asm.py`). A14 made OP with
   `funct7=0000001` (M) legal for all eight `funct3` values; A21 and A22 added
   the B, Zbkb and Zicond forms, each legal **only** at its exact
   `(funct7, funct3)` pairs — the table in `rvntt_decode.sv`, mirrored by
   `_BM_OP_R` in `model/rv32i_ref.py` — and, for the OP-IMM unary forms, its
   exact `rs2`. Every other pair is still illegal (`czero` under
   `funct7=0000111` is legal at `funct3` 101 and 111 only), which is what keeps
   the strict-reserved-field claim intact. No Zifencei, so FENCE.I is still
   illegal.

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
absent, verify against NOP-padded code, then add each layer. All of them are in:
forwarding (A6), the load-use interlock (A7), control flow (A8), and CSRs, traps
and `MRET` (A9). What is left is the **Xkntt coprocessor**. `dbg_unsupported`
flags an Xkntt instruction retiring — and still flags an *illegal* one, which A9
should make impossible, so that guard is now a live check on the trap path
rather than a leftover.

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

### A9 — the trap invariant, and where `minstret` is counted

**Every trap resolves in EX.** That is not an accident of the current exception
set: the misaligned-address check had to be placed in EX, where the address is
computed, rather than in MEM where the access lands, to keep it true. Two things
depend on it and would break quietly without it — nothing older than EX ever has
to be squashed, and a faulting store is suppressed before `rvntt_ram`'s address
register ever latches it. A faulting instruction is squashed into a bubble on
its way to MEM, so it never retires, which is also what keeps the commit log
comparable with Spike (which prints no line for a trapping instruction).

**`minstret` is incremented in EX, not in WB, and this is the least obvious
decision in the step.** A CSR access executes in EX and must report the number
of instructions retired *before* it; counting at WB leaves its two immediate
predecessors uncounted at that moment. Adding them back from the pipeline
registers works — the trap invariant makes it exact — and is *wrong the first
time software writes the counter*, because the write already accounts for
everything ahead of it and the correction then double-counts.
**riscv-tests' `instret_overflow` said so in one line**: `csrwi minstret, 0;
csrr a0, minstret` must read 0, and the in-flight version reads 2. Counting in
EX has neither problem, and a write to either half of `minstret` suppresses the
writing instruction's own increment.

**What is deliberately not implemented**: `satp`, `pmpaddr*`, `pmpcfg*`,
`medeleg`, `mideleg`, `mnstatus`. Accessing one is an illegal-instruction trap,
and that is *the case riscv-tests' p-environment is written for* — each of its
`INIT_` macros points `mtvec` at the label immediately after itself before
touching an optional CSR, so the trap lands on the next line and the test carries
on. Two are load-bearing exceptions and **must** exist: `mie` is written before
`DELEGATE_NO_TRAPS` re-points `mtvec`, so a trap there jumps backwards into an
infinite loop; and `mhartid` is read before `mtvec` is set at all, so a trap
there vectors to address 0.

**`ECALL` now traps, which changed every test harness in the tree.** It is
squashed in EX and never retires, so the old "stop when an ECALL retires"
condition can never fire again. There are two replacements and the first one is
better than what it replaced: `--stop-pc <trap handler>` is *exclusive*, and it
is exactly where Spike's own trace is truncated — so both sides now count the
same instructions with no offset to remember. `--tohost <addr>` watches the
store bus for riscv-tests' result protocol, which works because a store is
issued from EX and nothing past EX is squashed.

### riscv-tests: the first suite this project did not write

54 of 54 pass — the full `rv32ui-p-*` set and `rv32mi-p-*` — with four skipped
for reasons that are features, not gaps: `fence_i` (Zifencei is not in the
target ISA), `ma_data` (requires misaligned access to *succeed*; this core traps,
which the `rv32mi *-misaligned` tests check instead), `breakpoint` (debug
triggers) and `pmpaddr` (PMP). Every test is run on **Spike first**: a test that
does not pass on the reference model is a broken build or a wrong ISA string,
and reporting it as an RTL failure sends the reader to the wrong place.

The value of an externally authored suite is exactly that it does not share the
design's blind spots. It found the `minstret` placement bug on the first run,
and nothing written alongside the core had questioned it.

### A10 — RISCOF, and four traps between here and a green report

76 of 76 selected riscv-arch-test tests pass: **38/38 of the RV32I `I` suite**,
22 `hints` and 16 `privilege` (which includes the misaligned load, store and
JALR cases). The report is committed at `docs/riscof-report.html`.

Getting there took four corrections, and each of them would have produced a
plausible-looking wrong answer.

**1. RISCOF's exit code is not the verdict.** `riscof run` returns 0 for a run
in which tests failed. The first version of `run_riscof.py` printed `RISCOF_OK`
over **50 real failures**. The verdict now comes from parsing the HTML report,
and a run with zero passes is also a failure — an empty report is not a green
one.

**2. `-mno-relax` is required, and its absence looks like a branch bug.**
`arch_test.h`'s `LA` macro wraps its `.align` in `.option rvc` so the padding can
be two bytes, then switches back with `.option norvc`. With linker relaxation
on, that alignment becomes an `R_RISCV_ALIGN` relocation the *linker* fills —
with **compressed** NOPs, because the relocation was recorded while rvc was
still enabled. The result is `c.nop` in the instruction stream of a test for a
core with no C extension. Spike itself faults on the first one, vectors to the
still-unset `mtvec` at address 0, and spins there forever. The symptom is "the
reference model hangs", which points nowhere near the compile line.

**3. The branch and jump tests need 2 MB of memory.** They walk the whole
immediate range: `beq-01` links to `0x8003aa28` and `jal-01` — exercising JAL's
±1 MB — to `0x801af18c`. Against this repo's usual 64 KB array the image is
silently truncated and the core runs off into unwritten memory. Seven tests
failed, all of them branches and `jal`, which reads exactly like a control-flow
bug and is a memory-size one.

**4. PMP has to be excluded by name.** Those tests carry
`verify (PMP['implemented'])` in their selection clause — but **riscof 1.25.3
does not implement `verify` at all**, filtering on the ISA regex alone, so they
are selected for any RV32I core and fail 43 times. They are dropped from the
test list explicitly, with the reason recorded next to the exclusion, in the
same style as the `riscv_tests` SKIPPED table. An exclusion with a reason is a
statement; a failure left in the report is noise.

**The signature is reconstructed from the store bus**, not read out of the RAM.
Peeking inside memory would mean marking `mem` public for Verilator — a
simulator-specific annotation on synthesisable RTL, for the benefit of a test.
Replaying the program's committed stores onto its own load image gives the same
answer from what the core already exposes, and is sound for the same reason the
tohost watch is: nothing past EX is squashed.

**The compliance suite is not a superset of the local tests, and fault
injection says so precisely.** Running RISCOF against deliberately broken RTL:

| mutation | RISCOF | caught locally by |
|---|---|---|
| `break_sra` | **2 tests fail** | `formal_alu`, `cosim_commit_log` |
| `break_bltu` (signed/unsigned) | **1 test fails** | `formal_branch`, `cosim_directed` |
| JALR does not clear bit 0 | **escapes — 76/76 still pass** | `cosim_directed` (`a8_control.S`) |

The RV32I `jalr-01` test never computes an odd target, and the two
`privilege/misalign*-jalr` tests aim at 2-mod-4 addresses, which bit 0 does not
affect. So an official compliance pass would have been perfectly green over a
JALR that ignores its own bit-0 rule. **This is the argument for keeping the
directed tests after the external suite arrives**, not before it — they cover
different things, and neither one subsumes the other.

**5. The reference model's ISA string was hardcoded** (found at A15). The
`spike_ref` plugin built `self.isa` as the literal `'rv32i'` plus an optional
`_zicsr` — correct, and correct right up until A14 added M. The arch-test suite
compiles each test from *its own* `isa` field, so the M tests were built
`-march=rv32im` and then handed to a Spike told `--isa=rv32i_zicsr`: illegal
instruction on the first `mul`, vector to an unset handler, **spin forever**.
Nothing failed. The run simply stopped making progress, silently, for 25
minutes — the same shape `-mno-relax` produces above, now for the second time.

Three fixes, in increasing order of value:

- The reference invocation carries a **`timeout 600`**, because *a reference
  model that hangs must fail rather than stop the run*. 600 s is about fifty
  times the slowest test here, so it can only fire on a real hang. This catches
  the **symptom**, and takes ten minutes per test to do it.
- The ISA string is **derived from `rvntt_isa.yaml`** rather than written down,
  so the ISA lives in two places instead of three.
- `run_riscof.py` now runs **`check_isa_consistency()` before anything else**,
  which reconstructs `misa` from the yaml's ISA letters and compares it against
  both the yaml's own `reset-val` and `rvntt_csr.sv`'s `MISA_VALUE`. That
  catches the **cause**, in milliseconds, and it is the same spec-drift shape as
  `model/rv32i_ref.py`'s `check_pkg_agreement()`. Fault-injected three ways —
  drop M from the yaml's ISA string, from its `reset-val`, or from the RTL's
  `MISA_VALUE` — and all three are caught.

**RISCOF itself is deprecated upstream.** riscv-arch-test's default branch has
moved to the ACT4 framework, which replaces RISCOF and needs the Sail model plus
a UDB configuration. `toolchain/riscv-arch-test` is pinned to the maintained
`old-framework-3.x` branch. That is deliberate: plan A10 asks for RISCOF and its
HTML report, and ACT4 produces neither. Moving to ACT4 is a real piece of work
and belongs to whoever wants the current certification flow.

The Python pins are their own small maze; `toolchain/test-suite-pins.txt` has
the reasoning, and the short version is that riscof 1.25.3 must be installed
with `--no-deps` because its `gitpython==3.1.17` pin predates Python 3.12.

### A11 — riscv-formal, and the bug five suites could not reach

43 checks pass at **BMC depth 14** — `liveness` at 47, and `rvntt_muldiv`'s
arithmetic abstracted, both since A15; see the A15 section below. The set is the
36 RV32I instruction models plus `reg`, `pc_fwd`, `pc_bwd`, `causal`, `liveness`
and `unique`, in about 35 s wall on eight jobs and five minutes of solver time. `tb/formal/run_riscv_formal.py` generates the
configuration, drives riscv-formal's own `genchecks.py`, and runs `sby`.

**Depth is counted from the first retirement, not from zero.** riscv-formal's
testbench constrains `reset` to step 0 only, and this pipeline is five stages
deep, so the first instruction retires at cycle 5 and a check at cycle N sees at
most N−4 instructions. The deepest thing a check must reach is a dependency at
distance 3 — the boundary between the forwarding network (1 and 2) and the
register file's write-through (3) — with a load-use stall and a two-cycle
control-flow bubble also in the window. That is four instructions plus up to
three bubbles, so 14 leaves margin and still solves in seconds. It is
deliberately not larger; the *Formal depth* section below is why.

#### The plan is wrong that the commit tracer is enough

Plan A11 says to "wire it out of your existing commit tracer — the information
is the same, in a standardized form". It is not. **riscv-formal requires a
trapping instruction to be reported, with `rvfi_trap` set**, and A9's trap
invariant squashes the faulting instruction in EX so it never reaches WB and
never appears in the commit stream at all. That squash is load-bearing — it is
what keeps the commit log line-for-line comparable with Spike, which prints
nothing for a trapping instruction, and what makes `minstret` right without an
in-flight correction — so `rvntt_rvfi.sv` adds a **second, parallel report
path** instead of unpicking it.

The trapped instruction fits in the hole it leaves behind: a trap at cycle T
clears `ex_mem_q`, so `mem_wb_q` is a bubble at T+2, which is exactly the cycle
that instruction would have retired. The shadow registers load on the same edges
as `ex_mem_q` and `mem_wb_q`, so the trap report emerges in that empty slot and
`NRET = 1` stays sound. The module asserts that rather than arguing it:
`a_trap_not_retired`, `a_nontrap_retired` and `a_shadow_pc`/`a_shadow_insn` run
inside every one of the 43 checks. They are not decoration — dropping the
shadow's MEM stage makes all four fire by name at step 14, which is a far better
diagnostic than the `pc_fwd` counterexample the same bug also produces.

**`rvfi_order` is not `minstret`, and cannot be derived from it.** It must count
trapped instructions; `minstret` deliberately does not. It is its own counter.

**What comes from the real pipeline and what comes from the shadow.** Everything
that still exists at WB is read from the registers the core actually uses — `pc`,
`insn`, and the register file's own write port — so RVFI reports what the machine
did rather than what a parallel copy predicted. Only what has no later copy is
shadowed: the forwarded EX operands, the data-bus request, and the trap's
`pc_wdata`. Taking `rvfi_rd_addr` from `wb_we` rather than from the decoded field
also gets the RVFI rule ("zero for an instruction that writes no register") for
free, and a trapped instruction needs no mux of its own because it was squashed.

#### The bug it found

`reg_ch0` failed on the first honest run, in seven seconds, from an
unconstrained instruction stream:

```
ord=6  insn=0x01ba048b  (custom-0, kmm)  rd=x9 <- 0x00000000
ord=7  insn=0x01948803  (lb)             rs1=x9 reads 0xe0008001
```

`ex_mem_fwd_data` was written as "`pc_plus4` for `RES_PC4`, `ex_result` for
everything else", while `mem_result`'s case sends `RES_XKNTT` to its
`default: 32'h0` arm. **A legal Xkntt instruction forwarded its ALU output to
the next instruction and wrote zero to the register file.** Two case statements
over the same enum, disagreeing on one arm.

No RV32I program can reach it — the only instruction class that disagrees is the
one no stage executes — which is why A5's 500 random programs, the directed
tests, riscv-tests, RISCOF and 35 mutations had all missed it. The fix writes
both case statements the same way round. `fwd_xkntt_disagrees_with_writeback`
restores it permanently, so it cannot come back unnoticed.

The general shape is worth keeping: **an unimplemented feature is not the same as
an absent one.** The decoder accepts Xkntt and `dbg_unsupported` catches it
*retiring in simulation*, but formal has no such stop condition, so it explored
the datapath the decoder actually permits.

#### sby's exit code is not the verdict either

`genchecks` writes `expect pass,fail` into every generated `.sby`, which tells
sby that a failing proof is an *acceptable outcome*: it prints
`DONE (FAIL, rc=0)` and exits 0. The first version of the runner reported a green
**43/43 over a core with a deliberately broken adder**. The verdict now comes
from each check's `status` file, and a missing status is a failure.

This is the second time this exact shape has appeared here — A10's RISCOF runner
printed `RISCOF_OK` over 50 real failures — so it is now a rule rather than an
anecdote: **for any third-party test driver, find out what it does on failure
before believing a green run, and take the verdict from the artefact rather than
from the process.** Only fault injection caught either of them.

A related distinction the runner now makes: a check whose status is `ERROR` (the
design would not elaborate, or the solver gave up) is **not** a caught bug, and
`run_riscv_formal.py` exits 2 rather than 1 so the mutation harness refuses to
credit it. That matters specifically because `rvntt_rvfi.sv` is not in the
simulator's source list, so `build()` cannot vet a mutation to it.

#### What riscv-formal adds, and what it does not

Measured by running mutations against it, in the same style as A10's table:

| mutation | riscv-formal | also caught by |
|---|---|---|
| Xkntt forward/writeback disagreement | `reg` **fails** | *nothing else in the tree* |
| JALR does not clear bit 0 | `insn_jalr` **fails** | `cosim_directed` — **RISCOF passes 76/76** |
| ALU `ADD` becomes `OR` | `insn_add`, `insn_addi`, `insn_lw`, `reg` all fail; `insn_xor` passes | `formal_alu`, `cosim_commit_log`, RISCOF |
| load's rd stalls on `x0` (phantom stall) | **escapes** | `cycle_model` only |
| `minstret` counted at WB | **escapes** | `csr:a9_minstret` only |

The two escapes are the honest boundary. A phantom stall changes no
architectural state, and RVFI carries none of the timing information that would
show it — so `tb/cosim/cycle_model.py` remains the only thing that can see one.
And there are no CSR checks here at all, so nothing about `mcycle`/`minstret`
is proved.

Note that `pc_fwd` does **not** catch the JALR mutation, even though the pc is
wrong: the core is internally self-consistent, fetching from exactly the odd pc
it reports. Only the instruction model, which states `& ~1` directly, sees it.
The same shape appears in `rvfi_mem_addr_not_word_aligned`, which `insn_lw`
misses — a word load that does not trap is aligned already — and only the
sub-word models catch. **Picking the widest test is not picking the strongest
one.**

#### What is deliberately not checked

Stated here rather than left to be discovered from a short check list:

- **`dmem` and the `bus_*` family.** They verify that a load returns what an
  earlier store wrote, which needs a memory model in the wrapper. This wrapper
  leaves `dmem_rdata` unconstrained on purpose — that is what makes the proof
  cover every possible memory response — so memory consistency is covered by
  A5's cosimulation and riscv-tests against a real RAM instead.
- **`ill`, `csrw`, `csr_ill`.** riscv-formal has no instruction model for Zicsr,
  ECALL, EBREAK, MRET or FENCE, and its `ill` check asserts that anything
  outside the model set *traps* — which would demand that this core trap on
  `csrr` and on `fence`. The CSR file is covered by `formal_csr`, the `rv32mi-p`
  tests and RISCOF's privilege suite.
- **The Xkntt encodings themselves.** No stage executes them and there is no
  model, so the proof says nothing about what custom-0 and custom-1 *should*
  compute — only, as above, that the core does not contradict itself about what
  it currently does. M13's work.
- **What the M instructions compute** (A15). `rvntt_muldiv`'s arithmetic is
  abstracted to a free value here. Its **sequencer is not** — `done`, and
  therefore every stall, bubble and retirement time, is the real design's — so
  everything these 43 checks actually depend on is unabstracted. The arithmetic
  is proved by `formal_muldiv`, by the eight `rv32um` tests and by
  cosimulation. See below for why.

#### Three things about the wrapper

- **The memory interface is not a handshake, so there is nothing to constrain.**
  `rvntt_ram` registers each port's address, and the core has no valid, no ready
  and no way to stall on memory. The fixed timing is expressed by `imem_rdata`
  and `dmem_rdata` simply being fresh symbolic values every cycle, which
  over-approximates any one-cycle memory. Adding a `stall` input, as the NERV
  wrapper has, would be modelling a signal this core does not have.
- **`rvfi_mem_addr` reports the full computed address**, word-aligned for
  `RISCV_FORMAL_ALIGNED_MEM` — not the address `rvntt_ram` used. The RAM drops
  everything above its array and aliases rather than faulting; that is the
  memory's behaviour, not the instruction's, and RVFI describes the instruction.
- **Asynchronous reset needed one deliberate thought, not a workaround.** The
  testbench constrains `reset == $initstate`, so reset is high for step 0 only.
  An active-low async reset held through step 0 leaves every reset flop at its
  reset value from step 1, which is what a synchronous reset of the same length
  would give — but it does **not** constrain those flops *during* step 0, so
  nothing in the core may assert about its own state on that first edge.
  `f_started_q` carries a declaration initialiser as well as a reset for exactly
  that reason, and the initialiser is the half that matters.

#### The 64-bit counters turned out not to be the problem

`rvntt_csr.sv` carries 64-bit `mcycle` and `minstret` with incrementers — 128
bits of state that no check here reads, since no CSR is exposed on RVFI — and
the obvious worry was that they would dominate solve time the way depth does for
`formal_regfile`. **Measured instead of assumed: they do not.** At depth 14 the
whole set is four minutes of solver time and the slowest single check is 11 s,
so there is no blackboxing and no `cutpoint` here to explain later. Two 64-bit
adders bit-blast cheaply; it is the *unrolling* of a 32×32 memory that blows up,
not the width of an accumulator. If the depth is ever raised, re-measure before
concluding anything.

### riscv-formal's work area is shared, and two runs will eat each other

`tb/formal/run_riscv_formal.py` builds into
`toolchain/riscv-formal/cores/rvntt/checks/`, and that path is **fixed** — it is
not derived from a temp directory and it is not per-invocation. Two riscv-formal
runs at once therefore delete each other's `engine_0/` mid-solve, and the
symptom is not a failed check. It is sby dying inside its own error handler:

    FileNotFoundError: [Errno 2] No such file or directory: 'engine_0/trace_tb.v'
    ...
    FileNotFoundError: [Errno 2] No such file or directory: 'reg_ch0/ERROR'
    verdict from .../checks/reg_ch0/status: NO-STATUS (sby exit 1)

`RVFORMAL_ERROR: reg_ch0=NO-STATUS` is the harness reporting honestly that it
does not know the answer — which is exactly right, and is the reason a missing
`status` file is not treated as a pass.

A17 hit this by running riscv-formal on a **scratch copy of the tree** whose
`toolchain/riscv-formal` was a symlink back to the real checkout, while the
regression's `mutation_pipeline` was running its own `rvfi:` checks. The two
trees looked independent and shared one work area. **A copy of this repo used
for trial builds must not symlink `toolchain/riscv-formal`**, and nothing else
may run riscv-formal while `make regress` is in flight.

### Two more ways a test can be accidentally blind

Both found by mutation at A9, and both are about the *stimulus*, as at A5 and A7.

- **A probe placed just after a taken branch sits in a pipeline bubble.** The
  `minstret` program read the counter immediately after a taken branch, so MEM
  and WB held bubbles and the EX-counted and WB-counted schemes gave the *same*
  answer. Two ordinary instructions before the probe refill the pipeline and the
  difference appears. A test that cannot reach the state it is checking passes
  for a reason that has nothing to do with the design.
- **Setting both bits of a swap makes a broken swap look right.** The MRET case
  originally set `MIE` and `MPIE` both to 1 before the return, so an MRET that
  forgot to restore `MIE` still produced `0x88`. Clearing `MIE` first gives the
  swap somewhere to move a value *from*.

### `$past` inside `always_ff` costs a cycle to learn

Two things, both of which produced a confusing counterexample first:

- An assertion inside `always_ff` sees the values of the cycle that is *ending*,
  not the ones the edge produces. So `assert (!mstatus_mie_q)` guarded by
  `$past(trap_en)` compares cycle T's state against cycle T-1's input, which is
  the relationship wanted — but it is not what it looks like.
- **`$past` is a register, and in BMC its content at the first step is
  unconstrained** — not the input's initial value. Without an `f_past_valid`
  guard the solver simply invents a trap that never happened, and hands back a
  counterexample in which the input was plainly zero the whole time.

### Mutation anchors go stale, and the harness says so

Two A7 mutations turned into `NO-OP` the moment A8 edited the lines they
anchored to. That is the harness working: an anchor that no longer matches is
reported as a problem rather than silently skipped, so a mutation cannot quietly
stop testing anything. When a step edits `rvntt_core.sv`, expect to re-anchor
the previous step's mutations — and treat a `NO-OP` as a failure, never as
noise.

**A14 broke ten of them at once**, which is what a step that renames `stall` and
adds an arm to two pipeline registers does. Eight were ordinary staleness. The
other two were a different and worse thing, and the harness could not see it:
`rvntt_muldiv` is instantiated from the same two forwarded operands, with the
same port names and the same spacing, as `rvntt_branch` — so A8's
`.a (ex_rs1_fwd),` anchor started matching **twice**, and `replace(old, new, 1)`
silently mutated the *multiplier* instead of the comparator. The verdict was a
`PARTIAL`: a true statement about a bug nobody had injected.

**An ambiguous anchor is worse than a missing one**, because a missing one is
reported and an ambiguous one is obeyed. `mirror_rtl` now requires **exactly
one** match and reports anything else as an error, in the same spirit as the
`NO-OP` rule it sits beside.

### A commit-log diff cannot see timing

This is the A7 lesson worth carrying forward. A **phantom stall** — a bubble
inserted where none was needed — changes no architectural state whatsoever. The
commit log is byte-identical. The program is simply slower, and the first
symptom appears much later as an IPC number that disagrees with the LLVM
`SchedMachineModel`, at a point where nothing points back at `rvntt_hazard.sv`.

`tb/cosim/cycle_model.py` closes that. It predicts the **span** — the cycle
distance from the first retirement to the last — as

    span = (retired - 1) + stalls + 2 x redirects

and the testbench reports the measured span — plus, as of A14, a
`Σ(occupancy − 1)` term for the multi-cycle instructions. Using the span rather
than a total cycle count means the model needs to know neither the reset length
nor the pipeline fill depth; both cancel. Hazards are found by decoding the *dynamic*
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

### A14 — the multi-cycle EX mechanism, and the M extension on top of it

`MODS_A` A14. The mechanism was built first and generically, because plan §8 I1
needs exactly it for the `Xkntt` Tier-1 unit and building it twice is how you get
a hazard bug. M is its first user because M arrives with **rv32um, RISCOF and
riscv-formal as external references**, and a custom extension arrives with none.

**Two stalls, and they do opposite things to the same register.** `id_stall`
(A7) has the consumer in ID, so ID/EX takes a bubble. `ex_stall` (A14) has the
producer in EX, so ID/EX **holds** and EX/MEM takes the bubble. Getting that
backwards either drops the multi-cycle instruction on the floor or executes it
repeatedly — both are in the mutation manifest. The front end holds for either.

**The whole of the core's side of the mechanism is three lines**: a `req` that is
high for every cycle the instruction is in EX, a `done` that is high on the last
of them, and `ex_stall = req && !done`. The latency is a property of the unit.

**The result comes back through `ex_result`, not through a new `result_sel_e`
member.** A new member has to be added to *two* case statements that must agree
— `ex_mem_fwd_data` and `mem_result` — and the one time a member was added to
those, they disagreed and shipped (A11's `RES_XKNTT` bug, above). Delivering
through `ex_result` means `result_sel` stays `RES_ALU` and neither case
statement changes at all, so there is nothing to disagree about.

**The store path is deliberately *not* gated on `ex_stall`.** That gate would sit
on the design's critical path (`rtl/soc/CLAUDE.md`). The invariant it would
enforce — no multi-cycle instruction is also a memory operation — is *proved*
instead, in `rvntt_decode`'s formal property 7, which is free.

**`ex_redirect` *is* gated on `!ex_stall`**, and that gate is unreachable today:
no multi-cycle instruction is a branch, a jump, an MRET or a trapping
instruction. It is one AND gate off the critical path, and it makes an invariant
structural rather than argued. There is deliberately **no mutation for it** —
removing it is a no-op, and a manifest entry that is expected to escape teaches
nothing.

**`minstret` needed `&& !ex_stall`, and nothing except `a9_minstret` can see it.**
A 34-cycle divide would otherwise count 34 times. No architectural value moves,
so the commit-log differ, rv32um and riscv-formal are all structurally blind;
`a9_minstret.S` now puts a multiply and a divide inside its loop for exactly
this. It is the same shape as A7's phantom stall, from the other side.

**The RVFI shadow takes the same bubble `ex_mem_q` takes.** Without it a 34-cycle
divide is *reported* 34 times and every check downstream of `rvfi_order` is wrong
from there on. The load-use stall needed no equivalent, because it bubbles ID/EX
and `ex_valid` is already low — which is why this was easy to miss.

#### The datapath

One 33×33 signed multiplier serves all four multiplies: extending each operand
to 33 bits with its sign bit *or with zero* turns "signed × unsigned" into an
ordinary signed multiply, which is the only way `MULHSU` is not a third
datapath. Three register stages, 4 cycles of EX occupancy.

The divider is radix-2 restoring on magnitudes, 32 iterations, 34 cycles, and
**data-independent by construction**. An early-out on a small dividend would be
free performance and would make `tb/cosim/cycle_model.py` unbuildable — the
model predicts the span from the retired instruction stream, and a latency it
cannot compute from the opcode alone is a latency it cannot predict. (It is also
plan §B4's constant-time property, but that is not why.)

**Divide by zero is a real special case; signed overflow is not.** The zero
divisor is detected at capture and bypasses the loop — quotient all ones,
remainder the *original signed* dividend. `-2³¹ / -1` falls out of the general
path: the magnitude of `-2³¹` is `0x80000000`, dividing by 1 gives `0x80000000`,
and the sign rule (negative iff exactly one operand is negative) says positive,
so it is never negated. There is therefore **nothing to delete** for a mutation
to find, which is why the manifest breaks bit 31 of a positive quotient instead
— the one bit only that case needs.

#### DSP inference: the checking script was wrong before the design was

**4 × DSP48E1**, confirmed from the synthesis report and not assumed, which is
what `MODS_A` A14 insists on. `fpga/scripts/synth_ooc.sh <module>` synthesises
one module out of context in about a minute — `synth_design -rtl` (what
`elab_core.sh` runs) stops at the RTL netlist and says nothing about mapping.

The first version of that script reported **`DSP=0 FF=0`** over a netlist
containing four DSP48E1s and 239 flops, because it filtered on `PRIMITIVE_TYPE`
group names that were guessed rather than looked up. Believed, it would have
sent someone to fix a multiplier that was already correct. It now prints a
**histogram of every `REF_NAME` present**, filtered by nothing — a report that
names no group cannot name one wrongly. This is the third time this exact shape
has appeared here, after A10's RISCOF exit code and A11's sby exit code:
**take the verdict from the artefact.**

What the mapping table says, and it is worth reading rather than the count:
`AREG` and `BREG` are absorbed, `PREG` is absorbed on the two cascade DSPs, and
**`MREG` is not used at all** — the 33×33 is one expression, so there is no
register in the source between the partial products and the cascade adds for
Vivado to push into `M`. The consequence is a combinational path through two
chained DSPs inside one clock, which was flagged here as a candidate for the new
critical path with A16's Fmax search named as the thing that would settle it.

**A16 settled it: no.** The post-route critical path is the same
`EX/MEM rd_addr → forwarding mux → ALU → trap → BRAM` path A12 had, the
multiplier is nowhere on it, and Fmax went *up* — 70.131 to 73.752 MHz. The
unused `MREG` costs nothing today. It is still the lever to reach for if a later
step makes the multiplier critical, and it is written down here so that step does
not have to rediscover it.

Total: 403 LUTs, 239 FFs, 53 CARRY4, 4 DSP48E1, out of 63,400 / 126,800 / 240.

#### What fault injection found here

Twelve mutations. Three results are worth carrying:

| mutation | caught by | not caught by |
|---|---|---|
| M instructions occupy EX one cycle too long | `cycle_model`, via `a14_muldiv` and the random suite | **everything else** — every value and every ordering is correct |
| `minstret` counts stalled cycles | `csr:a9_minstret` | everything else |
| EX/MEM latches every stalled cycle (one instruction retires 34×) | the commit-log differ | **`rv32um/mul`** |
| divide-by-zero remainder returns the magnitude | `directed:a14_muldiv` | **`rv32um/rem`** |

The last two are statements about the external suite, and they are the useful
kind. `rv32um` is self-checking on **final register values**: the last of 34
spurious retirements writes the correct answer, so the three before it leave
nothing it can look at. And its only negative-dividend-over-zero case is
`rem(-2³¹, 0)` — the one value whose 32-bit magnitude *is itself*, which is
precisely the value that makes the mutation invisible. `a14_muldiv.S` uses
−12345.

**One mutation escaped, and the escape was the finding.** Dropping the enable on
the multiplier's operand registers — so they reload from the drifting forwarding
muxes every stalled cycle — was caught by nothing, correctly. The multiplier is a
three-deep register *chain* whose latency equals its depth, so the value read on
the done cycle is the product of the operands present on the **start** cycle
whatever the enable does; the later reloads are still in flight behind it. The
enable is load-bearing for the **divider**, whose state is a loop rather than a
chain. The bug that mutation was reaching for is real and lives one level up — in
`rvntt_core` wiring the unit to `id_ex_q.rs1_data` instead of to the forwarding
muxes — and *that* mutation is in the manifest and is caught.

#### riscv-formal got very slow, and it is not a depth problem

`MODS_A` §3.2 predicted trouble here and predicted the wrong cause. It expected
the **34-cycle divider** to push the required BMC depth from 14 to ~46. What
actually happened is that the existing 43 checks, at their existing depth of 14,
went from about 40 s of wall time for the whole set to **several hundred seconds
per check**.

That is the multiplier, not the divider, and it is not about depth at all: a
combinational 33×33 multiplier bit-blasted and unrolled fourteen times is the
classic worst case for a SAT solver, and it is in the cone of the RVFI outputs
whether or not any check reads it. Nothing about the divider's *latency* is the
problem; nothing about depth would fix it.

The fix belongs to A15 and is §3.2's route 2 in a sharper form: keep the
sequencer, so `done` and therefore the pipeline's timing stay exactly real, and
abstract only the **arithmetic** — a free value under `RISCV_FORMAL`. The 43
RV32I checks do not care what M computes; they care that the pipeline is
self-consistent around it, and that is preserved exactly. The arithmetic is then
proved separately as a standalone module, and is already covered by rv32um and
by Spike cosimulation.

---

### A15 — M compliance and formal, and two things that had to be abstracted

`MODS_A` A15. RISCOF now claims `RV32IMZicsr`; `riscv-formal` needed two
changes, and §3.2 named the wrong cause for one of them.

#### riscv-formal: the problem was the multiplier, not the divider

§3.2 predicted that A14's **34-cycle divider** would push the required BMC depth
from 14 to about 46 and very likely not converge. What actually happened is that
the existing 43 checks, at their existing depth of 14, went from about 40 s of
wall time *for the whole set* to **250–680 s per check**. Nothing failed; it
simply stopped finishing.

That is the **multiplier**, and it is not a depth problem at all. A combinational
33×33 multiplier bit-blasted and unrolled fourteen times is the canonical worst
case for a SAT solver, and it sits in the cone of the RVFI outputs whether or not
any check reads it — so every check paid for it and none benefited. Raising or
lowering the depth would not have touched it.

The fix is §3.2's route 2, placed where it pays: `RVNTT_ABSTRACT_MULDIV` replaces
`result` with a free value and leaves the **sequencer** exactly as it is. The 43
checks do not care what M computes; they care that the pipeline stays
self-consistent around it, and that is preserved rather than approximated. Back
to 281 s of solver time for the whole set.

The define is deliberately **not** `RISCV_FORMAL`: `formal_muldiv` compiles the
same file and must see the real arithmetic. Naming the abstraction after the tool
that needs it makes it impossible to enable by accident and greppable when a
result looks too good.

#### `liveness` is the one check whose depth is a latency

It genuinely did fail, and it is the only one that did. Its property is *"if
instruction N retires at the trigger cycle, N+1 has retired by the check
cycle"* — a gap between two **retirements**, not, as everywhere else here, a
distance between two dependent instructions. With the trigger at 9 that gap is
1 normally, +2 for a control-flow flush, +1 for a load-use stall, and **+33 for
a `DIV` occupying EX for 34 cycles**. 9 + 37 = 46, so it runs at 47 and the other
42 stay at 14 (§3.2's route 1, used only here). Raising the *global* depth to
suit one check would cost every check; lowering it to make one fit is the thing
§3.2 says never to do. Same argument, both directions.

It proves in three seconds — but only with the multiplier abstracted. Against the
concrete one it took eight seconds to *fail* at depth 14 and would not have
finished at 47.

#### `reg` found a real bug, again

`reg_ch0` failed on the first honest run with the abstraction in place, from a
**`MULH`**: `rvfi_rs1_rdata` reported `0xfffffff5` where the register held
something else.

The RVFI shadow sampled the forwarded operands on a multi-cycle instruction's
**last** EX cycle. By then the producers behind it have drained out of MEM and
WB — EX/MEM is bubbled on every stalled cycle, so `FWD_MEM` stops matching after
the first and `FWD_WB` after the second — and the mux has fallen back to
`id_ex_q.rs1_data`, the value the register file held at ID time. By the last
cycle of a divide that is stale by two instructions.

The **arithmetic was never affected**: the unit latches its operands when it
starts. Only the *report* was wrong — and `rvfi_rs1_rdata` is not an
architectural value, it is a claim about one, so no commit-log diff, no `rv32um`
test and no Spike comparison could see it. The fix holds the whole packet from
the instruction's first EX cycle. This is the second time `reg` has caught
something nothing else in the tree could reach.

#### RISCOF: the ISA string was in three places and one was hardcoded

Claiming `RV32IMZicsr` selects eight more tests, which is the point. It also
found a latent bug in the A10 harness: the Spike reference plugin's ISA string
was the literal `'rv32i'`, so the M tests compiled `-march=rv32im` and ran on a
Spike told `rv32i`. It hung rather than failing. See A10's trap 5 above; the
string is now derived from the yaml, and the reference invocation has a timeout.

#### The divider, proved — and the property that would not solve

`formal_muldiv` runs at **depth 37**, the first proof here whose depth is set by
a latency rather than a dependency distance: a divide presents its result on its
34th EX cycle, so nothing about it is observable before step 34.

**The obvious property does not solve.** The natural statement of correctness is
`quotient × divisor + remainder == dividend` with `remainder < divisor`, which
pins the answer completely. Written that way it asks the solver to prove a
32-step shift-and-subtract loop equivalent to a symbolic 32×32 multiply —
multiplier equivalence, the canonical hard SAT instance. bitwuzla reached step 34
in five seconds and then sat on that one query for a quarter of an hour. Depth,
solver and patience were all irrelevant: **the shape of the query was the
problem, not its size.**

**The same statement with no multiplication anywhere.** Track the algebra
alongside the loop in ghost registers whose recurrences are shifts and adds, and
assert an *invariant after every iteration* rather than a conclusion after the
last. With `D` the dividend magnitude, `V` the divisor and `k` the iteration
count, the loop maintains `rem + Q_k·V == D >> (32−k)`, `rem < V` and
`quo == (D << k) | Q_k`. Carrying `Q_k·V`, `D >> (32−k)`, `D << k` and `Q_k` as
ghosts turns both lines into 64-bit comparisons. At `k = 32` the low half of
`D << k` is zero and they collapse to the original identity — **derived rather
than asserted**. It passes in **4m13s**.

Generalise it: when a property asks a solver to relate two different circuits
that compute the same arithmetic, state the *invariant they both maintain*
instead of the *conclusion they both reach*.

**What that is and is not a proof of**, stated rather than left to be discovered:
it is a complete proof of the **magnitude loop** — 32 cycles of subtle state, and
the only part a directed test cannot enumerate. It says nothing about the sign
fixup or the quotient/remainder selection, which are a handful of combinational
gates covered by `rv32um`, by `a14_muldiv.S`'s four-way sign matrix, and by 1000
random programs against Spike.

Two things about writing the properties, both of which cost a run:

- **A standalone proof of this module needs `initial assume (!rst_n)`.** Its
  multiplier registers are deliberately unreset so Vivado can pack them into the
  DSP, so with `rst_n` free the solver starts from a state the hardware cannot
  reach — a `done` on step 0 over arbitrary register contents — and every
  property becomes a claim about nothing. The first run failed at step 0 before
  reaching any arithmetic.
- **A property true of only half the operations it is asserted over is a wrong
  property, not a found bug.** `a_rem_sign` ("the remainder agrees with the
  dividend in sign") failed at step 34 on a `DIVU` with a dividend above 2³¹ —
  where the top bit is a magnitude bit, not a sign. It needed a `signed`
  flag it had no other use for.

And one real, if benign, finding: `done` was `active_q && cnt_q == target`, so a
request that dropped on the exact cycle the counter reached its target — an abort
arriving at the finish line — raised `done` for an instruction no longer in EX.
The core would not act on it, because it computes `ex_stall = req && !done` and
latches nothing when `req` is low. `done` now includes `req`: one AND gate, and
the module's stated contract becomes literally true instead of nearly true.

---

### A17 — the dedicated address adder, and an assertion that pays for itself

`ex_addr_misaligned` used to be `|ex_alu_y[1:0]`, read off the **muxed** ALU
output. So the ALU's four-level operation-select mux sat in front of the
alignment check, which gates the trap, which gates the byte enables, which reach
the BRAM's write enable — 33% of the critical path, spent deciding whether an
address was aligned using a mux that had just chosen between an AND, a shift and
a sum.

A memory address is always `rs1 + imm`, and the decoder enforces it. So there is
a second adder now:

```systemverilog
wire [31:0] ex_mem_addr = ex_rs1_fwd + id_ex_q.imm;
```

feeding `dmem_addr`, the alignment check, the store byte offset, `mtval` and
RVFI's `mem_addr`. **26 LUTs, and Fmax went from 73.752 to 83.542 MHz.**

**The whole thing rests on one equality**, and that equality is a property of the
*decoder* — which is a file that gets edited. So it is asserted rather than
argued:

```systemverilog
`ifdef RISCV_FORMAL
  always_comb
    if (id_ex_q.valid && (id_ex_q.ctrl.mem_read || id_ex_q.ctrl.mem_write))
      a_addr_adder_matches_alu: assert (ex_mem_addr == ex_alu_y);
`endif
```

An assert in the design is an obligation on **every** riscv-formal check, so all
43 of them prove it at depth 14 and it costs no new check and no new runtime.
The `RISCV_FORMAL` guard is not because the property is formal-only; it is
because that is the only harness here that reads the core with assertions
enabled, and an unguarded SVA block would be dead text in nine Verilator builds.

**A loose constraint is not a measurement.** Lever 1 was first judged by
re-running A16's old 73.0 MHz constraint and comparing slack: +0.111 → +0.290 ns,
a 0.18 ns improvement, which is what it would have been reported as. The binary
search then found the same design passing at 83.542 MHz — 1.589 ns faster. The
router optimises to the constraint and stops; the slack it leaves is a
measurement of when it stopped, not of how fast the design is.

### A18 — the instrument, and why it is not in the core

`cycles = retired + load-use stalls + multi-cycle EX stalls + 2 × redirects`,
residual **exactly zero** on Dhrystone, CoreMark and both NTT builds. Not a
counter in the RTL: a Verilator observer (`tb/perf/tb_profile.cpp`) built with
`--public-flat-rw`, because hardware counters would have perturbed the design
A17 was tuning and are not needed — this SoC is deterministic and the simulation
reproduces the board exactly.

**Two closures, not one, and the second exists because of a fault injection.**
`--selftest` breaks the accounting five ways. Four move the residual. The fifth —
classifying every branch as taken — leaves the residual at **exactly zero** and
is caught only by

    redirects = taken branches + JAL + JALR

which also holds exactly. A19 is judged on the taken/not-taken split, so the
split needed a check the cycle identity is structurally blind to. See
`docs/a18-stalls.md`.

### A19 — the random suite cannot see a branch predictor

All eight A19 mutations escaped `random:branch` on their first run. Not one was
caught, and the reason is structural: `gen_random_prog.py` emits **forward-only**
branches and jumps, because that is what guarantees a generated program
terminates. A random program is therefore a straight line in which **every
control transfer site executes at most once**, and a predictor whose entire job
is to remember what a site did last time is inert in every random program this
project has ever produced.

The 1000-program cosimulation still proves what it always proved — the predictor
is architecturally invisible, byte-identical against Spike — and it is
structurally unable to say whether the predictor works. `sw/tests/a19_bpred.S`
is what says that: eight loop shapes, span-checked against `model/bpred.py`.

**And the model had to be taught the pipeline.** Its first version applied every
predictor update immediately and predicted a 916-cycle span where the RTL
measured 992. An update lands in EX and cannot reach a lookup that already
happened, so it is visible only to a transfer **four or more retire cycles
later** — which a three-instruction loop is not. `bpred.DelayedBPred` queues
updates on that rule and the two now agree to the cycle. The rule costs the
benchmarks nothing (their loops are longer), which is worth knowing before anyone
spends a mux forwarding the write.

**The retire-versus-fetch error was in the MODEL, and it hid asymmetrically.**
The visibility rule was first written on *retire* cycles; `retire - fetch` is not
constant, because a load-use interlock holds an instruction in ID after its
prediction has been made. Dhrystone reaches its callees through stalls and
CoreMark does not, so the error showed up as 39 wrong mispredicts on one
benchmark and none on the other. A front-end register mirror in `tb_profile.cpp`
now dates each instruction by its **fetch** cycle and the two agree to the unit
everywhere. **When a model disagrees with the RTL on one workload and not
another, the asymmetry is the clue** — a uniformly wrong model is usually a wrong
constant, an asymmetrically wrong one is usually a wrong *event*.

**`SUPPRESS_AFTER_REDIRECT` is an architectural rule that timing forced.** The
first bitstream failed 80 MHz by 3.886 ns because looking the BTB up with
`pc_next` drags the forwarding mux and the whole ALU carry chain into the fetch
path — `pc_next` contains `ex_redirect_target`. The lookup now reads only
registered sources, so the instruction at a redirect target cannot be predicted.
That is in `docs/a19-bpred-spec.md` and in `model/bpred.py`, not just in the RTL,
because it changes what the machine *does* and not merely how fast: 4,002
Dhrystone cycles, 0.33%. See `rtl/soc/CLAUDE.md` for the path and the 77.501 MHz
it bought.

**Two timing levers were added afterwards and MEASURED to be free**, not assumed:
a dedicated `pc + imm` branch/JAL target adder, and `max_fanout` on `ex_redirect`
(244 loads, 1.491 ns of route). Reverting only those two and re-running the
profile gives **52 counters identical in all four regions**. The adder's equality
with the ALU is additionally proved by `a_pc_target_matches_alu` under
riscv-formal — over every reachable state, rather than over one benchmark.

**A19 invalidated an A7 mutation anchor, and the harness said so correctly.**
Moving the PC hold from a branch of the `pc_q` `always_ff` into an arm of the
combinational `pc_next` mux left `stall_lets_the_pc_advance` matching nothing;
`run_mutation.py` reported **NO-OP** rather than scoring a mutation it had not
applied. That is the fourth stale anchor after a rename here. What made it
expensive was not the anchor but `run_regress.py` echoing only the last 40 lines
of a failing test: a 75-row manifest overflowed the tail, so a precise NO-OP
message arrived as a bare `exit 1` and cost a fifteen-minute standalone re-run to
recover. **Both are fixed, and the reporting fix is fault-injected both ways** —
it fires on a long failing test and names the hidden row, and stays silent on a
long passing one, a verbose passing one and a short failing one.

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

- **A forwarding mux and a writeback mux disagreeing on one enum arm** (A11).
  `ex_mem_fwd_data` sent everything that was not `RES_PC4` to `ex_result`;
  `mem_result` sends `RES_XKNTT` to zero. Unreachable by any RV32I program, so
  five suites and 35 mutations had missed it; riscv-formal's `reg` check found
  it in seven seconds. **An unimplemented feature is not an absent one** — the
  decoder accepts Xkntt, so the datapath has to be self-consistent about it even
  before a stage executes it.
- **A test runner that reported 43/43 over a broken adder** (A11). `sby` exits 0
  on a failed check, because riscv-formal's generated `.sby` files carry
  `expect pass,fail`. Identical in shape to A10's RISCOF exit code. The rule
  that came out of it: **for any third-party test driver, find out what it does
  on failure before believing a green run, and take the verdict from the
  artefact rather than from the process.**
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
- **The test kinds are `formal:`, `directed:`, `random:`, `riscv:`, `csr:`,
  `rvfi:` and `a4`.** `rvfi:<check>` runs ONE riscv-formal check against the
  mutated tree, for the same reason the random suites here are eight programs
  and not a thousand: the manifest entry is a claim about *which* check sees the
  bug, and running all 43 would let one broad check be credited with everything.
- **A mutation that fails to build is an error, not a catch.** Deleting an
  expression's only use makes Verilator's `UNUSEDSIGNAL` the detector rather
  than the test. Two A4 mutations and two A6 ones had to be reformulated so
  every signal stayed referenced.

A baseline run against unmutated RTL comes first, so a stuck-at-fail test
cannot appear to catch everything.

## Formal depth

The `riscv_formal` check set is the exception that proves the rule: it runs at
depth 14 over the whole pipeline, 64-bit counters included, in seconds. Width is
cheap for a bit-blasting solver; **unrolling a memory is what is expensive**, and
that is what the numbers below are about.

`formal_muldiv` is the exception in the other direction: **depth 37**, because
its deepest property is a *latency* — a divide presents its result on its 34th
cycle, so nothing about it can be observed before step 34. It is also the one
proof here whose natural formulation had to be abandoned: see A15 above for why
`quotient × divisor + remainder == dividend` does not solve and a per-iteration
invariant does.

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

---

## A20 — the counters, and the two things that made them worth building

`MODS_A2` A20. Six `Zihpm` counters. The full write-up is
`docs/a20-counters.md`; two findings belong here because they are about how this
project checks things rather than about the counters.

**A comparison that passes on the first run has not been shown to work.** The
hardware-counter-against-A18 comparison failed immediately, on Dhrystone, by
exactly 1 — and passed on CoreMark. **Neither side was wrong.** The instrument
delays its redirect count by three cycles *on purpose*, so a redirect's two lost
cycles are charged to the region that actually lost them; a counter in silicon
increments on the pulse and cannot do that. The two therefore disagree, per
region, by the number of pulses in flight across a boundary.

The tempting fix was a tolerance. The right fix was to make the instrument keep
**both** counts — the delayed one for the cycle identity, the raw one for this
comparison — so the check stays exact. **A ±1 tolerance would have passed this
and also passed a genuinely miscounting predicate**, which is the entire class of
bug the comparison exists to find.

**Constant versus proportional is the whole test for instrumentation
overhead.** The third validation path has software read the counters through
`csrr`, and it reads *high* — the six `csrr`s and their loop fall inside the
counted window and outside the timed one, by the same convention `minstret`
already uses here. The first version of that check used a relative bound and
failed the small region while passing the large one, which is exactly backwards:
**a snapshot footprint is a constant, and a miscounting predicate is a
proportion.** Doubling the Dhrystone region doubled every event count and left
all four excesses identical to the event (+6, +0, +16, +23). That measurement is
the justification for an absolute bound, and it is recorded rather than assumed.

**A guard believed to be load-bearing, which was not.** The load-use event is
written `id_stall && !ex_stall`, mirroring the instrument's tie-break, and the
comment beside it said that was the line the whole step was won or lost on.
Injecting the obvious mutation — drop the guard — changed **no count anywhere on
either benchmark**, because the two stalls are disjoint by construction:
`id_stall` needs a load in EX and `ex_stall` needs a multiply or divide there.
The claim was wrong, fault injection is what showed it, and the fix was to turn
the real fact into `a_stalls_are_disjoint`, proved by every riscv-formal check at
depth 14. **A guard against an impossible case is harmless; a guard mistakenly
believed to be doing work is not**, because it makes the next reader model an
overlap that cannot happen.

**One gap is recorded rather than papered over.** `hpm_btbhit` has no
counterpart in the retired instruction stream — nothing there says whether the
BTB held an entry — so it is *reported, not checked*, and the mutation that would
exploit that (`pred_hit` ignoring `flush`) is listed in
`tb/mutate/run_mutation.py` as uncatchable with the reason, rather than given a
catcher that does not catch it. Closing it needs an assertion in
`rvntt_bpred`'s own formal run relating `pred_hit` to the previous cycle's flush.

---

## A21 — B and Zbkb, and the sixth green tick that was not about anything

`MODS_A2` A21. 34 instructions; full write-up in `docs/a21-bitmanip.md`. Three
things belong here because they are about how this project checks itself.

**A new functional unit went beside the ALU, not into it, and the reason is
measured.** `MODS_A2` §3.4 puts the ALU result mux at a third of the critical
path, with `ex_alu_y` feeding `ex_jump_target`. `rvntt_bitmanip.sv` joins at
`ex_result` instead — where `rvntt_muldiv` and the Zicsr read already join — so
`alu_op_e` stays 4 bits and `rvntt_alu.sv` stays exactly as verified. **When a
new class of instruction arrives, the question is not "where does it fit" but
"what is it in front of".**

**`tb/cocotb/run_cocotb.py` ended in `return 0` and had since A1.**
`cocotb_tools`' `runner.test()` runs the tests, writes `results.xml`, and returns
normally whether they passed or failed. **Every cocotb test in this project
reported PASS unconditionally**, and it surfaced only because a broken wrapper
printed `TESTS=6 PASS=1 FAIL=5` and a green regression row on the same run.
Fixing it exposed two real failures hidden since A14: `test_alu_cocotb` and
`test_immgen_cocotb` each kept their own copy of the spec-drift member count, and
both went stale when the two multi-cycle latency constants were added. The count
is now exported once as `ref.PKG_MEMBERS_CHECKED`.

**Sixth time.** A10's RISCOF exit code, A11's sby exit code, A14's
`synth_ooc.sh` reporting `DSP=0` over four DSPs, A19's `bench_hardware` pointing
at a three-step-old bitstream, A20's stale mutation anchors, and now this. The
rule earned by all six: **a tool's exit code is not its verdict unless you have
checked that it is** — and the check belongs in the runner, not in the reader.

**The Spike reference plugin dropped every Z extension, for the second time.**
It built its ISA string from the single letters plus the literal `_zicsr`, so B's
tests compiled `-march=rv32izbb` while Spike was told `rv32im_zicsr`, trapped on
the first `clz`, and **spun to a 600-second timeout reporting nothing**. Its own
header already described A14's identical bug with `mul`. It now carries all Z
extensions and then *reassembles the string and compares it to the yaml*,
refusing to run on any mismatch — because a reference with a smaller ISA than the
DUT does not fail, it hangs.

**And one mutation escaped for the right reason.** The first "rotate by zero"
mutation was `6'd32 - shamt`, which is a valid alternative implementation — on a
32-bit target `a << 32` is zero. It escaped because it was not a bug. Together
with A20's stall-tie guard, that is twice in two steps: **an escaped mutation is
sometimes evidence about the mutation, not about the checks.**
