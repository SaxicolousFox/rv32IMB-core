## Digilent Arty A7-100T (XC7A100T-1CSG324C) -- SoC.
##
## Pins are from the official Digilent master XDC (Arty-A7-100-Master.xdc).
## LD0-LD3 are the RGB LEDs and LD4-LD7 are the plain green ones, so led[0]
## is the LED labelled LD4.

## ---------------------------------------------------------------- configuration
set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]

## ---------------------------------------------------------------------- clock
set_property -dict { PACKAGE_PIN E3 IOSTANDARD LVCMOS33 } [get_ports { CLK100MHZ }]
create_clock -name sys_clk -period 10.000 -waveform {0 5} [get_ports { CLK100MHZ }]

## The core clock is the MMCM's CLKOUT0.  Vivado derives its generated clock
## from the MMCM's parameters, so it must NOT be declared here; gen_soc_clk.py
## sets the frequency through the divider and build_soc.tcl checks the derived
## period.

## ---------------------------------------------------------------------- reset
set_property -dict { PACKAGE_PIN C2 IOSTANDARD LVCMOS33 } [get_ports { ck_rst }]

## ------------------------------------------------------------- green LEDs (GPIO)
set_property -dict { PACKAGE_PIN H5  IOSTANDARD LVCMOS33 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN J5  IOSTANDARD LVCMOS33 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN T9  IOSTANDARD LVCMOS33 } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN T10 IOSTANDARD LVCMOS33 } [get_ports { led[3] }]

## ------------------------------------------------------- RGB LD0 (hardware status)
## The master XDC lists these in the order b, g, r.  tb/fpga/check_xdc_pins.py
## compares every line below against fpga/constraints/arty_a7_100t_pins.txt,
## extracted mechanically from the vendor file.
set_property -dict { PACKAGE_PIN G6 IOSTANDARD LVCMOS33 } [get_ports { led0_r }]
set_property -dict { PACKAGE_PIN F6 IOSTANDARD LVCMOS33 } [get_ports { led0_g }]
set_property -dict { PACKAGE_PIN E1 IOSTANDARD LVCMOS33 } [get_ports { led0_b }]

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
## Every input on this board is asynchronous to the core clock (mechanical
## contacts, slide switches, a serial line on the host's oscillator), so the
## paths are cut and the RTL puts a synchroniser behind every one of them.
set_false_path -from [get_ports ck_rst]
set_false_path -from [get_ports { sw[*] }]
set_false_path -from [get_ports { btn[*] }]
set_false_path -from [get_ports uart_txd_in]

## Outputs drive a human eye and a UART receiver that resynchronises on every
## start bit, so their pad delay is equally meaningless.
set_false_path -to [get_ports { led[*] }]
set_false_path -to [get_ports { led0_r led0_g led0_b }]
set_false_path -to [get_ports uart_rxd_out]

## No set_clock_groups: there is no logic in the sys_clk domain (the
## oscillator drives the MMCM and nothing else), so there is no crossing to
## except.
