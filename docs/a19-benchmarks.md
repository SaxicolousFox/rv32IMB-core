# A19 — branch prediction on the Arty A7-100T

The design is `docs/a19-bpred-spec.md`; what it did to the cycle counts is
`docs/a19-bpred.md`. This is what the board measured, and it is the record
`MODS_A` M7.2 asks for.

Machine-readable: `docs/a19-benchmarks.json`. Raw captures and per-run JSON:
`fpga/build/bench_a19/a19_run{1,2,3}.{log,json}`.

**A16's RV32IM figures and A13's RV32I figures are not superseded.** They are
the record of the machine at those steps, kept for the same reason A13's were
kept through A16. This is the same software on the same board with a predictor
added.

---

## Results

Three JTAG programming passes, four report blocks each. **All twelve blocks are
identical to the cycle** (0 ppm), and `tb/fpga/compare_bench_runs.py` agrees
across all three passes on 29 invariant and 5 scaled fields.

| | A17 @ 86.486 MHz | **A19 @ 77.500 MHz** | |
|---|---|---|---|
| Dhrystone cycles, 200,000 runs | 155,400,030 | **121,800,090** | **1.2759×** |
| Dhrystone instructions | 106,600,032 | 106,600,032 | **identical** |
| cycles per run | 777.000 | **609.000** | |
| **IPC** | 0.6860 | **0.8752** | +27.6% |
| **DMIPS/MHz** | 0.7325 | **0.9346** | +27.6% |
| DMIPS | 63.3513 | **72.4290** | **+14.3%** |
| Dhrystones/sec | 111,308.2 | **127,257.7** | |
| **CoreMark/MHz** | 2.4309 | **2.8933** | +19.0% |
| CoreMark iterations/sec | 210.2394 | **224.2269** | **+6.7%** |
| CoreMark IPC | 0.7006 | **0.8338** | +19.0% |
| ML-KEM NTT, `rv32i` | 205,884 | **165,752** | 1.2421× |
| ML-KEM NTT, `rv32im` | 39,058 | **33,948** | 1.1505× |
| **Fmax** | 86.490 MHz | **77.501 MHz** | **−10.4%** |
| LUTs / FFs | 2,637 / 1,157 | 3,471 / 1,530 | +31.6% / +32.2% |
| BRAM tiles / DSP48E1 | 32 / 4 | 32 / 4 | unchanged |

**The predictor costs 10.4% of the clock and returns more than that in IPC**, so
both benchmarks improve in absolute terms on a slower part. `DMIPS/MHz 0.9346`
and both IPC figures land inside §A13's `0.8–1.2` and `0.75–0.95` bands — the
first time either has.

The NTT's 256 output coefficients are identical between the `rv32i` and `rv32im`
builds (`sum 0xc5e604c0`), as in A16, so the dual baseline still agrees.

---

## CoreMark's iteration count changed, and raw cycles are not comparable

A16 and A17 ran **2200** iterations. A19 runs **2500**, and the reason is that
A19 worked: 2200 iterations at 1.19× fewer cycles on a 10.4% slower clock
finished in **9.81 s**, under CoreMark's own **10 s** reporting minimum, and
`tb/fpga/parse_bench_uart.py` refused all three passes.

That refusal is the check doing its job. `--allow-short` exists for calibration
runs and was **not** used: publishing a score that breaks the benchmark's own run
rules is the same category of error as reporting Dhrystone's overflowed
`Dhrystones_Per_Second`, which A13 fixed by computing rates in Python rather than
by ignoring the wrap.

**The consequence, stated before the numbers rather than after.** Dhrystone stays
at 200,000 runs and its cycle counts remain directly comparable to A16 and A17.
CoreMark's do **not** — 2500 against 2200. `CoreMark/MHz` and `cm_ipc` are
per-iteration and remain valid, and they are what the table above compares and
what §A13's bands are stated in. Anyone diffing raw CoreMark `mcycle` between
A17's captures and these will see a change that is partly the iteration count.

---

## An open item: simulation at 2,000 Dhrystone runs is 1% high

| where | runs | cycles | per run |
|---|---|---|---|
| simulation | 2,000 | 1,230,096 | **615.048** |
| simulation | 4,000 | 2,436,100 | 609.025 |
| simulation | 8,000 | 4,872,100 | 609.013 |
| **this board** | 200,000 | 121,800,090 | **609.000** |

The 4,000-run, 8,000-run and hardware points agree; `cycles(8000)` is exactly
`2 × cycles(4000)`, so the intercept there is ~100, not thousands. The 2,000-run
point carries **exactly 3 extra mispredicts per run** — 5,998 extra redirects,
11,996 extra cycles, matching to the cycle.

Two candidate explanations were tested and **both are wrong**:

- **Not the image configuration.** 2,000 runs with CoreMark and the NTT linked in
  and 2,000 runs without them give **byte-identical** counters.
- **Not a fixed warm-up cost.** A fixed cost `W` would appear as `609 + W/runs`
  and would still be visible at 4,000 runs; it is not.

**A18 without the predictor shows no such effect** — 777.015 cycles/run at 2,000
against this board's 777.000, consistent with A13's intercept of 30 — so the
effect belongs to the predictor. It is **unexplained**, and it is written down
here rather than smoothed away.

Its consequence is bounded and matters: the 2,000-run simulation ratio of
**1.2633×** understates this board's **1.2759×**, and a per-run rate taken from a
2,000-run simulation is not a safe proxy for this design. Every headline figure
above is from the board.

---

## Fmax, and why it carries a wider band than usual

**77.501 MHz**, WNS +0.003 ns, WHS +0.030 ns; fastest failing constraint 77.942
MHz. Vivado 2025.2, `xc7a100tcsg324-1`, **−1** speed grade, `explore_postroute`.

**The search is not monotonic.** 79.246 MHz failed by −1.186 ns while the
*tighter* 80.998 MHz failed by only −0.676. Implied path delay for one netlist
spans **12.90–13.81 ns — a 0.9 ns spread, twice the ±0.4 ns A12 recorded**. A
binary search assumes pass/fail is monotone in frequency and it is not, so
**77.501 MHz is the highest constraint observed to pass in this search, not a
boundary**. The seven points are tabulated in `docs/a19-bpred.md`.

The A17-to-A19 comparison survives that: 8.99 MHz is far outside the spread. A
future ±1 MHz claim against this baseline would not, and should not be made
without repeated runs.

**This is the core-only Fmax baseline for §9**, superseding A17's 86.490 MHz.

---

## Reproducing it

```sh
source toolchain/env.sh

# 1. the clock, then the image (runs and iterations are compile-time)
python3 fpga/scripts/gen_soc_clk.py --mhz 77.501
CLK=$(grep -oE 'SOC_CORE_HZ +[0-9]+' fpga/generated/soc_clk.svh | awk '{print $2}')
python3 fpga/scripts/build_bench_image.py \
    --out fpga/generated/bench_init.mem --elf fpga/generated/bench_image.elf \
    --core-hz "$CLK" --dhry-runs 200000 --iterations 2500 --arch rv32im --ntt

# 2. the bitstream -- ~18 minutes, and it needs the sandbox disabled
SOC_MEM="$PWD/fpga/generated/bench_init.mem" OUT="$PWD/fpga/build/bench_a19" \
    bash fpga/scripts/build_soc.sh 1 1 explore_postroute

# 3. program, capture, parse -- no human in the loop
for i in 1 2 3; do
  python3 fpga/scripts/hw_bringup.py --bit fpga/build/bench_a19/rvntt_soc_top.bit \
      --seconds 75 --send-byte -1 --out-name "a19_run${i}.log" \
      --keep "fpga/build/bench_a19/a19_run${i}.log" \
      --parser tb/fpga/parse_bench_uart.py \
      --parser-arg=--min-blocks --parser-arg=3 \
      --parser-arg=--json --parser-arg="fpga/build/bench_a19/a19_run${i}.json"
done
```

---

## What is measured here and what is not

**Measured**: cycles and instructions from `mcycle`/`minstret` on the board, over
three programming passes, all twelve blocks bit-identical; CoreMark's four CRCs
match its self-check; the NTT's 256 coefficients agree between the two builds.

**Not measured**: anything about `Xkntt` — no stage executes it yet. Nothing
about power. Nothing about a flash image; configuration is still volatile.

**Not mechanisable, and this is not a formality**: which signal drives which
output pad. `tb/fpga/check_xdc_pins.py` compares every constraint against a
pinout extracted from the vendor file, which is what stops A12's transposed RGB
LED pins recurring — but a swapped *output* is invisible to lint, elaboration,
synthesis, timing, programming and a byte-perfect UART capture. A19 adds no new
outputs, so it adds no new exposure; that is an argument from scope, not a check.
