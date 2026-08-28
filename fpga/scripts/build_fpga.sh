#!/usr/bin/env bash
# Drive the Windows Vivado from WSL to build the P0.5 bitstream.
#
# Why the staging copy: Vivado runs natively on Windows and is unreliable at
# reading \\wsl.localhost UNC paths, so sources are staged onto the Windows
# filesystem, built there, and the products copied back into fpga/build/.
#
# NOTE: the WSL<->Windows interop socket is blocked under the agent sandbox, so
# this must run with the sandbox disabled (or from a normal shell).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

VIVADO_WIN="${VIVADO_WIN:-C:\\AMDDesignTools\\2025.2\\Vivado\\bin\\vivado.bat}"
STAGE_WIN="${STAGE_WIN:-C:\\Users\\liamf\\rvntt-fpga}"
STAGE_WSL="${STAGE_WSL:-/mnt/c/Users/liamf/rvntt-fpga}"

echo "=== staging sources to $STAGE_WSL ==="
rm -rf "$STAGE_WSL"
mkdir -p "$STAGE_WSL/rtl" "$STAGE_WSL/constraints"

cp "$ROOT"/rtl/soc/rvntt_blinky_top.sv \
   "$ROOT"/rtl/soc/rvntt_clkgen.sv \
   "$ROOT"/rtl/soc/rvntt_uart_tx.sv \
   "$ROOT"/rtl/soc/rvntt_uart_report.sv \
   "$ROOT"/rtl/soc/rvntt_bram_selftest.sv \
   "$ROOT"/rtl/common/rvntt_sync_reset.sv \
   "$STAGE_WSL/rtl/"

cp "$ROOT"/fpga/constraints/arty_a7_100t.xdc "$STAGE_WSL/constraints/"
cp "$ROOT"/fpga/scripts/build_blinky.tcl     "$STAGE_WSL/"
# $readmemh and `include resolve relative to the Vivado working directory.
cp "$ROOT"/fpga/generated/bram_init.mem      "$STAGE_WSL/"
cp "$ROOT"/fpga/generated/bram_expected.svh  "$STAGE_WSL/"

echo "=== running Vivado (batch) ==="
cd "$STAGE_WSL"
set +e
# No inner quotes: cmd.exe mangles nested quoting in /c, and neither path
# contains spaces.  Keep it that way (or switch to a .cmd shim if it ever does).
cmd.exe /c "cd /d $STAGE_WIN && $VIVADO_WIN -mode batch -source build_blinky.tcl -log vivado.log -journal vivado.jou" 2>&1
rc=$?
set -e

echo "=== copying products back ==="
mkdir -p "$ROOT/fpga/build"
cp -r "$STAGE_WSL"/out/* "$ROOT/fpga/build/" 2>/dev/null || true
cp "$STAGE_WSL"/vivado.log "$ROOT/fpga/build/" 2>/dev/null || true

if [ -f "$ROOT/fpga/build/rvntt_blinky_top.bit" ]; then
  echo "BITSTREAM: fpga/build/rvntt_blinky_top.bit"
  ls -la "$ROOT/fpga/build/rvntt_blinky_top.bit"
else
  echo "NO BITSTREAM PRODUCED (vivado rc=$rc)" >&2
fi
exit $rc
