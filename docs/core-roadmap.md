# Beyond A30 — Where the `rvntt` Core Can Go

### A prioritised roadmap for the post-Track-A RV32IM_B_Zicond_Zkr_Zkt pipeline

**Companion to:** [`docs/core-report-a30.md`](core-report-a30.md), which establishes
the current state and is the source of every measurement quoted here.
**Baseline:** commit `5d6540a`, tag `m7.5-zkr-zkt` — 96.246 MHz, IPC 0.8734/0.8689,
5502 LUTs, 8.7% of an `xc7a100tcsg324-1`.

> **Published, illustrated version:** <https://claude.ai/code/artifact/e543b2d8-d3ad-440f-b70e-bcf98e037096>
> Companion report: <https://claude.ai/code/artifact/6f307952-fb8a-40cb-a73c-c4490ffe4fe0>

---

## 0. How to read this document

This is a *menu with prices*, not a plan. Thirty-one proposals are grouped into five
tiers by what they are *for*, and each is costed against the same six axes so that
options in different tiers can be compared honestly. Nothing here is scheduled;
§10 offers a sequencing argument and §11 the dependency graph, but the ordering is a
recommendation, not a commitment.

**Two things are done differently from the usual roadmap.**

First, **every estimate is traced to a measurement in the companion report or is
explicitly labelled as an estimate.** Where a proposal's benefit is bounded above by
something already measured, the bound is given instead of a guess. Where nobody has
measured the relevant quantity, the proposal says *that* — and in three cases the
honest first move is a measurement rather than a change.

Second, **§9 lists things that look attractive and should not be done**, with the
evidence against them. A roadmap that only proposes is less useful than one that also
forecloses.

---

## 1. The framing: how much headroom actually exists

Before any individual proposal, the arithmetic that bounds all of them.

### 1.1 The single-issue ceiling

The core is single-issue and in-order. Its IPC is **0.8734** (Dhrystone) and
**0.8689** (CoreMark). **A perfect machine of this shape has IPC 1.0.** So:

> **Every microarchitectural improvement short of changing the issue width — better
> branch prediction, a shorter multiply, a faster divider, a cleverer forwarding
> network, all of them together and perfectly — is worth at most 1.145× on Dhrystone
> and 1.151× on CoreMark.**

That is the whole budget, and it decomposes exactly, because the cycle identity closes:

| Where the non-retired cycles go | Dhrystone | CoreMark | Best case if eliminated entirely |
|---|---:|---:|---|
| Load-use interlock | **4.28%** | **7.02%** | CoreMark's largest term |
| Multi-cycle EX stall | **5.43%** | **3.18%** | Dhrystone: *all divide*. CoreMark: *all multiply* |
| Flush (2 × redirect) | **2.96%** | **2.91%** | a perfect predictor saves exactly this |
| **Total recoverable** | **12.66%** | **13.11%** | |

Two consequences that are easy to get wrong:

- **CoreMark's largest remaining term is the load-use interlock, not branches.** The
  branch predictor was the right thing to build at A19 — flushes were then 18–28% of
  cycles — and it has done its job. Proposing a fancier predictor *now* is proposing
  to attack a 2.9% term.
- **Dhrystone's multi-cycle stall is one divide per run and no multiplies at all**,
  because B strength-reduced its multiply into `sh*add`. Its 5.43% is a *divider*
  number. CoreMark's 3.18% is a *multiplier* number — one stall cycle per multiply at
  `MUL_CYCLES = 2`.

### 1.2 The clock ceiling

A24 measured a representative Tier-1 `Xkntt` butterfly at **128.125 MHz** out of
context on this part and speed grade. Since §3.3 of the modification document
established that Tier 1 **cannot** be in its own clock domain, that is the ceiling the
core's clock inherits. With A24's pre-stated 10% margin the core's limit is
**116.5 MHz**.

> **The core is at 96.246 MHz, so there is 21.0% of clock headroom before Track B
> becomes the constraint — and 33.1% before the raw probe number does.**

### 1.3 The combined ceiling

**1.386× (Dhrystone) and 1.393× (CoreMark) is the absolute limit of this machine's
shape**: perfect IPC at the highest clock Track B permits. Anything beyond that
requires a different machine — wider issue, a deeper pipeline, or a faster part.

**This bound is the single most useful number in this document.** It says that the
entire remaining space of conventional optimisation is worth about 39%, and that the
project's actual goal — a hardware NTT — is worth **5.8× on the ML-KEM kernel before
the coprocessor has computed anything**, because that is the measured `rv32i`:`rv32im`
ratio and the coprocessor's target is far beyond `rv32im`.

### 1.4 What the project is actually for

`rvntt` is not a general-purpose core being optimised for its own sake. It is the
**host** for an ML-KEM-768 NTT coprocessor, and its remaining job is to *not be the
bottleneck*. Every proposal below is scored on **relevance to that goal** as well as
on its own merits, and the two frequently disagree — a proposal can be excellent
engineering and nearly irrelevant.

**The measured Amdahl terms are already known**, which is unusual this early:

| Term | Measured | Source |
|---|---:|---|
| ML-KEM NTT, `rv32i` → `rv32im` | **5.802×** cycles | A28 board |
| Keccak (SHAKE128), B and Zbkb | **1.1953×** cycles | A23 board |
| NTT as a fraction the coprocessor can attack | 28 570 cycles on the shipped ISA | A28 board |
| Keccak as the fraction it **cannot** | 312 402 cycles | A23 board |

**Keccak is 10.9× more cycles than the NTT on the shipped ISA.** That ratio is the
single most important strategic fact in this document and §7.1 acts on it.

---

## 2. Scoring

Every proposal carries the same six axes.

| Axis | Scale |
|---|---|
| **Relevance** | how much it advances the project's actual goal (M8–M16). ★☆☆☆☆ … ★★★★★ |
| **Urgency** | whether delaying it costs more later. `now` / `soon` / `whenever` / `after B` |
| **Effect** | the estimated quantitative improvement, with its bound where one exists |
| **Difficulty** | `S` (≤1 day) · `M` (2–5 days) · `L` (1–3 weeks) · `XL` (months) · `R` (research) |
| **Risk** | what can go wrong, and whether the failure is loud or quiet |
| **Depends on** | other proposals, by number |

**A note on "Risk".** The most dangerous risk in this project has never been "it does
not work" — that is loud. It has been "**it appears to work**": seven instances of a
report whose green was not about the thing it named. Every proposal's risk entry is
therefore written in terms of *how the failure would present*, and a **quiet** failure
mode is weighted far more heavily than a loud one.

---

## 3. Tier 0 — On the critical path to the project's goal

These are not optional. They are the remaining plan, M8 through M16, and everything in
Tiers 1–4 is a detour from them.

### P1 — Track B, Tier 1: the butterfly as an EX functional unit
> **Relevance ★★★★★ · Urgency `now` · Effect: unlocks M8–M10, M13 · Difficulty L · Depends on: —**

**What.** Implement `kmm`, `kbfct`, `kbfgs`, `kbmul0`, `kbmul1` and `kmac` as a
multi-cycle EX unit satisfying the frozen latency contract (4/5/5/9/5/6 cycles of EX
occupancy).

**Why now.** Everything needed is already in place, and that was deliberate:

- **The generic `req`/`done` multi-cycle EX handshake exists** and has been in silicon
  since A14. It was built *before* the instructions that use it, specifically for this,
  and `M` was its first user only because `M` arrives with external references.
- **The register file has had three read ports since A1**, for `kbmul0` and `kmac`'s
  `rs3`. That cost one distributed-RAM copy and has cost nothing since.
- **The frequency is measured, not assumed.** A24 says 128.125 MHz for the arrangement
  the frozen table asks for; the core runs at 96.246. **33.1% margin.**
- **The frozen table is measured to be right.** Fusing so every operation finishes in
  four costs 24%; in three, 45%. The limiting path is the Montgomery reduction, not the
  multiply.
- **The decoder already accepts the encodings, and riscv-formal has already found one
  bug about them** — the A11 forwarding/writeback disagreement on `RES_XKNTT`.
- **Spike already executes the extension** and passes a full 10 000-vector KAT.

**Effect.** Plan §B6 projects the kernel speedup; the honest denominator is now
**28 570 cycles** for the `rv32im` software NTT (A28). This is the step the entire
project exists for.

**Risk.** *Quiet.* An `Xkntt` unit that computes a slightly wrong answer will pass every
existing test in the tree, because **no existing test executes an `Xkntt` instruction.**
The mitigations are all pre-built: `model/isa/xkntt.py` is frozen and bit-exact against
the C reference over 1261 polynomials; Spike is the cosimulation reference; and A24's
probe testbench already demonstrates the *specific* stimulus trap — **holding operands
steady through a stall makes the testbench blind to a stage reading a combinational
value instead of a registered one.** Corrupt the operands after the start cycle.

**Do not skip the cycle model.** A phantom stall in the new unit changes no
architectural state, so the commit log stays byte-identical. Only
`tb/cosim/cycle_model.py` sees it, and its `Sum(latency − 1)` term must learn the new
latencies.

---

### P2 — Track C: the LLVM backend, and the fourth member of the four-way agreement
> **Relevance ★★★★★ · Urgency `soon` · Effect: unlocks M11, M12 · Difficulty L · Depends on: —** (parallel with P1)

**What.** The `Xkntt` target description in LLVM: MC-layer encodings, Clang builtins,
intrinsics, and — critically — the `SchedMachineModel`.

**Why now, and why the `SchedModel` is the load-bearing half.** The project defines a
**four-way agreement** between the RTL decoder, `model/isa/xkntt.py`, Spike, and the
LLVM `SchedModel`. **Three of the four exist. The fourth does not, and it is the one
that carries the latency contract.**

> A stale scheduling model produces code that stalls on real hardware, **and the
> symptom appears nowhere near the cause.** When the latency table changes it must
> change in the RTL, Spike and the `SchedModel` **together, in one commit** — never one
> at a time. Right now there is nothing to change it in, which means the discipline
> cannot be exercised and its absence is invisible.

**Effect.** Removes the `.insn` bridge from the critical path of every Track B
experiment. `llvm-lit` + `FileCheck` gives `# CHECK-ENCODING:` assertions on the exact
bit pattern — plan §0 calls this "genuinely the load-bearing artifact of the whole
toolchain track", and it becomes the contract both the RTL decoder and Spike must
satisfy.

**Risk.** *Loud and quiet in different halves.* Encoding errors are loud —
`FileCheck` catches them. **Scheduling-model errors are silent**: the code is correct
and merely slower, exactly the class A7's cycle model exists to catch on the RTL side.
The mitigation is to make the `SchedModel` a *generated* artefact from the same source
as `docs/isa-spec.md`'s latency table, not a transcription. (A21's encoding tables are
already generated by the assembler rather than typed, after typing them put four
instructions in the wrong opcode.)

**Pros/cons.** LLVM was chosen over GCC in plan §0 for four reasons that still hold —
one codebase, a clean intrinsic path, dozens of in-tree vendor-extension examples, and
`llvm-lit`. The con is that the LLVM fork is pinned at `llvmorg-22.1.8` with an empty
`xkntt` branch, so this is greenfield: **there is no partial work to build on.**

---

### P3 — Complete plan C6: the TIER2 backend, RTL NTT verification, and `make KAT`
> **Relevance ★★★★★ · Urgency `soon` · Effect: unblocks M14 · Difficulty M · Depends on: P1 (partially)**

**What.** The three-backend Kyber build (`SW`, `TIER1`, `TIER2`) with a `make KAT`
target and RTL-level verification.

**Why now.** **C6 is only partially done, and it gates more than it appears to.** Any
milestone whose criteria depend on the full three-backend suite — **M14 in
particular** — must not be marked complete until it is. This is recorded in the root
`CLAUDE.md` and is the kind of gate that is easy to forget and expensive to discover.

**Risk.** *Quiet.* A partially-implemented backend suite makes M14 look claimable. The
mitigation is the one already in place: the gate is written down.

---

### P4 — Tier 2: the coprocessor, its buffers, and integration
> **Relevance ★★★★★ · Urgency `after B` · Effect: M14, M15 · Difficulty XL · Depends on: P1, P3**

**What.** The memory-mapped Tier-2 coprocessor: `kntt.cfg` / `kntt.start` / `kntt.wait`
/ `kntt.stat` in `custom-1`, with its own coefficient RAM.

**Why the memory budget was preserved for this.** A17 measured that halving the SoC
memory to 64 KB reaches **≥90.0 MHz on 16 BRAM tiles** — a ~4% clock gain — **and did
not adopt it**, explicitly because *"the Tier-2 coprocessor's buffers do not exist yet
and cannot be sized against an array already given away."* That decision is now due to
be cashed in. **32 of 135 BRAM tiles are in use; 103 are free.**

**Effect.** M14/M15, and plan §10's headline numbers.

**Risk.** *Loud, but late.* This is where §9's failure mode — "the integrated bitstream
does not meet timing" — would fire, which is why A24 measured the Tier-1 floor two
tracks early. Tier 2 is memory-mapped rather than an EX unit, so it **may** be in its
own clock domain; that is a genuinely open architectural question and should be settled
by measurement the way A24 settled Tier 1's, **before** committing.

**Dependency worth naming:** `kntt.wait` is a blocking poll today. If Tier 2 runs long
enough to be worth doing something else during, it wants an **interrupt** — which is
P8, and which does not exist.

---

## 4. Tier 1 — Short-term, practical, low risk

Small, contained, and each fixes something the report identifies as an actual gap.

### P5 — Move the HPM event decode off the critical path (third time)
> **Relevance ★★☆☆☆ · Urgency `soon` · Effect: bounded by the next path — see below · Difficulty S · Risk low · Depends on: —**

**What.** The shipping A29 bitstream's worst path is
`id_ex_q[insn][30] → CSR read → CSR write data → mhpmevent decode →
hpm_watch_q[3][1]/CE` — **9.808 ns, 9 logic levels, 84% route.** In plain terms: a CSR
instruction's own read-modify-write feeding the performance counters' event-selection
logic.

**Why now.** **This is the third time A20's counters have appeared on or near the
critical path.** A23 found their 6:1 event mux after `ex_redirect` and fixed it twice —
a registered one-hot mask, then registering the event bus as well — and both fixes were
**provably cycle-neutral**. The same technique applies: register the event-selection
mask so the CSR write data does not reach a clock enable combinationally.

**Effect — and this is where honesty matters.** The gain is **bounded by the next
worst path, which is already known.** The A26 Fmax netlist at the *same* 96.246 MHz
constraint closed on a different path entirely: `u_ram` BRAM output →
`id_ex_q[pred_target]`, **10.358 ns**. So fixing the HPM path exposes a path that is
*longer* in that netlist. **The realistic outcome is a few percent, not another 29%**,
and the honest framing is: *the core's own datapath is no longer the limit — a piece of
instrumentation and the instruction-fetch-to-predictor path are.*

**Pros/cons.** Cheap, contained, and there is a proven cycle-neutral technique. Against
it: the benefit is small and may be entirely absorbed by the placement spread, which
A26 measured at **1.63 ns** across six runs. **A single build cannot distinguish a
0.3 ns gain from placement noise** — this needs `fmax_search.py --probe` at a fixed
constraint, before and after, and the result stated as a probe (a bound) rather than as
an Fmax.

**Risk.** *Loud.* Cycle-neutrality is checked by comparing all 42 counters; a
regression shows up as a differing counter.

---

### P6 — Attack the BRAM → predictor path
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: est. 3–8% clock · Difficulty M · Risk medium · Depends on: P5**

**What.** `u_ram/mem_reg/CLKBWRCLK → id_ex_q[pred_target]` — 10.358 ns, 8 levels, 33%
logic / 67% route. Instruction memory output, through decode, into the predictor's
target field in ID/EX.

**Why.** Once P5 lands, this is the limit. It is a *fetch-to-decode-to-predictor* path,
which is structurally different from everything A26 attacked (all of which were in EX
or the PC path).

**Candidate levers, in the order the evidence suggests:** the `pred_target` field may
not need to be written in the same cycle it is decoded; the BTB's output register could
be moved a stage; or the decode of what makes an instruction a control transfer could
be narrowed. **None of these has been probed**, so the honest first move is one
`--probe` run per lever, exactly as A26 did — *each measured separately at one
constraint so the deltas are comparable.*

**Risk.** *Quiet if done carelessly.* Anything that touches the predictor's timing risks
changing cycle counts, and A26's experience is that the structural argument for a
predictor change can be **right about the edge an instruction enters a stage and silent
about the cycles it stays there.** The mitigation is A26's own: **keep a second copy of
the original computation and assert the two agree** under `RISCV_FORMAL`, so all 77
checks prove it at depth 14.

---

### P7 — A flash image, so configuration survives a power cycle
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: qualitative · Difficulty S · Risk low · Depends on: —**

**What.** Write the bitstream to the Arty's QSPI flash so the board boots standalone.

**Why.** Recorded as a known gap since A12. Costs nothing today because every
measurement is taken over JTAG anyway, **and costs a great deal the first time this
board needs to be demonstrated, left running, or used somewhere without a host.**

**Risk.** *Low and loud.* A flash image that does not boot does not boot.

---

### P8 — Interrupts
> **Relevance ★★★☆☆ · Urgency `after B` · Effect: qualitative, but a Tier-2 dependency · Difficulty M · Risk medium · Depends on: —**

**What.** `mie` is writable storage and `mip` reads a hard zero — `rvntt_csr.sv`'s own
comment says *"no interrupt sources exist"*, and the core has no `irq` port at all. Add
at least a timer and an external source, and the trap-entry path for asynchronous causes.

**Why.** Two reasons, one of which is on the project's critical path:

1. **Tier 2's `kntt.wait` is a blocking poll.** If the coprocessor runs long enough for
   the core to usefully do something else, it wants a completion interrupt. That is an
   architectural decision for P4 and it should be made deliberately, not by default.
2. Nothing else in the design is a system in the ordinary sense without them.

**Risk.** *Quiet, and this is the one to be careful with.* An asynchronous trap breaks
the invariant every part of this design leans on: **every trap resolves in EX.** That
invariant buys three things — nothing older than EX is ever squashed, a faulting store
is suppressed before the RAM latches, and a faulting instruction never retires, which
keeps the commit log line-for-line comparable with Spike. **An interrupt is not tied to
an instruction's own execution**, so where it is taken is a new decision, and taking it
in the wrong place would break cosimulation in a way that presents as an unexplained
divergence. riscv-formal has no CSR or trap models enabled, so it will not catch this.

**Prerequisite:** decide and *write down* the interrupt-entry point before implementing,
the way A19's predictor specification was written before the RTL.

---

### P9 — Stabilise the CoreMark iteration count
> **Relevance ★☆☆☆☆ · Urgency `whenever` · Effect: methodological · Difficulty S · Risk low · Depends on: —**

**What.** CoreMark's iteration count has moved 800 → 2200 → 2500 → 3500 across A13, A16,
A19/A23/A25 and A28, every time because the core got faster and the run fell under
CoreMark's own **10-second reporting minimum**. Each move makes **raw cycle counts
non-comparable across those steps** while `CoreMark/MHz` and IPC stay comparable.

**Fix.** Compute the iteration count from the *previous* step's measured
cycles-per-iteration and the target clock, so the run lands at a fixed wall-clock
duration (say 12 s) automatically. Keep the parser's 10-second enforcement — **using
`--allow-short` would be the same error as reporting Dhrystone's overflowed
`Dhrystones_Per_Second`, which A13 fixed rather than ignored.**

**Risk.** *Low.* The failure is a refused capture, which is loud by design.

---

### P10 — Explain the 2000-run Dhrystone simulation anomaly
> **Relevance ★☆☆☆☆ · Urgency `soon` · Effect: none directly — but it is an unexplained discrepancy in a model everything trusts · Difficulty M · Risk low · Depends on: —**

**What.** Dhrystone *in simulation* at 2000 runs costs **615.048 cycles/run**, where
4000 runs, 8000 runs and the board at 200 000 runs all agree on **~609.0**. The
2000-run point carries **exactly 3 extra mispredicts per run**. It is not the image
configuration, it is not a fixed warm-up cost, A18 shows no such effect, **and it is
unexplained.**

**Why it matters more than its size suggests.** `model/bpred.py` and
`tb/cosim/cycle_model.py` are the tools that will validate P1's Tier-1 latencies and
P4's coprocessor timing. **An unexplained 1% discrepancy in the predictor model is a
1% discrepancy you cannot distinguish from a real Tier-1 bug later.** Every other
closure in this project is exact — residual *zero*, counters *identical*, reports
*byte-identical* — and this is the only open numerical loose end.

**Suggested first move:** the anomaly is 3 mispredicts per run, which is small and
periodic. Diff the `model/bpred.py` trace against the RTL redirect trace at 2000 and
4000 runs and find the first divergent transfer. The infrastructure to do that exists.

---

## 5. Tier 2 — Performance and microarchitecture

Each of these is bounded by §1.1's budget. **Read that section before costing any of
them.**

### P11 — Reduce the load-use interlock penalty
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: ≤ 7.02% of CoreMark cycles, ≤ 4.28% of Dhrystone · Difficulty M–L · Risk medium · Depends on: P5, P6**

**What.** The load-use interlock is now **CoreMark's largest non-retired term** at
7.02%. A load in EX whose `rd` is read in ID costs one cycle.

**Why it was built this way, and why revisiting it is legitimate *now*.** A load in MEM
is deliberately **not** a forwarding source. Forwarding it would put
`BRAM → sign-extend → forwarding mux → ALU → BRAM address` inside one cycle — *the worst
path in the design*. **The interlock and the `FWD_MEM` exclusion are two halves of one
decision and neither is correct alone.**

That reasoning was correct when the ALU path *was* the critical path. **It no longer
is.** A26 rebuilt the forwarding network, the jump-target adder and the predictor
lookup, and the current worst path is a CSR/HPM path with the BRAM→predictor path
behind it. **Whether the load-forward path is now affordable is an open, answerable
question that nobody has asked since A26 changed the answer's premises.**

**Suggested first move — and it is a measurement, not a change.** Build the
load-forwarding path, *do not commit it*, and run `fmax_search.py --probe 96.25`. If it
still costs more than a nanosecond, the answer is unchanged and the experiment cost one
implementation run.

**Risk.** *Loud on timing, quiet on correctness.* If it closes, the correctness
argument is subtle: the interlock also interacts with `id_stall`'s disjointness from
`ex_stall`, which is a proved invariant (`a_stalls_are_disjoint`) that twelve
riscv-formal checks depend on being reachable. **Changing the interlock changes that
proof's premises.**

---

### P12 — A faster divider
> **Relevance ★☆☆☆☆ · Urgency `whenever` · Effect: ≤ 2.71% of Dhrystone cycles at radix-4 · Difficulty M · Risk medium · Depends on: —**

**What.** The divider is radix-2 restoring, 34 cycles. Radix-4 would roughly halve it.

**Why the effect is small and precisely known.** Dhrystone's multi-cycle EX stall
counter is **66 000 000 over 2 000 000 runs — exactly 33 per run: one divide and no
multiplies at all.** So halving the divider saves at most ~2.71% of Dhrystone cycles
and essentially nothing on CoreMark.

**The constraint that must not be broken.** The divider is **data-independent by
construction**, and that is not an accident — `tb/cosim/cycle_model.py` is *unbuildable*
otherwise, because it predicts cycle spans from the retired instruction stream alone. A
radix-4 divider with an early-out or a variable-iteration count **would break the cycle
model, not just the timing claim.** (It would also be exactly the fault A30 injected to
prove the `Zkt` cone-of-influence check works: a data-dependent early-out in the
multiplier was caught by reporting 2 operand bits in `done`'s fan-in. The same check
covers the divider.)

**Verdict.** Legitimate but low-value. **Do it only if the divider is on the critical
path**, which it currently is not — although A25 recorded that at `MUL_CYCLES = 2` the
out-of-context endpoint *moved to the divider*, so it is closer than it was.

---

### P13 — A one-cycle multiply
> **Relevance ★☆☆☆☆ · Urgency `whenever` · Effect: ≤ 3.18% of CoreMark cycles · Difficulty S · Risk: costs clock · Depends on: —**

**What.** `MUL_CYCLES = 1`, removing CoreMark's remaining one-stall-cycle-per-multiply.

**Why it probably does not work, stated from measurement.** A28 measured
`MUL_CYCLES = 2` in the SoC at the adopted clock at **WNS +0.004 ns** against 3 cycles'
+0.010 — *six picoseconds of margin.* At 1 cycle the 33×33 multiply becomes fully
combinational within a single EX cycle, into a path that already has essentially no
slack. **The expected outcome is that it does not close at 96.246 MHz.**

**But it is cheap to find out**, because `MUL_CYCLES` is already a module parameter that
`synth_ooc.sh` can sweep — A25 made it one for exactly this reason, and
`isa_consistency` checks that nothing instantiates it with a non-default value.

**Note the structural gotcha A28 found:** at `MUL_CYCLES = 2` there is no product
register, so `muldiv_done_one_cycle_early` **degrades from a functional fault to a
timing-only one** and its catcher list had to be narrowed. At 1 cycle the same reasoning
applies more strongly, and the mutation manifest would need another look.

---

### P14 — A better branch predictor (gshare / TAGE / a larger BTB)
> **Relevance ★☆☆☆☆ · Urgency `whenever` · Effect: ≤ 2.96% / 2.91% of cycles · Difficulty M–L · Risk: costs clock · Depends on: P6**

**What.** Replace the 2-bit bimodal direction predictor with a history-indexed scheme.

**Why the ceiling is low, and why this is the most over-proposed idea on the list.**
Flushes are now **2.96%** of Dhrystone cycles and **2.91%** of CoreMark's. **A
perfect predictor — zero mispredicts, ever — saves exactly that.** The predictor was
worth building at A19 because flushes were then 18–28%; it has already collected
almost all of the available benefit.

**And it would cost clock.** A19's history is unambiguous: the predictor cost 10.4% of
Fmax when it landed, and A26's largest single lever (+2.293 ns) was making the
*existing* predictor stop looking things up. Adding a history register and a second
array to the fetch path is exactly the wrong direction on a path that P6 has just been
asked to shorten.

**The measured sizing work is already done and points the other way.** 64 → 256 BTB
entries was measured (256 reaches the floor); XOR-folding the index was tried and
rejected — *it recovers a third of Dhrystone's loss and costs CoreMark more than it
gains.* The RAS at 4, 8, 16 and 32 entries gives **14 040 mispredicts each — identical.**

**Verdict.** **Not recommended on performance grounds.** It appears again in §7.3 for a
completely different reason — as a *security* problem — and that is where the case for
touching it actually lies.

---

### P15 — Push to 116 MHz
> **Relevance ★★★☆☆ · Urgency `after B` · Effect: up to 1.21× on everything · Difficulty L · Risk medium · Depends on: P5, P6**

**What.** A concerted Fmax round targeting the ceiling A24 licensed.

**Why it is worth more than any single IPC lever.** 21.0% of clock beats every
microarchitectural proposal in this tier individually, and it **applies to the
coprocessor too**, because Tier 1 shares the core's clock.

**Why it is `after B` rather than `now`.** Two reasons. First, A26 already took the
easy 29.1%, and what remains is a path in *instrumentation* (P5) and a
*fetch-to-decode* path (P6) — both narrower levers than JALR-off-the-ALU or the
predictor hold. Second, **the placement spread is now 1.63 ns across six runs**, which
at 10.39 ns is ±16%: at this point in the curve, *distinguishing a real gain from
placement noise costs more implementation runs than the gain is worth* unless several
levers are bundled — and bundling makes the result unattributable, which is the mistake
A17's discipline exists to prevent.

**The honest sequencing argument:** land P1 (Tier 1), see what the integrated design's
critical path actually is, and *then* run an Fmax round against the design that will
ship. Optimising the clock of a design that is about to gain a new functional unit in
EX is optimising the wrong netlist.

---

### P16 — Dual-issue / a wider machine
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: IPC beyond 1.0 — the only way past §1.1's ceiling · Difficulty XL · Risk high · Depends on: everything**

**What.** Two-wide in-order superscalar, or a decoupled front end.

**Why it is on the list at all.** §1.1's bound is absolute: **1.145× / 1.151× is
everything a single-issue machine has left.** If the project ever needs more general
CPU performance than that, this is the only door.

**Why it is almost certainly the wrong door for *this* project.** The goal is a
hardware NTT, and the measured `rv32i`→`rv32im` step alone was **5.802×** on the
kernel. Spending months to buy 1.4× on the host, on a machine whose remaining job is to
*not be the bottleneck*, inverts the priorities.

**What it would cost beyond the RTL, and this is the part usually underestimated.**
Nearly every verification asset in this project assumes single-issue in-order:
`tb/cosim/cycle_model.py`'s span identity; `model/bpred.py`'s update rule; A18's cycle
identity; riscv-formal's `unique` and `causal` checks at depth 14; and the argument
that IPC > 1 is *impossible* — which is the only thing that catches
`minstret_counts_twice`. **A wider machine invalidates that catcher and several others,
and the manifest would need re-deriving, not re-running.**

**Verdict.** Documented as the ceiling-breaker, **not recommended** within this
project's scope.

---

## 6. Tier 3 — Verification, methodology and infrastructure

Cheap, and each one closes a gap the report names explicitly.

### P17 — riscv-formal memory consistency (`dmem`, `bus_*`)
> **Relevance ★★★☆☆ · Urgency `soon` · Effect: closes a recorded M6 gap · Difficulty M · Risk low · Depends on: —**

**What.** Enable riscv-formal's memory-consistency checks.

**The obstacle, which is real and recorded.** They need a memory model in the wrapper,
**which would defeat the unconstrained `dmem_rdata` the rest of the proof depends on.**
So this is not "turn on a flag" — it is "build a second wrapper", and the two must be
kept in sync.

**Why it matters more once P4 exists.** Tier 2 is a second bus master over the same
memory. **Every argument in this project that says "there is no second bus master"
stops being true**, and A13's justification for putting both benchmarks in one
bitstream is one of them. A memory-consistency proof is far more valuable *after* the
coprocessor exists than before it — but the wrapper is easier to build *before*.

---

### P18 — riscv-formal CSR checks (`csrw`, `csr_ill`, `ill`)
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: closes the other M6 gap · Difficulty M · Risk low · Depends on: —**

**What.** The CSR checks have no model for Zicsr, `ECALL`, `MRET` or `FENCE` in this
wrapper, so **nothing about the privileged architecture is formally proved.**

**Why it is worth more than it looks.** A9's `minstret`-in-EX bug was found by an
external suite, not by reasoning, and the CSR file has since grown `Zihpm`, `mcountinhibit`,
the user shadows, and the `Zkr` `seed` access rules. **The `seed` CSR's access
semantics are unusual** — a read that does not write *traps* — and are currently proved
only by `formal_seed`, a module-level proof, plus four directed assertions. A
system-level CSR model would cover the interaction with traps and `MRET` that
module-level proofs cannot see.

**This becomes urgent if P8 (interrupts) is taken**, because asynchronous traps touch
`mstatus`, `mie`, `mip` and `mepc` in ways nothing currently proves.

---

### P19 — A scheduled full regression, so tiering can become honest
> **Relevance ★★☆☆☆ · Urgency `soon` · Effect: methodological · Difficulty S · Risk low · Depends on: —**

**What.** A nightly or per-push full run.

**Why, in the project's own words.** H1 rejected `--step` tiering as a default
specifically because *"an escape in an untouched step would hide indefinitely, and a
partial default is only honest with a scheduled full run, **which this project does not
have**."* **Adding one makes a whole class of optimisation legitimate that is currently
forbidden** — and the full run is only 1230–1470 s, which is a nightly job, not an
infrastructure project.

**Second benefit:** it would give the placement-spread question a free dataset. The full
run does not build bitstreams, but a weekly `--probe` at a fixed constraint would
accumulate the distribution that §1.2's error bars are currently guessed from.

---

### P20 — Mechanise more of the board check
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: removes the last human step · Difficulty M · Risk low · Depends on: —**

**What.** The bring-up loop needs no human **except for the LEDs**, and that is not a
formality: the A12 pin transposition passed lint, elaboration, synthesis, timing,
programming and a **byte-perfect UART capture**, because a swapped *output* pin is
invisible to everything upstream of the pad. A person looking at the board was the only
thing that found it.

`tb/fpga/check_xdc_pins.py` now compares every constraint against a mechanically
extracted pinout, so *that specific class* cannot recur. **Which signal drives which
port still cannot be mechanised** — from inside the FPGA.

**From outside it can.** Two options, in increasing order of cost: loop the LED outputs
back to spare PMOD inputs and have the program read its own outputs (catches
transposition, catches stuck pins, does **not** catch a wrong constraint on the loopback
itself); or a cheap USB camera and a colour check (catches everything, including the
"healthy looks turquoise" case, and is genuinely amusing).

**Honest limitation:** the loopback creates a new thing that can be miswired, so it must
itself be fault-injected — and the first fault to inject is the loopback constraint
transposed.

---

### P21 — Extend mutation coverage to the software and script layer
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: methodological · Difficulty M · Risk low · Depends on: —**

**What.** 90 mutations cover the RTL. The harnesses, parsers and build scripts are
fault-injected **ad hoc**, one at a time, when they are written.

**Why.** **Seven of this project's most serious defects were in the checking layer, not
the RTL** — and every one was found by a hand-written injection that happened to be
done. `run_cocotb.py`'s bare `return 0` **survived from A1 to A21** because nobody
happened to inject that particular fault. A systematic manifest over the Python layer
would have found it in one run.

---

## 7. Tier 4 — Research-grade and genuinely novel terrain

These are not incremental. Each one is a piece of work that could stand on its own, and
three of them address problems this project has *documented* rather than solved.

### P22 — Close the `Zkt` control-flow gap: a predictor-state partition or flush
> **Relevance ★★★☆☆ · Urgency `whenever` · Effect: a real security property, currently absent · Difficulty R · Risk high · Depends on: P14's machinery, P18**

**This is the most publishable item on the list, and the reason is that the project
already did the hard part — it identified and *documented* the gap rather than papering
over it.**

**The problem, stated verbatim from A30's claim:**

> This core's branch predictor introduces data-dependent timing that `Zkt` does not
> cover and this core does not remove. The BTB, its counters and the RAS are
> **architecturally invisible state that persists across whatever runs on the machine,
> with no flush and no partition.**

**Why this is a general problem and not a local one.** `Zkt` is a *latency* guarantee
about listed instructions. It says nothing about control flow, and **RISC-V ships no
standard mechanism for flushing or partitioning predictor state.** Every RISC-V core
with a branch predictor and a `Zkt` claim has this gap; most do not say so. A concrete
mechanism plus a measured cost would be a contribution.

**Three designs, in increasing ambition:**

1. **A `Zkt`-mode CSR bit that disables the predictor.** Trivial to build, and its cost
   is *already measured*: A19's data says a predictor-free machine runs Dhrystone at
   IPC 0.6860 against 0.8752 — **a 21.6% slowdown**, paid only inside the protected
   region. The interesting question is whether "disabled" is enough: **a disabled
   predictor whose arrays still hold the previous program's state still leaks on the
   next lookup**, so disable must imply invalidate.
2. **An explicit flush instruction or CSR write**, with a measured flush latency and a
   proof that post-flush behaviour is independent of pre-flush state. The proof
   technique already exists here: **A30's cone-of-influence check** generalises directly
   — compute the fan-in of the prediction outputs and require the pre-flush state
   registers to be absent.
3. **Partitioning by a domain ID**, so a protected region's predictor state cannot be
   observed from outside it. This is the research version, and it is where the
   interesting cost/benefit lives.

**Why this project is unusually well placed to do it.** The predictor is **specified in
a document and implemented twice** (`rvntt_bpred.sv` and `model/bpred.py`), so a
proposed mechanism can be modelled before it is built and its cost projected from the
retired stream. **Almost no other soft core has that.**

**Risk.** *High and quiet.* A partial flush that leaves one array — the RAS, most
likely — is a security claim that is false in a way no functional test detects. The
mitigation is the structural proof, not a test.

---

### P23 — Run the SP 800-90B validation campaign the entropy source does not have
> **Relevance ★★★☆☆ · Urgency `whenever` · Effect: turns an uncertified source into a certified one · Difficulty R · Risk high · Depends on: P24**

**What.** The report's §5.1 statement is unambiguous: **no SP 800-90B statistical
validation campaign has been run.** There is no entropy-rate estimation, no restart
tests, and no IID/non-IID track. `ES16`'s specified meaning is entropy meeting
SP 800-90B, **and this implementation does not establish that.** H = 1 bit/sample is an
**assumption** — and it is the assumption *both health-test cutoffs are derived from*,
so if it is wrong, 21 and 589 are the wrong numbers.

**Why it is a real project and not a checkbox.** None of it is a simulation exercise.
It requires **millions of samples of the physical ring's raw output**, captured from
the board across temperature and voltage — and the current design has **no path to
export raw pre-health-test samples at all.** P24 is therefore a hard prerequisite.

**What would come out of it.** A measured H, which either confirms the cutoffs or moves
them; the restart-test result, which is where injection-locking would show up as a
*correlation between restarts*; and — most valuably — **a measurement of whether the
adaptive test's known blind spot is exercised in practice.** The project has already
established that SP 800-90B's first-sample designation is structurally blind to a
periodic source whose period divides the window, and that this is *the characteristic
ring-oscillator failure*. Nobody knows whether this particular ring does it.

**Pros/cons.** Genuinely valuable, genuinely expensive, and requires equipment (a
thermal chamber, a variable supply) this project does not have. **The honest
intermediate step is P24 plus a room-temperature campaign**, reported as exactly that.

---

### P24 — A raw-entropy debug path
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: prerequisite for P23 · Difficulty M · Risk medium · Depends on: —**

**What.** A gated, explicitly-disabled-by-default path that streams pre-health-test ring
samples out over the UART.

**Why it must be designed carefully.** This is a **deliberate backdoor into the entropy
source**, and the design rule is the one A29 already applied to the KAT path: make it
**structurally impossible** in the shipping configuration, and **check that
structurally.** `kat_no_seed` is the template — it disassembles both builds and requires
**0** accesses to CSR `0x015` in one and **at least one** in the other, *because the
second half is what stops the first from being satisfied by a build that does not
exist.* The equivalent here: the debug path's RTL is behind a parameter that is 0 in
`rvntt_soc_top`, and a check requires the raw-sample net to be **absent from the shipping
netlist** — a cone-of-influence question, and A30's `run_zkt.py` already does exactly
that kind of query with Yosys.

**Risk.** *Quiet and severe if done wrong.* A debug path left enabled is a total
compromise of the RNG. **Do not build it as a runtime-gated feature; build it as a
build-time-absent one.**

---

### P25 — Whole-program constant-time verification
> **Relevance ★★★☆☆ · Urgency `whenever` · Effect: a property `Zkt` explicitly does not give · Difficulty R · Risk medium · Depends on: P22**

**What.** `Zkt` guarantees *instruction* latency. It does not guarantee that a
*program* is constant-time — that depends on control flow and memory addressing, both
of which the programmer controls and neither of which any hardware mechanism here
checks.

**The proposal.** Extend A30's cone-of-influence technique from a *hardware* question to
a *hardware+software* one: given a binary and a set of secret-tainted registers, compute
whether any branch condition or memory address in the dynamic trace is in the taint's
cone. The infrastructure is largely present — the commit-log tracer emits every retired
instruction with its writes, and `model/rv32i_ref.py` is a frozen spec-derived decoder.

**Why it is a natural fit for *this* project specifically.** ML-KEM's reference
implementation is written to be constant-time, and **the whole point of a hardware NTT
is to replace the part of it that is hardest to keep that way.** A tool that verifies
the property end-to-end, on the actual retired stream from the actual core, would make
the security claim about the *system* rather than about the ISA.

**Honest limitation.** Trace-based taint analysis proves a property of *the traces you
ran*, not of the program. Making it a proof rather than a test requires symbolic
execution, which is the research half.

---

### P26 — A masked (side-channel-resistant) Tier-1 butterfly
> **Relevance ★★★★☆ · Urgency `after B` · Effect: the property real ML-KEM deployments need · Difficulty R · Risk high · Depends on: P1**

**What.** First-order Boolean or arithmetic masking of the `Xkntt` datapath, so power
consumption is independent of the secret coefficients.

**Why it is the most *relevant* research item.** Post-quantum KEMs on embedded targets
are attacked by power analysis, not by cryptanalysis. A hardware NTT that is *fast* and
*leaky* solves the wrong half of the problem for the deployments ML-KEM-768 is aimed at.

**Why now is the right time to decide, even if not to build.** Masking is **not
retrofittable cheaply** — arithmetic masking modulo `q = 3329` interacts directly with
the Montgomery reduction, which A24 measured as **the limiting path in every
configuration**. A masked butterfly is a different pipeline, not a wrapper around this
one. **The decision "will this ever be masked?" should be made before P1's datapath is
frozen**, in the same spirit as A1 putting three read ports in the register file for a
coprocessor that did not exist.

**Cost, honestly.** First-order masking typically costs 2–3× area and some frequency.
A24's probe has 33.1% frequency margin and the part is 91% empty, **so this project has
unusually much room to absorb it** — which is itself an argument for doing it here
rather than somewhere tighter.

**Verification.** This is the hard part and it is worth stating: masking correctness is
not a functional property. It needs a **leakage model and a statistical test (TVLA)**,
which means the same "capture from the board" infrastructure P24 needs. The two share a
prerequisite.

---

### P27 — Energy per operation
> **Relevance ★★★☆☆ · Urgency `soon` · Effect: an entire unmeasured dimension · Difficulty M–R · Risk low · Depends on: —**

**What.** Nothing in this project has ever measured **power or energy.** Every figure is
cycles, frequency, or area.

**Why that is a real gap and not a nicety.** The metric that decides whether a hardware
NTT is worth building for an embedded post-quantum target is **energy per KEM
operation**, not cycles. A coprocessor that is 10× faster and 12× more power-hungry is
a *regression* on a battery. **The project is about to build that coprocessor with no
baseline to compare it against.**

**What is achievable, in increasing order of cost.**

1. **Vivado's `report_power` on the post-route design**, with a real SAIF from a
   Verilator or post-route simulation. Cheap, estimate-quality, and — crucially —
   *comparable between designs*, which is all that is needed for a before/after.
2. **Measuring the Arty's actual supply.** The board has no on-board current monitor, so
   this needs a shunt and a meter on the barrel jack or the USB rail. Coarse, but real,
   and it would catch anything the estimate gets badly wrong.
3. Per-block attribution via switching-activity analysis — the research end.

**Do (1) now, before P1.** It costs one report and creates the baseline that P4's
result will otherwise be compared against nothing. **This is the cheapest item on the
list with the highest ratio of insight to effort**, precisely because the dimension is
entirely unexplored.

---

### P28 — Port to a second part, or a second speed grade
> **Relevance ★★☆☆☆ · Urgency `whenever` · Effect: settles the placement-spread question · Difficulty M · Risk low · Depends on: —**

**What.** Build the same RTL for a `-2` or `-3` speed-grade Artix, or a different family.

**Why it answers a question this project has been unable to answer three times.** The
design-to-design placement spread has been measured at ±0.4 ns (A12), 0.9 ns (A19) and
**1.63 ns (A26)** — and it is now large enough that individual Fmax levers are hard to
resolve (see P15). **Every one of those measurements is confounded**, because a design
change and a placement change arrive together.

A second part **separates them**: the same netlist, a different device, gives a second
independent sample of "what does this design's critical path actually cost?" It also
directly tests the report's claim that the current path is **84% route at 8.7%
utilisation** — if that is placement-driven, a roomier or faster part moves it a lot; if
it is a genuine logic depth, it moves proportionally.

**Secondary benefit:** it would make the whole project's numbers *portable*, which
matters if anyone ever wants to compare `rvntt` against another soft core. Right now
every figure is specific to one part at one speed grade with one Vivado version.

---

### P29 — An ASIC flow (OpenLane / Sky130 or equivalent)
> **Relevance ★☆☆☆☆ · Urgency `whenever` · Effect: a completely different set of numbers · Difficulty R · Risk medium · Depends on: P28**

**What.** Push the core through an open-source ASIC flow.

**Why it is interesting rather than useful.** The design is written to be portable
across three tools already — **Verilator, Yosys and Vivado all read it**, which A2
established was only possible with fully-qualified package references and no `import`.
Yosys is the front end of every open ASIC flow, so the barrier is lower than usual.

**What it would show.** A route-dominated 84% path on an FPGA is a statement about
FPGA interconnect. On an ASIC, the same design's critical path would be dominated by
*logic*, and **the ranking of the levers would change completely.** That is genuinely
informative about which of A17/A26's levers were fixing a real depth problem and which
were fixing an FPGA routing problem.

**Verdict.** Legitimate curiosity, no project relevance. Listed for completeness and
because the portability groundwork is already paid for.

---

### P30 — Compiler auto-generation of `Xkntt` from plain C
> **Relevance ★★☆☆☆ · Urgency `after B` · Effect: removes intrinsics from the ML-KEM port · Difficulty R · Risk medium · Depends on: P2**

**What.** Pattern-match `(a * b) % 3329` — or, more realistically, the Montgomery
reduction idiom — out of generic C, and select `kmm` automatically.

**Why the plan says not to.** Plan §0 is explicit: *"You are not going to pattern-match
`(a*b) % 3329` out of generic C. Nobody does that in production. What you want is
`__builtin_riscv_kntt_bfly(...)` lowering to one instruction."* **That is correct advice
and P2 should follow it.**

**Why it is nonetheless research-interesting.** The reason nobody does it is that the
*idiom* is not the arithmetic — it is a specific sequence of a widening multiply, a
low-half multiply by `QINV`, a multiply by `q` and a subtraction, which the reference
code writes explicitly. **That is a recognisable DAG, not a semantic inference.** A
pattern over the reference's actual shape is a much smaller problem than "recognise
modular arithmetic", and it would let the *unmodified* pq-crystals reference — which
this project keeps pristine specifically so its KATs stay valid — compile to the
extension without an intrinsic in sight.

**Risk.** *Quiet.* A pattern that fires on a *nearly*-matching sequence produces wrong
answers in code that never asked for the extension. The mitigation is the frozen model
and the KATs, both of which exist.

---

### P31 — Fault-injection resistance for the coprocessor
> **Relevance ★★☆☆☆ · Urgency `after B` · Effect: a second real attack class · Difficulty R · Risk medium · Depends on: P1, P26**

**What.** Detect or tolerate injected computational faults in the NTT datapath —
redundancy, residue checks, or an inverse-transform verification.

**Why it belongs on this list.** Fault attacks on lattice KEMs are a live area, and the
NTT is the natural target because a single corrupted coefficient propagates. **There is
a cheap and elegant option specific to this problem**: `INTT(NTT(x)) == x` is already a
milestone criterion (M10), and a *sampled* round-trip check is a runtime fault detector
that costs a fraction of the transform.

**Why it pairs with P26.** Masking and fault resistance are the two standard hardening
axes, they interact (masking can *help* or *hinder* fault detection depending on the
scheme), and deciding them together is much cheaper than sequentially.

---

## 8. The master table

Sorted by relevance, then urgency.

| # | Proposal | Rel. | Urgency | Effect (bounded where known) | Diff. | Risk | Depends |
|---|---|:---:|---|---|:---:|---|---|
| **P1** | **Tier-1 `Xkntt` as an EX unit** | ★★★★★ | `now` | **the project's purpose**; M8–M10, M13 | L | quiet | — |
| **P2** | **LLVM backend + `SchedModel`** | ★★★★★ | `soon` | M11, M12; the 4th of four | L | mixed | — |
| **P3** | **Complete C6** | ★★★★★ | `soon` | unblocks M14 | M | quiet | P1 |
| **P4** | **Tier-2 coprocessor** | ★★★★★ | `after B` | M14, M15 | XL | loud, late | P1, P3 |
| P26 | Masked Tier-1 butterfly | ★★★★☆ | `after B` | the property real deployments need | R | high | P1 |
| P8 | Interrupts | ★★★☆☆ | `after B` | qualitative; a P4 dependency | M | **quiet** | — |
| P15 | Push to 116 MHz | ★★★☆☆ | `after B` | **≤ 1.21×** on everything | L | medium | P5, P6 |
| P17 | riscv-formal memory consistency | ★★★☆☆ | `soon` | closes an M6 gap | M | low | — |
| P22 | Close the `Zkt` control-flow gap | ★★★☆☆ | `whenever` | a real, absent security property | R | high | P14, P18 |
| P23 | SP 800-90B campaign | ★★★☆☆ | `whenever` | certified vs. uncertified | R | high | P24 |
| P25 | Whole-program constant-time | ★★★☆☆ | `whenever` | a property `Zkt` does not give | R | medium | P22 |
| **P27** | **Energy per operation** | ★★★☆☆ | **`soon`** | **an entirely unmeasured dimension** | M–R | low | — |
| P5 | HPM decode off the critical path | ★★☆☆☆ | `soon` | small; bounded by P6's path | S | low | — |
| P6 | BRAM → predictor path | ★★☆☆☆ | `whenever` | est. 3–8% clock | M | medium | P5 |
| P7 | Flash image | ★★☆☆☆ | `whenever` | qualitative | S | low | — |
| P10 | Explain the 2000-run anomaly | ★☆☆☆☆ | `soon` | none directly — **but it is the only open loose end** | M | low | — |
| P11 | Reduce load-use penalty | ★★☆☆☆ | `whenever` | **≤ 7.02%** CoreMark cycles | M–L | medium | P5, P6 |
| P18 | riscv-formal CSR checks | ★★☆☆☆ | `whenever` | closes the other M6 gap | M | low | — |
| P19 | Scheduled full regression | ★★☆☆☆ | `soon` | makes tiering honest | S | low | — |
| P20 | Mechanise the board check | ★★☆☆☆ | `whenever` | removes the last human step | M | low | — |
| P21 | Mutate the script layer | ★★☆☆☆ | `whenever` | **7 of 7 worst defects were here** | M | low | — |
| P24 | Raw-entropy debug path | ★★☆☆☆ | `whenever` | prerequisite for P23 | M | **quiet, severe** | — |
| P28 | Second part / speed grade | ★★☆☆☆ | `whenever` | settles the placement-spread question | M | low | — |
| P30 | Auto-generate `Xkntt` from C | ★★☆☆☆ | `after B` | removes intrinsics from the port | R | quiet | P2 |
| P31 | Fault-injection resistance | ★★☆☆☆ | `after B` | a second attack class | R | medium | P1, P26 |
| P16 | Dual-issue | ★★☆☆☆ | `whenever` | **the only way past 1.145×** | XL | high | everything |
| P9 | Stabilise CoreMark iterations | ★☆☆☆☆ | `whenever` | methodological | S | low | — |
| P12 | Faster divider | ★☆☆☆☆ | `whenever` | **≤ 2.71%** Dhrystone cycles | M | medium | — |
| P13 | One-cycle multiply | ★☆☆☆☆ | `whenever` | **≤ 3.18%** CoreMark cycles | S | costs clock | — |
| P14 | Better branch predictor | ★☆☆☆☆ | `whenever` | **≤ 2.96%** cycles — *see §9* | M–L | costs clock | P6 |
| P29 | ASIC flow | ★☆☆☆☆ | `whenever` | different numbers, no relevance | R | medium | P28 |

---

## 9. Not recommended, with the evidence

A roadmap that only proposes is less useful than one that also forecloses. Each of
these is a reasonable-sounding idea that the project's own measurements argue against.

**A fancier branch predictor, for performance (P14).** Flushes are **2.96%** and
**2.91%** of cycles. A *perfect* predictor saves exactly that, and history-based
prediction would add an array and a history register to the fetch path — where A26's
largest lever was making the existing predictor do *less* work. The sizing study is
already done and points the other way: 256 BTB entries reaches the floor, XOR-folding
was measured and rejected, and the RAS gives **identical** mispredict counts at 4, 8, 16
and 32 entries. **Revisit this only for the security reason in P22.**

**Halving the memory to buy clock.** A17 measured **≥90.0 MHz on 16 BRAM tiles** — about
4% — and rejected it. The reasoning is stronger now, not weaker: **P4's coprocessor
buffers still do not exist and still cannot be sized against an array already given
away.** 103 of 135 BRAM tiles are free; the constraint is not the part, it is that the
consumer has not specified itself yet.

**Floorplanning.** A27 measured two pblocks and **both were slower** — by 0.651 ns with
the memory pinned and 0.189 ns without. Vivado had *already* concentrated the design
into a contiguous 2×2 clock-region block with four regions empty. **Route delay at low
utilisation is not automatically evidence of a spread placement**; it can be a path whose
hops are set by where the site *types* are. Reopen this only with a *different* argument,
not with the same one.

**Tiering the mutation set by `--step` as a default.** H1 rejected it: an escape in an
untouched step would hide indefinitely, and a partial default is only honest with a
scheduled full run. **P19 changes that premise** — with a nightly full run, `--step` as
an *interactive* default becomes defensible.

**Bundling Fmax levers to save implementation runs.** A17 established the discipline and
A26 depended on it: levers measured together are unattributable. A26's lever 1 measured
+0.073 ns and its *value* was that it exposed a 14.033 ns path nobody had seen — **a
fact that is invisible if levers 1, 2 and 5 are measured as a bundle.**

**Reporting `1/(T − WNS)` as Fmax.** Stated for completeness because it is the single
most common Fmax error in the field, and because this project has three separate
measurements of why it is wrong.

**Setting `misa.B` or `misa.K`.** Both are recorded boundaries with reasons.
`isa_consistency` was fault-injected against *setting `misa.B`* specifically, so this
one now fails loudly rather than silently deleting the compliance run.

---

## 10. A suggested sequence

Not a schedule. An argument about order.

**Immediately, in parallel with everything (days):**
`P27` energy baseline · `P19` scheduled full run · `P10` the 2000-run anomaly

> All three are cheap, and all three are worth more *before* the next big change than
> after. P27 in particular: **a power baseline taken after the coprocessor lands is not a
> baseline.** P10 matters because `model/bpred.py` and `cycle_model.py` are about to be
> asked to validate Tier-1 latencies, and a 1% unexplained discrepancy is a 1%
> discrepancy you cannot distinguish from a real bug.

**Then, the project's actual work (weeks):**
`P1` Tier-1 `Xkntt` — with `P2` LLVM in parallel, since they share no files

> P1 is why every one of the last thirty steps happened. Everything is in place: the
> handshake, the third read port, the frozen model, Spike, the measured 33.1% frequency
> margin, and a decoder that already accepts the encodings. **P2 in parallel because the
> `SchedModel` is the fourth of the four-way agreement and its absence means the latency
> discipline currently cannot be exercised at all.**

**Then, before Tier 2:**
`P3` complete C6 · `P17` memory-consistency wrapper · `P8` interrupts *if* P4 wants them

> The memory-consistency wrapper is much easier to build **before** a second bus master
> exists, and much more valuable **after**. Build it now, use it then.

**Then:**
`P4` Tier 2 → `P15` an Fmax round **against the design that will ship**

> Optimising the clock of a design about to gain a functional unit in EX is optimising
> the wrong netlist. **P5 and P6 are the levers, but the right time to pull them is after
> P1 has changed the critical path — and it will.**

**Whenever there is appetite for research:**
`P26` masking (**decide before P1 freezes the datapath, even if you build it later**) ·
`P22` the `Zkt` gap · `P24` → `P23` the entropy campaign · `P25` whole-program
constant-time

> P26's placement in this list is the one genuine sequencing *hazard* in the document:
> **masking is not retrofittable cheaply**, because arithmetic masking mod `q` interacts
> directly with the Montgomery reduction that A24 measured as the limiting path in every
> configuration. The decision — not the implementation — belongs before P1.

---

## 11. Dependency graph

```
                      ┌─ P2  LLVM backend ──────────────┬─▶ P30 auto-generation
                      │                                 │
  (nothing) ──────────┼─ P1  Tier-1 Xkntt ──┬─▶ P3 C6 ──┴─▶ P4 Tier 2 ─┬─▶ P15 Fmax round
                      │        │            │                          │
                      │        └────────────┴─▶ P26 masking ──▶ P31 fault resistance
                      │                              │
                      │                              └── (decide BEFORE P1 freezes)
                      │
                      ├─ P5  HPM path ──▶ P6 BRAM→pred ──┬─▶ P11 load-use
                      │                                  ├─▶ P14 predictor ──▶ P22 Zkt gap
                      │                                  └─▶ P15 Fmax round
                      │
                      ├─ P18 CSR formal ─────────────────────▶ P22 Zkt gap ──▶ P25 whole-program CT
                      ├─ P17 memory-consistency formal ──────▶ (much more valuable after P4)
                      ├─ P24 raw-entropy path ───────────────▶ P23 SP 800-90B campaign
                      ├─ P28 second part ────────────────────▶ P29 ASIC flow
                      │
                      └─ independent: P7 flash · P8 interrupts · P9 CoreMark ·
                                      P10 anomaly · P12 divider · P13 MUL=1 ·
                                      P16 dual-issue · P19 CI · P20 board check ·
                                      P21 script mutation · P27 energy
```

---

## 12. One closing observation

The most valuable thing Track A produced is not the 96.246 MHz or the 326 CoreMark. It
is that **every claim in the companion report has a boundary attached to it**, and that
those boundaries were found by a policy — *break every checking mechanism and confirm it
reports failure* — rather than by care.

That policy found seven tools reporting green over nothing, six of them in software
nobody would have thought to test. **It is the thing most worth carrying into Track B**,
where the risk profile is worse in a specific way: no existing test in this repository
executes an `Xkntt` instruction, so an `Xkntt` unit that computes a slightly wrong answer
**will pass everything currently green.**

The mitigations for that are already built — a frozen bit-exact model, a Spike reference
with a 10 000-vector KAT, a cycle model that sees phantom stalls, and a mutation harness
that reports a miscredited test as `PARTIAL` rather than as a pass. **They were built
before they were needed, which was the point.**
