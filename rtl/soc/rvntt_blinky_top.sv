// Blinky/UART/BRAM self-test top: exercises the XDC, the MMCM, the reset
// synchroniser, BRAM inference with $readmemh, the USB-UART pinout and the
// batch Tcl build, with no core.
//
//   led[0]  1 Hz, counted in the raw 100 MHz oscillator domain
//   led[1]  1 Hz, counted in the 75 MHz MMCM domain (must stay in lockstep
//           with led[0], which proves the MMCM ratio)
//   led[2]  MMCM locked
//   led[3]  BRAM self-test passed
`default_nettype none

module rvntt_blinky_top #(
    parameter int OSC_HZ  = 100_000_000,
    parameter int CORE_HZ =  75_000_000,
    parameter     INIT_FILE = "bram_init.mem"
) (
    input  wire        CLK100MHZ,     // E3
    input  wire        ck_rst,        // C2, active low
    output wire [3:0]  led,           // H5 J5 T9 T10
    output wire        uart_rxd_out   // D10 (FPGA drives, host receives)
);
`include "bram_expected.svh"

  // ---------------------------------------------------------------- clocking
  wire clk_core, mmcm_locked;

  rvntt_clkgen u_clkgen (
      .clk_in100 (CLK100MHZ),
      .arst_n    (ck_rst),
      .clk_core  (clk_core),
      .locked    (mmcm_locked)
  );

  // One reset per domain; the core domain additionally waits for MMCM lock.
  wire rst_n_osc, rst_n_core;

  rvntt_sync_reset #(.STAGES(3)) u_rst_osc (
      .clk (CLK100MHZ), .arst_n (ck_rst), .rst_n (rst_n_osc));

  rvntt_sync_reset #(.STAGES(3)) u_rst_core (
      .clk (clk_core), .arst_n (ck_rst & mmcm_locked), .rst_n (rst_n_core));

  // ------------------------------------------------- 1 Hz in each domain
  localparam int OSC_TOP  = OSC_HZ  / 2 - 1;   // toggle at 1 Hz -> 0.5 Hz square
  localparam int CORE_TOP = CORE_HZ / 2 - 1;

  logic [$clog2(OSC_HZ/2)-1:0]  osc_cnt;
  logic                         osc_tog;
  always_ff @(posedge CLK100MHZ or negedge rst_n_osc) begin
    if (!rst_n_osc) begin
      osc_cnt <= '0; osc_tog <= 1'b0;
    end else if (osc_cnt == $clog2(OSC_HZ/2)'(OSC_TOP)) begin
      osc_cnt <= '0; osc_tog <= ~osc_tog;
    end else begin
      osc_cnt <= osc_cnt + 1'b1;
    end
  end

  logic [$clog2(CORE_HZ/2)-1:0] core_cnt;
  logic                         core_tog;
  always_ff @(posedge clk_core or negedge rst_n_core) begin
    if (!rst_n_core) begin
      core_cnt <= '0; core_tog <= 1'b0;
    end else if (core_cnt == $clog2(CORE_HZ/2)'(CORE_TOP)) begin
      core_cnt <= '0; core_tog <= ~core_tog;
    end else begin
      core_cnt <= core_cnt + 1'b1;
    end
  end

  // ------------------------------------------------------------ BRAM selftest
  wire        bram_done, bram_pass;
  wire [31:0] bram_sum;

  rvntt_bram_selftest #(
      .INIT_FILE (INIT_FILE),
      .DEPTH     (BRAM_DEPTH),
      .EXPECTED  (BRAM_EXPECTED)
  ) u_bram (
      .clk (clk_core), .rst_n (rst_n_core),
      .done (bram_done), .pass (bram_pass), .checksum (bram_sum)
  );

  // ----------------------------------------------------------- UART reporter
  wire uart_tx_line;
  rvntt_uart_report #(.CLK_HZ (CORE_HZ)) u_report (
      .clk      (clk_core),
      .rst_n    (rst_n_core),
      .sum      (bram_sum),
      .sum_valid(bram_done),
      .pass     (bram_pass),
      .tx       (uart_tx_line)
  );

  assign uart_rxd_out = uart_tx_line;
  assign led = {bram_pass, mmcm_locked, core_tog, osc_tog};

endmodule

`default_nettype wire
