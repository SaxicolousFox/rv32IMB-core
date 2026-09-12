// ============================================================================
// rvfi_wrapper -- rvntt_core, presented to riscv-formal.
//
// riscv-formal's generated testbench instantiates a module with this exact
// name and port list.  Everything the core needs from outside is driven by an
// unconstrained symbolic value, so the proof covers every instruction stream
// and every memory response.
//
// The memory interface is not a handshake: rvntt_ram registers each port's
// address, the core has no way to stall on memory, and so the fixed timing
// is expressed by imem_rdata and dmem_rdata being fresh symbolic values every
// cycle.  Because dmem_rdata is unconstrained, nothing here checks that a
// load returns what an earlier store wrote (riscv-formal's `dmem` and `bus_*`
// checks need a memory model); cosimulation and riscv-tests cover that
// against a real RAM.
//
// The core's own outputs are pulled out to `(* keep *)` wires so a
// counterexample trace shows them.
// ============================================================================
module rvfi_wrapper (
	input         clock,
	input         reset,
	`RVFI_OUTPUTS
);
	// Fresh every cycle, unrelated to any address: the insn checks do not
	// require the word to have come from memory[pc].
	(* keep *) `rvformal_rand_reg [31:0] imem_rdata;
	(* keep *) `rvformal_rand_reg [31:0] dmem_rdata;

	(* keep *) wire [31:0] imem_addr;
	(* keep *) wire [31:0] dmem_addr;
	(* keep *) wire [31:0] dmem_wdata;
	(* keep *) wire [ 3:0] dmem_be;

	// The commit trace, alongside RVFI: it omits trapping instructions on
	// purpose, so one cannot be derived from the other.
	(* keep *) wire        commit_valid;
	(* keep *) wire [31:0] commit_pc;
	(* keep *) wire [31:0] commit_insn;
	(* keep *) wire        commit_reg_write;
	(* keep *) wire [ 4:0] commit_rd;
	(* keep *) wire [31:0] commit_wdata;
	(* keep *) wire        dbg_unsupported;

	// rst_n is asserted for the one cycle riscv-formal holds `reset`
	// (`reset == $initstate`).  An async active-low reset held through step 0
	// leaves every reset flop at its reset value from step 1; nothing in the
	// core may assert anything about its state during step 0 (see
	// rvntt_rvfi.sv's f_started_q).
	rvntt_core uut (
		.clk              (clock),
		.rst_n            (!reset),

		.imem_addr        (imem_addr),
		.imem_rdata       (imem_rdata),

		.dmem_addr        (dmem_addr),
		.dmem_wdata       (dmem_wdata),
		.dmem_be          (dmem_be),
		.dmem_rdata       (dmem_rdata),

		.commit_valid     (commit_valid),
		.commit_pc        (commit_pc),
		.commit_insn      (commit_insn),
		.commit_reg_write (commit_reg_write),
		.commit_rd        (commit_rd),
		.commit_wdata     (commit_wdata),
		.dbg_unsupported  (dbg_unsupported),

		`RVFI_CONN32
	);
endmodule
