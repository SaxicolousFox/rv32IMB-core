# ============================================================================
# A27 (MODS_A2), configuration P2 -- the CORE in the memory's row, the memory
# left free.
#
# The difference from P1 is the question being asked.  P1 asks "does pulling
# everything into one row help?".  P2 asks "is it the CORE's spread that costs,
# or the core-to-memory crossing?" -- by constraining the logic and letting
# Vivado put the 32 BRAM tiles wherever it likes, including outside the row.
#
# If P2 matches P1 the memory's placement was never the issue; if P2 is worse,
# the crossing is what the pblock bought.  Two runs, one distinction, and it is
# the same shape of question A17 asked about its 16-versus-32-tile experiment.
# ============================================================================
create_pblock pb_core
add_cells_to_pblock [get_pblocks pb_core] [get_cells -quiet u_core]
resize_pblock [get_pblocks pb_core] -add {CLOCKREGION_X0Y1:CLOCKREGION_X1Y1}
set_property CONTAIN_ROUTING false [get_pblocks pb_core]
