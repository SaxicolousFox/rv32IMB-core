// Minimal UART receiver, 8 data bits, no parity, 1 stop bit.
//
// Mirror image of rvntt_uart_tx and deliberately the same shape: one integer
// baud divisor, no oversampling clock, no FIFO.  The receiver resynchronises on
// every start bit and then samples each bit at its MIDPOINT, which is what buys
// the tolerance -- with a 0.006% divisor error at 75 MHz the sample point has
// drifted by well under a percent of a bit time by the stop bit.
//
// Sampling at the midpoint rather than the edge is the whole design.  Sampling
// on the nominal bit boundary would put the sample exactly where the line is
// transitioning, and the receiver would work on a testbench with ideal edges and
// fail on a real FTDI with any skew at all.
//
// No FIFO, one byte of holding register: `valid` stays high until `ack`.  A byte
// that arrives while the previous one is unread sets `overrun` and is dropped,
// rather than silently replacing it -- software polling at CPU speed will never
// see this, but a wrong baud rate makes it fire constantly, which is a much
// better symptom than garbage characters.
//
// `overrun` is sticky and is cleared only by `overrun_clr`.  The clear lives
// here rather than in the bus block because a sticky flag whose clear is
// somewhere else is how you get a flag that latches once and never re-arms --
// which is exactly what the first version of this design did, with a comment
// claiming otherwise.  Set beats clear within a cycle, so an overrun coincident
// with its own acknowledgement is reported rather than lost.
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

  // Two-flop synchroniser: rx is asynchronous to clk by construction (it comes
  // from the host's clock, not ours).  Without this a metastable sample can
  // propagate into the state machine and corrupt a whole frame.
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
          // Falling edge = putative start bit.  Wait half a bit and re-check,
          // so a glitch on the line does not start a frame.
          if (!rx_s) begin
            div_q   <= DIV_W'(DIVISOR / 2);
            state_q <= R_START;
          end
        end
        R_START: begin
          if (div_q == '0) begin
            if (!rx_s) begin
              // Genuine start bit; from here sample one full bit time apart,
              // which lands us in the middle of each data bit.
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
            // A framing error (stop bit low) drops the byte.  Accepting it
            // anyway would turn a baud mismatch into plausible-looking data.
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
