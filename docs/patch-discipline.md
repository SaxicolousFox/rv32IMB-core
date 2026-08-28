# Patch discipline for the third-party forks (P0.4)

We carry local changes to two upstream projects:

| Project | Checkout | Branch | Patches |
|---|---|---|---|
| Spike (`riscv-isa-sim`) | `toolchain/spike-src` | `xkntt` | `patches/spike/` |
| LLVM (`llvm-project`)   | `toolchain/llvm-project` | `xkntt` | `patches/llvm/` |

Neither checkout is committed to this repo (both are in `.gitignore`; together
they are several GB). **The patch series in `patches/` is the artifact of
record** — it must be sufficient to reconstruct the exact fork we tested.

## The model

Each fork has a branch holding our commits on top of a **pinned** upstream base
commit, recorded in `toolchain/upstream-pins.txt`:

```
spike	650c1a25a15d9de58026d2129c7b81794ed279fe	2026-08-27 origin/master
```

`patches/<project>/` is `git format-patch` output for `<pinned base>..<branch>`.

Upstream moves and we will rebase, so the goal is that rebasing is cheap and
that drift between the branch and `patches/` is *detected*, not discovered
months later.

## Commands

```sh
toolchain/patches.sh status            # base, commit count, patch count per project
toolchain/patches.sh export [project]  # regenerate patches/ from the branch
toolchain/patches.sh apply  [project]  # rebuild the branch from patches/ onto the pinned base
toolchain/patches.sh verify [project]  # fail if patches/ and the branch disagree
toolchain/patches.sh rebase [project] [ref]
                                       # rebase onto a new upstream ref, re-export, re-pin
```

## Rules

1. **Commit to the branch, then `export`.** The branch is where you work;
   `patches/` is generated. Never hand-edit a file in `patches/`.
2. **`verify` runs in `make regress`** (test `patches_in_sync`). If you commit to
   a fork branch and forget to re-export, the regression fails. This is the
   whole point — otherwise `patches/` silently stops reproducing the build that
   was actually tested.
3. **Keep commits small and rebaseable.** One logical change each, with a
   message explaining *why*, since these are the commits you will be dragging
   across upstream churn for months.
4. **Re-pin only via `rebase`.** It rebases, re-exports, and updates
   `upstream-pins.txt` in one step so the three can't disagree.

## Notes

- `verify` compares patch *content*, normalising the `From <sha>` header and
  `index` lines. `git am` necessarily creates new commit objects, so a byte-exact
  comparison would report a false mismatch after every round-trip.
- Both checkouts are shallow clones. `rebase` runs `git fetch --unshallow`
  first, because a shallow clone cannot rebase onto history it does not have.
- The round-trip is tested, not assumed: the `xkntt` branch was deleted outright
  and rebuilt from `patches/` alone, then Spike was recompiled and re-checked.

## Current series

### Spike
1. `Xkntt: recognise the vendor extension in the ISA-string parser` — makes
   `--isa=rv32i_zicsr_zicntr_xkntt0p1` work. Spike's generic `x<name>` path
   assumes a dlopen'd plugin and fails with
   `Plugin "imsic_mmio" already registered`; Xkntt is built in instead.
   No instruction semantics yet — those land in C1, once `docs/isa-spec.md` is
   frozen in Phase 1.

### LLVM
Empty. The backend work is Track C2 and later; the checkout is pinned now only
so that the series has a stable base and the tooling is symmetric.
