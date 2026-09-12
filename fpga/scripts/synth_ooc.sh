#!/usr/bin/env bash
# Drive the Windows Vivado from WSL to synthesise one module out of context.
# Same staging as build_fpga.sh.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

TOP="${1:-rvntt_muldiv}"
# Everything after the top is handed to the Tcl script verbatim: an optional
# clock period in ns, then any number of NAME=VALUE generics.
shift || true
# `cmd.exe /c` splits on `=` as if it were a space, so NAME=VALUE is
# translated to NAME:VALUE here; synth_ooc.tcl hard-fails if trailing
# arguments parse to no generic.
EXTRA=()
for a in "$@"; do EXTRA+=("${a/=/:}"); done

# The log name carries the generics, so a sweep does not overwrite itself.
TAG="$TOP"
for a in "${EXTRA[@]}"; do
  case "$a" in
    *:*) TAG="${TAG}_${a/:/}" ;;
  esac
done

VIVADO_WIN="${VIVADO_WIN:-C:\\AMDDesignTools\\2025.2\\Vivado\\bin\\vivado.bat}"
STAGE_WIN="${STAGE_WIN:-C:\\Users\\liamf\\rv32imb-core-ooc}"
STAGE_WSL="${STAGE_WSL:-/mnt/c/Users/liamf/rv32imb-core-ooc}"

echo "=== staging sources to $STAGE_WSL ==="
rm -rf "$STAGE_WSL"
mkdir -p "$STAGE_WSL/rtl"

cp "$ROOT"/rtl/core/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
cp "$ROOT"/rtl/common/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
# The whole tree is staged: the top may instantiate others.  The RVFI port is
# excluded (compiled only under RISCV_FORMAL).
cp "$ROOT"/rtl/soc/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
cp "$ROOT"/fpga/generated/*.svh "$STAGE_WSL/" 2>/dev/null
cp "$ROOT"/fpga/generated/*.svh "$STAGE_WSL/rtl/" 2>/dev/null
# $readmemh resolves against Vivado's working directory.
cp "$ROOT"/fpga/generated/*.mem "$STAGE_WSL/" 2>/dev/null
rm -f "$STAGE_WSL/rtl/rvntt_rvfi.sv"
cp "$ROOT"/fpga/scripts/synth_ooc.tcl "$STAGE_WSL/"

cd "$STAGE_WSL"
# No inner quotes: cmd.exe mangles nested quoting in /c.  -log/-journal must
# come before -tclargs, since everything after -tclargs is argv.
cmd.exe /c "cd /d $STAGE_WIN && $VIVADO_WIN -mode batch -log ooc_$TAG.log -journal ooc_$TAG.jou -source synth_ooc.tcl -tclargs $TOP ${EXTRA[*]}" 2>&1
rc=$?

mkdir -p "$ROOT/fpga/build"
cp "$STAGE_WSL"/ooc_*.log "$ROOT/fpga/build/" 2>/dev/null

if grep -q "OOC_OK: $TOP" "$STAGE_WSL/ooc_$TAG.log" 2>/dev/null; then
  # Echo the timing line here so the caller does not have to grep a Windows
  # path.  Absent when no period was given, which is not an error.
  grep -h "^OOC_TIMING:\|^OOC_ENDPOINT:\|^OOC_DSP_TOTAL:\|^OOC_CELLS:\|^OOC_GENERICS:" \
       "$STAGE_WSL/ooc_$TAG.log" 2>/dev/null
  echo "OOC_OK: $TOP"
  exit 0
fi
echo "OOC_FAIL: $TOP (vivado rc=$rc) -- see fpga/build/ooc_$TAG.log" >&2
exit 1
