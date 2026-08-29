// ============================================================================
// rvntt_trace -- WB-stage commit-log monitor (plan A5).
//
// Emits one line per retiring instruction in the EXACT format Spike's
// --log-commits produces, so the two logs can be diffed directly:
//
//   core   0: 3 0x80000000 (0x00500513) x10 0x00000005
//   core   0: 3 0x80000014 (0x00b50633) x12 0x0000000c
//   core   0: 3 0x80000048 (0x0000006f)
//
// The spacing is not decorative and was taken from real Spike output rather
// than from the plan's prose.  The register field is LEFT-JUSTIFIED IN THREE
// COLUMNS -- `x5 ` and `x11` -- so a single-digit register is followed by two
// spaces before the value and a double-digit one by a single space.  Getting
// that wrong produces a log that looks right and diffs wrong on every line
// involving x0..x9.
//
// What this deliberately does NOT emit:
//
//   * `mem 0x<addr>` on loads and `mem 0x<addr> 0x<data>` on stores.  Spike
//     appends these; the plan's A5 format does not include them, and the
//     differ normalises them away on the Spike side.
//   * `c773_mtvec 0x...` style CSR writebacks.  There is no CSR file until A9.
//
// Both omissions are handled in ONE place -- the renderer in
// tb/cosim/commit_diff.py -- rather than by loosening the comparison, so a
// future CSR file only has to be taught to the renderer.
//
// Simulation-only: $fopen and $fwrite are not synthesisable, which is why this
// lives under tb/ and is never compiled into anything that reaches Vivado.
// ============================================================================
`default_nettype none

module rvntt_trace (
    input  wire        clk,
    // No rst_n port on purpose.  commit_valid is already low throughout reset
    // (mem_wb_q.valid resets to 0), so gating on rst_n here would add nothing
    // -- and reading it synchronously while rvntt_core uses it asynchronously
    // is a SYNCASYNCNET warning, i.e. a monitor introducing a lint problem into
    // a design that does not have one.
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

  // The register field is padded to three columns HERE rather than with a
  // `%-3s` format specifier.  Verilator's `-` flag leaks into the following
  // conversion: `"%-3s 0x%08x"` renders the value left-justified and
  // space-filled ("0x0       ") instead of zero-padded ("0x00000000"), so the
  // log looks almost right and fails to parse.  Building the field explicitly
  // sidesteps the whole question, and rd is only ever 0..31, so the padding is
  // a single comparison.
  // A function rather than a temporary: assigning to a variable inside
  // always_ff is a blocking assignment in a sequential process (BLKSEQ), which
  // is a legitimate warning even though this process is only a monitor.
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
