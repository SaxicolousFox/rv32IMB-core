# Program the Arty's XC7A100T over JTAG from a batch Vivado run.  Volatile
# configuration only: nothing is written to the QSPI flash.
#
#   vivado -mode batch -source program_soc.tcl -tclargs <path-to-.bit>
set BIT [lindex $argv 0]
if {![file exists $BIT]} {
  puts "PROGRAM_FAIL: no such bitstream: $BIT"
  exit 1
}

open_hw_manager
connect_hw_server -allow_non_jtag
set targets [get_hw_targets]
if {[llength $targets] == 0} {
  puts "PROGRAM_FAIL: no JTAG target -- is the board plugged in and powered?"
  exit 1
}
current_hw_target [lindex $targets 0]
open_hw_target

set dev [lindex [get_hw_devices] 0]
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
puts "=== device: $dev  part=[get_property PART $dev] ==="

set_property PROGRAM.FILE $BIT $dev
program_hw_devices $dev
refresh_hw_device $dev

# DONE is the only evidence that configuration actually took; program_hw_devices
# succeeding is not the same thing.
set done [get_property REGISTER.IR.BIT5_DONE $dev]
puts "=== DONE = $done ==="
close_hw_target
if {$done ne "1"} {
  puts "PROGRAM_FAIL: DONE did not go high"
  exit 1
}
puts "PROGRAM_OK"
exit 0
