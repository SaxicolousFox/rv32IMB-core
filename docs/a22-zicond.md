# A22 — Zicond, and what the predictor is actually worth

`MODS_A2` A22. Two instructions, `czero.eqz` and `czero.nez`, one `funct7`
(`0000111`) in `OP`. The build was the easy part; the step exists for the
measurement.

| Check | Result |
|---|---|
| RISCOF `Zicond` suite | **2/2** — total now **120/120** |
| `formal_bitmanip` | Zicond properties, **3/3 injected faults caught** |
| Cosim vs Spike | 30/30 byte-identical with Zicond in the stream |
| A3 decoder equivalence | 10⁶ words; reserved `funct3` under `0000111` still traps |
| Mutations | **3/3 caught** |

Semantics were read from Spike's own `czero_eqz.h` / `czero_nez.h` — `RS2 == 0 ?
0 : RS1` and its complement — not recalled. It lives in the bitmanip lane, for
the same reason B does: what matters is not that it is small but that it stays
off `ex_alu_y`.

**The zero test is on all 32 bits, and that is the one way to get this wrong
quietly.** Every other operation in `rvntt_bitmanip` uses exactly `b[4:0]` —
shifts, rotates, bit indices all do — so the surrounding code actively invites
`b[4:0] == 0`. That is wrong only when `rs2` is nonzero with its low five bits
clear: one operand in 32. It has its own assertion and its own mutation.

---

## The measurement-ordering trap, and which way it was resolved

`MODS_A2` A22 flagged that Zicond removes branches and therefore changes what
A19's predictor is worth, and gave two options: sequence it before the
predictor's numbers are final, or re-measure afterwards and report the delta.

**Re-measuring was the only option left.** M7.2 is complete, committed, tagged
`m7.2-bpred-77mhz` and pushed; reopening it to re-sequence a later extension
would break the project's own rule about not retagging a finished milestone —
the same rule that kept M7's RV32I figures when M7.1 superseded them.

## Two points were not enough, and the third changed the answer

The obvious comparison is `rv32im` against `rv32im+B+Zicond`. Run that way,
Dhrystone shows **2,000 fewer conditional branches and 3,999 fewer
mispredicts**, and it is very tempting to write that Zicond removed a fifth of
Dhrystone's mispredicts.

**It did not. B did.** `Zbb`'s `min`/`max` are themselves if-conversion
instructions and `Zba`'s `sh1add`/`sh2add`/`sh3add` replace multiplies, so a
two-point comparison cannot separate them. A third build —
`rv32imzb`, B **without** Zicond — exists only to make that separation, and it
says:

| Dhrystone | `rv32im` | `+B` | `+B+Zicond` | B did | **Zicond did** |
|---|---|---|---|---|---|
| cycles | 1,230,096 | 1,212,098 | 1,212,098 | −17,998 | **0** |
| retired | 1,066,022 | 1,062,022 | 1,062,022 | −4,000 | **0** |
| conditional branches | 174,001 | 174,001 | 172,001 | 0 | **−2,000** |
| mispredicts | 20,036 | 16,037 | 16,037 | −3,999 | **0** |
| multi-cycle EX stalls | 72,000 | 66,000 | 66,000 | −6,000 | **0** |
| mispredicts / branch | 11.51% | 9.22% | 9.32% | −2.30 pt | **+0.11 pt** |
| IPC | 0.8666 | 0.8762 | 0.8762 | +0.0096 | **0.0000** |

| CoreMark | `rv32im` | `+B` | `+B+Zicond` | B did | **Zicond did** |
|---|---|---|---|---|---|
| cycles | 2,074,049 | 1,883,023 | 1,883,107 | −191,026 | **+84** |
| retired | 1,729,257 | 1,537,997 | 1,537,997 | −191,260 | **0** |
| conditional branches | 319,241 | 319,241 | 319,241 | 0 | **0** |
| mispredicts | 25,691 | 25,784 | 25,825 | +93 | **+41** |
| IPC | 0.8338 | 0.8168 | 0.8167 | −0.0170 | **−0.0001** |

**Zicond is worth approximately nothing on either benchmark**, and that is the
honest result. On Dhrystone it converts 2,000 conditional branches into 2,000
`czero` instructions — a one-for-one swap, with retired count, cycle count and
mispredict count all *exactly* unchanged. On CoreMark the compiler finds no
if-conversion opportunity at all and the +84 cycles are the noise of a slightly
different layout. A22's own "Realistic expectation" said *"do not expect much"*;
this is less than that.

**The predicted shape was half right.** A22 said the mispredict *rate* might go
up while the *count* went down. The count did not move at all; the rate rose
0.11 points purely because the denominator shrank. Reporting only the rate would
have made a neutral change look like a regression, and reporting only the count
would have made it look like nothing happened. Both are in the table for that
reason.

**Every removed branch was NOT taken.** `br_taken + JAL + JALR` is identical
across all three builds at 182,004, while conditional branches fall by 2,000 —
so what Zicond replaced were 2,000 not-taken branches, which a 2-bit bimodal
predicts correctly anyway. That is precisely why removing them buys no cycles.

## The finding that was not about Zicond

**B removed 3,999 Dhrystone mispredicts without removing a single branch.**
That is a 20% change in the mispredict count from an extension that changed no
control flow whatsoever.

It is real, and it is not an RTL artefact: `model/bpred.py` — which reads only
the retired instruction stream and `docs/a19-bpred-spec.md`, and never touches
the RTL — reproduces the RTL's redirect count **to the unit in all three
builds** (20,037 / 16,038 / 16,038). So the change is a genuine consequence of
the instruction stream, not of the implementation.

The mechanism is code layout and stall spacing rather than branch elimination:
B removes 4,000 retired instructions and 6,000 multi-cycle EX stall cycles —
`sh1add`/`sh2add`/`sh3add` replacing multiplies by constants — which moves every
subsequent branch's address and changes the spacing between a BTB update and the
next lookup that needs it. **Both candidate mechanisms are consistent with the
data and this write-up does not claim which dominates**; separating them needs an
experiment that changes layout without changing stalls, which nothing here does.

**What it means for A19's payoff figure is worth stating plainly**: the
predictor's mispredict count on this core is sensitive at the 20% level to code
that contains no branches. A predictor result quoted against one compilation is
a result about that compilation.

## What is not done here

No Fmax and no hardware numbers — A23's. The measurements above are Verilator,
at 2,000 Dhrystone runs and 6 CoreMark iterations, which is why they are quoted
as ratios and deltas rather than as scores.
