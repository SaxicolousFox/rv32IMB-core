# A29 — `Zkr`: the `seed` CSR and an entropy source

**The highest-risk item in the round and the easiest to over-claim.** The CSR is
easy. The source is not, and the honest deliverable is a working source with its
mandatory health tests and an explicit statement of what has *not* been done.

---

## The statement that has to come first

> **No SP 800-90B statistical validation campaign has been run on this entropy
> source, and it is therefore UNCERTIFIED.** What exists is a noise source of a
> standard construction and the two health tests SP 800-90B makes mandatory,
> shown by fault injection to detect a stuck and a biased source. What does not
> exist is the entropy-rate estimation, the restart tests, or the IID/non-IID
> track that certification requires — none of which is a simulation exercise and
> all of which need a large sample captured from the physical device.
> **`ES16` is returned because the CSR's interface requires a status code, and
> its specified meaning is entropy meeting SP 800-90B. This implementation does
> not establish that.** Anyone using this for real key material should treat the
> source as unvalidated.

Three further gaps, none of which the statement above covers and all of which
are real:

- **There is no cryptographic conditioning.** Sixteen raw samples are shifted
  into a register and returned. A certified design would run a vetted
  conditioning component and claim an entropy rate the estimation track had
  established.
- **H = 1 bit per sample is an assumption, not a measurement.** The health-test
  cutoffs are derived from it. Assuming H = 1 makes them *tighter* than a lower
  rate would, so the tests are conservative in the safe direction — but the
  number itself is unestablished.
- **No output of the physical ring has ever been captured.** Everything below
  is measured against a stub; §3.7 explains why, and the boundary is where the
  step's own done-when puts it.

---

## Three pieces, and only two of them can be verified here

`MODS_A2` §3.7 established the split in advance rather than discovering it:
a ring oscillator is a combinational loop, so **Verilator cannot simulate it
meaningfully and Yosys cannot represent it at all.** It is the first module in
this project outside the reach of the project's own primary methods. So it is
structured so that everything *except* the loop is verifiable.

| file | what it is | verified by |
|---|---|---|
| `rtl/core/rvntt_entropy.sv` | the noise source: 3 inverter rings of coprime odd length, XORed, sampled | synthesis only — see below |
| `rtl/core/rvntt_entropy_health.sv` | the two mandatory SP 800-90B continuous tests | `formal_entropy_health`, `entropy_health` |
| `rtl/core/rvntt_seed.sv` | the `seed` state machine, BIST → WAIT/ES16 → DEAD | `formal_seed`, `entropy_health` |
| `rvntt_csr.sv` | the read-write-only access rule | `formal_csr`, 2 mutations |

`STUB = 1` in every simulation and formal build in this project; `STUB = 0` only
in the bitstream. Under `STUB = 1` **no loop exists in the elaborated design at
all** — it is absent, not disabled.

---

## The cutoffs are derived, not quoted

Health-test cutoffs are exactly the constant this project has learned not to
write from memory, and this one has a trap in it: **the widely-cited "821" for a
1024-sample adaptive-proportion window belongs to a different assumed entropy
rate.** Using it would have made the test four sigma looser than intended while
looking authoritative.

`tb/unit/test_entropy_health.py` recomputes both from the standard's own
definitions and **fails if the RTL disagrees**:

| test | definition | value |
|---|---|---|
| repetition count (4.4.1) | `C = 1 + ceil(-log2(α) / H)` | **21** |
| adaptive proportion (4.4.2) | smallest `C` with `P[Bin(W, 2^-H) ≥ C] ≤ α` | **589** |

at `H = 1`, `α = 2^-20`, `W = 1024`. 589 is 4.8σ on `Bin(1024, ½)`, whose mean
is 512 and whose standard deviation is 16. The tail is computed with exact
`Fraction` arithmetic, not floats — 2^-20 is where double precision starts to
matter and where being off by one would move the cutoff silently.

**Fault-injected:** setting `AP_CUTOFF` to 821 is reported as a mismatch against
the derivation, with the instruction not to "fix" it by copying the RTL value
into the test.

---

## The adaptive test was strengthened, because the mandated one has a blind spot

SP 800-90B designates the **first sample** of each window as the value `A` and
counts how many of the window's samples equal it. This implementation counts
**both** values and fails if either reaches the cutoff — strictly stronger,
since whenever the mandated test fails so does this one.

**The reason is a blind spot that fault injection found, not one that was
reasoned to.** A periodic source whose period divides the window has a *fixed
phase*, so the designated first sample is the same value in every window. Feed
the mandated test seven ones and a zero, forever, with a 1024-sample window:
1024 is a multiple of 8, and if phase 0 lands on the minority value the count is
128 against a cutoff of 589 and **the test never fires. Not eventually — never.**

And that is not a contrived input. **It is the characteristic failure of the
hardware this module guards**: a ring oscillator sampled by a clock it is
asynchronous to injection-locks to that clock and emits a short periodic
sequence. The mandated test can be structurally blind to the single most likely
way this noise source dies — which is also why `rvntt_entropy` uses several
rings of coprime length rather than one.

Counting both values costs one comparator and doubles the false-positive rate
from 2^-20 to 2^-19 per window.

---

## The health tests were observed to fire

`MODS_A2` A29: *"A health test that has never been observed to fire is not a
health test."*

| source | outcome | which test |
|---|---|---|
| ideal (xorshift), 40k idle cycles | **alive** | — |
| stuck at 0 | **DEAD** within 64 samples | repetition |
| stuck at 1 | **DEAD** within 64 samples | repetition |
| biased 7:1 | **DEAD** while being consumed | either |
| biased 3:1 | **DEAD** while being consumed | adaptive (repetition is unlikely at (¾)²⁰) |
| **periodic 7-in-8, max run 7** | **DEAD** | **adaptive only** — repetition structurally cannot |

The last row is the one that matters: before it existed, **every bad source was
catchable by the repetition test alone**, and deleting the adaptive test
entirely changed no verdict. Five faults were injected into the RTL and each
was caught by the row it should be:

| fault | caught by |
|---|---|
| the repetition test never fires | stuck-at-0, stuck-at-1 |
| the adaptive test counts half what it should | **periodic 7-in-8** |
| the adaptive cutoff loosened to 821 | the derivation check |
| a consuming read does not empty the buffer | fresh-bits, and biased 3:1 |
| `health_fail` made non-sticky | `a_fail_is_sticky`, formally |

Formally, at a small window (`REP_CUTOFF=5`, `AP_WINDOW=8`): the failure is
sticky, the repetition test fires within its cutoff, the run and window counters
are bounded, and nothing fails without samples. `formal_seed` adds: **DEAD
latches**, a health failure reaches DEAD immediately, **a consuming read empties
the buffer**, entropy appears only with `ES16`, and reserved bits are zero. Both
proofs were vacuity-checked by injecting faults and observing them fail.

---

## The finding that changed the design: α = 2^-20 is *per sample*

The repetition cutoff is `C = 1 + ceil(-log2(α)/H)`, so **a correctly
functioning source trips it about once every 2^20 samples by construction.**
That is the standard's design point, not a defect.

Zkr's `DEAD`, however, **latches until reset**. Free-running at 96.246 MHz, the
two together take the entropy source permanently out of service after about
**eleven milliseconds** of ordinary operation.

**That was measured, not predicted.** The ideal-source scenario went DEAD at
42 000 samples on a run of 23 identical bits — for a fair coin, a perfectly
ordinary 6% event over that length. The first version of the test asserted
survival over 42 000 samples, which is a 4% coin flip, and it duly came up
tails.

So **the source is sampled only when it is needed**: free-running through the
start-up test, then only to refill the 16-bit buffer. That costs 16 samples per
seed read instead of one per clock, moving the expected trip from 2^20 *cycles*
to **2^20 / 16 = 65 536 reads**. The limitation does not go away — it cannot,
without choosing a different α or giving up DEAD's latching — and the number is
stated here rather than left to be discovered.

**One redundancy is recorded rather than removed.** `dead_q`'s explicit latch in
`rvntt_seed` is unobservable, because `want_sample` already stops sampling when
`dead_q` is set, so a source cannot recover even without it. Both are kept —
`dead_q` is the architectural DEAD and `health_fail`'s stickiness is the
standard's requirement — and the redundancy is written down rather than assumed
load-bearing, which is what A20 settled for guards like this.

---

## `seed` is not a normal CSR

- **Read-write-only.** `csrrs rd, seed, x0` — the ordinary way to read a CSR —
  **traps**. Reading `seed` destroys the entropy it returns, and requiring the
  write is how the architecture stops that happening by accident, for instance
  in a debugger's register dump. `rvntt_csr.sv` owns the rule because only it
  sees `wen`.
- **A successful read consumes.** Two consecutive `ES16` reads must not return
  the same bits.
- **Status in `[31:30]`**, entropy in `[15:0]`, `[29:16]` reserved and zero.
- **`DEAD` latches.**

Three properties are proved in `rvntt_csr`'s `FORMAL` block — rebuilt from the
raw inputs rather than reusing the wires they check, in the same spirit as
`rvntt_forward`'s — and two mutations were added: `seed_is_an_ordinary_readable_csr`
(the sense of the rule inverted) and `a_trapping_seed_access_still_consumes`
(the consuming read not gated on legality). Both are caught by `formal_csr`.

---

## The KAT path cannot reach the CSR, and that is checked

`MODS_A2` A29 requires a **build-time** selection rather than a runtime flag,
because the failure is silent in both directions and neither direction has a
test that catches it afterwards:

- a KAT that passes because the entropy source was bypassed proves nothing about
  the source;
- a keygen that is deterministic in the field is a catastrophic bug that **no
  known-answer test can ever detect**, because being deterministic is exactly
  what a KAT requires.

So the selection is which object file is linked. `sw/kyber/randombytes_seed.c`
reads `seed`; `make ENTROPY=1` compiles it and `#ifndef KAT_ENTROPY` removes
`kat_main.c`'s deterministic generator, so a duplicate definition is a link
error rather than one silently shadowing the other.

`tb/unit/test_kat_no_seed.py` builds **both** and disassembles them:

```
KAT build (deterministic randombytes):  0 access(es) to CSR 0x015
ENTROPY=1 build (seed CSR):             2 access(es) to CSR 0x015
```

**The second line is what stops the first from being vacuous.** A check that
only ever asserts an absence is satisfied by a build that does not exist.

The ML-KEM KATs pass unchanged.

---

## The ring oscillator fought the tools, in three separate ways

A29 predicted it — *"the first two attempts will probably be optimised away or
fail implementation; that is the expected cost of the step"*. It took three
distinct mechanisms, each failing differently:

| mechanism | what it stops | how it fails without it |
|---|---|---|
| `DONT_TOUCH` + `KEEP_HIERARCHY` | the loop being constant-folded away | the ring silently vanishes and the source is a constant |
| `set_disable_timing` on the closing arc | the loop being *timed* | implementation errors on the combinational loop |
| `ALLOW_COMBINATORIAL_LOOPS` on the ring's nets | the **bitgen DRC** refusing it | routes cleanly at WNS 0.000, then produces **no bitstream** |

The third was the surprise: the design placed and routed with timing met and
then `write_bitstream` refused it with `[DRC LUTLP-1] Combinatorial Loop Alert`.
**That is the right failure mode** — Vivado will not silently ship a
combinational loop — and the error message names its own remedy.

**The acknowledgement is set only on the ring's own nets, never design-wide.** A
blanket one would suppress the same DRC for an *accidental* loop elsewhere in
the core, which is exactly the class of bug the check exists to catch and one of
the few that simulation cannot see either — Verilator reports it as
`DIDNOTCONVERGE`, not as a wrong answer. That happened too, the first time
`rvntt_soc_top` hardcoded `ENTROPY_STUB = 0`: every SoC simulation died until
`rvntt_soc_sim_top` overrode it back to 1.

`build_soc.tcl` prints `SOC_ENTROPY_RING: <n> ring cell(s)` on every build and
says explicitly when the count is zero, because `set_disable_timing` over an
empty collection is a warning and the build then fails much later naming a cell
nobody recognises.

---

## The ISA string, and `misa.K`

`Zkr` is in all six ISA-string sources and the RISCOF YAML, moved together as
`isa_consistency` requires:
**`RV32IMZicsr_Zicond_Zba_Zbb_Zbkb_Zbs_Zkr_Zkt`**.

**`misa.K` is deliberately NOT set** — the same shape of recorded boundary as
`misa.B`. `K` is the umbrella letter for scalar cryptography and claiming it
would claim Zkn and Zks, which this core does not implement at all.

**arch-test ships no `Zkr` suite**, checked rather than assumed: the RISCOF
report before and after has a byte-identical test set. `riscv-config` accepts
the letter, so nothing broke; no coverage was gained either, and that is
recorded rather than papered over. RISCOF remains **120/120**.
