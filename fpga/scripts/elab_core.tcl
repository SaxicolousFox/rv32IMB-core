# ============================================================================
# Vivado elaboration check for the core sources: synth_design -rtl, the
# cheapest thing that exercises Vivado's full SystemVerilog front end, which
# disagrees with Verilator often enough on packages, structs and array
# initialisation to be worth running.
#
# Run from the staging directory:
#   vivado -mode batch -source elab_core.tcl -tclargs <top>
# ============================================================================

set PART xc7a100tcsg324-1
set TOP  [lindex $argv 0]
if {$TOP eq ""} { set TOP rvntt_regfile }

puts "=== rvntt A1: Vivado elaboration of $TOP for $PART ==="
puts "=== Vivado [version -short] ==="

# rv32i_pkg.sv must be read before anything that imports it.
set pkgs [glob -nocomplain rtl/rv32i_pkg.sv]
set rest [lsort [glob rtl/*.sv]]
foreach p $pkgs { set rest [lsearch -inline -all -not -exact $rest $p] }
read_verilog -sv [concat $pkgs $rest]

# `include "soc_clk.svh" resolves against these.
set_property include_dirs [list [pwd] [pwd]/rtl] [current_fileset]

# Elaborate only.  -rtl stops after the RTL-level netlist, which is where SV
# front-end errors surface, and takes seconds rather than minutes.
synth_design -rtl -top $TOP -part $PART

# Vivado reports many front-end problems as CRITICAL WARNING and continues;
# fail explicitly.
set crit [get_msg_config -severity "CRITICAL WARNING" -count]
set errs [get_msg_config -severity "ERROR" -count]
puts "=== elaboration messages: $errs error(s), $crit critical warning(s) ==="
if {$errs > 0 || $crit > 0} {
  puts "ELAB_FAIL: $TOP"
  exit 1
}

puts "ELAB_OK: $TOP"
exit 0
