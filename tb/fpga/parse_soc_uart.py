#!/usr/bin/env python3
"""
Parse a UART capture from the A12 SoC and decide whether the board is running.

This is the checker that turns "Hello arrived" from something a person reads
into something a script decides, which is what makes the hardware loop
automatic.  It is therefore itself a checking mechanism, and gets the same
treatment as every other one in this project: `--selftest` feeds it captures
that are wrong in nine specific ways and requires it to reject every one.  A
parser that has only ever seen good input is indistinguishable from `return 0`.

Expected block, repeating (see sw/soc/hello.c):

    === rvntt A12 ===
    hello=Hello, world!
    sw=0xN btn=0xN
    mcycle=0xXXXXXXXX minstret=0xXXXXXXXX
    echo=0xNN | echo=none [ overrun]
    img=OK | img=BAD 0xXXXXXXXX
    iter=0xXXXXXXXX
    === end ===
"""
import argparse, re, sys

START = "=== rvntt A12 ==="
END   = "=== end ==="

RE_HELLO = re.compile(r"^hello=Hello, world!$")
RE_GPIO  = re.compile(r"^sw=0x([0-9A-F])\s+btn=0x([0-9A-F])$")
RE_CNT   = re.compile(r"^mcycle=0x([0-9A-F]{8})\s+minstret=0x([0-9A-F]{8})$")
RE_ECHO  = re.compile(r"^echo=(none|0x([0-9A-F]{2}))( overrun)?$")
RE_IMG   = re.compile(r"^img=(OK|BAD 0x[0-9A-F]{8})$")
RE_ITER  = re.compile(r"^iter=0x([0-9A-F]{8})$")


class Block(object):
    __slots__ = ("sw", "btn", "mcycle", "minstret", "echo", "overrun", "img",
                 "iter")


def parse_blocks(text):
    """Every COMPLETE, well-formed block in `text`.  Malformed ones are dropped.

    Dropping rather than raising is deliberate: a capture legitimately starts
    mid-block, and the first partial one is not an error.  The checks that
    matter are applied to what survives, in check().
    """
    blocks, errors = [], []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        if lines[i].strip() != START:
            i += 1
            continue
        j = i + 1
        body = []
        while j < len(lines) and lines[j].strip() != END:
            if lines[j].strip() == START:      # a truncated block
                break
            body.append(lines[j].strip())
            j += 1
        if j >= len(lines) or lines[j].strip() != END:
            i = j
            continue
        b = Block()
        ok = True
        if len(body) != 6:
            errors.append("block at line %d has %d lines, expected 6" % (i + 1, len(body)))
            ok = False
        else:
            m = RE_HELLO.match(body[0])
            if not m:
                errors.append("bad hello line: %r" % body[0]); ok = False
            m = RE_GPIO.match(body[1])
            if m:
                b.sw, b.btn = int(m.group(1), 16), int(m.group(2), 16)
            else:
                errors.append("bad gpio line: %r" % body[1]); ok = False
            m = RE_CNT.match(body[2])
            if m:
                b.mcycle, b.minstret = int(m.group(1), 16), int(m.group(2), 16)
            else:
                errors.append("bad counter line: %r" % body[2]); ok = False
            m = RE_ECHO.match(body[3])
            if m:
                b.echo = None if m.group(1) == "none" else int(m.group(2), 16)
                b.overrun = m.group(3) is not None
            else:
                errors.append("bad echo line: %r" % body[3]); ok = False
            m = RE_IMG.match(body[4])
            if m:
                b.img = m.group(1)
            else:
                errors.append("bad img line: %r" % body[4]); ok = False
            m = RE_ITER.match(body[5])
            if m:
                b.iter = int(m.group(1), 16)
            else:
                errors.append("bad iter line: %r" % body[5]); ok = False
        if ok:
            blocks.append(b)
        i = j + 1
    return blocks, errors


def check(text, expect_echo=None, min_blocks=2, expect_sw=None, expect_btn=None):
    """Return (ok, list_of_findings).  Findings are printed by the caller."""
    blocks, findings = parse_blocks(text)
    findings = list(findings)

    if len(blocks) < min_blocks:
        findings.append("only %d complete block(s), need %d"
                        % (len(blocks), min_blocks))
        return False, findings

    # The program loops forever, so consecutive blocks must show it moving.  A
    # board that emitted one block and hung, or a capture file left over from a
    # previous run, passes every per-block check and fails these two.
    for a, b in zip(blocks, blocks[1:]):
        if b.iter != a.iter + 1:
            findings.append("iter did not advance by one: 0x%08X -> 0x%08X"
                            % (a.iter, b.iter))
        if b.mcycle <= a.mcycle:
            findings.append("mcycle did not advance: 0x%08X -> 0x%08X"
                            % (a.mcycle, b.mcycle))
        if b.minstret <= a.minstret:
            findings.append("minstret did not advance: 0x%08X -> 0x%08X"
                            % (a.minstret, b.minstret))

    # The program re-reads its own .text.init and compares it against the value
    # taken before the first store.  BAD means something wrote over the running
    # image -- an MMIO store aliasing into RAM is the case this exists for, and
    # nothing else here can see it, because the clobbered words are crt0's and
    # crt0 never runs again.
    for b in blocks:
        if b.img != "OK":
            findings.append("program image changed under itself: %s" % b.img)
            break

    if expect_echo is not None:
        if not any(b.echo == expect_echo for b in blocks):
            findings.append("injected byte 0x%02X never echoed back (RX path)"
                            % expect_echo)
    if expect_sw is not None and blocks[0].sw != expect_sw:
        findings.append("sw reads 0x%X, expected 0x%X" % (blocks[0].sw, expect_sw))
    if expect_btn is not None and blocks[0].btn != expect_btn:
        findings.append("btn reads 0x%X, expected 0x%X" % (blocks[0].btn, expect_btn))

    return (len(findings) == 0), findings


GOOD = "".join(
    "=== rvntt A12 ===\r\n"
    "hello=Hello, world!\r\n"
    "sw=0x0 btn=0x0\r\n"
    "mcycle=0x%08X minstret=0x%08X\r\n"
    "echo=%s\r\n"
    "img=OK\r\n"
    "iter=0x%08X\r\n"
    "=== end ===\r\n" % (0x1000 * (n + 1), 0x800 * (n + 1),
                         "0x5A" if n == 1 else "none", n)
    for n in range(3))

# Each entry is (name, mutated capture, why the parser must reject it).  These
# are the failures that would otherwise be reported as a pass on hardware.
SELFTESTS = [
    ("truncated",       GOOD[:len(GOOD) // 3],
     "a capture cut short must not count as blocks"),
    ("no_blocks",       "garbage on the line\r\n" * 20,
     "line noise must not parse as a block"),
    ("wrong_banner",    GOOD.replace("Hello, world!", "Hello, world?"),
     "a corrupted payload byte must be caught, not tolerated"),
    ("iter_stuck",      GOOD.replace("iter=0x00000001", "iter=0x00000000")
                            .replace("iter=0x00000002", "iter=0x00000000"),
     "a hung program repeating one block must fail"),
    ("mcycle_stuck",    re.sub(r"mcycle=0x[0-9A-F]{8}", "mcycle=0x00001000", GOOD),
     "a stopped cycle counter must fail even if iter advances"),
    ("minstret_stuck",  re.sub(r"minstret=0x[0-9A-F]{8}", "minstret=0x00000800", GOOD),
     "a stopped retire counter must fail"),
    ("missing_line",    GOOD.replace("sw=0x0 btn=0x0\r\n", "", 1),
     "a block with a line missing must not silently parse"),
    ("half_hex",        GOOD.replace("mcycle=0x00001000", "mcycle=0x1000"),
     "a short hex field means the formatter is wrong and must fail"),
    ("no_echo",         GOOD.replace("echo=0x5A", "echo=none"),
     "with --expect-echo, a missing echo must fail the RX path"),
    ("img_bad",         GOOD.replace("img=OK", "img=BAD 0xDEADBEEF", 1),
     "the program overwriting its own image must fail even though every "
     "other field is perfect"),
]


def selftest() -> int:
    ok, findings = check(GOOD, expect_echo=0x5A, expect_sw=0x0, expect_btn=0x0)
    print("%-16s %s" % ("GOOD", "accepted" if ok else "REJECTED %s" % findings))
    fails = 0 if ok else 1
    if not ok:
        print("  FAIL: the parser rejects a known-good capture")

    for name, text, why in SELFTESTS:
        bad_ok, bad_find = check(text, expect_echo=0x5A)
        verdict = "accepted" if bad_ok else "rejected"
        print("%-16s %s   (%s)" % (name, verdict, why))
        if bad_ok:
            print("  FAIL: mutation %r was ACCEPTED -- the parser cannot see it" % name)
            fails += 1

    if fails:
        print("PARSER_SELFTEST_FAIL (%d)" % fails)
        return 1
    print("PARSER_SELFTEST_OK (%d mutations, all rejected)" % len(SELFTESTS))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", nargs="?")
    ap.add_argument("--expect-echo", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--expect-sw", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--expect-btn", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--min-blocks", type=int, default=2)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.capture:
        ap.error("a capture file is required unless --selftest")

    with open(a.capture, "r", errors="replace") as f:
        text = f.read()

    blocks, _ = parse_blocks(text)
    ok, findings = check(text, expect_echo=a.expect_echo, min_blocks=a.min_blocks,
                         expect_sw=a.expect_sw, expect_btn=a.expect_btn)

    print("capture: %s (%d bytes, %d complete block(s))"
          % (a.capture, len(text), len(blocks)))
    for b in blocks[:3]:
        print("  iter=0x%08X sw=0x%X btn=0x%X mcycle=0x%08X minstret=0x%08X "
              "echo=%s img=%s"
              % (b.iter, b.sw, b.btn, b.mcycle, b.minstret,
                 "none" if b.echo is None else "0x%02X" % b.echo, b.img))
    if len(blocks) >= 2:
        d_cyc = blocks[1].mcycle - blocks[0].mcycle
        d_ins = blocks[1].minstret - blocks[0].minstret
        print("  between blocks: %d cycles, %d instructions retired (IPC %.3f)"
              % (d_cyc, d_ins, (d_ins / d_cyc) if d_cyc else 0.0))
    for f in findings:
        print("  FINDING: %s" % f)
    print("SOC_UART_OK" if ok else "SOC_UART_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
