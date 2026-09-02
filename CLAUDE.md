# riscv-ntt

An RV32IM pipeline, an ML-KEM-768 NTT coprocessor, and the `Xkntt` custom ISA
extension that binds them, targeting a Digilent Arty A7-100T.

> **`M` is A14's, from `docs/RISC-V_NTT_MODS_A.txt`, and it OVERRIDES §1.5's
> `RV32I`.** Read that document's §2 before trusting the original on ISA scope.

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
