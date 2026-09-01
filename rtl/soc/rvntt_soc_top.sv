// ============================================================================
// rvntt_soc_top -- A12's synthesisable top level for the Arty A7-100T.
//
//   rvntt_core  +  rvntt_ram (128 KB, dual port)  +  rvntt_mmio (UART, GPIO)
//
// Everything below the clocking was proven on this board by P0.5 and is reused
// unchanged: rvntt_clkgen, rvntt_sync_reset, rvntt_uart_tx, and the async-IO
// treatment in the XDC.  What is new is the core, the memory map and the
// receiver.
//
// ONE MEMORY, NOT TWO.  Plan A12 asks for a 64 KB instruction BRAM and a 64 KB
// data BRAM; this is a single 128 KB TRUE DUAL PORT array instead, which is
// what plan section 1.4 actually specifies ("Harvard-style, both from the same
// physical BRAM array via true dual-port") and what rvntt_ram already
// implements.  It matters beyond tidiness: one array means one image, so the
// ELF that runs on Spike and in the A5 cosimulation is the SAME bytes that go
// into the bitstream.  Two arrays would need a second decoder, a split image,
// and a way for them to disagree.
//
// LED semantics -- the four green LEDs are software's, the RGB LED is the
// hardware's, so a dead program still leaves a diagnosis on the board:
//
//   LD4-LD7  led[3:0]   GPIO_OUT register, entirely software-controlled
//   LD0 green           heartbeat, ~1 Hz, core clock domain
//                       -> dark means the MMCM never locked or the core clock
//                          is stopped; nothing else on the board is meaningful
//   LD0 blue            solid once ANY instruction has retired
//                       -> green blinking but blue dark = clock fine, CPU not
//                          executing (bad reset, empty memory, bad RESET_PC)
//   LD0 red             solid if dbg_unsupported ever fired
//                       -> an illegal or Xkntt instruction retired.  A9 makes
//                          illegal unreachable, so red is a real defect, not a
//                          program error.
//
// The RGB LEDs are driven statically and are BRIGHT.  That is deliberate: this
// is a bring-up indicator that has to be readable across a desk.
// ============================================================================
`default_nettype none
`include "soc_clk.svh"

module rvntt_soc_top #(
    parameter int          CORE_HZ   = `SOC_CORE_HZ,
    parameter real         MMCM_MULT = `SOC_MMCM_MULT,
    parameter int          MMCM_DIV  = `SOC_MMCM_DIV,
    parameter real         MMCM_OUT  = `SOC_MMCM_OUT,
    parameter int          BAUD      = `SOC_BAUD,
    parameter int          RAM_WORDS = 32768,               // 128 KB
    parameter logic [31:0] RAM_BASE  = 32'h8000_0000,
    parameter logic [31:0] MMIO_BASE = 32'h4000_0000,
    parameter string       INIT_FILE = "soc_init.mem",
    // Cycles between heartbeat toggles.  Overridden down in simulation, where
    // waiting 37.5 million cycles to see one edge is not an option.
    parameter int          HEARTBEAT_DIV = CORE_HZ / 2
) (
    input  wire        CLK100MHZ,      // E3
    input  wire        ck_rst,         // C2, active low pushbutton
    output wire [3:0]  led,            // H5 J5 T9 T10  (LD4..LD7)
    output wire        led0_r,         // E1
    output wire        led0_g,         // F6
    output wire        led0_b,         // G6
    output wire        uart_rxd_out,   // D10, FPGA drives / host receives
    input  wire        uart_txd_in,    // A9,  host drives / FPGA receives
    input  wire [3:0]  sw,             // A8 C11 C10 A10
    input  wire [3:0]  btn             // D9 C9 B9 B8
);
  // ---------------------------------------------------------------- clocking
  wire clk_core, mmcm_locked;

  rvntt_clkgen #(
      .MULT (MMCM_MULT), .DIVCLK (MMCM_DIV), .OUT_DIV (MMCM_OUT)
  ) u_clkgen (
      .clk_in100 (CLK100MHZ),
      .arst_n    (ck_rst),
      .clk_core  (clk_core),
      .locked    (mmcm_locked)
  );

  // Asynchronous assert, SYNCHRONOUS RELEASE, and gated on MMCM lock so the
  // core never sees an edge while the output frequency is still ramping.
  // Releasing asynchronously is the nondeterministic-bring-up failure plan A12
  // names by name: different flops leave reset on different edges, and the
  // pipeline comes up with a half-initialised state that is different every
  // power-on.
  wire rst_n_core;
  rvntt_sync_reset #(.STAGES(3)) u_rst_core (
      .clk (clk_core), .arst_n (ck_rst & mmcm_locked), .rst_n (rst_n_core));

  // ------------------------------------------------------------------- core
  wire [31:0] imem_addr, imem_rdata;
  wire [31:0] dmem_addr, dmem_wdata, dmem_rdata;
  wire [3:0]  dmem_be;

  wire        commit_valid;
  wire [31:0] commit_pc, commit_insn, commit_wdata;
  wire        commit_reg_write;
  wire [4:0]  commit_rd;
  wire        dbg_unsupported;

  rvntt_core #(.RESET_PC (RAM_BASE)) u_core (
      .clk (clk_core), .rst_n (rst_n_core),
      .imem_addr (imem_addr), .imem_rdata (imem_rdata),
      .dmem_addr (dmem_addr), .dmem_wdata (dmem_wdata),
      .dmem_be (dmem_be),     .dmem_rdata (dmem_rdata),
      .commit_valid (commit_valid), .commit_pc (commit_pc),
      .commit_insn (commit_insn),   .commit_reg_write (commit_reg_write),
      .commit_rd (commit_rd),       .commit_wdata (commit_wdata),
      .dbg_unsupported (dbg_unsupported)
  );

  // ----------------------------------------------------------------- memory
  wire [3:0]  ram_be;
  wire [31:0] ram_rdata;

  rvntt_ram #(
      .WORDS (RAM_WORDS), .BASE (RAM_BASE), .INIT_FILE (INIT_FILE)
  ) u_ram (
      .clk (clk_core),
      .addr_a (imem_addr), .rdata_a (imem_rdata),
      .addr_b (dmem_addr), .wdata_b (dmem_wdata),
      .be_b (ram_be),      .rdata_b (ram_rdata)
  );

  // ------------------------------------------------------------ peripherals
  // Switches and buttons are asynchronous to clk_core -- a slide switch and a
  // tactile button have no clock at all -- so they get the same two-flop
  // treatment rvntt_uart_rx gives its serial input.  Without it the MMIO read
  // register is the first flop to see the pin, and a switch flipped near a
  // clock edge can put a metastable value straight into the value the CPU
  // reads.  The XDC cuts these paths, which is only correct BECAUSE this
  // exists; false-pathing an unsynchronised input is the actual bug and looks
  // identical in the constraint file.
  logic [3:0] sw_meta_q,  sw_sync_q;
  logic [3:0] btn_meta_q, btn_sync_q;
  always_ff @(posedge clk_core or negedge rst_n_core) begin
    if (!rst_n_core) begin
      sw_meta_q  <= 4'h0; sw_sync_q  <= 4'h0;
      btn_meta_q <= 4'h0; btn_sync_q <= 4'h0;
    end else begin
      sw_meta_q  <= sw;   sw_sync_q  <= sw_meta_q;
      btn_meta_q <= btn;  btn_sync_q <= btn_meta_q;
    end
  end

  wire [3:0] gpio_out;

  rvntt_mmio #(
      .CLK_HZ (CORE_HZ), .BAUD (BAUD),
      .RAM_BASE (RAM_BASE), .MMIO_BASE (MMIO_BASE)
  ) u_mmio (
      .clk (clk_core), .rst_n (rst_n_core),
      .dmem_addr (dmem_addr), .dmem_wdata (dmem_wdata),
      .dmem_be (dmem_be),     .dmem_rdata (dmem_rdata),
      .ram_be (ram_be),       .ram_rdata (ram_rdata),
      .uart_rx_pin (uart_txd_in), .uart_tx_pin (uart_rxd_out),
      .gpio_out (gpio_out),
      .sw (sw_sync_q), .btn (btn_sync_q)
  );

  // --------------------------------------------------------- status indicators
  localparam int HB_W = $clog2(HEARTBEAT_DIV + 1);
  logic [HB_W-1:0] hb_cnt;
  logic            hb_q;
  always_ff @(posedge clk_core or negedge rst_n_core) begin
    if (!rst_n_core) begin
      hb_cnt <= '0;
      hb_q   <= 1'b0;
    end else if (hb_cnt == HB_W'(HEARTBEAT_DIV - 1)) begin
      hb_cnt <= '0;
      hb_q   <= ~hb_q;
    end else begin
      hb_cnt <= hb_cnt + HB_W'(1);
    end
  end

  // Latching rather than following: both of these are single-cycle pulses at
  // 75 MHz, which no eye can see.  A latch turns "it happened once, ever" into
  // something readable from across the room, and that is the question being
  // asked in both cases.
  logic alive_q, err_q;
  always_ff @(posedge clk_core or negedge rst_n_core) begin
    if (!rst_n_core) begin
      alive_q <= 1'b0;
      err_q   <= 1'b0;
    end else begin
      if (commit_valid)    alive_q <= 1'b1;
      if (dbg_unsupported) err_q   <= 1'b1;
    end
  end

  assign led    = gpio_out;
  assign led0_g = hb_q;
  assign led0_b = alive_q;
  assign led0_r = err_q;

  // The remaining commit-trace fields are the cosimulation harness's, not the
  // board's; bringing them out would turn them into pins.
  wire _unused = &{1'b0, commit_pc, commit_insn, commit_wdata,
                   commit_reg_write, commit_rd, mmcm_locked};

endmodule

`default_nettype wire
