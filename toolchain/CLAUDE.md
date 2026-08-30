# toolchain — forks, pins and patch discipline

`source toolchain/env.sh` before anything. See the root `CLAUDE.md` for why
skipping it produces a green regression that tested nothing.

## Patch discipline

The Spike and LLVM forks live as rebaseable branches (`xkntt`) with
`git format-patch` output in `patches/`. `toolchain/upstream-pins.txt` records
the base commit for each.

```sh
toolchain/patches.sh status          # branch vs patches/ vs pin
toolchain/patches.sh export spike    # after ANY commit touching the fork
toolchain/patches.sh apply  spike    # rebuild the branch from patches/ alone
toolchain/patches.sh rebase spike    # onto new upstream, then re-pin
toolchain/patches.sh verify spike    # round-trip check
```

**Re-export after every commit to a fork**, or `patches_in_sync` fails.

**`origin/master` here is not this repo.** `patches.sh` and
`upstream-pins.txt` name `origin/master` because that is **riscv-isa-sim's**
default branch. This project's own default branch is `main`. Renaming those
strings would break `patches.sh rebase`.

`verify` normalises the `From <sha>` and `index` lines before comparing:
`git am` necessarily creates new commit objects, so those differ after any
round-trip even when the content is identical. The stronger check is `apply` —
it resets the branch to the pin and rebuilds from `patches/`, and the resulting
tree should be hash-identical.

Structure the Spike series so **new files come first and integration second**.
Upstream churn then lands on the integration patch and never on the semantics,
which keeps rebasing cheap.

## Test-suite checkouts and their pins

`riscv-tests` (A9) and `riscv-arch-test` (A10) are gitignored checkouts like
`spike-src`, reproduced from **`toolchain/test-suite-pins.txt`** rather than
committed. That file is deliberately separate from `upstream-pins.txt`, which
`patches.sh` parses and which is only for the forks this project *patches*.

It also records the RISCOF Python pins, which are not obvious: riscof 1.25.3
must be installed with `--no-deps`, because its `gitpython==3.1.17` pin predates
Python 3.12 and blocks resolution on this venv's 3.13 — while the version uv
picks instead, 1.21.1, needs a riscv-config too old to accept the ISA schema.

## Third-party checkouts are gitignored

`spike-src/`, `spike-build/`, `kyber/`, `llvm-project/`, `install/`, `opt/`,
`dist/` are all excluded. The forks are reproduced from `patches/` plus the
pins, not committed.

**`toolchain/kyber/` must stay pristine** so the reference's own KATs remain
valid. Instrumentation is generated into `model/cref/`; never edit in place.

## Rebuild costs

`cd toolchain/spike-build && make -j"$(nproc)" && make install`

Touching `riscv/xkntt.h`, `riscv/xkntt_encoding.h` or `riscv/insn_template.h`
rebuilds all ~1600 generated instruction files — **about 15 minutes, background
it**. A single `riscv/insns/*.h` or `riscv/xkntt.cc` rebuilds in seconds, which
is worth planning fault-injection experiments around.

Editing `riscv/riscv.mk.in` needs a re-run of `configure` (or at least
`make riscv.mk`) before the change takes effect.

## Pinned choices

Bitwuzla is the primary SMT solver, with Boolector and Z3 available. The
primary RISC-V toolchain is xPack `riscv-none-elf-gcc` — the riscv-collab
prebuilt turned out to be single-multilib, with no `rv32i/ilp32` libgcc or
newlib. cocotb runs on a standalone CPython 3.13 fetched by `uv`, because
cocotb 2.0.1 rejects the system 3.14.
