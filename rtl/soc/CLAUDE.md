# rtl/soc — the SoC, the board, and what the silicon actually said

Two tops live here and they are not variations on each other:

| File | What it is |
|---|---|
| `rvntt_blinky_top.sv` | P0.5's de-risk design. **Hardware-confirmed, M0.** Do not change it to suit A12 — its whole value is that it is the last thing that worked. |
| `rvntt_soc_top.sv` | A12's SoC: `rvntt_core` + `rvntt_ram` + `rvntt_mmio`. **Hardware-confirmed.** |
| `rvntt_soc_sim_top.sv` | The real top with three simulation constants. Not synthesised. |
| `rvntt_core_sim_top.sv` | A4/A5's core + RAM, no peripherals. Cosimulation's, not the board's. |

`rvntt_clkgen`, `rvntt_uart_tx` and `rvntt_sync_reset` are shared between the
blinky and the SoC and were **already proven on this board**. A12 reused them
rather than rewriting them, and the only change was parameterising the MMCM
ratio — with defaults that reproduce P0.5's 75 MHz exactly, so M0's bitstream is
byte-for-byte unaffected by anything A12 did.

---

## The measured numbers

**Fmax = 70.131 MHz**, Vivado 2025.2, `xc7a100tcsg324-1` (**-1** speed grade),
default implementation strategy (`synth_design` then
`opt`/`place`/`phys_opt`/`route_design`, no directives).

Found by the plan's method and only by it: constrain `T`, implement to routed,
read WNS **post-route**, bisect. Six full implementation runs:

| Constraint | Period | WNS | WHS | |
|---|---|---|---|---|
| 64.998 MHz | 15.385 ns | +0.752 | +0.049 | pass |
| 69.104 MHz | 14.471 ns | +0.002 | +0.047 | pass — *barely* |
| **70.131 MHz** | **14.259 ns** | **+0.170** | **+0.092** | **pass — Fmax** |
| 70.641 MHz | 14.156 ns | −0.245 | +0.068 | fail |
| 71.154 MHz | 14.054 ns | −0.404 | +0.035 | fail |
| 73.196 MHz | 13.662 ns | −0.306 | +0.027 | fail |

**WNS is not monotonic in the constraint, and the table shows it twice.**
69.104 MHz scrapes through at +0.002 ns while the *faster* 70.131 MHz clears by
+0.170; and 73.196 MHz fails by less than 71.154 MHz does. That is placement and
routing landing differently, not measurement error.

It is also exactly why `1/(T − WNS)` from a passing run is not Fmax. At
64.998 MHz this design closes with +0.752 ns, which would extrapolate to about
68 MHz — *below* the answer, because the router stopped there having no reason to
try harder. The extrapolation is not merely optimistic or pessimistic; it is
uninformative in either direction. Only a run **constrained** at `T` is evidence
about `T`.

**These numbers were measured twice, and the first set was wrong.** The original
search reported 73.121 MHz — on a design with the RGB LED's red and blue pins
transposed. Fixing two output pins, a change with no logical content, cost
3 MHz. See "What only a person could catch" below.

Utilisation at Fmax: **2126 LUTs (3.4%), 913 FFs (0.7%), 32 BRAM tiles (23.7%),
0 DSPs, 1 MMCM, 19 IOBs.**

### A16 re-measured it after A14 added M, and it went UP

**Fmax = 73.752 MHz** with the M extension in the core — Vivado 2025.2, same
part, same **-1** speed grade, same default strategy, same `soc_init.mem` image,
six full implementation runs. **A12's 70.131 MHz is preserved above as the RV32I
record and is not superseded**; the two are measurements of two designs.

| Constraint | Period | WNS | WHS | |
|---|---|---|---|---|
| 72.998 MHz | 13.699 ns | +0.111 | +0.071 | pass |
| **73.752 MHz** | **13.559 ns** | **+0.005** | **+0.066** | **pass — Fmax** |
| 74.118 MHz | 13.492 ns | −0.494 | +0.085 | fail |
| 74.488 MHz | 13.425 ns | −0.388 | +0.091 | fail |
| 75.999 MHz | 13.158 ns | −0.494 | +0.072 | fail |
| 79.001 MHz | 12.658 ns | −0.673 | +0.133 | fail |

**Adding a 33×33 multiplier and a 32-cycle divider made the design 5.2%
faster.** That is not a paradox and it is not a mistake — it is the ±0.4 ns
spread in the section below, arriving in the direction nobody double-checks. The
critical path does not go through either new unit, A14 changed placement, and
placement is worth more than the M extension costs. **The lesson is the one A12
already recorded, and this is the harder half of it**: a *favourable* Fmax
movement after a design change is exactly as much a measurement of a different
design as an unfavourable one, and is far less likely to be questioned.

Non-monotonicity shows up again, twice: 74.118 MHz fails by −0.494 while the
*slower* 74.488 MHz fails by only −0.388, and 75.999 MHz fails by the same
−0.494 as 74.118.

Utilisation at Fmax: **2613 LUTs (4.1%), 1148 FFs (0.9%), 32 BRAM tiles,
4 DSP48E1 (1.7%), 1 MMCM, 19 IOBs** — so M cost **+487 LUTs, +235 FFs and
4 DSPs**, and no BRAM. `build_soc.tcl` now counts DSP primitives by `REF_NAME`
and **fails the build** below four: a multiplier that fell back to fabric would
still be correct, would cost about a thousand LUTs and several nanoseconds, and
every symptom would appear as a timing number with no obvious cause.

**The critical path is the same shape it was**, which is what makes A17 still the
right next move. Source `ex_mem_q_reg[rd_addr]`, destination the BRAM — this time
`DIADI` (store data) rather than `WEA` (byte enables), which are the two halves
of the same gated store. 13.168 ns of data path, **logic 2.870 ns (21.8%), route
10.298 ns (78.2%)** — the same 78% as A12, on a different design:

| segment | delay | share |
|---|---|---|
| EX/MEM `rd_addr` → forwarding mux → `ex_alu_a` | 4.49 ns | 34% |
| → CARRY4 ×2 (the ALU adder) → ALU result mux | 3.52 ns | 27% |
| → `ex_trap9_out` (fanout **176**) | 1.64 ns | 12% |
| → store-data mux → BRAM `DIADI` | 2.78 ns | 21% |

The final core-to-BRAM net is 1.424 ns — **11% of the path**, again under a
fifth, exactly as the corrected A12 analysis said. Forwarding mux plus ALU chain
is 61%, and that is what A17 is aimed at.

### A19 took it BACK DOWN to 77.501 MHz, and that is the trade

**Fmax = 77.501 MHz** with the branch predictor — Vivado 2025.2, same part, same
**-1** speed grade, `explore_postroute`, seven implementation runs. WNS +0.003 ns,
WHS +0.030 ns, fastest failing constraint 77.942 MHz. **3471 LUTs, 1530 FFs, 32
BRAM tiles, 4 DSP48E1** — the predictor is about a third of the core's logic.

**This is the core-only Fmax baseline for §9**, superseding A17's number below.
A17's 86.490 MHz is preserved as the measurement of the machine without a
predictor, exactly as A12's and A16's are preserved below it.

| | A16 (RV32IM) | A17 | **A19** |
|---|---|---|---|
| Fmax | 73.752 MHz | 86.490 MHz | **77.501 MHz** |
| period | 13.559 ns | 11.562 ns | **12.903 ns** |
| LUTs / FFs | 2613 / 1148 | 2637 / 1157 | **3471 / 1530** |
| Dhrystone IPC | 0.6860 | 0.6860 | **0.8752** |
| DMIPS (absolute) | 54.03 | 63.35 | **72.43** |

**A 10.4% clock loss bought a 27.6% IPC gain**, so the board is faster in
absolute terms on a slower part. The decision to keep it is recorded in
`docs/a19-benchmarks.md` along with everything needed to reverse it.

**THE FIRST BUILD FAILED 80 MHz BY 3.886 ns**, and the fix was architectural
rather than a lever. Looking the predictor up with `pc_next` puts the ALU in the
fetch path, because `pc_next` contains `ex_redirect_target`:

    ex_mem_q[rd_addr] -> forwarding mux -> ALU (9 x CARRY4) -> ex_jump_target
                      -> ex_redirect_target -> pc_next -> BTB index -> RAMD64E
                      -> tag compare -> pred_taken_q          16.058 ns, 24 levels

`MODS_A` A19 names this as the one real timing risk and prescribes registering
the prediction. **That was done from the start and was nowhere near sufficient** —
registering an output does not remove the ALU from the cone that computes it. The
lookup now reads only *registered* sources and the instruction at a redirect
target goes unpredicted (`SUPPRESS_AFTER_REDIRECT`), which costs 0.33% of
Dhrystone to buy 4 ns.

**THE SEARCH IS NOT MONOTONIC, and this is the number's real uncertainty.**

| constraint | WNS | implied path |
|---|---|---|
| 77.501 MHz | +0.003 | 12.900 ns |
| 77.942 MHz | −0.198 | 13.028 ns |
| 78.376 MHz | −0.612 | 13.371 ns |
| 79.246 MHz | **−1.186** | **13.805 ns** |
| 80.998 MHz | −0.676 | 13.022 ns |
| 87.997 MHz | −1.966 | 13.330 ns |

**79.246 MHz failed by more than the tighter 80.998 MHz did.** One netlist's
implied path delay spans 12.90–13.81 ns — **a 0.9 ns spread, twice the ±0.4 ns
recorded below**. So 77.501 MHz is *the highest constraint observed to pass*, not
a boundary. The A17-to-A19 gap of 8.99 MHz survives that comfortably; a future
±1 MHz claim against this baseline would not, and must not be made from a single
search.

---

### A17 took it to 86.490 MHz, and two of the four levers did nothing

**Fmax = 86.490 MHz**, Vivado 2025.2, same part, same **-1** speed grade, same
`soc_init.mem` image, **strategy `explore_postroute`** — seven implementation
runs for the strategy search on top of eight for the address adder. A12's
70.131 MHz and A16's 73.752 MHz are preserved above as the RV32I and RV32IM
records and are not superseded; all three are measurements of three designs, and
the last one is under a different implementation strategy as well.

| | A12 (RV32I) | A16 (RV32IM) | A17 |
|---|---|---|---|
| Fmax | 70.131 MHz | 73.752 MHz | **86.490 MHz** |
| period | 14.259 ns | 13.559 ns | **11.562 ns** |
| strategy | default | default | **explore_postroute** |
| LUTs / FFs | 2126 / 913 | 2613 / 1148 | 2637 / 1157 |

**Where the 1.997 ns came from, and what did not come:**

| lever | verdict | contribution |
|---|---|---|
| 1 — dedicated address adder | **adopted** | **−1.589 ns**, 26 LUTs |
| 2 — `MAX_FANOUT` on `ex_trap` | rejected | +0.263 ns at the constraint lever 1 met; inside the spread |
| 3 — `explore_postroute` | adopted | **−0.408 ns**, 0 LUTs — *and that is exactly the spread* |
| 4 — 64 KB instead of 128 KB | see `docs/a17-fmax.md` | |

Two things are worth carrying forward from that table. **Lever 3's entire
contribution is the size of the ±0.4 ns spread described below**, so 86.490 MHz
should be read as 83.5–86.5 MHz with the strategy as a plausible but unproven
cause. And **lever 2 was aimed by the post-route report and still did nothing**:
`ex_trap` drives 141 loads and sits on the path, and replicating its driver
bought no measurable time — a high-fanout net is only worth what its own route
costs, which here is a nanosecond in eleven.

**The critical path's SOURCE moved.** A12's and A16's both began at
`ex_mem_q_reg[rd_addr]` — the forwarding mux. A17's begins at
`id_ex_q_reg[insn][20]`, and the forwarding mux is not on it at all:

| segment | delay | share |
|---|---|---|
| `id_ex_q.insn[20]` → decode → `uses_rs2` → operand select | 3.90 ns | 36% |
| → CARRY4 (the ALU adder) | 1.81 ns | 17% |
| → 4 × LUT6 → `ex_trap9_out` (fanout 141) | 2.92 ns | 27% |
| → byte enables → BRAM `WEA` | 2.24 ns | 21% |

Lever 1 did not make the forwarding mux faster. It made the tail behind the ALU
short enough that **decode and operand selection became the longest thing in the
design**, which is where the next lever is — and that one changes
microarchitecture rather than only timing, so it needs its own before/after.

### How much to trust the last digit: about ±0.4 ns

Transposing two **output pins** — a change with no logical content at all, and
one that cannot affect a single internal path — moved WNS at 73.121 MHz from
**+0.218 ns to −0.153 ns**, and moved Fmax from 73.121 MHz to 70.131 MHz. Same
RTL, same constraint, same strategy; placement simply landed differently.

So quote this as **about 70 MHz**, not to three decimals: the design-to-design
spread is around ±0.4 ns, or ±2 MHz. Vivado is deterministic for identical
input, so this never shows up as noise in a repeated build — it appears the
moment anything perturbs placement, which is what every real edit does. **Anyone
carrying an Fmax number forward across a design change is quoting a measurement
of a different design.** Re-run the search.

One distinction worth keeping straight: that spread is *design to design*, not
margin within a given bitstream. A routed design that closes at +0.170 ns closes
— static timing at the slow corner already derates for voltage and temperature,
and re-running the same build cannot move it. So shipping *at* Fmax is fine; it
is the number, not the bitstream, that needs the error bar.

### The critical path, named

```
u_core/ex_mem_q_reg[rd_addr]  →  forwarding mux  →  ex_alu_b
                              →  ALU  →  store byte-enable decode
                              →  u_ram/mem_reg_3_0_3/WEA[0]

15 logic levels (9×LUT6, 3×LUT5, LUT4, LUT3, CARRY4)
data path 13.273 ns:  logic 2.880 ns (22%),  route 10.393 ns (78%)
```

This is the plan's predicted forwarding-mux path, and it is a **direct
consequence of a decision `rvntt_ram.sv` documents**: port B's address comes
from the *combinational* ALU result in EX, not from the EX/MEM register, because
registering it would push load data into WB and add a second load-use bubble.
The store byte enables depend on that same address for sub-word alignment, so
"forwarding → ALU → alignment → WEA" is one combinational path by construction.
The timing cost of that choice is now a number rather than an opinion.

**78% route delay at 3.3% utilisation is the more interesting half — but the
first reading of it was wrong, and the correction matters.** This file used to
say the design is slow *because* it is spread across the 32 BRAMs of the 128 KB
array, and that anyone pushing past 73 MHz should start with a floorplan
constraint or a smaller array. Reading the post-route path hop by hop
(`fpga/build/*/post_route_critical.rpt`) says otherwise. Of the 13.373 ns:

| segment | delay | share |
|---|---|---|
| EX/MEM `rd_addr` → forwarding mux → `ex_alu_b` | 4.56 ns | 34% |
| `ex_alu_b` → CARRY4 → **ALU result mux, 4× LUT6** → `ex_addr_misaligned` | 4.44 ns | 33% |
| misalign → `ex_trap7_out` (**fanout 245**) → `dmem_be` | 2.14 ns | 16% |
| `dmem_be` → `ram_be` → BRAM `WEA` | 1.32 ns | 10% |

**The core-to-BRAM crossing is under a fifth of the path.** Roughly half is a
logical dependency chain, and the route dominance is substantially a
*consequence* of that chain being long enough that the placer cannot keep it
local — not an independent cause. The BRAM spread is real and contributes, but
it is not the lever it was described as.

The lever is `ex_addr_misaligned`, which is `|ex_alu_y[1:0]` — bits read off the
**muxed** ALU output, so the entire four-level operation-select mux sits in front
of the alignment check, which then gates the trap, which then gates the byte
enables. A memory address is always `rs1 + imm`; it is never a shift or an AND.
A dedicated address adder feeding `dmem_addr` and the misalign check would
bypass that mux entirely. That, plus `MAX_FANOUT` on the 245-load trap net, is
where to start — not the forwarding muxes, and not the array size.

(Note also that the two LUT counts in this project are both correct and count
different things: `build_soc.tcl` counts LUT **primitives** — 2126 — while
Vivado's utilisation report counts **Slice LUTs after LUT combining** — 1893.)

---

## The memory map

One **128 KB true dual-port** array at `0x8000_0000`, port A instruction fetch,
port B load/store. Plan A12 asks for a 64 KB instruction BRAM and a separate
64 KB data BRAM; plan §1.4 asks for exactly what is built here, and §1.4 wins,
because one array means **one image** — the ELF that runs on Spike, in the A5
cosimulation and in the bitstream is the same bytes.

MMIO at `0x4000_0000`, decoded on the top nibble. **Word access only.**

| Offset | Name | R | W |
|---|---|---|---|
| `0x00` | `UART_TX` | — | `[7:0]` byte to send |
| `0x04` | `UART_STAT` | `{rx_overrun, rx_valid, tx_ready}` | write bit 2 clears `rx_overrun` |
| `0x08` | `UART_RX` | `[7:0]` last byte, **non-destructive** | any value consumes it |
| `0x0C` | `GPIO_OUT` | readback | `[3:0]` → `led[3:0]` |
| `0x10` | `GPIO_IN` | `{btn[3:0], sw[3:0]}` | — |

### Reads here have no side effects, and that is structural

`dmem_addr` is the **raw combinational ALU result of whatever is in EX**, driven
every cycle for every instruction. The core signals a read as `be == 0`, which is
also what a non-memory instruction presents — so an `add` whose result happens to
equal `0x40000008` is indistinguishable from a load of `UART_RX`.

A read-to-pop FIFO would therefore be emptied by *arithmetic*, intermittently,
and the symptom would be dropped UART bytes with no pattern. Every consuming
action here is an explicit **write** instead. Writes are safe to qualify because
`be != 0` only for a real store, and A9's trap invariant guarantees a faulting
store is suppressed in EX before its byte enables ever arrive.

The alternative — adding a `dmem_re` output to `rvntt_core` — was rejected: it
changes a port list that four testbenches, the RVFI wrapper and the mutation
harness all wire up, to buy a convenience the software does not need.

### The timing contract is fixed, not negotiated

There is **no handshake**. `rvntt_ram`'s output register *is* the pipeline
register, so read data must appear exactly one cycle after the address. The MMIO
read mux is therefore one register deep, and `sel_ram_q` is registered
**alongside** the data — selecting with the live address would return RAM data
for an MMIO read issued a cycle later.

`ram_be` is `dmem_be` gated to the RAM region. This is not tidiness: the array
*aliases* rather than faults, so an ungated MMIO store would also land in RAM at
the truncated offset and silently corrupt the program.

---

## LEDs — software's and the hardware's

| | Driven by | Meaning |
|---|---|---|
| LD4–LD7 `led[3:0]` | `GPIO_OUT` | entirely software |
| LD0 **green** | hardware | ~1 Hz heartbeat, core clock domain |
| LD0 **blue** | hardware | latched: an instruction has retired |
| LD0 **red** | hardware | latched: `dbg_unsupported` fired |

The RGB LED is the hardware's precisely so that a **dead program still leaves a
diagnosis on the board**, and it reads as a ladder: green dark → the MMCM never
locked or the core clock is stopped, and nothing else means anything. Green
blinking but blue dark → clock fine, CPU not executing (reset, empty memory, bad
`RESET_PC`). Red → an illegal or Xkntt instruction retired, which A9 makes
unreachable, so red is a defect in the core rather than a bug in the program.

Both hardware indicators are **latched**, because a single-cycle pulse at 70 MHz
is invisible and "did it ever happen" is the actual question.

**Hardware-confirmed.** A healthy LD0 reads **cyan**: blue solid with green
blinking on top of it, mixing to turquoise once a second. Worth knowing before
you look, because colour mixing is also what disguised the pin transposition —
red plus green reads as orange, and the board was first described as "flashing
between green and red". LD4–LD7 count in binary with LD4 as the LSB, and
`sw=0xC btn=0x4` was read back through `GPIO_IN` with the switches physically
set, so both directions of the GPIO are confirmed on silicon.

---

## The MMCM is the timing constraint

With an MMCM-generated clock you do **not** write a `create_clock` for the core
domain — Vivado derives the generated clock from the MMCM's own parameters, and a
competing `create_clock` would be ignored or would silently replace the real
relationship. So `fpga/scripts/gen_soc_clk.py` sets the frequency by *choosing
the MMCM divider*, and `build_soc.tcl` reads the period Vivado actually derived
and prints it. If intent and implementation ever disagree, every Fmax number is
wrong by the same unknown factor and nothing else would say so.

That script also emits `SOC_CORE_HZ`, which is what `rvntt_uart_tx` divides to
get the baud rate. **Three things have to move together** when the clock changes
— the MMCM divider, the baud divisor, and what the timing report is compared
against — and one generated header writes all three. Typing them separately is
how you measure 95 MHz and then decode the UART at the wrong baud and blame the
cable.

Grid: `CLKFBOUT_MULT_F` and `CLKOUT0_DIVIDE_F` move in 0.125 steps, so reachable
frequencies near 73 MHz are about 1 MHz apart. **The search resolution is bounded
by the hardware, not by patience.**

---

## Every input is asynchronous, and the XDC says so honestly

There is no source-synchronous interface anywhere on this board: two mechanical
contacts (`ck_rst`, `btn`), four slide switches, and a serial line clocked by the
host. `set_input_delay` has no meaningful number to carry for any of them, so
`arty_a7_100t_soc.xdc` cuts them with `set_false_path` — **which is only correct
because each one has a synchroniser behind it**. `rvntt_uart_rx` has a two-flop
synchroniser on `rx`; `rvntt_soc_top` has one on `sw`/`btn`; `ck_rst` goes
through `rvntt_sync_reset`.

False-pathing an *unsynchronised* input is the actual bug, and the constraint
file looks identical either way. That is why it is written down here.

**No `set_clock_groups`, deliberately** — a real difference from the P0.5
constraint set. That design had counters in both the oscillator and MMCM domains
and needed them declared asynchronous. This one has **no logic in the `sys_clk`
domain at all**: the oscillator drives the MMCM and nothing else. Declaring a
group anyway could only hide a crossing that appears later.

---

## What only a person could catch

**The RGB LED's red and blue were on each other's pins.** The Digilent master
XDC lists them in the order `led0_b`, `led0_g`, `led0_r`; they were read as
r, g, b; E1 and G6 were transposed.

Nothing upstream of the pad can see this. It linted, elaborated, synthesised,
met timing, programmed, ran, and produced byte-perfect UART — a swapped **output**
pin changes nothing any automated check in this repository looks at. The whole
symptom was that the board lit the wrong colour: green blinking with red solid,
where red was `alive_q` wearing blue's wiring, and the *actual* error indicator
was dark. Reported by someone looking at the board.

The lesson is not "be careful reading pin tables". It is **stop reading pin
tables by hand**: `fpga/constraints/arty_a7_100t_pins.txt` is extracted
mechanically from the vendor file, and `tb/fpga/check_xdc_pins.py` compares every
assignment in every XDC against it, with five injected faults of its own
(including this exact transposition). It found both pins independently.

What that checker still **cannot** check is whether the right *signal* drives a
correctly-named port: `led0_r` wired to the heartbeat would pass. Pin identity is
mechanisable; intent is not, and that is the part the LED check on the bench is
for. Do not skip it.

**And the fix cost more than the fix.** Swapping two output pins moved WNS from
**+0.218 ns to −0.153 ns** at the same 73.121 MHz constraint — a 0.371 ns swing
from a change with no logical content whatsoever, purely because placement moved.
That is the honest error bar on every Fmax number here; see below.

## What fault injection caught here

**A build that failed timing, reported as a successful one.** `build_soc.sh`
announced `BITSTREAM: …` by testing whether the file existed in the output
directory — but a run that misses timing writes no `.bit`, so the *previous*
run's bitstream was still sitting there and got announced, and `hw_bringup.py`
duly reprogrammed the board with the old design. Two independent guards now: the
output bitstream is deleted before the build so an absent artifact looks absent,
and `hw_bringup.py` warns when the `.bit` is older than the newest source file.
"I rebuilt and reprogrammed" silently becoming "I reprogrammed the previous
design" is close to invisible in the output, and it invalidates whatever the
board then says.

**A `$readmemh` file that was not there, reported as a CRITICAL WARNING.** The
first Vivado elaboration of `rvntt_soc_top` said `could not open $readmem data
file 'soc_init.mem' … ignoring` and would have carried straight on to a bitstream
with an **empty instruction memory**. `$readmemh` resolves against Vivado's
*working* directory, not the source file's; the staging scripts now copy the
image. This is the third time in this project that Vivado's CRITICAL-WARNING-and-
continue behaviour has produced something that builds and is wrong.

**An overrun flag that could latch once and never re-arm.** The first version of
`rvntt_mmio` kept `rx_overrun`'s sticky bit in the receiver and its clear in the
bus block, with a comment claiming it re-armed. It could not: the receiver's flag
had no clear input, so one acknowledgement disabled it permanently. The clear now
lives in `rvntt_uart_rx` next to the set, where set beats clear in the same cycle.
Caught by reading the code against its own comment, which is worth doing.

**A simulation memory smaller than the hardware's.** `rvntt_soc_sim_top`
originally used `RAM_WORDS = 4096` to keep the zeroing loop short. The linker puts
`_stack_top` at the top of the 128 KB array, and `rvntt_ram` drops the high
address bits rather than faulting — so the stack aliased straight onto the
program. Simulating a *smaller* memory than the board has is precisely how you
get a testbench that passes over a bug the hardware would hit. `RAM_WORDS` is now
identical in both.

**Two mutations that ESCAPED, and both were the STIMULUS.** The A12 entries in
`tb/mutate/run_mutation.py` are declared to be caught by `soc`, and two of them
were not, at first:

* `mmio_store_not_gated_from_ram` — an ungated MMIO store aliases onto **word 0**,
  the first instruction of `crt0`. `crt0` runs once and is never revisited, so
  the program carries on working while its own image rots. Nothing in the
  testbench could see it. Fixed in the *program*, not the checker: `hello.c` now
  re-reads `.text.init` and compares it against the value taken before the first
  store, and prints `img=OK` / `img=BAD`.
* `uart_rx_samples_on_bit_edge` — with a host whose edges are perfect, a receiver
  that samples on the bit **boundary** decodes exactly as well as one that samples
  at the **midpoint**, so mid-bit sampling was untestable and the mutation was
  invisible. Fixed in the *stimulus*: the simulation baud divisor went from 8 to
  34 cycles (`CORE_HZ` 1 MHz → 4 MHz) and `tb_soc.cpp` now transmits **~2.9%
  slow** — which is what `rvntt_uart_rx` claims to tolerate, and what gives the
  midpoint something to be right about.

Two others had to be **reformulated because they did not build**: replacing
`sel_ram_q` with `is_ram` in the read mux, and zeroing the synchroniser output,
each left a signal unreferenced and Verilator refused. A mutation that fails to
compile proves nothing; every signal has to stay live. All four are caught now.

**The whole hardware loop, proven able to report failure.** See
`docs/fpga-bringup.md`: the `world!` string was mutated to `world?` **in the
memory image**, not in the C source, and the board was reprogrammed. The parser
reported `SOC_UART_FAIL`. Mutating the image rather than the source proves two
things at once — that the checker can fail, and that the `.mem` contents genuinely
reach the bitstream and drive the output.

---

## The hardware loop needs no human

`fpga/scripts/hw_bringup.py` does program → capture → parse unattended:

- **JTAG** through `vivado.bat -mode batch`, which also reads back
  `REGISTER.IR.BIT5_DONE` — `program_hw_devices` succeeding is *not* the same as
  the device being configured.
- **Serial** through `fpga/scripts/serial_capture.ps1`, which writes to `C:\` so
  the WSL side can read it from `/mnt/c`. WSL cannot see the FT2232 without
  `usbipd`; do not spend time on that route.
- **The Arty is identified by `FTDIBUS\VID_0403+PID_6010`**, not by "a COM port
  exists" — the FT2232 exposes channel A as the JTAG programmer and channel B as
  the UART, sharing a serial number with an A/B suffix.

The program on the board **repeats its report block forever**, which is what makes
this ordering-free: programming and capturing do not have to be interleaved, and
`iter` advancing distinguishes a live capture from a stale file.

What still needs a person: looking at the LEDs, and power-cycling.

`soc_hardware` in the regression is **opt-in** (`RVNTT_HW=1`), because running it
reconfigures the FPGA and `make regress` has no business doing that behind your
back. It SKIPs loudly otherwise — and `tb/run_regress.py` now reports a test that
prints an uppercase `*_SKIP:` line as SKIP rather than PASS, which it previously
did not, so a missing board or checkout no longer shows up as a green row.

---

## A13 — the benchmarks, on this same SoC

Full methodology and every caveat: [`docs/a13-benchmarks.md`](../../docs/a13-benchmarks.md).
The short version, measured on the board:

| | |
|---|---|
| **DMIPS/MHz** | **0.7306** |
| **CoreMark/MHz** | **0.9607** |
| IPC | 0.7227 Dhrystone, 0.6930 CoreMark |
| at | 70.129 870 MHz |

Two things about this SoC that A13 confirmed and that are worth knowing here:

**Changing only the BRAM contents changes nothing about timing.** The benchmark
bitstream is the same RTL, the same constraints and the same clock as A12's, with
a different `$readmemh` image — and Vivado returns the identical 2126 LUTs,
913 FFs, 32 BRAM tiles and **WNS +0.170 ns**. So a new program costs a fourteen-
minute implementation run but no re-measurement of Fmax, and no LED check: there
is no new output pin to get wrong. Compare that with A12's finding that
correcting two output *pins* — no logical content at all — cost 3 MHz. Contents
are free; pinout is not.

**The determinism is total.** Nine report blocks across three separate JTAG
programming passes are identical to the cycle. No cache, no DRAM refresh, no
interrupt source and no second bus master means there is nothing to vary, and the
parser requires exact equality rather than a tolerance so that any future
variation has to be explained rather than averaged away.

**`mcycle` finally has a reference.** `rtl/core/rvntt_csr.sv` says it is a real
cycle counter, that Spike therefore disagrees with it, and that *nothing compares
it*. Verilator can, because it counted the clock edges itself: the timed regions
plus the computable UART transmit time account for 96.4% of the simulated run.
The mutation `mcycle_counts_retires_not_cycles` — Spike's behaviour, and
self-consistent enough that IPC comes out at a plausible 1.00 — is caught by that
and by nothing else.

## Not done here

No flash image — configuration is volatile, so a power cycle returns the board to
whatever was there before. That is the right default for a bring-up loop.

No Xkntt. The decoder recognises the extension and `dbg_unsupported` (LD0 red)
fires if one ever retires, which on this board it must not.

**No branch predictor**, and A13 says what that is worth measuring: Dhrystone
retires 112 600 032 instructions in 155 800 003 cycles, so 0.384 cycles per
instruction go to stalls and flushes. **Nothing here attributes that split** —
the core has no branch or stall counters — and adding them is the first step of
the plan's optional 2-bit-bimodal-plus-64-entry-BTB loop, not of A13.

**M7 is met by A12 and A13 together.** §12 wants "Fmax measured, Dhrystone +
CoreMark on hardware": A12 measured Fmax, A13 ran the benchmarks. Neither alone
does it.
