# ============================================================================
# Out-of-context synthesis of ONE module, for its resource report and,
# optionally, its post-route reg-to-reg timing.
#
# elab_core.tcl stops at the RTL netlist and says nothing about mapping (DSPs,
# BRAMs, LUT count); this answers that in about a minute.  Out of context
# because the module is not a top level: without -mode out_of_context Vivado
# inserts an IBUF/OBUF for every port.  With a PERIOD given it also places and
# routes and reports post-route worst negative slack.  No I/O delays are
# assumed: the module's operands arrive through the core's own paths, which
# the SoC implementation run measures.
#
# Run from the staging directory:
#   vivado -mode batch -source synth_ooc.tcl -tclargs <top> [period_ns] [G=V ...]
# ============================================================================

set PART xc7a100tcsg324-1
set TOP  [lindex $argv 0]
if {$TOP eq ""} { set TOP rvntt_muldiv }

# Optional second argument: the clock period in ns.  Absent (or 0) means
# synthesis only.
set PERIOD 0
if {[llength $argv] > 1} { set PERIOD [lindex $argv 1] }

# Everything after that is a generic, written `NAME:VALUE`: the wrapper
# reaches Vivado through `cmd.exe /c`, which treats `=` as an argument
# separator.
set GENERICS {}
foreach a [lrange $argv 2 end] {
  set kv [split $a ":"]
  if {[llength $kv] == 2} { lappend GENERICS "[lindex $kv 0]=[lindex $kv 1]" }
}

# Trailing arguments that produced no generic mean the transport mangled
# them; that is a failure, not a run with defaults.
if {[llength $argv] > 2 && [llength $GENERICS] == 0} {
  puts "OOC_FAIL: [lrange $argv 2 end] parsed to no generic.  Generics are\
        written NAME:VALUE; `=` does not survive cmd.exe."
  exit 1
}

set TAG $TOP
foreach g $GENERICS { append TAG "_[string map {= {}} $g]" }

puts "=== out-of-context synthesis of $TOP for $PART ==="
puts "=== Vivado [version -short] ==="

set pkgs [glob -nocomplain rtl/rv32i_pkg.sv]
set rest [lsort [glob rtl/*.sv]]
foreach p $pkgs { set rest [lsearch -inline -all -not -exact $rest $p] }
read_verilog -sv [concat $pkgs $rest]
set_property include_dirs [list [pwd] [pwd]/rtl] [current_fileset]

if {[llength $GENERICS] > 0} {
  puts "OOC_GENERICS: $TOP $GENERICS"
  synth_design -mode out_of_context -top $TOP -part $PART -generic $GENERICS
} else {
  puts "OOC_GENERICS: $TOP (none)"
  synth_design -mode out_of_context -top $TOP -part $PART
}

# Counted by REF_NAME over every primitive, with nothing filtered: a
# histogram of what is present cannot name a group wrongly (a PRIMITIVE_TYPE
# filter once reported DSP=0 over four DSP48E1s).
array set hist {}
foreach c [get_cells -hierarchical -filter {IS_PRIMITIVE}] {
  set r [get_property REF_NAME $c]
  if {[info exists hist($r)]} { incr hist($r) } else { set hist($r) 1 }
}
set line ""
foreach r [lsort [array names hist]] { append line " $r=$hist($r)" }
puts "OOC_CELLS: $TOP$line"

# ... and then the two headline numbers, derived from that same histogram so
# they cannot disagree with it.
set n_dsp 0
foreach r [array names hist] {
  if {[string match "DSP*" $r]} { incr n_dsp $hist($r) }
}
puts "OOC_DSP_TOTAL: $TOP $n_dsp"

# The DSP mapping table is the thing worth reading by eye: it says which
# internal registers (AREG/BREG/MREG/PREG) Vivado actually absorbed, which is a
# different and more useful question than how many DSPs it used.
report_utilization -hierarchical

# ---------------------------------------------------------------------------
# Implementation and post-route timing, when a period was asked for.
# ---------------------------------------------------------------------------
if {$PERIOD > 0} {
  create_clock -name clk -period $PERIOD [get_ports clk]
  puts "=== implementing $TAG at period $PERIOD ns ==="
  opt_design
  place_design
  route_design

  set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1 -nworst 1]]
  set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1 -nworst 1]]

  # Filtered to paths whose start and end are sequential, so an unconstrained
  # I/O path cannot masquerade as the design's limit.
  set r2r [get_timing_paths -delay_type max -max_paths 1 -nworst 1 \
             -from [all_registers] -to [all_registers]]
  set r2r_slack "n/a"
  set r2r_delay "n/a"
  set r2r_ep    "n/a"
  if {[llength $r2r] > 0} {
    set r2r_slack [get_property SLACK $r2r]
    set r2r_delay [get_property DATAPATH_DELAY $r2r]
    set r2r_ep    [get_property ENDPOINT_PIN $r2r]
  }

  set lut 0
  set ff  0
  array set h2 {}
  foreach c [get_cells -hierarchical -filter {IS_PRIMITIVE}] {
    set r [get_property REF_NAME $c]
    if {[info exists h2($r)]} { incr h2($r) } else { set h2($r) 1 }
  }
  foreach r [array names h2] {
    if {[string match "LUT*" $r]}  { incr lut $h2($r) }
    if {[string match "FD*" $r]}   { incr ff  $h2($r) }
  }
  set ndsp 0
  foreach r [array names h2] { if {[string match "DSP*" $r]} { incr ndsp $h2($r) } }

  set verdict "fail"
  if {$wns >= 0 && $whs >= 0} { set verdict "PASS" }
  puts "OOC_TIMING: $TAG period=$PERIOD wns=$wns whs=$whs r2r_slack=$r2r_slack r2r_delay=$r2r_delay lut=$lut ff=$ff dsp=$ndsp verdict=$verdict"
  puts "OOC_ENDPOINT: $TAG $r2r_ep"
  report_timing -delay_type max -max_paths 1 -nworst 1 -from [all_registers] -to [all_registers]
}

set errs [get_msg_config -severity "ERROR" -count]
if {$errs > 0} {
  puts "OOC_FAIL: $TOP"
  exit 1
}
puts "OOC_OK: $TOP"
exit 0
