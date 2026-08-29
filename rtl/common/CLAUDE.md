# rtl/common — shared RTL

Modules used by **both** Track A and Track B. Treat anything here as frozen
once both tracks depend on it: coordinate before editing rather than changing
it in one worktree and discovering the collision at merge.

Present: `rvntt_sync_reset.sv` — the two-flop reset synchroniser, covered by
lint, Verilator simulation *and* a formal BMC property.

Two things learned building it, worth repeating for anything added here:

- **Give flops explicit power-on values** (`= '0`). The formal run produced a
  spurious counterexample because an unconstrained initial state let `sync_q`
  power up all-ones. An explicit reset value is accurate for Xilinx FFs, and it
  is the right fix — weakening the assertion is not.
- **Derive counter widths from the constant, not the reverse.** A UART banner
  repeated 15× too fast because a width was chosen first and the constant
  scaled to fit it. The deeper defect was in the testbench, which read one
  message and stopped, so nothing observed the repeat interval at all.

Every module here should be lint-clean under `-Wall`, with any suppression
narrowly scoped and commented with *why* the flagged structure is intended.
