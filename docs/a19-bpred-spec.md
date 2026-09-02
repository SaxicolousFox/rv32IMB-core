# `rvntt_bpred` — branch prediction for the RV32IM core

**Status: specification. Written before the RTL, and that is the point.**

`MODS_A` §3.3 requires `tb/cosim/cycle_model.py` to predict a program's cycle
span *with mispredicts*, and requires it to do so **from this document rather
than from the implementation**. A cycle model that reads predictor state out of
the RTL is not a check; it is a mirror. So everything below is written to be
implemented twice, independently: once in `rtl/core/rvntt_bpred.sv` and once in
`model/bpred.py`. Where the two disagree, this document decides which is wrong.

Two decisions below exist *only* to keep that possible, and they are called out
where they appear:

- **state changes only when a control transfer resolves in EX**, never on a
  speculative fetch — so the predictor is a pure function of the retired
  transfer stream, which is what a trace contains and what the wrong path does
  not (§4);
- **the reset state is "every entry invalid" and nothing else** — so the model
  starts where the hardware starts without knowing anything about cycles (§6).

---

## 1. What it is

Two structures.

| | size | indexed by | reset state |
|---|---|---|---|
| **BTB** — branch target buffer | 256 entries, direct-mapped, **tagged** | `pc[9:2]`, tag `pc[31:10]` | all invalid |
| **RAS** — return address stack | 8 entries | — | empty |

A **BTB entry** is `{valid, tag[21:0], kind[1:0], cnt[1:0], target[31:2]}` — one
flip-flop for `valid` and 56 bits of distributed RAM for the rest, 256 of them.

There is no separate pattern-history table. `MODS_A` A19 says to size the PHT
independently of the BTB; that was measured, the answer was that it does not
matter (§10), and the two-bit counter lives **inside the BTB entry** instead.
`kind` is one of:

| `kind` | meaning | predicted taken when | target from |
|---|---|---|---|
| `BRANCH` | conditional branch | `cnt >= 2` | BTB |
| `JUMP` | `JAL`/`JALR`, no link register | always | BTB |
| `CALL` | `JAL`/`JALR` writing `x1` or `x5` | always | BTB |
| `RET` | `JALR` reading `x1`/`x5`, not writing one | always, unless the RAS is empty | **RAS** |

`kind` is in the BTB because prediction happens in IF, where the only thing
known about the instruction is its address. Without it there is no way to tell a
return from a jump early enough to matter.

**The tag is the whole of the rest of the PC.** Index `pc[9:2]` and tag
`pc[31:10]` together are `pc[31:2]`, and instructions are word-aligned, so a tag
hit identifies the address exactly. There is no aliasing to reason about — a hit
is the same instruction that allocated the entry, always. That is what lets EX
skip a "was this really a branch?" question, and it is asserted in the RTL
rather than left as a sentence.

---

## 2. Where it sits, and why a hit costs zero bubbles

The core fetches with `imem_addr = pc_q`, and `rvntt_ram`'s output register *is*
the IF/ID instruction register, so the instruction at `pc_q` reaches ID one cycle
later. For a predicted-taken transfer at `P` to cost nothing, the cycle after `P`
is fetched must fetch the **target**, not `P+4`.

That means the prediction for `P` has to be ready at the start of the cycle in
which `P` is fetched — not produced during it. So the lookup runs **one address
ahead**:

```
cycle N     pc_q = Q.  The predictor is looked up with pc_next -- the address
            that pc_q will hold in cycle N+1 -- and {taken, target} for that
            address are REGISTERED at the end of the cycle.

cycle N+1   pc_q = P (= the pc_next of cycle N).  imem fetches P.  The
            registered prediction already describes P, so

                pc_next = pred_taken_q ? pred_target_q : pc_q + 4

            is a single 2:1 mux in front of the PC register.
```

`pc_next` is also what indexes the predictor in cycle N+1, so the arrangement is
self-sustaining. The PC register's own input path is **one mux**; the array read
and the tag compare sit on a path that *ends* at the prediction register.

This is `MODS_A` A19's "register the BTB's output, not just its input", and it is
the difference between a predictor and a slower core with a predictor. The BTB is
**distributed RAM with an asynchronous read** for the same reason: a synchronous
read would put the array a cycle later than the tag compare needs it. Two read
ports are needed — one for the lookup, one for the update's read-modify-write of
`cnt` — which is a dual-port distributed RAM, one write and two asynchronous
reads.

**The full PC mux, in priority order:**

```
pc_next = ex_redirect       ? ex_redirect_target
        : front_stall       ? pc_q
        : pred_taken_q      ? pred_target_q
        :                     pc_q + 4
```

`ex_redirect` outranks the prediction because EX has seen the instruction and IF
has only seen its address. Under `front_stall` the address is unchanged, so the
lookup simply repeats and re-registers the same answer; nothing needs to be held.

### The lookup address is NOT `pc_next`, and this is A19's whole timing story

`pc_next` contains `ex_redirect_target`, which is the **ALU's own output**.
Indexing the BTB with it and comparing a tag therefore put the forwarding mux,
the full ALU carry chain and a 256-entry array read into a single cycle:

    ex_mem_q[rd_addr] -> forwarding mux -> ALU (9 x CARRY4) -> ex_jump_target
                      -> ex_redirect_target -> pc_next -> BTB index -> RAMD64E
                      -> tag compare -> pred_taken_q

**16.058 ns, 24 logic levels**, against A17's 11.562. The first A19 Fmax run
failed 80 MHz by **3.886 ns** — a ~30% clock loss for a ~27% IPC gain, which is
not a trade worth making. Registering the prediction, which this section already
called for and which the RTL does, was necessary and nowhere near sufficient.

So the lookup reads **only registered sources**:

```
lookup_pc = front_stall  ? pc_q
          : pred_taken_q ? pred_target_q
          :                pc_q + 4
```

and the entire EX datapath leaves the fetch path. **The consequence is that the
instruction at a redirect target is not predicted**: during the cycle a redirect
fires, the predictor is looking at the address the front end would otherwise
have fetched, so its answer is about the wrong address and is suppressed
(`flush`). One unpredicted instruction per redirect.

**Measured cost of the suppression**, on the small profiling image:

| | mispredicts | cycles | IPC |
|---|---|---|---|
| Dhrystone, `pc_next` lookup | 396 | 24,614 | 0.8671 |
| Dhrystone, registered lookup | 437 | 24,696 (+0.33%) | 0.8642 |
| CoreMark, `pc_next` lookup | 4,152 | 345,413 | 0.8344 |
| CoreMark, registered lookup | 4,353 | 345,815 (+0.12%) | 0.8334 |

**A third of a percent of cycles for four nanoseconds of critical path.** It is
recorded here as a design rule rather than an implementation detail because the
model has to know it: `model/bpred.py`'s `SUPPRESS_AFTER_REDIRECT`, and the same
rule in `tb/cosim/cycle_model.py`.

---

## 3. Prediction, exactly

For lookup address `A` (`A[1:0]` is always `2'b00`):

```
  i    = A[9:2]
  hit  = valid[i] && tag[i] == A[31:10]
  kind = kind[i]

  taken  = hit && ( kind == BRANCH ? (cnt[i] >= 2)
                  : kind == RET    ? (ras_count > 0)
                  :                  1 )

  target = (kind == RET) ? RAS[(ras_sp - 1) mod 8] : {target[i], 2'b00}
```

**A BTB miss predicts not-taken and falls through**, which is exactly the core's
behaviour today. That is deliberate: the predictor can only ever *add* value on a
cold entry, never subtract. `MODS_A` A19's "require a BTB tag hit before
predicting taken" is this line.

---

## 4. Update, exactly

**Only on the resolution in EX of a valid, non-trapping control transfer.**
Nothing else writes either structure — no allocation on fetch, no speculative RAS
movement.

A trapping instruction teaches the predictor nothing: it did not transfer control
to its target, it transferred to `mtvec`, and recording that would make the BTB
predict the trap vector for a branch that will not trap next time. Nothing
younger than EX can be flushed out from under an update, because a flush reaches
only IF/ID and ID/EX — the instruction in EX is always a real one.

Let the resolving instruction have address `P`, actual outcome `taken`, actual
target `T`, and:

```
  link(r) = (r == 1) || (r == 5)                     # x1 = ra, x5 = t0

  kind = BRANCH                       if it is a B-type branch
       = RET                          if JALR and link(rs1) and !link(rd)
       = CALL                         if link(rd)
       = JUMP                         otherwise

  i    = P[9:2];  tag = P[31:10];  hit = valid[i] && tag[i] == tag
```

**BTB:**

```
  if taken:
      cnt' = (hit && kind == BRANCH) ? min(cnt[i] + 1, 3) : 2      # weakly taken
      entry[i] = {valid: 1, tag, kind, cnt: cnt', target: T[31:2]}

  else if hit && kind == BRANCH:
      cnt[i] = max(cnt[i] - 1, 0)                                  # nothing else moves
```

Direct-mapped, so a write evicts whatever shared the index. **A branch that is
never taken never allocates**, and therefore never costs a cycle it does not cost
today; one that already has an entry still learns from a not-taken execution.

A freshly allocated entry starts **weakly taken**, which is the same value a
separate weakly-not-taken counter would have reached on the very event that
allocated it.

**RAS** — on the same resolution:

```
  if kind == CALL:  RAS[ras_sp] = P + 4
                    ras_sp    = (ras_sp + 1) mod 8
                    ras_count = min(ras_count + 1, 8)
  if kind == RET:   ras_sp    = (ras_sp - 1) mod 8
                    ras_count = max(ras_count - 1, 0)
```

The stack wraps rather than saturating on push; with eight entries and a
`ras_count` that saturates, a deeper call chain loses its oldest frames and
predicts those returns wrongly, which is a mispredict and nothing worse.

**The `link(rd) && link(rs1)` case** — the privileged spec's "pop, then push" —
is classified `CALL` here, so such a return is mispredicted. It is a co-routine
idiom that neither Dhrystone nor CoreMark contains, and giving it its own `kind`
would cost a wider field for a case that never occurs. Recorded because it is a
deviation from the architectural hint table, not because it matters.

---

## 5. Checking the prediction, and recovering

The prediction travels with the instruction: `pred_taken` (1 bit) and
`pred_target` are added to IF/ID and ID/EX. In EX, with the actual outcome in
hand:

```
  mispredict = (pred_taken != taken) || (taken && pred_target != T)

  ex_redirect        = mispredict || ex_trap || ex_mret
  ex_redirect_target = ex_trap ? mtvec : ex_mret ? mepc : taken ? T : P + 4
```

Three things follow, and each is a way the design could be wrong without looking
wrong:

1. **A correctly predicted taken transfer no longer redirects at all.** Today
   every taken transfer costs two flushed slots; after this, only mispredicts do.
2. **A branch predicted taken and resolved not-taken redirects to `P + 4`.** This
   is a redirect the core has never issued before, and it is the entire downside
   term of §7.
3. **`ex_redirect_target` is now the architectural next PC unconditionally**,
   whether or not a redirect is taken — because RVFI reports it as `pc_wdata`,
   and a correctly predicted branch does not redirect. Computing `pc_wdata` from
   `ex_redirect` would have RVFI claim the branch fell through, on exactly the
   branches the predictor got right. riscv-formal's `pc_fwd` is the check that
   finds that; making the signal unconditional is cheaper than being found by it.

**The predictor is architecturally invisible.** It changes *when* instructions
are fetched, never *which* ones retire. §9 says how that is proved, and it is
proved against **every possible prediction**, not against the ones this predictor
happens to make.

---

## 6. Reset, and why it has no sweep

BTB `valid` all zero, RAS empty, `pred_taken` cleared. The `valid` bits are
flip-flops with an asynchronous reset; **nothing else needs one**, and the
argument is worth stating because it replaced a worse design:

- an entry's `tag`, `kind`, `cnt` and `target` are read only when its `valid` bit
  is set, and `valid` is set only by a write that fills all four. So no
  uninitialised RAM location is ever read.
- `RAS[i]` is read only when `ras_count > 0`, which requires a push first.

A cold machine therefore behaves *exactly* as the core does today: every transfer
mispredicts, every taken one costs two cycles. The first taken execution of a
transfer allocates its entry weakly taken; the second is predicted.

**The design this replaced** put the counters in a separate 256-entry table and
cleared both arrays with a 256-cycle sweep after reset, because distributed RAM
cannot be reset. That works on hardware and is invisible in a benchmark — and it
would have made `model/bpred.py` wrong for every program shorter than a few
thousand cycles, because the model would have had to know how many cycles had
passed since reset to know whether the predictor was awake yet. It would also
have made **riscv-formal vacuous**: at BMC depth 14 the sweep has not finished,
so all 43 checks would have passed over a predictor that had not done anything.
Neither of those is a thing you notice by looking at a benchmark result.

---

## 7. The downside term, stated before it is measured

Under static not-taken, a not-taken branch is **free**. A18 measured 58,001 of
them per 2,000 Dhrystone runs and 71,335 per 3 CoreMark iterations. Every one
this predictor gets wrong costs two cycles that the current core does not spend.

Three decisions above exist only to bound that:

- a **tag hit is required** before anything is predicted taken;
- the BTB **allocates only on a taken resolution**, so a never-taken branch never
  acquires an entry;
- a fresh entry is **weakly** taken, so one not-taken execution is enough to stop
  predicting taken.

The report must show the regression term separately from the improvement. A net
gain that hides a large regression is a different design from one that does not,
and only one of them is worth carrying into §9 of the plan.

---

## 8. What this specification knowingly gives up

**Updates land at EX, not at fetch**, and they take four **fetch** cycles to
become visible. This is not a caveat; it is a rule, and it is exact:

- instruction `i` is fetched at `F(i)` and the predictor is looked up one address
  ahead, at `F(i) − 1` (§2);
- transfer `j` resolves in EX at `F(j) + 2`, and its write to the distributed RAM
  is readable from `F(j) + 3`.

    j's update is visible to i's lookup  <=>  F(i) − F(j) >= 4

**It must be fetch cycles, and that cost a wrong answer to learn.** The first
version of this section said *retire* cycles, on the reasoning that a control
transfer never stalls in EX so `retire = F + 4`. That is true of EX and false of
ID: an instruction held in ID by a load-use interlock, or behind a multi-cycle
EX instruction, retires later than its fetch implies — and its prediction was
already made. A rule on retirements is short by exactly the number of cycles the
instruction was held. On Dhrystone that is **39 of 396 mispredicts**, every one
of them a return into a callee reached through a stall; on CoreMark, whose calls
are not, it is **zero**, which is precisely how the error stayed hidden until an
instrument compared the two.

**A three-instruction loop fetches its branch every three cycles when it is
predicted, which is inside that window.** So the second iteration of a tight loop
cannot see what the first one learned, mispredicts, and the two extra cycles then
push the third iteration outside the window — where it is predicted correctly.
Tight loops therefore mispredict on alternate iterations. That is a real cost and
it is the price of §4's rule.

**It is also why §4's rule is worth the price.** Updating at fetch would buy those
cycles back and would make the predictor a function of the *speculative* fetch
stream, which a retired trace does not contain — `model/bpred.py` could then not
be driven by an A18 trace at all, and the projection in §10 would have had to come
from the RTL it was supposed to be checking.

**The model implements this rule, and it had to be told.** `model/bpred.py`'s
first version applied every update immediately. It predicted a span of **916**
cycles for `sw/tests/a19_bpred.S` where the RTL measured **992** — 38 tight-loop
iterations the hardware cannot yet know about and the model thought it could, or
8% of the program. `DelayedBPred` queues updates and releases them on the rule
above, and the model and the RTL now agree **to the cycle** on a program with 239
control transfers, nested calls, an indirect jump and two branch sites 1 kB apart.

A cheaper-looking fix was available and was rejected: forwarding the in-flight
write into the lookup would close the common case, cost a mux on the prediction
path A17 has just spent six implementation runs shortening, and still leave a
two-instruction loop outside the window. **Modelling the machine that exists beats
changing the machine to suit the model.**

**And here is what the rule actually costs**, measured by re-running the model
over the A19 core's own trace with the gap set to a range of values. An
instantaneous predictor is `gap = 0`; the real machine is `gap = 4`.

| gap | 0 | 2 | **4** | 6 | 8 | 16 |
|---|---|---|---|---|---|---|
| Dhrystone mispredicts | 16,037 | 16,037 | **18,036** | 24,034 | 26,033 | 32,031 |
| CoreMark mispredicts | 12,289 | 12,289 | **12,289** | 12,289 | 13,190 | 13,452 |

**Dhrystone pays 1,999 extra mispredicts — 3,998 cycles, 0.33% of the region.
CoreMark pays nothing.** The difference is not loop length but *call* structure:
the window bites between a `CALL` and the `ret` of a short callee, and Dhrystone
reaches its callees through load-use stalls that move the `ret`'s fetch earlier
relative to its retirement. It is the same asymmetry that hid the
retire-versus-fetch error above, appearing a second time as a cost.

At `gap = 4` the model reproduces the RTL's mispredict count exactly in every
region, so the bold row is not a projection -- it is the machine. (The absolute
figures above are from the pre-`SUPPRESS_AFTER_REDIRECT` build; §11's rule adds
2,001 Dhrystone and 614 CoreMark mispredicts on top, and the model reproduces
those too. The *shape* of this table -- what the visibility window costs, and
that it costs Dhrystone rather than CoreMark -- is unchanged by it.)

---

## 9. How it is checked

| | what it proves |
|---|---|
| riscv-formal, depth 14, the same 43 checks, with the prediction **abstracted to a free pair of signals** | the architecture is invariant under *every possible* prediction, not just this predictor's |
| `tb/formal/rvntt_bpred.sby` on the module itself | the predictor's own invariants: aligned targets, saturating counters, a bounded RAS, index+tag = the address |
| cosim, 1000 random programs at high branch density, byte-identical vs Spike | the same, dynamically, on real instruction mixes — and **nothing about the predictor's effectiveness**; see below |
| `sw/tests/a19_bpred.S`, eight loop shapes, span checked against the model | the predictor actually predicts, and the model knows what it does |
| `tb/cosim/cycle_model.py` predicting each program's span with mispredicts | §3.3's requirement, and the check that the RTL matches **this document** |
| A18's instrument, before and after | the payoff, with the §7 regression term shown separately |
| A16's hardware procedure, three programming passes | the numbers that get reported |

**Why riscv-formal abstracts the prediction rather than proving over the real
one.** The claim is that the predictor cannot change what retires. Proving it
against this predictor proves it for the predictions this predictor makes;
proving it against `(* anyseq *)` signals proves it for every prediction any
predictor could make — including a misaligned target, a prediction of taken on an
`addi`, and a different wrong answer every cycle. That is the property, stated
exactly, and it is the same abstraction pattern as A15's `RVNTT_ABSTRACT_MULDIV`
with the same recorded boundary: **it proves nothing about the predictor's own
logic**, which is answered by the four rows above it.

**Why the random suite cannot do this alone, stated because it was discovered
rather than anticipated.** All eight mutations below escaped `random:branch` on
their first run — every one of them, which is a far clearer signal than one
escape would have been. `tb/cosim/gen_random_prog.py` emits **forward-only**
branches and jumps, because that is what guarantees a generated program
terminates; the consequence is that a random program is a straight line in which
**every control transfer site executes at most once**. A predictor whose whole job
is to remember what a site did last time is therefore inert in every random
program this project has ever generated, and the 1000-program cosimulation proves
the predictor is *architecturally invisible* — its main risk — while being
structurally unable to say whether it works at all.

`sw/tests/a19_bpred.S` is what closes that: eight loops covering a strongly-taken
branch, an alternating one, a never-taken one inside a hot loop, a leaf call, one
callee reached from two call sites, two-deep nesting, an indirect jump that is not
a return, and two branches one kilobyte apart that share a BTB index and evict
each other every pass. Its span is checked against `model/bpred.py` and matches to
the cycle.

**One gap remains and is not closed**: a *false hit* — a wrong tag that matches.
Producing one by hand needs two hot sites whose tags collide under the particular
broken comparison being tested, which is tuning a program to a mutation rather
than testing a property. The stimulus that would cover it is a long random program
containing loops, which the generator cannot emit. Recorded here rather than left
for someone to assume.

**Fault injections required**, from `MODS_A` A19 — each must be caught, and at
least one must be caught by the *cycle model* rather than by a correctness check,
because a predictor bug that is architecturally invisible is precisely what a
commit-log diff cannot see. All four are already caught by `model/bpred.py`
driven from an A18 trace, **before any RTL exists**:

| fault | Dhrystone mispredicts |
|---|---|
| *(none)* | 16,039 |
| the BTB tag comparison is ignored | 22,038 |
| the 2-bit counter wraps instead of saturating | 69,030 |
| the RAS push condition is dropped | 44,028 |
| a cold entry is predicted taken | 68,040 |

**And eight more against the RTL**, in `tb/mutate/run_mutation.py`, all caught:
the tag compared against a rotated copy of itself; a counter that wraps instead
of saturating; the RAS push condition dropped; a BTB that refuses to replace a
live entry; a mispredict check that ignores the target; a PC mux that lets the
prediction outrank the EX redirect; `pc_wdata` reported as a fall-through; and a
BTB that allocates only for branches. **Six of the eight are architecturally
invisible** — the commit log is byte-identical with them applied — and are caught
by the span check against `model/bpred.py` and by nothing else.

There is deliberately **no mutation for the BTB valid bit**. Dropping it is a
no-op in simulation: an entry that was never written reads as all zeros, so its
tag is zero, and nothing in this project executes below address `0x400` — the tag
comparison alone rejects it. The valid bit is defence against a memory that does
*not* power up zeroed, which is the case §6's reset argument is written for and
the case no simulation here can produce. A mutation that cannot fail is not
evidence, and an eight-entry list where a reader expects nine needs a reason.

---

## 10. Deviations from `MODS_A` A19, and the measurements that earned them

`MODS_A` A19's heading is "2-bit bimodal, **64-entry** BTB, 8-entry RAS", and it
says of its own numbers: *"after A14 the instruction mix changes and the branch
fraction with it, so retake the baseline and recompute the projection before
believing the delta."* That was done, with A18's instrument, on the RV32IM core.
The RAS survived it unchanged; the other two did not.

**The BTB is 256 entries, not 64.** Dhrystone executes only **49 distinct
control-transfer sites** in its timed region — comfortably inside 64 — and a
64-entry direct-mapped BTB still mispredicts 40,027 of its 240,005 transfers.
They are **conflict** misses, not capacity misses: the sites are spread across
enough of the address space that `pc[7:2]` aliases hot pairs onto each other, and
the entries evict one another every iteration.

| BTB | index | Dhrystone mispredicts | Dhrystone | CoreMark |
|---|---|---|---|---|
| 64 | `pc[7:2]` | 40,027 | 1.224× | 1.189× |
| 64 | `pc[7:2] ^ pc[13:8]` | 34,030 | 1.235× | 1.182× |
| 128 | `pc[8:2]` | 24,035 | 1.255× | 1.190× |
| **256** | **`pc[9:2]`** | **14,040** | **1.276×** | **1.191×** |
| 512 | `pc[10:2]` | 14,040 | 1.276× | 1.192× |

XOR-folding the index was tried first, because it is free: it recovers a third of
Dhrystone's loss and **costs CoreMark more than it gains** (13,444 → 16,368
mispredicts), so it was rejected. 256 entries reaches the floor — 512 is
identical on Dhrystone and 0.001× better on CoreMark.

**There is no separate PHT.** `MODS_A` A19 asks for the pattern-history table to
be sized independently of the BTB. It was, and the measurement says the
independence buys nothing: widening it from 256 to 1024 entries changes Dhrystone
by **zero** mispredicts and CoreMark by 25 in 12,278. Given that, the counter was
folded into the BTB entry, for a reason that is not area:

| | separate 256-entry PHT | counter folded into the BTB |
|---|---|---|
| Dhrystone | 14,040 mispredicts, 1.276× | 16,039, **1.272×** |
| CoreMark | 12,278, 1.191× | 12,306, **1.191×** |
| NTT `rv32im` | 144, 1.151× | 145, **1.150×** |
| reset | needs a 256-cycle sweep | **`valid` bits only** |

The folded version is 0.3% slower on Dhrystone and identical everywhere else, and
it removes the sweep — which §6 explains was not a cost but a correctness problem
for both the cycle model and riscv-formal. **A design that is 0.3% slower and
checkable beats one that is 0.3% faster and not.**

**The RAS stays at 8 entries**, measured rather than inherited: 4, 8, 16 and 32
give 14,040 / 14,040 / 14,040 / 14,040 Dhrystone mispredicts, and 8 is where
CoreMark stops improving (12,287 → 12,278 → 12,278). `MODS_A`'s number was right.

**The projection, recomputed on the RV32IM core**, which is what `MODS_A` A19
asked for and what replaces its RV32I table:

| | before (A16/A18) | projected | **measured (shipping)** |
|---|---|---|---|
| DMIPS/MHz | 0.7325 | 0.9315 | **0.9254** |
| CoreMark/MHz | 2.4309 | 2.8960 | **2.8929** |
| IPC (Dhrystone) | 0.6860 | 0.8723 | **0.8666** |
| IPC (CoreMark) | 0.7006 | 0.8347 | **0.8337** |

Dhrystone falls 0.65% short of its projection and CoreMark 0.11%, from two
causes that are kept apart deliberately: §8's retire-versus-fetch error in the
model (1,999 Dhrystone mispredicts), and §11's `SUPPRESS_AFTER_REDIRECT`, which
did not exist when the projection was made and which the timing fix forced
(a further 2,001). The projection is left here as the record of what was
predicted before either was known, rather than quietly re-run to agree with the
answer.

Both land inside §A13's `0.8–1.2 DMIPS/MHz` and `IPC 0.75–0.95` bands — the first
time either figure has. And the §7 regression term, shown separately as required
and **measured, not projected**:

| | Dhrystone | CoreMark |
|---|---|---|
| taken transfers correctly predicted | 167,967 of 182,004 | 104,667 of 111,390 |
| **cycles saved** | **335,934** | **209,334** |
| not-taken branches predicted taken | 6,000 of 58,001 (10.3%) | 6,180 of 71,335 (8.7%) |
| **cycles lost** | **12,000** | **12,360** |
| net | 323,934 | 196,974 |
| **measured reduction** | **323,934** | **196,974** |

The last two rows are the point. The decomposition is not an estimate that
happens to land close; it accounts for every cycle. The regression is real, it is
3.6% and 5.9% of the respective gross saving, and it is not what decides the
result.
