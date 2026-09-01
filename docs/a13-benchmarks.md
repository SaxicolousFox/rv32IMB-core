# A13 — Dhrystone, CoreMark and IPC on the Arty A7-100T

Measured on hardware. Every number below comes from a UART capture off the
board, parsed by `tb/fpga/parse_bench_uart.py`; nothing here is a simulation
result. The machine-readable record is
[`docs/a13-benchmarks.json`](a13-benchmarks.json), and **Reproducing it** has the
exact commands.

**Three separate JTAG programming passes, three report blocks each — all nine
blocks identical to the cycle.** Not "within tolerance": every counter, every
CRC, byte for byte. That is the plan's "reproducible across three runs",
and on a machine with no cache, no DRAM and no interrupt source it is the right
bar; `tb/fpga/parse_bench_uart.py` enforces exact equality rather than a
tolerance, and a one-cycle perturbation of a real capture is rejected.

## Results

| | value |
|---|---|
| **DMIPS/MHz** | **0.7306** |
| **CoreMark/MHz** | **0.9607** |
| **IPC, Dhrystone** | **0.7227** |
| **IPC, CoreMark** | **0.6930** |
| Dhrystones/second | 90 025.5 |
| CoreMark (iterations/second) | 67.372 |
| Core clock | 70.129 870 MHz |
| Part / speed grade | `xc7a100tcsg324-1`, **-1** |
| Vivado | 2025.2, default strategy |

Raw counters, which is what everything above is computed from:

| | Dhrystone | CoreMark |
|---|---|---|
| work | 200 000 runs | 800 iterations |
| `mcycle` | 155 800 003 | 832 746 233 |
| `minstret` | 112 600 032 | 577 088 625 |
| wall time | 2.22 s | 11.87 s |
| per unit of work | 779.0 cycles, 563.0 instructions | 1 040 933 cycles, 721 361 instructions |

The instruction counts are printed so the benchmark can be checked for having
actually run: 563 instructions per Dhrystone iteration is the right order for
Dhrystone 2.1 built with the no-inline pragma, and a compiler that had optimised
the benchmark away would show a small fraction of it.

**Build flags**, in full, as they appear in the capture itself:

```
-march=rv32i_zicsr -mabi=ilp32 -O2 -ffreestanding -nostdlib -nostartfiles
-fno-common -Wall
  | dhrystone: -std=gnu89 + upstream no-inline pragma
  | mul/div: libgcc software (no M extension)
```

## How to read these numbers

**DMIPS/MHz and CoreMark/MHz do not depend on the clock frequency**, and that is
not a coincidence:

```
DMIPS/MHz    = (runs * f / cycles) / 1757 / (f / 1e6)  =  runs * 1e6 / (cycles * 1757)
CoreMark/MHz = (iters * f / cycles)      / (f / 1e6)   =  iters * 1e6 / cycles
```

`f` cancels. Both are exact ratios of two integers read out of `mcycle`, so
neither inherits the ±0.4 ns uncertainty in the measured Fmax — which matters,
because A12 found that spread to be larger than it looks (correcting two output
pins, with no logical change at all, moved Fmax by 3 MHz through placement
alone). **Dhrystones/second and the raw CoreMark score do depend on `f`** and are
quoted at 70.129 870 MHz, the frequency the MMCM is configured for in this
bitstream.

Against the plan's expectations: IPC of 0.69–0.72 is below the 0.75–0.95 it
predicts, and 0.7306 DMIPS/MHz is below the 0.8–1.2 range. Both have the same
two causes, and neither is a surprise:

- **There is no M extension.** Every multiply, divide and modulo in either
  benchmark is a call into libgcc's `__mulsi3` / `__divsi3` / `__umodsi3` —
  branch-heavy shift-and-subtract loops. That inflates the instruction count and
  fills it with taken branches.
- **Branches are statically predicted not-taken and there is no BTB.** Every
  taken branch costs bubbles, and libgcc's division loops are almost nothing but
  taken branches.

Dhrystone spends 155 800 003 cycles retiring 112 600 032 instructions, so
43 199 971 cycles — 0.384 per instruction — go to stalls and flushes. **This
work does not attribute that split**; the core has no branch or stall counters,
and adding them is outside A13. It is the measurement the plan's optional
improvement loop (a 2-bit bimodal predictor with a 64-entry BTB) would need
first, and the obvious next thing to build if that loop is taken up.

## Reproducing it

```sh
source toolchain/env.sh

# 1. the image (Dhrystone runs and CoreMark iterations are compile-time)
python3 fpga/scripts/build_bench_image.py \
    --out fpga/generated/bench_init.mem --elf fpga/generated/bench_image.elf \
    --dhry-runs 200000 --iterations 800 --gap-cycles 2000000

# 2. the bitstream -- ~14 minutes, and it needs the sandbox disabled
SOC_MEM=$PWD/fpga/generated/bench_init.mem OUT=$PWD/fpga/build/bench \
STAGE_WIN='C:\Users\liamf\rvntt-bench' STAGE_WSL=/mnt/c/Users/liamf/rvntt-bench \
    bash fpga/scripts/build_soc.sh 1 1

# 3. program, capture, parse -- no human in the loop
python3 fpga/scripts/hw_bringup.py \
    --bit fpga/build/bench/rvntt_soc_top.bit \
    --seconds 65 --send-byte -1 --out-name bench_uart.log \
    --parser tb/fpga/parse_bench_uart.py \
    --parser-arg=--min-blocks --parser-arg=3
```

Or, with the board plugged in, `RVNTT_HW=1 python3 tb/run_regress.py -k bench`.

The board must be connected; nothing else is manual. There is no LED to read
this time — A12's bring-up already confirmed the pinout, and A13 changes only
the contents of the BRAM.

## Methodology

### Where the benchmarks come from

Both are compiled **in place** out of their upstream checkouts, which stay
pristine — the same rule that keeps `toolchain/kyber/` pristine, and for the same
reason: `toolchain/riscv-tests/` is also what the `riscv_tests` regression runs
against.

| | source | pin |
|---|---|---|
| Dhrystone 2.1 | `toolchain/riscv-tests/benchmarks/dhrystone/` | `2ebecad` |
| CoreMark | `toolchain/coremark/` | `1f483d5` |

The port is entirely in files this project owns:

- `sw/bench/include/util.h` — Dhrystone's platform header. `dhrystone_main.c`
  includes it *after* `dhrystone.h`, which is the seam that lets it override
  `NUMBER_OF_RUNS` without editing the benchmark.
- `sw/bench/coremark_port/core_portme.c` / `.h` — exactly the file EEMBC expects
  a porter to write, derived from their own `barebones/` template.
- `sw/bench/bench_io.c`, `bench_lib.c`, `dhry_glue.c`, `bench_main.c` — console,
  string functions, Dhrystone's `setStats` hook, and the driver.

`-Dmain=dhry_main` and `-Dmain=coremark_main` rename each benchmark's `main` per
translation unit so `bench_main.c` can run them in sequence.

**`-std=gnu89` is not cosmetic.** Dhrystone is K&R C throughout, GCC 15 defaults
to C23, and C23 removed both old-style definitions and the empty parameter list —
`Enumeration Func_1 ();` would become a prototype taking no arguments and the
two-argument call a hard error.

### One image, two benchmarks

Both run from one bitstream. `$readmemh` bakes the program into the BRAM at
synthesis time — there is no loader — so a second benchmark would mean a second
fourteen-minute implementation run and a second programming pass.

Running Dhrystone first cannot perturb CoreMark **on this SoC**: there is no
cache, no DRAM refresh, no interrupt source and no other bus master, so the
machine CoreMark starts on is bit-identical to the one it would have started on
alone. On anything with a cache that would not be true and the two would have to
be separated.

### CoreMark's run rules

`PERFORMANCE_RUN=1`, `TOTAL_DATA_SIZE=2000`, `MEM_METHOD=MEM_STACK`,
`SEED_METHOD=SEED_VOLATILE`, one context. CoreMark reports this as
`2K performance run parameters for coremark.` and validates its own results
against published CRCs:

```
seedcrc 0xe9f5   crclist 0xe714   crcmatrix 0x1fd7   crcstate 0x8e3a
```

`ITERATIONS=800` gives **11.87 s**, over CoreMark's ten-second minimum. It was
not guessed: one iteration costs 1 040 925 cycles in simulation and four cost
4 163 504, i.e. linear to within 50 cycles, so the count needed at 70.13 MHz is
arithmetic. The parser enforces the ten-second rule independently
(`--allow-short` exists only for calibration runs and is itself fault-injected).

`HAS_FLOAT` is 0 — there is no FPU and no soft-float linked — so CoreMark's own
`Iterations/Sec` line prints as an integer and its official `CoreMark 1.0 :` line
is compiled out. **The score quoted here is not that integer**; it is computed
from the raw `mcycle` count, exactly.

### Why every rate is computed in Python

Dhrystone's own `Microseconds` and `Dhrystones_Per_Second` **overflow 32-bit
`long`** at these run counts — cycles-per-run × 10⁶ is about 2.5 × 10⁹ — so the
two rate lines it prints are wrong. They are ignored. The program prints run
counts and raw `mcycle`/`minstret` deltas and nothing derived; the parser does
the arithmetic with no width limit.

That overflow is signed, i.e. undefined behaviour. `-fwrapv` would define it
without touching the source, and the obvious move is to add it — **so it was
measured instead: it costs 1.4%** (779.0 → 790.1 cycles per run), because it
changes code generation inside the timed loop. Depressing the score to tidy up
two lines that are printed and discarded is the wrong trade, so the flag is not
used and the overflow is documented here instead. The affected computation
happens after everything measured, is only printed, and feeds no branch or
pointer.

### Timing windows

Dhrystone times itself with `read_csr(mcycle)` in `Start_Timer`/`Stop_Timer`.
IPC needs `minstret` over the same window, and `setStats(1)`/`setStats(0)` — the
platform hook the benchmark calls immediately outside its own timer — is the only
pair of points where both can be sampled without editing it. The setStats window
is therefore a few instructions **wider**, and both are reported so that is
visible rather than assumed: **27 cycles** out of 155 800 003. The parser fails if
the gap exceeds 4096 cycles.

That bound is absolute rather than a percentage on purpose. A relative bound
would be a different test at every run length — 27 cycles is 0.1% of a 30-run
sanity check and 0.00002% of the real one, so only the first would ever fail.
This was caught by the 30-run simulation failing a 0.1% check that the real run
would have passed vacuously.

### Verification, and what each check can actually catch

A wrong score has three possible causes that look identical from outside — a
broken port, a broken core, a broken formatter — so each is checked separately.

| check | what it would catch | fault injection |
|---|---|---|
| `bench_printf` | a formatting bug: the only path from a counter to a printed number, so a mis-printed correct measurement and a genuinely slow core produce the same capture | 340 cases diffed against glibc; 4/4 injected formatter faults caught |
| `bench_host` | a broken port: the same sources compiled natively, checking CoreMark's CRCs and Dhrystone's published final values in about a second | breaking one `dhry_verify` expectation fails it |
| `bench_sim` | a broken core: the same image on the RTL under Verilator | 3/3 A13 mutations caught |
| `bench_uart_parser` | a checker that cannot fail | 20/20 injected faults, including both flags that *relax* a check |
| `bench_hardware` | the measurement itself | — |

If the CRCs are wrong on the board and right on the host, the core is at fault;
if they are wrong in both, the port is. Without the host run those are one
symptom.

**Dhrystone's final values are checked programmatically, not printed.** Upstream
`dhrystone.c` defines `debug_printf` as an empty function, which suppresses the
sixty lines of "should be" prose; that is upstream's configuration and this port
does not change it. `dhry_verify()` in `sw/bench/dhry_glue.c` checks all fourteen
published expectations and returns a bitmask, so a failure names which one broke.

It has to take a snapshot to do that: `Ptr_Glob` and `Next_Ptr_Glob` come from
`alloca()` **inside** Dhrystone's `main`, so once it returns they point at dead
stack — and on a bare-metal machine with nothing to reuse it, reading them
afterwards would usually still "work", which is the worst possible behaviour for
a check. `setStats(0)` fires while that frame is still live.

### `mcycle` now has a ground-truth reference

`rtl/core/rvntt_csr.sv` notes that `mcycle` is a real cycle counter and therefore
disagrees with Spike, whose `mcycle` advances once per instruction — and that
**nothing compares it**. A13 makes that sentence false. Verilator counted the
clock edges itself, so `tb/unit/test_bench_verilator.py` checks that the timed
regions plus the computable UART transmit time account for the simulated run:
**96.4%**, with the remainder being startup and the inter-block gap.

The mutation `mcycle_counts_retires_not_cycles` — Spike's behaviour, and
self-consistent enough that IPC comes out at a perfectly plausible 1.00 — is
caught by exactly this and by nothing else.

Hardware and simulation agree **to the cycle**: 779.0 cycles per Dhrystone run
and 0.9607 CoreMark/MHz in both.

## What is *not* settled

- **No attribution of the 0.384 stall cycles per instruction.** See above.
- **The benchmarks do not cover negative-operand SRA.** A mutation making SRA
  fill with zeros ESCAPED both of them. Both *do* execute arithmetic right
  shifts — libgcc's `__divsi3` opens with `srai a2,a0,31` to capture the sign —
  but only on non-negative values, where SRA and SRL agree. That coverage comes
  from riscv-tests and riscv-formal, not from here. Recorded rather than papered
  over by inventing a benchmark input to reach it.
- **The string functions are deliberately naive.** `sw/bench/bench_lib.c` is
  byte-at-a-time. Dhrystone's inner loop is three 31-byte `strcpy` calls and one
  `strcmp`, so a word-at-a-time implementation would raise the Dhrystone score
  without touching the core. A newlib build for a machine with no unaligned
  access ends up doing much the same thing, so this is the honest default, but it
  is a choice and it is worth knowing it was made.
- **Dhrystone is gameable and this is the un-gamed configuration**: `-O2`, no
  LTO, upstream's no-inline pragma, `register` not used. CoreMark is quoted
  alongside it for exactly the reason the plan gives — it is much harder to game
  and it validates its own output.
- **Reproducibility is checked as exact equality, not as a tolerance.** With no
  cache, no DRAM and no interrupts, consecutive blocks should be bit-identical,
  and requiring that makes any source of variation something to explain rather
  than average away. Measured spread across nine blocks and three programming
  passes: **0 counts**. Injecting a single cycle into one block of a real
  capture is rejected — `cm_cycles varies across 3 blocks by 1 (0.0012 ppm,
  limit 0.0000)` — which is what says the check is doing work.
