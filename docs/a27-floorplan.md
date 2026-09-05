# A27 — Fmax, part 2: floorplanning, which had never been tried

**No pblock is adopted.** Both configurations made the design *slower*, and the
measurement is the result.

---

## The numbers

Each configuration is one implementation run at **100 MHz** (10.000 ns) with
`explore_postroute` — the constraint A26's design fails by 0.210 ns, chosen so
the router works to the same target in every case and the WNS values are
directly comparable.

| configuration | WNS at 100 MHz | vs no floorplan |
|---|---:|---:|
| **A26, no pblock — adopted** | **−0.210 ns** | — |
| P1 — `u_core` **and** `u_ram` in `CLOCKREGION_X0Y1:X1Y1` | −0.861 ns | **−0.651 ns** |
| P2 — `u_core` only; the memory left free | −0.399 ns | **−0.189 ns** |

**Fmax therefore remains A26's 96.246 MHz**, and the bitstream validated on
hardware in A28 is the unconstrained one.

---

## Why the premise was half wrong

`MODS_A2` A27 opens with *"67.6% of the critical path is route delay at 3.5%
utilisation. That is not congestion — there is nothing to congest. It is the
placer spreading a 19-level chain across a die it has no reason to keep it on
one corner of."*

The route fraction is right — A26's final path is **66.7% route**. The
inference is not. `post_route_clock_util.rpt` says the design already occupies
**four** clock regions and only four: X0Y1, X1Y1, X0Y2 and X1Y2, a contiguous
2×2 block in the middle of the die, with X0Y0, X1Y0, X0Y3 and X1Y3 completely
empty. **Vivado had already concentrated it.** What was left to remove was the
vertical span — from two clock-region rows to one — and that is exactly what
both pblocks did, and it cost time rather than saving it.

**Route delay at low utilisation is not automatically evidence of a spread
placement.** It can equally be a path whose eight hops are each a short hop
between different *kinds* of site — a BRAM, four LUT levels in three different
modules, a register — where the distance is set by where those site types exist,
not by where the placer felt like putting things. Compressing the region does
not shorten those hops; it makes every other net compete for the tracks that
were serving them.

## What the two configurations distinguish

P1 and P2 differ in one thing — whether the 32 BRAM tiles are pinned with the
core — and the difference is informative. **P2, which leaves the memory free, is
0.462 ns better than P1, which pins it.** So the more expensive half of the
constraint is the memory's placement: Vivado's own choice of BRAM sites was
better than being forced into the row that already held 29 of them.

That also answers A27's third item, "the BRAM tiles' own placement", without a
third run. A17 found 16 tiles beat 32 by ≥3.5 MHz and §3.7 declined to cut
memory; A27 proposed constraining where the 32 go as "the same lever without the
cost". **It is not the same lever.** Fewer tiles is less to reach; the same tiles
in a chosen place is more constraint on a placer that was already choosing well.

## What was checked, and why it needed checking

A floorplanning experiment whose constraint silently fails to arrive reports the
unconstrained number as a result — which is exactly what happened to A24 three
steps ago, when `cmd.exe` ate an `=` and three "configurations" were all the
default. So `build_soc.tcl` prints, on every run:

```
SOC_PBLOCK: present | none
SOC_PBLOCK_CELLS: pb_soc 2 cell(s) at CLOCKREGION_X0Y1:CLOCKREGION_X1Y1
```

The second line is the one that matters: `add_cells_to_pblock` with `-quiet`
over a renamed instance creates an **empty** region and constrains nothing, and
that run is indistinguishable from an unconstrained one in every other respect.
Both configurations reported the cell counts they should — 2 for P1 (`u_core`
and `u_ram`), 1 for P2 — and `SOC_PBLOCK: none` is printed for the adopted
build, so "no floorplan" is recorded rather than assumed.

`SOC_PBLOCK=<file>` selects a constraint from `fpga/constraints/`; unset, the
build is byte-for-byte the flow A26 measured, which is what makes the comparison
one-variable. A missing file is a hard failure, not a silent skip.

---

## Where the Fmax work stops

`MODS_A2` A27's stop rule, quoted because it is the reason this step ends here
rather than continuing:

> If A26 and A27 together land below 110 MHz having worked through the levers
> above, **that is the result, and it is reported as one.** Further Fmax work
> past this point means pipeline surgery — splitting EX, re-timing the register
> file, a deeper fetch — every one of which changes cycle counts, changes the
> hazard structure, invalidates A18's identity and A19's predictor model, and
> makes §8 I1's Tier-1 integration harder against a moving target.

**A26 and A27 land at 96.246 MHz against a 110 MHz goal.** The remaining 13.8%
is in a path that is eight logic levels long and two-thirds route: the
instruction memory, the decoder, the load-use hazard, the ID/EX register. The
one logical term left to remove is `uses_rs1`/`uses_rs2` emerging from the
decoder's full `unique case` before the hazard comparator can start, and taking
it out means a second decode path in the one module whose equivalence against
the Python decoder is swept over 10⁶ words.

**And the goal was never Fmax for its own sake.** A24 measured the Tier-1
butterfly at 128.125 MHz, so 96.246 MHz is a clock the coprocessor clears with
33% margin — comfortably more than the 10% A24's stop rule requires. The core is
not the constraint on Track B, which is the question this phase existed to
settle.
