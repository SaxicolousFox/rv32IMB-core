// ============================================================================
// rvntt_mmio -- address decoder and peripheral block for the A12 SoC.
//
// Sits on rvntt_core's data port, which is NOT a handshake bus.  The core drives
// dmem_addr / dmem_be / dmem_wdata combinationally from EX and expects read data
// exactly one cycle later, in MEM, because rvntt_ram's output register IS the
// pipeline register (see rtl/soc/rvntt_ram.sv).  Everything here is built to
// that fixed timing: the read mux is registered on the same edge the RAM
// registers its output, so both sources land in MEM together and no arbitration
// or wait state exists to get wrong.
//
// ---------------------------------------------------------------------------
// READS HERE HAVE NO SIDE EFFECTS, AND THAT IS A HARD REQUIREMENT.
// ---------------------------------------------------------------------------
// dmem_addr is the raw combinational ALU result of whatever is in EX -- it is
// driven every cycle, for every instruction, load or not.  A load is not
// distinguishable from an `add` whose result happens to equal a peripheral
// address, because the core signals "read" as be == 0, which is also what a
// non-memory instruction presents.  So a read-to-pop FIFO would be emptied by
// arithmetic, intermittently, in a way that would look like a UART bug.
//
// Hence: every register here is consumed by an explicit WRITE.  Writes are safe
// to qualify, because be != 0 only for a real store, and the trap invariant
// (rtl/core/CLAUDE.md, A9) guarantees a faulting store is already suppressed in
// EX before its byte enables ever reach this block.
//
// The alternative -- adding a dmem_re output to rvntt_core -- was rejected: it
// changes a port list that four testbenches, the RVFI wrapper and the mutation
// harness all wire up, to buy a convenience the software does not need.
// ---------------------------------------------------------------------------
//
// Register map, word offsets from MMIO_BASE.  WORD ACCESS ONLY (use `sw`/`lw`);
// a sub-word store would place its data somewhere in wdata that this block does
// not look at.
//
//   0x00  UART_TX    W  [7:0] byte to transmit.  Poll UART_STAT.tx_ready first;
//                       writing while busy is dropped, not queued.
//   0x04  UART_STAT  R  {29'b0, rx_overrun, rx_valid, tx_ready}
//                    W  write bit 2 set to clear the sticky rx_overrun flag
//   0x08  UART_RX    R  [7:0] last received byte (non-destructive)
//                    W  any value consumes it, clearing rx_valid
//   0x0C  GPIO_OUT   RW [3:0] -> led[3:0]
//   0x10  GPIO_IN    R  {24'b0, btn[3:0], sw[3:0]}
// ============================================================================
`default_nettype none

module rvntt_mmio #(
    parameter int          CLK_HZ    = 75_000_000,
    parameter int          BAUD      = 115_200,
    parameter logic [31:0] RAM_BASE  = 32'h8000_0000,
    parameter logic [31:0] MMIO_BASE = 32'h4000_0000
) (
    input  wire         clk,
    input  wire         rst_n,

    // Core data port.
    input  wire  [31:0] dmem_addr,
    input  wire  [31:0] dmem_wdata,
    input  wire  [3:0]  dmem_be,
    output logic [31:0] dmem_rdata,

    // rvntt_ram port B.  ram_be is dmem_be gated to the RAM region: the array
    // aliases rather than faults, so an ungated MMIO store would ALSO land in
    // RAM at (addr - RAM_BASE) truncated, silently corrupting the program.
    output logic [3:0]  ram_be,
    input  wire  [31:0] ram_rdata,

    // Pins.
    input  wire         uart_rx_pin,
    output wire         uart_tx_pin,
    output logic [3:0]  gpio_out,
    input  wire  [3:0]  sw,
    input  wire  [3:0]  btn
);
  // ------------------------------------------------------------------ decode
  // Decoded on the top nibble only.  The regions are 256 MB apart and nothing
  // else is mapped, so a finer decode would only add a way to be wrong; an
  // access to neither region reads 0 and writes nowhere.
  wire is_ram  = (dmem_addr[31:28] == RAM_BASE[31:28]);
  wire is_mmio = (dmem_addr[31:28] == MMIO_BASE[31:28]);

  wire       we  = is_mmio && (dmem_be != 4'b0000);
  wire [3:0] sel = dmem_addr[5:2];          // 16 word registers is ample

  localparam logic [3:0] R_UART_TX   = 4'h0;
  localparam logic [3:0] R_UART_STAT = 4'h1;
  localparam logic [3:0] R_UART_RX   = 4'h2;
  localparam logic [3:0] R_GPIO_OUT  = 4'h3;
  localparam logic [3:0] R_GPIO_IN   = 4'h4;

  always_comb ram_be = is_ram ? dmem_be : 4'b0000;

  // --------------------------------------------------------------- UART TX
  wire       tx_ready;
  wire       tx_valid = we && (sel == R_UART_TX);

  rvntt_uart_tx #(.CLK_HZ (CLK_HZ), .BAUD (BAUD)) u_tx (
      .clk (clk), .rst_n (rst_n),
      .data (dmem_wdata[7:0]), .valid (tx_valid),
      .ready (tx_ready), .tx (uart_tx_pin));

  // --------------------------------------------------------------- UART RX
  wire [7:0] rx_data;
  wire       rx_valid;
  wire       rx_overrun;
  wire       rx_ack     = we && (sel == R_UART_RX);
  // Writing UART_STAT with bit 2 set acknowledges the sticky overrun flag.
  wire       rx_ovr_clr = we && (sel == R_UART_STAT) && dmem_wdata[2];

  rvntt_uart_rx #(.CLK_HZ (CLK_HZ), .BAUD (BAUD)) u_rx (
      .clk (clk), .rst_n (rst_n),
      .rx (uart_rx_pin),
      .data (rx_data), .valid (rx_valid), .ack (rx_ack),
      .overrun (rx_overrun), .overrun_clr (rx_ovr_clr));


  // --------------------------------------------------------------- GPIO out
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)                            gpio_out <= 4'h0;
    else if (we && (sel == R_GPIO_OUT))    gpio_out <= dmem_wdata[3:0];
  end

  // ------------------------------------------------------------- read return
  logic [31:0] mmio_rdata_c;
  always_comb begin
    unique case (sel)
      R_UART_STAT: mmio_rdata_c = {29'b0, rx_overrun, rx_valid, tx_ready};
      R_UART_RX:   mmio_rdata_c = {24'b0, rx_data};
      R_GPIO_OUT:  mmio_rdata_c = {28'b0, gpio_out};
      R_GPIO_IN:   mmio_rdata_c = {24'b0, btn, sw};
      // UART_TX is write-only and everything else is unmapped.  Zero rather
      // than X: this is what a real bus returns, and an X here would propagate
      // through the pipeline in simulation and mask the actual mistake.
      default:     mmio_rdata_c = 32'h0;
    endcase
  end

  // One register deep, to match rvntt_ram's output register exactly.  The RAM
  // select is registered alongside the data so the mux decides with the SAME
  // cycle's address that produced the data -- selecting with the live address
  // would return RAM data for an MMIO read issued one cycle later.
  logic [31:0] mmio_rdata_q;
  logic        sel_ram_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mmio_rdata_q <= 32'h0;
      sel_ram_q    <= 1'b0;
    end else begin
      mmio_rdata_q <= mmio_rdata_c;
      sel_ram_q    <= is_ram;
    end
  end

  always_comb dmem_rdata = sel_ram_q ? ram_rdata : mmio_rdata_q;

  wire _unused = &{1'b0, dmem_addr[27:6], dmem_addr[1:0], dmem_wdata[31:8]};

endmodule

`default_nettype wire
