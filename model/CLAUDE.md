# model — the frozen reference contract

**If the RTL disagrees with this directory, the RTL is wrong.** Do not adjust
the model to make hardware pass. These files are validated bit-exactly against
the pq-crystals C reference over 1261 polynomials with per-layer dumps.

| File | Role |
|---|---|
| `modarith.py` | `montgomery_reduce`, `barrett_reduce`, and the C integer semantics they depend on |
| `ntt_ref.py` | Bit-exact transcription, with per-layer snapshots — tells you *which layer* broke |
| `ntt_math.py` | Independent derivation: no butterflies, no Montgomery, no zeta table — tells you whether the maths is right at all |
| `isa/xkntt.py` | Executable `Xkntt` model: `encode`, `decode`, `exec_*` |
| `cref/` | Instrumented C reference, **generated** from the pristine checkout |
| `compare_ntt.py` | The P0.3 acceptance run |

## Why the lazy arithmetic is deliberate

`montgomery_reduce` and `barrett_reduce` reproduce the reference's *unreduced*
`int16` output ranges, truncations and all. Plan §1.3 is emphatic about this: if
the model normalises where the reference does not, comparisons fail for reasons
that are not real bugs. The truncations are load-bearing — do not "clean them
up".

## Two independent models, on purpose

`ntt_ref` and `ntt_math` fail for different reasons, which is the point.
`ntt_ref` agreeing with the C reference means the transcription is faithful;
`ntt_math` agreeing means the transform is mathematically correct. A bug that
fools both is a bug in the reference itself.

## Generated, not copied

The zeta table is derived from the root of unity and then checked against the
reference's literal array element for element (plan B0: do not hand-copy it).
`model/cref/ntt_dump.c` is likewise generated from the pristine `ref/ntt.c`, so
`toolchain/kyber/` stays untouched and its own KATs remain valid. The diff is
kept at `patches/kyber-ref-dump-ntt.patch`.
