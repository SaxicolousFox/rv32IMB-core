#!/usr/bin/env bash
# Drive the Windows Vivado from WSL to elaborate the core sources.  Same
# staging as build_fpga.sh.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

TOP="${1:-rvntt_regfile}"

VIVADO_WIN="${VIVADO_WIN:-C:\\AMDDesignTools\\2025.2\\Vivado\\bin\\vivado.bat}"
STAGE_WIN="${STAGE_WIN:-C:\\Users\\liamf\\rv32imb-core-elab}"
STAGE_WSL="${STAGE_WSL:-/mnt/c/Users/liamf/rv32imb-core-elab}"

echo "=== staging core sources to $STAGE_WSL ==="
rm -rf "$STAGE_WSL"
mkdir -p "$STAGE_WSL/rtl"

cp "$ROOT"/rtl/core/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
cp "$ROOT"/rtl/common/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
# The SoC tops need the generated clock header.  The RVFI port is excluded:
# it is only compiled under RISCV_FORMAL.
cp "$ROOT"/rtl/soc/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
cp "$ROOT"/fpga/generated/*.svh "$STAGE_WSL/" 2>/dev/null
cp "$ROOT"/fpga/generated/*.svh "$STAGE_WSL/rtl/" 2>/dev/null
# $readmemh resolves against Vivado's working directory; without the image
# elaboration emits a CRITICAL WARNING and carries on with an empty memory.
cp "$ROOT"/fpga/generated/*.mem "$STAGE_WSL/" 2>/dev/null
rm -f "$STAGE_WSL/rtl/rvntt_rvfi.sv"
cp "$ROOT"/fpga/scripts/elab_core.tcl "$STAGE_WSL/"

cd "$STAGE_WSL"
# No inner quotes: cmd.exe mangles nested quoting in /c.  -log/-journal must
# come before -tclargs, since everything after -tclargs is argv.
cmd.exe /c "cd /d $STAGE_WIN && $VIVADO_WIN -mode batch -log elab_$TOP.log -journal elab_$TOP.jou -source elab_core.tcl -tclargs $TOP" 2>&1
rc=$?

mkdir -p "$ROOT/fpga/build"
cp "$STAGE_WSL"/elab_*.log "$ROOT/fpga/build/" 2>/dev/null

if grep -q "ELAB_OK: $TOP" "$STAGE_WSL/elab_$TOP.log" 2>/dev/null; then
  echo "ELAB_OK: $TOP"
  exit 0
fi
echo "ELAB_FAIL: $TOP (vivado rc=$rc) -- see fpga/build/elab_$TOP.log" >&2
exit 1
