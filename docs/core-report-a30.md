# The `rvntt` Core after Track A

### Design, chronology and measurements for the post-A30 RV32IM_B_Zicond_Zkr_Zkt pipeline

**Revision:** post-A30 · commit `5d6540a` · tag `m7.5-zkr-zkt` · 2026-09-04
**Target:** Digilent Arty A7-100T — `xc7a100tcsg324-1`, Artix-7, **-1** speed grade
**Toolchain:** Vivado 2025.2 · GCC `riscv-none-elf` · Verilator · Yosys/SymbiYosys · Spike (forked)

> **Published, illustrated version:** <https://claude.ai/code/artifact/6f307952-fb8a-40cb-a73c-c4490ffe4fe0>
> (same content, with the figures rendered). Companion roadmap: <https://claude.ai/code/artifact/e543b2d8-d3ad-440f-b70e-bcf98e037096>

---

## 0. How to read this document

This is the closing report for **Track A** of the `riscv-ntt` project: a soft RISC-V
processor built as the host for an ML-KEM-768 number-theoretic-transform
coprocessor and the `Xkntt` custom instruction-set extension that binds them.
Track A delivers the *host*. Tracks B and C — the coprocessor and the LLVM
backend — are not started, and the boundary between what is finished and what is
not is stated explicitly rather than implied.

The document has three parts, and they are meant to be readable independently.

- **Part I — The core as it stands.** What the machine is, in every dimension
  that matters: instruction set, microarchitecture, privileged state, physical
  implementation, measured performance, and the verification apparatus that
  stands behind each claim. Read this if you want to know what you have.
- **Part II — The chronology, A1 → A30.** Thirty engineering steps, each in the
  same shape: *what the machine was, what changed, why, what went wrong on the
  way, and what the result was.* Read this if you want to know **why** the
  machine is the way it is, or if you are about to change it.
- **Part III — Cross-cutting findings.** Six patterns that recurred often enough
  across thirty steps to be worth naming. These are the transferable part.

A companion document, [`docs/core-roadmap.md`](core-roadmap.md), takes the same
evidence forward into proposed future work.

**A note on numbers.** Every figure in this document is measured, and the
measurement's provenance is given. Where a figure is an assumption, an estimate
or a bound rather than a measurement, it says so in the same sentence. Where a
claim has a boundary — something the evidence does *not* establish — the boundary
is stated next to the claim rather than in a footnote. That convention is not
stylistic; it is the project's central working norm, and Part III explains what
it cost to learn.

---

# Part I — The core as it stands

## 1. Executive summary

`rvntt_core` is a **five-stage, single-issue, in-order RV32IM pipeline** with the
ratified **B** bit-manipulation extension, **Zbkb**, **Zicond**, machine-mode
**Zicsr/Zicntr/Zihpm**, and the two RISC-V scalar-cryptography support extensions
**Zkr** (entropy source) and **Zkt** (data-independent execution latency). It runs
on an Artix-7 at **96.246 MHz** inside a small SoC — 128 KB of dual-port block RAM,
a UART and a GPIO block — and its benchmark numbers have been confirmed on the
physical board across three independent JTAG programming passes with all 42
integer counters identical every time.

| Headline | Value | Provenance |
|---|---:|---|
| ISA string | `RV32IMZicsr_Zicond_Zba_Zbb_Zbkb_Zbs_Zkr_Zkt` | `isa_consistency`, 6 sources cross-checked |
| **Fmax** | **96.246 MHz** | binary search on post-route WNS, 6 implementation runs |
| **DMIPS/MHz** | **0.9361** | board, 3 JTAG passes, 2 000 000 Dhrystone runs |
| **DMIPS** | **90.096** | board, at 96.246 MHz |
| **CoreMark/MHz** | **3.3896** | board, 3 JTAG passes, 3500 iterations |
| **CoreMark** | **326.24** | board, at 96.246 MHz |
| **IPC** | **0.8734** (Dhrystone) / **0.8689** (CoreMark) | board hardware counters |
| Area | 5502 LUTs · 1987 FFs · 32 BRAM tiles · 4 DSP48E1 | post-route, `xc7a100tcsg324-1` |
| Device utilisation | 8.7% LUTs · 1.6% FFs · 23.7% BRAM · 1.7% DSP | `report_utilization`, post-route |
| Compliance | **RISCOF 120/120** | `docs/riscof-report.html` |
| Formal | **riscv-formal 77/77** at BMC depth 14 | plus 12 module proofs |
| Regression | **56 PASS · 1 XFAIL · 2 SKIP · 0 FAIL** | 59 registered tests |
| Mutation testing | **90/90 caught as declared** | `tb/mutate/run_mutation.py` |

Against the project's own starting point — the RV32I core measured on the same
board at A13 — the finished machine is **1.37× faster in clock**, **1.76× faster
on absolute Dhrystone**, and **4.84× faster on absolute CoreMark**.

**What it does not do.** It executes no `Xkntt` instruction. The decoder
recognises the extension and the SoC lights a red LED if one ever retires, but no
stage implements one; that is Track B's work and plan §8's integration step. There
is no flash image, so configuration is volatile. There are no interrupts, no
caches, no PMP, no virtual memory, and no compressed instructions. §9 of this
part lists every boundary explicitly.

---

## 2. Instruction set

The canonical ISA string is written in six places — the RISCOF YAML, the Spike
reference plugin, the build flags, `rv32i_pkg.sv`'s `MISA_VALUE`, the RTL
decoder and the Python model — and a 0.02-second regression pre-flight
(`isa_consistency`) requires all six to agree. That check exists because the
failure mode of a mismatch is not a failure: a reference model told a *smaller*
ISA than the design traps on the first instruction it does not know and **spins to
a timeout**, reporting nothing. That has cost this project two runs.

| Extension | Content | Verification |
|---|---|---|
| **RV32I** | 37 base integer instructions | RISCOF 38/38, riscv-formal 36 instruction models |
| **M** | `mul mulh mulhsu mulhu div divu rem remu` | RISCOF 8/8, `rv32um` 8/8, `formal_muldiv` depth 37 |
| **Zba** | `sh1add sh2add sh3add` | RISCOF (ratified-B 29/29), riscv-formal |
| **Zbb** | `andn orn xnor clz ctz cpop max maxu min minu sext.b sext.h zext.h rol ror rori orc.b rev8` | as above |
| **Zbs** | `bclr bclri bext bexti binv binvi bset bseti` | as above |
| **Zbkb** | `pack packh brev8 zip unzip` (+ `rol/ror/rori/andn/orn/xnor` shared with Zbb) | RISCOF 5/5 |
| **Zicond** | `czero.eqz czero.nez` | RISCOF 2/2 |
| **Zicsr** | full machine-mode CSR access | riscv-tests, `formal_csr` |
| **Zicntr** | `cycle`/`instret` + high halves, user shadows | `a9_minstret`, hardware |
| **Zihpm** | 6 programmable 64-bit counters | `hpm_counters`, board |
| **Zkr** | `seed` CSR (0x015) with entropy source | `formal_seed`, `entropy_health` |
| **Zkt** | data-independent latency, 37 instructions claimed | `zkt_latency` (cone of influence + riscv-formal) |

**Two `misa` bits are deliberately not set, and both are recorded boundaries
rather than oversights.**

`misa.B` is withheld because **riscv-config 3.18.3 cannot express the `B` letter in
an ISA string at all** — every spelling is rejected as "does not match accepted
canonical ordering" — and it derives the expected `misa` from single-letter
extensions only. Asserting bit 1 makes the RISCOF configuration invalid, at which
point the entire compliance run silently vanishes. Nothing is lost by withholding
it, because arch-test selects suites by **regex on the ISA string**, not from
`misa`. (A related, non-obvious constraint: riscv-config requires `Zbs` to come
*after* `Zbkb`, so the canonical string is `Zba_Zbb_Zbkb_Zbs` and is **not**
alphabetical.)

`misa.K` is withheld because `K` is the *umbrella* letter for scalar cryptography.
Claiming it would claim Zkn and Zks, which this core does not implement. arch-test
ships no `Zkr` or `Zkt` suite — checked rather than assumed: the RISCOF report
before and after adding both letters has a **byte-identical test set**.

**Reserved encoding fields are strict.** A nonzero field in a position an
instruction does not use is an *illegal instruction*, not an ignored one. This is
enforced against a Python model over 10⁶ random 32-bit words with zero
mismatches, and it is the property that a lax decoder and a strict decoder
disagree about on exactly those words. Base RV32I `FENCE` is the deliberate
exception — the ISA says base implementations *shall ignore* those fields, so
copying the strict rule onto `FENCE` would diverge from Spike.

`misa` reads `0x40001100`.

---

## 3. Microarchitecture

### 3.1 The pipeline

Five stages: **IF · ID · EX · MEM · WB**, single-issue, in-order, with no
speculation past a resolved branch and no out-of-order completion.

```
        ┌────┐   ┌────┐   ┌────────────────────────┐   ┌─────┐   ┌────┐
 PC ───▶│ IF │──▶│ ID │──▶│           EX           │──▶│ MEM │──▶│ WB │──▶ regfile
        └────┘   └────┘   └────────────────────────┘   └─────┘   └────┘
           ▲        ▲      ALU · addr adder · branch       │         │
           │        │      bitmanip · muldiv · CSR         │         │
           │        └───── forwarding (precomputed in ID) ─┴─────────┘
           │
        BTB + 2-bit counters + RAS (looked up one address AHEAD of the fetch)
```

**The memory timing is the load-bearing structural decision of the whole design,
and it was made at A4.** `rvntt_ram` is a true dual-port array that registers each
port's address, so **its output register *is* a pipeline register**:

- Port A's address comes from the **PC register**, so an instruction fetched at
  cycle *T* arrives in ID at *T+1*. There is no separate IF/ID instruction
  register in `rvntt_core` — the RAM's output register is it.
- Port B's address comes from the **combinational** address adder in EX, not from
  the EX/MEM pipeline register, which is what makes load data available in MEM.

Both facts have consequences that surface much later and are easy to get wrong:
a stall cannot simply hold `pc_q` and `if_id_q`, because the RAM's output register
has *already* been loaded with the next word; a 32-bit hold register and a mux are
required (A7). And simulating a *smaller* memory than the board has causes the
stack — which the linker places at the top of the 128 KB array — to alias straight
onto the program, because `rvntt_ram` drops high address bits by design rather
than faulting (A12).

### 3.2 Data hazards and forwarding

Three dependency distances, handled in three different places, and the boundaries
between them are deliberate:

| Distance | Mechanism | File |
|---|---|---|
| 1 (EX/MEM → EX) | forwarding mux | `rvntt_forward.sv` |
| 2 (MEM/WB → EX) | forwarding mux | `rvntt_forward.sv` |
| 3 (WB → ID) | register-file **write-through** | `rvntt_regfile.sv` |

Write-through is a *correctness simplification*, not an optimisation: it deletes
the whole WB→ID forwarding case before that case can be forgotten. The mutation
that deletes write-through leaves the forwarding unit provably correct and the
pipeline broken, which is the clearest possible statement of where the boundary
sits.

**A load in MEM is deliberately not a forwarding source**, even though its data is
available by then. Forwarding it would put BRAM → sign-extend → forwarding mux →
ALU → BRAM address inside one cycle, which is the worst path in the design.
A7's load-use interlock exists to avoid that path, not because the data is
missing — the interlock and the `FWD_MEM` exclusion are two halves of one
decision and neither is correct alone. One stall cycle suffices *because of this
pipeline's memory timing*, not because of the textbook: after one stall the load
is in WB, and `FWD_WB` does carry load data.

**Since A26 the forwarding *decision* is precomputed in ID** and carried in the
ID/EX register, taking the comparator chain off the EX critical path. The select
**decays** `MEM → WB → REG`, one step per stalled cycle. That decay is not
cosmetic: during a multi-cycle EX stall the producers drain out from under the
consumer, and a held `FWD_MEM` would read a **bubble — zero** — which is worse
than the register file's stale copy. §14.2 of Part II tells the story of how that
was found.

### 3.3 Control hazards and branch prediction

Branches and jumps resolve in **EX**. A taken transfer or a misprediction
redirects the PC and squashes the two younger instructions in flight — one in ID
and one whose fetch is already in the RAM's output register.

The predictor is specified in [`docs/a19-bpred-spec.md`](a19-bpred-spec.md) and
**implemented twice**: once in `rtl/core/rvntt_bpred.sv` and once in
`model/bpred.py`. Where the two disagree, the document decides which is wrong.

| Parameter | Value | Why this value |
|---|---|---|
| BTB entries | **256**, direct-mapped, tagged | measured: 64 entries still mispredicts 40 027 of Dhrystone's 240 005 transfers, because `pc[7:2]` aliases hot pairs. XOR-folding the index was tried and rejected — it recovers a third of Dhrystone's loss and costs CoreMark more than it gains. |
| Direction | 2-bit bimodal, **folded into the BTB entry** | a separate PHT needed a 256-cycle reset sweep; folding it costs 0.3% on Dhrystone and leaves the valid bits as the only reset in the structure |
| Return-address stack | **8 entries** | measured: 4/8/16/32 give 14 040 mispredicts each — identical |
| Lookup | in **IF**, one address *ahead* of the fetch | a correctly predicted taken transfer costs **zero** bubbles |
| Update | at **EX resolve only** | makes the predictor a pure function of the retired transfer stream, which is what lets `model/bpred.py` reproduce it |
| After a redirect | `SUPPRESS_AFTER_REDIRECT` — no prediction | the predictor was looking elsewhere during the redirect cycle; costs Dhrystone 4002 cycles (0.33%) to keep the entire EX datapath out of the fetch path |
| Visibility gap | 4 fetch cycles | an update in EX cannot reach a lookup that has already happened, so a three-instruction loop mispredicts on alternate iterations — modelled, not designed away |

**The lookup reads only registered sources.** The obvious address to look up with
is `pc_next`, which contains `ex_redirect_target` — the ALU's own output — and
indexing a 256-entry RAM with it put the forwarding mux, the full ALU carry chain
and the array read in one cycle: **16.058 ns and 24 logic levels** against A17's
11.562. Registering the *prediction*, which the modification document prescribed,
was nowhere near sufficient.

Since A26 the predictor **holds** its registered answer under a front stall rather
than re-looking it up. This is an identity, not an approximation: every piece of
predictor state is written under `upd_valid` alone, and `upd_valid` is provably
false during a front stall — proved at BMC depth 14 by
`a_no_bp_update_under_front_stall`. It was the **largest single Fmax contribution
in the entire project** (+2.293 ns) and it is a lever the modification document
does not list.

### 3.4 Multi-cycle execution

`rvntt_muldiv.sv` presents a generic **`req`/`done` handshake with the latency a
property of the unit**, not of the core. The core's side of it is three lines.
This mechanism was built *before* the instructions that use it, deliberately:
plan §8 I1 needs a multi-cycle EX unit for the `Xkntt` Tier-1 butterfly, and
building it twice is how you get a hazard bug. `M` is its first user because `M`
arrives with `rv32um`, RISCOF and riscv-formal as external references, and a
custom extension arrives with none.

| Operation | EX occupancy | Structure |
|---|---:|---|
| `mul` / `mulh` / `mulhsu` / `mulhu` | **2 cycles** | one 33×33 signed multiplier on **4 DSP48E1** |
| `div` / `divu` / `rem` / `remu` | **34 cycles** | radix-2 restoring, **data-independent by construction** |

The multiplier's product-pipeline depth is **derived** from the latency
(`MUL_PIPE = MUL_CYCLES − 2`) rather than written twice, so the two cannot drift.
At `MUL_CYCLES = 2` there *is* no product register.

Two stalls exist and they do opposite things to the same register: `id_stall`
(A7) has the consumer in ID and bubbles ID/EX; `ex_stall` (A14) has the producer
in EX and *holds* it, bubbling EX/MEM instead. **They are provably disjoint** —
`a_stalls_are_disjoint`, proved by every riscv-formal check at depth 14, and shown
non-vacuous by asserting each operand unreachable and watching twelve checks fail.

The divider's data independence is genuine but is **deliberately not filed under
Zkt**: the ratified extension excludes `div`/`rem` outright, on the grounds that
"cryptographers typically assume division to be variable-time". Claiming it as
Zkt compliance would be claiming credit under the wrong heading.

### 3.5 The bit-manipulation unit

34 instructions in `rvntt_bitmanip.sv`, joined at **`ex_result`** rather than
folded into the ALU. **This is the step's central decision and it is measured
rather than stylistic.** The post-route critical path at the time put the ALU's
operation-select mux at a third of the total, with `ex_alu_y` feeding
`ex_jump_target` and the mispredict comparison. Folding 34 instructions in would
take `alu_op_e` from four bits to six and add roughly two LUT levels to a path
with 0.003 ns of slack, in the round whose *other* goal was to shorten it.
`ex_result` terminates at a pipeline register and is not on that path.
`rvntt_alu.sv` is untouched, including its formal block.

---

## 4. Privileged architecture

### 4.1 The trap invariant

**Every trap resolves in EX.** This is not an accident of the exception set — the
misaligned-address check was deliberately placed in EX, where the address is
computed, rather than in MEM where the access lands, in order to keep it true. It
buys three things:

1. Nothing older than EX is ever squashed.
2. A faulting store is suppressed before the RAM latches its address.
3. A faulting instruction never retires, which keeps the commit log
   line-for-line comparable with Spike — Spike prints no commit line at all for a
   trapping instruction.

`minstret` is incremented in **EX, not WB**, and this is the least obvious
decision in the CSR block. A CSR access executes in EX and must report the
instructions retired *before* it; counting at WB leaves its two predecessors
uncounted at that instant. Adding them back from the pipeline registers is exact
— and wrong the first time software *writes* the counter, because the write
already accounts for everything ahead of it and the correction double-counts.
`riscv-tests`' `instret_overflow` puts it in one line: `csrwi minstret, 0; csrr
a0, minstret` must read 0, and the in-flight version reads 2. **Nothing written
alongside this core had questioned the placement**, which is the whole argument
for a suite the project did not write.

### 4.2 CSRs

23 named CSRs plus the counter files. Machine mode only; there is no U-mode
privilege level, only the read-only user *shadows* of the counters.

| Group | CSRs |
|---|---|
| Identification | `mvendorid` `marchid` `mimpid` `mhartid` `misa` |
| Status / trap | `mstatus` `mtvec` `mepc` `mcause` `mtval` `mscratch` `mie` `mip` |
| Counters | `mcycle(h)` `minstret(h)` `mhpmcounter3–8(h)`, user shadows `cycle` `instret` `hpmcounter3–31` |
| Counter control | `mcountinhibit`, `mhpmevent3–8` (WARL over 0…6) |
| Entropy | **`seed` (0x015)** |

`mhpmcounter9–31` and `mhpmevent9–31` are **decoded, read as zero, and do not
trap** — which is a different thing from being undecoded. Software probes for how
many counters exist by reading them, and a decoder that omits them turns that
probe into an illegal instruction.

`satp`, the PMP registers, `medeleg`, `mideleg` and `mnstatus` are *deliberately
absent*, so accessing one traps. That is the case `riscv-tests`' p-environment is
written for.

### 4.3 Hardware performance counters

Six 64-bit counters, any of which can be routed to any of six events. The
numbering is this core's — the specification leaves `mhpmevent`'s encoding
entirely implementation-defined.

| # | Event | Predicate |
|---|---|---|
| 0 | none — **the reset value** | — |
| 1 | load-use interlock cycles | `id_stall && !ex_stall` |
| 2 | multi-cycle EX stall cycles | `ex_stall` |
| 3 | fetch redirects, all causes | `ex_redirect` |
| 4 | redirects caused by a misprediction | `!ex_stall && ex_mispredict` |
| 5 | control transfers that hit in the BTB | `ex_bp_upd && id_ex_q.pred_hit` |
| 6 | taken control transfers retired | `ex_bp_upd && ex_ctrl_xfer` |

These were validated against an **independently written Verilator observer** (A18)
to the count, on both benchmarks, across the whole ISA. As of A28 the pairing is
**retired**: the hardware counters are the primary source and the simulation
instrument is the cross-check. The instrument keeps two jobs the counters cannot
do — it closes the cycle identity at residual *exactly zero*, which software
reading its own counters structurally cannot, and it runs without a board.

---

## 5. Cryptographic extensions

### 5.1 `Zkr` — the entropy source

`seed` at CSR `0x015`, with the mandated access rules: it is **write-only in the
architectural sense** — a read that does not also write traps — and each
successful access *consumes* a 16-bit entropy sample. The state machine is
`BIST → WAIT → ES16 → DEAD`, with `DEAD` latching.

The noise source is a **ring oscillator**: three inverter chains of length 13, 19
and 33, sampled and XOR-combined, followed by the two health tests SP 800-90B
makes mandatory.

> ### ⚠ The entropy source is UNCERTIFIED
>
> **No SP 800-90B statistical validation campaign has been run on this source.**
> `ES16`'s specified meaning is entropy meeting SP 800-90B, and this
> implementation does not establish that. There is **no cryptographic
> conditioning**. **H = 1 bit/sample is an assumption**, not a measurement — and
> it is the assumption both health-test cutoffs are derived from. No output of
> the physical ring has ever been captured. What exists is a noise source of a
> standard construction plus the two mandatory health tests, shown by fault
> injection to detect stuck, biased and periodic sources. What does not exist is
> entropy-rate estimation, restart tests, or the IID/non-IID track — none of
> which is a simulation exercise.
>
> The full statement is in [`docs/a29-zkr.md`](a29-zkr.md).

**The health-test cutoffs are derived, and the number everyone quotes is wrong for
this configuration.** `tb/unit/test_entropy_health.py` recomputes both from
SP 800-90B's own definitions and fails if the RTL disagrees:

| Test | Parameter | Value |
|---|---|---:|
| Repetition count | C = 1 + ⌈−log₂α / H⌉ | **21** |
| Adaptive proportion | window W | **1024** |
| Adaptive proportion | cutoff C | **589** |
| Both | α | **2⁻²⁰** |
| Both | assumed H | **1 bit/sample** |

The widely-cited **821** belongs to a different assumed entropy rate. Using it
would have made the test four sigma looser while looking authoritative.

**The mandated adaptive test has a blind spot, and it is precisely the one that
matters for a ring oscillator.** SP 800-90B designates the *first* sample of each
window as the value to count. A periodic source whose period divides the window
has a fixed phase — so if that sample happens to be the minority value, the test
**never fires. Not eventually: never.** That is the characteristic failure mode of
a ring oscillator injection-locking to its sampling clock. This implementation
counts **both** values, which is strictly stronger, and the `entropy_health`
suite's periodic 7-in-8 scenario is the regression for it. Before that scenario
existed, every bad source was catchable by the repetition test alone, and deleting
the adaptive test changed no verdict.

**α = 2⁻²⁰ is per *sample*, and combined with `DEAD`'s latching it nearly shipped
an RNG that bricks itself.** A correctly functioning source trips the repetition
test roughly once per 2²⁰ samples *by construction*; free-running at 96.246 MHz
that is **eleven milliseconds**. This was measured, not predicted — the ideal
source went `DEAD` at 42 000 samples on a run of 23, a 6% event, and the test had
asserted survival over what was a 4% coin flip. The sampler is therefore **gated
on needing to refill**, which moves the expected trip to **65 536 seed reads**.
The limitation cannot be removed without changing α or giving up `DEAD`'s
latching, so it is stated as a number rather than hidden.

**The ring fought the tools in three separate ways**, each failing differently:

| Attribute | Stops the tools from |
|---|---|
| `DONT_TOUCH` + `KEEP_HIERARCHY` | **deleting** the loop |
| `set_disable_timing` | **timing** the loop |
| `ALLOW_COMBINATORIAL_LOOPS` | the **bitgen DRC refusing** it |

Without the third, the design **routed at WNS 0.000 and then produced no
bitstream** — which is the right failure mode, but only if you know to expect it.
The acknowledgement is set **only on the ring's own 66 nets**, never design-wide,
because a blanket one would suppress the same DRC for an accidental loop
elsewhere. Verilator's version of the same objection is `DIDNOTCONVERGE`.

**The KAT path cannot reach the CSR, structurally, and it is checked by
disassembly.** `make ENTROPY=1` links `randombytes_seed.c`; the KAT build does not
compile it at all. `kat_no_seed` requires **0** accesses to CSR `0x015` in the KAT
binary and **at least one** in the entropy binary — the second half is what stops
the first from being satisfied by a build that does not exist.

### 5.2 `Zkt` — data-independent execution latency

The claim is made **only with both of its boundaries**, quoted verbatim from the
scoping document:

> 1. `Zkt` is a statement about **listed instructions' latency**, and **not**
>    about control flow.
> 2. **This core's branch predictor introduces data-dependent timing that `Zkt`
>    does not cover and this core does not remove.** The BTB, its counters and the
>    RAS are architecturally invisible state that persists across whatever runs on
>    the machine, with no flush and no partition.

**A `Zkt` claim that omits the second boundary is a security falsehood, which is
worse than no claim.**

**37 instructions are claimed.** The remainder of the extension's list is reported
as *not implemented*, not as passing: `clmul`/`clmulh` (Zbc), `xperm4`/`xperm8`
(Zbkx), the Zkn/Zks crypto instructions, and `C`. `tb/formal/run_zkt.py --list`
generates both tables from the same place that proves the claim.

The claim rests on two proofs of different kinds:

- **Claim A** — `a_zkt_only_muldiv_stalls`: an assertion riscv-formal proves on
  all 77 checks. It is stated about `ex_stall` rather than instruction by
  instruction, so that a future multi-cycle unit breaks it at depth 14 rather
  than quietly breaking `Zkt`.
- **Claim B** — proved **structurally, by cone of influence**, and that is better
  than a BMC here. `run_zkt.py` computes the transitive fan-in of `done` with
  Yosys and checks the operand ports are absent: **exact rather than
  depth-bounded**, total over operands, and it fails *by naming the operand*.
  The vacuity guard is that the **opcode must be present** in the cone — an
  absence is trivially satisfied by looking at nothing.

**Both claims were observed to fail** under injected faults. The multiplier was
given the data-dependent early-out a real design would be tempted by (return in
one cycle when either operand is zero) and the cone check reported 2 operand bits;
Claim A was broken by giving the bit-manipulation unit a stall.

---

## 6. The SoC

`rvntt_soc_top` is the core plus the minimum needed to run and observe a program
on the board.

| Block | Detail |
|---|---|
| Memory | **128 KB true dual-port BRAM**, 32 RAMB36E1 tiles. Port A fetch, port B load/store. **One array, not two** — one array means one image, so the ELF that runs on Spike, in cosimulation and in the bitstream is the same bytes. |
| Clock | MMCM, ratio generated into `fpga/generated/soc_clk.svh`; `build_soc.tcl` reads back the period Vivado actually derived, because if intent and implementation disagree every number is wrong by the same unknown factor. |
| UART | 115 200 8N1 TX + RX, memory-mapped |
| GPIO | 4 LEDs out, 4 switches + 4 buttons in |
| Debug | RGB LED: blue = alive, green = 1 Hz blink, **red = an `Xkntt` instruction retired** |

**Memory map** (base `0x40000000`):

| Offset | Register | Access |
|---|---|---|
| `0x00` | `UART_TX` | W — byte to transmit |
| `0x04` | `UART_STAT` | R — `{rx_overrun, rx_valid, tx_ready}` |
| `0x08` | `UART_RX` | R — last received byte, **non-destructive** |
| `0x0C` | `GPIO_OUT` | RW — `led[3:0]` |
| `0x10` | `GPIO_IN` | R — `{btn[3:0], sw[3:0]}` |

**MMIO reads have no side effects, and that is a hard constraint rather than a
convenience.** `dmem_addr` is the raw combinational ALU result of whatever is in
EX, driven every cycle for every instruction, and the core signals a read as
`be == 0` — which is also what a non-memory instruction presents. An `add` whose
result happens to equal `0x40000008` is indistinguishable from a load of
`UART_RX`. A read-to-pop FIFO would therefore be emptied by *arithmetic*,
intermittently. Everything is consumed by an explicit write instead.

---

## 7. Physical implementation

### 7.1 Fmax and how it is measured

**Fmax = 96.246 MHz.** The method is the plan's and only the plan's: constrain the
target period, run synthesis and implementation to routed, read **post-route WNS**,
and binary-search the constraint. **`1/(T − WNS)` from a passing run is reported
nowhere**, because the router optimises to the constraint and stops — the slack it
leaves measures *when it stopped*, not how fast the design could go.

| Constraint | Period | WNS | WHS | Verdict | Runtime |
|---:|---:|---:|---:|---|---:|
| 84.998 MHz | 11.765 ns | +0.190 | +0.048 | PASS | 784 s |
| 104.998 MHz | 9.524 ns | −0.423 | +0.041 | fail | 1784 s |
| 95.003 MHz | 10.526 ns | +0.023 | +0.016 | PASS | 449 s |
| 100.000 MHz | 10.000 ns | −0.210 | +0.047 | fail | 734 s |
| 97.504 MHz | 10.256 ns | −0.152 | +0.027 | fail | 755 s |
| **96.246 MHz** | **10.390 ns** | **+0.010** | **+0.052** | **PASS — Fmax** | 686 s |

**96.246 MHz is the fastest constraint observed to pass. It is not a boundary.**
The A26 search was *monotonic*, unlike A19's, but the implied path delay across
the six runs spans **9.947–11.575 ns — a 1.63 ns spread**, wider than A12's ±0.4
and A19's 0.9. Vivado 2025.2, `xc7a100tcsg324-1`, **-1** speed grade,
`explore_postroute` strategy, **no floorplan**.

### 7.2 Area

| Resource | Used | Available | Util% |
|---|---:|---:|---:|
| LUT primitives (project convention) | **5502** | 63 400 | — |
| Slice LUTs (`report_utilization`) | 5530 | 63 400 | **8.72%** |
| — as logic | 5016 | 63 400 | 7.91% |
| — as distributed RAM | 514 | 19 000 | 2.71% |
| Slice registers | **1987** | 126 800 | **1.57%** |
| Slices occupied | 1737 | 15 850 | 10.96% |
| Block RAM tiles (RAMB36E1) | **32** | 135 | **23.70%** |
| DSP48E1 | **4** | 240 | **1.67%** |
| F7 / F8 muxes | 314 / 32 | 31 700 / 15 850 | 0.99% / 0.20% |

The two LUT numbers differ because they count different things: the project's
`build_soc.tcl` counts LUT *primitive cells*, while `report_utilization` counts
Slice LUTs including memory LUTs. Both are quoted rather than reconciled away.

**The design uses 8.7% of the part.** The 91% that is free is the argument that
Track B's coprocessor is not area-constrained on this device — plan §B6's worst
case, including P=16, was estimated at ~65 of 240 DSPs.

### 7.3 The critical path — and it has moved

The shipping A29/A30 bitstream at 96.246 MHz closes at **WNS 0.000 ns**, and its
worst path is **not** the one every previous step found:

| | Value |
|---|---|
| Source | `u_core/id_ex_q_reg[insn][30]/C` |
| Destination | **`u_core/u_csr/hpm_watch_q_reg[3][1]/CE`** |
| Data path delay | **9.808 ns** — logic 1.572 ns (16.0%), **route 8.236 ns (84.0%)** |
| Logic levels | 9 (LUT2×1, LUT3×1, LUT4×1, LUT6×6) |

Read hop by hop, the path is: the ID/EX instruction register → the CSR address
decode → `ex_csr_rdata` → `ex_csr_wdata` → the `mhpmevent` write decode → the
**HPM event-watch mask enable**. In plain terms: *a CSR instruction's own
read-modify-write feeding the performance counters' event-selection logic.*

This matters for two reasons. First, it is the **third** time A20's counters have
appeared on or near the critical path — A23 found their 6:1 event mux sitting
after `ex_redirect` and fixed it twice — so it is a structural tendency of the
design rather than a coincidence. Second, and more usefully, **the core's own
datapath is no longer the limit**: A26 shortened the forwarding, jump-target and
predictor paths until a piece of *instrumentation* became the worst path. That is
a concrete, named lever for whoever pushes past 96 MHz, and the roadmap document
treats it as one.

For contrast, the A26 Fmax netlist at the same 96.246 MHz constraint closed on a
*different* path — `u_ram` BRAM output → `id_ex_q[pred_target]`, 10.358 ns, 8
levels, 33% logic / 67% route — the instruction-fetch-to-predictor path. Two
netlists of nearly the same design at the same constraint have different worst
paths, which is itself a measurement worth remembering.

### 7.4 Floorplanning — measured and rejected

A27 tried two pblocks and **adopted neither**. Both were slower: core+memory in
one clock-region row by **0.651 ns**, core alone by **0.189 ns**.

The premise was half wrong. `post_route_clock_util.rpt` says Vivado had **already**
concentrated the design into a contiguous 2×2 clock-region block with four regions
empty. That the memory-free pblock is the *better* of the two says the memory's
forced placement is the more expensive half — Vivado's own BRAM choice beat being
pushed into the row that already held 29 of the 32 tiles.

**Route delay at low utilisation is not automatically evidence of a spread
placement.** It can be a path whose hops are set by where the site *types* are.
`SOC_PBLOCK=<file>` selects one, and `build_soc.tcl` prints `SOC_PBLOCK_CELLS` so
an empty pblock cannot pass as a floorplan.

---

## 8. Measured performance

### 8.1 Headline figures, on the board

Three separate JTAG programming passes, five report blocks each, **all 42 integer
counters identical across all fifteen blocks** — not within tolerance, byte for
byte, with exact equality enforced by the parser.

| | Dhrystone | CoreMark |
|---|---:|---:|
| Configuration | 2 000 000 runs | 3500 iterations, 2K performance |
| Cycles | 1 216 000 065 | 1 032 567 160 |
| Instructions retired | 1 062 000 032 | 897 147 464 |
| **IPC** | **0.8734** | **0.8689** |
| **Rate/MHz** | **0.9361 DMIPS/MHz** | **3.3896 CoreMark/MHz** |
| **Absolute** | **90.096 DMIPS** | **326.24 CoreMark** |
| Absolute, secondary | 158 299 Dhrystones/s | — |

Both rate-per-MHz figures are **exact integer ratios of `mcycle` counts** and do
not depend on the clock, so neither inherits the Fmax uncertainty of §7.1. The
absolute figures do, and are quoted at 96.246 MHz.

**Two methodology choices are load-bearing and easy to get wrong later.** Every
rate is computed in Python by the capture parser, never on the target, because
Dhrystone's own `Microseconds` and `Dhrystones_Per_Second` **overflow 32-bit
`long`** at these run counts and print figures that are wrong in an entirely
plausible way. And `-fwrapv` — which would define that overflow away without
touching the source — is deliberately **not** used, because it was *measured* to
cost 1.4% inside the timed loop (779.0 → 790.1 cycles per run). Depressing the
score to tidy up two discarded lines is the wrong trade.

`CoreMark`'s iteration count is 3500, not A23/A25's 2500, because at 96.246 MHz a
2500-iteration run finishes in 7.91 s — under CoreMark's own 10-second reporting
minimum, which the parser enforces and would have refused. **Raw cycle counts are
therefore not comparable across those steps; `CoreMark/MHz` and IPC are.**

### 8.2 Where the cycles go

The identity is `cycles = retired + load-use stalls + multi-cycle EX stalls + 2 ×
redirects`, and on hardware it closes to the snapshot code's own footprint.

| | Dhrystone | % | CoreMark | % |
|---|---:|---:|---:|---:|
| Retired instructions | 1 062 000 032 | 87.34% | 897 147 464 | 86.89% |
| Load-use interlock | 52 000 006 | 4.28% | 72 524 286 | 7.02% |
| Multi-cycle EX stall | 66 000 000 | 5.43% | 32 886 000 | 3.18% |
| Flush (2 × redirects) | 36 000 092 | 2.96% | 30 009 458 | 2.91% |
| **Identity residual** | **−40** | | **−48** | |

**The residual is not slop.** −40 is *exactly* the Dhrystone snapshot code's
independently measured footprint, 6 + 0 + 2×17. Residual exactly zero on hardware
is **structurally unachievable** — A18's Verilator instrument samples every
counter at one instant and software cannot, because each counter is read by its
own instruction and the windows nest. CoreMark's residual was −168 until its glue
was made to nest the reads in the same order Dhrystone's does, **and that
difference between two regions was the finding.** Residual exactly zero remains
the standard for the simulation instrument, where it still holds.

| Predictor counters | Dhrystone | CoreMark |
|---|---:|---:|
| Redirects (all causes) | 18 000 046 | 15 004 729 |
| Mispredicts | 18 000 046 | 15 004 729 |
| Mispredicts per 1000 instructions | 16.95 | 16.72 |
| BTB hits on resolved transfers | 171 999 989 | 185 913 253 |
| Taken transfers retired | 182 000 027 | 129 966 213 |

**`redirects − mispredicts = 1` on both benchmarks, measured rather than
assumed.** A19's second closure said the trap-and-MRET term was negligible; the
board now says it is *the single `ECALL` that ends the program*.

### 8.3 The dual baselines — the numbers the project actually depends on

These are not benchmark scores. They are the denominators plan §10 M2 and M3 will
divide the coprocessor's result by, and they are measured on one core at one clock
within microseconds of each other.

**The ML-KEM NTT, `rv32i` vs `rv32im`** — the same reference C, compiled twice
and linked into one image. All 256 output coefficients are identical between the
two builds, and the parser refuses the capture otherwise: *two builds measured
against each other are only a comparison if they compute the same thing.*

| | `rv32i` | `rv32im` (shipped ISA) | ratio |
|---|---:|---:|---:|
| Cycles | 165 752 | **28 570** | **5.802×** |
| Instructions | 148 655 | 23 805 | 6.245× |

The instruction ratio matches the 6.247× measured independently on Spike. Plan
§B6's "15 000–30 000 cycles" estimate for the software NTT baseline lands **inside**
the `rv32im` band and 5–10× outside the `rv32i` one — which is the contradiction
that caused `M` to be added to the scope in the first place.

**SHAKE128, `rv32im` vs `rv32im`+B+Zbkb** — the same `fips202.c` compiled twice,
both with `M`, the only variable being B. This is plan §10 M3's *other* term, the
half a hardware NTT never touches, and it is the number the B round exists for.

| | `rv32im` | `rv32imb` | ratio |
|---|---:|---:|---:|
| Cycles | 373 407 | **312 402** | **1.1953×** |
| Instructions | 346 629 | 290 114 | 1.1948× |

Digests are byte-identical. **The compiler's use of B was verified rather than
assumed**: the `rv32imzb` copy of `KeccakF1600_StatePermute` contains 100 B
instructions and the `rv32im` copy contains 0, established by following the call
graph after an address-range attribution got it backwards — link order is not
declaration order.

### 8.4 The progression, A13 → A30

| Step | What changed | Fmax | DMIPS/MHz | CM/MHz | IPC (D) | IPC (C) | LUTs | FFs |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A12/A13 | RV32I baseline | 70.131 | 0.7306 | 0.9607 | 0.7227 | 0.6930 | 2126 | 913 |
| A16 | **+ M** | 73.752 | 0.7325 | 2.4309 | 0.6860 | 0.7006 | 2613 | 1148 |
| A17 | + address adder | **86.490** | *unchanged* | *unchanged* | *unchanged* | *unchanged* | — | — |
| A19 | + branch predictor | 77.501 | 0.9346 | 2.8933 | 0.8752 | 0.8338 | 3471 | 1530 |
| A23 | + Zihpm, B, Zbkb, Zicond | 74.577 | 0.9361 | 3.1866 | 0.8734 | 0.8168 | 5770 | 2027 |
| A25 | MUL 4 → 3 cycles | 70.000\* | 0.9361 | 3.2850 | 0.8734 | 0.8420 | 5722 | 1963 |
| A28 | + A26 levers, MUL → 2 | **96.246** | 0.9361 | 3.3896 | 0.8734 | 0.8689 | 5793 | 1926 |
| **A29/A30** | **+ Zkr, Zkt** | **96.246** | **0.9361** | **3.3896** | **0.8734** | **0.8689** | **5502** | **1987** |

\* A25's netlist missed A23's clock by 0.423 ns on a path the multiplier is not on;
its board figures are taken at 70.000 MHz and its `/MHz` ratios are unaffected.

Two features of this table are worth naming, because both are counter-intuitive
and both are real:

**Fmax went *up* by 5.2% at A16 after adding a multiplier and a divider.** That is
the ±0.4 ns placement spread arriving in the direction nobody double-checks. The
critical path was the same one A12 had, and neither new unit was on it. **A
favourable Fmax movement across a design change is exactly as much a measurement
of a different design as an unfavourable one**, and far less likely to be
questioned.

**A19 gave back 10.4% of A17's clock on purpose**, and it was the right trade:
absolute Dhrystone still rose 63.35 → 72.43 DMIPS on the slower part, and both IPC
figures landed inside the plan's expected bands for the first time.

### 8.5 Three results that came out against prediction

**`M` was predicted to improve both benchmarks. Only one moved.** CoreMark/MHz
went ×2.53; Dhrystone moved **+0.3%** — it retires 5.3% fewer instructions and pays
almost all of it back in four-cycle `MUL` stalls, so its **IPC falls**, 0.7227 →
0.6860. `M`'s benefit is concentrated in multiply-bound code, which is ML-KEM's
polynomial arithmetic and *not* Keccak.

**`Zicond` is worth approximately nothing on either benchmark, and separating it
from B took a third build.** Comparing `rv32im` against `rv32im`+B+Zicond shows
Dhrystone losing 2000 conditional branches and 3999 mispredicts *together*, which
reads as Zicond removing a fifth of the mispredicts. **It did not.** Zbb's
`min`/`max` are themselves if-conversion instructions and Zba's `sh*add` replace
multiplies, so a two-point comparison cannot tell them apart. A third build — B
*without* Zicond, which exists for no other purpose — says **B removed all 3999 and
Zicond removed none.**

**Dhrystone gains exactly zero from the shorter multiply, and B is why.** Its
multi-cycle EX stall counter is 66 000 000 over 2 000 000 runs — exactly 33 per
run: **one divide and no multiplies at all**, because at `-march=rv32imb` the
compiler strength-reduces Dhrystone's multiply into Zba shift-adds. **A21 had
already removed the thing A25 makes cheaper.** The same measurement at
`-march=rv32im` shows a cycle saved per run. **The value of a shorter multiply
depends on which other extensions are enabled — measure it on the image that
ships.**

---

## 9. Verification apparatus

Seven independent mechanisms, each of which catches a class the others cannot.
The regression registers **59 tests**; the current state is **56 PASS · 1 XFAIL ·
2 SKIP · 0 FAIL**.

| Mechanism | Scale | What only it catches |
|---|---|---|
| **Golden models** (`model/`) | 1261 polynomials, per-layer, bit-exact vs. C reference | a *specification* error, before any RTL exists |
| **cocotb unit tests** | ALU 10⁵ triples + 16-value cross; immgen 7 × 10⁵ vectors + walking-ones; decoder **10⁶ words** | a decode-table or datapath disagreement with the spec-derived model |
| **Module formal proofs** (12) | SymbiYosys, depths 8–37 | properties over *all* 2³² inputs, which a 10⁶ sample cannot claim |
| **Lockstep cosimulation vs. Spike** | 1000 random programs at max hazard density | any architectural divergence, on real dynamic instruction mixes |
| **Cycle model** (`tb/cosim/cycle_model.py`) | every cosim program's cycle *span* | a **phantom stall** — which changes no architectural state, so the commit log stays byte-identical and the program is merely slower |
| **External suites** | `riscv-tests` 62, `rv32um` 8/8, **RISCOF 120/120** | what nobody who wrote this core thought to question |
| **riscv-formal** | **77 checks at BMC depth 14** | a datapath self-inconsistency no *program* can reach |
| **Mutation testing** | **90 mutations, all caught as declared** | a test that has quietly stopped testing anything |
| **Hardware** | 3 JTAG passes × 5 report blocks, exact equality | a swapped output pin; anything past the pad |

**Mutation testing is the mechanism that checks the other mechanisms**, and it
has repeatedly found tests miscredited with coverage they did not have. A declared
catcher that fails to catch is reported as `PARTIAL`, not as a pass.

### 9.1 What riscv-formal proves, and what it does not

The 36 RV32I instruction models, the 34 B/Zbkb models, plus `reg`, `pc_fwd`,
`pc_bwd`, `causal`, `liveness` and `unique` all pass at depth 14 (`liveness` at 47).
Depth 14 is counted **from the first retirement**, not from zero: reset is step 0
only and this pipeline is five stages deep.

**Two abstractions are recorded rather than buried:**

`rvntt_muldiv`'s *arithmetic* is abstracted to a free value. A combinational
33×33 multiplier unrolled fourteen times is the canonical hard SAT instance and
sits in the cone of the RVFI outputs whether any check reads it or not — it took
the check set from 40 s **for the whole set** to several hundred seconds **each**.
Its **sequencer is not abstracted**, so every stall, bubble and retirement time
those checks depend on is the real design's. The arithmetic is proved separately
by `formal_muldiv` at depth 37, by `rv32um`, and by cosimulation. riscv-formal's
own `insn_mul*`/`insn_div*` models are deliberately **not** enabled — against the
abstraction they would be vacuous and against the concrete multiplier they do not
converge.

The *predictor's prediction* is abstracted to a free pair of signals, which makes
the proof **stronger**: the claim is that the predictor cannot change what
retires, and `anyseq` proves it for every prediction any predictor could make —
a misaligned target, a prediction of taken on an `addi`, a different wrong answer
every cycle. What it does not prove is anything about the predictor's own logic.

**Not proved at all**: memory consistency (`dmem` and the `bus_*` checks need a
memory model in the wrapper, which would defeat the unconstrained `dmem_rdata` the
rest of the proof depends on); anything about CSRs (`csrw`, `csr_ill` and `ill`
have no model for Zicsr, `ECALL`, `MRET` or `FENCE`); and the `Xkntt` encodings.

### 9.2 What RISCOF's 120/120 covers

| Suite | Result |
|---|---|
| `I` | 38/38 |
| `M` | 8/8 |
| ratified **B** | 29/29 (3 `clmul*` tests are **Zbc**, excluded by name) |
| `Zbkb` | 5/5 |
| `Zicond` | 2/2 |
| hints | 22/22 |
| privilege | 16/16 |
| `pmp` | **excluded by name** — plan §1.5 excludes PMP, and riscof 1.25.3 ignores the `verify` clause those tests use to deselect themselves |

**The compliance suite is not a superset of the local directed tests, and that was
measured.** Breaking `SRA` fails 2 arch-test tests and breaking `BLTU`'s signedness
fails 1 — but **a `JALR` that never clears bit 0 of its target escapes the suite
entirely**: `jalr-01` never computes an odd target, and the two privilege
misalign-jalr tests aim at 2-mod-4 addresses, which bit 0 does not affect. An
official compliance pass would have been green over it. The directed
`sw/tests/a8_control.S` catches it, and so does riscv-formal. **That is the argument
for keeping local tests after the external suite arrives, rather than before it.**

### 9.3 Regression tiers

Every figure below is measured on this machine (8 cores), not estimated. Three
tests are **84%** of the run.

| When | Command | Cost | Drops |
|---|---|---:|---|
| Tight edit loop | `RVNTT_FAST=1 python3 tb/run_regress.py` | **249 s** | the three heavy tests |
| After an RTL change | `RVNTT_NO_MUTATE=1 …` | **793 s** | the mutation set only |
| Step boundary | full run | **1230–1470 s** | nothing |
| Milestone | full + `RVNTT_HW=1 … -k fpga` | +~250 s | nothing |

**The full-run figure is a range on purpose.** Two clean runs of the same tree
measured 1226 s and 1468 s — a 20% spread that did not exist when the mutation set
was serial, because a parallel run's wall clock depends on what else the machine
is doing. `RVNTT_FAST=1` **announces itself in the summary** ("this was the
EDIT-LOOP TIER … it is not a regression result") because the hazard of a tier is
somebody quoting its green line as a regression.

**The two 20-millisecond pre-flights are the model to copy.** `mutation_anchors`
(0.03 s) and `isa_consistency` (0.02 s) each catch a class of failure the
expensive run would reveal much later or not at all: a stale anchor becomes a
`NO-OP` after fourteen minutes, and a narrowed ISA string does not fail — it
**hangs**. Both classes have bitten this project repeatedly (anchors five times,
ISA strings twice). **A cheap check that fails fast reduces total time; it does not
add to it.**

---

## 10. Boundaries — what this core is *not*

Stated explicitly, because an unstated boundary reads as a capability.

| Not present | Note |
|---|---|
| **Any `Xkntt` execution** | the decoder recognises the extension and LD0 lights red if one retires; no stage runs one |
| Interrupts | `mie`/`mip` exist as storage; nothing drives them |
| Caches | the BRAM is single-cycle; there is no hierarchy to miss in |
| PMP, `satp`, virtual memory | excluded by plan §1.5; accessing the CSRs traps |
| `C` (compressed) | out of scope; also excluded from the `Zkt` claim |
| `Zbc`, `Zbkx`, `Zkn`, `Zks` | reported as not-implemented in the `Zkt` table |
| `A` (atomics), `F`/`D` (floating point) | out of scope |
| Flash / non-volatile configuration | the bitstream is loaded over JTAG each time |
| Formal memory-consistency proof | needs a memory model that would defeat the unconstrained `dmem_rdata` |
| Formal CSR proof | riscv-formal's `csrw`/`csr_ill`/`ill` are not enabled |
| SP 800-90B certification | see §5.1 — the source is **uncertified** |
| `Zkt` for control flow | see §5.2 — the predictor's state persists, unflushed and unpartitioned |
| A TIER2 backend, RTL NTT verification, a `make KAT` target | plan C6 is **partially done**, which gates M14 |


---

# Part II — The chronology, A1 → A30

Thirty engineering steps over nine days of work, in six phases. Each step is given
in the same shape: **the machine before**, **what changed and why**, **what went
wrong on the way**, and **the result and what it carried forward**. The steps that
produced a surprise are given more space than the ones that did not, and the
surprises are the reason to read this part.

Three specification documents govern the sequence, and they are strictly additive:
`docs/RISC-V_NTT.txt` (the plan, **never edited**), `docs/RISC-V_NTT_MODS_A.txt`
(A14–A19), and `docs/RISC-V_NTT_MODS_A2.txt` (A20–A30). Where the plan is
factually wrong about *how*, the corrections live in the modification documents
and in `CLAUDE.md` — never by editing the plan.

---

## Phase 0 — Infrastructure (P0.1–P0.5, 2026-08-27 → 08-28)

**Before:** nothing.

**What was built:** the toolchain environment (`toolchain/env.sh`), the regression
runner, Spike built from source, the golden models (C reference KATs, `DUMP_NTT`
per-layer instrumentation, an independent Python NTT), a rebaseable patch series
for the Spike and LLVM forks, and a blinky + UART + BRAM-readback bitstream on the
actual board.

**Two decisions here shaped everything after them.** First, `model/` is a **frozen
contract** validated bit-exactly against the C reference over 1261 polynomials —
*if the RTL disagrees with the model, the RTL is wrong.* Second,
`toolchain/kyber/` must stay **pristine** so the reference's own KATs remain
valid; instrumentation is *generated* into `model/cref/` with the diff kept as a
patch. Neither rule has been broken since.

**The speedbump:** the UART banner repeated **15 times per second** instead of
once. Fixed at P0.5, and it is the first entry in what became a long list of
things that looked right and were not.

**Result:** M0. `make regress` runs; a bitstream is on the board.

---

## Phase 1 — The ISA contract (2026-08-28)

**Before:** an informal description in the plan.

**What was built:** [`docs/isa-spec.md`](isa-spec.md), frozen — every `Xkntt`
encoding hand-derived and verified, the semantics stated, the latency contract
tabulated, and the exception behaviour specified. Then the `.insn` bridge
(`sw/include/xkntt.h`) so the extension can be emitted from C before any compiler
knows about it, and the Spike fork that executes it.

**Two corrections to the plan were found here and are authoritative:**

1. **`kbfgs`'s subtraction order is the negation of what the plan says.** Plan §3
   specifies `montgomery_reduce(z * (t − b))`, i.e. `(a − b)`. The pq-crystals
   reference computes `fqmul(zeta, r[j+len] − r[j])` — that is `(b − a)`.
   Implementing the plan literally yields a **working forward NTT and a silently
   broken inverse**. It was caught by building `invntt` from instruction semantics
   alone and diffing against the golden model:
   `invntt via kbfgs differs at coeff 2: 1369 != -1369`.
2. **`kbmul1` exists, at `funct3=5`**, and the plan assigns nothing there. Without
   it there is no instruction for `c1 = a0·b1 + a1·b0`, and `basemul` cannot be
   performed at all. R-type, not R4 — `c1` needs no zeta, and encoding an unused
   `rs3` would burn a register-file read port.

**Result:** M1, M2 and M3. Spike executes the extension and the full **10 000-vector
ML-KEM keygen KAT passes** on it. The four-way agreement that governs the rest of
the project — RTL decoder, `model/isa/xkntt.py`, Spike, and (eventually) the LLVM
`SchedModel` — is defined here, on this contract.

---

## Phase 2 — The pipeline, A1 → A9 (2026-08-29)

### A1 — The type package and the register file

**Before:** no RTL.

**Changed:** `rv32i_pkg.sv` (opcode/funct constants, the four pipeline-register
structs) and `rvntt_regfile.sv` (32 × 32, **three read ports**).

**Three read ports, from the start.** Plan §1.1 took the R4-type recommendation,
so `kbmul0` and `kmac` read `rs3` as well as `rs1`/`rs2`. Retrofitting a third
port later would mean re-timing ID, so it is there from day one; it costs one more
distributed-RAM copy and does not touch the critical path. **This is a decision
made for a coprocessor that does not yet exist, and it has cost nothing since.**

Write-through is a correctness simplification (see Part I §3.2). There is no reset
port — the array powers up at zero, matching Spike's architectural state at reset
so A5's cosimulation starts aligned.

**Speedbumps — three tool disagreements, each costing a cycle:**
- **Yosys rejects `'{default: '0}`** as an unpacked-array declaration initialiser
  where Verilator accepts it, so the formal flow could not read the file at all.
  The initial-loop form is portable across all three tools.
- **Verilator parses a comment beginning `// Verilator ...` as a pragma** and
  errors with `BADVLTPRAGMA` on ordinary prose. (This trap was walked into again
  at A3, which is some evidence the note earned its place.)
- **A package linted standalone reports every localparam unused**, which is a
  property of linting a library in isolation, not a defect.

`formal_regfile` runs at depth **8**, not the default 20: the deepest property
spans two cycles, and each BMC step adds another symbolic write to a 32×32 memory.
Depth 8 proves in ~3 s; depth 20 was still grinding at step 13 after eight minutes
with nothing left to find.

**Result:** 7/7 mutations caught by both the simulation testbench and the formal
proof. The Vivado elaboration gate was itself fault-injected with a deliberately
broken module and reported `ELAB_FAIL`.

### A2 — ALU and immediate generation

**Before:** storage but no computation.

**Changed:** `rvntt_alu.sv` and `rvntt_immgen.sv`, both purely combinational, plus
`model/rv32i_ref.py` as the golden reference — **written from the ISA specification
rather than transcribed from the RTL**, because a model derived from the design
agrees with it by construction and proves nothing.

**Random alone reaches a 32-bit datapath's corners badly**, so it is not the only
test. The ALU is crossed over 16 corner values — `INT_MIN`, because
`−INT_MIN == INT_MIN`, and the pair either side of the signed/unsigned divide
where `SLT` and `SLTU` must disagree — and swept over every shift amount 0…31 with
junk in `b[31:5]`, which is what actually catches a shifter fed the whole operand
instead of `b[4:0]`.

For `immgen` the strong test is **walking ones and walking zeros over all 32
instruction bits**, which is *complete* for detecting any mis-routed wire: immgen
is a pure wire permutation plus sign extension, so a bit connected to the wrong
place changes exactly one of those 64 vectors per format, and the reported XOR
names the misplaced output bit.

**Speedbump — a formal proof that was weaker than it looked.** Fifteen mutations
were run against **both** mechanisms. Fourteen were caught by both. The fifteenth
— replacing `IMM_S`'s `insn[11:7]` with `insn[19:15]`, taking a store offset's low
five bits from `rs1`'s field instead of `rd`'s — **passed the formal proof
cleanly**, because the only `IMM_S` property was about sign extension above bit 11
and the mutation left that intact. cocotb caught it, so the RTL was never at risk,
but the proof was decoration on that format. Every source bit is now pinned.

**Speedbump — a vacuous drift check.** `check_pkg_agreement()` parses `rv32i_pkg.sv`
and compares the duplicated enum encodings, because *a duplicated constant nobody
checks is how you end up testing the wrong operation and passing*. Its first
version was vacuous in a subtle way: a non-greedy `.*?` with `re.S` let the match
start at the *first* enum in the file and run to the requested one, reporting
`opcode_e`'s width as `alu_op_e`'s. It is now fault-injected against four drift
modes.

**Speedbump — a third tool disagreement.** Yosys rejects `import` outright, in both
the port-list and module-body forms. Probing the alternatives found that
**fully-qualified references with no import at all** is the only form Verilator,
Yosys and Vivado all accept, *and* it keeps the port's enum type rather than
degrading it to a plain vector. Core RTL is written that way from here on.

**Result:** 100 000 ALU triples and 700 000 immgen vectors pass. cocotb sustains
~110 k vectors/second against Verilator, so the required scale costs seconds and
there was no need to trade coverage for runtime.

### A3 — The instruction decoder

**Before:** no legality checking anywhere.

**Changed:** `rvntt_decode.sv`, compared against `model/rv32i_ref.py::decode` over
**10⁶ random words with zero mismatches**.

**Three legality rules that are genuinely different from each other:**

1. **`Xkntt` reserved fields are strict** — a nonzero field an instruction does not
   use is an illegal instruction.
2. **Base RV32I `FENCE` fields are the opposite** — the ISA says base
   implementations *shall ignore* them, so copying the strict rule onto `FENCE`
   would diverge from Spike, which is A5's reference.
3. Anything outside the declared ISA string is illegal.

**The custom opcodes are not decoded twice.** The Python side *delegates* custom-0
and custom-1 to `model/isa/xkntt.py` — the frozen contract — rather than
reimplementing the rules, because a second hand-written copy could agree with the
RTL while both drifted away from the contract.

**What the 10⁶-word run found:** an illegal instruction originally produced a
*partial* reset bundle. Several decode arms set `result_sel` or `imm_fmt` before
legality is known, so an illegal custom-0 word left `result_sel = RES_XKNTT` in the
RTL and `RES_ALU` in the model. Making the rule **total** — illegal produces
exactly the reset bundle, the whole `ctrl_t` — is both easier to prove and
impossible for two implementations to disagree about. **A 10⁴-word run would very
likely have missed it**, which is the argument for running the comparison at the
size the plan specifies.

**Speedbump — the one mutation random testing could not reach.** Dropping the
`rs1 == 0` half of the `ECALL`/`EBREAK`/`MRET`/`WFI` reserved-field check escaped
every test in the file *including* the 10⁶-word run, and the arithmetic says why:
such a word has probability 7.1 × 10⁻⁸, which is **0.07 expected hits per
million draws**. The fix is a directed sweep over `rd × rs1` for all four
privileged encodings plus all 4096 `funct12` values.

> **The general rule, and it applies to every reserved field from here:** a
> constraint that picks out a handful of specific encodings out of 2³² needs a
> **directed sweep**. Random testing covers the common case and never the rare
> constraint.

**Result:** 34 mutations run — 11 realistic decoder bugs and 23 that flip one
`ctrl_t` field each. The second group exists because the cocotb wrapper that
flattens `ctrl_t` into scalar ports duplicates the field list by hand, and *a
field wired to the wrong port would make the comparison silently ignore it.* All
23 caught, so the wrapper carries every field.

### A4 — The pipeline skeleton

**Before:** verified components, no pipeline.

**Changed:** five stages wired together with **no forwarding, no stalls and no
flushes**, plus `rvntt_ram` and a simulation top.

**The omissions are the design, not a to-do list**, and the dangerous one is the
absence of control flow. `dbg_unsupported` pulses whenever an instruction retires
that this core cannot execute faithfully — illegal, `Xkntt`, branch, jump, or a CSR
access that writes a register — and the testbench fails on it. Without that guard
a program containing a taken branch would compute nonsense quietly, and A5's
differ would report a mismatch far downstream of the cause.

**The memory timing decision is made here** (Part I §3.1). Driving port B from the
*registered* result instead of the combinational one is an injected fault, and it
moves the answer.

**The expected value is not written down anywhere.** The test parses the `.word`
literals out of the assembly and computes the checksum from them, and Spike runs
the identical ELF as an independent third opinion. **The two references must agree
before the RTL is started**, so a disagreement between them is reported as its own
failure rather than as a DUT bug.

**Speedbump — six of seventeen mutations escaped, all the same shape: comparing one
register at the end is too weak.** Each needed a different fix, and one is
instructive:

> An off-by-one instruction fetch skipped the program's leading `auipc`, and the
> resulting **truncated pointer still aliased to the right word**, because
> `rvntt_ram` ignores high address bits by design. *Right answer, wrong instruction
> stream.* The testbench now requires the retired count to equal Spike's — the
> cheapest possible shadow of what A5 does properly.

Three more escaped for lack of byte-lane coverage: every store and load happened
to sit at byte offset 0, and **at offset 0 a store that forgets to replicate its
data across the lanes, a load that always takes lane 0, and a RAM that ignores its
byte enables are all indistinguishable from correct.**

**Result:** 477 instructions retire, producing `0xec22896c`, agreeing with a Python
model and with Spike.

### A5 — The commit-log tracer and the Spike differ

**Before:** a pipeline checked by one final checksum.

**Changed:** `rvntt_trace.sv` emits one line per retiring instruction in Spike's
`--log-commits` format, and `tb/cosim/commit_diff.py` runs both on the same ELF
and reports the first divergent line with context on either side. The plan calls
this the **highest-return item in Track A**, and it is.

**Equality is defined by a single renderer.** Both logs are parsed into
`(pc, insn, writes)` records and rendered back out through `commit_diff.render()`,
so "byte-identical" is literally true while being immune to a formatting
difference, and there is exactly one place to teach about a new annotation. **The
RTL's own log is re-parsed rather than trusted**, which is what caught the monitor
emitting a malformed line: Verilator's `-` flag leaks out of `%-3s` into the
following `%08x`.

**Fault injection asked a harder question than usual**, because "caught it" is the
wrong bar for a differ: a tool that only said *"these logs differ"* would pass a
naive mutation run while being nearly useless. Every mutation is required to
produce a **first divergence naming a commit index and a `pc`**, and that `pc` is
checked to be a real program address. The differ is separately required to fail on
an empty log and on a truncated one, and to pass on two identical logs, so it can
neither rot into a no-op nor be stuck at fail.

**Speedbump — two mutations escaped, and both were the *generator*, not the
differ.** In the distribution of **values**, not of instructions:

- A byte load that fails to sign-extend is invisible against an all-zero scratch
  area. Scratch is now pre-filled with a pattern containing bytes with the high bit
  set.
- A shift amount taken as `b[5:0]` instead of `b[4:0]` was invisible because almost
  every register held 0 or 1 — `slt`/`sltu` produce those, and drawing `x0` as a
  source 10% of the time compounded it, so **all seven shifts with bit 5 set were
  shifting 0, 1 or all-ones.**

> **The general lesson, and it recurs at A7, A12, A17 and A19:** when a mutation
> escapes, **check whether the stimulus could ever have reached it** before
> touching the checker.

**Result:** 500 generated programs of 400 instructions each, byte-identical. The
regression runs 100.

### A6 — Forwarding

**Before:** correct only with NOP padding between every producer and consumer.

**Changed:** EX/MEM → EX and MEM/WB → EX for both operands, younger producer wins,
a producer whose `rd` is `x0` supplies nothing.

**Speedbump — a formal proof that was checking itself.** `rvntt_forward`'s
properties were written in terms of the module's own `mem_supplies` /
`wb_supplies` wires, so every priority, completeness and soundness claim was
checking those wires **against themselves**; a mutation inside them sailed through.
The properties now rebuild the predicates from the raw input ports in De Morgan
form.

> **Generalised:** *a property that reuses an intermediate signal of the design
> under test cannot detect a bug in that signal.*

**Speedbump — a directed test miscredited with coverage it did not have.**
`a4_checksum.S` pads every RAW with three NOPs, so its dependencies sit at distance
4 and **never touch write-through at all**. The mutation harness reported it as
`PARTIAL`, which is how it was found. This is the first appearance of the harness
catching a *test* rather than a *design*.

**Result:** 1000 random programs at `--raw-density 1.0` — no padding at all —
byte-identical against Spike. The mutation harness is committed rather than left in
a scratch directory.

### A7 — The load-use interlock

**Before:** correct only if a load's consumer was padded away from it.

**Changed:** a load in EX whose `rd` is read by the instruction in ID holds IF and
ID for one cycle and puts a bubble into EX.

**The stall needed a register nobody expects** — the IF/ID hold register described
in Part I §3.1. Rewinding `pc_q` instead would cost two cycles per stall, not one.

**The larger thing this step added is `tb/cosim/cycle_model.py`, because a commit-log
diff cannot see timing.** A *phantom stall* — a bubble where none was needed —
changes no architectural state at all, so the log stays byte-identical and the
program is simply slower; the first symptom would be an IPC number disagreeing with
the LLVM `SchedMachineModel`, far from anything that points here. The model
predicts the span as `(retired − 1) + stalls + 2 × redirects`, finding hazards by
decoding the dynamic retired stream with the **frozen, spec-derived**
`model/rv32i_ref.py` — not the RTL and not `rvntt_hazard`'s own predicate. Using a
*span* rather than a cycle total means it needs to know neither the reset length
nor the pipeline depth.

**Two of the seven mutations are invisible to everything except that check:**
`lw x0, ...` becoming a stall source, and the interlock keying on the `rs1` **field**
rather than on `uses_rs1`. Both are functionally perfect and measurably slower.

**Speedbump — again the stimulus.** Reaching a non-source-field stall needs a `LUI`
or `AUIPC` whose immediate bits 7:3 name the register a load just wrote — about
0.5% per load. The generator now fills that field with the most recently written
register half the time, **generically, with no knowledge that the interesting
predecessor is a load.**

### A8 — Control hazards, and M4

**Before:** branch-free programs only.

**Changed:** branches resolve in EX; a taken branch or jump redirects the PC and
squashes both younger instructions in flight.

**The flush kills two slots** because a redirect resolved in EX has two younger
instructions behind it. The directed test puts a taken branch immediately behind a
taken branch for that reason: with a one-slot flush the second one redirects too
and the program ends up somewhere it was never meant to go — **which looks nothing
like "the flush is one slot short."**

**Three smaller decisions worth recording:** `funct3` for the branch condition
comes from the instruction word rather than a new decoder output, because widening
`ctrl_t` would mean changing the frozen model's bundle to suit the RTL. `JALR`'s
bit-0 clear is applied where the spec states it — *and note what its absence looks
like*: `rvntt_ram` ignores low address bits, so the core executes exactly the right
instruction at a `pc` that is off by one, and **only the commit log's `pc` column
shows it.** The link register is never forwarded and *cannot* be: a jump always
flushes two slots, so its consumer is three slots behind and takes the value
through write-through.

**Speedbump — two A7 mutation anchors went stale** the moment this step edited the
lines they pointed at. A stale anchor is reported as a problem rather than skipped,
**which is the only reason they did not quietly stop testing anything.** (This
happens five times in the project; the 0.03-second pre-flight that catches it
arrives at A20.)

**Result: M4.** 1000/1000 random programs byte-identical at `--raw-density 1.0
--load-use-density 1.0 --branch-density 0.12`, with **every program's cycle span
also matching the independent model** — so the claim is about timing as well as
values. Mutation testing becomes part of the regression rather than an optional
extra: it costs 2m10s, nearly doubling the run, **and it is the only test that
checks the other tests.**

### A9 — CSRs, traps and MRET

**Before:** no privileged state; `ECALL` did nothing.

**Changed:** Zicsr, the machine-mode CSR set, 64-bit `mcycle`/`minstret`, trap entry
and `MRET`. The trap invariant of Part I §4.1 is established here.

**The counter was in the wrong stage, and only the external suite said so.** See
Part I §4.1 for the mechanism. **Nothing written alongside this core had questioned
the placement.**

**Speedbump — `ECALL` now traps, which changed every harness in the tree.** The
replacement is better than what it replaced: `--stop-pc` at the trap handler is
exclusive and is exactly where Spike's trace is truncated, so both sides count the
same instructions with no offset to remember. The old `ECALL` asymmetry in
`commit_diff.py` is **gone rather than moved**.

**Speedbump — two more ways a test can be accidentally blind, both about stimulus.**
The `minstret` probe sat immediately after a taken branch, so MEM and WB held
bubbles and the EX-counted and WB-counted schemes gave the same answer — the
mutation escaped until two instructions were added to refill the pipeline. And the
`MRET` case set `MIE` and `MPIE` both high, so an `MRET` that forgot to restore
`MIE` still looked right; clearing `MIE` first **gives the swap somewhere to move a
value from.**

**Result:** 54/54 `riscv-tests` pass, with four skipped for reasons that are
features rather than gaps (`fence_i`, `ma_data`, `breakpoint`, `pmpaddr`). Every
test runs on Spike first, so a broken build cannot be reported as an RTL failure.

---

## Phase 3 — External verification, A10 → A11 (2026-08-30)

### A10 — RISCOF compliance, and four ways to a report that means nothing

**Before:** every test in the tree was written by this project.

**Changed:** the riscv-arch-test suite runs against Spike as the reference model.

**Four corrections stand between a first run and a report worth reading**, and each
would otherwise have produced a plausible wrong answer:

1. **RISCOF's exit code is not the verdict.** `riscof run` returns 0 for a run in
   which tests failed, and the first runner **printed `RISCOF_OK` over 50 real
   failures**. The verdict now comes from parsing the report — and *a run with zero
   passes is a failure too*, because an empty report is not a green one.
2. **`-mno-relax` is required, and its absence looks like a branch bug.**
   `arch_test.h`'s `LA` macro wraps its `.align` in `.option rvc`, then switches
   back. With linker relaxation on, that alignment becomes an `R_RISCV_ALIGN`
   relocation **the linker fills — with compressed nops**, because the relocation
   was recorded while `rvc` was still enabled. The result is `c.nop` in the
   instruction stream of a test for a core with no `C` extension. Spike faults on
   the first one, vectors to the still-unset `mtvec` at address zero and spins
   forever, **so the symptom is "the reference model hangs."**
3. **The branch and jump tests need 2 MB of memory.** They walk the whole immediate
   range — `jal-01` links to `0x801af18c`. Against the usual 64 KB array the image
   is truncated *silently*. Seven tests failed, all branches and `jal`, **which
   reads exactly like a control-flow bug.**
4. **PMP is excluded by name.** Those tests carry `verify (PMP['implemented'])` in
   their selection clause, but riscof 1.25.3 does not implement `verify` at all.
   **An exclusion with a reason recorded next to it is a statement; 43 failures
   left in a report are noise.**

**Fault injection found the suite's own blind spot** — the `JALR` bit-0 escape
described in Part I §9.2.

**Result: M5.** 76/76 selected tests pass; the HTML report is committed.

### A11 — riscv-formal, and the bug five suites could not reach

**Before:** correctness established by simulation and directed proof only.

**Changed:** 43 riscv-formal checks at BMC depth 14.

**The plan is wrong that the commit tracer is enough.** It says to wire RVFI out of
the existing tracer because "the information is the same, in a standardized form."
It is not: riscv-formal requires a **trapping** instruction to be reported with
`rvfi_trap` set, and A9's trap invariant squashes the faulting instruction in EX so
it never reaches WB. That squash is load-bearing, so `rvntt_rvfi.sv` adds a second,
**parallel** report path rather than unpicking it. The trapped instruction fits in
the hole it leaves behind: a trap at cycle *T* clears `ex_mem_q`, so `mem_wb_q` is a
bubble at *T+2* — exactly the cycle that instruction would have retired. The module
**asserts that mutual exclusion rather than arguing it.**

**What it found, in seven seconds, on the first honest run:** `ex_mem_fwd_data` was
written as *"pc_plus4 for `RES_PC4`, `ex_result` for everything else"*, while
`mem_result` sends `RES_XKNTT` to its default zero arm. **A legal `Xkntt` instruction
forwarded its ALU output to the next instruction and wrote zero to the register
file.** Two case statements over the same enum, disagreeing on one arm. No RV32I
program can reach it, which is why A5's random programs, the directed tests,
`riscv-tests`, RISCOF and 35 mutations had all missed it.

> **The lesson generalises:** an unimplemented feature is not an absent one, and
> the datapath has to be self-consistent about an encoding **the decoder accepts**
> even before a stage executes it.

**And what nearly hid it.** The first version of the runner reported a green 43/43
**over a core with a deliberately broken adder**, because `genchecks` writes
`expect pass,fail` into every `.sby` and sby therefore exits 0 on a failed check.
That is the same shape as A10's exit code, so it becomes a rule rather than an
anecdote: **take the verdict from the artefact, not from the process.**

**Result: M6.** Seven new mutations target the RVFI port itself — *a verification
interface that misreports is as dangerous as a broken datapath, because everything
downstream believes it.*

---

## Phase 4 — Silicon, A12 → A13 (2026-09-01)

### A12 — A CPU on the board

**Before:** a verified core with no SoC.

**Changed:** `rvntt_soc_top` = core + 128 KB dual-port BRAM + memory-mapped UART and
GPIO. It loads, runs a program out of BRAM, and prints over the USB-UART.

**Almost nothing here is new hardware.** P0.5 put the MMCM, the reset synchroniser,
the UART, the XDC idiom and the batch Tcl flow on the board already, and reusing
them meant that **when something went wrong it was one of the parts A12 actually
added.**

**One memory and not two**, against A12's own text, because plan §1.4 asks for one
true dual-port array and **one array means one image** (Part I §6).

**Speedbump — the elaboration gate caught an empty instruction memory.** A missing
`$readmemh` image was reported by Vivado as a **CRITICAL WARNING** — a bitstream
built and silently wrong. `$readmemh` resolves against Vivado's working directory,
not the source file's. **Third time that CRITICAL-WARNING-and-continue behaviour
had been the difference between a build and a correct build.**

**Speedbump — the simulation wrapper was smaller than the board.** It shrank the
memory to 4096 words to keep the zeroing loop short, and the stack aliased straight
onto the program. **Simulating a smaller memory than the board has is precisely how
a testbench passes over a bug the hardware hits.**

**Speedbump — two of four new mutations escaped, both the stimulus.**
`mmio_store_not_gated_from_ram` aliases onto word 0 — the first instruction of
`crt0`, which runs once and is never revisited, **so the program keeps working while
its own image rots underneath it.** Fixed in the program, which now re-reads
`.text.init` and compares. And `uart_rx_samples_on_bit_edge` was invisible because a
receiver sampling on the bit boundary decodes exactly as well as one sampling at the
midpoint **when the host's edges are perfect**; the testbench now transmits ~2.9%
slow.

**The hardware loop itself was fault-injected on the board:** one byte of the
*memory image* was changed from `world!` to `world?` and the FPGA reprogrammed. The
parser reported `SOC_UART_FAIL` and named the line. **Mutating the image rather than
the C source proves two things at once** — that the checker can fail, and that the
`$readmemh` contents genuinely reach the bitstream.

**Fmax: 73.121 MHz.** WNS turned out **not to be monotonic**: 73.121 MHz closes at
+0.218 ns while 71.250 MHz closes at +0.092. That is the concrete reason
`1/(T − WNS)` from a passing run is not Fmax — **extrapolating from the slower run
would have *understated* the answer.**

### A12 fix — the RGB LED's red and blue were on each other's pins

**Caught by a person looking at the board, which is the whole point.**

The Digilent master XDC lists the RGB LED as `led0_b, led0_g, led0_r` — in that
order. They were read as `r, g, b`, and two pins were transposed.

**Everything passed.** Verilator lint, Vivado elaboration, synthesis, timing
closure, bitstream generation, JTAG programming, and an **eight-block UART capture
that was byte-perfect including the receive path and the program's own image
checksum**. A swapped *output* pin is invisible to every one of those, because none
of them can see past the pad. The entire symptom was that LD0 showed green blinking
with red solid: the alive indicator wearing blue's wiring, while the actual error
indicator sat dark. **Had `dbg_unsupported` ever fired, nothing would have shown
it.**

**So the fix is not care.** `fpga/constraints/arty_a7_100t_pins.txt` is extracted
mechanically from the vendor file — provenance and SHA-256 in its header — and
`tb/fpga/check_xdc_pins.py` compares every assignment in every XDC against it. It
found both pins independently, and it runs five injected faults on itself first,
including this exact transposition. **What it still cannot check is whether the
right *signal* drives a correctly named port**; that is what the LED check on the
bench is for, and it should not be skipped.

**And Fmax was measured on the wrong design.** Correcting two output pins — a change
with **no logical content, which cannot affect a single internal path** — moved WNS
at 73.121 MHz from +0.218 ns to −0.153 ns and moved Fmax down 3 MHz, to **70.131**.
Placement simply landed differently. **This is where the ±0.4 ns design-to-design
spread was first quantified**, and it governs how every later Fmax claim in this
project is worded.

**The colour-mixing note is not decoration.** Mixing is what disguised the
transposition: red plus green reads as *orange*, and the board was reported as
"flashing between green and red" rather than "red is on." **Healthy looks
turquoise**, and someone checking this cold needs to know that or they will report
the working case as broken.

### A13 — Dhrystone and CoreMark on the board

**Before:** a working SoC with no performance number.

**Changed:** both benchmarks ported and measured. **DMIPS/MHz 0.7306, CoreMark/MHz
0.9607, IPC 0.7227 and 0.6930**, at 70.129 870 MHz, three JTAG passes, all nine
report blocks identical to the cycle. **That closes M7 together with A12's Fmax.**

**Both benchmarks live in one bitstream**, because `$readmemh` bakes the program in
at synthesis and a second benchmark is a second fourteen-minute implementation run
for no measurement benefit. **On this SoC running Dhrystone first cannot perturb
CoreMark** — no cache, no DRAM refresh, no interrupt source, no second bus master.
*On anything with a cache that would be wrong, which is why it is stated rather
than assumed.*

**A wrong score has three causes that look identical from outside** — broken port,
broken core, broken formatter — so each is checked separately and each check is
fault-injected. `bench_printf` diffs the formatter against glibc over 340 cases;
`bench_host` compiles the same sources natively and checks CoreMark's CRCs and
Dhrystone's published final values, **so a CRC wrong on the board and right on the
host means the core, and wrong in both means the port.**

**`mcycle` got a ground-truth reference, which the CSR file had observed it did
not have.** Spike cannot be it — Spike's `mcycle` advances once per instruction —
but Verilator counted the clock edges itself. The mutation that makes `mcycle`
count *retirements* is **self-consistent enough that IPC comes out at a plausible
1.00**, and is caught by that and by nothing else.

**Two negative results worth as much as the positive ones.** A mutation making
`SRA` fill with zeros **escaped both benchmarks**: they do execute arithmetic right
shifts — libgcc's `__divsi3` opens with `srai a2,a0,31` — but only on non-negative
values, where `SRA` and `SRL` agree. That coverage comes from `riscv-tests` and
riscv-formal, **and it is recorded rather than papered over.** And a 30-run sanity
check failed a 0.1% *relative* bound that the real run would have passed
**vacuously**; the gap is a fixed 27 cycles, so the bound is now absolute. *Any
threshold that scales with the workload deserves the same look.*

**Both figures are below the plan's expectation, for a reason stated up front:
there is no `M` extension.** Every multiply, divide and modulo is a branch-heavy
libgcc call, and branches are statically not-taken with no BTB. Dhrystone spends
0.384 cycles per instruction on stalls and flushes, and **nothing attributes that
split yet** — the core has no branch or stall counters.

---

## Phase 5 — `MODS_A`: RV32IM and branch prediction, A14 → A19 (2026-09-01 → 09-02)

### The modification document itself

**Two of A13's measurements said the scope was one extension short of the baseline
the plan itself assumes.**

The reference `ntt()`, same source and same `-O2`, costs 148 645 instructions on
`rv32i` and 23 795 on `rv32im` — **6.25×**. Plan §B6 estimates the software NTT
baseline at "roughly 15 000–30 000 cycles". The `rv32im` figure lands inside that
band and the `rv32i` one is 7–14× outside it, **so §B6 was written assuming a
hardware multiplier while §1.5 scopes the core without one.** The contradiction is
in the plan, and it is not cosmetic: at §B6's P=4 the headline kernel speedup is
~150× against an honest baseline and ~900× against ours. Worse, **the distortion is
not uniform** — ML-KEM's polynomial arithmetic is multiply-bound and Keccak has no
multiplies at all — so the Amdahl breakdown §10 M3 asks for is wrong on this
hardware however it is worded. **That is why this is a silicon change and not a
wording change.**

The second measurement: stall attribution on the real RTL. Branch and jump flushes
are **24.4%** of Dhrystone's cycles and **28.7%** of CoreMark's; load-use interlocks
are 3.3% and 2.0%. **Control flow is ~90% of what is lost**, which promotes the
plan's *optional* improvement loop to *specified*.

**The conflict search found four things that could have been intractable and are
not, each checked rather than assumed:** `Xkntt` is `custom-0`/`custom-1` and `M` is
`OP` `funct7=0000001`, disjoint, so the strict reserved-field rule is untouched; the
toolchain already ships `rv32im/ilp32` with a real libgcc; Spike supports `M`
natively, so no fork changes; DSPs are ~65 of 240 in the worst case.

**A revision that came from reading the post-route path hop by hop.** A12 had
concluded the SoC is slow because it is spread across 32 BRAM tiles, and told anyone
pushing past 73 MHz to start with a floorplan. **That is overstated.** Of the
13.373 ns: 4.56 ns is the forwarding mux reaching the ALU operand, 4.44 ns is the
**ALU result mux** feeding the misaligned-address check, 2.14 ns is the trap net
(fanout 245) gating the byte enables, and **only 1.32 ns is the crossing to the
BRAM** — under a fifth. About half is a logical dependency chain. Both `CLAUDE.md`
files were corrected, and **an Fmax step (A17) was inserted before the predictor
rather than after it**, for three reasons: it is the largest single lever and a
contained change; the predictor is the one addition with real Fmax risk and should
land on a design with margin; and plan §9 needs headroom for the coprocessor.

**A17 carries a done-when that makes it honest: not one cycle count may change.**

### A14 + A15 — RV32IM

**Before:** RV32I + Zicsr; every multiply a libgcc call.

**Changed:** the generic multi-cycle EX handshake and `rvntt_muldiv.sv`. **The
mechanism was built before the instructions, and generically** (Part I §3.4).

**One mutation escaped and the escape was the finding.** Removing the enable on the
multiplier's operand registers was caught by nothing — *correctly*: the multiplier
is a register chain whose latency equals its depth, so the start-cycle operands win
whatever the enable does. **The enable is load-bearing for the divider**, whose
state is a loop. The bug that mutation was reaching for lives one level up —
feeding the unit `id_ex_q.rs1_data` instead of the forwarding muxes — and *that* one
is caught.

**Speedbump — the checking script was wrong before the design was.**
`synth_ooc.sh`'s first version reported `DSP=0` over a netlist containing **four**
DSP48E1s, because it filtered on `PRIMITIVE_TYPE` group names that were **guessed
rather than looked up**. Believed, it would have sent someone to fix a multiplier
that was already right. It now prints a histogram of every `REF_NAME` present,
filtered by nothing: **a report that names no group cannot name one wrongly.**
*Third time this shape had appeared, after A10's exit code and A11's exit code.*

**Speedbump — RISCOF did not fail, it hung.** The Spike reference plugin held its
own literal `rv32i`, so the arch-test `M` cases compiled `-march=rv32im` and ran on
a Spike told `rv32i`: illegal instruction on the first `mul`, vector to an unset
handler, spin. **Twenty-five minutes of silence.** Three fixes in increasing order of
value: a timeout (catches the symptom), the ISA string derived from the YAML (two
copies instead of three), and `check_isa_consistency()` reconstructing `misa` from
the letters and comparing against both the YAML and the RTL (**catches the cause, in
milliseconds**).

**Speedbump — the prediction about riscv-formal named the wrong cause.** `MODS_A`
§3.2 expected the 34-cycle divider to force the depth from 14 to 46. What actually
happened is that **a combinational 33×33 multiplier unrolled fourteen times is the
canonical hard SAT instance** and sits in the cone of the RVFI outputs whether or
not any check reads it. `RVNTT_ABSTRACT_MULDIV` frees `result` and leaves the
sequencer real.

> **State the invariant, not the conclusion.** The divider's natural correctness
> property — `q·b + r == a` with `r < b` — asks the solver to prove a shift-subtract
> loop equivalent to a symbolic 32×32 multiply, and bitwuzla sat on that one query
> for **a quarter of an hour**. The same statement as a **per-iteration invariant**,
> carried in ghost registers whose recurrences are shifts and adds, proves in **four
> minutes at depth 37**.

**Speedbump — an ambiguous mutation anchor is worse than a missing one**, because a
missing one is *reported* and an ambiguous one is *obeyed*. A14 broke ten anchors;
eight were ordinary staleness, and **two matched twice**, because `rvntt_muldiv` is
wired from the same two forwarded operands with the same port names and spacing as
`rvntt_branch`. The harness silently mutated the multiplier and reported a
`PARTIAL`: **a true statement about a bug nobody injected.** `mirror_rtl` now
requires exactly one match.

### A16 — RV32IM on the board

**Result: M7.1.** Fmax **73.752 MHz**, DMIPS/MHz 0.7325, CoreMark/MHz **2.4309**,
IPC 0.6860 and 0.7006, three JTAG passes, twelve report blocks identical.

**The dual baseline is the point of the step** (Part I §8.3).

**A13's figures are preserved, and that was proved rather than asserted.** A second
bitstream carries A13's exact image on the RV32IM core, and **every counter comes
back identical**. Adding a multiplier, a divider and a stall path to EX cost RV32I
code **exactly zero cycles**. *A multi-cycle unit wired into the stall path is
precisely the kind of change that quietly costs a cycle somewhere, and nothing
short of counting would have shown it.*

**`MODS_A` A14 predicted both benchmarks would improve. Only one does** (Part I
§8.5).

**Fmax went up by 5.2% after adding a multiplier and a divider**, and the write-up
records the asymmetry: *a favourable Fmax movement across a design change is exactly
as much a measurement of a different design as an unfavourable one, and far less
likely to be questioned.*

`build_soc.tcl` now counts DSP primitives by `REF_NAME` and **fails the build below
four**, because a multiplier that fell back to fabric would still be correct, would
cost about a thousand LUTs and several nanoseconds, and **every symptom would
surface as a timing number with no obvious cause.**

### A17 — Address generation off the store path

**Before:** `ex_addr_misaligned` read `|ex_alu_y[1:0]` — bits off the **muxed** ALU
output — so the four-level operation-select mux sat in front of the alignment check,
which gates the trap, which gates the byte enables, which reach the BRAM's write
enable.

**Changed:** a memory address is always `rs1 + imm` and the decoder enforces it, so
a dedicated 32-bit adder feeds `dmem_addr`, the alignment check, the store byte
offset, `mtval` and RVFI. **Twenty-six LUTs, and 1.589 ns off the period.**

**The whole thing rests on one equality — that for loads and stores the second adder
and the ALU agree — and that is a property of the *decoder*, which is a file people
edit.** So `a_addr_adder_matches_alu` asserts it under `RISCV_FORMAL`, where every
one of riscv-formal's 43 checks then proves it at depth 14 **for free**.

> **A loose constraint is not a measurement, and this step turned that principle
> into a number.** The adder was first judged by re-running the old 73.0 MHz
> constraint and comparing slack: +0.111 → +0.290 ns, **an 0.18 ns improvement**,
> which is what it would have been reported as. The binary search then found the
> same design passing at **83.542 MHz — 1.589 ns faster.** The router optimises to
> the constraint and stops; the slack it leaves measures *when it stopped*.

**Two of the four levers did nothing, and they are written up at the same length as
the two that did.** `MAX_FANOUT` on the trap net — aimed by the post-route report,
at a net driving 141 loads genuinely on the path — moved timing **0.263 ns the wrong
way**, which is inside the placement spread, so the honest verdict is "no measurable
gain" rather than "it hurt". *A high-fanout net is only worth what its own route
costs, and here that is a nanosecond in eleven.* The implementation-strategy sweep
gained 0.408 ns, which **is exactly the spread**: it is adopted because it changes no
logic, and **86.490 MHz should be read as 83.5–86.5 with the strategy as a plausible
but unproven cause.**

**The 64 KB memory experiment reaches ≥90.0 MHz on 16 BRAM tiles and is *not*
adopted.** The payoff is ~4%, §1.4 specifies 128 KB, and **the memory is not spare**:
the Tier-2 coprocessor's buffers do not exist yet and cannot be sized against an
array already given away. *Buying clock now by spending memory the thing this clock
exists to serve has not asked for yet is the wrong order.*

**Result: 73.752 → 86.490 MHz, +17.3%, with not one benchmark cycle count changed** —
verified on the board across three programming passes.

### A18 — The instrument that closes

**Before:** 0.384 stall-and-flush cycles per instruction, unattributed.

**Changed:** a Verilator observer implementing
`cycles = retired + load-use stalls + multi-cycle EX stalls + 2 × redirects`,
**residual exactly zero** on Dhrystone, CoreMark and both NTT builds.

**A Verilator observer rather than hardware counters, because counters would have
perturbed the design A17 was tuning.** And it does not need to be in the core: the
NTT regions match the board's cycle counts exactly, and Dhrystone matches as
`cycles = 777 × runs + 30` and `instructions = 533 × runs + 32` with **silicon and
simulation independently producing the same two intercepts from measurements a
hundredfold apart in scale.**

| Region | Cycles | Retired | IPC | Flush | Load-use | EX stall | Residual |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dhrystone | 1 554 030 | 1 066 022 | 0.6860 | 364 008 (23.4%) | 52 000 (3.3%) | 72 000 (4.6%) | **0** |
| CoreMark | 1 234 083 | 864 600 | 0.7006 | 222 780 (18.1%) | 62 139 (5.0%) | 84 564 (6.9%) | **0** |
| NTT `rv32i` | 205 884 | 148 647 | 0.7220 | 56 334 (27.4%) | 903 (0.4%) | 0 | **0** |
| NTT `rv32im` | 39 058 | 23 797 | 0.6093 | 5 398 (13.8%) | 1 799 (4.6%) | 8 064 (**20.6%**) | **0** |

**Fault injection, and what it caught.** `--selftest` breaks the accounting five
ways. Four move the residual. **The fifth — classifying every branch as taken —
leaves the residual at exactly zero** and is caught only by the second closure,
`redirects = taken + JAL + JALR`. *That is why there are two checks: A19 is judged on
the taken/not-taken split, and the cycle identity is structurally blind to it.*

**The benchmark comparison tool is fault-injected too, and its fourth fault — a rate
not scaled by the clock — is vacuous when the two captures share a clock, which is
the normal case.** So its selftest runs against a synthetic 1.2×-clock copy instead.
**A check that silently cannot fail on the inputs it was handed is the exact failure
mode the tool exists to prevent, and it does not get to do it to itself.**

### A19 — The branch predictor

**Before:** statically not-taken, no BTB, two lost cycles per taken transfer.

**Changed:** the predictor of Part I §3.3.

**The first build failed 80 MHz by 3.886 ns, and fixing it changed the
architecture** (Part I §3.3).

**The specification came first and is a deliverable.** `docs/a19-bpred-spec.md` is
implemented twice, and **that is only possible because two decisions in the
specification were made to make it possible**: state changes only when a transfer
*resolves* in EX, and the reset state is "every entry invalid" and nothing else.

**Both of those replaced a worse design.** The first draft had a separate 256-entry
PHT and cleared both arrays with a 256-cycle sweep after reset, because distributed
RAM cannot be reset. **That sweep works on hardware and is invisible in a benchmark,
and it would have been a correctness problem twice**: the cycle model would have
needed to count cycles since reset to know whether the predictor was awake, and
**riscv-formal at BMC depth 14 would have proved all 43 checks over a predictor that
had not yet done anything.**

**All eight mutations escaped `random:branch` on their first run.** *Every one,*
which is a far clearer signal than one escape would have been.
`gen_random_prog.py` emits forward-only branches so that a generated program
terminates, and the consequence is that **a random program is a straight line in
which every control-transfer site executes at most once** — so a predictor that
remembers what a site did last time is **inert in every random program this project
has ever produced.** The 1000-program cosimulation still proves the predictor is
*architecturally invisible*, which is its main risk, and it is **structurally unable
to say whether the predictor works.** `sw/tests/a19_bpred.S` is what says that:
eight loop shapes, including two branches one kilobyte apart that share a BTB index
and evict each other every pass.

**And the model had to be taught the pipeline.** It predicted a 916-cycle span where
the RTL measured 992. An update lands in EX and cannot reach a lookup that has
already happened, **so tight loops mispredict on alternate iterations.**
`DelayedBPred` queues updates on that rule. *Forwarding the in-flight write would
have closed the common case, cost a mux on the prediction path A17 had just spent
six implementation runs shortening, and still left a two-instruction loop outside
the window: modelling the machine that exists beat changing the machine to suit the
model.*

**The predictor costs clock, and that is the trade this step makes.** A17 closed at
86.490 MHz; A19 closes at **77.501**. Both levers applied after the lookup fix were
measured separately and **proved cycle-neutral** — reverting only them gives 52
counters identical across all four regions.

**Result: M7.2.** IPC 0.6860 → **0.8752** on Dhrystone and 0.7006 → **0.8338** on
CoreMark; **absolute Dhrystone rises 63.35 → 72.43 DMIPS on a slower part.** Both IPC
figures and DMIPS/MHz land inside the plan's expected bands for the first time.

**A19's Fmax carries a wider band than any previous one, and the reason is recorded
rather than averaged away.** The search is **not monotonic** — 79.246 MHz failed by
−1.186 ns while the *tighter* 80.998 MHz failed by only −0.676 — and one netlist's
implied path delay spans **12.90–13.81 ns, a 0.9 ns spread**.

**Two things are left open and neither is buried.** CoreMark's iteration count had to
change from 2200 to 2500 because A19 made the 2200-iteration run finish in 9.81 s,
under CoreMark's own 10-second reporting minimum — **`--allow-short` exists and was
not used**, because publishing a score that breaks the benchmark's run rules is the
same error as reporting Dhrystone's overflowed figure. And Dhrystone *in simulation*
at 2000 runs costs 615.048 cycles/run where 4000, 8000 and the board all agree on
~609.0; the 2000-run point carries exactly 3 extra mispredicts per run, it is not the
image configuration and not a fixed warm-up cost, and **it is unexplained.**

**A note on how these three steps were committed.** A17+A18 and A19 were developed to
be committed separately, and the separation was to be produced by mechanically
inverting the script that applied A19. **The inverse no longer composed** — five of
its twenty-nine substitutions had later edits land *inside* the regions they created.
**A split whose purpose is to avoid an untested intermediate commit is not worth
producing an untested intermediate commit**, so the three are one commit with both
intended messages preserved in full.


---

## Phase 6 — `MODS_A2`: the ISA, performance and cryptographic rounds, A20 → A30 (2026-09-02 → 09-04)

### The second modification document

**Five claims the round depends on were verified by running something rather than
recalled, and two came back the opposite of what the scope expected.**

riscv-formal **does** ship usable Zb instruction models — all 34 — plus pre-built
bundles. The pinned arch-test branch **does** have B coverage: 32 tests, plus 2 for
Zicond and the Zbkb subset of K. Both had been expected to be absent.

**But the arch-test B suite is the *old* grouping** and includes `clmul`, `clmulh`
and `clmulr`, which are **Zbc and not in ratified B**. So a boundary is needed
anyway, just not the one anticipated. *The dishonest third option — claiming the
suite passes without saying which tests ran — is the shape this project had by then
hit four times, so it is named in the risk register.*

**Zkt's instruction list was read from the ratified specification, and it excludes
division and remainder outright.** The premise that this core's data-independent
divider pays a second dividend at Zkt **is therefore wrong — Zkt never asked for
it.**

**Two findings changed the plan rather than decorating it.**

*The coprocessor's clock is decided, not deferred.* Tier-1 `Xkntt` **cannot** be in
a separate clock domain — it is an EX-stage functional unit whose frozen latency
table gives `kmm` four cycles, and a CDC crossing costs four to six *before any
arithmetic*. So §B9's "coprocessor Fmax exceeds core Fmax" is the wrong criterion
for Tier 1: **the core's Fmax is a hard floor the butterfly must meet.** A24 measures
that floor **before** the Fmax steps commit to a number, which is what keeps it from
firing at M15, two tracks from its cause.

*And B fights the Fmax goal in one specific place*, which is why it goes in its own
unit (Part I §3.5). **Reading the same report turned up a lever nobody had noticed**:
`ex_jump_target` taps `ex_alu_y` for `JALR` alone, and A17's address adder already
computes exactly `rs1 + imm`.

### A20 — Hardware performance counters

**Before:** three of the four terms in A18's identity came from a Verilator
observer; only `mcycle` and `minstret` came from silicon.

**Changed:** six `Zihpm` counters, validated against the instrument **to the count**
on both benchmarks. Six real counters rather than 29, because the spec permits any
subset and **29 × 64 bits of counter on a design whose Fmax is the point of this
round would be self-defeating.**

**The comparison failed on its first run, by exactly one, on one benchmark, and
neither side was wrong.** The instrument delays its redirect count by three cycles
on purpose so a redirect's two lost cycles are charged to the region that actually
lost them; a counter in silicon increments on the pulse and cannot do that. **The
tempting fix was a tolerance.** The right fix was to have the instrument keep *both*
counts, so the check stays an exact equality — *a ±1 tolerance would also have passed
a genuinely miscounting predicate, which is the entire class of bug the comparison
exists to find.*

**The stall tie-break does nothing, and that was the finding.** The load-use event
was written `id_stall && !ex_stall` to mirror the instrument's else-if, and the
comment beside it said **that was the line the whole step was won or lost on.**
Dropping the guard changed no count anywhere on either benchmark, because `id_stall`
requires a *load* in EX and `ex_stall` requires a *multiply or divide* there — **one
instruction cannot be both.** A guard against an impossible case is harmless; **a
guard believed to be load-bearing is not, because it makes the next reader model an
overlap that cannot happen.** The real fact is now asserted as
`a_stalls_are_disjoint`, proved at depth 14, **and shown non-vacuous**: asserting each
operand unreachable makes twelve checks fail.

**Constant versus proportional is the whole test for instrumentation overhead.** A
third path has software read the counters through `csrr`, the way the board will.
The first version bounded that *relatively* and failed the small region while passing
the large one — **which is backwards.** Doubling the Dhrystone region doubled every
event count and left all four excesses **identical** (+6, +0, +16, +23), so the bound
is absolute and the measurement is what justifies it.

**One real gap is recorded rather than papered over:** `hpm_btbhit` has no
counterpart in the retired instruction stream, so it is *reported and not checked*,
and the mutation that would exploit that is listed **with its reason instead of a
catcher that does not catch it.**

Two mutation anchors went stale — **the fifth time** — and were visible only because
the previous step had fixed `run_regress.py`'s 40-line output truncation that had
been hiding exactly those lines. A **`--check-anchors` pre-flight** now runs as its
own regression test: a string search, **0.03 s against the full run's fourteen
minutes**, fault-injected both ways.

### A21 — B and Zbkb in their own lane

**Before:** RV32IM.

**Changed:** 34 instructions in `rvntt_bitmanip.sv`, joined at `ex_result` (Part I
§3.5). **RISCOF 84 → 120**; riscv-formal **43 → 77 checks at depth 14 unchanged**,
because every instruction here is combinational and none of A14's divider depth
problem recurs. Cosim 30/30 byte-identical against Spike at 25% B density.

**`misa.B` is deliberately not set, and the plan said the opposite** (Part I §2).
Both halves of the plan's note were wrong.

**Two checkers were found reporting green over nothing.**

`tb/cocotb/run_cocotb.py` ended in a bare `return 0` **and had since A1**.
`cocotb_tools`' `runner.test()` runs the tests, writes `results.xml` and returns
normally whether they passed or failed — so **every cocotb test in this project had
reported PASS unconditionally.** It surfaced because a deliberately broken wrapper
printed `TESTS=6 PASS=1 FAIL=5` **and a green regression row on the same run.**
Fixing it exposed **two real failures hidden since A14**.

`riscof_spike_ref.py` dropped every `Z` extension from the reference ISA string —
**for the second time.** Its own header describes A14's version of the identical bug
with `mul`; this time B's tests compiled `-march=rv32izbb` while Spike was told
`rv32im_zicsr`, trapped on the first `clz` and **spun to a 600-second timeout, three
at a time, reporting nothing at all.**

> Counting A10's RISCOF exit code, A11's sby exit code, A14's `synth_ooc` `DSP=0`,
> A19's stale fixture and A20's stale anchors, **that is six instances of one
> shape.** The rule earned by all six: *a tool's exit code is not its verdict unless
> you have checked that it is, and **the check belongs in the runner, not in the
> reader.***

**A sixth mutation escaped and was right to:** `6'd32 − shamt` is a valid alternative
rotate implementation, not a bug, since `a << 32` is zero on a 32-bit target.
*Second time in two steps that an escape was evidence about the mutation rather than
about the checks.*

**The encoding tables in the RTL, the Python model and the document's appendix are
all generated by the assembler rather than typed.** Writing them by hand was tried
first and put `rev8`, `brev8`, `zip` and `unzip` in `OP` when they are `OP-IMM`. The
generated table also exposed a decode trap worth naming: **`clz`, `ctz`, `cpop`,
`sext.b` and `sext.h` share opcode, funct3 and imm[11:5] entirely and differ only in
the `rs2` field**, so a decoder treating it as don't-care accepts three illegal
encodings **while passing every functional test.** Only the 10⁶-word decoder sweep
catches that mutation, *because no functional test ever emits a reserved encoding.*

### A22 + A23 — Zicond, and the ISA round on hardware

**Before:** B and the counters in the tree, no Fmax measured since either landed.

**Changed:** Zicond (two instructions, half a day — **the step exists for the
measurement**), then the full hardware round.

**Two builds could not separate Zicond from B**, and it took a third (Part I §8.5).
**Zicond is worth approximately nothing on either benchmark.** The predicted *shape*
was half right: the mispredict count did not move and the *rate* rose 0.11 points
purely because the denominator shrank, **so reporting either alone would have been
misleading in opposite directions.**

**Separately, and not about Zicond: B removed 3999 Dhrystone mispredicts without
removing one branch.** `model/bpred.py` — which reads only the retired stream and the
written specification — reproduces the RTL's redirect count to the unit in all three
builds, so it is a real consequence of the instruction stream. **The predictor's
mispredict count on this core is sensitive at the 20% level to code containing no
branches.**

**Fmax fell 3.8%, and the first search blamed the wrong thing.** 72 MHz failed by
−0.943 ns and A21's stop rule points at the new bit-manipulation unit — **but the
post-route destination was `mhpmcounter_q[2][25]/CE`. The cost was A20's counters**,
whose 6:1 event mux sat after `ex_redirect`, *exactly where A20's own write-up said
to look.* A registered one-hot mask got 70.998 MHz; registering the event bus as well
got **74.577** and moved the endpoint back onto the core's own redirect path. **Both
fixes are provably cycle-neutral** — 32 and 36 counters compared, 0 differing.

**M7.3's done-when was revised once, and the revision is recorded** (Part I §8.2).
CoreMark's residual was −168 until its glue nested the reads in the same order
Dhrystone's does, **and that difference between two regions was the finding.**

**Three fixtures went stale under this step and all three were caught by checks that
are equalities rather than tolerances**: the profiler armed the counters through a
register the enable had stopped reading (**every hardware counter read zero**); the
hardware benchmark pointed at A19's bitstream; and its 75-second capture window could
not fit three blocks of an image running ten times A19's iterations.

**Result: M7.3.** Fmax 74.577 MHz, DMIPS/MHz 0.9361, CoreMark/MHz **3.1866**, all 44
integer counters identical across three passes. **B makes Keccak 1.1953× faster** —
the number the round exists for. **CoreMark/MHz rose 10.1% while its IPC *fell*
2.0%**, which is the document's pre-committed prediction arriving exactly as written:
B replaces sequences with single instructions, so retired count falls faster than
cycles.

### H1 — Making the regression affordable

**Before:** twenty-six minutes, and three tests were **84%** of it —
`mutation_pipeline` 1284 s, `riscv_formal` 295 s, `formal_muldiv` 181 s, with the
other 46 tests summing to 180 s.

**Measuring first was the whole point:** the growth was **concentrated, not broad**,
and only one of the three was structural rather than solver time.

**Mutation testing is now parallel: 1151 s → 547 s on identical code, and the serial
and parallel reports are byte-identical.** That is verified, not assumed, and it is
why results are collected and printed **in manifest order rather than as they
complete** — *this table is meant to be diffable between runs, and one whose line
order depends on scheduling is not.*

**Making it safe meant finding the shared mutable state, and there was more than
expected**: a single `image.hex` baked into every simulator at build time; one
riscv-formal work directory every worker generated into; a lazily populated ELF
cache; and a generated ISA bundle written non-atomically.

> **The riscv-formal one is the instructive failure.** It did not corrupt anything
> quietly — it made every `rvfi:` mutation **die at once on the first parallel run,
> loudly**, which is the good outcome. *A version of the same bug that merely
> interleaved would have produced a check that described some other worker's design
> and reported a verdict about it.*

**The baseline turned out to be the floor.** With mutations parallel, 566 s of the
run was the baseline validating 44 distinct catchers one at a time.

**What was deliberately not done.** Running only the steps whose RTL changed was
rejected: `--step` is right for an edit loop and **wrong as a default**, because an
escape in an untouched step would hide indefinitely and *a partial default is only
honest with a scheduled full run, which this project does not have.* Parallelising
`run_regress.py` itself was also rejected — its tests share build directories,
`fpga/build` paths and **a JTAG cable**. *The concentrated 84% was worth attacking at
that risk; the distributed 16% was not.*

**Two checks were added rather than removed**, both cheap enough that they reduce
total time by failing fast: `isa_consistency` (0.02 s) and the `--probe` flag that
makes A23's one-run "did this lever help?" question explicit — **and says in its own
output that a probe is a bound and not an Fmax.**

### A24 — The Tier-1 probe

**Before:** the Fmax target for A26 was unbounded by evidence, and the risk was
committing the core to a clock the coprocessor could not reach — **§9's failure mode
firing at M15, two tracks from its cause.**

**Changed:** a representative Tier-1 butterfly, built in `rtl/probe/` — **never
`rtl/ntt/`, which is Track B's, because a probe sitting there would eventually be
mistaken for a starting point.** *What carries across is the frequency, not the RTL.*

**Result: Fmax = 128.125 MHz** out of context on this part and speed grade, with **2
DSP48E1, 326 LUTs, 150 FFs**; 132.500 MHz is the fastest constraint that failed, by
0.019 ns. The stop rule was stated *before* the measurement — 10% margin — so the
core may target 110 MHz **and clears it with 16%.**

**The frozen latency table is not a compromise; it is measured to be right.**

| Arrangement | Fmax | Cost |
|---|---:|---:|
| **4 stages** (the frozen table: `kmm` 4, `kbfct`/`kbfgs` 5) | **128.125 MHz** | — |
| 3 stages (every operation finishes in four) | 97.500 MHz | **−24%** |
| 2 stages (every operation finishes in three) | 70.000 MHz | **−45%** |

One four-stage pipeline serves both numbers because **`kmm` does not use the
butterfly segment and taps out of the Montgomery one an edge early.** *The limiting
path is the Montgomery reduction in every configuration* — **not** the multiply,
which is a DSP nowhere near the critical path.

**Three arrangements were wrong before this one and none of the three was reasoned
to.** Putting Barrett *after* Montgomery rather than beside it gave 13.638 ns with
two dependent DSPs and no register between them. Pushing every constant into fabric
was right for `q` and wrong for `BARR_V`. And:

> **Vivado ignored `use_dsp = "no"` in both documented forms** — on a net declaration
> and on an `always_comb` variable. The cell histogram said `DSP48E1=4` both times and
> the resulting **4.023 ns DSP hop was the whole critical path**. *Attributes were
> asked twice and answered neither time; structure answered once.* Write the constant
> multiplies as shift-adds **derived from the constant itself**. Whether a constant
> multiply wants a DSP or fabric is decided by its **population count**: `q = 3329`
> (four set bits) belongs in fabric, `BARR_V = 20159` (eleven) does not.

**And the seventh "green over nothing", which was mine.** `cmd.exe` treats `=` as an
argument separator, so `-tclargs … STAGES=2` reached the Tcl script as **two
arguments**, `synth_design` got no generic at all, and **three "configurations" were
implemented and all three were the default** — byte-identical WNS at every search
point. **The only tell was a critical-path endpoint naming a generate block the
shallower configurations do not contain.** It gets *two* guards rather than one:
generics now travel as `NAME:VALUE`, `synth_ooc.tcl` hard-fails if trailing arguments
parse to none, and `probe_tier1.py` **refuses to report two configurations whose
netlists are identical** — a different pipeline depth cannot have the same flop count.

**The probe ships nothing and still gets a correctness test**, against the frozen
model over 2000 vectors in all three configurations, **because a datapath that
computes the wrong answer is very likely a *smaller* one — which reports a frequency
the real unit cannot reach, i.e. this step's own failure mode arriving through the
step meant to prevent it.**

Two of the nine injected faults are worth recording: an elaboration check comparing
shift positions against **a second hand-written bit list** was vacuous, and deriving
the shifts from the constant **removed the transcription instead of guarding it**;
and holding the operands steady through the stall made the testbench **blind to a
stage reading a combinational value instead of a registered one**, so the stimulus
now corrupts them after the start cycle exactly as the core's forwarding muxes do.

### A25 — The three-cycle multiply

**Before:** A14 built the multiplier as three named registers with
`MULDIV_MUL_CYCLES` hardcoded at 4 beside them, **related only by a comment**.

**Changed:** the depth is now **derived** from the latency, so the two cannot drift;
`MUL_CYCLES` is a module parameter defaulted from the package **purely so
`synth_ooc.sh` can sweep it**, nothing instantiates it differently, and
`isa_consistency` checks both halves of that.

**Out of context the unit clears 160 MHz at 4 cycles, at 3 *and* at 2**, with the
worst register-to-register path inside the DSP cascade in each case. **A14's third
stage was buying nothing this core can use, and CoreMark was paying 23 490 000 cycles
for it.**

**The answer is 3 and not 2 for a reason the same table does not contain**: at 2 the
33×33 becomes combinational into the module's `result` **port**, which an
out-of-context run with no I/O delays **does not time at all** — the endpoint moves
to the divider, **which is the tell.** *So 2 is not ruled out; it is unmeasured.*

**On the board, every delta closes exactly, and the interesting one is a zero.**
CoreMark **−23 490 000 cycles (−2.99%)**, and its multi-cycle EX stall counter fell by
the *same* number. The ML-KEM NTT saves 2688 cycles — 2688 multiplies. Its `rv32i`
build and SHAKE128 are unchanged because **neither contains a `MUL`.**

**Dhrystone gains exactly zero, and B is why** (Part I §8.5). *The same measurement at
`-march=rv32im` shows a cycle saved per run, which is what the simulation instrument
reported and what would have been believed.*

**The pre-committed consequence arrived rather than being noticed afterwards:** the
software NTT baseline got **faster** (33 946 → 31 258 cycles), so §10 M2's reported
coprocessor speedup gets **smaller** and the RV32I:RV32IM ratio *rises* 4.8828 →
5.3027. **That is the correct direction**, and `docs/a25-benchmarks.json` is now the
denominator, not A16's.

**It is not Fmax-neutral, and the multiplier is not why.** Rebuilt at A23's exact
clock with a byte-identical memory image, the netlist **misses by 0.423 ns on
`mem_wb rd_addr → pc_q`** — the core's own writeback-to-redirect path, where
`rvntt_muldiv` appears nowhere. **The comparison is one-variable**, so the move is
attributable to removing 64 flops perturbing placement. *One build cannot separate
"the change cost this" from "a different design placed differently."*

### A26 — The Fmax round

**Before:** 74.577 MHz (A23), or a netlist that missed it (A25).

**Changed:** three levers, **each measured separately at one constraint (85 MHz,
`default`) so the deltas are comparable.**

| Lever | WNS at 85 MHz | Contribution |
|---|---:|---:|
| A25, as committed | −2.562 ns | — |
| **1** — `JALR` off the ALU result mux, onto A17's address adder | −2.489 ns | **+0.073 ns** |
| **2** — the forwarding decision precomputed in ID | −1.816 ns | **+0.673 ns** |
| **5** — the predictor **holds** instead of re-looking-up | **+0.477 ns** | **+2.293 ns** |
| | | **+3.039 ns total** |

**Lever 1's number is not lever 1's value.** The document predicted ~2.7 ns and the
logic really is gone; what the probe measures is *the design's worst path*, and
lever 1 **exposed a different one that was already 14.033 ns.** *Everything after it
is measured against that newly exposed path — which is why lever 5, a lever the plan
does not list, is the largest contribution in the round.*

**Lever 5 is the one worth carrying forward** (Part I §3.3). A19 wrote
`bp_lookup_pc = front_stall ? pc_q : …` and **its own comment says why it is
correct**: *"under `front_stall` the address does not move, so the lookup simply
repeats and re-registers the same answer."* **True, and expensive** — `front_stall`
is a function of the *decoded* instruction, so it dragged the instruction memory, the
decoder and the hazard unit in front of the BTB's index mux, array read and tag
compare. **If the lookup re-registers the same answer, the register can keep it.**

**riscv-formal found a real bug in lever 2, on all 77 checks at once, and the check
that found it is the one worth copying.** Lever 2 was verified by keeping **a second
copy of the original EX-stage computation** and asserting the two agree — *not a
restatement of the transformation, but the claim that the transformation changed
nothing.*

> The structural argument — *"the same decision, one cycle earlier"* — is right about
> the edge an instruction **enters** EX and **silent about the cycles it stays
> there**. During a multi-cycle EX stall the producers drain out from under the
> consumer, and a held `FWD_MEM` reads a **bubble — zero** — which is worse than the
> register file's stale copy. **Cosimulation passes over that bug**, because the only
> reader after the start cycle is the multi-cycle unit and it does not re-read.

**Levers 3 and 4 were dropped with evidence, not omitted.** The mispredict comparison
lever 3 targets is no longer on the critical path, so it would **cost cycles for zero
benefit**; and the fanout-131 and fanout-66 nets lever 4 aims at **are gone**, the
largest fanout on the new path being 41.

**Result: 74.577 → 96.246 MHz, +29.1%, with not one cycle count changed** — checked
rather than asserted: every counter on the board is identical to A25's, and both
identity residuals are **−40 and −48**, the same two numbers A23 measured, out of a
design whose forwarding network, jump-target adder and predictor lookup were all
rebuilt.

### A27 — Floorplanning

**Answer: negative, and its premise was half wrong** (Part I §7.4). **None is
adopted.**

### A28 — The two-cycle multiply, and M7.4

**Before:** `MUL_CYCLES = 3`, with 2 recorded as *unmeasured, not ruled out.*

**Changed:** measured **in the SoC at the adopted clock**, where the port *is*
timed: **WNS +0.004 ns against 3 cycles' +0.010** — six picoseconds — for another
3.2% of CoreMark. **A25's stop rule named the wrong hazard**: there is no
data-dependent path at any latency, because the multiplier is a register chain whose
length is its latency.

**One mutation changed verdict at 2 cycles, and the reason is structural.**
`muldiv_done_one_cycle_early` was caught by `rv32um/mul` at 4 cycles because it read
the product register early. **At 2 cycles there is no product register** — `MUL_PIPE`
is 0 — so it cannot read a partial result and **degrades to a timing-only fault.**
The two span checks still catch it. **The catcher list was narrowed with that reason
written down.**

**The counter pairing is retired** (Part I §4.3): 8 of 8 rows agree.

**Result: M7.4.** Fmax **96.246 MHz** — up 29.1% from M7.3 — DMIPS **90.096**,
CoreMark **326.24**, IPC 0.8734 and 0.8689, **all 42 integer counters identical
across fifteen report blocks**, no floorplan. **§9's core-only Fmax baseline is now
96.246 MHz, not 77.501.**

### A29 — `Zkr`

**Before:** no entropy source; `misa` without `Zkr`.

**Changed:** the `seed` CSR, the ring oscillator, and the two mandatory health tests
(Part I §5.1). **The uncertified statement comes first in the write-up, before
anything else.**

The three findings that outlive the step — the adaptive test's structural blind spot,
α being *per sample*, and the ring fighting the tools in three separate ways — are in
Part I §5.1 in full.

**Result:** the ring is in a real bitstream at 96.246 MHz with **every benchmark
counter identical to A28's**. *Zkr costs no cycles.*

### A30 — `Zkt`, and M7.5

**Before:** a data-independent divider and no claim about latency.

**Changed:** the claim of Part I §5.2, with both boundaries and 37 instructions.
**Claim B is proved structurally by cone of influence** — exact rather than
depth-bounded — **and its vacuity guard earned itself immediately**: the first run
reported an empty cone because `yosys -q` had suppressed the output being parsed.

**Both claims were observed to fail** under injected faults.

**The divider's data independence is deliberately *not* filed under `Zkt`.**

**And a correction:** A21's "RISCOF 118/118" was wrong — its enumeration silently
dropped the Zicond row. **The suite total was always 120.**

**Result: M7.5**, hardware-confirmed. ISA
`RV32IMZicsr_Zicond_Zba_Zbb_Zbkb_Zbs_Zkr_Zkt`, RISCOF **120/120**, full regression
**56 PASS · 1 XFAIL · 2 SKIP · 0 FAIL**.

---

# Part III — Cross-cutting findings

Six patterns recurred often enough across thirty steps to be worth naming
separately. These are the transferable part of the project.

## 11. "Green over nothing" — a report whose green was not about the thing it named

**Seven instances, in seven different tools.** This is the single most frequent class
of defect in the entire project, and **not one of them was found by reasoning about
the code.** Every one was found by deliberately breaking the thing the check watched
and observing that the check stayed green.

| # | Step | The tool | What it reported | What was actually true |
|---|---|---|---|---|
| 1 | A10 | `riscof run` | `RISCOF_OK` | 50 tests had failed; riscof exits 0 regardless |
| 2 | A11 | `sby` | 43/43 checks pass | the core had a **deliberately broken adder**; `genchecks` writes `expect pass,fail` |
| 3 | A14 | `synth_ooc.sh` | `DSP=0` | the netlist contained **four DSP48E1s**; the filter used a *guessed* group name |
| 4 | A19 | `bench_hardware` | PASS | the fixture pointed at an **older bitstream** |
| 5 | A20 | mutation anchors | tests running | anchors were stale; mutations were `NO-OP`s |
| 6 | A21 | `run_cocotb.py` | every cocotb test PASS | a bare `return 0` **since A1**; two real failures hidden since A14 |
| 7 | A24 | `probe_tier1.py` | three configurations measured | `cmd.exe` ate the `=`; **all three were the default** |

**The rule earned by all seven:** *a tool's exit code is not its verdict unless you
have checked that it is — and **the check belongs in the runner, not in the
reader.*** Each fix puts the verdict-derivation next to the tool invocation, and each
fix was itself fault-injected.

A closely related sub-pattern: **a check that silently cannot fail on the inputs it
was handed.** A18's benchmark comparison had a fault — a rate not scaled by the clock
— that is *vacuous when the two captures share a clock*, which is the normal case.
Its selftest therefore runs against a synthetic 1.2×-clock copy. **A check that
cannot fail is the exact failure mode these tools exist to prevent, and it does not
get to do it to itself.**

## 12. Fault injection is a policy, not an activity

**Every checking mechanism in this project has been deliberately broken and observed
to report failure.** Not once at the end — as the mechanism is built.

It has caught, among others: the `kbfgs` sign error in the frozen ISA contract, a
UART banner repeating 15× too fast, a formal proof checking itself (A6), a directed
test miscredited with coverage (A6), a spec-drift check that would have passed
vacuously (A2), a decoder legality rule reachable at 0.07 hits per million random
draws (A3), the transposed LED pins (A12), all seven "green over nothing" instances,
and an RNG that would have bricked itself after eleven milliseconds (A29).

**The mutation harness is the formal version of this**, and its most valuable
property is that it reports a *declared catcher that fails to catch* as `PARTIAL`
rather than as a pass. That is how it finds **tests**, not designs. Current state:
**90 mutations, all caught as declared.**

**Three refinements the harness needed, each learned the hard way:**
- **An ambiguous anchor is worse than a missing one**, because a missing one is
  reported and an ambiguous one is *obeyed*. `mirror_rtl` now requires exactly one
  match.
- **A mutation that cannot fail is not evidence.** One BTB mutation was *removed
  entirely* because an unwritten entry reads as an all-zero tag and nothing here
  executes below address 0x400.
- **A mutation that does not compile is rejected**, correctly — Verilator's `-Wall`
  makes an unused signal a build error, so several mutations had to be reformulated
  as an off-by-a-shift or an inverted sense rather than a deletion.

## 13. When a mutation escapes, suspect the stimulus before the checker

**This diagnosis was right five times out of five.**

| Step | The escape | The stimulus defect |
|---|---|---|
| A5 | byte load not sign-extending | scratch memory was all zero |
| A5 | shift amount `b[5:0]` vs `b[4:0]` | almost every register held 0 or 1 |
| A7 | interlock keying on the `rs1` field | needs a `LUI` whose imm bits name a just-written register — 0.5% per load |
| A12 | MMIO store not gated from RAM | aliases onto `crt0`, which never re-executes |
| A17 | address operand not forwarded | **every** memory access used `x8`, which is never written |
| A19 | **all eight** predictor mutations | generated programs are forward-branch-only straight lines: **every transfer site executes at most once** |

**A19's is the clearest.** The 1000-program cosimulation still proves the predictor
is *architecturally invisible*, which is its main risk — and it is **structurally
unable to say whether the predictor works.** Two different tests, two different
claims, and conflating them would have shipped an untested predictor behind a green
suite.

## 14. Measurement discipline

### 14.1 Fmax

**A loose constraint is not a measurement.** A17 quantified this: the same design
judged by slack at an old constraint reported **+0.18 ns**; judged by a binary search
it was **+1.589 ns**. The router optimises to the constraint and stops, so *the slack
it leaves measures when it stopped.*

`1/(T − WNS)` from a passing run is reported nowhere in this project.

**The design-to-design spread is real and has been quantified three times**, and it
widens as the design gets faster:

| Step | Spread in implied path delay | Cause |
|---|---:|---|
| A12 | ±0.4 ns | first measured by correcting **two output pins with no logical content** |
| A19 | 0.9 ns | search **not monotonic**: 79.246 MHz failed by −1.186 while the tighter 80.998 failed by −0.676 |
| A26 | **1.63 ns** | search *was* monotonic; the router optimises to the constraint and stops |

Every Fmax figure in this project is therefore quoted as **"the fastest constraint
observed to pass"**, never as a boundary.

### 14.2 Cycle counts

Cycle-neutrality is **checked, not asserted.** A17, A19's two levers, A23's two
counter fixes and all of A26 each carried a "not one cycle count may change"
done-when, and each was verified by comparing every counter in every region — 32,
36, 42, 44 and 52 counters at various points, **0 differing**.

**This is the discipline that let A26 claim +29.1% Fmax credibly**, because the same
comparison that proves the levers are free would have caught one that was not.

### 14.3 Isolating a variable

**Two builds could not separate Zicond from B** (A22), and it took a third that
exists for no other purpose. **A25's Fmax move could not be separated from
placement** by one build, and it said so rather than guessing. **The compiler's use of
B was verified by following the call graph** after an address-range attribution got it
backwards.

**And the negative results are reported at the same length as the positive ones**:
A17's two dead levers, A26's two dropped levers, A27's rejected floorplans, A22's
Zicond, A24's three wrong arrangements.

## 15. State the invariant, not the conclusion

**A15's lesson, and it generalised twice.**

When a property asks a solver to relate two circuits that compute the same
arithmetic, **state the invariant they both maintain rather than the conclusion they
both reach.** The divider's obvious correctness statement is multiplier equivalence
and did not return in fifteen minutes; the same statement as a **per-iteration
invariant** proves in four.

**A26 applied the same idea to a transformation.** Lever 2 was verified not by
restating what the transformation does, but by keeping a second copy of the
computation it replaced and asserting the two agree — **the claim that the
transformation changed nothing.** That is what found the multi-cycle-stall bug the
structural argument was silent about.

**A30 applied it again, to a security property.** Claim B is proved by **cone of
influence** — a structural statement about what `done` can possibly depend on —
rather than by bounded model checking of what it does depend on within 14 cycles.
**Exact rather than depth-bounded, total over operands, and it fails by naming the
operand.**

## 16. Structure controls what attributes do not

**A24's finding, and it is the one most likely to transfer to other FPGA work.**

Vivado ignored `use_dsp = "no"` in **both** documented forms — on a net declaration
and on an `always_comb` variable. The cell histogram said `DSP48E1=4` both times, and
the resulting 4.023 ns DSP hop was the entire critical path. **Attributes were asked
twice and answered neither time; structure answered once.**

Write the constant multiplies as shift-adds derived from the constant itself. And
the decision of *which* constant belongs in fabric is made by **population count**:
`q = 3329` has four set bits and belongs in fabric; `BARR_V = 20159` has eleven and
does not — its shift-add tree measured *worse* than the DSP it replaced.

The same lesson has a counterpart in A29, where three *different* attributes were
each genuinely necessary and each stopped a different tool from doing a different
thing (Part I §5.1). **The general form: when a tool is not doing what an attribute
asks, find out which of the tool's several passes is responsible before adding a
second attribute.**

---

# Part IV — Artefacts

## 17. Where everything lives

| Artefact | Path |
|---|---|
| **This document** | `docs/core-report-a30.md` |
| **Roadmap (companion)** | [`docs/core-roadmap.md`](core-roadmap.md) |
| The plan — **never edited** | `docs/RISC-V_NTT.txt` |
| Track A modifications, round 1 (A14–A19) | `docs/RISC-V_NTT_MODS_A.txt` |
| Track A modifications, round 2 (A20–A30) | `docs/RISC-V_NTT_MODS_A2.txt` |
| The frozen `Xkntt` contract | [`docs/isa-spec.md`](isa-spec.md) |
| Branch predictor specification (implemented twice) | [`docs/a19-bpred-spec.md`](a19-bpred-spec.md) |
| RISCOF compliance report, 120/120 | [`docs/riscof-report.html`](riscof-report.html) |
| Board bring-up and the JTAG/UART loop | [`docs/fpga-bringup.md`](fpga-bringup.md) |
| Spike fork | [`docs/spike-xkntt.md`](spike-xkntt.md) |
| `.insn` bridge | [`docs/insn-bridge.md`](insn-bridge.md) |
| Kyber backends | [`docs/kyber-backends.md`](kyber-backends.md) |
| Patch discipline | [`docs/patch-discipline.md`](patch-discipline.md) |

**Per-step records**, each with a `.md` write-up and most with a machine-readable
`.json`:

| Step | Document | Data |
|---|---|---|
| A12 | — | [`a12-fmax.json`](a12-fmax.json) |
| A13 | [`a13-benchmarks.md`](a13-benchmarks.md) | [`.json`](a13-benchmarks.json) |
| A16 | [`a16-benchmarks.md`](a16-benchmarks.md) | [`.json`](a16-benchmarks.json) |
| A17 | [`a17-fmax.md`](a17-fmax.md) | — |
| A18 | [`a18-stalls.md`](a18-stalls.md) | — |
| A19 | [`a19-bpred.md`](a19-bpred.md), [`a19-benchmarks.md`](a19-benchmarks.md) | [`.json`](a19-benchmarks.json) |
| A20 | [`a20-counters.md`](a20-counters.md) | — |
| A21 | [`a21-bitmanip.md`](a21-bitmanip.md) | — |
| A22 | [`a22-zicond.md`](a22-zicond.md) | — |
| A23 | [`a23-benchmarks.md`](a23-benchmarks.md) | [`.json`](a23-benchmarks.json) |
| A24 | [`a24-tier1-probe.md`](a24-tier1-probe.md) | [`.json`](a24-tier1-probe.json) |
| A25 | [`a25-multiply.md`](a25-multiply.md) | [`.json`](a25-benchmarks.json), [`probe`](a25-muldiv-probe.json) |
| A26 | [`a26-fmax.md`](a26-fmax.md) | [`.json`](a26-fmax.json) |
| A27 | [`a27-floorplan.md`](a27-floorplan.md) | — |
| A28 | [`a28-benchmarks.md`](a28-benchmarks.md) | [`.json`](a28-benchmarks.json) |
| A29 | [`a29-zkr.md`](a29-zkr.md) | [`.json`](a29-benchmarks.json) |
| A30 | [`a30-zkt.md`](a30-zkt.md) | — |

**Per-directory engineering guides** — these carry the traps, not the summaries:
`rtl/core/CLAUDE.md` (1492 lines), `rtl/soc/CLAUDE.md` (591), `rtl/probe/CLAUDE.md`
(55), and the root `CLAUDE.md`.

**Source inventory:** 7579 lines of RTL across 30 files; 25 248 lines of
testbench/harness; 4177 of software; 2153 of FPGA scripting; 1975 of golden model.

**Tags:** `m3-tier1-kat`, `m4-pipeline-cosim`, `m5-riscof-compliance`,
`m6-riscv-formal`, `m7-fmax-and-benchmarks`, `m7p1-rv32im-benchmarks`,
`m7.2-bpred-77mhz`, `m7.3-bitmanip-hw`, `m7.4-fmax-96`, `m7.5-zkr-zkt`.

## 18. Reproducing the numbers

```sh
source toolchain/env.sh          # WITHOUT THIS, MISSING TOOLS REPORT **SKIP, NOT FAIL**

python3 tb/run_regress.py                        # full: 1230-1470 s
RVNTT_FAST=1     python3 tb/run_regress.py       # edit loop: 249 s (announces itself)
RVNTT_NO_MUTATE=1 python3 tb/run_regress.py      # after an RTL change: 793 s
RVNTT_HW=1       python3 tb/run_regress.py -k fpga   # needs the board

python3 fpga/scripts/fmax_search.py --probe 96.25   # one run: a BOUND, not an Fmax
python3 fpga/scripts/hw_bringup.py                  # program + capture + parse, no human
python3 tb/formal/run_zkt.py --list                 # the Zkt instruction tables
python3 tb/mutate/run_mutation.py --check-anchors   # 0.03 s pre-flight
```

**Vivado runs on Windows over the WSL interop socket and needs the sandbox disabled;
`sudo` has no TTY in this environment.** Both constraints are documented in the root
`CLAUDE.md`.

## 19. Milestone status

| | Milestone | Status |
|---|---|---|
| M0 | `make regress`; blinky+UART bitstream | ✅ hardware-confirmed |
| M1 | `isa-spec.md` frozen, every instruction hand-encoded | ✅ |
| M2 | Python and C golden models agree bit-exactly | ✅ 1261 polynomials |
| M3 | Spike executes `Xkntt`; ML-KEM keygen passes | ✅ full 10 000-vector KAT |
| M4 | 1000 random programs, lockstep cosim vs. Spike | ✅ at max hazard density |
| M5 | RISCOF RV32I compliance | ✅ 38/38 `I` + hints + privilege |
| M6 | riscv-formal | ✅ 77/77 at depth 14 |
| M7 | Core-only bitstream: Fmax + Dhrystone + CoreMark | ✅ hardware-confirmed |
| M7.1 | RV32IM verified and re-measured | ✅ hardware-confirmed |
| M7.2 | Fmax recovered, branch prediction measured | ✅ hardware-confirmed |
| M7.3 | B, Zicond and hardware counters | ✅ hardware-confirmed |
| M7.4 | Faster multiply and the Fmax the coprocessor inherits | ✅ hardware-confirmed |
| M7.5 | `Zkr` and `Zkt` | ✅ hardware-confirmed |
| **M8–M16** | **The coprocessor, the compiler, integration** | **not started** |

**Track A is complete. The next milestone, M8, is Track B's first: "Montgomery
multiplier passes exhaustive test over all valid inputs."** A24 has already measured
that the Tier-1 unit closes at 128.125 MHz on this part — **the core's 96.246 MHz
clears it with 33% margin** — so the host is not the constraint.

**One gate is already known and must not be forgotten:** plan C6 is only *partially*
done. There is no TIER2 backend, no RTL verification of the NTT, and no `make KAT`
target. **Any milestone whose criteria depend on the full three-backend suite — M14
in particular — must not be marked complete.**
