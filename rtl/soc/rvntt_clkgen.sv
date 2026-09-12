// MMCM clock generator: 100 MHz board oscillator -> the core clock.
//   VCO = 100 MHz * MULT / DIVCLK (600-1200 MHz on an Artix-7 -1)
//   clk = VCO / OUT_DIV
// Under Verilator the Xilinx primitives do not exist, so a behavioural
// pass-through stands in.
`default_nettype none

module rvntt_clkgen #(
    // With an MMCM the divider is the timing constraint: Vivado derives the
    // generated clock from these.  Defaults give 75 MHz.
    parameter real MULT     = 9.000,     // CLKFBOUT_MULT_F
    parameter int  DIVCLK   = 1,         // DIVCLK_DIVIDE
    parameter real OUT_DIV  = 12.000     // CLKOUT0_DIVIDE_F
) (
    input  wire  clk_in100,      // E3, board oscillator
    input  wire  arst_n,         // async reset in (button, active low)
    output wire  clk_core,       // MMCM output, 75 MHz
    output wire  locked
);

`ifdef VERILATOR
  // Behavioural stand-in: simulation runs the core domain at the input rate.
  assign clk_core = clk_in100;
  assign locked   = 1'b1;
  // Keep the parameters referenced in the build that cannot use them.
  wire _unused = &{1'b0, arst_n, (MULT != 0.0), (DIVCLK != 0), (OUT_DIV != 0.0)};
`else
  wire clk_fb_out, clk_fb_in, clk_core_raw;

  MMCME2_BASE #(
      .BANDWIDTH          ("OPTIMIZED"),
      .CLKIN1_PERIOD      (10.000),   // 100 MHz
      .DIVCLK_DIVIDE      (DIVCLK),
      .CLKFBOUT_MULT_F    (MULT),     // VCO = 100 MHz * MULT / DIVCLK
      .CLKOUT0_DIVIDE_F   (OUT_DIV),  // core clock = VCO / OUT_DIV
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

  // Feedback and output both through global buffers, or the MMCM compensates
  // for the wrong delay.
  BUFG u_fb_bufg   (.I(clk_fb_out),   .O(clk_fb_in));
  BUFG u_core_bufg (.I(clk_core_raw), .O(clk_core));
`endif

endmodule

`default_nettype wire
