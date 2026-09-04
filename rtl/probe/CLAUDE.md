# rtl/probe — measurement artefacts, not designs

**Nothing here ships, and nothing here may be instantiated by anything that
does.** These modules exist to be synthesised out of context and timed, so that
a number is known before a decision is made rather than after.

| Module | Step | Question it answers |
|---|---|---|
| `rvntt_tier1_probe.sv` | A24 (`MODS_A2`) | What Fmax can a Tier-1 `Xkntt` butterfly reach, and therefore what ceiling does the core's clock have? |

## Why this directory exists rather than `rtl/ntt/`

`rtl/ntt/` is **Track B's**. A probe sitting in it would eventually be mistaken
for a starting point, and it is not one: the Tier-1 probe is deliberately a
*representative* datapath, not §B1's or §B2's design. **What carries across to
Track B is the frequency, not the RTL.** Track B is free to build something
completely different and should not feel bound by an arrangement chosen to be
timed quickly.

## The rules a probe still has to follow

**It gets a correctness test.** `tb/probe/test_tier1_probe.py` checks the
butterfly against `model/isa/xkntt.py` over 2000 vectors in every
configuration. This is not ceremony: a probe's entire value is one frequency,
and a datapath that computes the wrong answer is very likely a *smaller*
datapath than the right one — which reports a frequency the real unit cannot
reach, i.e. the exact failure the step exists to prevent.

**Its stimulus corrupts the operands after the start cycle**, because the core's
forwarding muxes drift while the pipeline is stalled (`rvntt_muldiv.sv` records
why). Holding the inputs steady made the testbench blind to a mutation that read
a combinational value instead of a registered one; with them moving, it is
caught immediately.

**It must lint clean at every parameter value it is measured at**, and it is
included in `tb/lint_all.py`'s standalone walk automatically because that walks
the whole `rtl/` tree.

## Running one

```sh
source toolchain/env.sh
python3 fpga/scripts/probe_tier1.py --stages 4 --lo 80 --hi 150 --iters 4
```

Needs `dangerouslyDisableSandbox: true` — Vivado is on the Windows side and the
sandbox blocks the interop socket. Method and caveats: `docs/a24-tier1-probe.md`.

**Generics travel as `NAME:VALUE`, not `NAME=VALUE`.** `cmd.exe /c` treats `=`
as an argument separator, so `STAGES=2` arrives as two arguments and
`synth_design` gets no generic at all — which once produced three
"configurations" that were all the default, with byte-identical WNS.
`synth_ooc.sh` translates for you; `synth_ooc.tcl` hard-fails if trailing
arguments parse to no generic, and `probe_tier1.py` refuses to report two
configurations whose netlists are identical.
