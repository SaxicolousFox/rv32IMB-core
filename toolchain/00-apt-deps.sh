#!/usr/bin/env bash
# System package prerequisites (Ubuntu / WSL2).  Run once with:
#   sudo bash toolchain/00-apt-deps.sh
#
#   python3-venv/pip/dev  -> the project venv (.venv)
#   device-tree-compiler  -> Spike's configure requires dtc
#   libboost-*            -> Spike needs boost asio + regex
#   build-essential/cmake -> native builds
#   gtkwave/graphviz      -> waveform viewing, `yosys show`
set -euo pipefail

apt-get update
apt-get install -y --no-install-recommends \
  python3-pip python3-venv python3-dev python3-setuptools \
  build-essential cmake ninja-build pkg-config autoconf automake libtool ccache \
  git curl wget xz-utils unzip bc file ca-certificates \
  device-tree-compiler \
  libboost-dev libboost-regex-dev libboost-system-dev libboost-filesystem-dev \
  flex bison gawk gperf texinfo patchutils \
  libgmp-dev libmpfr-dev libmpc-dev libexpat1-dev zlib1g-dev \
  libreadline-dev libffi-dev tcl-dev libssl-dev \
  gtkwave graphviz

echo "OK: apt dependencies installed"
