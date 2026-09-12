# Project tool environment.  Usage:  source toolchain/env.sh
#
# PATH ordering: /usr/local/bin first (the system Verilator wins over the one
# bundled in oss-cad-suite), then oss-cad-suite (yosys, sby, bitwuzla, z3),
# then the RISC-V toolchains.

_here="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export RVNTT_ROOT="$( cd "$_here/.." && pwd )"
export OSS_CAD="$_here/opt/oss-cad-suite"

# ---- RISC-V toolchains -------------------------------------------------------
# xPack (riscv-none-elf-*) is the primary toolchain: it ships a full multilib
# set including rv32i/ilp32.  The riscv-collab prebuilt is single-multilib
# (rv32imafdc/ilp32d) and is kept on PATH as a secondary (gdb, qemu, objdump).
export XPACK_RV="$(ls -d "$_here"/opt/xpack-riscv-none-elf-gcc-*/ 2>/dev/null | head -1)"
export RISCV="$_here/opt/riscv"

export PATH="/usr/local/bin:$OSS_CAD/bin${XPACK_RV:+:${XPACK_RV%/}/bin}:$RISCV/bin:$PATH"

# Spike
export SPIKE_PREFIX="$_here/install"
[ -d "$SPIKE_PREFIX/bin" ] && export PATH="$SPIKE_PREFIX/bin:$PATH"

# Python venv
if [ -f "$RVNTT_ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "$RVNTT_ROOT/.venv/bin/activate"
fi

unset _here
