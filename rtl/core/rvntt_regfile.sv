// ============================================================================
// rvntt_regfile -- the RV32I architectural register file.
//
// 32 x 32 bits, x0 hardwired to zero, THREE read ports, one write port, with
// write-through on a same-cycle read/write of the same address.
//
// Three read ports rather than two because plan §1.1 took the R4-type
// recommendation: `kbmul0` and `kmac` read rs3 as well as rs1/rs2
// (model/isa/xkntt.py, funct3 3 and 4).  Adding the third port later would
// mean re-timing ID, so it is here from the start.  It costs one extra
// RAM32M-style copy of the array in distributed RAM, which is cheap on this
// part and does not touch the critical path.
//
// Write-through is not an optimisation, it is a correctness simplification:
// with it, a WB-stage write is visible to an ID-stage read in the same cycle,
// which deletes the entire WB->ID forwarding case from A6.  Without it that
// case exists and is the one people forget.
//
// Deliberately package-free and parameterised.  Nothing here needs rv32i_pkg,
// and staying self-contained means tb/formal/run_formal.py -- which passes
// exactly one source file to sby -- can prove it without modification.
//
// No reset port.  The array powers up at zero via an explicit initial value,
// which on Xilinx is what the FF/LUTRAM INIT attribute actually does, and
// which matches Spike's architectural state at reset so A5's cosim starts
// aligned.  A synchronous reset across 31x32 flops would also block
// distributed-RAM inference and buy nothing.
// ============================================================================
`default_nettype none

module rvntt_regfile #(
    parameter int XLEN  = 32,
    parameter int NREGS = 32
) (
    input  wire                     clk,

    // Read ports (combinational, with write-through).
    input  wire [$clog2(NREGS)-1:0] ra1,
    input  wire [$clog2(NREGS)-1:0] ra2,
    input  wire [$clog2(NREGS)-1:0] ra3,
    output wire [XLEN-1:0]          rd1,
    output wire [XLEN-1:0]          rd2,
    output wire [XLEN-1:0]          rd3,

    // Write port.
    input  wire                     we,
    input  wire [$clog2(NREGS)-1:0] wa,
    input  wire [XLEN-1:0]          wd
);

  localparam int AW = $clog2(NREGS);

  // Power-on state, written as an `initial` loop rather than a declaration
  // initialiser (`= '{default: '0}`).  That form is legal SystemVerilog and is
  // accepted by Verilator, but Yosys does not parse it -- it fails with
  // "syntax error, unexpected TOK_DEFAULT" -- so the formal flow could not read
  // the file at all.  The loop is the idiom Vivado infers block/distributed RAM
  // initialisation from anyway, so this is portable across all three tools.
  //
  // (Keep the word "verilator" out of the first position after `//` in this
  //  file: Verilator parses `// verilator <word>` as a pragma and errors out
  //  with BADVLTPRAGMA on prose that happens to start that way.)
  logic [XLEN-1:0] regs [0:NREGS-1];
  initial begin
    for (int i = 0; i < NREGS; i++) regs[i] = '0;
  end

  // x0 is enforced at the WRITE, not at the read.  Entry 0 is initialised to
  // zero and never written, so regs[0] is a compile-time constant zero and the
  // read ports need no extra mux -- and the write-through mux below cannot fire
  // for address 0 either, because wr_en is already false there.
  wire wr_en = we && (wa != {AW{1'b0}});

  always_ff @(posedge clk) begin
    if (wr_en) regs[wa] <= wd;
  end

  assign rd1 = (wr_en && (wa == ra1)) ? wd : regs[ra1];
  assign rd2 = (wr_en && (wa == ra2)) ? wd : regs[ra2];
  assign rd3 = (wr_en && (wa == ra3)) ? wd : regs[ra3];

`ifdef FORMAL
  // Bounded proof of the three properties the directed testbench checks by
  // example.  These are cheap and they hold for EVERY address/data sequence,
  // not just the ones the C++ testbench happens to drive.

  // 1. x0 always reads as zero, on every port, unconditionally.
  always_comb begin
    if (ra1 == {AW{1'b0}}) assert (rd1 == {XLEN{1'b0}});
    if (ra2 == {AW{1'b0}}) assert (rd2 == {XLEN{1'b0}});
    if (ra3 == {AW{1'b0}}) assert (rd3 == {XLEN{1'b0}});
  end

  // 2. Write-through: a read of the address being written returns the new data
  //    in the same cycle, not the stale contents.
  always_comb begin
    if (wr_en && ra1 == wa) assert (rd1 == wd);
    if (wr_en && ra2 == wa) assert (rd2 == wd);
    if (wr_en && ra3 == wa) assert (rd3 == wd);
  end

  // 3. Read ports agree whenever their addresses agree.  This is what catches a
  //    copy-paste bug in the port replication above -- the single most likely
  //    defect in a hand-written multi-port array, and one a directed test only
  //    finds if it happens to drive the same address on two ports.
  always_comb begin
    if (ra1 == ra2) assert (rd1 == rd2);
    if (ra1 == ra3) assert (rd1 == rd3);
  end

  // 4. Storage is stable: an entry may only change on a write to that entry.
  //    Checked on one symbolic address so the property is address-generic
  //    without unrolling 32 copies.
  (* anyconst *) logic [AW-1:0] f_addr;
  logic                f_past_valid = 1'b0;
  logic [XLEN-1:0]     f_prev;

  always_ff @(posedge clk) begin
    f_past_valid <= 1'b1;
    f_prev       <= regs[f_addr];
  end

  always_ff @(posedge clk) begin
    if (f_past_valid && !($past(wr_en) && $past(wa) == f_addr))
      assert (regs[f_addr] == f_prev);
  end
`endif

endmodule

`default_nettype wire
