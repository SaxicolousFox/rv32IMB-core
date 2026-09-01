## Digilent Arty A7-100T (XC7A100T-1CSG324C) -- A12 SoC.
##
## Pins are from the official Digilent master XDC (Arty-A7-100-Master.xdc); the
## seven that P0.5 already ran on this board (E3, C2, H5, J5, T9, T10, D10) are
## byte-identical to fpga/constraints/arty_a7_100t.xdc.  That file is left alone
## rather than extended: it is the constraint set behind a hardware-confirmed
## milestone, and there is no reason for an A12 mistake to be able to invalidate
## M0's bitstream.
##
## Note on the silkscreen, repeated here because it costs a bench session:
## LD0-LD3 are the RGB LEDs and LD4-LD7 are the plain green ones, so led[0] is
## the LED labelled LD4.  Nothing is shifted.

## ---------------------------------------------------------------- configuration
set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]

## ---------------------------------------------------------------------- clock
set_property -dict { PACKAGE_PIN E3 IOSTANDARD LVCMOS33 } [get_ports { CLK100MHZ }]
create_clock -name sys_clk -period 10.000 -waveform {0 5} [get_ports { CLK100MHZ }]

## The core clock is the MMCM's CLKOUT0.  Vivado derives its generated clock from
## the MMCM's own parameters, so it must NOT be declared here -- and that is also
## why fpga/scripts/gen_soc_clk.py sets the frequency by choosing the MMCM
## divider rather than by writing a period into this file.  build_soc.tcl checks
## afterwards that the period Vivado derived is the one that was asked for.

## ---------------------------------------------------------------------- reset
set_property -dict { PACKAGE_PIN C2 IOSTANDARD LVCMOS33 } [get_ports { ck_rst }]

## ------------------------------------------------------------- green LEDs (GPIO)
set_property -dict { PACKAGE_PIN H5  IOSTANDARD LVCMOS33 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN J5  IOSTANDARD LVCMOS33 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN T9  IOSTANDARD LVCMOS33 } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN T10 IOSTANDARD LVCMOS33 } [get_ports { led[3] }]

## ------------------------------------------------------- RGB LD0 (hardware status)
set_property -dict { PACKAGE_PIN E1 IOSTANDARD LVCMOS33 } [get_ports { led0_r }]
set_property -dict { PACKAGE_PIN F6 IOSTANDARD LVCMOS33 } [get_ports { led0_g }]
set_property -dict { PACKAGE_PIN G6 IOSTANDARD LVCMOS33 } [get_ports { led0_b }]

## ------------------------------------------------------------- USB-UART bridge
## Naming is from the HOST's point of view: uart_rxd_out is what the FPGA DRIVES
## and the PC receives; uart_txd_in is what the PC drives and the FPGA receives.
## Getting this backwards is the classic Arty UART bug.
set_property -dict { PACKAGE_PIN D10 IOSTANDARD LVCMOS33 } [get_ports { uart_rxd_out }]
set_property -dict { PACKAGE_PIN A9  IOSTANDARD LVCMOS33 } [get_ports { uart_txd_in }]

## ------------------------------------------------------------- switches, buttons
set_property -dict { PACKAGE_PIN A8  IOSTANDARD LVCMOS33 } [get_ports { sw[0] }]
set_property -dict { PACKAGE_PIN C11 IOSTANDARD LVCMOS33 } [get_ports { sw[1] }]
set_property -dict { PACKAGE_PIN C10 IOSTANDARD LVCMOS33 } [get_ports { sw[2] }]
set_property -dict { PACKAGE_PIN A10 IOSTANDARD LVCMOS33 } [get_ports { sw[3] }]

set_property -dict { PACKAGE_PIN D9 IOSTANDARD LVCMOS33 } [get_ports { btn[0] }]
set_property -dict { PACKAGE_PIN C9 IOSTANDARD LVCMOS33 } [get_ports { btn[1] }]
set_property -dict { PACKAGE_PIN B9 IOSTANDARD LVCMOS33 } [get_ports { btn[2] }]
set_property -dict { PACKAGE_PIN B8 IOSTANDARD LVCMOS33 } [get_ports { btn[3] }]

## ------------------------------------------------------------------- timing IO
## EVERY input on this board is asynchronous to the core clock: two mechanical
## contacts (ck_rst, btn), four slide switches, and a serial line clocked by the
## host's oscillator.  There is no source-synchronous interface anywhere, so
## set_input_delay has no meaningful number to carry -- the honest constraint is
## to cut the path and put a synchroniser behind every one of them, which is what
## the RTL does: rvntt_uart_rx has a two-flop synchroniser on rx, rvntt_soc_top
## has one on sw/btn, and ck_rst goes through rvntt_sync_reset.
##
## Cutting them without those synchronisers would be the actual mistake, and it
## would look exactly like this file does.
set_false_path -from [get_ports ck_rst]
set_false_path -from [get_ports { sw[*] }]
set_false_path -from [get_ports { btn[*] }]
set_false_path -from [get_ports uart_txd_in]

## Outputs drive a human eye and a UART receiver that resynchronises on every
## start bit, so their pad delay is equally meaningless.
set_false_path -to [get_ports { led[*] }]
set_false_path -to [get_ports { led0_r led0_g led0_b }]
set_false_path -to [get_ports uart_rxd_out]

## No set_clock_groups here, deliberately, and this is a real difference from the
## P0.5 constraint set.  That design had counters in BOTH the oscillator and MMCM
## domains and needed the two declared asynchronous.  This one has NO logic in
## the sys_clk domain at all -- the oscillator drives the MMCM and nothing else --
## so there is no crossing to except, and declaring one anyway would only be able
## to hide a crossing that appears later.
