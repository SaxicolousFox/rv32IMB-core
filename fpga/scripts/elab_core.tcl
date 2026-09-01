# ============================================================================
# Vivado elaboration check for the Track A core sources.
#
# Plan A1's "Done when" requires the package to compile cleanly under BOTH
# Verilator --lint-only -Wall AND Vivado elaboration.  Those two disagree more
# often than people expect on SystemVerilog packages, structs and unpacked
# array initialisation -- Yosys already rejected `'{default: '0}` here, which
# Verilator accepted -- so the second half is not a formality.
#
# This runs synth_design -rtl (elaborate only, no optimisation or mapping),
# which is the cheapest thing that exercises Vivado's full SV front end.
#
# Run from the staging directory:
#   vivado -mode batch -source elab_core.tcl -tclargs <top>
# ============================================================================

set PART xc7a100tcsg324-1
set TOP  [lindex $argv 0]
if {$TOP eq ""} { set TOP rvntt_regfile }

puts "=== rvntt A1: Vivado elaboration of $TOP for $PART ==="
puts "=== Vivado [version -short] ==="

# Order matters for packages: rv32i_pkg.sv must be read before anything that
# imports it.  glob alone does not guarantee that, so the package is listed
# explicitly first.
set pkgs [glob -nocomplain rtl/rv32i_pkg.sv]
set rest [lsort [glob rtl/*.sv]]
foreach p $pkgs { set rest [lsearch -inline -all -not -exact $rest $p] }
read_verilog -sv [concat $pkgs $rest]

# `include "soc_clk.svh" resolves against these, not against the file's own
# directory.  Both are listed because the staging script drops the generated
# headers in each.
set_property include_dirs [list [pwd] [pwd]/rtl] [current_fileset]

# Elaborate only.  -rtl stops after the RTL-level netlist, which is where SV
# front-end errors surface, and takes seconds rather than minutes.
synth_design -rtl -top $TOP -part $PART

# Vivado reports many front-end problems as CRITICAL WARNING rather than ERROR,
# and a batch run happily continues past them.  Fail the script explicitly so a
# clean exit code actually means something.
set crit [get_msg_config -severity "CRITICAL WARNING" -count]
set errs [get_msg_config -severity "ERROR" -count]
puts "=== elaboration messages: $errs error(s), $crit critical warning(s) ==="
if {$errs > 0 || $crit > 0} {
  puts "ELAB_FAIL: $TOP"
  exit 1
}

puts "ELAB_OK: $TOP"
exit 0
