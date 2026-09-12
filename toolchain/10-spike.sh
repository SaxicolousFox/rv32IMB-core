#!/usr/bin/env bash
# Build Spike (riscv-isa-sim) from toolchain/spike-src into toolchain/install.
# The commit is pinned in toolchain/pins.txt.
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
