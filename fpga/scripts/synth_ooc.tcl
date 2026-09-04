# ============================================================================
# Out-of-context synthesis of ONE module, for its resource report.
#
# elab_core.tcl runs `synth_design -rtl`, which stops at the RTL netlist and
# therefore says NOTHING about mapping: no DSPs, no BRAMs, no LUT count.  That
# is exactly what MODS_A A14 asks to be checked and not assumed -- "verify DSP
# inference by reading the synthesis report" -- and it is what A17 will need
# repeatedly while it moves logic around.  A full SoC implementation run answers
# the same question in fourteen minutes; this answers it in about one.
#
# OUT OF CONTEXT because the module under test is not a top level: without
# -mode out_of_context Vivado inserts an IBUF/OBUF for every port, which on a
# 100-pin arithmetic unit both fails placement and buries the numbers being
# looked for.
#
# MODS_A2 A24 ADDED IMPLEMENTATION AND TIMING.  Synthesis alone answers "what
# did it map to"; it does NOT answer "how fast does it run", because synthesis
# timing is estimated with no placement and no routing, and A12 measured route
# delay at 78% of this design's critical path.  A24 needs a real frequency for
# a datapath Track B has to meet, so when a PERIOD is given this script places
# and routes as well and reports POST-ROUTE worst negative slack -- the same
# quantity fmax_search.py binary-searches on for the SoC, so the two numbers
# are comparable.
#
# NO I/O DELAYS ARE ASSUMED, and that is a stated boundary rather than an
# omission.  A Tier-1 unit's operands arrive through the core's forwarding mux
# and its result leaves through the core's writeback mux; both are the CORE's
# paths and are measured by the SoC implementation run, not here.  Budgeting
# them here would mean inventing a number.  So this reports the unit's internal
# register-to-register frequency, and prints the worst reg-to-reg DELAY beside
# it so any budget can be applied afterwards by arithmetic.
#
# Run from the staging directory:
#   vivado -mode batch -source synth_ooc.tcl -tclargs <top> [period_ns] [G=V ...]
# ============================================================================

set PART xc7a100tcsg324-1
set TOP  [lindex $argv 0]
if {$TOP eq ""} { set TOP rvntt_muldiv }

# Optional second argument: the clock period in ns.  Absent (or 0) keeps the
# original synthesis-only behaviour, which A14 and A17 both depend on.
set PERIOD 0
if {[llength $argv] > 1} { set PERIOD [lindex $argv 1] }

# Everything after that is a generic, written `NAME:VALUE`.
#
# COLON, NOT EQUALS, AND THAT IS NOT A STYLE CHOICE.  The wrapper reaches
# Vivado through `cmd.exe /c`, and cmd.exe treats `=` as an argument SEPARATOR
# exactly like a space.  `-tclargs rvntt_tier1_probe 8.04 STAGES=2` arrives here
# as `... 8.04 STAGES 2` -- four argv entries, no `=` anywhere, so the pattern
# below matched nothing and synth_design was called with NO generic at all.
# Three "configurations" were implemented and all three were the default, with
# byte-identical WNS at every search point; the only thing that gave it away was
# a critical-path endpoint naming a generate block that the shallower
# configurations do not contain.  A report whose green was not about the thing
# it named -- the seventh in this project.
set GENERICS {}
foreach a [lrange $argv 2 end] {
  set kv [split $a ":"]
  if {[llength $kv] == 2} { lappend GENERICS "[lindex $kv 0]=[lindex $kv 1]" }
}

# AND IT MUST NOT BE ABLE TO FAIL QUIETLY AGAIN.  Trailing arguments that
# produced no generic mean the transport mangled them; that is a failure, not a
# run with defaults.
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

# COUNTED BY REF_NAME, over every primitive in the netlist, with nothing
# filtered out and no group name written down anywhere.
#
# The first version of this did filter, on PRIMITIVE_TYPE, and it reported
# `DSP=0 FF=0` over a netlist containing four DSP48E1s and 239 flops -- because
# the group names are FLOP_LATCH and (for this part) the DSP does not match the
# pattern that was guessed for it.  A checking script that answers "the
# multiplier was not inferred" when it was is worse than no script, and it is
# the same failure this project has now hit three times: A10's RISCOF exit code,
# A11's sby exit code, and this.  TAKE THE VERDICT FROM THE ARTEFACT.  A
# histogram of every REF_NAME present cannot name a group wrongly, because it
# names nothing -- it reports what is there.
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

  # The reg-to-reg number, reported separately and by construction: filtered to
  # paths whose start AND end are sequential, so an unconstrained I/O path
  # cannot masquerade as the design's limit.  `report_timing_summary`'s WNS
  # would happily be an input path here, and this script's own history is that
  # a number taken from the wrong field is the failure mode to design against.
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
