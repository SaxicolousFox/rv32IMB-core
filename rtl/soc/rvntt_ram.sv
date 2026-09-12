// ============================================================================
// rvntt_ram -- true dual-port synchronous RAM, the core's whole memory system.
//
// Port A is instruction fetch (read only), port B is load/store with byte
// enables.  Each port's address is registered inside the RAM, so its output
// register is a pipeline register: port A's address comes from the PC, so
// rdata_a is the instruction during ID; port B's comes from the EX address
// adder, so rdata_b is valid during MEM.  Byte addresses in the ELF's space
// (BASE = 0x80000000); the low two bits are ignored, and everything above the
// array aliases rather than faults.
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

  // Zero first, then $readmemh, so uncovered words are zero rather than X
  // (as on a real FPGA).  An `initial` loop, because Yosys does not parse
  // `'{default: '0}`.
  initial begin
    for (int i = 0; i < WORDS; i++) mem[i] = 32'h0;
    if (INIT_FILE != "") $readmemh(INIT_FILE, mem);
  end

  // Byte address -> word index, sliced so the dropped bits are explicit: the
  // low two (alignment is the LSU's problem) and everything above AW+1.
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
