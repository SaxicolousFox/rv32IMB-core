#!/usr/bin/env bash
# P0.1 — project Python venv: cocotb + cocotb-test (+ pytest, numpy for the models).
#
# WHY THIS IS NOT JUST `python3 -m venv`:
#   Ubuntu 26.04 ships Python 3.14, but cocotb 2.0.1 hard-caps at 3.13 and its
#   build refuses to run on 3.14.  Rather than force it with
#   COCOTB_IGNORE_PYTHON_REQUIRES (unsupported, and this venv underpins every
#   testbench in the project), we use `uv` to fetch a standalone CPython 3.13.
#   uv needs no root and installs nothing system-wide.
#
#   Cache/install dirs are redirected into toolchain/ because $HOME is not
#   writable under the agent sandbox.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"

UV="$ROOT/toolchain/opt/uv-x86_64-unknown-linux-gnu/uv"
export UV_CACHE_DIR="$ROOT/toolchain/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$ROOT/toolchain/opt/python"

if [ ! -x "$UV" ]; then
  echo "ERROR: uv not found at $UV" >&2
  echo "Fetch it with:" >&2
  echo "  curl -sSL -o toolchain/dist/uv.tar.gz https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz" >&2
  echo "  tar -xzf toolchain/dist/uv.tar.gz -C toolchain/opt/" >&2
  exit 1
fi

"$UV" python install 3.13
rm -rf .venv
"$UV" venv --python 3.13 .venv
"$UV" pip install --python .venv/bin/python cocotb cocotb-test pytest numpy

echo "PYENV_DONE"
.venv/bin/python -c "import cocotb, cocotb_test; print('cocotb', cocotb.__version__)"
