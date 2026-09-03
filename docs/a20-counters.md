# A20 — hardware performance counters (`Zihpm`)

`MODS_A2` A20. Six 64-bit counters, six event selectors, `mcountinhibit`, and
the thing that makes them worth having: **they are validated against A18's
independently-written simulation instrument, to the count, over both
benchmarks.**

Before this step every attribution number in the project was a simulation
result checked against two hardware counters — `mcycle` and `minstret`. A18's
identity

    cycles = retired + load-use stalls + multi-cycle EX stalls + 2 × redirects

closed with residual exactly zero, but three of its four terms came from a
Verilator observer rather than from the silicon. A20 makes them first-hand.

---

## What is implemented

| CSR | Address | Notes |
|---|---|---|
| `mhpmcounter3`–`mhpmcounter8` | `0xB03`–`0xB08` | 64-bit, with `…h` halves at `0xB83`–`0xB88` |
| `mhpmcounter9`–`mhpmcounter31` | `0xB09`–`0xB1F` | **decoded, read-only zero, do not trap** |
| `hpmcounter3`–`hpmcounter31` | `0xC03`–`0xC1F` | read-only user shadows, `…h` at `0xC83`–`0xC9F` |
| `mhpmevent3`–`mhpmevent8` | `0x323`–`0x328` | WARL over 0…6 |
| `mhpmevent9`–`mhpmevent31` | `0x329`–`0x33F` | read-only zero |
| `mcountinhibit` | `0x320` | CY, IR and HPM3–8; **TM reads zero** — there is no `mtime` |

**Six real counters, not 29.** The privileged spec permits any subset and
requires only that unimplemented counters read zero and do not fault.
Implementing 29 × 64 bits of counter on a design whose Fmax is the point of this
round would be self-defeating. Any of the six events can be routed to any of the
six counters — a 6:1 selection each, which is trivial and avoids the WARL stretch
of hardwiring counter *N* to event *N*.

**Unimplemented is not the same as undecoded**, and the difference is the whole
of case 3 in the directed test. Software probes for how many counters exist by
reading them; a decoder that simply omits 9…31 turns that probe into an illegal
instruction.

## The events

The numbering is this core's — the spec leaves `mhpmevent`'s encoding entirely
to the implementation. It lives in `rv32i_pkg.sv` as `HPM_EV_*` so the core, the
CSR block and the testbenches name one constant.

| # | Event | Predicate in `rvntt_core.sv` |
|---|---|---|
| 0 | none — **the reset value** | — |
| 1 | load-use interlock cycles | `id_stall && !ex_stall` |
| 2 | multi-cycle EX stall cycles | `ex_stall` |
| 3 | fetch redirects, all causes | `ex_redirect` |
| 4 | redirects caused by a misprediction | `!ex_stall && ex_mispredict` |
| 5 | control transfers that hit in the BTB | `ex_bp_upd && id_ex_q.pred_hit` |
| 6 | taken control transfers retired | `ex_bp_upd && ex_ctrl_xfer` |

**Event 0 is the reset value and counts nothing**, so a counter software never
programmed reads zero forever rather than accumulating something arbitrary.

**Events 3 and 4 differ by the trap-and-MRET term.** A19's second closure
asserted that term is zero on both benchmarks; these two counters are what lets
that be checked on the board rather than assumed.

**Events 1 and 2 cannot both fire in the same cycle, and that was checked
rather than assumed.** The load-use event is written `id_stall && !ex_stall`, to
mirror the `else if` in `tb/perf/tb_profile.cpp` that charges an overlapping
cycle to the multi-cycle unit. **The guard does nothing, and the fault injection
is what established that.** Dropping it changed no count anywhere on either
benchmark, because `id_stall` requires the instruction in EX to be a *load*
(`rvntt_hazard`'s `ex_pending_load`) and `ex_stall` requires it to be a *multiply
or divide* — one instruction cannot be both.

The original version of this section claimed the tie-break was the load-bearing
line in the step. It is not, and the correction matters more than the fact: a
guard against an impossible case is harmless, but a guard **believed** to be
load-bearing makes the next reader reason about an overlap that does not exist.
So the disjointness is now asserted in `rvntt_core.sv` as
`a_stalls_are_disjoint` and proved by every riscv-formal check at depth 14 — a
stronger statement than the comment it replaces — and the guard stays as
documentation of it. If a future multi-cycle unit ever does overlap with a load,
that assertion fires instead of the counters silently double-counting.

---

## The validation, in three paths

### 1. The CSR contract — `sw/tests/a20_hpm.S`, nine cases

Self-checking against riscv-tests' `tohost` protocol, because what it checks is
counter *values* and Spike advances `mcycle` once per instruction — a commit-log
diff cannot check any of it.

Readable and writable; 9…31 decoded and zero without trapping; `mcountinhibit`
WARL with TM reading back zero; **`mcountinhibit` actually inhibiting**; a
programmed event actually counting; event 0 counting nothing; `mhpmevent` WARL
over 0…6; the halves independent and carrying; the user shadows reading through
and rejecting writes.

**Seven faults were injected and all seven caught**, each naming the right case:

| Injected fault | Caught by |
|---|---|
| `mcountinhibit` stored but not wired to the counter | case 5 |
| counters 9…31 trap instead of reading zero | case 3 |
| `mhpmevent` accepts an out-of-range selector | case 8 |
| the event never increments the counter | case 6 |
| the counter wraps in place instead of carrying | case 9 |
| writing the low half clobbers the high half | case 9 |
| the user-mode shadow is writable | case 10 |

An eighth injection — dropping the `&& !ex_stall` guard — **escaped**, and that
is the finding above: it is a semantically equivalent rewrite, not a bug.

### 2. Hardware counters against A18's instrument — **exact**

`tb/perf/run_stall_profile.py` arms the six selectors from the testbench and
reads `mhpmcounter_q` at the same `mcycle` reads that bracket each region. Four
independent equalities, required to hold **to the count**:

| Event | vs the instrument |
|---|---|
| load-use interlock cycles | `id_stall` |
| multi-cycle EX stall cycles | `ex_stall` |
| fetch redirects | `redirect_raw` |
| taken control transfers | `br_taken + jal + jalr` |

Arming from the testbench rather than from the benchmark is deliberate: A18's
whole value is that the instrument does not perturb what it measures, and adding
CSR writes to the benchmark would mean the number being validated came from a
different program than the number validating it.

> **`redirect_raw`, and why it had to be added.** The first run of this
> comparison failed on Dhrystone by exactly 1 — hardware 2036, instrument 2037 —
> and passed on CoreMark. Neither side was wrong. The instrument **delays its
> redirect count by `redir_delay` = 3 cycles on purpose**, so that a redirect's
> two lost cycles are charged to the region that actually lost them; without
> that the cycle identity is wrong by 2 whenever a redirect fires near a region
> boundary. A counter in silicon cannot do that — it increments on the pulse. So
> the two differ, per region, by the pulses in flight across a boundary.
>
> The fix was to have the instrument keep **both** counts: the delayed one for
> the cycle identity, and an undelayed one for this comparison. Loosening the
> check to a tolerance would have hidden the class of bug it exists to find.

**`hpm_btbhit` has no counterpart and is reported rather than checked.** The
retired instruction stream contains no evidence about whether the BTB held an
entry. That gap is stated in the code, and it is why the "BTB hit counted when
the prediction was suppressed" mutation is recorded as uncatchable rather than
listed with a catcher that does not catch it.

### 3. The counters as software reads them — **bounded, not exact, and why**

The third path builds the image with `--hpm` so that `sw/bench/` arms and reads
the counters through `csrr`, the way the board will. This goes through the CSR
read port, the decoder and the pipeline — the part a testbench-only check never
touches.

**This one cannot be an exact match, and the reason is structural.** `sw/bench`
reads the counters immediately *outside* the cycle window on entry and outside
it on exit — the same convention `minstret` already uses here, which
`dhry_glue.c` states in its own comment. So the six `csrr`s, their loop, and the
`mcycle`/`minstret` reads themselves all fall inside the counted window and
outside the timed one. The counters read **high by the snapshot code's own
footprint**.

That is not an error to tune away; it is what reading a counter from software
costs, and it is exactly why A18 built a non-perturbing observer in the first
place. What is checkable is that the excess is a **constant**, not a proportion —
a footprint does not grow with the region, a miscounting predicate does. Measured
by doubling the region:

| `--dhry-runs` | load-use | EX stall | redirects | transfers |
|---|---|---|---|---|
| 200 | 5206 / 5200 | 7200 / 7200 | 2052 / 2036 | 18227 / 18204 |
| 400 | 10406 / 10400 | 14400 / 14400 | 4052 / 4036 | 36427 / 36404 |
| **excess** | **+6** | **+0** | **+16** | **+23** |

**Every event count doubled and every excess is identical to the event.** That
is the constant signature, measured rather than assumed. A *relative* limit would
have called the +6 a failure on the small region and a pass on the large one,
which is precisely backwards — so the bound is absolute.

---

## What this costs

`BENCH_HPM` is a build-time switch and defaults **off**, for the same reason
`BENCH_NTT` does: A13's, A16's and A19's numbers must stay reproducible from
their own images. Arming adds CSR writes to the startup path and six `csrr`s to
each timer hook. They land outside every timed region by construction — but
"outside the timed region" is a claim that should be checked rather than
asserted, and A23 checks it by building both ways and diffing the cycle counts.

`tb/fpga/parse_bench_uart.py` treats the twelve `*_hpm_*` lines as **optional and
all-or-nothing**: a capture carrying three of the six is a truncated capture, not
a different build, and saying so at the parser is cheaper than a `KeyError` three
functions away. Its 28-fault selftest still passes.

## What is not done here

**The identity has not yet been closed on the board.** Everything above is
simulation plus the directed test. A23 programs the part and reports the six
counters from hardware; until then the software path is validated but not
exercised on silicon.

**No Fmax measurement has been taken since the counters were added.** The event
wires are derived from `ex_mispredict` and `ex_redirect`, which `MODS_A2` §3.4
shows are on the critical path, so a cost is expected rather than assumed to be
zero. A23 measures it.
