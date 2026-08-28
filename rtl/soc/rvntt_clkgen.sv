// MMCM clock generator: 100 MHz board oscillator -> CORE_HZ.
//
// Plan P0.5 wants the LED blink rate to *prove* the MMCM ratio, so the core
// clock is deliberately NOT 100 MHz -- a pass-through would prove nothing.
//
//   VCO   = 100 MHz * CLKFBOUT_MULT_F / DIVCLK_DIVIDE = 900 MHz   (in the
//           600-1200 MHz range required for an Artix-7 -1 speed grade)
//   clk   = VCO / CLKOUT0_DIVIDE_F = 900 / 12 = 75 MHz
//
// Under Verilator the Xilinx primitives do not exist, so a behavioural
// pass-through stands in; the RTL above it is identical in both cases.
`default_nettype none

module rvntt_clkgen (
    input  wire  clk_in100,      // E3, board oscillator
    input  wire  arst_n,         // async reset in (button, active low)
    output wire  clk_core,       // MMCM output, 75 MHz
    output wire  locked
);

`ifdef VERILATOR
  // Behavioural stand-in: simulation runs the core domain at the input rate.
  assign clk_core = clk_in100;
  assign locked   = 1'b1;
  wire _unused = &{1'b0, arst_n};
`else
  wire clk_fb_out, clk_fb_in, clk_core_raw;

  MMCME2_BASE #(
      .BANDWIDTH          ("OPTIMIZED"),
      .CLKIN1_PERIOD      (10.000),   // 100 MHz
      .DIVCLK_DIVIDE      (1),
      .CLKFBOUT_MULT_F    (9.000),    // VCO = 900 MHz
      .CLKOUT0_DIVIDE_F   (12.000),   // 900 / 12 = 75 MHz
      .CLKOUT0_DUTY_CYCLE (0.500),
      .CLKOUT0_PHASE      (0.000),
      .STARTUP_WAIT       ("FALSE")
  ) u_mmcm (
      .CLKIN1   (clk_in100),
      .CLKFBIN  (clk_fb_in),
      .CLKFBOUT (clk_fb_out),
      .CLKOUT0  (clk_core_raw),
      .CLKOUT0B (), .CLKOUT1 (), .CLKOUT1B (), .CLKOUT2 (), .CLKOUT2B (),
      .CLKOUT3  (), .CLKOUT3B (), .CLKOUT4 (), .CLKOUT5 (), .CLKOUT6 (),
      .CLKFBOUTB(),
      .LOCKED   (locked),
      .PWRDWN   (1'b0),
      .RST      (~arst_n)
  );

  // Feedback and output must both go through global buffers, or the MMCM
  // compensates for the wrong delay and the ratio is right but the phase is not.
  BUFG u_fb_bufg   (.I(clk_fb_out),   .O(clk_fb_in));
  BUFG u_core_bufg (.I(clk_core_raw), .O(clk_core));
`endif

endmodule

`default_nettype wire
