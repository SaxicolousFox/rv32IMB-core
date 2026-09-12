# ============================================================================
# The ring oscillator's timing exclusion.
#
# A combinational loop is not a timing path.  DONT_TOUCH and KEEP_HIERARCHY
# stop the loop being deleted; set_disable_timing on the closing arc (the
# feedback inverter's input pin) stops it being timed, leaving every other
# arc analysed.  set_disable_timing on an empty collection is only a warning,
# so build_soc.tcl checks and reports the match count.
# ============================================================================
set ring_cells [get_cells -quiet -hierarchical -filter {NAME =~ *u_seed/u_noise/g_ring.g_r*.chain*}]
puts "ENTROPY_RING: [llength $ring_cells] cell(s) matched"

# The loop-closing LUTs.  Disabling timing through them cuts the ring for
# analysis; the two-flop synchroniser that samples it is a normal path and is
# deliberately NOT excluded, because that one really does need to be timed.
foreach c $ring_cells {
    set_disable_timing -quiet $c
}

# ---------------------------------------------------------------------------
# The bitstream DRC has to be acknowledged separately: write_bitstream refuses
# a combinational loop (DRC LUTLP-1) even after it placed and routed cleanly.
# ALLOW_COMBINATORIAL_LOOPS is set only on the ring's own nets, never
# design-wide, so an accidental loop elsewhere is still caught.
# ---------------------------------------------------------------------------
set ring_nets [get_nets -quiet -hierarchical -filter {NAME =~ *u_seed/u_noise/g_ring.g_r*.chain*}]
puts "ENTROPY_RING_NETS: [llength $ring_nets] net(s) acknowledged"
foreach n $ring_nets {
    set_property ALLOW_COMBINATORIAL_LOOPS TRUE $n
}

# The sampled bit is metastable by construction (the entropy is the jitter
# between the ring's edge and the sampling edge); ASYNC_REG keeps the
# synchroniser pair adjacent, and this removes the setup relationship.
set_false_path -quiet -to [get_pins -quiet -hierarchical -filter {NAME =~ *u_seed/u_noise/g_ring.meta_q_reg*/D}]
