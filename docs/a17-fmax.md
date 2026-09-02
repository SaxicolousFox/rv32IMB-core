# A17 — Fmax recovery: address generation off the store path

`MODS_A` A17 exists because A12's explanation of its own Fmax number was wrong,
and it names four levers to try, in order, **each measured on its own**. This is
the record of doing that. Two of the four paid; two did not, and the two that
did not are written up at the same length as the two that did.

**Result: 73.752 MHz → 86.490 MHz, +17.3%** (the MMCM's exact frequency is
86.486486 MHz; `fmax_search.py` quotes Vivado's period rounded to three decimals), with the benchmark cycle counts
bit-identical to A16's — which is the done-when that makes the number mean
"the same machine, faster" rather than "a different machine".

---

## The method, and one thing it taught

Fmax is measured by A12's binary search on **post-route WNS**: constrain a
period through the MMCM divider, implement to routed, read the worst slack from
the post-route report, and bisect until the fastest constraint that passes is
found. `1/(T − WNS)` is never reported, because the router optimises to the
constraint and stops — a run that passes with 2 ns to spare says only that the
router had no reason to try harder.

**A17 turned that from a principle into a measurement.** Lever 1 was first
looked at by re-running the *old* constraint, 73.0 MHz, and comparing slack:
+0.111 ns before, +0.290 ns after. A 0.18 ns improvement — disappointing against
the 2–3 ns `MODS_A` expected, and it would have been reported as such. The
binary search then found the design passing at **83.542 MHz**, 1.59 ns faster.

The slack at a loose constraint is not a measure of a design's speed; it is a
measure of when the router stopped. Every lever below therefore got its own
search, not a slack comparison — which is also what `MODS_A` A17 asked for and
the reason it asked.

The other thing the method needs stating: **A12 measured a ±0.4 ns
design-to-design placement spread**, and at 84 MHz a period is 11.9 ns, so ±0.4
ns is **±2.8 MHz**. Nothing below 3 MHz is distinguishable from placement noise,
and no lever is credited with a movement smaller than that.

---

## Lever 1 — the dedicated address adder — **ADOPTED, +9.79 MHz**

`ex_addr_misaligned` was `|ex_alu_y[1:0]` — bits read off the **muxed** ALU
output, so the whole four-level operation-select mux sat in front of the
alignment check, which gates the trap, which gates the byte enables, which reach
the BRAM's write enable. A12 measured that mux at 33% of the critical path.

A memory address is always `rs1 + imm`. It is never a shift, an AND or a SUB, and
the decoder enforces that: every load and store sets `alu_op = ALU_ADD`,
`alu_src_a = SRCA_RS1`, `alu_src_b = SRCB_IMM`. So

```systemverilog
wire [31:0] ex_mem_addr = ex_rs1_fwd + id_ex_q.imm;
```

feeds `dmem_addr`, the alignment check, the store byte offset, `mtval` and RVFI's
`mem_addr`, and the ALU's result mux is out of the cone entirely. The alignment
check now needs only the bottom two bits of a sum — no carry chain in front of it
at all.

| | A16 | A17 lever 1 |
|---|---|---|
| **Fmax** | 73.752 MHz | **83.542 MHz** (+13.3%) |
| period | 13.559 ns | 11.970 ns (−1.589 ns) |
| WNS at Fmax | +0.005 | +0.036 |
| fastest failing | 74.118 | 83.963 |
| LUTs | 2613 | 2639 (+26) |
| FFs | 1148 | 1157 |

**26 LUTs.** That is what the whole step cost.

### The invariant it rests on, and why it is an assertion

Everything the adder is allowed to do follows from one equality: for the
instructions that use an address, the second adder and the ALU agree. That is a
property of the *decoder*, and decoders get edited, so it is stated as

```systemverilog
a_addr_adder_matches_alu: assert (ex_mem_addr == ex_alu_y);
```

guarded by `RISCV_FORMAL`, which every one of riscv-formal's 43 checks then
proves at depth 14 for free — an assertion in the design is an obligation on all
of them.

### What the post-route path said afterwards

At the loose 73 MHz constraint the path still ran through the ALU's adder, via
`ex_jump_target[1]` → `ex_target_misaligned` → `ex_trap` → the byte enables —
the branch-target alignment check, which shares the trap with the load/store one.
A follow-up lever to split the store's trap term from the branch's was drafted on
the strength of that reading. **The binary search made it unnecessary**: under a
tight constraint the router removes that path, and at 83.542 MHz the critical
path ends at the BRAM's *data* input rather than its write enable, with the ALU
result mux gone. The lever was not built, and that is the second time in this
step that a loose-constraint reading pointed somewhere the design was not.

---

## Lever 2 — `MAX_FANOUT` on the trap net — **REJECTED, no measurable gain**

`ex_trap` drives 142 loads: every byte enable, every store-data bit, every
pipeline-register clear. At lever 1's 83.542 MHz it sits on the critical path
with 0.635 ns of route on its own net and 2.08 ns more in the two hops after it.
`MODS_A` §9 lists driver replication as the technique and A17 expected 0.3–0.5 ns.

`(* max_fanout = 32 *)` on the declaration, and the constraint lever 1 had just
met **failed by 0.263 ns**.

That is inside the ±0.4 ns placement spread, so the honest statement is *no
measurable gain*, not *it made things worse* — one run cannot distinguish those.
Either way there is nothing to adopt, and the attribute was removed. A comment
where it was records that it was tried, so the next person does not spend the
hour again.

The likely reason it had nothing to give: the net's own route is 0.635 ns of an
11.97 ns path — 5%. Replicating a driver splits the fanout but adds a level of
logic and hands the placer a different problem, and 5% is not enough headroom for
that trade to come out ahead.

**Scope of the rejection, stated so it is not over-read.** Lever 2 was measured
against lever 1 under the *default* strategy, which is the design as it stood
when `MODS_A` says to try it. It was **not** re-measured under lever 3's
`explore_postroute`, where the router behaves differently and the answer could
differ. That is what "measure each lever on its own" costs: every verdict is
about the design the lever was applied to, and none of them is about the
combination. Re-testing lever 2 on top of lever 3 is a further hour of Vivado for
a lever that has already shown nothing, and it was not spent.

---

## Lever 3 — implementation strategy — **ADOPTED, +2.95 MHz, and it is at the noise floor**

A12 used Vivado's default flow: `opt_design`, `place_design`, `phys_opt_design`,
`route_design`, no directives. `MODS_A` §9 suggests
`Performance_ExplorePostRoutePhysOpt`, which in a non-project flow is those four
with `-directive Explore` **plus a second `phys_opt_design` after the router** —
the "PostRoutePhysOpt" half of the name, and the half that does something a
place-and-route rerun cannot.

`build_soc.tcl` takes the strategy as a third argument; `explore_postroute`
selects `-directive Explore` on `opt_design`, `place_design`, `phys_opt_design`
and `route_design`, and adds a second `phys_opt_design` after the router.

| | lever 1 (default) | + lever 3 (`explore_postroute`) |
|---|---|---|
| **Fmax** | 83.542 MHz | **86.490 MHz** |
| period | 11.970 ns | 11.562 ns (−0.408 ns) |
| WNS at Fmax | +0.036 | +0.005 |
| fastest failing | 83.963 | 86.745 |
| LUTs / FFs | 2639 / 1157 | 2637 / 1157 |
| build time per run | ~180–630 s | ~185–570 s |

**It is free in area and it is not free in honesty.** 0.408 ns is *exactly* the
±0.4 ns design-to-design placement spread A12 measured by transposing two output
pins. So this lever's entire contribution is the size of the noise the method
already admits to, and a single search cannot separate "the strategy helped" from
"this placement happened to be a good one".

It is adopted anyway, for two reasons that are about cost rather than confidence:
it changes no logic, so it cannot cost a cycle; and it costs only Vivado time,
which is spent whether or not the number moves. But **the 86.490 MHz figure
should be read as 83.5–86.5 MHz with the strategy as a plausible but unproven
cause**, and A12's warning about favourable movements applies to it exactly as it
applied to A16's.

The search itself is not monotonic, again: 86.745 MHz fails by −0.015 ns while
the *faster* 87.781 MHz fails by −0.050 and 92.0 by only −0.238.

| constraint | period | WNS | WHS | |
|---|---|---|---|---|
| 83.542 MHz | 11.970 ns | +0.090 | +0.125 | pass |
| 85.712 MHz | 11.667 ns | +0.017 | +0.029 | pass |
| 86.237 MHz | 11.596 ns | +0.049 | +0.022 | pass |
| **86.490 MHz** | **11.562 ns** | **+0.005** | **+0.084** | **pass — Fmax** |
| 86.745 MHz | 11.528 ns | −0.015 | +0.073 | fail |
| 87.781 MHz | 11.392 ns | −0.050 | +0.075 | fail |
| 91.996 MHz | 10.870 ns | −0.238 | +0.063 | fail |

**The strategy is now part of the recorded measurement.** `build_soc.tcl` takes
it as a third argument, echoes it in `SOC_RESULT`, and `fmax_search.py` writes it
into `fmax.json` — because an Fmax taken under a different strategy is a
different measurement, and A12's, A16's and lever 1's were all taken under
`default`.

**`default` remains the script's default, deliberately.** Making
`explore_postroute` implicit would silently change what every future build
measures, and would make a comparison against A12's 70.131 MHz or A16's 73.752
MHz wrong without anything saying so. The cost of the choice is that someone who
builds at M7.2's clock without passing the strategy gets a bitstream that fails
timing — which is a loud failure, and the right one.

---

## Lever 4 — the memory-size experiment — **measured, ≥ +3.5 MHz, NOT adopted**

128 KB is 32 BRAM tiles; the benchmark image is 21.8 KB. `RAM_WORDS` and
`sw/soc/link.ld` move together — halving the array alone would leave the stack
pointer at `0x80020000`, which `rvntt_ram` aliases straight back onto the
program, and a bitstream that routes but cannot run is not a measurement of
anything.

**64 KB (16 tiles) meets timing at 90.000 MHz with WNS +0.025 ns**, against
86.490 MHz for 128 KB. So the lever is worth **at least +3.5 MHz**, which is
above the ±2.8 MHz that the ±0.4 ns spread allows at this frequency. It is a real
effect, and it is the one lever here whose mechanism is obvious: half as many
tiles is half as much die for the placer to spread a store path across.

| | 128 KB (lever 3) | 64 KB |
|---|---|---|
| Fmax | 86.490 MHz | **≥ 90.000 MHz** |
| WNS at that constraint | +0.005 | +0.025 |
| BRAM tiles | 32 | **16** |
| LUTs / FFs | 2637 / 1157 | 2638 / 1144 |

**The search was stopped after the first point rather than converged, and that is
a deliberate trade.** `MODS_A` A17's bar for this lever is *"adopt it only if the
payoff is large"*, and one passing point above the incumbent already bounds the
contribution well enough to decide against it. Converging it would have cost
several more Explore runs at roughly half an hour each to refine a number that
changes nothing. What is reported is therefore a **lower bound**, ≥ 90.000 MHz,
and it is labelled as one.

**It is not adopted, and the reason is not the 4%.** §1.4 specifies 128 KB, and
the memory is not spare: ML-KEM-768's keys, ciphertext and Keccak state, the
coefficient arrays, and — the part that decides it — the Tier-2 coprocessor's own
buffers, which do not exist yet and cannot be sized against an array that has
already been given away. **Buying 4% of clock now by spending memory the thing
this clock exists to serve has not asked for yet is the wrong order to do it in.**

If it is ever adopted it becomes a third **OVERRIDES** row against §1.4 in
`MODS_A` §2, with the reason recorded there rather than in a commit message —
that is `MODS_A`'s own rule for this lever and it is why the row is not being
written today.

---

## The done-when: not one cycle count may change

Every lever above is combinational or physical restructuring, so **the benchmark
output before and after must be identical except for the clock frequency**. This
is checked rather than argued, in two places:

**In simulation, exactly.** A18's instrument runs the A16 benchmark image on the
A17 RTL and reports, for the timed regions:

| | A16, on the board | A17, in simulation |
|---|---|---|
| NTT `rv32i` cycles | 205,884 | **205,884** |
| NTT `rv32im` cycles | 39,058 | **39,058** |
| Dhrystone timed cycles | 155,400,030 (200,000 runs) | 1,554,030 (2,000 runs) |
| — as `777 x runs + b`, `b` = | **30** | **30** |
| Dhrystone timed instructions | 106,600,032 | 1,066,032 |
| — as `533 x runs + b`, `b` = | **32** | **32** |

The NTT regions are absolute matches — same run counts, same numbers. Dhrystone
was run at a hundredth of the scale, so it matches as a linear model, and the
part of that model which is *evidence* rather than arithmetic is the intercept:
the fixed cost of entering and leaving the timed region is 30 cycles and 32
instructions, and A16's silicon and A17's simulation produce the same two
integers independently.

**On hardware, mechanically.** `fpga/build/bench_a17/` was built at
**86.486486 MHz** with A16's exact run counts (200,000 Dhrystone runs, 2,200
CoreMark iterations, both NTT builds) and programmed three times.
`tb/fpga/compare_bench_runs.py` then compared each capture against A16's, and it
does not compare them by eye:

- **29 invariant fields** — every cycle count, every instruction count, every
  CRC, and every ratio computed from a pair of counters (DMIPS/MHz, CoreMark/MHz,
  both IPCs, the NTT ratios). Any difference at all is a failure.
- **5 scaled fields** — Dhrystones/sec, DMIPS, the raw CoreMark score and the two
  run times. These are counts over a frequency, so they must move by **exactly**
  the frequency ratio, checked to 1 part in 10⁶ rather than merely permitted to
  differ.

All three passes: `BENCH_COMPARE_OK`, at a frequency ratio of **1.172698115**.

| | A16, 73.750000 MHz | A17, 86.486486 MHz |
|---|---|---|
| Dhrystone cycles | 155,400,003 | **155,400,003** |
| Dhrystone instructions | 106,600,032 | **106,600,032** |
| CoreMark cycles | 905,017,031 | **905,017,031** |
| CoreMark instructions | 634,048,425 | **634,048,425** |
| NTT `rv32i` / `rv32im` cycles | 205,884 / 39,058 | **205,884 / 39,058** |
| CoreMark `crcfinal` | 0x33ff | **0x33ff** |
| DMIPS/MHz | 0.7325 | **0.7325** |
| CoreMark/MHz | 2.4309 | **2.4309** |
| IPC (Dhry / CM) | 0.6860 / 0.7006 | **0.6860 / 0.7006** |
| Dhrystones/sec | 94,916.3 | 111,308.2 — ×1.1727 |
| CoreMark score | 179.28 | 210.24 — ×1.1727 |

Reproducibility within each capture: **5 report blocks, worst spread 0 counts.**

The comparison tool is fault-injected and runs in the regression (`bench_compare`)
against A16's own two captures, so the four checks it makes cannot rot: a cycle
added, a CRC bit flipped, a rate off by 1% of the ratio, and a rate not scaled at
all are each rejected. The last of those is checked against a **synthetic**
1.2×-clock copy, because two runs of the same bitstream share a clock and would
make it vacuous — which is the failure mode the whole tool exists to prevent, and
it does not get to do it to itself.

The two together are the check. Simulation alone would leave open the
possibility that the board does something the model does not; hardware alone
would not have caught a cycle-count change until after a fourteen-minute
implementation run.

---

## Where the critical path is now

At 86.490 MHz the path is **10.862 ns, 12 logic levels, 75.9% route** — and its
*source* has moved. A12's and A16's both started at `ex_mem_q_reg[rd_addr]`, the
forwarding mux. This one starts at `id_ex_q_reg[insn][20]`:

| segment | delay | share |
|---|---|---|
| `id_ex_q.insn[20]` → decode → `ctrl.uses_rs2` → operand select | 3.90 ns | 36% |
| → CARRY4 (the ALU adder) | 1.81 ns | 17% |
| → 4 × LUT6 → `ex_trap9_out` (fanout **141**) | 2.92 ns | 27% |
| → byte enables → BRAM `WEA` | 2.24 ns | 21% |

**The forwarding mux is no longer on it.** That is what lever 1 bought: it did not
make the forwarding mux faster, it made the tail behind the ALU short enough that
a *different* head — decode and operand selection out of the instruction register
— became the longest thing in the design.

Three things follow for whoever picks this up next.

1. **The next lever is in ID, not EX.** 36% of the path is spent turning an
   instruction word into control and operand selects, every cycle, from scratch.
   Precomputing the selects one stage earlier is the obvious move and it is a
   *microarchitecture* change, not a timing one — it belongs in a step with its
   own before/after, not in an A17.
2. **The trap net is still there**, still fanning out to 141 loads, still costing
   about a nanosecond of route. Lever 2 said nothing about it under `default`;
   it was not re-measured under `explore_postroute` and might say something
   different.
3. **The store path is still the destination.** Every version of this path since
   A12 has ended at the BRAM's write enable or its data input. Address generation
   came off it; the trap gating did not.
