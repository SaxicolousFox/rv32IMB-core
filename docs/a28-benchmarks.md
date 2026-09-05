# A28 — the performance round's baseline

**The same five measurements as A23, on the machine A25, A26 and A27 produced.**
Both baselines are kept: A23's is the ISA round's, A28's is the performance
round's, and `MODS_A2` §3.6's rule is that neither replaces the other because
they are measurements of two different machines.

Three JTAG programming passes, five report blocks each, **all 42 integer
counters identical across all fifteen blocks.**

---

## Headline

| | A23 | **A28** | change |
|---|---:|---:|---:|
| **Fmax** | 74.577 MHz | **96.246 MHz** | **+29.1%** |
| DMIPS | 69.811 | **90.096** | **+29.1%** |
| Dhrystones/sec | 122 658 | **158 299** | +29.1% |
| CoreMark score | — (2500 iter) | **326.24** | — |
| DMIPS/MHz | 0.9361 | **0.9361** | 0 |
| CoreMark/MHz | 3.1866 | **3.3896** | **+6.4%** |
| Dhrystone IPC | 0.8734 | **0.8734** | 0 |
| CoreMark IPC | 0.8168 | **0.8689** | **+6.4%** |
| `MUL` EX occupancy | 4 cycles | **2 cycles** | −2 |
| LUTs / FFs | 5770 / 2027 | 5793 / 1926 | +23 / −101 |

At 96.246 000 MHz, `explore_postroute`, **no floorplan** — A27 measured two
pblocks and adopted neither. WNS +0.004 ns, WHS +0.065 ns.

**The per-MHz figures separate the three things that produced this.** The
multiply — 4 → 3 in A25, 3 → 2 here — raised CoreMark/MHz and CoreMark IPC by
6.4% and moved Dhrystone's **not at all**, because Dhrystone's one multiply per
run had already been strength-reduced into Zba shift-adds by A21's B. A26 raised
the clock by 29.1% and moved **no** per-MHz figure at all, which is what
"cycle-neutral" looks like when it is true. A27 moved nothing, by measurement.

### The 2-cycle multiply, which A25 declined and A28 adopted

A25 took `MUL` from 4 cycles to 3 and recorded 2 as **unmeasured, not ruled
out** — because at 2 the 33×33 product is combinational into the module's
`result` **port**, and an out-of-context run with no I/O delays does not time
that path at all. Its endpoint moved to the divider, which was the tell.

A26 then gave the SoC a clock worth probing at, and the path could be measured
where it actually lands. At the adopted 96.246 MHz:

| `MUL_CYCLES` | WNS | LUTs | FFs |
|---|---:|---:|---:|
| 3 | +0.010 ns | 5740 | 1972 |
| **2 — adopted** | **+0.004 ns** | 5793 | 1926 |

**Six picoseconds**, on a design whose build-to-build spread is over a
nanosecond, for another 3.2% of CoreMark. `MODS_A2` A25's stop rule named the
wrong hazard — *"if 2 cycles cannot be reached without a data-dependent path"* —
because there is no data-dependent path at any latency: the multiplier is a
register chain whose length is its latency. The hazard was Fmax, and it turned
out not to be one.

**CoreMark's stall term decomposes exactly, twice over.** 32 886 000 stall
cycles over 3500 iterations is **9 396 per iteration**, one per multiply at
`MUL = 2`. A25 measured 46 980 000 over 2500 iterations — **18 792 per
iteration**, two per multiply at `MUL = 3`. The same 9 396 multiplies per
iteration, counted two different ways.

**One mutation changed verdict, and the reason is a property of the design.**
`muldiv_done_one_cycle_early` was caught by `rv32um/mul` at 4 cycles because it
read the product register before the product reached it. At 2 cycles there *is*
no product register — `MUL_PIPE` is 0 and the 33×33 is combinational from held
operand registers — so `done` a cycle early cannot read a partial result, and
the mutation degrades to a timing-only fault. The two span checks
(`directed:a14_muldiv`, `random:muldiv`) still catch it, and `rv32um/div` still
does because the divider is untouched at 34 cycles and its loop really does hold
partial state. The catcher list was narrowed with that reason written down, not
because the test got weaker.

---

## Against A25: every counter identical

A26's requirement is that not one cycle count may change. On the board, against
A25's run of the same benchmarks:

| counter | A25 (70.000 MHz) | A28 (96.246 MHz) | Δ |
|---|---:|---:|---:|
| Dhrystone cycles | 1 216 000 065 | 1 216 000 065 | **0** |
| Dhrystone instret | 1 062 000 032 | 1 062 000 032 | **0** |
| Dhrystone load-use | 52 000 006 | 52 000 006 | **0** |
| Dhrystone multi-cycle EX | 66 000 000 | 66 000 000 | **0** |
| Dhrystone redirects | 18 000 046 | 18 000 046 | **0** |
| Dhrystone mispredicts | 18 000 046 | 18 000 046 | **0** |
| Dhrystone BTB hits | 171 999 989 | 171 999 989 | **0** |
| Dhrystone taken transfers | 182 000 027 | 182 000 027 | **0** |
| ML-KEM NTT `rv32i` | 165 752 | 165 752 | **0** |
| SHAKE128 `rv32im` | 373 407 | 373 407 | **0** |
| SHAKE128 `rv32imb` | 312 402 | 312 402 | **0** |
| ML-KEM NTT `rv32im` | 31 258 | **28 570** | −2 688 |

**Everything A26 and A27 could touch is identical**, and Dhrystone's DMIPS/MHz
and IPC match A25's to four decimal places. The two rows that move are the ones
A28's own 2-cycle multiply moves, and both move by exactly the multiply count:
2 688 in the NTT, 9 396 per CoreMark iteration. `rv32i` and SHAKE128 are
unchanged because neither contains a `MUL` at all.

**The identity residuals are unchanged too, and that is the sharper check.**

```
Dhrystone   -40      (A23: -40)
CoreMark    -48      (A23: -48)
```

`MODS_A2` §6 revised M7.3's done-when because residual-exactly-zero is
structurally unachievable on hardware: A18's instrument samples every counter at
one instant and software cannot, because each counter is read by its own
instruction and the windows nest. The residual is therefore the snapshot code's
own footprint — `6 + 0 + 2×17` on Dhrystone — and **it is a constant**. Getting
the same two numbers out of a design whose clock moved 29% and whose forwarding
network, jump-target adder and predictor lookup were all rebuilt is a stronger
statement than the cycle counts alone.

---

## CoreMark runs 3500 iterations, not 2500

At 96.246 MHz the 2500-iteration run finished in **7.91 s**, under CoreMark's
own 10 s reporting minimum, and the parser refused it — correctly. 3300
iterations gave 10.44 s at `MUL = 3`; adopting the 2-cycle multiply took that
back under the margin, so the final image runs **3500** (10.73 s). The minimum
was never overridden.

**So CoreMark's raw cycle counts are not comparable with A23's or A25's**, in
exactly the way A19's 2200→2500 change was not comparable with A16's and A17's.
CoreMark/MHz, CoreMark IPC and the score-per-clock *are* comparable, and
CoreMark/MHz is identical to A25's to four decimal places.

This is the second time a faster core has invalidated a CoreMark iteration
count. It is a property of the benchmark's run rules, not of the measurement.

---

## The counter pairing, reported for the last time

`MODS_A2` A28 asks for A20's hardware counters and A18's simulation instrument
to be reported **alongside each other one final time**, and says what follows if
they agree. On the shipped ISA (`rv32imb`):

| region | event | hardware | instrument | agree |
|---|---|---:|---:|:-:|
| Dhrystone | load-use interlock cycles | 5 200 | 5 200 | ✅ |
| Dhrystone | multi-cycle EX stall cycles | 6 600 | 6 600 | ✅ |
| Dhrystone | fetch redirects | 1 637 | 1 637 | ✅ |
| Dhrystone | taken control transfers | 18 204 | 18 204 | ✅ |
| CoreMark | load-use interlock cycles | 41 444 | 41 444 | ✅ |
| CoreMark | multi-cycle EX stall cycles | 18 792 | 18 792 | ✅ |
| CoreMark | fetch redirects | 8 669 | 8 669 | ✅ |
| CoreMark | taken control transfers | 74 265 | 74 265 | ✅ |

**Eight of eight, to the count.** A20's done-when is therefore satisfied on the
final design as well as on the one it was written against.

**From this point the hardware counters are the project's primary source and
A18's simulation instrument is the cross-check, not the record.** That is a
change in what this project's numbers *are*, and A28 exists partly to say so out
loud rather than let it happen by drift. The instrument keeps two jobs the
counters cannot do: it closes the identity at residual **exactly zero**, which
software reading its own counters structurally cannot; and it runs without a
board.

---

## Both baselines, and which to divide by

**§10 M2 and M3 divide by A28's numbers, not A23's or A16's.**

| | A16 | A23 | A25 | **A28** |
|---|---:|---:|---:|---:|
| ML-KEM NTT, `rv32i` | 205 884 | 165 752 | 165 752 | **165 752** |
| ML-KEM NTT, `rv32im` | 39 058 | 33 946 | 31 258 | **28 570** |
| cycle ratio | 5.271 | 4.883 | 5.303 | **5.802** |
| SHAKE128, `rv32im` | — | 373 407 | 373 407 | **373 407** |
| SHAKE128, `rv32imb` | — | 312 402 | 312 402 | **312 402** |
| B's Keccak speedup | — | 1.1953× | 1.1953× | **1.1953×** |

The software NTT has got **faster three times** across this round — A21's B and
A22's Zicond through A23, then A25's 3-cycle multiply, then A28's 2-cycle one —
so §10 M2's reported coprocessor speedup gets **smaller** each time. `MODS_A2` §3.6 P1 required that consequence
to be written down before it was measured, and it was.

All 256 output coefficients agree between the two NTT builds
(`ntt_check = 0`), and the two SHAKE128 digests are identical
(`kc_check = 0`) — the same guards A23 shipped.

---

## Reproducing it

```sh
source toolchain/env.sh
python3 fpga/scripts/gen_soc_clk.py --mhz 96.246
python3 fpga/scripts/build_bench_image.py --out fpga/generated/bench_a28.mem \
    --elf fpga/generated/bench_a28.elf --core-hz 96246000 \
    --dhry-runs 2000000 --iterations 3500 --arch rv32imb --ntt --hpm --keccak
SOC_MEM=$PWD/fpga/generated/bench_a28.mem OUT=$PWD/fpga/build/bench_a28 \
    bash fpga/scripts/build_soc.sh 1 1 explore_postroute
RVNTT_HW=1 python3 tb/run_regress.py -k bench_hardware
```

`SOC_PBLOCK` is deliberately **unset**: A27's two floorplans both cost time and
neither is adopted. Raw data: `docs/a28-benchmarks.json`. Fmax search log and
post-route reports: `fpga/build/fmax_a26/`.
