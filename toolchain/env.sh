# Project tool environment.  Usage:  source toolchain/env.sh
#
# Deliberate PATH ordering:
#   /usr/local/bin  first  -> the system Verilator 5.050 wins over the one bundled
#                             in oss-cad-suite, so simulator version is pinned/stable.
#   oss-cad-suite   next   -> yosys, sby, yosys-smtbmc, bitwuzla/boolector/z3, surfer.
#                             These are self-contained wrapper scripts, so no
#                             LD_LIBRARY_PATH surgery is needed and nothing leaks.
#   riscv toolchains next  -> see NOTE below on which one and why.

_here="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export RVNTT_ROOT="$( cd "$_here/.." && pwd )"
export OSS_CAD="$_here/opt/oss-cad-suite"

# ---- RISC-V toolchains -------------------------------------------------------
# XPACK (riscv-none-elf-*) is the PRIMARY toolchain.
#   The riscv-collab prebuilt (riscv32-unknown-elf-*) is single-multilib:
#   `--print-multi-lib` reports only `.;`, i.e. rv32imafdc/ilp32d.  This project
#   targets bare RV32I + Zicsr, so that toolchain has NO usable libgcc or newlib
#   (no __mulsi3, no libc) for our ABI.  xPack ships a full multilib set
#   including rv32i/ilp32, which is what Dhrystone/CoreMark/ML-KEM need.
#   The riscv-collab one is kept on PATH as a secondary (gdb, qemu, objdump).
export XPACK_RV="$(ls -d "$_here"/opt/xpack-riscv-none-elf-gcc-*/ 2>/dev/null | head -1)"
export RISCV="$_here/opt/riscv"

export PATH="/usr/local/bin:$OSS_CAD/bin${XPACK_RV:+:${XPACK_RV%/}/bin}:$RISCV/bin:$PATH"

# Spike, once built (P0.1)
export SPIKE_PREFIX="$_here/install"
[ -d "$SPIKE_PREFIX/bin" ] && export PATH="$SPIKE_PREFIX/bin:$PATH"

# Python venv holding cocotb + cocotb-test (P0.1)
if [ -f "$RVNTT_ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "$RVNTT_ROOT/.venv/bin/activate"
fi

unset _here
