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
