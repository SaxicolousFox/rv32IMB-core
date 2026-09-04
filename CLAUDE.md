# riscv-ntt

An RV32IM pipeline, an ML-KEM-768 NTT coprocessor, and the `Xkntt` custom ISA
extension that binds them, targeting a Digilent Arty A7-100T.

> **`M` is A14's, from `docs/RISC-V_NTT_MODS_A.txt`, and it OVERRIDES §1.5's
> `RV32I`.** Read that document's §2 before trusting the original on ISA scope.
> **`docs/RISC-V_NTT_MODS_A2.txt` OVERRIDES it again** — A20–A30 add `Zihpm`,
> B (Zba+Zbb+Zbs), Zbkb, Zicond, `Zkr` and `Zkt`. Read its §2 as well; the two
> modification documents are additive and neither is ever edited by the other.

---

## Environment — read this first

```sh
source toolchain/env.sh
```

**Run this before any tool invocation.** Without it, `spike`,
`riscv-none-elf-gcc`, `yosys`, `sby` and the Python venv are all off `$PATH`.

This matters more than it looks. The regression harness reports a missing tool
as **SKIP, not FAIL**, so a session that forgets it gets a green-looking table
that tested nothing. If you see unexpected SKIPs, that is the first thing to
check — not a broken test.

Two environment constraints that will otherwise waste a cycle each:

- **`sudo` has no TTY here.** It fails both directly and via the `!` prefix.
  Batch everything you need and hand the user a single command to run in a
  separate terminal.
- **Vivado needs `dangerouslyDisableSandbox: true`.** The sandbox blocks the
  WSL↔Windows interop socket (`UtilConnectUnix:526: socket failed`). Vivado
  lives on Windows; shell out with `vivado.bat -mode batch -source <script>.tcl`
  rather than installing anything Vivado-related in WSL.

---

## Documents

| Where | What |
|---|---|
| `docs/RISC-V_NTT.txt` | **The plan document** — the source of truth for scope, sequencing and milestones. Read §12 before claiming any milestone. **Never edit it**; corrections go in the file below or in "Known errors" here. |
| `docs/RISC-V_NTT_MODS_A.txt` | **Track A modifications** — A14–A18 and milestones M7.1/M7.2, adding RV32IM and branch prediction. It **OVERRIDES §1.5's `RV32I`** and §A13's expected IPC/DMIPS bands; read its §2 before trusting the original on ISA scope. |
| `docs/RISC-V_NTT_MODS_A2.txt` | **Track A modifications, round 2** — A20–A30 and milestones M7.3/M7.4/M7.5: hardware performance counters, B + Zbkb, Zicond, a 2-cycle multiply, an Fmax push, `Zkr` and `Zkt`. **OVERRIDES §1.5 a second time.** Its Appendix A is a mechanically-generated encoding table. |
| `docs/isa-spec.md` | The frozen `Xkntt` contract: encodings, semantics, latency, exceptions. |
| `docs/spike-xkntt.md` | The Spike fork. **Track A needs this** — Spike is the cosim reference. |
| `docs/insn-bridge.md` | How to emit `Xkntt` instructions from C or a testbench. |
| `docs/kyber-backends.md` | The ML-KEM-768 build with swappable NTT backends. |
| `docs/patch-discipline.md`, `docs/fpga-bringup.md` | Toolchain forks; board bring-up. |

> The plan is committed, so every clone and worktree has it. It is the
> authority on *what to build*; where it is factually wrong about *how*, the
> corrections below win.

---

## Known errors in the plan document

These are **authoritative corrections**. An agent implementing the plan
literally will get them wrong.

**1. `kbfgs`'s subtraction order.** Plan §3 specifies
`montgomery_reduce(z * (t - b))`, i.e. `(a - b)`. The pq-crystals reference
computes `fqmul(zeta, r[j+len] - r[j])` — that is `(b - a)`, the **negation**.

Implementing the plan literally yields a working forward NTT and a *silently
broken inverse*. It was caught by building `invntt` from instruction semantics
alone and diffing against the golden model:
`invntt via kbfgs differs at coeff 2: 1369 != -1369`.

**2. `kbmul1` exists, at `funct3=5`.** The plan assigns nothing there. Without
it there is no instruction for `c1 = a0*b1 + a1*b0`, and `basemul` cannot be
performed at all. R-type, not R4 — `c1` needs no zeta, and encoding an unused
`rs3` would burn a register-file read port.

**3. Reserved encoding fields are strict.** A nonzero reserved field is an
illegal instruction, not "ignored". Do not relax this. A14 added exactly one
legal `funct7` to `OP` — `0000001`, the M extension, for all eight `funct3`
values — and every other `funct7` there is still illegal, which is what keeps
this rule intact rather than eroded. Plan A3 compares the RTL
decoder against the Python decoder over 10⁶ random words; a lax and a strict
decoder disagree on exactly those words, and the divergence would surface
during cosimulation as an unexplained mismatch.

---

## Correctness guards

**`model/` is a frozen contract.** `modarith.py`, `ntt_ref.py`, `ntt_math.py`
and `isa/xkntt.py` are validated bit-exactly against the C reference over 1261
polynomials with per-layer dumps. **If the RTL disagrees with the model, the
RTL is wrong.** Do not adjust the model to make hardware pass.

**`toolchain/kyber/` must stay pristine** so the reference's own KATs remain
valid. Instrumentation is *generated* into `model/cref/`, with the diff kept at
`patches/kyber-ref-dump-ntt.patch`. Never edit the checkout in place.

**`harness_detects_failure` is a permanent, deliberate XFAIL.** It proves the
regression can actually report failure. Do not "fix" it.

**Re-export Spike patches after any commit touching the fork:**

```sh
toolchain/patches.sh export spike
```

Otherwise `patches_in_sync` fails. `toolchain/patches.sh` also does
`apply` / `rebase` / `verify` / `status`.

### The four-way agreement

Track A's RTL decoder, `model/isa/xkntt.py`, Spike, and the LLVM `SchedModel`
must agree **exactly**, strict reserved fields included. Three of the four
exist today.

Latency is a *performance* contract, and the one part of `docs/isa-spec.md`
expected to change:

| `kmm` | `kbfct` | `kbfgs` | `kbmul0` | `kmac` | `kbmul1` |
|---|---|---|---|---|---|
| 4 | 5 | 5 | 9 | 6 | 5 |

When these change they **must change in the RTL, Spike and the LLVM
SchedMachineModel together, in one commit** — never one at a time. A stale
scheduling model produces code that stalls on real hardware, and the symptom
appears nowhere near the cause.

---

## Working norms

**Fault-inject every checking mechanism.** After building a test, deliberately
break the thing it watches and confirm it reports failure. This is standard
practice here, not something done only on request — it has caught more real
bugs than any other habit in this project, including the `kbfgs` sign error, a
UART banner repeating 15× too fast, and a spec-drift check that would otherwise
have passed vacuously.

### Which checks to run, and when (H1)

**Three tests are 84% of the regression** — `mutation_pipeline`,
`riscv_formal` and `formal_muldiv`. Every figure below is measured on this
machine (8 cores), not estimated:

| When | What | Cost | Drops |
|---|---|---|---|
| Tight edit loop | `RVNTT_FAST=1 python3 tb/run_regress.py` | **249 s** | the three heavy tests |
| After an RTL change | `RVNTT_NO_MUTATE=1 …` | **793 s** | the mutation set only |
| Step boundary | full run | **1230–1470 s** | nothing |
| Milestone | full, plus `RVNTT_HW=1 … -k fpga` | +~250 s | nothing |

**The full-run figure is a range on purpose.** Two clean runs of the same tree
measured 1226 s and 1468 s, with `mutation_pipeline` at 563 s and 678 s — a 20%
spread that did not exist when it was serial, because a parallel run's wall
clock now depends on what else the machine is doing. Quote the range, and do
not read a slower run as a regression in the harness.

`RVNTT_FAST=1` **announces itself in the summary** — "this was the EDIT-LOOP
TIER … it is not a regression result" — because the hazard of a tier is
somebody quoting its green line as a regression. Both 20-millisecond
pre-flights run in every tier.

**The two pre-flights are the model to copy.** `mutation_anchors` (0.03 s) and
`isa_consistency` (0.02 s) each catch a class of failure that the expensive run
would otherwise reveal much later or not at all — a stale anchor becomes a
`NO-OP` after fourteen minutes, and a narrowed ISA string does not fail, it
**hangs**. Both classes have bitten this project repeatedly (anchors five times,
ISA strings twice). **A cheap check that fails fast reduces total time; it does
not add to it.** When you add a checker, ask what its cheapest possible
pre-flight is.

**H1 made the mutation set 2.1× faster rather than smaller**: 1151 s → 547 s,
by running mutations, and the baseline's 44 independent checks, in parallel
(`-j`, default half the cores). The serial and parallel reports are
**byte-identical** — verified — because results are printed in manifest order,
never completion order.

**Do not tier the mutation set by `--step` as a default.** `--step` is right for
an edit loop and wrong as a policy: an escape in an untouched step would hide
indefinitely, and a partial default is only honest with a scheduled full run,
which this project does not have. H1 made the whole set cheap instead.

**For Fmax, probe before you search.** `fmax_search.py --probe MHZ` is one
implementation run against a known number and answers "did this lever help?".
A full search is 6–8 runs and answers "what is Fmax?" — A23's took over three
hours. Ask the first question first; a probe is a bound, not a measurement, and
says so in its own output.

**Background anything over ~5 minutes** and poll rather than blocking. A full
Spike rebuild is ~15 minutes; touching `riscv/xkntt.h`, `riscv/xkntt_encoding.h`
or `riscv/insn_template.h` triggers one, while a single `riscv/insns/*.h` or
`riscv/xkntt.cc` rebuilds in seconds.

**Give a short status report after each step** — what passed, what deviated,
and why.

**Make reasonable judgment calls and keep moving** rather than stopping to ask.
But *state the call in the status report* so it can be reviewed and overridden
after the fact.

**Commit trailers.** End every commit message with:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

Commit bodies here are prose explaining *why*, not a list of changed files —
including what was fault-injected and what caught it.

---

## Worktree collisions

These are shared across both track worktrees. **Coordinate before editing; do
not treat them as independently owned.**

| File / directory | Why it collides |
|---|---|
| `tb/run_regress.py` | Both tracks append to the same test registry |
| `rtl/common/` | Shared modules (e.g. `rvntt_sync_reset.sv`) |
| `sw/include/xkntt.h` | Consumed by Track A testbenches *and* Track B software |
| `docs/` | Both tracks add documents here |

---

## Milestones

**Before marking anything complete, re-read §12 of `docs/RISC-V_NTT.txt`
directly.**
Do not rely on remembered M-numbering — check the actual "Done when" text and
confirm it is satisfied.

| | Milestone | Status |
|---|---|---|
| M0 | `make regress` runs; blinky+UART bitstream on the board | ✅ hardware-confirmed |
| M1 | `docs/isa-spec.md` frozen; every instruction hand-encoded | ✅ |
| M2 | Python and C golden models agree bit-exactly, per-layer | ✅ 1261 polynomials |
| M3 | Spike executes the extension; ML-KEM keygen passes on Spike | ✅ full 10000-vector KAT |
| M4 | Pipeline passes 1000 random programs in lockstep cosim vs. Spike | ✅ at max hazard density |
| M5 | RISCOF RV32I compliance suite passes | ✅ 38/38 `I`, plus hints and privilege |
| M6 | riscv-formal checks pass | ✅ 43 checks at BMC depth 14 (`liveness` 47 since A15) |
| M7 | Core-only bitstream: Fmax + Dhrystone + CoreMark on hardware | ✅ hardware-confirmed |
| **M7.1** | **RV32IM core: `M` verified and the benchmark re-measured** (`MODS_A`) | ✅ **hardware-confirmed** |
| **M7.2** | **Core performance: Fmax recovered and branch prediction measured** (`MODS_A`) | ✅ **hardware-confirmed** |
| **M7.3** | **The ISA round: B, Zicond and hardware counters** (`MODS_A2`) | ✅ **hardware-confirmed** |
| **M7.4** | **A faster multiply and the Fmax the coprocessor can inherit** (`MODS_A2`) | **A24 ✅, A25 ✅; A26–A28 not started** |
| **M7.5** | **The cryptographic guarantees: `Zkr` and `Zkt`** (`MODS_A2`) | not started |
| M8–M16 | — | not started |

**M5 is a compliance claim, and its boundaries are recorded rather than
implied.** The RV32I `I` suite passes 38/38 and the report is committed at
`docs/riscof-report.html`. **A15 added the `M` suite — 8/8, so the report is now
84/84** — and that is recorded under M7.1 rather than by re-opening M5. The `pmp` tests are **excluded by name**, because
plan §1.5 excludes PMP and riscof 1.25.3 ignores the `verify` clause those
tests use to deselect themselves. RISCOF itself is deprecated upstream — the
arch-test default branch has moved to ACT4, which needs Sail and a UDB config —
so `toolchain/riscv-arch-test` is pinned to the maintained `old-framework-3.x`
branch. See `rtl/core/CLAUDE.md` for the four corrections it took to get a
report that means anything.

**M6's boundaries are recorded too, and A15 added one more to the list.**
`rvntt_muldiv`'s *arithmetic* is abstracted to a free value in the riscv-formal
run: a combinational 33×33 multiplier unrolled fourteen times is the canonical
hard SAT instance, and it sits in the cone of the RVFI outputs whether any check
reads it or not — it took the 43 checks from 40 s for the whole set to several
hundred seconds each. Its **sequencer is not abstracted**, so every stall,
bubble and retirement time those checks depend on is the real design's. The
arithmetic is proved separately by `formal_muldiv` at depth 37, by the eight
`rv32um` tests and by cosimulation. riscv-formal's own `insn_mul*`/`insn_div*`
models are deliberately **not** enabled — against the abstraction they would be
vacuous and against the concrete multiplier they do not converge — and that is
`MODS_A` §3.2's third route taken knowingly, not a check quietly skipped.

 The 36 RV32I instruction models plus
`reg`, `pc_fwd`, `pc_bwd`, `causal`, `liveness` and `unique` all pass at depth
14. What is **not** proved: memory consistency (`dmem` and the `bus_*` checks
need a memory model in the wrapper, which would defeat the unconstrained
`dmem_rdata` the rest of the proof depends on), anything about CSRs (`csrw`,
`csr_ill` and `ill` have no model for Zicsr, ECALL, MRET or FENCE), and the
Xkntt encodings. It found one real bug on its first honest run — a forwarding
mux and a writeback mux disagreeing on `RES_XKNTT` — and its first *dishonest*
run reported 43/43 over a broken adder, because sby exits 0 on a failed check
by design. See `rtl/core/CLAUDE.md`.

**A12 is done and hardware-confirmed.** The SoC —
`rvntt_core` + a 128 KB dual-port BRAM + a memory-mapped UART and GPIO — loads,
runs a program out of BRAM, and prints over the USB-UART from the board.
**Fmax ≈ 70 MHz** — 70.131 MHz measured (Vivado 2025.2, `xc7a100tcsg324-1`,
**-1** speed grade, default strategy) by the plan's binary search over six full
implementation runs; 70.641 MHz is the fastest constraint that fails. 2126 LUTs,
913 FFs, 32 BRAM tiles.

**Do not carry that number across a design change.** It was measured twice: the
first search said 73.121 MHz, on a build whose RGB LED red and blue pins were
transposed. Correcting two output pins — no logical content at all — cost 3 MHz
through placement alone. The design-to-design spread is about ±0.4 ns. The critical path is EX/MEM `rd_addr` → forwarding mux →
ALU → store byte-enables → BRAM `WEA`, and it is **78% route delay at 3.3%
utilisation**. An earlier reading of that blamed the 32-BRAM spread; the hop-by-hop
post-route path says the core-to-BRAM crossing is **under a fifth** of it and that
about half is a logical dependency chain — forwarding mux, then the full ALU
result mux, then the misaligned-address check, then the trap gating the byte
enables. The correction and the lever it points at are in `rtl/soc/CLAUDE.md`;
see also `docs/fpga-bringup.md`.

**M7 is met by A12 and A13 together** — §12's text is "Core-only bitstream: Fmax
measured, Dhrystone + CoreMark on hardware", and neither step alone does it.

**A13's numbers, measured on the board: DMIPS/MHz 0.7306, CoreMark/MHz 0.9607,
IPC 0.7227 (Dhrystone) and 0.6930 (CoreMark)**, at 70.129 870 MHz. Three separate
JTAG programming passes, three report blocks each; all nine identical to the
cycle. Both headline ratios are exact integer ratios of `mcycle` counts and do
**not** depend on the clock, so neither inherits the Fmax uncertainty above;
Dhrystones/sec (90 025.5) and the raw CoreMark score (67.37) do, and are quoted
at that frequency. See `docs/a13-benchmarks.md` and `docs/a13-benchmarks.json`.

Both are below the plan's expectation (0.8–1.2 DMIPS/MHz, IPC 0.75–0.95) for one
reason stated up front: **there is no M extension**, so every multiply, divide
and modulo in either benchmark is a branch-heavy call into libgcc, and branches
are statically not-taken with no BTB. Dhrystone spends 0.384 cycles per
instruction on stalls and flushes; **nothing attributes that split yet**, and
doing so is the first step of the plan's optional predictor loop, not of A13.

Dhrystone and CoreMark are compiled **in place** from `toolchain/riscv-tests/`
and `toolchain/coremark/`, which stay pristine — the port is `sw/bench/`. Two
methodology choices are load-bearing and easy to get wrong later: every rate is
computed in Python because Dhrystone's own `Microseconds` and
`Dhrystones_Per_Second` overflow 32-bit `long` at these run counts, and `-fwrapv`
is deliberately **not** used to define that overflow away because it was measured
to cost 1.4% inside the timed loop.

The bring-up loop needs no human: `fpga/scripts/hw_bringup.py` programs the board
over JTAG in batch, captures the UART through a PowerShell helper whose output
WSL reads back from `/mnt/c`, and parses it. **Only the LEDs still need eyes, and
that is not a formality** — the pin transposition above passed lint, elaboration,
synthesis, timing, programming and a byte-perfect UART capture, because a swapped
*output* pin is invisible to everything upstream of the pad. A person looking at
the board was the only thing that found it. `tb/fpga/check_xdc_pins.py` now
compares every constraint against a pinout extracted mechanically from the vendor
file, so that specific class cannot recur; which signal drives which port still
cannot be mechanised.

**What is still missing after A12 and A13**: any Xkntt execution — the decoder
recognises the extension and LD0 red lights if one ever retires, but no stage
runs it. Also no flash image (configuration is volatile). *(The third item here
was the unattributed 0.384 stall cycles per instruction; **A18 attributed it**,
with a Verilator observer rather than the core counters this paragraph assumed
were needed — see M7.2 below. The core still has no hardware performance
counters, and `Zihpm` remains unimplemented.)*

**M7.1 is met by A14, A15 and A16 together, and is hardware-confirmed.**
**Fmax 73.752 MHz** (up from A12's 70.131 — see below), **DMIPS/MHz 0.7325,
CoreMark/MHz 2.4309, IPC 0.6860 (Dhrystone) and 0.7006 (CoreMark)** at
73.750 000 MHz, over three JTAG programming passes with all twelve report blocks
identical to the cycle. 2613 LUTs, 1148 FFs, 32 BRAM tiles, **4 DSP48E1**. See
`docs/a16-benchmarks.md` and `docs/a16-benchmarks.json`.

**M7.2 is met by A17, A18 and A19 together, and is hardware-confirmed.**
**Fmax 77.501 MHz**, **DMIPS/MHz 0.9346, CoreMark/MHz 2.8933, IPC 0.8752
(Dhrystone) and 0.8338 (CoreMark)** at 77.500 000 MHz, over three JTAG passes
with all twelve report blocks identical to the cycle. 3471 LUTs, 1530 FFs, 32
BRAM tiles, 4 DSP48E1. See `docs/a19-benchmarks.md` and `docs/a19-benchmarks.json`.

**A17 raised Fmax 17.3% without changing one cycle count, and A19 gave 10.4% of
it back on purpose.** A17 reached 86.490 MHz with a dedicated load/store address
adder (−1.589 ns for 26 LUTs); A19's predictor costs clock and returns more than
it costs in IPC, so **absolute Dhrystone still rises 63.35 → 72.43 DMIPS on a
slower part**. Both IPC figures and DMIPS/MHz land inside §A13's bands for the
first time. **§9's core-only Fmax baseline is now 77.501 MHz**, not 86.490.

**A19's Fmax carries a wider band than any previous one, and the reason is
recorded rather than averaged away.** The binary search is **not monotonic** —
79.246 MHz failed by −1.186 ns while the tighter 80.998 MHz failed by only
−0.676 — and one netlist's implied path delay spans 12.90–13.81 ns, **a 0.9 ns
spread against A12's ±0.4 ns**. 77.501 MHz is the highest constraint *observed to
pass*, not a boundary. The A17-to-A19 gap survives that; a ±1 MHz claim would not.

**A18's identity closes at residual exactly zero** in all four regions —
`cycles = retired + load-use stalls + multi-cycle EX stalls + 2 × redirects` —
and after A19 a second closure holds too: `redirects (RTL) == mispredicts
(model/bpred.py)`, to the unit, on both benchmarks. The gain and the not-taken
regression decompose **exactly**: Dhrystone saves 335,934 cycles and loses
12,000, net 323,934, which is the measured reduction to the cycle.

**Two things about A19 are open or constrained, and neither is buried.**
CoreMark now runs **2500** iterations, not A16/A17's 2200, because A19 made the
2200-iteration run finish in 9.81 s — under CoreMark's own 10 s reporting
minimum — so its **raw cycle counts are not comparable across those steps** while
CoreMark/MHz and IPC are. And Dhrystone in *simulation* at 2,000 runs costs
615.048 cycles/run where 4,000, 8,000 and this board all agree on ~609.0; the
2,000-run point carries exactly 3 extra mispredicts per run, it is **not** the
image configuration and **not** a fixed warm-up cost, A18 shows no such effect,
and it is **unexplained**. Every headline figure above is from the board.


**The dual baseline, which is the number §10 M2 and M3 actually depend on**: the
same reference ML-KEM NTT compiled `-march=rv32i` and `-march=rv32im`, linked
into one image, measured on one core at one clock — **205 884 vs 39 058 cycles
and 148 655 vs 23 805 instructions, a 5.271× cycle and 6.245× instruction
ratio**, with all 256 output coefficients identical between the two builds. The
instruction ratio matches the 6.247× A13 measured on Spike. Plan §B6's
"15 000–30 000 cycles" baseline estimate lands inside the RV32IM band and 5–10×
outside the RV32I one, which is the contradiction `MODS_A` §1 exists to resolve.

**A13's RV32I figures are preserved, and A16 proved it rather than asserting
it**: A13's exact image was rebuilt and run on the RV32IM core, and every
counter came back identical — 155 800 003 cycles and 112 600 032 instructions
for Dhrystone, 832 746 233 and 577 088 625 for CoreMark. **Adding M cost RV32I
code exactly zero cycles.** `docs/a13-benchmarks.md` still stands as the RV32I
record.

**`MODS_A` A14 predicted Dhrystone and CoreMark would both improve. Only one
does.** CoreMark/MHz went ×2.53; Dhrystone moved +0.3%, because it retires 5.3%
fewer instructions and pays almost all of it back in four-cycle `MUL` stalls —
its IPC *falls*, 0.7227 to 0.6860. M's benefit is concentrated in multiply-bound
code, which is ML-KEM's polynomial arithmetic and not Keccak.

**Fmax went UP by 5.2% after adding a multiplier and a divider**, and that is
the ±0.4 ns placement spread A12 recorded arriving in the direction nobody
double-checks. The critical path is the same one A12 had — forwarding mux, ALU,
trap, BRAM — and neither new unit is on it. **A favourable Fmax movement across
a design change is exactly as much a measurement of a different design as an
unfavourable one.** `rtl/soc/CLAUDE.md` has the table and the hop-by-hop path.

**A14 and A15 are done: the core is RV32IM.** A generic multi-cycle EX handshake
— built for plan §8 I1's Xkntt Tier-1 unit and exercised first by a standard
extension that comes with external references — plus `rvntt_muldiv.sv`: one
33×33 multiplier on **4 DSP48E1** at 4 cycles of EX occupancy, and a radix-2
restoring divider at 34, data-independent by construction because
`tb/cosim/cycle_model.py` is unbuildable otherwise. `misa` now reads
`0x40001100`. All 8 `rv32um` tests pass, RISCOF selects and passes the `M`
suite, riscv-formal still proves 43/43, and the divider has a standalone proof
of its own at depth 37. Fifteen mutations, all caught as declared.

Two findings from it are worth carrying beyond Track A. **A checking script that
guesses is worse than none**: the first version of `fpga/scripts/synth_ooc.sh`
reported `DSP=0` over a netlist containing four DSP48E1s, because it filtered on
`PRIMITIVE_TYPE` group names that were guessed rather than looked up — the third
time this shape has appeared here after A10's RISCOF exit code and A11's sby exit
code. And **when a property asks a solver to relate two circuits that compute the
same arithmetic, state the invariant they both maintain rather than the
conclusion they both reach**: the divider's obvious correctness statement is
multiplier equivalence and did not return in fifteen minutes; the same statement
as a per-iteration invariant proves in four.

**C6 is only partially done**, which gates more than it appears to: there is no
TIER2 backend, no RTL verification, and no `make KAT` target. Any milestone
whose criteria depend on the full three-backend suite — M14 in particular —
must not be marked complete.

### When a milestone is met — the full ritual

This is your own workflow, not something to be asked for each time. On
confirming a milestone's actual "Done when" text is satisfied:

1. **Update the milestone table above** and any affected `CLAUDE.md`, in the
   same commit as the work.
2. **Commit**, with the trailers above.
3. **Tag**, annotated, named for the milestone and what is distinctive about
   it — e.g. `m3-tier1-kat`. Write the annotation so it stands alone: what is
   verified, what is explicitly *not* settled, and where to go next. Someone
   will land on it cold.
4. **Push the branch and the tags.**

```sh
git push origin main
git push origin --tags
```

Push at milestone boundaries and at the end of a track or phase — not after
every commit, and never leave a completed milestone unpushed. `origin` is
`github.com/SaxicolousFox/riscv-ntt`, authenticated, and `main` tracks
`origin/main`.

> **`main` is this repo; `master` is upstream Spike.** The default branch here
> was renamed to `main`. The `origin/master` in `toolchain/patches.sh` and
> `toolchain/upstream-pins.txt` refers to **riscv-isa-sim's** default branch,
> which really is `master`. Do not "fix" those — it would break
> `patches.sh rebase`.

**A20 and A21 are done (`MODS_A2`), and M7.3 needs A22 and A23 to close.**

**A20 — six `Zihpm` counters**, validated against A18's simulation instrument
**to the count** on both benchmarks. Before it, three of the four terms in A18's
identity came from a Verilator observer. `mhpmcounter3–8` plus selectors,
`mcountinhibit` and the user shadows; 9–31 decoded, read-zero, non-trapping.
Software arming is behind `BENCH_HPM`, **off by default**, so A13/A16/A19 images
stay reproducible. See `docs/a20-counters.md`.

**A21 — B (Zba+Zbb+Zbs) and Zbkb, 34 instructions in `rvntt_bitmanip.sv`.**
**RISCOF 118/118** (29/29 ratified-B with 3 `Zbc` tests excluded by name, 5/5
Zbkb, 38 `I`, 8 `M`, 22 hints, 16 privilege); **riscv-formal 77/77 at depth 14**,
up from 43 and with the depth unchanged; cosim 30/30 byte-identical against
Spike at 25% B density. See `docs/a21-bitmanip.md`.

**The unit is deliberately NOT in the ALU**, and that is the step's central
decision: `MODS_A2` §3.4 measures the ALU result mux at a third of the critical
path with `ex_alu_y` feeding `ex_jump_target`, so B joins at `ex_result` instead.
`rvntt_alu.sv` is untouched.

**`misa.B` is deliberately NOT set, and it is a recorded boundary.**
riscv-config 3.18.3 cannot express the `B` letter in an ISA string at all and
derives `misa` from single-letter extensions only, so asserting bit 1 makes the
RISCOF config invalid and the whole compliance run vanishes. arch-test selects
suites by **regex on the ISA string**, not from `misa`, so nothing is lost. The
canonical string is `RV32IMZicsr_Zba_Zbb_Zbkb_Zbs` — `Zbs` must come **after**
`Zbkb`, which riscv-config enforces and which is not alphabetical.

**Two checkers were found reporting green over nothing, and both are fixed.**
`tb/cocotb/run_cocotb.py` ended in a bare `return 0` since A1, so **every cocotb
test reported PASS unconditionally** — which had been hiding two real failures
since A14. And `riscof_spike_ref.py` dropped every `Z` extension from the
reference's ISA string for the **second time**, making Spike trap on the first
`clz` and spin to a 600 s timeout while reporting nothing. Counting A10, A11,
A14, A19 and A20, that is **six instances of the same shape**: a report whose
green was not about the thing it named. Both fixes put the check *in the runner*
and were fault-injected.

`tb/mutate/run_mutation.py` now has a **`--check-anchors` pre-flight** — a string
search, 0.03 s against the full run's fourteen minutes — because mutation anchors
have gone stale five times, always because a later step edited the line they
point at.

**M7.3 is met by A20, A21, A22 and A23 together, and is hardware-confirmed.**
**Fmax 74.577 MHz**, **DMIPS/MHz 0.9361, CoreMark/MHz 3.1866, IPC 0.8734
(Dhrystone) and 0.8168 (CoreMark)** at 74.576 271 MHz, over three JTAG passes
with **all 44 integer counters identical across all three**. 5770 LUTs, 2027 FFs,
32 BRAM tiles, 4 DSP48E1. See `docs/a23-benchmarks.md` and `.json`.

**The number this round exists for: B makes Keccak 1.1953× faster.** SHAKE128,
the same `fips202.c` compiled twice into one image, both builds with `M`, the
only variable being B and Zbkb — 373,407 → 312,402 cycles, digests identical.
That is plan §10 M3's *other* term, the half a hardware NTT never touches, and
it is why B was added. **The compiler's use of B was verified rather than
assumed**: the `rv32imzb` copy of `KeccakF1600_StatePermute` holds 100 B
instructions and the `rv32im` copy holds 0, established by following the call
graph after an address-range attribution got it backwards.

**CoreMark/MHz rose 10.1% while its IPC FELL 2.0%**, which is `MODS_A2` §3.6's
pre-committed P4 arriving exactly as written — B replaces sequences with single
instructions, so retired count falls faster than cycles. Absolute CoreMark is up
6.0% **on a slower part**. Dhrystone barely moves; its profile is string and
branch work B has little to offer.

**Fmax fell 3.8% from M7.2's 77.501 MHz, and the first search blamed the wrong
thing.** 72 MHz failed by −0.943 ns and A21's stop rule points at the new
bit-manipulation unit — but the post-route destination was
`mhpmcounter_q[2][25]/CE`. **The cost was A20's counters**, whose 6:1 event mux
sat after `ex_redirect`. A registered one-hot mask got 70.998 MHz; registering
the event bus as well got **74.577** and moved the endpoint back onto the core's
own redirect path. Both fixes are **provably cycle-neutral** — 32 and 36 counters
compared, 0 differing. The residual 2.92 MHz is B's placement cost, and §3.4's
A26 levers target that exact path; pulling them forward was deliberately
declined, because A26's discipline is that cycle-neutral Fmax work gets its own
before/after.

**A24 answered the round's gating question, and the answer is that nothing is
gated.** A Tier-1 butterfly satisfying the frozen contract — `kmm` at 4 cycles of
EX occupancy, `kbfct`/`kbfgs` at 5 — closes at **128.125 MHz** out of context on
this part and speed grade, with 2 DSP48E1, 326 LUTs and 150 FFs. §3.3 established
that Tier 1 **cannot** be in its own clock domain, so that number is the ceiling
the core's clock inherits; A24's pre-stated 10% margin puts the core's limit at
**116.5 MHz**, so **A26 and A27 may target 110 MHz and are not bounded by Track
B**. See `docs/a24-tier1-probe.md` and `.json`, and `rtl/probe/CLAUDE.md`.

**The frozen latency table is not a compromise — it is measured to be right.**
Reading "`kmm` 4" as a constraint on the whole pipeline, so that every operation
finishes in four, costs **24%** (97.5 MHz); three costs **45%** (70.0 MHz). One
four-stage pipeline serves both numbers because `kmm` does not use the butterfly
segment and taps out of the Montgomery one an edge early. **The limiting path is
the Montgomery reduction in every configuration** — not the multiply, which is a
DSP nowhere near the critical path.

**The probe lives in `rtl/probe/`, never `rtl/ntt/`, and ships nothing.**
`rtl/ntt/` is Track B's; a probe there would be mistaken for a starting point.
**What carries across is the frequency, not the RTL.** It still gets a
correctness test against the frozen model (`tier1_probe`, 0.2 s), because a
datapath that computes the wrong answer is very likely a *smaller* one — which
reports a frequency the real unit cannot reach, i.e. exactly the failure the step
exists to prevent.

**A seventh "green over nothing", and it was mine.** `cmd.exe` treats `=` as an
argument separator, so `-tclargs … STAGES=2` reached the Tcl script as two
arguments, `synth_design` got no generic, and three "configurations" were all the
default with byte-identical WNS at every point. The tell was a critical-path
endpoint naming a generate block the shallower configurations do not contain.
**Generics now travel as `NAME:VALUE`**, `synth_ooc.tcl` hard-fails if trailing
arguments parse to none, and `probe_tier1.py` refuses to report two
configurations whose netlists are identical — a different pipeline depth cannot
have the same flop count.

**Vivado ignored `use_dsp = "no"` in both documented forms**, on a net
declaration and on an `always_comb` variable; the cell histogram said `DSP48E1=4`
both times and the resulting 4.023 ns DSP hop was the whole critical path.
**Structure controls what attributes did not** — write the constant multiplies as
shift-adds derived from the constant itself. Whether a constant multiply wants a
DSP or fabric is decided by its **population count**: `q = 3329` (four set bits)
belongs in fabric, `BARR_V = 20159` (eleven) does not.

**A25 cut the multiply from 4 cycles of EX occupancy to 3, and it is
hardware-confirmed.** The product pipeline's depth is now **derived** from the
latency (`MUL_PIPE = MUL_CYCLES - 2`) rather than written twice, and `MUL_CYCLES`
is a module parameter — defaulted from `rv32i_pkg` — only so that
`fpga/scripts/synth_ooc.sh` can sweep it. **Nothing instantiates it with a
different value and `isa_consistency` checks that.** Out of context the unit
clears 160 MHz at 4, at 3 *and* at 2, so A14's third register stage was buying
nothing this core can use.

**Every board delta closes exactly, and the interesting one is a zero.**
CoreMark **−23 490 000 cycles (−2.99%)**, and its multi-cycle EX stall counter
fell by the *same* number — A23's `exstall`/3 is 23 490 000 multiplies and A25
removes one cycle from each. CoreMark/MHz 3.1866 → **3.2850**, IPC 0.8168 →
**0.8420**. The ML-KEM NTT's `rv32im` build saves 2 688 cycles (2 688
multiplies); its `rv32i` build and SHAKE128 are unchanged because neither
contains a `MUL`. Three JTAG passes, **42 integer counters identical**.
See `docs/a25-multiply.md` and `docs/a25-benchmarks.json`.

**Dhrystone gains exactly zero, and B is why — measured, not guessed.** Its
`exstall` is 66 000 000 over 2 000 000 runs, exactly 33 per run: one divide and
**no multiplies at all**, because at `-march=rv32imb` the compiler
strength-reduces Dhrystone's multiply into Zba shift-adds. **A21 had already
removed the thing A25 makes cheaper.** The same measurement at `-march=rv32im`
shows Dhrystone saving a cycle per run, so **the value of a shorter multiply
depends on which other extensions are enabled** — measure it on the image that
ships.

**§3.6 P1 arrived as pre-committed**: the software NTT baseline got *faster*
(33 946 → 31 258 cycles), so §10 M2's reported coprocessor speedup gets
**smaller**, and the RV32I:RV32IM cycle ratio rises 4.8828 → 5.3027. That is the
correct direction, and `docs/a25-benchmarks.json` is now the denominator, not
A16's.

**A25 is NOT Fmax-neutral, and the multiplier is not why.** Rebuilt at A23's
exact clock with a byte-identical memory image and `explore_postroute`, the A25
netlist **misses 74.577 MHz by 0.423 ns** and produces no bitstream; the board
numbers above are taken at **70.000 MHz**. The failing path is
`mem_wb rd_addr → … → pc_q` — the core's own writeback-to-redirect path, the
same family A12, A17, A19 and A23 each found in turn — and `rvntt_muldiv`
appears nowhere on it. **The comparison is one-variable** (`build_soc.sh` does
not stage `rtl/probe/`, and the last commit before A25 to touch a staged source
is A23's), so the move is attributable to removing 64 flops perturbing
placement — which is the ±0.4 ns band A12 recorded, arriving unfavourably this
time. **One build cannot separate "the change cost this" from "a different
design placed differently"; A28 measures Fmax properly and A26's levers target
this exact path.**

**M7.3's done-when was revised once, and the revision is recorded in
`MODS_A2` §6.** "The identity closes with residual exactly zero on hardware" is
structurally unachievable: A18's instrument samples every counter at one instant
and software cannot, because each counter is read by its own instruction and the
windows nest. Measured residual is **−40 on Dhrystone — exactly the snapshot
code's independently-measured footprint, 6 + 0 + 2×17** — and −48 on CoreMark.
CoreMark's was −168 until its glue was made to nest the reads in the same order
Dhrystone's does; **that difference between two regions was the finding.**
Residual exactly zero remains the standard for the simulation instrument, where
it still holds.

**`redirects − mispredicts = 1` on both benchmarks, measured rather than
assumed** — A19's second closure said the trap-and-MRET term was negligible, and
the board now says it is the single `ECALL` that ends the program.
