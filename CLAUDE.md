# riscv-ntt

An RV32I pipeline, an ML-KEM-768 NTT coprocessor, and the `Xkntt` custom ISA
extension that binds them, targeting a Digilent Arty A7-100T.

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
| `docs/RISC-V_NTT.txt` | **The plan document** — the source of truth for scope, sequencing and milestones. Read §12 before claiming any milestone. |
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
illegal instruction, not "ignored". Do not relax this. Plan A3 compares the RTL
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
| M6 | riscv-formal checks pass | ✅ 43 checks at BMC depth 14 |
| M7–M16 | — | not started |

**M5 is a compliance claim, and its boundaries are recorded rather than
implied.** The RV32I `I` suite passes 38/38 and the report is committed at
`docs/riscof-report.html`. The `pmp` tests are **excluded by name**, because
plan §1.5 excludes PMP and riscof 1.25.3 ignores the `verify` clause those
tests use to deselect themselves. RISCOF itself is deprecated upstream — the
arch-test default branch has moved to ACT4, which needs Sail and a UDB config —
so `toolchain/riscv-arch-test` is pinned to the maintained `old-framework-3.x`
branch. See `rtl/core/CLAUDE.md` for the four corrections it took to get a
report that means anything.

**M6's boundaries are recorded too.** The 36 RV32I instruction models plus
`reg`, `pc_fwd`, `pc_bwd`, `causal`, `liveness` and `unique` all pass at depth
14. What is **not** proved: memory consistency (`dmem` and the `bus_*` checks
need a memory model in the wrapper, which would defeat the unconstrained
`dmem_rdata` the rest of the proof depends on), anything about CSRs (`csrw`,
`csr_ill` and `ill` have no model for Zicsr, ECALL, MRET or FENCE), and the
Xkntt encodings. It found one real bug on its first honest run — a forwarding
mux and a writeback mux disagreeing on `RES_XKNTT` — and its first *dishonest*
run reported 43/43 over a broken adder, because sby exits 0 on a failed check
by design. See `rtl/core/CLAUDE.md`.

**What is still missing after M6**: no synthesis or timing closure, no
bitstream, and no Xkntt execution — the decoder recognises the extension but no
stage runs it.

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
