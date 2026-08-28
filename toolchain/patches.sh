#!/usr/bin/env bash
# P0.4 -- patch-series discipline for the third-party forks (Spike, LLVM).
#
# The model: each upstream checkout carries a branch (default 'xkntt') holding
# our commits on top of a PINNED upstream base.  patches/<project>/ holds the
# `git format-patch` output for that branch.  Upstream moves; rebasing must stay
# cheap, so the base commit is recorded in toolchain/upstream-pins.txt and the
# export/apply round-trip is verifiable.
#
# Usage:
#   toolchain/patches.sh export [project]   regenerate patches/<project>/ from the branch
#   toolchain/patches.sh apply  [project]   recreate the branch from patches/ onto the pinned base
#   toolchain/patches.sh rebase [project] [ref]
#                                           rebase the branch onto a NEW upstream ref,
#                                           then re-export and re-pin
#   toolchain/patches.sh status             show each project's base, branch, commit count
#   toolchain/patches.sh verify [project]   check patches/ is in sync with the branch
#
# 'project' is spike or llvm; omitting it means all configured projects.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PINS="$HERE/upstream-pins.txt"
BRANCH="${XKNTT_BRANCH:-xkntt}"

# project -> checkout dir (relative to repo root)
declare -A SRCDIR=(
  [spike]="toolchain/spike-src"
  [llvm]="toolchain/llvm-project"
)
# project -> upstream ref that the series is based on (default when re-pinning)
declare -A UPSTREAM=(
  [spike]="origin/master"
  [llvm]="origin/main"
)

# Careful: under `set -e` + pipefail, both `ls <nonmatching glob> | wc -l` and
# `find <missing dir> | wc -l` abort the whole script rather than yielding 0.
count_patches() {
  local d="$ROOT/patches/$1"
  [ -d "$d" ] || { echo 0; return 0; }
  find "$d" -maxdepth 1 -name '*.patch' | wc -l
}

projects() { if [ $# -gt 0 ] && [ -n "${1:-}" ]; then echo "$1"; else echo "spike llvm"; fi; }

have() { local p="$1"; [ -d "$ROOT/${SRCDIR[$p]}/.git" ]; }

# NB: `|| true` matters -- grep exits 1 for an unpinned project, and under
# `set -e` with pipefail that would abort the whole script instead of returning
# an empty pin for the caller to handle.
pin_get() { { grep -E "^$1[[:space:]]" "$PINS" 2>/dev/null || true; } | awk '{print $2}' | head -1; }

pin_set() {
  local p="$1" sha="$2" desc="${3:-}"
  touch "$PINS"
  local tmp; tmp="$(mktemp)"
  grep -vE "^$p[[:space:]]" "$PINS" > "$tmp" 2>/dev/null || true
  printf '%s\t%s\t%s\n' "$p" "$sha" "$desc" >> "$tmp"
  sort -o "$PINS" "$tmp"; rm -f "$tmp"
}

do_export() {
  local p="$1" dir="$ROOT/${SRCDIR[$p]}" base
  base="$(pin_get "$p")"
  [ -n "$base" ] || { echo "ERROR: no pinned base for $p; run 'rebase' or 'status' first" >&2; return 1; }
  local out="$ROOT/patches/$p"
  rm -rf "$out"; mkdir -p "$out"
  git -C "$dir" format-patch --no-signature -o "$out" "$base..$BRANCH" >/dev/null
  local n; n=$(count_patches "$p")
  echo "$p: exported $n patch(es) to patches/$p/ (base ${base:0:12})"
}

do_apply() {
  local p="$1" dir="$ROOT/${SRCDIR[$p]}" base
  base="$(pin_get "$p")"
  [ -n "$base" ] || { echo "ERROR: no pinned base for $p" >&2; return 1; }
  git -C "$dir" checkout -q --detach "$base"
  git -C "$dir" branch -f "$BRANCH" "$base"
  git -C "$dir" checkout -q "$BRANCH"
  shopt -s nullglob
  local patches=("$ROOT/patches/$p"/*.patch)
  shopt -u nullglob
  if [ ${#patches[@]} -eq 0 ]; then echo "$p: no patches to apply"; return 0; fi
  git -C "$dir" am "${patches[@]}"
  echo "$p: applied ${#patches[@]} patch(es) onto ${base:0:12}"
}

do_rebase() {
  local p="$1" newref="${2:-${UPSTREAM[$p]}}" dir="$ROOT/${SRCDIR[$p]}"
  # A shallow clone cannot rebase onto history it does not have.
  if [ -f "$dir/.git/shallow" ]; then
    echo "$p: unshallowing before rebase (this may take a while)..."
    git -C "$dir" fetch --unshallow origin || git -C "$dir" fetch origin
  fi
  git -C "$dir" fetch origin
  local sha; sha="$(git -C "$dir" rev-parse "$newref")"
  git -C "$dir" checkout -q "$BRANCH"
  git -C "$dir" rebase --onto "$sha" "$(pin_get "$p")" "$BRANCH"
  pin_set "$p" "$sha" "$(git -C "$dir" log -1 --format=%cs "$sha") $newref"
  do_export "$p"
  echo "$p: rebased onto ${sha:0:12} and re-pinned"
}

do_status() {
  printf "%-8s %-14s %-8s %s\n" PROJECT BASE COMMITS PATCHES
  for p in spike llvm; do
    if ! have "$p"; then printf "%-8s %-14s %-8s %s\n" "$p" "(not cloned)" "-" "-"; continue; fi
    local dir="$ROOT/${SRCDIR[$p]}" base n np
    base="$(pin_get "$p")"; base="${base:-unpinned}"
    n="-"
    if [ "$base" != "unpinned" ] && git -C "$dir" rev-parse --verify -q "$BRANCH" >/dev/null; then
      n=$(git -C "$dir" rev-list --count "$base..$BRANCH" 2>/dev/null || echo "?")
    fi
    np=$(count_patches "$p")
    printf "%-8s %-14s %-8s %s\n" "$p" "${base:0:12}" "$n" "$np"
  done
}

# format-patch stamps the commit SHA into the "From <sha> Mon Sep 17 ..." header.
# `git am` necessarily produces a new commit object, so that line differs after
# any export->apply round-trip even when the change itself is identical.  Compare
# on content, not on the commit identity.
normalise_patches() {
  local src="$1" dst="$2"
  mkdir -p "$dst"
  shopt -s nullglob
  for f in "$src"/*.patch; do
    sed -e '1s/^From [0-9a-f]\{7,40\} /From <commit> /' \
        -e 's/^index [0-9a-f]*\.\.[0-9a-f]*/index <blob>/' \
        "$f" > "$dst/$(basename "$f")"
  done
  shopt -u nullglob
}

do_verify() {
  # Round-trip check: what is exported must equal what is on the branch.
  local p="$1" dir="$ROOT/${SRCDIR[$p]}" base
  base="$(pin_get "$p")"
  # A project can be cloned but not yet carry a series (e.g. LLVM before C2).
  if [ -z "$base" ]; then echo "$p: no pinned base yet, skipping"; return 0; fi
  if ! git -C "$dir" rev-parse --verify -q "$BRANCH" >/dev/null; then
    echo "$p: no '$BRANCH' branch yet, skipping"; return 0
  fi
  local tmp; tmp="$(mktemp -d)"
  git -C "$dir" format-patch --no-signature -o "$tmp/raw" "$base..$BRANCH" >/dev/null
  normalise_patches "$tmp/raw" "$tmp/a"
  normalise_patches "$ROOT/patches/$p" "$tmp/b"
  if diff -r -q "$tmp/a" "$tmp/b" >/dev/null 2>&1; then
    echo "$p: patches/ is IN SYNC with branch $BRANCH"
    rm -rf "$tmp"; return 0
  fi
  echo "$p: OUT OF SYNC -- run 'toolchain/patches.sh export $p'" >&2
  diff -r -u "$tmp/b" "$tmp/a" | head -40 || true
  rm -rf "$tmp"; return 1
}

cmd="${1:-status}"; shift || true
case "$cmd" in
  export|apply|rebase|verify)
    for p in $(projects "${1:-}"); do
      have "$p" || { echo "$p: not cloned, skipping"; continue; }
      "do_$cmd" "$p" "${2:-}"
    done ;;
  status) do_status ;;
  *) echo "unknown command: $cmd" >&2; exit 2 ;;
esac
