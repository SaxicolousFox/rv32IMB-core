# RV32I + NTT coprocessor -- top-level build/test entry points.
#
# `make regress` is the contract from plan P0.2: it runs every test, prints a
# pass/fail table, and returns nonzero on failure.

SHELL := /bin/bash
ROOT  := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
PY    ?= python3

# Tools live in toolchain/; env.sh puts them on PATH without polluting the shell.
ENV := source $(ROOT)/toolchain/env.sh &&

.PHONY: help regress regress-v list lint formal model models clean tools bitstream elab

help:
	@echo "make regress    - run the full regression (nonzero exit on failure)"
	@echo "make regress-v  - same, verbose (show output of every test)"
	@echo "make list       - list registered tests"
	@echo "make lint       - Verilator lint over all RTL"
	@echo "make formal     - run formal checks"
	@echo "make model      - run Python golden-model self-tests"
	@echo "make models     - build the instrumented C golden model"
	@echo "make tools      - print resolved tool versions"
	@echo "make bitstream  - build the P0.5 FPGA bitstream via Windows Vivado"
	@echo "make elab       - Vivado elaboration check on rtl/core (TOP=<module>)"
	@echo "make clean      - remove build/sim artifacts"

models:
	@$(MAKE) -s -C $(ROOT)/model/cref

regress: models
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py

regress-v: models
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py -v

list:
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py --list

lint:
	@$(ENV) $(PY) $(ROOT)/tb/lint_all.py

formal:
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py -k formal

model:
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py -k model

tools:
	@$(ENV) echo "verilator : $$(verilator --version 2>&1 | head -1)"; \
	 echo "yosys     : $$(yosys -V 2>&1 | head -1)"; \
	 echo "sby       : $$(sby --version 2>&1 | head -1)"; \
	 echo "bitwuzla  : $$(bitwuzla --version 2>&1 | head -1)"; \
	 echo "riscv gcc : $$(riscv-none-elf-gcc --version 2>&1 | head -1)"; \
	 echo "spike     : $$(spike --help 2>&1 | head -1 || echo 'NOT BUILT')"; \
	 echo "cocotb    : $$($(PY) -c 'import cocotb;print(cocotb.__version__)' 2>/dev/null || echo 'NOT INSTALLED')"

bitstream:
	@$(ROOT)/fpga/scripts/gen_bram_init.py
	@$(ROOT)/fpga/scripts/build_fpga.sh

# Plan A1's "Done when" requires the package to elaborate under Vivado as well
# as lint under Verilator; the two front ends disagree often enough on packages,
# structs and array initialisation that this is a real check, not a formality.
#
# Deliberately NOT in `make regress`: it needs the Windows Vivado over the
# WSL interop socket, which the agent sandbox blocks, and a regression that
# cannot run its own test would report SKIP -- which is exactly the
# green-looking-but-vacuous outcome the root CLAUDE.md warns about.  Same
# precedent as `make bitstream`.
TOP ?= rvntt_regfile
elab:
	@$(ROOT)/fpga/scripts/elab_core.sh $(TOP)

clean:
	rm -rf $(ROOT)/tb/formal/*/ $(ROOT)/tb/formal/*.sby
	rm -rf $(ROOT)/obj_dir $(ROOT)/sim_build
	find $(ROOT) -name '__pycache__' -not -path '*/toolchain/opt/*' -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "clean done"
