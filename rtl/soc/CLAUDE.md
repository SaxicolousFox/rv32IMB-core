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

**78% route delay at 3.3% utilisation is the more interesting half.** The design
is tiny; it is slow because it is *spread out*, and it is spread out because the
32 BRAMs of the 128 KB array are scattered across the die and the logic follows
them. Anyone trying to push past 73 MHz should look there — a floorplan
constraint or a smaller array — before touching the forwarding muxes, because
2.88 ns of logic is not what is costing the time.

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

Both hardware indicators are **latched**, because a single-cycle pulse at 73 MHz
is invisible and "did it ever happen" is the actual question.

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

## Not done here

No flash image — configuration is volatile, so a power cycle returns the board to
whatever was there before. That is the right default for a bring-up loop.

No Xkntt. The decoder recognises the extension and `dbg_unsupported` (LD0 red)
fires if one ever retires, which on this board it must not.

**A12 does not satisfy M7.** §12 wants "Fmax measured, Dhrystone + CoreMark on
hardware" — that is A12 **and** A13. Fmax is measured; the benchmarks are not run.
