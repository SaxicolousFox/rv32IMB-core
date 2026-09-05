# A26 — Fmax, part 1: the levers that change no cycles

**Held to A17's standard: not one cycle count may change.** Dhrystone and
CoreMark on this SoC are deterministic to the cycle, so before and after must be
identical except for the clock. A lever that moves a cycle count is a
microarchitecture change wearing a timing change's clothes and belongs in its
own step — which is where A25 is.

---

## The result

**Fmax = 96.246 MHz**, up from A23's 74.577 — **+29.1%, with every cycle count
unchanged.** The fastest constraint that failed is 97.504 MHz. Vivado 2025.2,
`xc7a100tcsg324-1` (**−1** speed grade), `explore_postroute`, binary search on
the constraint with the verdict from post-route WNS. 5740 LUTs, 1972 FFs,
32 BRAM tiles, 4 DSP48E1.

| | A23 | A25 | **A26** |
|---|---:|---:|---:|
| Fmax | 74.577 MHz | did not close at 74.577 | **96.246 MHz** |
| Dhrystone cycles (sim, 200 runs) | — | 122 896 | **122 896** |
| CoreMark cycles (sim, 2 iter) | — | 672 709 | **672 709** |

The search:

```
  lo    84.998 MHz (11.765 ns): WNS +0.190  PASS
  hi   104.998 MHz ( 9.524 ns): WNS -0.423  fail
  it1   95.003 MHz (10.526 ns): WNS +0.023  PASS
  it2  100.000 MHz (10.000 ns): WNS -0.210  fail
  it3   97.504 MHz (10.256 ns): WNS -0.152  fail
  it4   96.246 MHz (10.390 ns): WNS +0.010  PASS
```

**A26's own expectation was 95–110 MHz**, "with 110 reachable only if the
newly-exposed path is also short". It was not short — it is 10.358 ns — so the
result lands at the bottom of the predicted band, which is the prediction
holding rather than failing.

### Per-lever attribution

Every configuration probed at **85 MHz** (11.765 ns) with the **same strategy**
(`default`), one implementation run each, so the deltas are comparable. This is
`fmax_search.py --probe`, which A23 added and which says in its own output that
a probe is a bound and not an Fmax.

| configuration | WNS at 85 MHz | contribution |
|---|---:|---:|
| A25, as committed | −2.562 ns | — |
| + lever 1 — JALR off the ALU result mux | −2.489 ns | **+0.073 ns** |
| + lever 2 — forwarding decision precomputed in ID | −1.816 ns | **+0.673 ns** |
| + lever 5 — the predictor holds instead of re-looking-up | **+0.477 ns** | **+2.293 ns** |
| | | **+3.039 ns total** |

**Lever 1's number is not lever 1's value, and the difference matters.** §3.4
predicted ~2.7 ns from deleting the ALU result mux and the JALR target form, and
that logic really is gone — but the probe measures the *design's* worst path, and
lever 1 exposed a completely different one that was already 14.033 ns long. The
2.7 ns was removed from a path that stopped being critical. **Everything after
lever 1 is measured against that newly-exposed path, which is why lever 5 — a
lever the plan does not list — is the largest single contribution in the round.**

---

## Lever 1 — JALR onto A17's address adder

`ex_jump_target` tapped `ex_alu_y` for exactly one instruction, JALR, and A19's
comment explained why: *"its target genuinely depends on a register, which is the
whole difference between it and the other two shapes."* That is true, and it is
also exactly why **A17's adder was the right one and had been sitting there since
A17**: `ex_mem_addr = ex_rs1_fwd + imm` is a register-sourced sum. Nobody noticed
because A17 added it for loads and A19 added a different one for branches, and
JALR is neither.

**Zero new logic.** The decoder is what makes it true — `OPC_JALR` sets
`ALU_ADD`, `SRCA_RS1`, `SRCB_IMM` — so `a_jalr_target_matches_alu` asserts the
equality and riscv-formal proves it at depth 14, the same shape of claim as
`a_addr_adder_matches_alu` and `a_pc_target_matches_alu`.

`ex_alu_y` now terminates at `ex_result`, which terminates at a pipeline
register. The ALU has left the mispredict path entirely.

---

## Lever 2 — the forwarding decision, made a stage early

The comparison an instruction in EX makes is against producers in MEM and WB.
The same instruction in ID compares against producers in EX and MEM — the same
two instructions, one cycle sooner. Carrying the four-bit answer takes the
`rd_addr` comparator and the select encoder off the EX critical path and leaves
only the operand mux.

**Why it is the same decision** is a structural argument, not an appeal to
intuition, and it is asserted (`a_pipe_advances_together`):

- ID/EX takes a new instruction only in its final `else`, requiring
  `!ex_stall && !id_stall && !ex_redirect`.
- `ex_redirect` is `!ex_stall && (ex_trap || ex_mret || ex_mispredict)`, so
  `!ex_redirect && !ex_stall` implies `!ex_trap`.
- EX/MEM bubbles on `ex_trap || ex_stall`, and both are false there.
- MEM/WB copies EX/MEM unconditionally, always.

So on every edge where a new instruction enters EX, the one that was in EX enters
MEM and the one in MEM enters WB.

### What riscv-formal found, on all 77 checks at once

The transformation was verified by keeping **a second copy of the original
EX-stage computation** and asserting the two agree
(`a_fwd_precompute_matches_a/b`). That is deliberately not a restatement of the
transformation: it says the transformation changed nothing.

It failed at step 14, on every check. **The structural argument is right about
the edge an instruction *enters* EX and silent about the cycles it *stays*
there.** An instruction occupies EX for more than one cycle whenever the
multi-cycle unit is running, and during those cycles EX/MEM is bubbled and MEM/WB
copies it anyway — so the producers drain out from under the stalled consumer.
`rvntt_muldiv`'s own header describes exactly this ("on the second stall cycle
FWD_MEM stops matching, on the third FWD_WB stops matching") and captures its
operands on the start cycle because of it.

A **held** select keeps pointing at a stage that has since been cleared, and
`FWD_MEM` into a bubble reads **zero** — strictly worse than the register file's
stale copy, which is what the original falls back to. So the select **decays**
with the pipeline: `MEM → WB → REG`, one step per stalled cycle, which is
precisely what recomputing it in EX would have produced.

**Nothing else in the tree saw it.** Cosimulation passes over the bug, because
the only consumer that reads these operands after the start cycle is the
multi-cycle unit, and it does not re-read them. `fwd_precompute_does_not_decay`
is now in the mutation manifest with `rvfi:unique_ch0` as its catcher.

### Fault injection, and one escape worth more than the mutation

| fault | cosim | riscv-formal |
|---|---|---|
| the select does not decay through a stall | **escapes** | **CAUGHT** |
| forward from a flushed/bubble slot (`mem_valid` tied high) | **CAUGHT** | — |
| the load-use exclusion dropped in ID (`mem_mem_read` tied low) | escapes | escapes |

**The third escapes correctly, and it is not made into a mutation.** The only way
a load in EX can match the ID instruction's rs is the exact condition
`rvntt_hazard` stalls on, and a stalled ID instruction never latches the
precompute. The exclusion is structurally redundant *there* — but it is a
property of the interlock, not of the forwarding unit, so the port stays wired to
the real signal. A mutation aimed somewhere it could never bite is the shape A14
already rejected once, for the operand-register enable, and the response is the
same: record the reason, do not pretend a test covers it.

---

## Lever 5 — the predictor holds its answer instead of asking again

**Not one of §3.4's four, and the largest contribution in the round.** It exists
because lever 1 exposed the path that lever 5 attacks, and because levers 3 and 4
aim at a path that had stopped being critical.

After lever 1 the worst path was **14.033 ns** and had nothing to do with the
EX stage:

```
u_ram BRAM (instruction) → decode → load-use hazard → BTB read address
                         → BTB array read → tag compare → pred_taken_q
```

A19 wrote `bp_lookup_pc = front_stall ? pc_q : ...`, and its own comment records
the reason it is correct: *"under front_stall the address does not move, so the
lookup simply repeats and re-registers the same answer."* Correct, and expensive:
`front_stall` is a function of the **decoded** instruction, so the stall term
drags the instruction memory, the decoder and the hazard unit in front of the
BTB's index mux, its array read and its tag compare.

**If the lookup re-registers the same answer, the register can simply keep it.**
That is an identity rather than an approximation, and it rests on one fact:
every piece of predictor state — `btb_q`, `btb_valid_q`, `ras_q`, `ras_sp_q`,
`ras_cnt_q` — is written under `upd_valid` alone, and `upd_valid` is provably
false whenever the front end stalls. `ex_bp_upd` carries `!ex_stall` directly,
and `id_stall` requires the EX instruction to be a **load**, which is neither a
branch nor a jump. Two independent reasons, one assertion
(`a_no_bp_update_under_front_stall`), proved at depth 14.

`flush` outranks `hold` **inside the module** rather than by agreement with its
caller, so a redirect arriving during a stall clears a prediction that is about
an address the core will no longer fetch.

### The `|| flush` term is kept and deliberately not mutated

Removing it escapes five catchers — `directed:a19_bpred`, `rvfi:pc_fwd_ch0`,
`random:loaduse`, `random:branch`, `directed:a7_loaduse` — and the reason is
worth writing down. `front_stall && ex_redirect` needs the instruction in EX to
be both a load and something that redirects. `ex_stall` is excluded
structurally. `ex_mret` is not a load. **`ex_mispredict` cannot happen to a load
at all**: only a branch or a jump ever allocates a BTB entry, and index and tag
together are the whole word address, so a load can never hit and never be
predicted taken. What is left is a *misaligned* load with a dependent instruction
behind it — and even then the consequence is one stale prediction, corrected by
the next EX resolution. A20 settled what to do with guards like this: keep the
guard, do not pretend a test covers it.

---

## Levers 3 and 4 were dropped, with evidence rather than by omission

**Lever 3 — narrowing the mispredict comparison.** §3.4 measured it at 2.03 ns
of a 12.954 ns path. After levers 1, 2 and 5 the critical path does not contain
it: it runs from the instruction memory through the decoder and the hazard unit
to the ID/EX register, and `ex_mispredict` is nowhere on it. A26's own text says
the lever "may cost a few cycles; if it costs any, it moves to A27's phase or is
dropped." **It would cost cycles for a benefit that is now zero**, so it is
dropped.

**Lever 4 — `MAX_FANOUT`.** §3.4 aimed it at a fanout-131 net (`if_id_q[valid]`)
and a fanout-66 net, both on segments of the old path. Both are gone. The largest
fanout on the new critical path is **41**, and its route delay is spread across
eight hops rather than concentrated in one. There is no high-fanout net left to
split. A17 tried this lever and it did nothing; the reason is now a number rather
than an anecdote.

---

## The new critical path, which is A27's input

After A26 the worst path is, at 96.246 MHz:

```
Source:       u_ram/mem_reg_2_0_7/CLKBWRCLK          (instruction memory)
Destination:  u_core/id_ex_q_reg[pred_target][4]/D   (the ID/EX register)
10.358 ns   logic 3.446 (33.3%)   route 6.912 (66.7%)   8 logic levels
```

Hop by hop: **instruction memory → decoder (four LUT levels) → load-use hazard
→ the ID/EX register's stall-and-flush mux.** Every stage of it is in the front
end. The EX stage — the forwarding network, the ALU, the mispredict
comparison — appears nowhere, which is what levers 1, 2 and 5 were for.

**Two properties of it are A27's input.** It is **66.7% route delay at 8.8%
utilisation**, which is the case floorplanning exists for. And it is only
**eight logic levels**, so there is very little left to remove logically: what
remains is `uses_rs1`/`uses_rs2` falling out of the decoder's full `unique case`
before the hazard comparator can start. Shortening that means a second, shallow
decode path — real RTL surgery on the one module whose equivalence against the
Python decoder is swept over 10⁶ words — and it is **not** cycle-neutral work
in the sense A26 requires it to be checked, so it is out of this step's scope
and named here instead.

---

## Verification

Cycle counts **bit-identical to A25** in every counter, measured with
`tb/perf/run_stall_profile.py` at 200 Dhrystone runs and 2 CoreMark iterations —
the same instrument A18 built and A20 validated against the core's own counters:

| region | cycles | retired | load-use | multi-cycle EX | flush | redirects |
|---|---:|---:|---:|---:|---:|---:|
| Dhrystone | 122 896 | 106 622 | 5 200 | 7 000 | 4 074 | 2 037 |
| CoreMark | 672 709 | 576 435 | 41 428 | 37 584 | 17 262 | 8 631 |

Every one of those is A25's number to the unit, and A18's identity still closes
at residual exactly zero in all four regions.

Formal and simulation, all at `MUL_CYCLES = 3` (the latency A26 was developed
against; A28 later took it to 2 and re-ran everything):

| | |
|---|---|
| `riscv_formal` | **77/77 at depth 14** — including the three assertions A26 adds: `a_jalr_target_matches_alu`, `a_pipe_advances_together` and `a_fwd_precompute_matches_a/b`, plus `a_no_bp_update_under_front_stall` |
| `formal_forward`, `formal_hazard`, `formal_bpred` | pass, with `a_hold_freezes` new in the predictor |
| `riscof_arch_test` | 118/118 |
| cosim, `rv32um`, `stall_profile` | pass; the identity closes at residual exactly zero |
| `mutation_pipeline` | 88 mutations, all caught as declared |

**`a_fwd_precompute_matches` is the one that earned its place.** It is not a
restatement of lever 2's transformation — it is a second copy of the computation
the transformation replaced, asserted equal. It failed on all 77 checks the
first time it ran, which is how the multi-cycle-stall case above was found; and
after the fix it passes on all 77, which is how the fix is known to be complete
rather than merely sufficient for the cases anyone thought of.
