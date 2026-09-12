// Emits a status line over UART about once a second:
//   rvntt blinky clk=75MHz bram=0xD76C0E8D PASS\r\n
// The checksum is printed so a failure on hardware shows the actual value.
`default_nettype none

module rvntt_uart_report #(
    parameter int CLK_HZ = 75_000_000,
    parameter int BAUD   = 115_200
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [31:0] sum,
    input  wire        sum_valid,
    input  wire        pass,
    output wire        tx
);
  // "rvntt blinky clk=75MHz bram=0x"
  localparam int PRE_LEN = 30;
  localparam logic [7:0] PRE [0:PRE_LEN-1] = '{
      "r","v","n","t","t"," ","b","l","i","n","k","y"," ",
      "c","l","k","=","7","5","M","H","z"," ",
      "b","r","a","m","=","0","x"};

  // " PASS\r\n" / " FAIL\r\n"
  localparam int SUF_LEN = 7;
  localparam logic [7:0] SUF_PASS [0:SUF_LEN-1] = '{" ","P","A","S","S",8'h0D,8'h0A};
  localparam logic [7:0] SUF_FAIL [0:SUF_LEN-1] = '{" ","F","A","I","L",8'h0D,8'h0A};

  typedef enum logic [2:0] {S_IDLE, S_PRE, S_HEX, S_SUF, S_WAIT} state_e;
  state_e state_q;

  logic [4:0]  idx_q;        // index within PRE (0..29) or SUF (0..6)
  logic [2:0]  nib_q;        // which nibble, 7 down to 0
  logic [31:0] sum_q;
  // Gap between lines; the counter is sized from the constant.
  localparam int GAP_CYCLES = CLK_HZ;                 // one second
  localparam int GAP_W      = $clog2(GAP_CYCLES + 1);
  logic [GAP_W-1:0] wait_q;

  logic [7:0] tx_data;
  logic       tx_valid;
  wire        tx_ready;

  rvntt_uart_tx #(.CLK_HZ (CLK_HZ), .BAUD (BAUD)) u_tx (
      .clk (clk), .rst_n (rst_n),
      .data (tx_data), .valid (tx_valid), .ready (tx_ready), .tx (tx));

  function automatic logic [7:0] hex_char(input logic [3:0] n);
    hex_char = (n < 4'd10) ? (8'h30 + 8'(n)) : (8'h41 + 8'(n) - 8'd10);
  endfunction

  wire [3:0] cur_nib = sum_q[{nib_q, 2'b00} +: 4];

  always_comb begin
    tx_data = 8'h20;
    unique case (state_q)
      S_PRE: tx_data = PRE[idx_q];
      S_HEX: tx_data = hex_char(cur_nib);
      S_SUF: tx_data = pass ? SUF_PASS[idx_q[2:0]] : SUF_FAIL[idx_q[2:0]];
      default: tx_data = 8'h20;
    endcase
  end

  assign tx_valid = tx_ready && (state_q == S_PRE || state_q == S_HEX || state_q == S_SUF);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state_q <= S_IDLE;
      idx_q   <= '0;
      nib_q   <= 3'd7;
      sum_q   <= '0;
      wait_q  <= '0;
    end else begin
      unique case (state_q)
        S_IDLE: begin
          // Wait for the self-test to finish so the printed value is meaningful.
          if (sum_valid) begin
            sum_q   <= sum;
            idx_q   <= '0;
            state_q <= S_PRE;
          end
        end
        S_PRE: if (tx_ready) begin
          if (idx_q == 5'(PRE_LEN - 1)) begin
            idx_q <= '0; nib_q <= 3'd7; state_q <= S_HEX;
          end else begin
            idx_q <= idx_q + 5'd1;
          end
        end
        S_HEX: if (tx_ready) begin
          if (nib_q == 3'd0) begin
            idx_q <= '0; state_q <= S_SUF;
          end else begin
            nib_q <= nib_q - 3'd1;
          end
        end
        S_SUF: if (tx_ready) begin
          if (idx_q == 5'(SUF_LEN - 1)) begin
            wait_q <= '0; state_q <= S_WAIT;
          end else begin
            idx_q <= idx_q + 5'd1;
          end
        end
        S_WAIT: begin
          // One line per second.
          if (wait_q == GAP_W'(GAP_CYCLES - 1)) begin
            sum_q   <= sum;
            idx_q   <= '0;
            state_q <= S_PRE;
          end else begin
            wait_q <= wait_q + GAP_W'(1);
          end
        end
        default: state_q <= S_IDLE;
      endcase
    end
  end

endmodule

`default_nettype wire
