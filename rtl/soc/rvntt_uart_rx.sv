// Minimal UART receiver, 8 data bits, no parity, 1 stop bit.  One integer baud
// divisor, no oversampling clock, no FIFO.  Resynchronises on every start bit
// and samples each bit at its midpoint.  One byte of holding register: `valid`
// stays high until `ack`; a byte arriving while the previous one is unread
// sets the sticky `overrun` (cleared by `overrun_clr`; set beats clear) and is
// dropped.  A framing error drops the byte.
`default_nettype none

module rvntt_uart_rx #(
    parameter int CLK_HZ = 75_000_000,
    parameter int BAUD   = 115_200
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        rx,          // from uart_txd_in (A9), host drives
    output logic [7:0] data,
    output logic       valid,       // held until ack
    input  wire        ack,         // pulse to consume `data`
    output logic       overrun,     // sticky until overrun_clr
    input  wire        overrun_clr  // pulse to clear `overrun`
);
  localparam int DIVISOR = CLK_HZ / BAUD;
  localparam int DIV_W   = $clog2(DIVISOR + 1);

  // Two-flop synchroniser: rx is asynchronous to clk.
  logic [1:0] sync_q;
  wire        rx_s = sync_q[1];
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) sync_q <= 2'b11;    // idle line is high
    else        sync_q <= {sync_q[0], rx};
  end

  typedef enum logic [1:0] {R_IDLE, R_START, R_DATA, R_STOP} state_e;
  state_e           state_q;
  logic [DIV_W-1:0] div_q;
  logic [2:0]       bit_q;
  logic [7:0]       sh_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state_q <= R_IDLE;
      div_q   <= '0;
      bit_q   <= '0;
      sh_q    <= '0;
      data    <= '0;
      valid   <= 1'b0;
      overrun <= 1'b0;
    end else begin
      if (ack)         valid   <= 1'b0;
      if (overrun_clr) overrun <= 1'b0;

      unique case (state_q)
        R_IDLE: begin
          // Falling edge = putative start bit; re-check after half a bit.
          if (!rx_s) begin
            div_q   <= DIV_W'(DIVISOR / 2);
            state_q <= R_START;
          end
        end
        R_START: begin
          if (div_q == '0) begin
            if (!rx_s) begin
              // Genuine start bit; sample one bit time apart from here.
              div_q   <= DIV_W'(DIVISOR - 1);
              bit_q   <= '0;
              state_q <= R_DATA;
            end else begin
              state_q <= R_IDLE;      // glitch
            end
          end else begin
            div_q <= div_q - DIV_W'(1);
          end
        end
        R_DATA: begin
          if (div_q == '0) begin
            sh_q  <= {rx_s, sh_q[7:1]};      // LSB first
            div_q <= DIV_W'(DIVISOR - 1);
            if (bit_q == 3'd7) state_q <= R_STOP;
            else               bit_q   <= bit_q + 3'd1;
          end else begin
            div_q <= div_q - DIV_W'(1);
          end
        end
        R_STOP: begin
          if (div_q == '0) begin
            state_q <= R_IDLE;
            // A framing error (stop bit low) drops the byte.
            if (rx_s) begin
              if (valid && !ack) begin
                overrun <= 1'b1;
              end else begin
                data  <= sh_q;
                valid <= 1'b1;
              end
            end
          end else begin
            div_q <= div_q - DIV_W'(1);
          end
        end
        default: state_q <= R_IDLE;
      endcase
    end
  end

endmodule

`default_nettype wire
