# A23 — the ISA round on hardware, and what B actually bought

`MODS_A2` A23, closing **M7.3**. Every headline figure below is from the board:
three JTAG programming passes, **all 44 integer counters identical across all
three**. Machine-readable copy in `docs/a23-benchmarks.json`.

| | A19 (RV32IM + predictor) | **A23 (+ B, Zbkb, Zicond, Zihpm)** | |
|---|---|---|---|
| Fmax | 77.501 MHz | **74.577 MHz** | −3.8% |
| DMIPS/MHz | 0.9346 | **0.9361** | +0.2% |
| DMIPS | 72.4290 | **69.8112** | −3.6% |
| Dhrystone IPC | 0.8752 | **0.8734** | −0.2% |
| CoreMark/MHz | 2.8933 | **3.1866** | **+10.1%** |
| CoreMark | 224.2269 it/s | **237.6468 it/s** | **+6.0%** |
| CoreMark IPC | 0.8338 | **0.8168** | −2.0% |
| LUTs / FFs | 3471 / 1530 | **5770 / 2027** | +66% / +33% |
| BRAM / DSP | 32 / 4 | 32 / 4 | — |

Vivado 2025.2, `xc7a100tcsg324-1` (**−1** speed grade), `explore_postroute`,
at 74.576 271 MHz. Fastest constraint that failed: 75.002 MHz.

**Read the two ratios together, not separately.** CoreMark/MHz is up 10.1% while
its IPC is *down* 2.0%, which is `MODS_A2` §3.6's pre-committed **P4** arriving
exactly as written: B replaces instruction sequences with single instructions, so
retired count falls faster than cycles do. Absolute CoreMark rises 6.0% **on a
slower part**. Dhrystone barely moves, which is also expected — its profile is
string and branch work that B has little to offer.

---

## The measurement this round was taken for

Plan §10 M3 asks for the NTT/Keccak split, and B's entire justification is that
`rori` and `andn` accelerate the half a hardware NTT never touches. So the image
carries a **Keccak dual baseline** built the same way A16's NTT pair is: the same
`fips202.c`, compiled twice, measured on one machine in one run, with the 16
exported symbols renamed on the command line and `toolchain/kyber/` untouched.

| SHAKE128 (absorb + 16 squeezeblocks) | cycles | instructions |
|---|---|---|
| `-march=rv32im` | 373,407 | 346,629 |
| `-march=rv32im_zba_zbb_zbs_zbkb` | 312,402 | 290,114 |
| **ratio** | **1.1953×** | **1.1948×** |

**Both builds have M. The only variable is B and Zbkb**, so the ratio is a
property of those extensions and of nothing else. The two builds' digests are
compared byte for byte and the parser refuses a capture where they differ — two
builds of one source are only a comparison if they compute the same thing.

**The compiler's use of B was verified, not assumed.** The `rv32imzb` copy of
`KeccakF1600_StatePermute` contains **100 B instructions**; the `rv32im` copy
contains **0**. That was established by following the call graph from each
variant's `squeezeblocks`, after an attempt to attribute them by address range
got it backwards — link order is not declaration order.

**19.5% is a real result for the dominant half of ML-KEM, and it is smaller than
`rori`/`andn` alone would suggest**, because the reference `fips202.c` is written
in portable C and the compiler is finding these instructions by pattern-matching
rotates, not because anyone asked it to. A hand-written Keccak using the same
extensions would do better; that is not what this measures.

## The NTT baseline that §10 M2 depends on

| reference ML-KEM `ntt()` | cycles | instructions |
|---|---|---|
| `-march=rv32i` | 165,752 | 148,655 |
| `-march=rv32im` | 33,946 | 23,805 |
| **ratio** | **4.883×** | **6.245×** |

Unchanged in instructions from A16 and A19 — the same two builds of the same
source — with cycles moving only because the machine around them changed. All
256 output coefficients identical between builds.

---

## Fmax: −3.8%, and the first search blamed the wrong thing

**A23's first search failed 72 MHz by −0.943 ns**, well below A19's 77.501, and
A21's stop rule says to check whether the new bit-manipulation unit landed on the
critical path. **It had not.** The post-route report named the destination:
`u_core/u_csr/mhpmcounter_q_reg[2][25]/CE`. **The cost was A20's performance
counters**, in exactly the place A20's own write-up said to look.

| what changed | result |
|---|---|
| A20 as built — 6:1 event mux after `ex_redirect` | 72 MHz fails by −0.943 ns |
| + registered one-hot watch mask (`hpm_watch_q`) | **70.998 MHz** |
| + registered event bus (`hpm_event_q`) | **74.577 MHz** |

Both fixes are **provably cycle-neutral** — 32 and 36 counters compared across
both benchmarks, 0 differing — and after the second the critical path's endpoint
moved off the counters entirely, back to `pc_q_reg[29]/D` through `pc_next`,
which is the core's own redirect path and the one `MODS_A2` §3.4 describes.

**The remaining 2.92 MHz is B and Zicond, and it is placement rather than
depth.** LUTs went 3471 → 5770; the bitmanip unit shares `ex_alu_a`/`ex_alu_b`
with the ALU and adds fanout there. **§3.4's A26 levers target this exact path** —
JALR onto A17's address adder is worth ~2.7 ns on it — and pulling them forward
into A23 was deliberately not done: A26's whole discipline is that Fmax work with
no cycle cost gets its own step and its own before/after, and folding it in here
would make this number neither the ISA round's cost nor the Fmax step's benefit.

## The identity on hardware, and a done-when that had to be revised

`MODS_A2` M7.3 originally required A18's identity to close with **residual
exactly zero on hardware**. That is unachievable as written, and the reason is
structural rather than a shortfall: A18's instrument samples every counter at one
instant, and **software cannot**. Each of `mcycle`, `minstret` and the six HPM
counters is read by its own instruction, so the windows are *nested*, and the
reads themselves execute inside some and outside others.

| region | cycles | residual |
|---|---|---|
| Dhrystone | 1,216,000,090 | **−40** (0.03 ppm) |
| CoreMark | 784,528,484 | **−48** (0.06 ppm) |

**Dhrystone's −40 is exactly the snapshot code's own footprint**, measured
independently in simulation as +6 load-use, +0 multi-cycle EX and +17 redirects:
`6 + 0 + 2 × 17 = 40`.

**CoreMark's residual was −168 before a fix, and the difference between the two
regions was the finding.** Their glue nested the reads in *different orders*, and
CoreMark's put both `bench_hpm_read()` calls inside the retired-instruction
window. Aligning the order — HPM outermost, then `minstret`, then `mcycle` —
brought it to −48 and made the residual a property of the read sequence rather
than of which benchmark was being looked at. The revised requirement is that the
residual be **small, of the sign and magnitude the ordering predicts, and
constant rather than proportional** — the same distinction A20 drew for the
software-read overhead. Residual exactly zero remains the standard for A18's
simulation instrument, where it is achievable and still holds.

## The counters, read from the board for the first time

| event | Dhrystone | CoreMark |
|---|---|---|
| retired | 1,062,000,032 | 640,820,044 |
| load-use interlock cycles | 52,000,006 | 51,803,096 |
| multi-cycle EX stall cycles | 66,000,000 | 70,470,000 |
| fetch redirects | 18,000,046 | 10,717,696 |
| of which mispredicts | 18,000,045 | 10,717,695 |
| BTB hits | 171,999,988 | 132,795,149 |
| taken control transfers | 182,000,027 | 92,833,113 |

**`redirects − mispredicts = 1` in both regions.** A19's second closure asserted
that the trap-and-MRET term is negligible; this is the first time it has been
*measured* rather than assumed, and it is one — the single `ECALL` that ends the
program.

## Reproducing it

```sh
source toolchain/env.sh
python3 fpga/scripts/gen_soc_clk.py --mhz 74.577
python3 fpga/scripts/build_bench_image.py --out fpga/generated/bench_a23.mem \
    --elf fpga/generated/bench_a23.elf --core-hz 74576271 \
    --dhry-runs 2000000 --iterations 2500 --arch rv32imb --ntt --hpm --keccak
SOC_MEM=$PWD/fpga/generated/bench_a23.mem OUT=$PWD/fpga/build/bench_a23 \
    bash fpga/scripts/build_soc.sh 1 1 explore_postroute
python3 fpga/scripts/hw_bringup.py --bit fpga/build/bench_a23/rvntt_soc_top.bit \
    --seconds 75 --parser tb/fpga/parse_bench_uart.py
```

`RVNTT_HW=1 python3 tb/run_regress.py -k bench_hardware` does the same through
the regression, against the fixtures this step repointed.

## What is not measured here

**No full ML-KEM profile.** §10 M3 asks for keygen/encaps/decaps split by
function; this measures the *kernel* on both sides of that split — the NTT and
SHAKE128 — which is what M7.3 needs to make the baselines correct. The
application-level Amdahl breakdown is M3's, and it needs the coprocessor to be
worth doing.

**No Fmax attribution between B and Zicond.** They landed in one bitstream. The
three-point cycle attribution in `docs/a22-zicond.md` separates their
*performance* contributions; separating their *timing* contributions would cost
two more searches at roughly an hour each and would change nothing about what
A26 does next.
