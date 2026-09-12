// BRAM instantiation + readback self-test: proves Vivado infers a block RAM
// from this coding style and that $readmemh initialisation survives into the
// bitstream.  The checksum rotates before accumulating, so it is order
// sensitive.
`default_nettype none

module rvntt_bram_selftest #(
    parameter         INIT_FILE = "bram_init.mem",
    parameter int     DEPTH     = 256,
    parameter logic [31:0] EXPECTED = 32'h0
) (
    input  wire         clk,
    input  wire         rst_n,
    output logic        done,
    output logic        pass,
    output logic [31:0] checksum
);
  localparam int AW = $clog2(DEPTH);

  (* ram_style = "block" *) logic [31:0] mem [0:DEPTH-1];

  initial begin : init_mem
    $readmemh(INIT_FILE, mem);
  end

  localparam int CW = AW + 1;                 // wide enough to hold DEPTH itself

  logic [CW-1:0] raddr_q;     // address being issued
  logic [CW-1:0] nacc_q;      // how many words have been accumulated
  logic [31:0]   rdata_q;
  logic          rvalid_q;    // rdata_q holds a valid word this cycle

  // Synchronous read -- this is the shape that infers a true BRAM.
  always_ff @(posedge clk) begin
    rdata_q <= mem[raddr_q[AW-1:0]];
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      raddr_q  <= '0;
      nacc_q   <= '0;
      rvalid_q <= 1'b0;
      done     <= 1'b0;
      checksum <= '0;
    end else if (!done) begin
      // Data for raddr_q lands in rdata_q on the following cycle.
      rvalid_q <= (raddr_q < CW'(DEPTH));
      if (raddr_q < CW'(DEPTH)) raddr_q <= raddr_q + CW'(1);

      if (rvalid_q) begin
        checksum <= {checksum[30:0], checksum[31]} + rdata_q;
        nacc_q   <= nacc_q + CW'(1);
        if (nacc_q == CW'(DEPTH - 1)) done <= 1'b1;
      end
    end
  end

  assign pass = done && (checksum == EXPECTED);

endmodule

`default_nettype wire
