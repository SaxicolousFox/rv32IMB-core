# ML-KEM-768 with interchangeable NTT backends

`sw/kyber/` builds the pq-crystals ML-KEM-768 reference with the NTT swapped
out at link time, so the same known-answer test exercises every implementation
and a speedup measurement is a build-flag change rather than a code fork.
Plan step C6; the TIER1 backend is what makes plan step C1's acceptance test
possible.

```sh
cd sw/kyber
make TARGET=host BACKEND=SW    NTESTS=10000      # native, reference C
make TARGET=host BACKEND=TIER1 NTESTS=10000      # native, Xkntt emulated in C
make TARGET=rv32 BACKEND=SW    NTESTS=10         # bare-metal RV32I for Spike
make TARGET=rv32 BACKEND=TIER1 NTESTS=10         # ditto, real instructions
```

`NTESTS` and `DIGEST` are part of the output path, so changing one cannot hand
back a stale binary.

## The backends

**SW** compiles the reference `ntt.c` unmodified.

**TIER1** replaces `ntt()`, `invntt()` and `basemul()` — the whole of
`ntt.c` — with definitions built from `sw/include/xkntt.h`. One `kbfct` per
Cooley-Tukey butterfly, one `kbfgs` per Gentleman-Sande butterfly, one `kmm`
per coefficient for the final `mont^2/128` scaling, and `kbmul0`/`kbmul1` for
the two halves of a base multiplication. Everything else in the reference is
compiled untouched, so a KAT failure can only come from this file or from the
instructions themselves.

The file is **generated** by `sw/kyber/gen_ntt_tier1.py`, for one reason: the
zeta table. Plan B0 says not to hand-copy it, so the generator takes the values
from `model/ntt_ref.py` — which derives them from the root of unity — and
refuses to emit anything unless they match the literal array parsed out of the
pristine reference `ntt.c`, element for element.

**TIER2** is not built yet; it needs the coprocessor aperture layout, which
Track B fixes.

## Two build targets, and why both

The host build compiles the same sources with `XKNTT_EMULATE=1`, which swaps
the `.insn` wrappers in `sw/include/xkntt.h` for portable C with identical
semantics (held to `model/isa/xkntt.py` by `tb/unit/test_insn_bridge.py`).

That split separates two things that would otherwise fail together:

- the host TIER1 run tests the **algorithm restructuring** — packing, butterfly
  order, the zeta index sequence — over all 10000 vectors in six seconds;
- the Spike TIER1 run tests the **instruction implementations**, at roughly
  0.2 s per KAT record.

## Bare-metal runtime

`crt0.S`, `htif.c` and `link.ld` give a `-nostdlib` image: stack setup, `.bss`
clear, `memcpy`/`memset`, and buffered HTIF console output. `-lgcc` is still
required — RV32I has no multiply instruction, so the compiler calls
`__mulsi3`.

`kat_main.c` reproduces the reference's `test/test_vectors.c` exactly,
including the implicit-rejection record and the shared-secret equality check,
but without stdio. `DIGEST=1` absorbs the identical byte stream into SHAKE256
and prints 32 bytes instead of 46 MB.

## Results

| Build | Records | Result |
|---|---|---|
| native SW | 10000 | sha256 `b59ac4d2…` — the value in the reference's own `SHA256SUMS` |
| native TIER1 | 10000 | same sha256 |
| Spike SW | prefix | byte-identical to `tvecs768` |
| Spike TIER1 | prefix | byte-identical to `tvecs768` |

The first row is what makes the rest mean anything: it pins this driver to the
published known-answer test rather than to itself.

`KYBER_KAT_FULL=1` runs the full 10000 records on Spike, which takes about an
hour and is deliberately not part of the routine regression.
