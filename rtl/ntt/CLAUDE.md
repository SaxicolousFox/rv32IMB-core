# rtl/ntt — Track B, the NTT coprocessor

**Currently empty** — a P0.2 skeleton directory. Nothing here yet.

## Track B owns the provisional Tier-2 decisions

`docs/isa-spec.md` §9 defers three things to this track. Spike currently
implements a **provisional** choice for each so that C1 could be finished and
verified; those choices are marked as provisional in
`toolchain/spike-src/riscv/xkntt.h` and are yours to replace.

| Deferred to | What Spike provisionally assumes |
|---|---|
| B7 | Aperture at `KNTT_BASE = 0x50000000`, two 512-byte buffers A/B at offsets `0x000`/`0x200`, 256 coefficients each, packed two per word, low-index in the low half |
| B3 | `kntt.cfg`'s `rs1` operand descriptor — latched, otherwise unused |
| B3 | `basemul` operand convention — `OP=2` multiplies buffer A by buffer B, `SRC` selecting which is first |

**Changing any of these requires updating `docs/isa-spec.md` and
`toolchain/spike-src/riscv/xkntt.h` in the same commit**, then
`toolchain/patches.sh export spike`. Splitting that across commits leaves the
spec and the reference implementation disagreeing, which is exactly the failure
the four-way agreement (root `CLAUDE.md`) exists to prevent.

`CYCLES` in Spike is a model — `P = 4` lanes, one butterfly per lane per cycle,
plus a fixed fill/drain. It is **not** a measurement. B7's benchmarking and B8's
constant-time assertion must use real RTL counts, never these.

## Correctness

`model/ntt_ref.py` and `model/ntt_math.py` are the reference. `ntt_ref` is a
bit-exact transcription with per-layer snapshots (it tells you *which layer*
broke); `ntt_math` is an independent derivation with no butterflies, no
Montgomery form and no zeta table (it tells you whether the mathematics is
right at all). If the RTL disagrees with them, the RTL is wrong.

Do not hand-copy the zeta table (plan B0). `model/ntt_ref.gen_zetas()` derives
it from the root of unity, and `sw/kyber/gen_ntt_tier1.py` shows the pattern:
generate it, then assert it matches the pristine reference element for element.

## Before you start

- `source toolchain/env.sh` (see the root `CLAUDE.md`).
- `rtl/common/` and `sw/include/xkntt.h` are shared with Track A. Coordinate.
