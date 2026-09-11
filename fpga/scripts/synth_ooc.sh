#!/usr/bin/env bash
# Drive the Windows Vivado from WSL to synthesise ONE Track A module out of context.
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

TOP="${1:-rvntt_muldiv}"
# MODS_A2 A24: everything after the top is handed to the Tcl script verbatim --
# an optional clock period in ns, then any number of NAME=VALUE generics.  With
# no period the behaviour is exactly A14's: synthesis only, resource report only.
shift || true
# NAME=VALUE is the natural way to write a generic and the one thing that cannot
# be sent: `cmd.exe /c` splits on `=` as if it were a space, so the Tcl script
# received the name and the value as two separate argv entries and silently
# synthesised the default.  Callers still write `=`; it is translated to `:`
# here, and synth_ooc.tcl hard-fails if trailing arguments parse to no generic.
EXTRA=()
for a in "$@"; do EXTRA+=("${a/=/:}"); done

# The log name carries the generics, because A24 runs the same module at three
# different STAGES values and a shared log would silently overwrite the number
# from the previous configuration with the number from this one.
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
# The whole tree is staged even though one module is synthesised: the top may
# instantiate others, and out-of-context synthesis prunes what it does not
# reach.  The RVFI port is excluded -- it is compiled only under RISCV_FORMAL,
# and reading it here would need the define plus riscv-formal's macros.
cp "$ROOT"/rtl/soc/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
# rtl/probe/ holds MODS_A2 A24's Tier-1 timing probe.  It is a measurement
# artefact and is deliberately NOT in rtl/ntt/, which is Track B's.
cp "$ROOT"/rtl/probe/*.sv "$STAGE_WSL/rtl/" 2>/dev/null
cp "$ROOT"/fpga/generated/*.svh "$STAGE_WSL/" 2>/dev/null
cp "$ROOT"/fpga/generated/*.svh "$STAGE_WSL/rtl/" 2>/dev/null
# $readmemh resolves against Vivado's WORKING directory, not the source file's.
# Not needed for a module with no memory, but staged anyway so that this script
# works for rvntt_ram and the SoC tops too.
cp "$ROOT"/fpga/generated/*.mem "$STAGE_WSL/" 2>/dev/null
rm -f "$STAGE_WSL/rtl/rvntt_rvfi.sv"
cp "$ROOT"/fpga/scripts/synth_ooc.tcl "$STAGE_WSL/"

cd "$STAGE_WSL"
# No inner quotes: cmd.exe mangles nested quoting in /c, and neither path
# contains spaces.  Keep it that way (or switch to a .cmd shim if it ever does).
#
# -log/-journal MUST come before -tclargs.  Everything after -tclargs is handed
# to the Tcl script as argv, so the original ordering silently turned the log
# option into a script argument and Vivado wrote to the default vivado.log --
# the run itself succeeded and the wrapper still reported failure because it had
# no log to grep.
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
