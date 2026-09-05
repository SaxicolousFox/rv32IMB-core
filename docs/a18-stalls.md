# A18 — where the cycles go

`MODS_A` A18, in its own words: *"you cannot honestly report a predictor's
payoff without an instrument that measured the baseline first, and 'we added a
predictor and the benchmark got faster' is not an attribution."*

This is the baseline, on the **RV32IM** core as built by A14–A17.

- instrument: `tb/perf/tb_profile.cpp` + `tb/perf/run_stall_profile.py`
- regression entry: `stall_profile` (small config, `--selftest`)
- recorded runs: `fpga/build/a18_rv32im.json`, `fpga/build/a18_rv32i.json`
- the trace it emits for A19: `--trace`, consumed by `tb/perf/project_bpred.py`

---

## The identity, and why it is the whole point

    cycles = retired + load-use stalls + multi-cycle EX stalls + 2 x redirects

**The residual is exactly zero in every region below.** It is checked in the
script, not eyeballed, and `--selftest` breaks the accounting five ways and
requires each break to be caught. Four of the five move the residual. The fifth —
a redirect count that is off by one — leaves the residual at **exactly zero**,
because the identity *counts* redirects and a wrong count moves both sides of it
together. It is caught only by the second closure.

**The second closure, and how A19 changed it.** Before the predictor it was

    redirects = taken branches + JAL + JALR

which held exactly. `MODS_A` §3.3 said in advance that A19 would break it:
*"A19 changes `flush_penalties` from `2 x redirects` to `2 x mispredicts`, and a
mispredict count cannot be derived from the retired stream alone... the right fix
is to model the predictor from its specification."* A correctly predicted taken
transfer does not redirect, so the old identity is simply false, and it was
replaced by

    redirects (measured in the RTL) == mispredicts (predicted by model/bpred.py)

which is strictly stronger. The old closure compared two things the *hardware*
counted; this one compares what the hardware counted against what
`docs/a19-bpred-spec.md` says it should have — over a whole benchmark rather
than a directed test.

**It found a real modelling error on its first run.** It agreed exactly on
CoreMark and was 39 short of the RTL's 396 on Dhrystone, all of them returns. The
predictor's update-visibility rule had been expressed on RETIRE cycles, and
`retire − fetch` is not constant: an instruction held in ID by a load-use
interlock has already had its prediction made, so the rule was short by exactly
the number of cycles it was held. **Dhrystone reaches its callees through
load-use stalls and CoreMark does not, which is exactly how the error hid.**
`tb_profile.cpp` now dates every transfer by its *fetch* cycle — mirroring the
core's two front-end register load conditions, and checking that mirror by
requiring its FIFO never to underflow — and the model and the RTL now agree to
the unit on both benchmarks.

## Why it is a simulation and not a counter in the core

Three reasons, in order of weight.

1. **It does not have to be in the core.** This SoC is deterministic to the
   cycle, and the simulation reproduces the board exactly — see the validation
   below. `MODS_A` §1's own baseline table was produced this way.
2. **Counters would perturb what A17 just bought.** A17 spent four
   implementation runs moving the critical path; adding counters immediately
   afterwards would mean the Fmax being reported is not the Fmax of the design
   being reported.
3. **A19's headline numbers come from A16's hardware procedure anyway.** The
   instrument supplies the attribution, not the score.

**The validation that makes this legitimate** is that the instrument counts
cycles independently of the core and then agrees with the core's own counter:
`mcycle − instrument cycle` is **constant at −3** over every one of the 41
`csrr mcycle` retirements in the run, which is exactly the EX→WB distance of the
`csrr` that read it. A drifting offset would mean the instrument was not counting
this machine's cycles, and every number here would be quietly wrong rather than
obviously wrong.

And the region totals reproduce hardware **to the cycle**, in two independent
ways.

**The NTT regions match absolutely.** A16 measured them on the board at 205,884
and 39,058 cycles; the instrument reports 205,884 and 39,058. Same numbers, no
scaling, nothing fitted.

**Dhrystone matches as an exact linear model.** The board ran 200,000 runs and
the instrument runs 2,000 — a hundredfold difference in scale — and the two
measurements agree on **both** the slope and the intercept:

    cycles       = 777 x runs + 30
    instructions = 533 x runs + 32

| | on the board (200,000 runs) | in simulation (2,000 runs) |
|---|---|---|
| timed cycles | 155,400,030 | 1,554,030 |
| = 777 x runs + | **30** | **30** |
| timed instructions | 106,600,032 | 1,066,032 |
| = 533 x runs + | **32** | **32** |

Two points determine a line, so the *slope* being 777.000000 is arithmetic
rather than evidence. **The intercept is the evidence**: the fixed cost of
entering and leaving the timed region is 30 cycles and 32 instructions, and
silicon and simulation independently produce the same two integers. A model that
was merely close would not do that.

---

## The baseline — RV32IM (A16/A17 core)

2,000 Dhrystone runs, 3 CoreMark iterations, and the two NTT builds of A16's dual
baseline, all from one image.

| | Dhrystone | CoreMark | NTT `rv32i` | NTT `rv32im` |
|---|---|---|---|---|
| cycles | 1,554,030 | 1,234,083 | 205,884 | 39,058 |
| retired | 1,066,022 | 864,600 | 148,647 | 23,797 |
| **IPC** | **0.6860** | **0.7006** | 0.7220 | 0.6093 |
| flush cycles | 364,008 — **23.4%** | 222,780 — **18.1%** | 56,334 — 27.4% | 5,398 — 13.8% |
| load-use stalls | 52,000 — 3.3% | 62,139 — 5.0% | 903 — 0.4% | 1,799 — 4.6% |
| multi-cycle EX stalls | 72,000 — 4.6% | 84,564 — 6.9% | 0 | 8,064 — **20.6%** |
| **residual** | **0** | **0** | **0** | **0** |
| taken branches | 116,000 | 88,282 | 24,453 | 777 |
| not-taken branches | 58,001 | 71,335 | 16,836 | 380 |
| `JAL` | 36,002 | 16,696 | 1,920 | 1,024 |
| `JALR` | 30,002 | 6,412 | 1,794 | 898 |

**Control flow is still where the cycles go**, and adding `M` did not change
that: 364,008 of Dhrystone's 488,008 lost cycles and 222,780 of CoreMark's
369,483. The forwarding network is doing its job; the fetch is not. That is A19's
mandate, restated on the machine A19 will actually be built on.

**The multiply-heavy region is the one that looks different.** The `rv32im` NTT
spends **20.6%** of its cycles stalled in the multiplier — five times CoreMark's
share and the largest single non-control term anywhere in the table. It is also
the region with the fewest branches. This is the shape `MODS_A` §1 predicted when
it said `M`'s benefit is concentrated in ML-KEM's polynomial arithmetic, and it
is worth carrying into §8: an `Xkntt` unit inherits A14's EX handshake, so this
20.6% is the closest thing there is to a preview of what the coprocessor's own
occupancy will cost.

---

## RV32I, for continuity with `MODS_A` §1

The same instrument, same regions, on an image compiled `-march=rv32i` and run on
the same RV32IM core.

| | Dhrystone | CoreMark (per iteration) |
|---|---|---|
| cycles | 1,558,030 | 1,040,921 |
| retired | 1,126,022 | 721,356 |
| IPC | 0.7227 | 0.6930 |
| flush cycles | 380,008 — 24.4% | 298,852 — 28.7% |
| load-use stalls | 52,000 — 3.3% | 20,713 — 2.0% |

`MODS_A` §1's independently-measured table says 1,558,048 / 1,126,032 / 380,016 /
52,000 for Dhrystone. This instrument says 1,558,030 / 1,126,022 / 380,008 /
52,000 — and the difference is not "small", it is **fully accounted for**:

| | §1 | here | difference |
|---|---|---|---|
| cycles | 1,558,048 | 1,558,030 | 18 |
| retired | 1,126,032 | 1,126,022 | 10 |
| flush cycles | 380,016 | 380,008 | 8 |
| `JAL` | 38,004 | 38,002 | 2 |
| `JALR` | 32,004 | 32,002 | 2 |
| taken branches | 120,000 | 120,000 | **0** |
| not-taken branches | 74,001 | 74,001 | **0** |
| load-use stalls | 52,000 | 52,000 | **0** |

§1 bracketed a window ten instructions wider, of which **four are control
transfers** — two calls and two returns — costing 4 × 2 = **8 flush cycles**. And
10 + 8 = **18**, which is the entire cycle difference. Nothing is left over.

Two instruments written a month apart, from different premises, disagreeing by
exactly one call/return pair at each end of the window and by nothing else at
all, is a better result than agreeing would have been: agreement to within a
rounding error would have been consistent with both being approximately right.

CoreMark's comparison is looser only because §1 measured one iteration and this
measures three: 1,040,921 against 1,040,943 cycles per iteration, with the same
structure to the residual (18 extra instructions, 4 extra flush cycles, 22
cycles) and an identical 20,713 load-use stalls.

---

## What changed when `M` arrived

| per 2,000 Dhrystone runs | RV32I | RV32IM |
|---|---|---|
| retired | 1,126,022 | 1,066,022 — **5.3% fewer** |
| cycles | 1,558,030 | 1,554,030 — 0.3% fewer |
| IPC | 0.7227 | **0.6860 — worse** |
| stall + flush per retired instruction | 0.384 | **0.458** |

A16 reported this from the outside; A18 says where it went. Dhrystone retires
60,000 fewer instructions and pays 72,000 cycles of multiplier stall for them —
it buys back 16,000 cycles of flush by having fewer branches, and the net is
4,000 cycles on 1.55 million. **`M` is very nearly free on Dhrystone and enormous
on CoreMark**, and the reason is visible in one row of the table above: CoreMark
retires 84,564 cycles of multiplier occupancy against a workload that was
spending 1.9 million cycles in libgcc.

---

## Fault injection

The check is "the residual is zero", and a check never observed to fail is not
evidence. `--selftest` runs in the regression:

| fault | cycle residual | mispredict residual | |
|---|---|---|---|
| a flush is charged one cycle instead of two | +18,036 | 0 | caught |
| load-use interlock cycles are not counted | +52,000 | 0 | caught |
| multi-cycle EX stall cycles are not counted | +72,000 | 0 | caught |
| the retired count is off by one | −1 | 0 | caught |
| **the redirect count is off by one** | **0** | **−1** | **caught, by the second check only** |

The last row is the reason the second check exists: a single redirect too many or
too few is what a predictor bug looks like from outside, and the cycle identity
is structurally unable to see it — it *counts* redirects, so a wrong redirect
count moves both sides of it together.

## Where a redirect's cost actually lands

`ex_redirect` at cycle *t* clears IF/ID and ID/EX, so the two cycles in which
nothing retires are **later** than the pulse. Charging them to the pulse's cycle
makes the per-region identity wrong by exactly 2 whenever a redirect fires within
a few cycles of a region boundary — which never happened before A19 and does now,
because **a not-taken branch predicted taken is a redirect at a pc that never had
one**.

The offset is swept rather than argued. `--redir-delay` against the Dhrystone
residual:

| delay | 0 | 1 | **2** | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| residual | +2 | +2 | **0** | 0 | 0 | 0 |

Anything from 2 upward closes it; the default is **3**, which is what the
pipeline structure predicts and which sits inside the measured range. The sweep
bounds it, the derivation picks within the bound, and neither is asked to do the
other's job.