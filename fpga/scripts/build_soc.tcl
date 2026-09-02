# ============================================================================
# A12 -- non-project batch build of the RV32I SoC bitstream for the Arty A7-100T.
#
# Same shape as P0.5's build_blinky.tcl, which is the point: that script's flow
# is hardware-confirmed, so this one changes as little as possible.  What is new
# is the Fmax support -- the timing gate can be made non-fatal so a search
# iteration still produces reports for a failing period -- and two extra
# assertions about what synthesis actually did.
#
# Run from the staging directory (soc_init.mem must be in the cwd, because
# $readmemh resolves relative to it):
#   vivado -mode batch -source build_soc.tcl -tclargs <fail_on_neg> <want_bit>
#
#   fail_on_neg  1 (default) exit non-zero if WNS or WHS is negative
#                0 report and continue -- for the Fmax binary search
#   want_bit     1 (default) write a bitstream when timing is met
# ============================================================================

set PART   xc7a100tcsg324-1
set TOP    rvntt_soc_top
set OUTDIR [pwd]/out

set FAIL_ON_NEG 1
set WANT_BIT    1
if {[llength $argv] > 0} { set FAIL_ON_NEG [lindex $argv 0] }
if {[llength $argv] > 1} { set WANT_BIT    [lindex $argv 1] }

file mkdir $OUTDIR

puts "=== rvntt A12: building $TOP for $PART ==="
puts "=== Vivado [version -short] ==="

# ------------------------------------------------------------------ sources
# The package must be read before anything that references it.
set pkgs [glob -nocomplain rtl/rv32i_pkg.sv]
set rest [lsort [glob rtl/*.sv]]
foreach p $pkgs { set rest [lsearch -inline -all -not -exact $rest $p] }
read_verilog -sv [concat $pkgs $rest]

set_property include_dirs [list [pwd] [pwd]/rtl] [current_fileset]
read_xdc constraints/arty_a7_100t_soc.xdc

# ------------------------------------------------------------------ synthesis
synth_design -top $TOP -part $PART -include_dirs [list [pwd] [pwd]/rtl]
write_checkpoint -force $OUTDIR/post_synth.dcp
report_utilization -file $OUTDIR/post_synth_util.rpt

# Vivado reports a missing $readmemh file, an unconnected top-level port and
# several other things that produce a WORKING BUT WRONG bitstream as CRITICAL
# WARNING, then carries on.  P0.5's elaboration gate exists for the same reason;
# this is the synthesis-stage version of it.
set crit [get_msg_config -severity "CRITICAL WARNING" -count]
set errs [get_msg_config -severity "ERROR" -count]
puts "=== synthesis messages: $errs error(s), $crit critical warning(s) ==="
if {$errs > 0 || $crit > 0} {
  puts "SOC_FAIL: synthesis reported errors or critical warnings"
  exit 1
}

# The memory must be BLOCK RAM.  128 KB in fabric would not fit the device, and
# the failure mode without this check is a place-and-route that runs for an hour
# and then reports the design does not fit -- with no indication of why.
set nbram [llength [get_cells -hierarchical -filter {PRIMITIVE_TYPE =~ BMEM.*.*}]]
puts "=== inferred BRAM primitives: $nbram ==="
if {$nbram < 8} {
  puts "SOC_FAIL: memory was not inferred as block RAM ($nbram primitives)"
  exit 1
}

# ------------------------------------------------------- place and route
opt_design
place_design
phys_opt_design
route_design

write_checkpoint -force $OUTDIR/post_route.dcp
report_timing_summary -file $OUTDIR/post_route_timing.rpt
report_utilization    -file $OUTDIR/post_route_util.rpt
report_clock_utilization -file $OUTDIR/post_route_clock_util.rpt
report_drc            -file $OUTDIR/post_route_drc.rpt
# The worst setup path, with enough detail to NAME the critical path rather than
# just quote a number at it.  A12 asks for the path, not only the slack.
report_timing -max_paths 10 -nworst 10 -setup -path_type full_clock_expanded \
              -file $OUTDIR/post_route_critical.rpt

# --------------------------------------------------------------- the numbers
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]

# The core clock's period as VIVADO DERIVED IT from the MMCM, not as anyone
# believes it to be.  gen_soc_clk.py sets the frequency through the MMCM
# divider, so this is the only place the intent and the implementation can be
# compared -- and if they ever disagree, every Fmax number is wrong by the same
# unknown factor.
set core_clk [get_clocks -of_objects [get_pins u_clkgen/u_mmcm/CLKOUT0]]
set period   [get_property PERIOD $core_clk]
set fmhz     [expr {1000.0 / $period}]

set luts [llength [get_cells -hierarchical -filter {PRIMITIVE_GROUP == LUT}]]
set ffs  [llength [get_cells -hierarchical -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]

# A14's multiplier MUST be on DSP48E1s, and this is where that gets checked in
# the design that ships rather than in an out-of-context experiment.  Counted by
# REF_NAME over every primitive, with nothing filtered: fpga/scripts/synth_ooc.tcl
# first tried to count DSPs by PRIMITIVE_TYPE and reported ZERO over a netlist
# containing four, because the group name was guessed rather than looked up.  A
# histogram-style count names no group and therefore cannot name one wrongly.
#
# A multiplier that fell back to fabric would still be CORRECT -- which is why
# this is a hard failure rather than a warning.  It would cost roughly a
# thousand LUTs and several nanoseconds, and every symptom would show up as a
# timing number with no obvious cause.
set ndsp 0
foreach c [get_cells -hierarchical -filter {IS_PRIMITIVE}] {
  if {[string match "DSP*" [get_property REF_NAME $c]]} { incr ndsp }
}
puts "=== inferred DSP primitives: $ndsp ==="
if {$ndsp < 4} {
  puts "SOC_FAIL: the 33x33 multiplier was not mapped to DSP48E1 ($ndsp DSPs).\
It would still be correct, and it would cost about a thousand LUTs and several\
nanoseconds -- see rtl/core/rvntt_muldiv.sv."
  exit 1
}

puts "=== core clock [get_property NAME $core_clk] period $period ns = $fmhz MHz ==="
puts "=== WNS = $wns ns   WHS = $whs ns ==="
puts "SOC_RESULT period=$period mhz=$fmhz wns=$wns whs=$whs luts=$luts ffs=$ffs bram=$nbram dsp=$ndsp"

if {$wns < 0 || $whs < 0} {
  puts "SOC_TIMING_FAIL: WNS=$wns WHS=$whs"
  if {$FAIL_ON_NEG} { exit 1 }
  exit 0
}

# ------------------------------------------------------------------ bitstream
if {$WANT_BIT} {
  write_bitstream -force $OUTDIR/${TOP}.bit
  puts "=== bitstream written: $OUTDIR/${TOP}.bit ==="
}
puts "SOC_OK"
exit 0
