#!/usr/bin/env bash
# P0.1 — build Spike (riscv-isa-sim) FROM SOURCE (the plan requires source, not a
# package, because Track C1 patches it to add the Xkntt instructions).
# Installs into toolchain/install so it never touches the system.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
SRC="$PWD/spike-src"
BUILD="$PWD/spike-build"
PREFIX="$PWD/install"

mkdir -p "$BUILD" "$PREFIX"
cd "$BUILD"
if [ ! -f Makefile ]; then
  "$SRC/configure" --prefix="$PREFIX"
fi
make -j"$(nproc)"
make install
echo "SPIKE_BUILD_DONE"
"$PREFIX/bin/spike" --help 2>&1 | head -5 || true
