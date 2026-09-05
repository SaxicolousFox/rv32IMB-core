# A16 — RV32IM on the Arty A7-100T, and the dual baseline

`MODS_A` A16, milestone **M7.1**. Measured on hardware. Every number here comes
from a UART capture off the board, parsed by `tb/fpga/parse_bench_uart.py`;
nothing is a simulation result. The machine-readable record is
[`docs/a16-benchmarks.json`](a16-benchmarks.json).

**[`docs/a13-benchmarks.md`](a13-benchmarks.md) is not superseded.** It is the
RV32I record and it stands. This document adds the RV32IM one beside it, and
§"The control" below re-runs A13's exact software on the RV32IM core so the two
can be compared on the same silicon rather than across two designs.

**Three JTAG programming passes per image, four report blocks each — all twelve
identical to the cycle.** Not "within tolerance": every counter, every CRC, byte
for byte. `tb/fpga/parse_bench_uart.py` enforces exact equality, and a one-cycle
perturbation of a real capture is rejected.

## Results

| | RV32I (A13) | **RV32IM (A16)** | |
|---|---|---|---|
| **DMIPS/MHz** | 0.7306 | **0.7325** | +0.3% |
| **CoreMark/MHz** | 0.9607 | **2.4309** | **×2.53** |
| **IPC, Dhrystone** | 0.7227 | **0.6860** | |
| **IPC, CoreMark** | 0.6930 | **0.7006** | |
| Dhrystones/second | 90 025.5 | 94 916.3 | |
| CoreMark (iterations/second) | 67.372 | 179.278 | |
| Core clock | 70.129 870 MHz | **73.750 000 MHz** | |

Raw counters, which everything above is computed from:

| | Dhrystone | CoreMark |
|---|---|---|
| work | 200 000 runs | 2 200 iterations |
| `mcycle` | 155 400 003 | 905 017 031 |
| `minstret` | 106 600 032 | 634 048 425 |
| wall time | 2.11 s | 12.27 s |
| per unit of work | 777.0 cycles, 533.0 instructions | 411 371 cycles, 288 204 instructions |

**Build flags**, as they appear in the capture itself:

```
-march=rv32im_zicsr -mabi=ilp32 -O2 -ffreestanding -nostdlib -nostartfiles
-fno-common -Wall
  | dhrystone: -std=gnu89 + upstream no-inline pragma
  | mul/div: hardware M extension
```

## Dhrystone gains almost nothing, and that is the interesting result

`MODS_A` A14 predicted *"Dhrystone and CoreMark both improve, because both call
`__divsi3`/`__mulsi3` today."* **Only one does.**

Dhrystone moves 0.7306 → 0.7325 DMIPS/MHz, **+0.3%**, and the raw counters say
exactly why: it retires **5.3% fewer instructions** (112 600 032 → 106 600 032)
and takes **0.26% fewer cycles**. The multiplies it does have were being
strength-reduced by the compiler already, and what few remain now cost four EX
cycles each instead of a handful of cheap ALU ops — so the instructions saved and
the stall cycles added very nearly cancel. IPC *falls*, from 0.7227 to 0.6860,
for the same reason: the same work in the same time, with fewer instructions to
divide by.

CoreMark's ×2.53 is where all the benefit is. Its matrix kernel is genuinely
multiply-bound.

**Read that as a statement about where M helps**, not as a disappointment. The
benefit is concentrated in multiply-bound code — which is exactly ML-KEM's
polynomial arithmetic, and exactly *not* Keccak. `MODS_A` §1 warned the effect
would be non-uniform; this measures it.

## The dual baseline

The point of the exercise. The **same** reference ML-KEM NTT
(`toolchain/kyber/ref/ntt.c`, pristine), compiled twice and linked into one
image, measured on one core at one clock within microseconds of itself:

| | cycles | instructions | IPC |
|---|---|---|---|
| `-march=rv32i` | 205 884 | 148 655 | 0.7220 |
| `-march=rv32im` | 39 058 | 23 805 | 0.6095 |
| **ratio** | **5.271×** | **6.245×** | |

All 256 output coefficients identical between the two builds (`ntt_check = 0`,
checksum `0xc5e604c0`), and the parser refuses the capture otherwise — two builds
measured against each other are only a comparison if they compute the same thing.

**6.245× against the 6.247× A13 measured on Spike** (148 645 / 23 795). The ten
extra instructions per side are the counter reads themselves. The hardware counts
are also bit-identical to the Verilator run, which is three independent models
agreeing on the same number.

**The cycle ratio is lower than the instruction ratio**, 5.271 against 6.245,
and that gap is the multiplier's four-cycle latency: the rv32im build's IPC drops
to 0.6095 because it stalls on `MUL`. A single-cycle multiplier would close most
of it — which is a real option for A17 and is why the two ratios are reported
separately rather than averaged into one headline.

### Why this matters for §B6 and §10

Plan §B6 estimates *"a software NTT on your RV32I core of roughly 15 000–30 000
cycles"*. The RV32IM instruction count lands inside that band and the RV32I one
is 5–10× outside it, which is the contradiction `MODS_A` §1 was written about:
**§B6's baseline assumed a hardware multiplier while §1.5 scoped the core
without one.** With both numbers measured on the same silicon, §10 M2 and M3 can
quote the honest baseline *and* show the other beside it, instead of choosing.

The full ML-KEM-768 KAT is bit-identical under both ISAs on Spike — digest
`11408dc4143ad8cd1762bbe1debd97e86a6e537eb974f1d7e7efbe0d6f52a6c1` for
`MARCH=rv32i` and `MARCH=rv32im` alike — so the equivalence is not just the NTT.

## The control: A13's software, unchanged, on the RV32IM core

A second bitstream, same core, same clock, carrying **A13's exact image**
(`-march=rv32i`, 200 000 runs, 800 iterations). Every counter:

| | A13, on the RV32I core | A16 control, on the RV32IM core |
|---|---|---|
| Dhrystone `mcycle` | 155 800 003 | **155 800 003** |
| Dhrystone `minstret` | 112 600 032 | **112 600 032** |
| CoreMark `mcycle` | 832 746 233 | **832 746 233** |
| CoreMark `minstret` | 577 088 625 | **577 088 625** |
| DMIPS/MHz | 0.7306 | **0.7306** |
| CoreMark/MHz | 0.9607 | **0.9607** |

**Adding the M extension cost RV32I code exactly zero cycles.** Not "within
noise" — identical, on hardware, across three programming passes. That is the
strongest form of "the RV32I figures are preserved rather than overwritten", and
it is worth having measured rather than assumed: a multi-cycle unit wired into
the stall path is exactly the kind of change that quietly costs a cycle
somewhere, and nothing short of counting would have shown it.

## Fmax

**73.752 MHz**, up from A12's 70.131 MHz — *after* adding a multiplier and a
divider. `rtl/soc/CLAUDE.md` has the six-run table, the critical path and the
explanation; the short version is that the ±0.4 ns placement spread A12 recorded
is larger than what M costs, and the critical path does not go through either new
unit.

**The lesson is A12's, in its harder direction.** A favourable Fmax movement
after a design change is exactly as much a measurement of a different design as
an unfavourable one, and far less likely to be questioned.

Utilisation: **2613 LUTs, 1148 FFs, 32 BRAM tiles, 4 DSP48E1** — M cost +487
LUTs, +235 FFs, 4 DSPs, no BRAM. `build_soc.tcl` now **fails the build** if fewer
than four DSPs are inferred: a multiplier that fell back to fabric would still be
correct, would cost about a thousand LUTs and several nanoseconds, and every
symptom would surface as a timing number with no obvious cause.

## Reproducing it

```sh
source toolchain/env.sh

# 1. the clock, then the image (runs and iterations are compile-time)
python3 fpga/scripts/gen_soc_clk.py --mhz 73.744        # -> 73.750000 MHz
python3 fpga/scripts/build_bench_image.py \
    --out fpga/generated/bench_init.mem --elf fpga/generated/bench_image.elf \
    --dhry-runs 200000 --iterations 2200 --gap-cycles 2000000 \
    --arch rv32im --ntt

# 2. the bitstream -- ~14 minutes, and it needs the sandbox disabled
SOC_MEM=$PWD/fpga/generated/bench_init.mem OUT=$PWD/fpga/build/bench_a16 \
STAGE_WIN='C:\Users\liamf\rvntt-bench' STAGE_WSL=/mnt/c/Users/liamf/rvntt-bench \
    bash fpga/scripts/build_soc.sh 1 1

# 3. program, capture, parse -- no human in the loop
python3 fpga/scripts/hw_bringup.py \
    --bit fpga/build/bench_a16/rvntt_soc_top.bit \
    --seconds 75 --send-byte -1 --out-name bench_uart.log \
    --parser tb/fpga/parse_bench_uart.py \
    --parser-arg=--min-blocks --parser-arg=3
```

The control image is the same three steps with `--arch rv32i --iterations 800`
and no `--ntt`. The Fmax search is
`python3 fpga/scripts/fmax_search.py --lo 73.0 --hi 79.0 --tol 0.7`; pass it an
**absolute** `--out`, or each iteration's reports are written where the next one
deletes them.

`ITERATIONS = 2200` and not A13's 800: CoreMark is 2.53× faster here, and 800
iterations would run for 4.7 s, under CoreMark's own ten-second minimum. The
parser enforces that rule and would have refused the capture. 2200 gives 12.27 s.

The board must be connected; nothing else is manual, and there is no LED to read
— A12's bring-up confirmed the pinout and A16 changes only the core's ALU and the
contents of the BRAM.

## What is measured here and what is not

- **Measured**: DMIPS/MHz, CoreMark/MHz and both IPCs are exact integer ratios
  of `mcycle` and `minstret` counts and do **not** depend on the clock, so none
  of them inherits the Fmax uncertainty. Dhrystones/second (94 916.3) and the raw
  CoreMark score (179.278) do, and are quoted at 73.750 000 MHz.
- **Not measured**: any `Xkntt` execution — no stage runs it. No flash image;
  configuration is still volatile. And still no attribution of the stall cycles,
  which needs the branch and stall counters `MODS_A` A18 adds.

  That last gap got *wider*, not narrower. Stalls and flushes per retired
  instruction:

  | | RV32I (A13) | RV32IM (A16) |
  |---|---|---|
  | Dhrystone | 0.384 | **0.458** |
  | CoreMark | 0.443 | **0.427** |

  Dhrystone's rose because M replaced cheap instructions with four-cycle ones,
  and CoreMark's fell because the multiplies it removed were long libgcc call
  sequences full of branches. **Nothing here says how much of either is the
  multiplier and how much is control flow**, and A18 is what would — which is
  precisely why it comes before A19 rather than after.
