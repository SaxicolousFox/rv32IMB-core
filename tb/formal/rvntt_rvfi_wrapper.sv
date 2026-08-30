// ============================================================================
// rvfi_wrapper -- rvntt_core, presented to riscv-formal (plan A11).
//
// riscv-formal's generated testbench instantiates a module with this exact name
// and this exact port list, so the name is theirs, not ours.  Everything the
// core needs from outside is driven by an UNCONSTRAINED symbolic value, which is
// the whole idea: the proof then covers every instruction stream and every
// memory response, not the ones a testbench happened to think of.
//
// THE MEMORY INTERFACE IS NOT A HANDSHAKE, and that is why this file is short.
// rvntt_ram registers each port's address, so its output register IS a pipeline
// register: port A's address comes from the PC register in IF and port B's from
// the COMBINATIONAL ALU result in EX.  The core has no valid, no ready and no
// way to stall on memory, so there is no protocol here to constrain -- the fixed
// timing is expressed by imem_rdata and dmem_rdata simply being fresh symbolic
// values every cycle, which is a strict over-approximation of any memory that
// answers in one cycle.  Adding a `stall` input, as the NERV wrapper has, would
// be modelling a signal this core does not have.
//
// WHAT THAT DOES NOT COVER, stated rather than implied: because dmem_rdata is
// unconstrained, nothing here checks that a load returns what an earlier store
// wrote.  That is riscv-formal's `dmem` check, and it needs a memory model in
// this wrapper; the same goes for the `bus_*` checks, which need the RVFI_BUS
// observer ports.  Neither is part of plan A11's done-when list.  Memory
// consistency is covered instead by A5's cosimulation and by riscv-tests,
// against a real RAM.
//
// The core's own outputs are pulled out to `(* keep *)` wires so a
// counterexample trace shows them; nothing in the checks reads them.
// ============================================================================
module rvfi_wrapper (
	input         clock,
	input         reset,
	`RVFI_OUTPUTS
);
	// Fresh every cycle, unrelated to any address.  riscv-formal's insn checks
	// do not require the instruction word to have come from memory[pc] -- that
	// is what the imem/bus_imem checks are for -- so leaving this free is what
	// makes the proof cover every instruction sequence of the chosen depth.
	(* keep *) `rvformal_rand_reg [31:0] imem_rdata;
	(* keep *) `rvformal_rand_reg [31:0] dmem_rdata;

	(* keep *) wire [31:0] imem_addr;
	(* keep *) wire [31:0] dmem_addr;
	(* keep *) wire [31:0] dmem_wdata;
	(* keep *) wire [ 3:0] dmem_be;

	// A5's commit trace, alongside RVFI rather than replaced by it.  The two
	// carry different information -- the commit trace omits trapping
	// instructions on purpose, to stay line-for-line comparable with Spike --
	// and rvntt_rvfi.sv exists precisely because one cannot be derived from the
	// other.  Keeping both wired here means a counterexample shows them side by
	// side.
	(* keep *) wire        commit_valid;
	(* keep *) wire [31:0] commit_pc;
	(* keep *) wire [31:0] commit_insn;
	(* keep *) wire        commit_reg_write;
	(* keep *) wire [ 4:0] commit_rd;
	(* keep *) wire [31:0] commit_wdata;
	(* keep *) wire        dbg_unsupported;

	// rst_n is asserted for exactly the cycle riscv-formal's testbench holds
	// `reset` -- it constrains `reset == $initstate`, so that is step 0 only.
	// The core's flops are asynchronously reset, which needs no special handling
	// here: an active-low async reset held through step 0 leaves every reset
	// flop at its reset value from step 1 onward, which is the same state a
	// synchronous reset of the same length would produce.  What it does NOT do
	// is constrain those flops DURING step 0, so nothing in the core may assert
	// anything about its own state on that first edge (see rvntt_rvfi.sv's
	// f_started_q).
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
