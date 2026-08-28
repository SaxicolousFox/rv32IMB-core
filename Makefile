# RV32I + NTT coprocessor -- top-level build/test entry points.
#
# `make regress` is the contract from plan P0.2: it runs every test, prints a
# pass/fail table, and returns nonzero on failure.

SHELL := /bin/bash
ROOT  := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
PY    ?= python3

# Tools live in toolchain/; env.sh puts them on PATH without polluting the shell.
ENV := source $(ROOT)/toolchain/env.sh &&

.PHONY: help regress regress-v list lint formal model models clean tools

help:
	@echo "make regress    - run the full regression (nonzero exit on failure)"
	@echo "make regress-v  - same, verbose (show output of every test)"
	@echo "make list       - list registered tests"
	@echo "make lint       - Verilator lint over all RTL"
	@echo "make formal     - run formal checks"
	@echo "make model      - run Python golden-model self-tests"
	@echo "make models     - build the instrumented C golden model"
	@echo "make tools      - print resolved tool versions"
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
	@$(ENV) for f in $$(find $(ROOT)/rtl -name '*.sv'); do \
	   echo "lint $$f"; verilator --lint-only -Wall "$$f" || exit 1; done; \
	 echo "LINT OK"

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

clean:
	rm -rf $(ROOT)/tb/formal/*/ $(ROOT)/tb/formal/*.sby
	rm -rf $(ROOT)/obj_dir $(ROOT)/sim_build
	find $(ROOT) -name '__pycache__' -not -path '*/toolchain/opt/*' -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "clean done"
