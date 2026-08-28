# ============================================================================
# P0.5 -- non-project batch build of the blinky/UART/BRAM de-risk bitstream.
#
# Non-project mode on purpose: everything is scripted and reproducible, there is
# no .xpr to drift, and the same script shape carries straight over to A12.
#
# Run from the staging directory (bram_init.mem must be in the cwd, because
# $readmemh resolves relative to it during synthesis):
#   vivado -mode batch -source build_blinky.tcl
# ============================================================================

set PART      xc7a100tcsg324-1
set TOP       rvntt_blinky_top
set OUTDIR    [pwd]/out

file mkdir $OUTDIR

puts "=== rvntt P0.5: building $TOP for $PART ==="
puts "=== Vivado [version -short] ==="

# ------------------------------------------------------------------ sources
read_verilog -sv [glob rtl/*.sv]

# The generated header (bram_expected.svh) and the .mem image live here.
set_property include_dirs [list [pwd] [pwd]/rtl] [current_fileset]

read_xdc constraints/arty_a7_100t.xdc

# ------------------------------------------------------------------ synthesis
synth_design -top $TOP -part $PART -include_dirs [list [pwd] [pwd]/rtl]
write_checkpoint -force $OUTDIR/post_synth.dcp
report_utilization  -file $OUTDIR/post_synth_util.rpt

# Confirm the BRAM was actually inferred as a block RAM rather than turned into
# fabric.  This is a load-bearing check: if it silently became LUTRAM, A12's
# 64 KB instruction memory would not fit and we would find out much later.
set nbram [llength [get_cells -hierarchical -filter {PRIMITIVE_TYPE =~ BMEM.*.*}]]
puts "=== inferred BRAM primitives: $nbram ==="
if {$nbram < 1} {
  puts "WARNING: no BRAM primitives inferred -- check ram_style and the read port"
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

# --------------------------------------------------------------- timing gate
# Fail the build on negative slack.  A bitstream that does not meet timing is
# worse than no bitstream: it usually works on the bench and fails later.
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
puts "=== WNS = $wns ns   WHS = $whs ns ==="

if {$wns < 0 || $whs < 0} {
  puts "ERROR: timing not met (WNS=$wns, WHS=$whs)"
  exit 1
}

# ------------------------------------------------------------------ bitstream
write_bitstream -force $OUTDIR/${TOP}.bit
puts "=== bitstream written: $OUTDIR/${TOP}.bit ==="

# Report the MMCM ratio actually implemented, so the LED check has something to
# be compared against rather than being taken on trust.
puts "=== done ==="
exit 0
