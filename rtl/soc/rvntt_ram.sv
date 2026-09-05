// ============================================================================
// rvntt_ram -- true dual-port synchronous RAM, the core's whole memory system.
//
// Plan §1.4: BRAM only, no DDR3, no MIG.  Split I/D access "Harvard-style, both
// from the same physical BRAM array via true dual-port", which is what this is:
// port A is instruction fetch (read only), port B is load/store.  One array, so
// a program and its data live in one image and the addresses match the ELF --
// which is what lets A5 run the identical binary on Spike and on the RTL.
//
// SYNCHRONOUS READ, and the pipeline is built around that rather than fighting
// it.  Each port's address input is registered inside the RAM, so that register
// *is* a pipeline register:
//
//   * port A's address comes from the PC register in IF, so rdata_a is the
//     instruction during ID -- the RAM's output register is the IF/ID insn.
//   * port B's address comes from the COMBINATIONAL ALU result in EX, not from
//     the EX/MEM register, so rdata_b is valid during MEM.  Driving it from the
//     registered result instead would push load data into WB and add a second
//     load-use bubble that the plan's timing does not have.
//
// Byte enables on port B only; instruction fetch never writes.
//
// Addresses are byte addresses in the ELF's space (BASE = 0x80000000).  The
// low two bits are ignored -- alignment is the LSU's problem, not the RAM's.
// ============================================================================
`default_nettype none

module rvntt_ram #(
    parameter int          WORDS     = 16384,          // 64 KB
    parameter logic [31:0] BASE      = 32'h8000_0000,
    parameter string       INIT_FILE = ""
) (
    input  wire         clk,

    // Port A -- instruction fetch, read only.
    input  wire  [31:0] addr_a,
    output logic [31:0] rdata_a,

    // Port B -- load / store.
    input  wire  [31:0] addr_b,
    input  wire  [31:0] wdata_b,
    input  wire  [3:0]  be_b,        // byte enables; 0 means read
    output logic [31:0] rdata_b
);

  localparam int AW = $clog2(WORDS);

  logic [31:0] mem [0:WORDS-1];

  // Power-on contents.  The loop-then-$readmemh order matters: $readmemh leaves
  // any address the file does not cover untouched, so without the loop those
  // words would be X in simulation and a fetch from an unwritten address would
  // propagate X through the whole pipeline instead of behaving like the zeroed
  // BRAM a real FPGA gives you.
  //
  // The `initial` loop rather than a declaration initialiser: Yosys does not
  // parse `'{default: '0}` (see rtl/core/CLAUDE.md).
  initial begin
    for (int i = 0; i < WORDS; i++) mem[i] = 32'h0;
    if (INIT_FILE != "") $readmemh(INIT_FILE, mem);
  end

  // Byte address -> word index.  Everything above the array is dropped, which
  // makes the memory alias rather than fault; A9 owns access faults, and until
  // then aliasing is far easier to spot in a trace than an X.
  // Sliced rather than shifted-and-truncated: `(addr - BASE) >> 2` is a 32-bit
  // expression assigned to AW bits, which is a WIDTHTRUNC warning and, more to
  // the point, hides exactly which bits are being dropped.
  //
  // Two groups of bits are dropped on purpose, so UNUSEDSIGNAL is scoped to
  // these two declarations: the low two bits, because alignment is the LSU's
  // problem and not the array's, and everything above AW+1, because the memory
  // aliases rather than faults.
  /* verilator lint_off UNUSEDSIGNAL */
  wire [31:0] off_a = addr_a - BASE;
  wire [31:0] off_b = addr_b - BASE;
  /* verilator lint_on UNUSEDSIGNAL */
  wire [AW-1:0] wa_a = off_a[AW+1:2];
  wire [AW-1:0] wa_b = off_b[AW+1:2];

  always_ff @(posedge clk) begin
    rdata_a <= mem[wa_a];
  end

  always_ff @(posedge clk) begin
    rdata_b <= mem[wa_b];
    if (be_b[0]) mem[wa_b][7:0]   <= wdata_b[7:0];
    if (be_b[1]) mem[wa_b][15:8]  <= wdata_b[15:8];
    if (be_b[2]) mem[wa_b][23:16] <= wdata_b[23:16];
    if (be_b[3]) mem[wa_b][31:24] <= wdata_b[31:24];
  end

endmodule

`default_nettype wire
