// ============================================================================
// Testbench-only wrapper: rvntt_decode with ctrl_t flattened to scalar ports.
//
// A packed struct is flattened by the simulator into a single wide vector, so
// cocotb sees `ctrl` as one integer with no member access.  Reading it that way
// would mean encoding the struct's bit layout in the Python testbench -- a
// duplicated, unchecked copy of the field order, which is exactly the kind of
// thing that silently starts comparing the wrong bits after someone inserts a
// field.
//
// So the layout duplication lives here instead, in SystemVerilog, where the
// field names are checked by the compiler: a renamed or deleted ctrl_t member
// is a build error rather than a wrong comparison.  What this file CANNOT catch
// is a field wired to the wrong port, so the A3 fault-injection table mutates
// every ctrl field in turn -- a dropped or crossed connection here shows up as
// a mutation that escapes.
//
// Not in rtl/: this is test scaffolding and must never reach synthesis.  It is
// still linted, because run_cocotb.py builds this design with -Wall.
// ============================================================================
`default_nettype none

module rvntt_decode_flat (
    input  wire logic [31:0] insn,

    output wire logic        reg_write,
    output wire logic        mem_read,
    output wire logic        mem_write,
    output wire logic [2:0]  mem_op,
    output wire logic        branch,
    output wire logic        jump,
    output wire logic        jalr,
    output wire logic [3:0]  alu_op,
    output wire logic [1:0]  alu_src_a,
    output wire logic [0:0]  alu_src_b,
    output wire logic [2:0]  result_sel,
    output wire logic [2:0]  imm_fmt,
    output wire logic        uses_rs1,
    output wire logic        uses_rs2,
    output wire logic        is_ecall,
    output wire logic        is_ebreak,
    output wire logic        is_mret,
    output wire logic        is_csr,
    output wire logic        is_muldiv,
    output wire logic [2:0]  muldiv_op,
    output wire logic        is_bitmanip,
    output wire logic [5:0]  bm_op,
    output wire logic        is_illegal,

    output wire logic [4:0]  rd_addr,
    output wire logic [4:0]  rs1_addr,
    output wire logic [4:0]  rs2_addr
);

  rv32i_pkg::ctrl_t ctrl;

  rvntt_decode u_dut (
      .insn     (insn),
      .ctrl     (ctrl),
      .rd_addr  (rd_addr),
      .rs1_addr (rs1_addr),
      .rs2_addr (rs2_addr)
  );

  assign is_bitmanip = ctrl.is_bitmanip;
  assign bm_op       = ctrl.bm_op;
  assign reg_write  = ctrl.reg_write;
  assign mem_read   = ctrl.mem_read;
  assign mem_write  = ctrl.mem_write;
  assign mem_op     = ctrl.mem_op;
  assign branch     = ctrl.branch;
  assign jump       = ctrl.jump;
  assign jalr       = ctrl.jalr;
  assign alu_op     = ctrl.alu_op;
  assign alu_src_a  = ctrl.alu_src_a;
  assign alu_src_b  = ctrl.alu_src_b;
  assign result_sel = ctrl.result_sel;
  assign imm_fmt    = ctrl.imm_fmt;
  assign uses_rs1   = ctrl.uses_rs1;
  assign uses_rs2   = ctrl.uses_rs2;
  assign is_ecall   = ctrl.is_ecall;
  assign is_ebreak  = ctrl.is_ebreak;
  assign is_mret    = ctrl.is_mret;
  assign is_csr     = ctrl.is_csr;
  assign is_muldiv  = ctrl.is_muldiv;
  assign muldiv_op  = ctrl.muldiv_op;
  assign is_illegal = ctrl.is_illegal;

endmodule

`default_nettype wire
