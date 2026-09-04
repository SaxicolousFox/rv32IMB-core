# A24 — the Tier-1 timing probe

**What Fmax is Track B allowed to inherit?** `MODS_A2` §3.3 settled that Tier-1
`Xkntt` cannot live in its own clock domain — `docs/isa-spec.md` freezes `kmm`
at 4 cycles of EX occupancy, and a clock-domain crossing costs two to three
synchroniser flops in *each* direction before the unit does any arithmetic. So
Tier 1 shares the core's clock, and **the core's Fmax is a hard floor the Tier-1
butterfly must meet**. Committing the core to a number the butterfly cannot
reach is plan §9's failure mode fired at M15, two tracks away from its cause.

This step takes the evidence before the commitment. It is the cheapest step in
the round and the one with the largest consequence, which is why `MODS_A2` puts
it first in the phase.

---

## The answer

**Fmax = 128.125 MHz** for the arrangement the frozen table actually asks for —
`kmm` at 4 cycles of EX occupancy, `kbfct` and `kbfgs` at 5 — on
`xc7a100tcsg324-1`, **−1** speed grade, out of context. **2 DSP48E1, 326 LUTs,
150 FFs.** The fastest constraint that failed is 132.500 MHz, by −0.019 ns.

| pipeline | `kmm` / `kbfct`, `kbfgs` | Fmax | fastest fail | LUT | FF | DSP | limited by |
|---|---|---:|---:|---:|---:|---:|---|
| **`STAGES=4`** | **4 / 5** — the frozen table | **128.125 MHz** | 132.500 | 326 | 150 | 2 | `s3_m` — the Montgomery reduction |
| `STAGES=3` | 3 / 4 | 97.500 MHz | 101.875 | 324 | 135 | 2 | `s4_y` |
| `STAGES=2` | 2 / 3 | 70.000 MHz | 72.000 | 317 | 68 | 2 | `s4_y` |

**The DSP count is confirmed against the cell histogram, not asserted.**
`synth_ooc.tcl` counts every primitive by `REF_NAME` with nothing filtered and
no group name written down anywhere, because A14's first version of that script
reported `DSP=0` over a netlist containing four DSP48E1s. All three
configurations report `DSP48E1=2`, and the histogram lines are in
`fpga/build/ooc_rvntt_tier1_probe_STAGES*.log`.

### What it means for the core's target

`MODS_A2` A24's stop rule, stated in advance of the measurement: *do not adopt a
core Fmax that this probe says the Tier-1 butterfly cannot meet with at least
10% margin.*

```
128.125 / 1.10  =  116.5 MHz
```

**So the round's 110 MHz goal is not bounded by Track B.** A26 and A27 may
target 110 MHz; the butterfly clears it with 16% margin rather than the required
10%. A24's own expectation was "comfortably above 110 MHz, and treat any result
below 120 MHz as a finding that changes A26's and A27's target" — 128.125 is
above 120, so **no target changes**.

**The margin is thinner than 16% looks, and the reason is on the record rather
than in a footnote.** An earlier variant of this same datapath — identical
arithmetic, but without `kmm`'s early tap, so it gave `kmm` 5 cycles and did not
satisfy the contract — measured **116.875 MHz**. That is **0.75 ns** slower with
no logical content changed on the critical path, which is the placement spread
A19 recorded at 0.9 ns arriving again. At 128 MHz, 0.75 ns is worth 12 MHz.
**110 MHz should be treated as the ceiling A26/A27 aim at, not as a waypoint on
the way somewhere higher.**

### The frozen table is exactly right, and that is a measurement

`STAGES=3` is the same datapath with segments fused so that *every* operation
finishes in four cycles — the arrangement you get by reading "`kmm` 4" as a
constraint on the whole pipeline. It costs **24%**. `STAGES=2` costs **45%**.
The contract's 4-and-5 split, served by one four-stage pipeline with an early
tap, is not a compromise between them; it is strictly better than either.

**The limiting path is the Montgomery reduction**, `s2_prod → s3_m`, in every
configuration deep enough for it to stand alone. Not the multiply — the DSP is
not on the critical path at all — and not the butterfly. That is what A24
predicted would matter, and it is the one prediction in the step that held.


---

## What was built, and what it is not

`rtl/probe/rvntt_tier1_probe.sv` — a *representative* Tier-1 butterfly. It is
**not** §B1's or §B2's design and must never be instantiated by anything that
ships. It lives in `rtl/probe/` rather than `rtl/ntt/` deliberately: `rtl/ntt/`
is Track B's, and a probe sitting in it would eventually be mistaken for a
starting point. **What carries across to Track B is the frequency, not the
RTL.**

It implements the frozen contract's own three Tier-1 operations —
`kmm`, `kbfct`, `kbfgs` — on one datapath, so the operand-select mux in front
and the result mux behind are real rather than elided. `kbfgs` drags a Barrett
reduction in beside the Montgomery one; a probe that timed `kmm` alone would
report a number the full instruction set cannot hold. A single butterfly, not
P lanes, because lanes are independent and add area rather than depth.

`tb/probe/test_tier1_probe.py` checks it against `model/isa/xkntt.py` — 2000
vectors biased towards the edges of the lazy output range, in all three
configurations. **A probe that ships nothing still needs a correctness test**,
because a datapath that computes the wrong answer is very likely a *smaller*
datapath than the right one, and that reports a frequency the real unit cannot
reach — the failure the step exists to prevent, arriving through the step meant
to prevent it.

### Both frozen latencies, one pipeline

The natural reading of "`kmm` 4, `kbfct` 5" is that `kmm` needs a shorter
pipeline. That reading is wrong and expensive. A four-stage pipeline serves
both: `kmm` does not use the butterfly segment at all — its result is the
Montgomery output sign-extended — so it **taps out after segment C**, three
edges in, and `kbfct`/`kbfgs` take the fourth. One datapath, one clock, two
latencies, and the cost is one 32-bit mux on an output that was already muxed.

That claim is measured rather than asserted: `STAGES=3` is the same datapath
with segments fused so that *every* operation finishes in four, and it is the
row below the headline.

---

## Four structural decisions, every one of them made by measurement

The first arrangement of this file closed at **78 MHz** and would have capped
the whole round if it had been believed. None of what follows was reasoned to
in advance.

**1. The Barrett path runs *beside* the Montgomery path, not after it.**
`kbfgs` computes `a' = barrett(a + b)` and `b' = mont(z * (b - a))`, and neither
depends on the other. Putting Barrett in the last segment — the obvious reading
of "then the conditional add/subtract" — gave a **13.638 ns** path with two
dependent DSP48E1s in series and no register between them. Starting it in the
multiply segment costs nothing and removes the entire chain.

**2. Which multiplies get a DSP is decided by the constant's population count.**
The second arrangement said "only the variable × variable multiply gets a DSP"
and pushed every constant into fabric. That is wrong for `BARR_V = 20159 =
0x4EBF`, which has **eleven set bits**: the shift-add tree it becomes measured
**9.492 ns** with eight CARRY4 levels, *worse* than the DSP it replaced. It is
right for `q = 3329` (four set bits) and for `QINV = 62209` truncated to its low
16 bits.

| multiply | form | where |
|---|---|---|
| `z * b` | variable × variable, 16×16 | DSP |
| `(a + b) * BARR_V` | constant, 11 set bits | DSP |
| `p[15:0] * QINV` | constant, low 16 bits only | fabric |
| `t * q` | constant, 4 set bits | fabric |

**3. `use_dsp = "no"` did not work, in either documented form.** Not as a
module-level attribute with a signal-level `"yes"` inside it, and not on an
`always_comb` variable. Both came back with `DSP48E1=4` in the cell histogram,
and the resulting **4.023 ns DSP hop** in the middle of a two-multiply
*dependent* chain was the whole critical path (9.715 ns, 103 MHz). Attributes
were asked twice and answered neither time. **Structure answered once**: the
reductions are written as shifts and adds, which is how a real Montgomery unit
for a fixed `q` is built anyway.

**4. The constant stays the one source of truth.** The first shift-add version
wrote the bit positions out by hand and guarded them with an elaboration check
comparing a *second* hand-written bit list against `QINV`. **That check was
vacuous** — mistyping a shift in the datapath left it silent, as fault injection
showed, because it compared one transcription against another rather than either
against the datapath. Deriving the shifts from the constant with a loop removes
the transcription instead of guarding it.

---

## The measurement that was green over nothing

`cmd.exe /c` treats `=` as an argument separator, exactly like a space. So

```
-tclargs rvntt_tier1_probe 8.0402 STAGES=2
```

arrived at the Tcl script as `... 8.0402 STAGES 2` — four `argv` entries, no `=`
anywhere. The generic pattern matched nothing, `synth_design` was called with no
generic at all, and **three "configurations" were implemented and all three were
the default.** They came back with byte-identical WNS at every search point.

The only thing that gave it away was a critical-path endpoint naming
`g_s3_reg` — a generate block the shallower configurations do not contain.

Counting A10's RISCOF exit code, A11's `sby` exit code, A14's `DSP=0`, A19,
A20's cocotb `return 0` and A21's dropped `Z` extensions, that is **the seventh
instance in this project of a report whose green was not about the thing it
named**. Two guards were added rather than one:

- `synth_ooc.tcl` hard-fails if trailing arguments parse to no generic, and
  prints `OOC_GENERICS:` on every run. Generics travel as `NAME:VALUE`;
  `synth_ooc.sh` translates from the `NAME=VALUE` callers still write.
- `probe_tier1.py` fingerprints each configuration's netlist (LUT, FF, DSP,
  worst endpoint) and **refuses to report two configurations that produced the
  same netlist**. A different pipeline depth cannot have the same flop count, so
  identical fingerprints are evidence the parameter did nothing.

---

## What this number does *not* include

Stated so it is not over-claimed.

- **The operands arrive through the core's forwarding mux and the result leaves
  through the core's writeback mux.** Both are the *core's* paths, outside this
  module and outside the OOC netlist. This is the unit's internal
  register-to-register frequency. Budgeting the surroundings here would mean
  inventing a number; the reg-to-reg `DATAPATH_DELAY` is reported beside the
  frequency so any budget can be applied by arithmetic.
- **The probe is alone on the die.** Placement is unconstrained by anything
  else. A12 measured the SoC at 78% route delay at 3.3% utilisation, so
  congestion is not the mechanism to worry about — but an integrated butterfly
  is placed against the register file and the forwarding network, and that is
  not free.
- **It is not Track B's design.** A different microarchitecture will measure
  differently. The number bounds *a* butterfly that satisfies the frozen
  contract; it does not bound every one.

---

## Method

`fpga/scripts/probe_tier1.py`, which is A12's method applied out of context:
binary search on the **constraint**, verdict from **post-route WNS**, and
`1/(T − WNS)` reported nowhere. The router optimises to the constraint and
stops, so a run that passed with slack to spare says only that the router had no
reason to try harder. **The reported figure is the fastest constraint observed
to pass.**

Vivado 2025.2, `xc7a100tcsg324-1` (**−1** speed grade), out-of-context, no I/O
delays. Raw data — every search point, with WNS, WHS, delay, cell histogram and
endpoint — at **`docs/a24-tier1-probe.json`**.

### Reproducing it

```sh
source toolchain/env.sh
python3 fpga/scripts/probe_tier1.py --stages 4 --stages 3 --stages 2 \
    --lo 80 --hi 150 --iters 4
```

Needs `dangerouslyDisableSandbox: true`; Vivado is on the Windows side. The
correctness half needs no FPGA at all and runs in the regression as
`tier1_probe`:

```sh
python3 tb/probe/test_tier1_probe.py
```
