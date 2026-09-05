# ============================================================================
# A27 (MODS_A2), configuration P1 -- the whole SoC in ONE clock-region row.
#
# WHY THIS SHAPE.  post_route_clock_util.rpt for A26's netlist says the design
# already occupies four clock regions -- X0Y1, X1Y1, X0Y2, X1Y2 -- with 1957
# slices spread across roughly 7800 sites.  So A27's premise, "the placer
# spreading a chain across a die it has no reason to keep on one corner", is
# only half right: Vivado has already concentrated it into a 2x2 block, and
# what is left to remove is the VERTICAL span.
#
# Row Y1 is the row that holds the memory: 29 of the 32 RAMB36 tiles are in
# X0Y1 and X1Y1, which between them have 40 sites.  The critical path after
# A26 starts at a BRAM and ends at the ID/EX register, so pulling the logic
# into the memory's own row is the specific thing worth trying.
#
# Capacity, checked rather than assumed: 3900 slices against 1957 used (50%),
# 40 RAMB36 against 32 (80%), 40 DSP48 against 4.  A pblock that cannot hold
# the design fails to place LOUDLY, which is the right failure mode -- one that
# is subtly wrong routes and runs, which is why A27's verification is a
# hardware run and not a timing report.
# ============================================================================
create_pblock pb_soc
add_cells_to_pblock [get_pblocks pb_soc] [get_cells -quiet u_core]
add_cells_to_pblock [get_pblocks pb_soc] [get_cells -quiet u_ram]
resize_pblock [get_pblocks pb_soc] -add {CLOCKREGION_X0Y1:CLOCKREGION_X1Y1}
set_property CONTAIN_ROUTING false [get_pblocks pb_soc]
