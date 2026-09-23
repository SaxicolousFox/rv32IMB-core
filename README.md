# rv32IMB-core

A small in-order RISC-V core for FPGA, with B bit-manipulation extensions,
conditional operations, hardware performance counters, and an entropy source,
as well as the accompanying SoC, software and verification flow.

ISA string: `rv32im_zba_zbb_zbs_zbkb_zicond_zkr_zkt_zicsr_zicntr`
(riscv-config spelling `RV32IMZicsr_Zicond_Zba_Zbb_Zbkb_Zbs_Zkr_Zkt`).
Machine mode only. Target: Digilent Arty A7-100T (`xc7a100tcsg324-1`, speed
grade -1), Vivado 2025.2.

## Measurements

| | |
|---|---|
| Core clock (SoC, `explore_postroute`) | 96.246 MHz (fastest constraint observed to pass; ~1.4 MHz search grid) |
| Dhrystone | 0.9361 DMIPS/MHz, IPC 0.8734 |
| CoreMark | 3.3896 CoreMark/MHz, IPC 0.8689 |
| Resources (SoC, 128 KB RAM) | ~5.5 k LUTs, ~2.0 k FFs, 32 BRAM tiles, 4 DSP48E1 |
| riscv-tests | 54/54 (rv32ui, rv32um, rv32mi) |
| RISCOF riscv-arch-test | 120/120 (I, M, Zicond, B minus 3 Zbc tests, Zbkb, hints, privilege) |
| riscv-formal | 77/77 checks at BMC depth 14 (liveness 46) |

The benchmark ratios are ratios of `mcycle` counts and do not depend on the
clock. Both benchmarks were measured on the board over three programming
passes.

## The core (`rtl/core`)

Five-stage pipeline (IF, ID, EX, MEM, WB), one instruction per cycle when there
is no hazard.

- **Forwarding** from MEM and WB into EX, with select precomputed in ID.
  The register file reads through on a same-cycle write. A load followed by a
  dependent instruction costs one interlock cycle.
- **Branches and jumps** resolve in EX. A redirect costs two cycles. The
  predictor is a 256-entry direct-mapped BTB with a two-bit counter per entry
  and an 8-deep return-address stack, looked up one address ahead of fetch;
  the lookup holds under a front-end stall.
- **Multiply/divide** (`rvntt_muldiv`): a 33x33 multiplier on DSP48E1s at
  2 cycles of EX occupancy, and a radix-2 restoring divider at 34 cycles.
  Both latencies are data-independent. The multi-cycle handshake stalls EX
  and bubbles EX/MEM; the forwarding select decays MEM -> WB -> register file
  while the consumer waits.
- **Bit manipulation** (`rvntt_bitmanip`): Zba, Zbb, Zbs, Zbkb and Zicond in
  a separate result lane that joins at the EX result mux, so the ALU is
  untouched.
- **Traps** resolve in EX and squash nothing older; the faulting instruction
  never reaches MEM. Causes: instruction address misaligned (0), illegal
  instruction (2), breakpoint (3), load/store address misaligned (4/6),
  environment call (11). `mtvec` is direct mode. No interrupts are
  implemented; `mie`/`mip` exist and read as written/zero.
- **CSRs**: `mstatus`, `misa` (`0x40001100`), `mie`, `mtvec`, `mscratch`,
  `mepc`, `mcause`, `mtval`, `mip`, `mcycle[h]`, `minstret[h]`, the
  `cycle`/`instret` user shadows, `mcountinhibit`, `mvendorid`/`marchid`/
  `mimpid`/`mhartid`, and `seed`. `minstret` counts each instruction once, in
  EX, whatever its occupancy.
- **Zihpm**: six counters `mhpmcounter3..8` with `mhpmevent3..8` selecting
  from: load-use interlock cycles (1), multi-cycle EX stall cycles (2),
  fetch redirects (3), mispredicts (4), BTB hits (5), taken transfers (6).
  Counters 9..31 are decoded, read zero, and do not trap.
- **Zkr**: the `seed` CSR (0x015) is read-write-only and returns BIST, WAIT,
  ES16 or DEAD. The noise source is a set of ring oscillators of coprime
  length sampled into a synchroniser; the SP 800-90B repetition-count
  (cutoff 21) and adaptive-proportion (window 1024, cutoff 589, both values
  counted) health tests gate it, and a failure latches DEAD. The sampler is
  gated on needing to refill, so the per-sample false-positive rate of 2^-20
  is spent only on reads. The source is **uncertified**: no SP 800-90B
  entropy-rate estimation or IID/non-IID test has been run, there is no
  conditioning, and H = 1 bit/sample is an assumption. Every simulation and
  formal build uses a deterministic stub in its place (`ENTROPY_STUB`).
- **Zkt**: every listed instruction this core implements has an EX occupancy
  that only depends on its opcode. This was proved in two ways: an assertion that
  only the multi-cycle unit can stall EX (checked by every riscv-formal run),
  and a cone-of-influence check that the multiplier's `done` does not depend
  on its operands (`tb/formal/run_zkt.py`; use `--list` to see the extent of what's claimed). This says nothing about control flow, and the branch predictor's BTB, counters, and return stack are a timing state that persists across
  whatever runs on the machine, with no flush.

Boundaries: `misa.B` and `misa.K` are deliberately not set (Zkn/Zks not included). No C, Zifencei,
Zbc, Zbkx, PMP, interrupts, or user/supervisor modes are included. Misaligned accesses
trap. The memory interface is fixed-latency (address in EX, data one cycle
later), with no stall path.

Module list: `rv32i_pkg` (encodings, control bundle, latencies), `rvntt_core`
(pipeline), `rvntt_decode`, `rvntt_immgen`, `rvntt_regfile`, `rvntt_alu`,
`rvntt_bitmanip`, `rvntt_muldiv`, `rvntt_branch`, `rvntt_bpred`,
`rvntt_forward`, `rvntt_hazard`, `rvntt_csr`, `rvntt_seed`, `rvntt_entropy`,
`rvntt_entropy_health`, `rvntt_rvfi` (RVFI port, compiled only under
`RISCV_FORMAL`); `rtl/common/rvntt_sync_reset`.

## The SoC (`rtl/soc`)

`rvntt_soc_top` = core + 128 KB true-dual-port BRAM (instruction port and
data port) + a memory-mapped UART and GPIO, clocked by an MMCM from the
board's 100 MHz oscillator. The program is a `$readmemh` image baked at
synthesis; the same ELF runs on Spike, under Verilator, and on the board.

| Address | |
|---|---|
| `0x8000_0000` | RAM, 128 KB, aliases above the array; reset vector |
| `0x4000_0000` | `UART_TX` (W), `+4` `UART_STAT` (tx_ready, rx_valid, rx_overrun), `+8` `UART_RX`, `+C` `GPIO_OUT` -> LD4-7, `+10` `GPIO_IN` (btn, sw) |

UART: 115200 8N1 over the USB bridge. RGB LD0: green flashing heartbeat, blue once an
instruction has retired, red if an illegal instruction ever retired.

`rvntt_core_sim_top` and `rvntt_soc_sim_top` are the simulation tops
(deterministic entropy stub, short delays). `rvntt_blinky_top` is a
board-bring-up design (for MMCM, reset, BRAM inference, UART) with no core.

## Software (`sw`)

- `sw/soc/hello.c`: the SoC's bring-up program; prints a parseable block
  (`=== rvntt soc ===` ... `=== end ===`) forever.
- `sw/bench`: Dhrystone and CoreMark, compiled in place from the pristine
  `toolchain/riscv-tests` and `toolchain/coremark` checkouts with the ports
  in `sw/bench/include` and `sw/bench/coremark_port`. Only raw counter deltas
  are printed; every rate is computed by `tb/fpga/parse_bench_uart.py`.
  `--hpm` arms the six performance counters.
- `sw/tests/*.S`: directed programs for forwarding, the load-use interlock,
  control hazards, CSRs and traps, `minstret`, M, the predictor and Zihpm.

## Verification (`tb`)

`python3 tb/run_regress.py` runs everything (`--list` names the tests,
`-k PATTERN` selects). Missing tools report SKIP. Tiers:

| | |
|---|---|
| `RVNTT_FAST=1` | edit loop, ~4 min: doesn't include the mutation set, riscv-formal, or divider proof |
| `RVNTT_NO_MUTATE=1` | ~13 min: drops the mutation set only |
| (default) | ~25 min |
| `RVNTT_HW=1` | additionally programs the board and parses its UART |

Included in the full verification pass:

- **Lint**: Verilator `-Wall`
- **cocotb**: ALU, immediate generator and decoder against the Python model
  in `model/rv32i_ref.py` (written from the ISA specification), including
  10^6 random words through the decoder
- **Formal** (SymbiYosys, bitwuzla): proofs for the register file,
  ALU, immediates, decoder, forwarding, hazard unit, branch unit, CSRs,
  predictor, divider (depth 37), entropy health tests, and `seed`; the Zkt
  cone check; riscv-formal over the core (`tb/formal/run_riscv_formal.py`)
- **Cosimulation** against Spike: random programs with controllable hazard
  densities and directed programs (`tb/cosim/gen_random_prog.py`), as well as an independent cycle model to predict each program's cycle
  span so that a phantom stall or a predictor bug is caught. (`tb/cosim/cycle_model.py`, `model/bpred.py`) 
- **riscv-tests** and **RISCOF** (riscv-arch-test `old-framework-3.x`,
  riscof 1.25.3, Spike as reference).
- **SoC and benchmark simulation**: the hello image and a short benchmark
  image on `rvntt_soc_sim_top`, decoded off of the UART pins.
- **Stall profiler** (`tb/perf`): attributes every cycle of each timed
  region and cross-checks the hardware counters and the
  predictor model against it.
- **Mutation testing** (`tb/mutate/run_mutation.py`): 89 RTL mutations; both an escape and a catcher
  that does not fire are failures. `--check-anchors` is a 0.1 s pre-flight.
- `harness_detects_failure` is a permanent, deliberate XFAIL to verify failure catching

## Toolchain Building and Testing

```sh
sudo bash toolchain/00-apt-deps.sh     # once
bash toolchain/10-spike.sh             # builds Spike into toolchain/install
bash toolchain/20-python-env.sh        # .venv with cocotb, riscof, riscv-config
source toolchain/env.sh                # before anything else, every shell
make tools                             # prints what was found
```

```sh
make soc-bitstream      # hello image
make bench-bitstream    # benchmarks
make bitstream          # the blinky test
RVNTT_HW=1 python3 tb/run_regress.py -k fpga   # full regression
```
