#!/usr/bin/env bash
# Drive the Windows Vivado from WSL to build the SoC bitstream.  Staging copy,
# cmd.exe quoting and -log/-journal ordering as in build_fpga.sh.  The
# WSL<->Windows interop socket is blocked under the agent sandbox.
#
# Usage: build_soc.sh [fail_on_neg] [want_bit] [strategy]
#   SOC_MEM   the $readmemh image baked into the BRAM (default: the hello image)
#   OUT       where the products land (default: fpga/build/soc)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

FAIL_ON_NEG="${1:-1}"
WANT_BIT="${2:-1}"
# Passed through to build_soc.tcl and echoed back in SOC_RESULT.
STRATEGY="${3:-${SOC_STRATEGY:-default}}"

VIVADO_WIN="${VIVADO_WIN:-C:\\AMDDesignTools\\2025.2\\Vivado\\bin\\vivado.bat}"
STAGE_WIN="${STAGE_WIN:-C:\\Users\\liamf\\rv32imb-core-soc}"
STAGE_WSL="${STAGE_WSL:-/mnt/c/Users/liamf/rv32imb-core-soc}"
OUT="${OUT:-$ROOT/fpga/build/soc}"
# $readmemh runs at synthesis time, so the image is part of the bitstream and
# a different program means a different implementation run.
SOC_MEM="${SOC_MEM:-$ROOT/fpga/generated/soc_init.mem}"

echo "=== staging sources to $STAGE_WSL ==="
rm -rf "$STAGE_WSL"
mkdir -p "$STAGE_WSL/rtl" "$STAGE_WSL/constraints"

cp "$ROOT"/rtl/core/*.sv    "$STAGE_WSL/rtl/"
cp "$ROOT"/rtl/common/*.sv  "$STAGE_WSL/rtl/"
cp "$ROOT"/rtl/soc/*.sv     "$STAGE_WSL/rtl/"
# The RVFI port is compiled only under RISCV_FORMAL; the simulation tops and
# the blinky would be extra candidate tops.
rm -f "$STAGE_WSL/rtl/rvntt_rvfi.sv"
rm -f "$STAGE_WSL/rtl/rvntt_soc_sim_top.sv" "$STAGE_WSL/rtl/rvntt_core_sim_top.sv"
rm -f "$STAGE_WSL/rtl/rvntt_blinky_top.sv" "$STAGE_WSL/rtl/rvntt_bram_selftest.sv" \
      "$STAGE_WSL/rtl/rvntt_uart_report.sv"

cp "$ROOT"/fpga/constraints/arty_a7_100t_soc.xdc "$STAGE_WSL/constraints/"
# The ring oscillator's timing exclusion; a stub build matches no cells.
cp "$ROOT"/fpga/constraints/entropy_ring.xdc "$STAGE_WSL/constraints/"
cp "$ROOT"/fpga/scripts/build_soc.tcl            "$STAGE_WSL/"
# $readmemh and `include both resolve relative to Vivado's working directory.
cp "$ROOT"/fpga/generated/soc_clk.svh   "$STAGE_WSL/"
cp "$ROOT"/fpga/generated/soc_clk.svh   "$STAGE_WSL/rtl/"
if [ ! -f "$SOC_MEM" ]; then
  echo "no memory image at $SOC_MEM" >&2
  exit 2
fi
cp "$SOC_MEM" "$STAGE_WSL/soc_init.mem"
echo "image: $SOC_MEM ($(wc -l < "$SOC_MEM") words)"

echo "=== running Vivado (batch) ==="
cd "$STAGE_WSL"
cmd.exe /c "cd /d $STAGE_WIN && $VIVADO_WIN -mode batch -log vivado.log -journal vivado.jou -source build_soc.tcl -tclargs $FAIL_ON_NEG $WANT_BIT $STRATEGY" 2>&1
rc=$?

echo "=== copying products back ==="
mkdir -p "$OUT"
# Delete the previous bitstream first: a run that fails timing writes no .bit,
# and a stale one must not be announced as fresh.
rm -f "$OUT/rvntt_soc_top.bit"
cp -r "$STAGE_WSL"/out/* "$OUT/" 2>/dev/null
cp "$STAGE_WSL"/vivado.log "$OUT/" 2>/dev/null

grep -E "^SOC_RESULT|^SOC_OK|^SOC_FAIL|^SOC_TIMING_FAIL|inferred BRAM" "$OUT/vivado.log" 2>/dev/null

if [ -f "$OUT/rvntt_soc_top.bit" ]; then
  echo "BITSTREAM: $(realpath --relative-to="$ROOT" "$OUT/rvntt_soc_top.bit")"
  ls -la "$OUT/rvntt_soc_top.bit"
else
  echo "NO BITSTREAM PRODUCED (vivado rc=$rc) -- see $OUT/vivado.log" >&2
fi
exit $rc
