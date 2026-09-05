# P0.5 — FPGA bring-up checklist (Arty A7-100T)

The bitstream built by `make bitstream` is a deliberate de-risking exercise: it
exists so that XDC syntax, the MMCM, BRAM inference with `$readmemh`, the
USB-UART pinout and the batch Tcl flow are all shaken out **while there is
nothing else to blame**. When A12 puts a CPU on this board, none of these should
still be suspects.

## What the design does

| Silkscreen | Port | Pin | Meaning |
|---|---|---|---|
| **LD4** | `led[0]` | H5 | 1 Hz, counted in the **raw 100 MHz** oscillator domain |
| **LD5** | `led[1]` | J5 | 1 Hz, counted in the **75 MHz MMCM** domain |
| **LD6** | `led[2]` | T9 | MMCM `LOCKED` |
| **LD7** | `led[3]` | T10 | BRAM self-test passed |
| — | `uart_rxd_out` | D10 | 115200 8N1, one status line per second |

> **The silkscreen numbering is offset, and this is normal.** On the Arty A7,
> **LD0-LD3 are the RGB LEDs** (`led0_r/g/b` …), which this design does not
> drive — expect them to stay dark. The four plain green LEDs are silkscreened
> **LD4-LD7**, and Digilent's schematic calls them `led[4..7]` while the XDC port
> is `led[0..3]`. So `led[0]` lights **LD4**. Nothing is shifted.

Expected UART output, once per second:

```
rvntt P0.5 clk=75MHz bram=0xD76C0E8D PASS
```

## Why two blinking LEDs

`led[0]` and `led[1]` are both nominally 1 Hz, but they are counted in
*different clock domains* — one from the raw oscillator, one from the MMCM
output. Dividing by 100,000,000 and 75,000,000 respectively.

**They must stay visibly in lockstep.** That is the MMCM ratio check. Judging
whether a single LED is blinking at "about 1 Hz" by eye is unreliable; noticing
that two LEDs which started together have drifted apart is easy. If the MMCM
were actually producing 100 MHz instead of 75 MHz, `led[1]` would run 1.33×
fast and the two would be visibly out of phase within a few seconds, and
completely opposed within about 6.

## Before you program the board

1. **Check the build actually met timing.** `fpga/build/post_route_timing.rpt`,
   and the build fails on negative slack by design — a bitstream that misses
   timing usually works on the bench and fails later.
2. **Check the BRAM was inferred**, not turned into fabric. This is Vivado
   *console* output, so it is in `fpga/build/vivado.log`, **not** in the timing
   report:

   ```sh
   grep 'inferred BRAM primitives' fpga/build/vivado.log
   # === inferred BRAM primitives: 1 ===
   ```

   N must be ≥ 1. Cross-check in `post_route_util.rpt`, which should show
   `Block RAM Tile | 0.5`. If it were 0 the self-test would still pass in
   simulation, but A12's 64 KB instruction memory would not fit.
3. **Confirm the part** is `xc7a100tcsg324-1`. An Arty A7-**35**T has a
   different device and this bitstream will refuse to load.

## Programming (do this yourself the first time)

I have deliberately stopped short of programming the board — that is the one
step where eyes on the hardware matter.

1. Plug the Arty into USB. It enumerates as both a JTAG programmer and a
   USB-UART bridge.
2. Open Vivado → **Open Hardware Manager** → **Open Target** → **Auto Connect**.
   You should see `xc7a100t_0`.
3. **Program Device** → select `fpga/build/rvntt_blinky_top.bit` → Program.
   (The staged copy is also at `C:\Users\liamf\rvntt-fpga\out\`.)

## What to check, in order

**1. LEDs.** Immediately after programming:
- `led[2]` (MMCM locked) should be **solid on**. If it is off or flickering, the
  MMCM never locked — stop, nothing else is meaningful.
- `led[3]` (BRAM pass) should be **solid on**. If it is off, the BRAM read back
  the wrong data; the UART line will say `FAIL` and print the actual checksum.
- `led[0]` and `led[1]` should blink together at 1 Hz. Watch for ~30 seconds.
  **Any visible drift between them means the MMCM ratio is not 3:4.**

**2. UART.** **Use a Windows terminal — this is the expected path.** WSL2 does
not see USB serial devices unless they are explicitly attached with `usbipd`, so
`/dev/ttyUSB*` will simply not exist by default. Don't spend time on it.

PuTTY: *Connection type* **Serial**, *Serial line* the Arty's COM port (check
Device Manager → Ports; e.g. **COM7**), *Speed* **115200**, and under
Connection → Serial set *Flow control* to **None**. Then Open.

If you would rather use WSL, attach the device first from an **admin**
PowerShell:

```powershell
usbipd list                      # find the Arty's BUSID
usbipd attach --wsl --busid <BUSID>
```

then in WSL it appears as `/dev/ttyUSB1` (the FT2232 exposes two interfaces;
`/dev/ttyUSB0` is the JTAG channel):

```sh
screen /dev/ttyUSB1 115200        # exit with Ctrl-A then K
```

You should see, once per second:

```
rvntt P0.5 clk=75MHz bram=0xD76C0E8D PASS
```

## Interpreting failures

| Symptom | Most likely cause |
|---|---|
| No LEDs at all | Bitstream not loaded, or wrong device |
| `led[2]` off | MMCM not locking — check `CLK100MHZ` on E3 |
| `led[0]`/`led[1]` drift apart | MMCM ratio wrong; check `CLKFBOUT_MULT_F`/`CLKOUT0_DIVIDE_F` |
| LEDs fine, no UART | TX/RX swapped: `uart_rxd_out` (D10) is what the **FPGA drives** |
| UART garbage | Baud mismatch — the divisor assumes a 75 MHz core clock |
| Lines much faster than 1/sec | Report FSM gap constant wrong (fixed; regression now checks this) |
| `FAIL 0x00000000` | BRAM synthesised but `$readmemh` init did not reach the bitstream |
| `FAIL <other>` | BRAM initialised but the address generator is wrong |

The last two are why the checksum is printed rather than reduced to a single
pass/fail bit: those are completely different bugs and one LED cannot tell them
apart.

## Note on the checksum

`0xD76C0E8D` is generated by `fpga/scripts/gen_bram_init.py`, which writes both
the `.mem` image and the expected value, so the two cannot disagree. The
checksum rotates before accumulating, making it **order-sensitive** — an address
generator that read one address 256 times, or walked backwards, is caught rather
than summing to the same value.

---

## Build results (Vivado 2025.2, xc7a100tcsg324-1)

Recorded from the batch build of `fpga/scripts/build_blinky.tcl`.

**Timing** — `All user specified timing constraints are met.`

| Metric | Value |
|---|---|
| WNS | +6.127 ns |
| WHS | +0.114 ns |

(Rebuild after the UART gap fix; the first build was WNS +6.252 / WHS +0.082.
The design is ~0.3% of the device, so slack moves a little run to run and means
nothing here — it will matter in A12.)

**Clocks** — this is the MMCM ratio confirmed *in the implemented design*,
independently of the LED check:

| Clock | Period | Frequency |
|---|---|---|
| `sys_clk` (E3 oscillator) | 10.000 ns | 100.000 MHz |
| `clk_core_raw` (MMCM CLKOUT0) | 13.333 ns | **75.000 MHz** |

**Utilization** — essentially nothing, as expected; the point is that the flow
works, not that the design is big. It also establishes the baseline the core
will be measured against in A12.

| Resource | Used | Available | % |
|---|---|---|---|
| Slice LUTs | 200 | 63,400 | 0.32 |
| Slice Registers | 209 | 126,800 | 0.16 |
| Block RAM Tile | 0.5 | 135 | 0.37 |
| DSPs | 0 | 240 | 0.00 |
| Bonded IOB | 7 | 210 | 3.33 |
| MMCME2_ADV | 1 | 6 | 16.67 |

`=== inferred BRAM primitives: 1 ===` — the BRAM was inferred as a real block
RAM (half a tile, i.e. one RAMB18), not built out of fabric.

Bitstream: `fpga/build/rvntt_blinky_top.bit` (3,825,917 bytes).

---

# A12 — SoC bring-up (Arty A7-100T)

`rvntt_soc_top` is `rvntt_core` + a 128 KB dual-port BRAM + a memory-mapped UART
and GPIO. **Hardware-confirmed**: the bitstream loads, the program in BRAM runs,
and its output arrives over the USB-UART from the board.

Everything P0.5 proved is reused unchanged — the MMCM, the reset synchroniser,
the UART transmitter, the staging trick, and the seven pin assignments that had
already run. The design notes are in `rtl/soc/CLAUDE.md`; this file is the
**procedure**.

## The short version

```sh
source toolchain/env.sh

python3 fpga/scripts/gen_soc_clk.py --mhz 70.131        # MMCM + baud, one source
python3 fpga/scripts/build_soc_image.py \
        --out fpga/generated/soc_init.mem --delay-cycles 70131000
bash    fpga/scripts/build_soc.sh                        # ~2.5 min, Vivado on Windows
python3 fpga/scripts/hw_bringup.py                       # program, capture, parse
```

Expected last line: `SOC_UART_OK`.

**No part of that needs a person.** Programming runs through Vivado's hardware
manager in batch, and the serial capture through a PowerShell helper whose output
file WSL reads back from `/mnt/c`. The one thing still needing eyes is the LEDs,
below.

## What the board says

Once per second, forever:

```
=== rvntt A12 ===
hello=Hello, world!
sw=0x0 btn=0x0
mcycle=0x0468D04E minstret=0x02A3FC7C
echo=0x5A
img=OK
iter=0x00000001
=== end ===
```

Deliberately shaped to be **parsed, not read** — fixed banner, delimited
key=value lines, fixed terminator. `tb/fpga/parse_soc_uart.py` is the checker.

It **repeats**, which matters more than it looks: programming and capturing need
no ordering with respect to each other, because a capture started at any moment
catches a whole block. And `iter` advancing is what distinguishes a live capture
from a stale file — a single-shot banner cannot support that check.

`echo=` is the receive path: `hw_bringup.py` injects `0x5A` and it comes back.

`img=` is the program checking **that it has not overwritten itself**. MMIO is at
`0x4000_0000` and the RAM at `0x8000_0000`, and `rvntt_ram` *aliases* rather than
faulting — so an MMIO store that was not gated out of the RAM would also land at
word 0, the first instruction of `crt0`. Nothing would notice: `crt0` runs once
and is never revisited, so the program keeps working while its own image rots
underneath it. Re-reading `.text.init` and comparing against the value taken
before the first store is the only check here that can see that.

## Interpreting the LEDs

| | Meaning |
|---|---|
| **LD4–LD7** (green) | `GPIO_OUT`, entirely software — they count in binary, LD4 the LSB, once per block |
| **LD0 green** | ~1 Hz heartbeat in the core clock domain |
| **LD0 blue** | solid once any instruction has retired |
| **LD0 red** | solid if `dbg_unsupported` ever fired — **should never light** |

> **A healthy LD0 looks CYAN, not blue.** Blue is solid and green blinks on top of
> it, and the two mix: the LED alternates between blue and turquoise once a
> second. That is correct. Any red in the mix is not — and note that red plus
> green reads as orange/yellow rather than as "red", which is how the transposed
> pins were first described as "flashing between green and red".

**Hardware-confirmed**, all three: LD0 cyan/turquoise at 1 Hz with no red; LD4–LD7
counting in binary with LD4 as the LSB; and `sw=0xC btn=0x4` read back through
`GPIO_IN` with the switches and a button physically set.

Read it as a ladder, top down:

| Symptom | What it means |
|---|---|
| LD0 green dark | MMCM never locked or the core clock is stopped. **Nothing else on the board means anything.** Check `CLK100MHZ` on E3. |
| LD0 green blinks, blue dark | Clock is fine, CPU is not executing: reset held, empty memory, or wrong `RESET_PC`. |
| LD0 red on | An illegal or Xkntt instruction retired. A9 makes illegal unreachable, so this is a defect in the core, not in the program. |
| LEDs fine, no UART | TX/RX swapped. `uart_rxd_out` (D10) is what the **FPGA drives**. |
| UART garbage | Baud mismatch. The divisor comes from `SOC_CORE_HZ` in the generated header — if you changed the MMCM by hand, this is why. |
| Green LEDs frozen | The program stopped looping; the UART block count will confirm it. |
| `img=BAD 0x…` | Something wrote over the running program. A store aliasing into RAM is the first suspect — check `ram_be` gating in `rvntt_mmio`. |
| `echo=… overrun` | A byte was dropped because the previous one was unread. Constant overruns mean a baud mismatch, not a busy CPU. |

> Silkscreen, again: **LD0–LD3 are the RGB LEDs, LD4–LD7 the plain green ones.**
> `led[0]` is the LED labelled LD4. Nothing is shifted.

## Measured results

Vivado 2025.2, `xc7a100tcsg324-1`, **-1** speed grade, default implementation
strategy.

| | |
|---|---|
| **Fmax** | **70.131 MHz** (period 14.259 ns, WNS +0.170 ns, WHS +0.092 ns) — read it as *about 70 MHz*, see below |
| Fastest constraint that failed | 70.641 MHz (WNS −0.245 ns) |
| Utilisation | 2126 LUTs (3.4%), 913 FFs (0.7%), 32 BRAM tiles (23.7%), 0 DSP, 1 MMCM |
| Critical path | EX/MEM `rd_addr` → forwarding mux → ALU → store byte-enables → BRAM `WEA` |
| | 15 logic levels; 2.880 ns logic, 10.393 ns route (**78% route**) |
| Bitstream | `fpga/build/soc/rvntt_soc_top.bit` (3,825,914 bytes) |

The full search table is in `rtl/soc/CLAUDE.md`. Two things not to lose:

**Do not report `1/(T − WNS)` from a passing run.** At 64.998 MHz this design
closes with +0.752 ns, which extrapolates to ~68 MHz — *below* the real answer.
The router optimises to the constraint and stops; a passing run tells you nothing
about a tighter one, in either direction.

**Re-measure after any design change.** Fixing two transposed LED pins — no
logical content whatsoever — moved Fmax from 73.121 MHz to 70.131 MHz, purely
through placement. Carrying an Fmax number across an edit is quoting a
measurement of a different design.

---

## Manual procedures

These are the fallbacks, and the one step that is genuinely a person's.

### ACTION NEEDED — confirm the LEDs

**WHY:** The UART proves the CPU runs and the memory map works. It does not
prove the four GPIO LEDs are wired to the pins the XDC claims, and it cannot
prove the RGB status ladder is readable — those need eyes. This is the only part
of A12 that cannot be automated.

**BEFORE YOU START:** The Arty is plugged in and already programmed with
`fpga/build/soc/rvntt_soc_top.bit` (70.131 MHz build). If it has been
power-cycled since, re-run `python3 fpga/scripts/hw_bringup.py` first —
configuration is volatile and a power cycle clears it.

**Step 1.** Look at the RGB LED **LD0**, nearest the USB connector.
  - Expect: **green blinking at about 1 Hz**, **blue solid on**, **red off**.
  - If not: green dark means the MMCM did not lock — stop, nothing else is
    meaningful. Red on means `dbg_unsupported` fired, which is a core defect and
    should be reported rather than worked around.

**Step 2.** Look at the four green LEDs **LD4–LD7**.
  - Expect: a **binary count advancing once per second**, LD4 the least
    significant bit — so LD4 toggles every second, LD5 every two, and so on.
  - If not: all four dark means `GPIO_OUT` never reached the pins; frozen at one
    value means the program stopped looping.

**Step 3.** Flip the four slide switches **SW0–SW3** to any pattern and hold a
button down for a second or two.
  - Expect: the next `sw=0xN btn=0xN` line reports what you set. Capture it with:
    `python3 fpga/scripts/hw_bringup.py --no-program --seconds 6`
  - If not: `sw` stuck at `0x0` means either the pins or the two-flop
    synchroniser in `rvntt_soc_top`.

**PASTE BACK:** The output of the Step 3 command, and one sentence on Steps 1
and 2 — or a short video/photo if the blink pattern is hard to describe.

**IF IT GOES WRONG:** `fpga/build/soc/vivado.log` and
`fpga/build/soc/post_route_timing.rpt`, plus which of the three steps failed.

### Programming by hand (if the batch flow is unavailable)

1. Plug the Arty into USB. It enumerates as **both** a JTAG programmer and a
   USB-UART bridge — one FT2232, channel A is JTAG and channel B is the UART,
   sharing a serial number with an `A`/`B` suffix.
2. Vivado → **Open Hardware Manager** → **Open Target** → **Auto Connect**.
   Expect `xc7a100t_0`.
3. **Program Device** → `fpga/build/soc/rvntt_soc_top.bit` → Program.
   (The staged copy is at `C:\Users\liamf\rvntt-soc\out\`.)

### Reading the UART by hand

**Use a Windows terminal.** WSL2 does not see USB serial devices unless they are
explicitly attached with `usbipd`, so `/dev/ttyUSB*` will simply not exist.
Don't spend time on it.

PuTTY: *Connection type* **Serial**, *Serial line* the Arty's COM port (Device
Manager → Ports; **COM7** on this machine), *Speed* **115200**, and under
Connection → Serial set *Flow control* to **None**.

To find the port without Device Manager:

```sh
powershell.exe -NoProfile -Command \
  "Get-CimInstance Win32_PnPEntity | Where-Object { \
     \$_.PNPDeviceID -like 'FTDIBUS*VID_0403+PID_6010*' -and \$_.Name -match 'COM[0-9]+' \
   } | ForEach-Object { \$_.Name }"
```

## Proving the hardware check can fail

A bring-up check that has only ever seen a working board is indistinguishable
from `return 0`. This one was fault-injected on the actual hardware:

```sh
# flip one byte in the MEMORY IMAGE -- not the C source
python3 - <<'PY'
import struct
p='fpga/generated/soc_init.mem'
w=[int(l,16) for l in open(p) if l.strip()]
b=bytearray(b''.join(struct.pack('<I',x) for x in w))
i=b.find(b'world!'); b[i+5]=ord('?')
open(p,'w').write(''.join("%08x\n"%struct.unpack_from('<I',b,k)[0]
                          for k in range(0,len(b),4)))
PY
bash    fpga/scripts/build_soc.sh
python3 fpga/scripts/hw_bringup.py       # must print SOC_UART_FAIL, exit 1
```

Result, from the board:

```
  FINDING: bad hello line: 'hello=Hello, world?'
  FINDING: only 0 complete block(s), need 2
SOC_UART_FAIL
```

Mutating the **image** rather than the source is the point: it proves the checker
can fail *and* that the `$readmemh` contents genuinely reach the bitstream and
drive the output. Restore with a rebuild of `soc_init.mem` and reprogram.

The parser is separately fault-injected against nine wrong captures in
`python3 tb/fpga/parse_soc_uart.py --selftest`, which runs in the regression.

## Traps paid for during A12

**`$readmemh` resolves against Vivado's working directory.** The first
elaboration reported `could not open $readmem data file 'soc_init.mem' …
ignoring` — as a **CRITICAL WARNING**, which a batch run walks straight past on
its way to a bitstream with an empty instruction memory. Both staging scripts now
copy the image into the run directory. Fail on CRITICAL WARNING; this is the
third time in this project that has been the difference between a build and a
correct build.

**Do not write a `create_clock` for the MMCM output.** Vivado derives the
generated clock from the MMCM's own parameters. `gen_soc_clk.py` therefore sets
the frequency by choosing the divider, and `build_soc.tcl` reads back the period
Vivado actually derived and prints it — if intent and implementation disagree,
every Fmax number is wrong by the same unknown factor and nothing else says so.

**One header sets the MMCM ratio and the baud divisor.** They must move together
when the clock changes. Typing them separately is how you measure 95 MHz, decode
the UART at the wrong baud, and blame the cable.

**`-log`/`-journal` must precede `-tclargs`.** Everything after `-tclargs` is
handed to the Tcl script as `argv`. Already documented for `elab_core.sh`; it
applies to the programming script too.

---

# A13 — Dhrystone and CoreMark on the board

The measurement itself, its methodology and every caveat live in
[`docs/a13-benchmarks.md`](a13-benchmarks.md). What follows is only the
*procedure*: what to run, in what order, and what a person still has to do.

## What a person has to do

**Plug the Arty in, and nothing else.** That is the whole list.

A13 changes only the contents of the BRAM — the RTL, the constraints, the pin
assignments and the clock are byte-identical to A12's, and Vivado confirms it:
the same 2126 LUTs, 913 FFs, 32 BRAM tiles and the same **WNS +0.170 ns**. So the
LED check that A12 needed does not apply again; there is no new output pin to get
wrong. If you *do* look at the board, LD0 should be the same cyan as A12 (solid
blue plus a 1 Hz green) and LD4–LD7 will not count, because the benchmark program
does not drive GPIO_OUT.

Everything else runs unattended: the bitstream builds in batch, the board is
configured over JTAG in batch, the UART is captured through a PowerShell helper
whose output WSL reads back from `/mnt/c`, and the capture is parsed.

## The three commands

```sh
source toolchain/env.sh
```

**Step 1 — build the image.** The Dhrystone run count and the CoreMark iteration
count are compile-time constants, so this is where the run length is set.

```sh
python3 fpga/scripts/build_bench_image.py \
    --out fpga/generated/bench_init.mem --elf fpga/generated/bench_image.elf \
    --dhry-runs 200000 --iterations 800 --gap-cycles 2000000
```

Expect: `5439 words (21756 bytes used of 131072)` and a `flags:` line. If the
word count is wildly smaller, the linker dropped something.

**Step 2 — build the bitstream.** About fourteen minutes. Needs the sandbox
disabled (the WSL↔Windows interop socket), and `SOC_MEM` is what points this at
the benchmark image instead of A12's `hello`.

```sh
SOC_MEM=$PWD/fpga/generated/bench_init.mem OUT=$PWD/fpga/build/bench \
STAGE_WIN='C:\Users\liamf\rvntt-bench' STAGE_WSL=/mnt/c/Users/liamf/rvntt-bench \
    bash fpga/scripts/build_soc.sh 1 1
```

Expect: `SOC_RESULT period=14.259 … wns=0.170 …`, then `SOC_OK`, then
`BITSTREAM: fpga/build/bench/rvntt_soc_top.bit`.
If not: `NO BITSTREAM PRODUCED` means timing failed or the front end errored —
read `fpga/build/bench/vivado.log`. **Do not reprogram after that**: the script
deletes the previous `.bit` before building precisely so an absent artifact looks
absent, which it did not the first time this happened during A12.

**Step 3 — program, capture, parse.**

```sh
python3 fpga/scripts/hw_bringup.py \
    --bit fpga/build/bench/rvntt_soc_top.bit \
    --seconds 65 --send-byte -1 --out-name bench_uart.log \
    --parser tb/fpga/parse_bench_uart.py \
    --parser-arg=--min-blocks --parser-arg=3
```

Expect: `DONE = 1`, `PROGRAM_OK`, `CAPTURE_DONE bytes=…`, then the score table
and `BENCH_OK`.
If not: `SOC_HW_SKIP: no Arty enumerated` means the board is unplugged.
`BENCH_FAIL:` names the specific check that failed — it never fails generically.

## Why 65 seconds

One report block is Dhrystone (2.22 s) plus CoreMark (11.87 s) plus a gap, about
**14.2 s**. `--min-blocks 3` is the plan's "reproducible across three runs",
enforced rather than eyeballed, and a capture that starts mid-block needs room
for four: 4 × 14.2 = 57 s.

The block repeats forever, so programming and capturing do not have to be
interleaved — the same property A12 relies on. `iter` increments per block, so a
stale capture file is distinguishable from a live one.

`--send-byte -1` sends nothing. A12's program echoes an injected byte to prove
the receive path; the benchmark program has no such loop, and injecting a byte it
would never read is a false signal, not a spare check.

## From the regression

```sh
RVNTT_HW=1 python3 tb/run_regress.py -k bench
```

`bench_hardware` is opt-in for the same reason `soc_hardware` is: it
**reconfigures the FPGA**, which `make regress` has no business doing behind your
back. Without `RVNTT_HW=1` it SKIPs and prints the command. It also SKIPs, never
fails, if the board is unplugged or no bitstream has been built — a missing board
must not look like a broken design, and must not look like a pass either.

## Proving the hardware check can fail

Same technique as A12: break the *capture*, not the source, and confirm the
parser rejects it.

```sh
# take a real capture, then corrupt one field of it
sed 's/dhry_check=0x00000000/dhry_check=0x00000040/' cap.log > bad.log
python3 tb/fpga/parse_bench_uart.py bad.log
# BENCH_FAIL: block 0: Dhrystone final values wrong (0x00000040):
#             Arr_2_Glob[8][7] != runs + 10
```

The parser's twenty injected faults run in the regression as
`bench_uart_parser`, and two of them break the flags that *relax* a check
(`--allow-short`, `--functional-only`) — an escape hatch that does not actually
open is a second way to pass vacuously.

## Traps paid for during A13

**A 30-run sanity check failed a percentage bound the real run would have passed
vacuously.** The gap between Dhrystone's own timer and the `setStats` window is a
fixed ~27 cycles, so a relative bound is a different test at every run length.
It is now an absolute one. Any check whose threshold scales with the workload is
worth a second look for this.

**`-fwrapv` is not free.** Dhrystone's own rate arithmetic overflows 32-bit
`long`, and the tidy fix costs **1.4%** because it changes code generation inside
the timed loop. Measured, not assumed — and the flag was left out. See
`docs/a13-benchmarks.md`.

**`--parser-arg --min-blocks` does not parse; `--parser-arg=--min-blocks` does.**
argparse reads a value beginning with `-` as the next option.

**Back-to-back programming passes can collide on `hw_server`.** The third of
three consecutive `hw_bringup.py` runs failed with the Vivado log stopping at
`get_hw_devices` — the previous run's `hw_server` was still holding the JTAG
target. It reported `SOC_HW_FAIL: programming failed`, which is the correct
outcome (it did **not** go on to capture stale UART from the still-configured
board and report a pass). Re-running it a few seconds later succeeded. If you
script several passes, leave a gap between them.

**Injecting a fault into the discarded part of a capture proves nothing.** A
first attempt to corrupt a real capture perturbed the first `cm_cycles` in the
file, which sits in the leading PARTIAL block that the parser drops by design —
and the parser "passed", which looked like an escape and was not. A capture
almost always starts mid-block; aim injections at a complete one.
