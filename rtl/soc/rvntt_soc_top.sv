// ============================================================================
// rvntt_soc_top -- the synthesisable top level for the Arty A7-100T.
//
//   rvntt_core  +  rvntt_ram (128 KB, true dual port)  +  rvntt_mmio (UART, GPIO)
//
// One memory array holds program and data, so the ELF that runs on Spike and
// in cosimulation is the same bytes that go into the bitstream.
//
// LEDs: LD4-LD7 are GPIO_OUT (software's); the RGB LD0 is the hardware's:
//   green  heartbeat, ~1 Hz, core clock domain (dark: MMCM never locked)
//   blue   solid once any instruction has retired
//   red    solid if dbg_unsupported ever fired (an illegal instruction retired)
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
    // 0: this is the bitstream's top, so the real ring oscillator is used.
    // rvntt_soc_sim_top overrides it to 1 (Verilator cannot settle the loop).
    parameter bit          ENTROPY_STUB = 1'b0,
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

  // Asynchronous assert, synchronous release, gated on MMCM lock.
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

  // The stub bit is driven by a deterministic LFSR so a simulation build
  // (ENTROPY_STUB=1) has a live source rather than a stuck one; with the ring
  // it is dead logic and Vivado prunes it.
  /* verilator lint_off PROCASSINIT */
  logic [15:0] stub_lfsr_q = 16'hACE1;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk_core or negedge rst_n_core) begin
    if (!rst_n_core) stub_lfsr_q <= 16'hACE1;
    else             stub_lfsr_q <= {stub_lfsr_q[14:0],
                                     stub_lfsr_q[15] ^ stub_lfsr_q[13] ^
                                     stub_lfsr_q[12] ^ stub_lfsr_q[10]};
  end

  rvntt_core #(.RESET_PC (RAM_BASE), .ENTROPY_STUB (ENTROPY_STUB)) u_core (
      .clk (clk_core), .rst_n (rst_n_core),
      .entropy_stub_bit (stub_lfsr_q[0]),
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
  // Switches and buttons are asynchronous to clk_core: two-flop synchronisers,
  // which is what makes the XDC's false paths on them correct.
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

  // Latched: both are single-cycle pulses no eye could see.
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

  // The remaining commit-trace fields are the cosimulation harness's.
  wire _unused = &{1'b0, commit_pc, commit_insn, commit_wdata,
                   commit_reg_write, commit_rd, mmcm_locked};

endmodule

`default_nettype wire
