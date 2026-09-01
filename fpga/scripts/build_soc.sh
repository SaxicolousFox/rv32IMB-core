#!/usr/bin/env bash
# Drive the Windows Vivado from WSL to build the A12 SoC bitstream.
#
# Staging copy, cmd.exe quoting and -log/-journal ordering are all inherited from
# fpga/scripts/build_fpga.sh, which is hardware-confirmed; see the comments there
# for why each one is the way it is.
#
# NOTE: the WSL<->Windows interop socket is blocked under the agent sandbox, so
# this must run with the sandbox disabled (or from a normal shell).
#
# Usage: build_soc.sh [fail_on_neg] [want_bit]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

FAIL_ON_NEG="${1:-1}"
WANT_BIT="${2:-1}"

VIVADO_WIN="${VIVADO_WIN:-C:\\AMDDesignTools\\2025.2\\Vivado\\bin\\vivado.bat}"
STAGE_WIN="${STAGE_WIN:-C:\\Users\\liamf\\rvntt-soc}"
STAGE_WSL="${STAGE_WSL:-/mnt/c/Users/liamf/rvntt-soc}"
OUT="${OUT:-$ROOT/fpga/build/soc}"

echo "=== staging sources to $STAGE_WSL ==="
rm -rf "$STAGE_WSL"
mkdir -p "$STAGE_WSL/rtl" "$STAGE_WSL/constraints"

cp "$ROOT"/rtl/core/*.sv    "$STAGE_WSL/rtl/"
cp "$ROOT"/rtl/common/*.sv  "$STAGE_WSL/rtl/"
cp "$ROOT"/rtl/soc/*.sv     "$STAGE_WSL/rtl/"
# The RVFI port is verification-only, compiled solely under RISCV_FORMAL; it has
# no business in a bitstream and its macros are not defined here.
rm -f "$STAGE_WSL/rtl/rvntt_rvfi.sv"
# ... and neither simulation top belongs in a synthesis run: rvntt_soc_sim_top
# would drag in simulation-sized parameters, and rvntt_core_sim_top is a second
# unconstrained top that makes synth_design pick one by guesswork.
rm -f "$STAGE_WSL/rtl/rvntt_soc_sim_top.sv" "$STAGE_WSL/rtl/rvntt_core_sim_top.sv"
# The blinky is a separate, already-shipped design; leaving it here would give
# synth_design two candidate tops and its own $readmemh file to miss.
rm -f "$STAGE_WSL/rtl/rvntt_blinky_top.sv" "$STAGE_WSL/rtl/rvntt_bram_selftest.sv" \
      "$STAGE_WSL/rtl/rvntt_uart_report.sv"

cp "$ROOT"/fpga/constraints/arty_a7_100t_soc.xdc "$STAGE_WSL/constraints/"
cp "$ROOT"/fpga/scripts/build_soc.tcl            "$STAGE_WSL/"
# $readmemh and `include both resolve relative to Vivado's working directory.
cp "$ROOT"/fpga/generated/soc_clk.svh   "$STAGE_WSL/"
cp "$ROOT"/fpga/generated/soc_clk.svh   "$STAGE_WSL/rtl/"
cp "$ROOT"/fpga/generated/soc_init.mem  "$STAGE_WSL/"

echo "=== running Vivado (batch) ==="
cd "$STAGE_WSL"
cmd.exe /c "cd /d $STAGE_WIN && $VIVADO_WIN -mode batch -log vivado.log -journal vivado.jou -source build_soc.tcl -tclargs $FAIL_ON_NEG $WANT_BIT" 2>&1
rc=$?

echo "=== copying products back ==="
mkdir -p "$OUT"
cp -r "$STAGE_WSL"/out/* "$OUT/" 2>/dev/null
cp "$STAGE_WSL"/vivado.log "$OUT/" 2>/dev/null

grep -E "^SOC_RESULT|^SOC_OK|^SOC_FAIL|^SOC_TIMING_FAIL|inferred BRAM" "$OUT/vivado.log" 2>/dev/null

if [ -f "$OUT/rvntt_soc_top.bit" ]; then
  echo "BITSTREAM: $(realpath --relative-to="$ROOT" "$OUT/rvntt_soc_top.bit")"
  ls -la "$OUT/rvntt_soc_top.bit"
fi
exit $rc
