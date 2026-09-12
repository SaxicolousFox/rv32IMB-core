// ============================================================================
// rvntt_trace -- WB-stage commit-log monitor.
//
// Emits one line per retiring instruction in the exact format of Spike's
// --log-commits, so the two logs diff directly:
//
//   core   0: 3 0x80000000 (0x00500513) x10 0x00000005
//   core   0: 3 0x80000048 (0x0000006f)
//
// The register field is left-justified in three columns (`x5 `, `x11`).
// Spike's `mem ...` suffixes and CSR writebacks are not emitted; the renderer
// in tb/cosim/commit_diff.py normalises them away on the Spike side.
//
// Simulation-only ($fopen/$fwrite), so it lives under tb/.
// ============================================================================
`default_nettype none

module rvntt_trace (
    input  wire        clk,
    // No rst_n port: commit_valid is already low throughout reset, and reading
    // rst_n synchronously here would be a SYNCASYNCNET warning.
    input  wire        commit_valid,
    input  wire [31:0] commit_pc,
    input  wire [31:0] commit_insn,
    input  wire        commit_reg_write,
    input  wire [4:0]  commit_rd,
    input  wire [31:0] commit_wdata
);

  integer fd = 0;
  string  fname;

  initial begin
    if ($value$plusargs("trace_file=%s", fname)) begin
      fd = $fopen(fname, "w");
      if (fd == 0) begin
        $display("rvntt_trace: could not open %s", fname);
        $finish;
      end
    end
  end

  final begin
    if (fd != 0) $fclose(fd);
  end

  // Padded here rather than with `%-3s`: Verilator's `-` flag leaks into the
  // following conversion and the value comes out space-filled.  A function
  // rather than a temporary, to avoid a BLKSEQ warning in always_ff.
  function automatic string rd_name(input logic [4:0] r);
    return (r < 5'd10) ? $sformatf("x%0d ", r) : $sformatf("x%0d", r);
  endfunction

  always_ff @(posedge clk) begin
    if (commit_valid && fd != 0) begin
      if (commit_reg_write)
        $fwrite(fd, "core   0: 3 0x%08x (0x%08x) %s 0x%08x\n",
                commit_pc, commit_insn, rd_name(commit_rd), commit_wdata);
      else
        $fwrite(fd, "core   0: 3 0x%08x (0x%08x)\n", commit_pc, commit_insn);
    end
  end

endmodule

`default_nettype wire
