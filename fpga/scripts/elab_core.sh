#!/usr/bin/env bash
# Drive the Windows Vivado from WSL to ELABORATE the Track A core sources.
#
# Same staging trick as build_fpga.sh: Vivado runs natively on Windows and is
# unreliable reading \\wsl.localhost UNC paths, so sources are copied onto the
# Windows filesystem first.
#
# NOTE: the WSL<->Windows interop socket is blocked under the agent sandbox, so
# this must run with the sandbox disabled (or from a normal shell).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

TOP="${1:-rvntt_regfile}"

VIVADO_WIN="${VIVADO_WIN:-C:\\AMDDesignTools\\2025.2\\Vivado\\bin\\vivado.bat}"
STAGE_WIN="${STAGE_WIN:-C:\\Users\\liamf\\rvntt-elab}"
STAGE_WSL="${STAGE_WSL:-/mnt/c/Users/liamf/rvntt-elab}"

echo "=== staging core sources to $STAGE_WSL ==="
rm -rf "$STAGE_WSL"
mkdir -p "$STAGE_WSL/rtl"

cp "$ROOT"/rtl/core/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
cp "$ROOT"/rtl/common/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
cp "$ROOT"/fpga/scripts/elab_core.tcl "$STAGE_WSL/"

cd "$STAGE_WSL"
# No inner quotes: cmd.exe mangles nested quoting in /c, and neither path
# contains spaces.  Keep it that way (or switch to a .cmd shim if it ever does).
#
# -log/-journal MUST come before -tclargs.  Everything after -tclargs is handed
# to the Tcl script as argv, so the original ordering silently turned the log
# option into a script argument and Vivado wrote to the default vivado.log --
# the run itself succeeded and the wrapper still reported failure because it had
# no log to grep.
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
