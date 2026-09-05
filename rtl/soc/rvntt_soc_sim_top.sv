// ============================================================================
// rvntt_soc_sim_top -- rvntt_soc_top with simulation-sized constants.
//
// It instantiates THE REAL TOP, unmodified, and changes only three things:
//
//   CORE_HZ        4 MHz, so the UART baud divisor is 34 cycles per bit instead
//                  of 634.  It is not smaller for a reason: at 8 cycles per bit
//                  a realistic BAUD MISMATCH is not representable, and without
//                  one the receiver's mid-bit sampling cannot be distinguished
//                  from sampling on the bit boundary -- both are correct against
//                  a host whose edges are perfect.  34 cycles lets tb_soc.cpp
//                  transmit ~3% slow, which is what the receiver actually claims
//                  to tolerate and what makes that claim testable.
//   HEARTBEAT_DIV  small enough that the heartbeat toggles inside the run --
//                  at the real divisor no eye and no testbench would ever see
//                  an edge.
//   INIT_FILE      the simulation image, built from the same C source with a
//                  short inter-block delay.
//
// RAM_WORDS is DELIBERATELY NOT shrunk.  An earlier version used 4096 words to
// keep the zeroing loop short, and the stack pointer -- which the linker puts at
// the top of the 128 KB array -- aliased straight back onto the program, because
// rvntt_ram drops the high address bits rather than faulting.  Simulating a
// smaller memory than the hardware has is precisely how you get a testbench that
// passes over a bug the board would hit.
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
      // A29.  THE ONE OVERRIDE THAT MAKES THIS SIMULATABLE.  rvntt_soc_top
      // defaults to the real ring oscillator because it is the bitstream's
      // top; a combinational loop in a Verilator build reports
      // DIDNOTCONVERGE after 10 000 settle attempts and takes every SoC
      // simulation with it.
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
