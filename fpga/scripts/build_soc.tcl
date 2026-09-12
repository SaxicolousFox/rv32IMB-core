# ============================================================================
# Non-project batch build of the SoC bitstream for the Arty A7-100T.
#
# Run from the staging directory (soc_init.mem must be in the cwd, because
# $readmemh resolves relative to it):
#   vivado -mode batch -source build_soc.tcl -tclargs <fail_on_neg> <want_bit> [strategy]
#
#   fail_on_neg  1 (default) exit non-zero if WNS or WHS is negative
#                0 report and continue -- for the Fmax binary search
#   want_bit     1 (default) write a bitstream when timing is met
#   strategy     default | explore_postroute
# ============================================================================

set PART   xc7a100tcsg324-1
set TOP    rvntt_soc_top
set OUTDIR [pwd]/out

set FAIL_ON_NEG 1
set WANT_BIT    1
# Echoed into SOC_RESULT: a number measured under a different strategy is a
# different measurement.
set STRATEGY    "default"
if {[llength $argv] > 0} { set FAIL_ON_NEG [lindex $argv 0] }
if {[llength $argv] > 1} { set WANT_BIT    [lindex $argv 1] }
if {[llength $argv] > 2} { set STRATEGY    [lindex $argv 2] }

file mkdir $OUTDIR

puts "=== rvntt: building $TOP for $PART ==="
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

# The ring oscillator's exclusion is read after synthesis, because it selects
# cells.  The count is printed: set_disable_timing over an empty collection is
# only a warning, and the build would fail much later at the DRC.
if {[file exists constraints/entropy_ring.xdc]} {
  source constraints/entropy_ring.xdc
  set nring [llength [get_cells -quiet -hierarchical \
                        -filter {NAME =~ *u_seed/u_noise/g_ring.g_r*.chain*}]]
  puts "SOC_ENTROPY_RING: $nring ring cell(s)"
  if {$nring == 0} {
    puts "SOC_ENTROPY_RING: NONE -- this build has the STUB source, not the ring"
  }
}

write_checkpoint -force $OUTDIR/post_synth.dcp
report_utilization -file $OUTDIR/post_synth_util.rpt

# A missing $readmemh file or an unconnected top-level port is a CRITICAL
# WARNING, not an error, and produces a working but wrong bitstream.
set crit [get_msg_config -severity "CRITICAL WARNING" -count]
set errs [get_msg_config -severity "ERROR" -count]
puts "=== synthesis messages: $errs error(s), $crit critical warning(s) ==="
if {$errs > 0 || $crit > 0} {
  puts "SOC_FAIL: synthesis reported errors or critical warnings"
  exit 1
}

# The memory must be block RAM; 128 KB in fabric would not fit the device.
set nbram [llength [get_cells -hierarchical -filter {PRIMITIVE_TYPE =~ BMEM.*.*}]]
puts "=== inferred BRAM primitives: $nbram ==="
if {$nbram < 8} {
  puts "SOC_FAIL: memory was not inferred as block RAM ($nbram primitives)"
  exit 1
}

# ------------------------------------------------------- place and route
# explore_postroute is Performance_ExplorePostRoutePhysOpt in a non-project
# flow: Explore directives plus a second phys_opt_design after the router.
puts "=== implementation strategy: $STRATEGY ==="
if {$STRATEGY eq "explore_postroute"} {
  opt_design      -directive Explore
  place_design    -directive Explore
  phys_opt_design -directive Explore
  route_design    -directive Explore
  phys_opt_design -directive Explore
} else {
  opt_design
  place_design
  phys_opt_design
  route_design
}

write_checkpoint -force $OUTDIR/post_route.dcp
report_timing_summary -file $OUTDIR/post_route_timing.rpt
report_utilization    -file $OUTDIR/post_route_util.rpt
report_clock_utilization -file $OUTDIR/post_route_clock_util.rpt
report_drc            -file $OUTDIR/post_route_drc.rpt
# The worst setup paths, with enough detail to name the critical path.
report_timing -max_paths 10 -nworst 10 -setup -path_type full_clock_expanded \
              -file $OUTDIR/post_route_critical.rpt

# --------------------------------------------------------------- the numbers
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]

# The core clock's period as Vivado derived it from the MMCM, so intent
# (gen_soc_clk.py) and implementation can be compared.
set core_clk [get_clocks -of_objects [get_pins u_clkgen/u_mmcm/CLKOUT0]]
set period   [get_property PERIOD $core_clk]
set fmhz     [expr {1000.0 / $period}]

set luts [llength [get_cells -hierarchical -filter {PRIMITIVE_GROUP == LUT}]]
set ffs  [llength [get_cells -hierarchical -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]

# The multiplier must be on DSP48E1s.  Counted by REF_NAME over every primitive
# with nothing filtered.  A multiplier that fell back to fabric would still be
# correct, which is why this is a hard failure rather than a warning.
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
puts "SOC_RESULT period=$period mhz=$fmhz wns=$wns whs=$whs luts=$luts ffs=$ffs bram=$nbram dsp=$ndsp strategy=$STRATEGY"

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
