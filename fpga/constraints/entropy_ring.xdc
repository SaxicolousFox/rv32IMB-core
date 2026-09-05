# ============================================================================
# A29 (MODS_A2) -- the ring oscillator's timing exclusion.
#
# A combinational loop is not a timing path; it is a path with no beginning.
# Vivado's default behaviour is to report it as an error ("a combinational loop
# was detected") and refuse to route.  Telling it not to analyse the loop is a
# CONSTRAINT and not an attribute -- DONT_TOUCH and KEEP_HIERARCHY stop the
# loop being DELETED, and do nothing about it being TIMED.
#
# set_disable_timing on the closing arc is the documented way: it breaks the
# loop for analysis only, leaving the netlist alone.  The arc chosen is the
# feedback inverter's input pin -- the one that closes the ring -- so every
# other arc in the chain is still analysed and a genuine timing problem
# elsewhere in the module is still reported.
#
# THE GET_PINS PATTERNS ARE WRITTEN TO MATCH NOTHING QUIETLY IF THE HIERARCHY
# MOVES, WHICH IS THE FAILURE TO WATCH FOR.  `set_disable_timing` on an empty
# collection is a warning, not an error, and the build then fails much later
# with a combinational-loop error that names a cell nobody recognises.  So the
# count is checked and reported by build_soc.tcl.
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
# AND THE DRC HAS TO BE ACKNOWLEDGED SEPARATELY, which is a third mechanism.
# ---------------------------------------------------------------------------
# DONT_TOUCH stops the loop being DELETED.  set_disable_timing stops it being
# TIMED.  Neither stops `write_bitstream`'s DRC from refusing it:
#
#   ERROR: [DRC LUTLP-1] Combinatorial Loop Alert: 2 LUT cells form a
#   combinatorial loop.  This can create a race condition.
#
# The design placed and routed cleanly with WNS 0.000 and then produced no
# bitstream, which is the right failure mode -- Vivado will not silently ship a
# combinational loop, and it should not.  ALLOW_COMBINATORIAL_LOOPS is the
# documented acknowledgement, and the error message names it.
#
# IT IS SET ONLY ON THE RING'S OWN NETS, never design-wide.  A blanket
# acknowledgement would suppress the same DRC for an ACCIDENTAL loop somewhere
# else in the core, which is precisely the class of bug this check exists to
# catch -- and it is one of the few classes that simulation cannot see either,
# because Verilator reports it as DIDNOTCONVERGE rather than as a wrong answer.
set ring_nets [get_nets -quiet -hierarchical -filter {NAME =~ *u_seed/u_noise/g_ring.g_r*.chain*}]
puts "ENTROPY_RING_NETS: [llength $ring_nets] net(s) acknowledged"
foreach n $ring_nets {
    set_property ALLOW_COMBINATORIAL_LOOPS TRUE $n
}

# The sampled bit is metastable BY CONSTRUCTION -- the entropy is the jitter
# between the ring's edge and the sampling edge.  ASYNC_REG on the capture flop
# asks the placer to keep the synchroniser pair adjacent; this tells the timing
# engine not to expect a setup relationship that cannot exist.
set_false_path -quiet -to [get_pins -quiet -hierarchical -filter {NAME =~ *u_seed/u_noise/g_ring.meta_q_reg*/D}]
