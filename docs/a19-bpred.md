# A19 — the branch predictor, measured

The design is `docs/a19-bpred-spec.md`, written before the RTL and implemented
twice. This is what it did.

- projection, from an A18 trace before any RTL existed: `fpga/build/a19_projection.json`
- **hardware: `docs/a19-benchmarks.md` and `docs/a19-benchmarks.json`** — the
  headline result, three JTAG passes, twelve blocks identical to the cycle
- attribution before and after: `fpga/build/a18_rv32im.json`, `fpga/build/a19_stalls.json`

**The board is the measurement; the simulation below is the attribution.** On
hardware, Dhrystone goes **1.2759×** (IPC 0.6860 → **0.8752**, DMIPS/MHz 0.7325 →
**0.9346**) and CoreMark/MHz 2.4309 → **2.8933**, at 77.500 MHz against A17's
86.486. The simulated ratios in this document are 2,000-run figures and are
slightly *conservative* against the board — 1.2633× versus 1.2759× — for a reason
that is measured, bounded and **not yet explained**; `docs/a19-benchmarks.md`
gives it a section of its own rather than a footnote.

---

## Verification

| | result |
|---|---|
| riscv-formal, depth 14, 43 checks, prediction abstracted to `(* anyseq *)` | **43/43** |
| `formal_bpred` on the module (8 BTB entries, 4 RAS entries) | **pass** |
| cosim, random programs at 20% branch density, byte-identical vs Spike | **pass** |
| `sw/tests/a19_bpred.S`, 8 loop shapes, span vs `model/bpred.py` | **exact** |
| mutations | **8/8 caught** |

**The abstraction makes riscv-formal's claim stronger, not weaker.** The property
is that the predictor cannot change what retires; proving it against a free pair
of signals proves it for *every* prediction any predictor could make — a
misaligned target, a prediction of taken on an `addi`, a different wrong answer
every cycle. What it does not cover is the predictor's own logic, and that
boundary is recorded in the RTL next to the abstraction, exactly as A15 recorded
`RVNTT_ABSTRACT_MULDIV`'s.

**The abstraction is also necessary, not merely nicer.** The concrete predictor's
BTB is empty at reset and needs a resolved transfer before it can predict
anything; at BMC depth 14 there is barely room for that, and an earlier draft with
a 256-cycle reset sweep would have made all 43 checks pass over a predictor that
had not yet done anything. A check that cannot fail is not a check.

---

## What the random suite could not see, and why it is written down

**All eight mutations escaped `random:branch` on their first run.** Not one was
caught. The reason is structural rather than statistical:
`tb/cosim/gen_random_prog.py` emits **forward-only** branches and jumps, because
that is what guarantees a generated program terminates and never traps. A random
program is therefore a straight line in which **every control transfer site
executes at most once** — and a predictor whose entire job is to remember what a
site did last time is inert in every random program this project has ever
generated.

So the 1000-program cosimulation proves exactly what it always proved, and no
more: the predictor is **architecturally invisible**. That is its main risk and
the one the project cares most about. It is structurally unable to say whether
the predictor *works*.

`sw/tests/a19_bpred.S` is what says that. Eight cases, each a loop:

| case | what it exercises |
|---|---|
| 1 | a strongly-taken backward branch — allocation, then 15 free transfers |
| 2 | a branch that alternates — the 2-bit counter's hysteresis |
| 3 | a never-taken branch inside a hot loop — §7's downside term |
| 4 | a leaf function called in a loop — CALL/RET |
| 5 | one callee, two call sites — the case a BTB gets wrong and a RAS gets right |
| 6 | two-deep nesting — the stack has to be a stack |
| 7 | an indirect jump that is *not* a return — misclassifying it corrupts case 6 |
| 8 | two branches 1 kB apart sharing a BTB index — direct-mapped eviction |

Its span is checked against `model/bpred.py` and matches to the cycle.

**One gap remains and is not closed.** A *false hit* — a wrong tag that matches —
needs two hot sites whose tags collide under the particular broken comparison
being tested, which is tuning a program to a mutation rather than testing a
property. The stimulus that would cover it is a long random program containing
loops, which the generator cannot emit. Recorded rather than left to be assumed.

---

## The model had to be taught the pipeline

`model/bpred.py`'s first version applied every update immediately. It predicted a
**916**-cycle span for `a19_bpred.S` where the RTL measured **992**.

The difference is entirely one rule. An update lands in EX and cannot reach a
lookup that has already happened:

    j's update is visible to i's lookup  <=>  retire(i) - retire(j) >= 4

A three-instruction loop retires its branch every three cycles when predicted,
which is inside that window — so tight loops mispredict on alternate iterations.
`DelayedBPred` queues updates on that rule, and the model and the RTL now agree
to the cycle on a program with 239 control transfers, nested calls, an indirect
jump and a direct-mapped eviction contest.

**A cheaper-looking fix was rejected.** Forwarding the in-flight write into the
lookup would close the common case, cost a mux on the prediction path A17 had just
spent six implementation runs shortening, and still leave a two-instruction loop
outside the window. Re-running the projection with the gap at 0, 4, 8 and 16
retire cycles gives **16,039 / 16,039 / 24,037 / 32,035** Dhrystone mispredicts:
at the real value of 4 the rule costs the benchmarks *nothing*, because their
loops are longer than three instructions. Modelling the machine that exists beat
changing the machine to suit the model.

---

## Results

### Attribution, before and after, with A18's instrument

Same image, same run counts, one core: `fpga/build/a18_rv32im.json` against
`fpga/build/a19_stalls.json`. **Both closures hold exactly in every region** —
the cycle identity at residual 0, and `redirects == mispredicts` predicted by
`model/bpred.py`.

| per 2,000 Dhrystone runs | A18 (no predictor) | A19 | |
|---|---|---|---|
| cycles | 1,554,030 | **1,230,096** | **1.2633×** |
| retired | 1,066,022 | 1,066,022 | **identical** |
| **IPC** | 0.6860 | **0.8666** | |
| redirects | 182,004 | **20,037** | −89.0% |
| flush cycles | 364,008 — 23.4% | **40,074 — 3.3%** | |
| load-use stalls | 52,000 — 3.3% | 52,000 — 4.2% | identical |
| multi-cycle EX stalls | 72,000 — 4.6% | 72,000 — 5.9% | identical |

| per 3 CoreMark iterations | A18 | A19 | |
|---|---|---|---|
| cycles | 1,234,083 | **1,037,109** | **1.1899×** |
| retired | 864,600 | 864,600 | **identical** |
| **IPC** | 0.7006 | **0.8337** | |
| redirects | 111,390 | **12,903** | −88.4% |
| flush cycles | 222,780 — 18.1% | **25,806 — 2.5%** | |

| the ML-KEM NTT | A18 | A19 | |
|---|---|---|---|
| `rv32i` cycles | 205,884 | **165,754** | 1.2421× |
| `rv32im` cycles | 39,058 | **33,950** | 1.1505× |

> **These are the SHIPPING numbers, and they are not the ones this document
> carried first.** The tables above were originally measured before the lookup
> fix in §"Timing" below existed; that fix leaves the instruction at a redirect
> target unpredicted, which costs Dhrystone 4,002 cycles and moves every figure
> here by about a third of a percent. Re-measuring was the only honest option,
> because a document describing a design that is not the one built is worse than
> no document. The pre-fix data is kept at `fpga/build/a19_stalls_prefix.json`
> for the same reason the projection below is kept: the record of what was
> measured, with the reason it changed.

**Nothing architectural moved.** Retired counts, load-use stalls, multi-cycle EX
stalls and the full control-transfer classification — taken, not-taken, `JAL`,
`JALR` — are identical to the instruction in every region. Only cycles changed,
which is the whole claim of §5 restated as a measurement.

### The gain and the regression term, separately

`MODS_A` A19 requires the not-taken regression to be reported beside the
improvement rather than netted into it. It decomposes **exactly**:

| | Dhrystone | CoreMark |
|---|---|---|
| taken transfers correctly predicted | 167,967 of 182,004 (92.3%) | 104,667 of 111,390 (94.0%) |
| **cycles saved** | **335,934** | **209,334** |
| not-taken branches wrongly predicted taken | 6,000 of 58,001 (10.3%) | 6,180 of 71,335 (8.7%) |
| **cycles lost** | **12,000** | **12,360** |
| net | 323,934 | 196,974 |
| measured reduction | **323,934** | **196,974** |

The last two rows are the point: the decomposition is not an estimate that
happens to be close, it accounts for every cycle.

**Returns are where Dhrystone loses.** 80.0% of its 30,002 returns are predicted,
against CoreMark's 98.6% of 5,455 — and the reason is §8's visibility window,
not the return stack. Dhrystone's callees are reached through load-use stalls, so
a `ret` early in a short callee is fetched before the `CALL`'s push has landed.
That is the same effect that made the first version of the model wrong, and
having modelled it, it is now a number rather than a surprise.

### Against the projection

The projection made from an A18 trace before the RTL existed said 1.2716× for
Dhrystone and 1.1913× for CoreMark. The shipping design measures **1.2633× and
1.1899×**.

Two separate effects account for the gap, and they are worth keeping apart.

The first is the **retire-versus-fetch error** in the original model, described
in §"The model had to be taught the pipeline": it under-counted Dhrystone's
mispredicts by 1,999 across 2,000 iterations — almost exactly one per iteration,
which is what one return-after-a-stall per iteration looks like. It is fixed in
`model/bpred.py`, and the fixed model now reproduces the RTL's mispredict count
to the unit in every region.

The second is `SUPPRESS_AFTER_REDIRECT`, which did not exist when the projection
was made and which the timing fix below forced. It costs a further 2,001
Dhrystone mispredicts. The projection is left in place as the record of what was
predicted before either was known, with both errors identified rather than
quietly re-run.

---

## Timing, and the architectural rule it forced

**The first A19 build failed 80 MHz by 3.886 ns**, and this is the whole of
A19's timing story.

The obvious address to look the predictor up with is `pc_next` — one address
ahead of the fetch, so a correctly predicted taken transfer costs no bubbles.
But `pc_next` contains `ex_redirect_target`, which is the ALU's own output.
Indexing a 256-entry RAM with it and comparing a tag therefore put the
forwarding mux, the full ALU carry chain and the array read in **one cycle**:

    ex_mem_q[rd_addr] -> forwarding mux -> ex_rs1_fwd -> ALU (9 x CARRY4)
                      -> ex_jump_target -> ex_redirect_target -> pc_next
                      -> BTB index -> RAMD64E -> tag compare -> pred_taken_q

16.058 ns over 24 logic levels, against A17's 11.562. `MODS_A` A19 names this as
"the one real timing risk in this document" and prescribes registering the
prediction. **That was done from the start and was nowhere near sufficient** —
registering the output does not take the ALU out of the cone that computes it.

**The fix is architectural, not an implementation detail.** The lookup reads only
*registered* sources, so the instruction at a redirect target cannot be
predicted: during the cycle the redirect fired, the predictor was looking
somewhere else, and `flush` makes it say so rather than answer about the wrong
address. That is `SUPPRESS_AFTER_REDIRECT` in `docs/a19-bpred-spec.md` and in
`model/bpred.py`. It costs **4,002 Dhrystone cycles, 0.33%**, to take the entire
EX datapath out of the fetch path.

### The result, and what it costs

| | A17 | A19 |
|---|---|---|
| **Fmax** | 86.490 MHz | **77.501 MHz** (−10.4%) |
| WNS at Fmax | +0.005 ns | +0.003 ns |
| fastest failing constraint | 86.745 MHz | 77.942 MHz |
| LUTs / FFs | 2,637 / 1,157 | 3,471 / 1,530 |

Two further levers were applied after the lookup fix and are **measured, not
assumed, to be free**: a dedicated `pc + imm` target adder for branch and JAL,
which starts the mispredict comparison an ALU result-mux earlier, and
`max_fanout` on `ex_redirect`, whose 244 loads routed for 1.491 ns. Reverting
only those two and re-running the profile gives **52 counters identical across
all four regions** — their cost is zero cycles, not a small one. The adder's
equality with the ALU is additionally proved by `a_pc_target_matches_alu` under
riscv-formal, over every reachable state rather than one program.

### The search is not monotonic, and the number should be read accordingly

| constraint | period | WNS | implied path |
|---|---|---|---|
| 73.997 MHz | 13.514 ns | +0.009 | 13.505 ns |
| 77.501 MHz | 12.903 ns | +0.003 | 12.900 ns |
| 77.942 MHz | 12.830 ns | −0.198 | 13.028 ns |
| 78.376 MHz | 12.759 ns | −0.612 | 13.371 ns |
| 79.246 MHz | 12.619 ns | −1.186 | **13.805 ns** |
| 80.998 MHz | 12.346 ns | −0.676 | 13.022 ns |
| 87.997 MHz | 11.364 ns | −1.966 | 13.330 ns |

**79.246 MHz failed by more than the tighter 80.998 MHz did.** The implied path
delay for one netlist spans 12.90–13.81 ns — a 0.9 ns spread, twice the ±0.4 ns
A12 recorded. A binary search assumes pass/fail is monotone in frequency and it
is not, so **77.501 MHz is the highest constraint observed to pass in this
search, not a boundary**. The A17-to-A19 comparison survives that: 8.99 MHz is
far outside the spread. A future ±1 MHz claim against this baseline would not,
and should not be made without repeated runs.
