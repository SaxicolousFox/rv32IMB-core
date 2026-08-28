#!/usr/bin/env python3
"""
P0.4 guard: patches/ must stay in sync with the fork branches.

Without this, the patch series silently rots -- someone commits to the spike
branch, forgets to re-export, and patches/ no longer reproduces the build that
was actually tested.  Projects that are not cloned are skipped, not failed.
"""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SH   = os.path.join(ROOT, "toolchain", "patches.sh")


def main() -> int:
    if not os.path.exists(SH):
        print("SKIP: toolchain/patches.sh missing")
        return 0
    r = subprocess.run(["bash", SH, "verify"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    print(out.strip())
    if r.returncode != 0:
        print("PATCH_SYNC_FAIL")
        return 1
    print("PATCH_SYNC_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
