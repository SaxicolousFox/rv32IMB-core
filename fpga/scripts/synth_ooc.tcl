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
# Run from the staging directory:
#   vivado -mode batch -source synth_ooc.tcl -tclargs <top>
# ============================================================================

set PART xc7a100tcsg324-1
set TOP  [lindex $argv 0]
if {$TOP eq ""} { set TOP rvntt_muldiv }

puts "=== out-of-context synthesis of $TOP for $PART ==="
puts "=== Vivado [version -short] ==="

set pkgs [glob -nocomplain rtl/rv32i_pkg.sv]
set rest [lsort [glob rtl/*.sv]]
foreach p $pkgs { set rest [lsearch -inline -all -not -exact $rest $p] }
read_verilog -sv [concat $pkgs $rest]
set_property include_dirs [list [pwd] [pwd]/rtl] [current_fileset]

synth_design -mode out_of_context -top $TOP -part $PART

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

set errs [get_msg_config -severity "ERROR" -count]
if {$errs > 0} {
  puts "OOC_FAIL: $TOP"
  exit 1
}
puts "OOC_OK: $TOP"
exit 0
