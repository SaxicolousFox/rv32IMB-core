#!/usr/bin/env python3
"""
MODS_A2 A29 -- the ML-KEM KAT build must be structurally unable to reach the
`seed` CSR, and this checks the binary rather than the intention.

WHY THIS IS A SEPARATE TEST AND NOT A COMMENT.  A29 names the failure mode and
says it is silent in both directions:

  * a KAT that passes because the entropy source was bypassed proves nothing
    about the source;
  * a keygen that is deterministic in the field is a catastrophic bug that no
    known-answer test can ever catch, because being deterministic is exactly
    what a KAT requires.

So neither the KAT passing nor the KAT failing tells you which build you have.
The only thing that does is looking at the instructions.

WHAT IT CHECKS.  Both builds are compiled and disassembled:

  default        must contain ZERO accesses to CSR 0x015
  ENTROPY=1      must contain AT LEAST ONE

The second half is what stops this from being a test that passes because
nothing was built.  A check that only ever asserts an absence is satisfied by
an empty file.
"""
import os, re, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KYBER = os.path.join(ROOT, "sw/kyber")

# `csrrw a0, seed, x0` disassembles either with the CSR's name or its number,
# depending on how new the binutils is about Zkr.  Accept both, because a check
# that silently stops matching is worse than one that never matched.
SEED_RE = re.compile(r"\bcsr\w*\s+\S+,\s*(?:seed|0x015|0x15)\b")


def build_and_dump(env_extra, tag):
    out = os.path.join(ROOT, "build", "kat_seed_check_" + tag)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ)
    env.update(env_extra)
    env["OUT"] = out
    r = subprocess.run(["make", "-s", "TARGET=spike", "NTESTS=1", "OUT=" + out],
                       cwd=KYBER, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")[-3000:])
        raise SystemExit("KATSEED_FAIL: %s build failed" % tag)
    elf = os.path.join(out, "kat.elf")
    if not os.path.exists(elf):
        raise SystemExit("KATSEED_FAIL: %s produced no kat.elf -- the check "
                         "cannot be vacuous, so this is a failure" % tag)
    d = subprocess.run(["riscv-none-elf-objdump", "-d", elf],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return d.stdout.decode("utf-8", "replace")


def main() -> int:
    rc = 0

    kat = build_and_dump({}, "kat")
    hits = SEED_RE.findall(kat)
    print("KAT build (deterministic randombytes):  %d access(es) to CSR 0x015" % len(hits))
    if hits:
        print("KATSEED_FAIL: the KAT build reaches the entropy source.  A known-"
              "answer test that consults a random number generator is not a "
              "known-answer test, and nothing downstream can notice.")
        for h in hits[:5]:
            print("    " + h)
        rc = 1

    ent = build_and_dump({"ENTROPY": "1"}, "entropy")
    hits2 = SEED_RE.findall(ent)
    print("ENTROPY=1 build (seed CSR):             %d access(es) to CSR 0x015" % len(hits2))
    if not hits2:
        print("KATSEED_FAIL: the ENTROPY=1 build contains NO seed access, so the "
              "check above proves nothing -- an absence is trivially satisfied "
              "by a build that does not exist.")
        rc = 1

    if rc == 0:
        print("\nKATSEED_OK: the KAT build cannot reach CSR 0x015 and the "
              "entropy build does")
    return rc


if __name__ == "__main__":
    sys.exit(main())
