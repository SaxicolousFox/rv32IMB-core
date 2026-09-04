# A25 — a shorter multiply

**The first step in this round that changes cycle counts**, and it is kept in
its own step with its own before/after for exactly that reason. `MODS_A2` §3.4
keeps cycle-changing Fmax work strictly separate from cycle-neutral Fmax work;
this is the cycle-changing half, and A26 is the other.

---

## What changed

A14 built the multiplier as three named registers — operand, product, delay —
with `MULDIV_MUL_CYCLES` hardcoded at 4 beside them. The two were only related
by a comment. A25 makes the pipeline depth **derived from the latency**:

```systemverilog
localparam int MUL_PIPE = MUL_CYCLES - 2;   // product-side register levels
```

Occupancy `L` gives `L−1` clock edges between the operands appearing and the
result being read; one of those is spent capturing the operands, so the product
pipeline gets `L−2`.

| `MUL_CYCLES` | arrangement |
|---|---|
| 4 | operands │ product │ delay — A14's |
| 3 | operands │ product |
| 2 | operands, then combinational |

**At `L = 2` the 33×33 is combinational from the *operand register*, not from
the module input.** That distinction is the whole reason the operand capture is
the register that survives rather than the product register: the alternative
puts a 33×33 multiplier directly behind the forwarding mux, which is where the
core's critical path already is.

`MUL_CYCLES` became a module **parameter** — defaulted from `rv32i_pkg` — for
one reason: A25 has to answer "what does one register level cost in
nanoseconds", and only a real post-route run answers that.
`fpga/scripts/synth_ooc.sh` sweeps it out of context. **Nothing instantiates the
module with a different value**, and `tb/unit/test_isa_consistency.py` now
checks both halves of that: the default *is* the package constant, and nothing
in `rtl/` passes the parameter by name. Both were fault-injected.

---

## What it is worth, in simulation

Measured with `tb/perf/run_stall_profile.py` at 200 Dhrystone runs and 2 CoreMark
iterations, the same instrument A18 built and A20 validated against the core's
own counters.

| region | metric | `MUL_CYCLES` = 4 | `MUL_CYCLES` = 3 | change |
|---|---|---:|---:|---:|
| Dhrystone | cycles | 123 096 | 122 896 | **−200 (−0.16%)** |
| | retired | 106 622 | 106 622 | 0 |
| | IPC | 0.8662 | 0.8676 | +0.16% |
| | multi-cycle EX stalls | 7 200 | 7 000 | −200 |
| CoreMark | cycles | 691 501 | 672 709 | **−18 792 (−2.72%)** |
| | retired | 576 435 | 576 435 | 0 |
| | IPC | 0.8336 | 0.8569 | **+2.79%** |
| | multi-cycle EX stalls | 56 376 | 37 584 | −18 792 |

The cycle reduction and the stall reduction are **the same number** in both
regions, to the cycle, and A18's identity still closes at residual exactly zero
in all four regions.


**The multi-cycle EX stall term decomposes exactly**, which is the check that
the change did what it claims rather than something else that happened to be
the same size. With `3M + 33D` stall cycles at `L = 4` and `2M + 33D` at
`L = 3`:

| region | multiplies | divides |
|---|---|---|
| Dhrystone (200 runs) | 200 | 200 |
| CoreMark (2 iterations) | 18 792 | **0** |

Dhrystone does exactly one multiply and one divide per run. **CoreMark does no
divides at all**, so its entire multi-cycle stall term is multiplies and cutting
`MUL` from 4 cycles to 3 removes exactly one third of it. Dhrystone's is 92%
divider, which A25 does not touch — hence the 0.16%.

**`MODS_A2` A25 predicted this backwards, and the prediction is left standing
in the plan rather than quietly corrected.** It expected Dhrystone's IPC to rise
noticeably — "A16 measured Dhrystone giving back almost all of `M`'s
instruction-count win in 4-cycle `MUL` stalls, and this is the direct reversal
of that" — and CoreMark to gain less. The opposite happens, for a reason that is
now measured rather than assumed: A16's observation was about Dhrystone's
*instruction count* falling when `M` replaced libgcc calls, and libgcc's
`__divsi3` is the call that dominated. The hardware divider inherited that
weight, and it is still 34 cycles.

Retired instruction counts are **identical** in every region — 106 622 and
576 435 — which is the expected invariant: a latency change moves cycles, never
instructions.

> **These figures are `-march=rv32im`, and Dhrystone's 200-cycle saving does not
> survive to the shipped image.** `run_stall_profile.py` builds without B; the
> benchmark image is `rv32imb`, where the compiler strength-reduces Dhrystone's
> multiply into Zba shift-adds and A25 saves it **nothing at all**. The board
> section below has the measurement and the arithmetic. CoreMark's figure is
> unaffected and reproduces to a third of a percent.

---

## What the third register stage was actually worth

A14 chose 4 cycles so Vivado could pack `AREG`/`BREG`, `MREG` and `PREG` into
the DSP48E1 — plan §B1's advice, for plan §B1's reason. A25 measured it.
`fpga/scripts/probe_tier1.py --top rvntt_muldiv --param MUL_CYCLES`, out of
context on `xc7a100tcsg324-1` **−1**, binary search on the constraint with the
verdict from post-route WNS:

| `MUL_CYCLES` | Fmax | LUT | FF | DSP | worst reg-to-reg endpoint |
|---|---:|---:|---:|---:|---|
| 4 | **≥ 160 MHz** (+0.558 ns) | 402 | 239 | 4 | `m_pipe_q_reg[0]/PCIN[0]` |
| 3 | **≥ 160 MHz** (+0.558 ns) | 404 | 175 | 4 | `m_pipe_q_reg[0]/PCIN[0]` |
| 2 | **≥ 160 MHz** (+1.557 ns) | 403 | 141 | 4 | `quo_q_reg[0]/D` — *the divider* |

**All three cleared the search's upper bound, so 160 MHz is a lower bound and
not a measurement** — the script says so in its own output rather than letting
the number be quoted as an Fmax. That is enough: the core is at 74.577 MHz today
and this round's target is 110, so an internal path with 50 MHz of headroom at
every latency is not the thing to spend runs refining.

**The third stage was buying nothing this core can use**, and CoreMark was
paying 18 792 cycles for it. `MUL_CYCLES = 3` is structurally free: the result
still comes from a register (`m_pipe_q[0]` instead of `m_pipe_q[1]`), so the
path the *core* sees — the unit's `result` into the writeback and forwarding
muxes — is unchanged in depth. Nothing moved onto or off the core's critical
path, which is what A25 asked to be confirmed rather than assumed.

### Why the answer is 3 and not 2, and what is *not* evidence

The table above appears to say 2 is free as well. **It does not, and the reason
is the boundary this OOC method carries.**

At `MUL_CYCLES = 2` the 33×33 product is combinational and drives the module's
`result` **output port**. The OOC run sets no input or output delays — stated in
`synth_ooc.tcl` and in `docs/a24-tier1-probe.md` as a deliberate boundary — so
that path is not timed at all. The reg-to-reg filter excludes it by
construction, and the endpoint moving to `quo_q_reg[0]/D` says exactly that: at
`MUL_CYCLES = 2` the worst path Vivado *could* see is in the divider, because
the one that changed left the netlist through a port. **The 160 MHz for
`MUL_CYCLES = 2` is a measurement of everything except the thing being
measured.**

Where that path really terminates is the core's writeback mux, next door to the
critical path A12 identified and A26 exists to attack — forwarding mux, ALU
result mux, misaligned-address check, BRAM byte enables. Putting a combinational
33×33 in front of it is the wrong direction for the step that comes next.

> **A28 measured it and adopted 2.** The paragraph below stood for exactly as
> long as it took A26 to give the SoC a clock worth probing at. At the adopted
> 96.246 MHz a 2-cycle multiply closes with **WNS +0.004 ns** against 3 cycles'
> **+0.010** — six picoseconds, on a design whose build-to-build spread is over
> a nanosecond — and it is worth a further **23 490 000 CoreMark cycles**. The
> reasoning below is left standing because it is why 2 was *not* adopted in A25:
> the OOC method genuinely cannot see that path, and adopting a latency on a
> measurement that excludes the thing it changes would have been luck rather
> than evidence. See `docs/a28-benchmarks.md`.

So: **3, and 2 is not ruled out — it is unmeasured.** It is worth another
18 792 CoreMark cycles (a further 2.7%), and the run that would settle it is a
full SoC implementation, which A26 and A28 do anyway. `MUL_PIPE` is derived from
`MUL_CYCLES`, so the experiment is a one-line change when there is a reason to
run it. `MODS_A2` A25's stop rule — *"if 2 cycles cannot be reached without a
data-dependent path, stay at 3"* — names the wrong hazard for this design: there
is no data-dependent path at any of the three, because the multiplier is a
register chain whose length is its latency. The hazard is Fmax, and it is the
one recorded here.

---

## It is not Fmax-neutral, and the multiplier is not why

A25 was expected to help Fmax rather than fight it — moving work into DSP
internal registers takes fabric logic *off* the design, and A14's note says
neither the multiplier nor the divider is on the critical path. `MODS_A2` A25
asks for that to be **confirmed afterwards rather than assumed to have
survived**. It did not survive.

Rebuilt at A23's exact clock — 74.576 271 MHz, `explore_postroute`, the same
strategy, the same tools — the A25 netlist **misses timing by 0.423 ns** and no
bitstream is produced. 5757 LUTs and 1963 FFs against A23's 5770 and 2027: the
design is *smaller* by the 64 flops that were removed.

**The failing path is not the multiplier's.**

```
u_core/mem_wb_q_reg[rd_addr][2]/C  ->  u_core/pc_q_reg[11]_rep__0/D
13.757 ns   logic 4.742 (34%)   route 9.015 (66%)   20 levels
```

That is the writeback-stage `rd_addr` feeding the forwarding network and ending
at the PC — the core's own redirect path, the same family A12, A17, A19 and A23
have each in turn found at the top of the list. **`rvntt_muldiv` appears nowhere
on it.**

**The comparison is one-variable, and that is worth stating precisely.**
`fpga/scripts/build_soc.sh` stages only `rtl/core`, `rtl/common` and `rtl/soc`;
`rtl/probe/` is not among them, so A24 changed nothing in this netlist. The last
commit before A25 to touch any staged source is A23's own. The memory image is
**byte-identical** to `bench_a23.mem`. So the single difference between the build
that closed at 74.577 MHz and the build that missed it by 0.423 ns is
`MULDIV_MUL_CYCLES`.

**And that still does not make it a cost of the multiply.** Removing 64 flops
perturbs placement globally, and this project has measured that twice: A12 lost
**3 MHz** to correcting two output pins with no logical content at all, and A19
recorded a 0.9 ns spread on one netlist. 0.423 ns is inside the ±0.4 ns band A12
recorded. One build cannot separate "the change cost this" from "a different
design placed differently", and **A28 is the step that measures Fmax properly**;
A26's cycle-neutral levers target this exact path, and §3.4 lists two worth
about 2.7 ns and 1.0–1.5 ns.

What is recorded here, without inflation: **the A25 netlist does not close where
A23's did.** The board numbers below are therefore taken at **72.000 MHz**, the
nearest MMCM point this netlist closes at. Cycle counts and IPC do not depend on
the clock and are directly comparable with A23's; the derived rates are quoted at
72 MHz and are not.

### On the board

Three JTAG programming passes, four blocks each, at 70.000 000 MHz. **All 42
integer counters identical across all three passes** — the same standard A23
met, applied to twelve report blocks.

| region | metric | A23 (`MUL` 4) | A25 (`MUL` 3) | change |
|---|---|---:|---:|---:|
| **Dhrystone** | cycles | 1 216 000 065 | 1 216 000 065 | **0** |
| | instret | 1 062 000 032 | 1 062 000 032 | 0 |
| | multi-cycle EX stalls | 66 000 000 | 66 000 000 | **0** |
| | IPC | 0.8734 | 0.8734 | 0 |
| | DMIPS/MHz | 0.9361 | 0.9361 | 0 |
| **CoreMark** | cycles | 784 528 484 | 761 038 484 | **−23 490 000 (−2.99%)** |
| | instret | 640 820 044 | 640 820 044 | 0 |
| | multi-cycle EX stalls | 70 470 000 | 46 980 000 | **−23 490 000** |
| | IPC | 0.8168 | **0.8420** | +3.08% |
| | CoreMark/MHz | 3.1866 | **3.2850** | **+3.09%** |
| **ML-KEM NTT** | `rv32i` cycles | 165 752 | 165 752 | **0** |
| | `rv32im` cycles | 33 946 | **31 258** | **−2 688 (−7.9%)** |
| | cycle ratio | 4.8828 | **5.3027** | +8.6% |
| **SHAKE128** | `rv32im` cycles | 373 407 | 373 407 | 0 |
| | `rv32imb` cycles | 312 402 | 312 402 | 0 |

Load-use stalls, redirects, mispredicts, BTB hits and taken transfers are
identical everywhere, which is the invariant a pure latency change has to
satisfy.

### Every delta closes exactly

**CoreMark's cycle saving and its stall reduction are the same number**:
23 490 000. A23's `exstall` of 70 470 000 divided by 3 stall cycles per `MUL` is
**23 490 000 multiplies**, and A25 removes exactly one cycle from each. CoreMark
performs **no divides at all**, so its entire multi-cycle EX term is multiplies —
and the term fell by precisely a third.

**The NTT saves 2 688 cycles, which is 2 688 multiplies.** The `rv32i` build is
unchanged because it contains no `MUL` instruction at all; libgcc multiplies in
software, and software multiplication is unaffected by the hardware
multiplier's latency. SHAKE128 is unchanged for the same reason from the other
direction: it has no multiplies.

### Dhrystone gains nothing, and B is why

**Zero change in every Dhrystone counter**, and the counters say why rather than
leaving it to be guessed. `exstall` is 66 000 000 over 2 000 000 runs — exactly
**33 per run**, which is one divide (34 cycles of occupancy, 33 of stall) and
**no multiplies whatsoever**.

Dhrystone *does* contain a multiply. At `-march=rv32imb` the compiler
strength-reduces it into Zba shift-adds, so **A21 had already removed the thing
A25 makes cheaper.** The simulation figures above, taken at `-march=rv32im`, show
Dhrystone saving 200 cycles per 200 runs precisely because B is absent there.

That is a cross-step interaction worth stating plainly: **the value of a shorter
multiply depends on which other extensions are enabled**, and measuring it on a
build without B would have over-predicted Dhrystone by 2 000 000 cycles and
predicted nothing wrong about CoreMark. Both benchmarks were measured on the
image that ships.

### `MODS_A2` §3.6 P1, arriving as pre-committed

The plan required this consequence to be written down *before* it was measured,
and it was: **the software NTT baseline got faster, so §10 M2's reported
coprocessor speedup gets smaller.** The `rv32im` NTT is now 31 258 cycles rather
than 33 946, and the RV32I-to-RV32IM ratio rises 4.8828 → 5.3027. Any future
speedup claim divides by the smaller number. **That is the correct direction**,
and the number to divide by is `docs/a25-benchmarks.json`, not A16's.

---

## Fault injection

`tb/mutate/run_mutation.py` gains `mul_pipeline_deeper_than_its_latency`:
`MUL_PIPE` derived as `MUL_CYCLES − 1` instead of `MUL_CYCLES − 2`, so the
product pipeline is one register longer than the occupancy the core stalls for.
`done` then fires while the product is still in flight and every `MUL` returns
the *previous* multiply's answer. Caught by `directed:a14_muldiv`,
`random:muldiv` and `riscv:rv32um/mul`.

**The opposite direction is deliberately not a mutation.** A pipeline
*shallower* than the latency is invisible to every value check, for the same
reason A14's operand-enable mutation escaped correctly: the operands are held,
so the product arrives early and simply sits there being right. It wastes a
cycle and changes nothing else, which is `muldiv_done_one_cycle_late`'s
territory — and that entry already exists.

A14's two latency mutations (`muldiv_done_one_cycle_early` / `_late`) still
apply unchanged, because they anchor on the `done` condition rather than on the
register names A25 removed.

---

## What was verified

Full regression at `MUL_CYCLES = 3`: **51 PASS, 1 XFAIL, 2 SKIP, 0 FAIL.**

| | |
|---|---|
| `riscv_tests` | all 8 `rv32um` |
| `riscof_arch_test` | 118/118, `M` suite included |
| `formal_muldiv` | standalone proof, depth unchanged — **the divider sets it, not the multiplier**, so `MODS_A2` A25's "lower than 37, so this gets easier" does not apply |
| `riscv_formal` | 77 checks at depth 14, arithmetic abstracted per M6's recorded boundary |
| `cosim_commit_log`, `cosim_directed` | lockstep against Spike; `cycle_model.py`'s multi-cycle term is now `Σ(latency−1) = 2` for `MUL` and still predicts every span exactly |
| `mutation_pipeline` | 87 mutations, all caught as declared |
| `stall_profile` | A18's identity closes at residual exactly zero in all four regions |
| `bench_hardware` | 3 JTAG passes, 42 integer counters identical |

**`formal_muldiv`'s depth did not fall**, and the plan expected it to. The proof
is bounded by the 34-cycle divider, which A25 deliberately does not touch — it
stays data-independent by construction because `tb/cosim/cycle_model.py` is
unbuildable otherwise.

---

## Reproducing it

```sh
source toolchain/env.sh
python3 fpga/scripts/gen_soc_clk.py --mhz 70.0
python3 fpga/scripts/build_bench_image.py --out fpga/generated/bench_a25.mem \
    --elf fpga/generated/bench_a25.elf --core-hz 70000000 \
    --dhry-runs 2000000 --iterations 2500 --arch rv32imb --ntt --hpm --keccak
SOC_MEM=$PWD/fpga/generated/bench_a25.mem OUT=$PWD/fpga/build/bench_a25 \
    bash fpga/scripts/build_soc.sh 1 1 explore_postroute
RVNTT_HW=1 python3 tb/run_regress.py -k bench_hardware
```

The out-of-context latency sweep, which needs no board:

```sh
python3 fpga/scripts/probe_tier1.py --top rvntt_muldiv --param MUL_CYCLES \
    --value 4 --value 3 --value 2 --lo 80 --hi 160 --iters 4
```

Raw data: `docs/a25-benchmarks.json` (board) and `docs/a25-muldiv-probe.json`
(OOC). Both need `dangerouslyDisableSandbox: true` for the Vivado half.
