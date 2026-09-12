# rv32IMB-core -- top-level build/test entry points.

SHELL := /bin/bash
ROOT  := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
PY    ?= python3

# Tools live in toolchain/; env.sh puts them on PATH without polluting the shell.
ENV := source $(ROOT)/toolchain/env.sh &&

.PHONY: help regress regress-v list lint formal clean tools bitstream soc-bitstream bench-bitstream elab

help:
	@echo "make regress         - run the full regression (nonzero exit on failure)"
	@echo "make regress-v       - same, verbose (show output of every test)"
	@echo "make list            - list registered tests"
	@echo "make lint            - Verilator lint over all RTL"
	@echo "make formal          - run formal checks"
	@echo "make tools           - print resolved tool versions"
	@echo "make bitstream       - build the blinky/BRAM self-test bitstream via Windows Vivado"
	@echo "make soc-bitstream   - build the hello-world SoC bitstream"
	@echo "make bench-bitstream - build the Dhrystone/CoreMark SoC bitstream"
	@echo "make elab            - Vivado elaboration check on rtl/core (TOP=<module>)"
	@echo "make clean           - remove build/sim artifacts"

regress:
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py

regress-v:
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py -v

list:
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py --list

lint:
	@$(ENV) $(PY) $(ROOT)/tb/lint_all.py

formal:
	@$(ENV) $(PY) $(ROOT)/tb/run_regress.py -k formal

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

# The Vivado flows need the Windows Vivado over the WSL interop socket and are
# deliberately not part of `make regress`.  Both SoC builds use the clock in
# fpga/generated/soc_clk.svh (gen_soc_clk.py --mhz N to change it).
soc-bitstream:
	@$(ENV) $(PY) $(ROOT)/fpga/scripts/build_soc_image.py --out $(ROOT)/fpga/generated/soc_init.mem
	@SOC_MEM=$(ROOT)/fpga/generated/soc_init.mem OUT=$(ROOT)/fpga/build/soc \
	  bash $(ROOT)/fpga/scripts/build_soc.sh 1 1 explore_postroute

bench-bitstream:
	@$(ENV) $(PY) $(ROOT)/fpga/scripts/build_bench_image.py --out $(ROOT)/fpga/generated/bench.mem \
	  --dhry-runs 2000000 --iterations 3300 --arch rv32imb --hpm
	@SOC_MEM=$(ROOT)/fpga/generated/bench.mem OUT=$(ROOT)/fpga/build/bench \
	  bash $(ROOT)/fpga/scripts/build_soc.sh 1 1 explore_postroute

TOP ?= rvntt_regfile
elab:
	@$(ROOT)/fpga/scripts/elab_core.sh $(TOP)

clean:
	rm -rf $(ROOT)/tb/formal/*/ $(ROOT)/tb/formal/*.sby
	rm -rf $(ROOT)/obj_dir $(ROOT)/sim_build
	find $(ROOT) -name '__pycache__' -not -path '*/toolchain/opt/*' -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "clean done"
