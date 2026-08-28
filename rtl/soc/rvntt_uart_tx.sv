// Minimal UART transmitter, 8 data bits, no parity, 1 stop bit.
//
// Baud generation is a simple integer divisor.  At 75 MHz / 115200 the divisor
// is 651.04 -> 651, giving 115207 baud, a 0.006% error.  UART tolerates roughly
// 2% accumulated over 10 bit times, so this has enormous margin.
`default_nettype none

module rvntt_uart_tx #(
    parameter int CLK_HZ = 75_000_000,
    parameter int BAUD   = 115_200
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  data,
    input  wire        valid,      // pulse to send `data`
    output logic       ready,      // high when idle and able to accept
    output logic       tx          // to uart_rxd_out (D10)
);
  localparam int DIVISOR   = CLK_HZ / BAUD;
  localparam int DIV_W     = $clog2(DIVISOR);
  localparam int BIT_COUNT = 10;                 // start + 8 data + stop

  logic [DIV_W-1:0]           div_q;
  logic [3:0]                 bit_q;
  logic [BIT_COUNT-1:0]       shift_q;
  logic                       busy_q;

  assign ready = ~busy_q;

  wire baud_tick = (div_q == DIV_W'(DIVISOR - 1));

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      div_q   <= '0;
      bit_q   <= '0;
      shift_q <= '1;          // idle line is high
      busy_q  <= 1'b0;
      tx      <= 1'b1;
    end else if (!busy_q) begin
      tx    <= 1'b1;
      div_q <= '0;
      if (valid) begin
        // LSB-first: stop(1), data[7:0], start(0)
        shift_q <= {1'b1, data, 1'b0};
        bit_q   <= '0;
        busy_q  <= 1'b1;
      end
    end else begin
      if (baud_tick) begin
        div_q   <= '0;
        tx      <= shift_q[0];
        shift_q <= {1'b1, shift_q[BIT_COUNT-1:1]};
        bit_q   <= bit_q + 4'd1;
        if (bit_q == 4'(BIT_COUNT - 1)) busy_q <= 1'b0;
      end else begin
        div_q <= div_q + DIV_W'(1);
      end
    end
  end

`ifdef FORMAL
  // The line must be idle-high whenever the transmitter is not busy.
  always_comb if (!busy_q && rst_n) assert (tx == 1'b1 || !ready);
`endif

endmodule

`default_nettype wire
