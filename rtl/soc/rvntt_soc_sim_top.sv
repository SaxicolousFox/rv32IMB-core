// ============================================================================
// rvntt_soc_sim_top -- rvntt_soc_top with simulation-sized constants.
//
// The real top, unmodified, with CORE_HZ 4 MHz (34 cycles per UART bit, enough
// to represent a realistic baud mismatch), a short heartbeat, the simulation
// image, and ENTROPY_STUB=1.  RAM_WORDS is deliberately not shrunk: the stack
// sits at the top of the 128 KB array and a smaller memory aliases it onto the
// program.
// ============================================================================
`default_nettype none

module rvntt_soc_sim_top (
    input  wire        CLK100MHZ,
    input  wire        ck_rst,
    output wire [3:0]  led,
    output wire        led0_r,
    output wire        led0_g,
    output wire        led0_b,
    output wire        uart_rxd_out,
    input  wire        uart_txd_in,
    input  wire [3:0]  sw,
    input  wire [3:0]  btn
);
  rvntt_soc_top #(
      .CORE_HZ       (4_000_000),
      .HEARTBEAT_DIV (3_000),
      // The ring oscillator's combinational loop cannot settle under Verilator.
      .ENTROPY_STUB  (1'b1),
      .INIT_FILE     ("soc_sim.mem")
  ) u_soc (
      .CLK100MHZ (CLK100MHZ), .ck_rst (ck_rst),
      .led (led), .led0_r (led0_r), .led0_g (led0_g), .led0_b (led0_b),
      .uart_rxd_out (uart_rxd_out), .uart_txd_in (uart_txd_in),
      .sw (sw), .btn (btn)
  );
endmodule

`default_nettype wire
