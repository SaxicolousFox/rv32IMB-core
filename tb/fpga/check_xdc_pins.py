#!/usr/bin/env python3
"""
Check every constraint file's pin assignments against the board's own pinout.

fpga/constraints/arty_a7_100t_pins.txt is extracted mechanically from the
vendor XDC, and this compares every port in every XDC against it -- a swapped
output pin is invisible to elaboration, synthesis, timing and every UART
check.  It does not check whether the design drives the right signal onto a
correctly-named port.
"""
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONSTR = os.path.join(ROOT, "fpga", "constraints")
PINS   = os.path.join(CONSTR, "arty_a7_100t_pins.txt")

RE_ASSIGN = re.compile(
    r"^\s*set_property\s+-dict\s*\{\s*PACKAGE_PIN\s+(\S+)\s+IOSTANDARD\s+(\S+)\s*\}"
    r"\s*\[get_ports\s*\{\s*([^}]+?)\s*\}\]", re.M)


def load_pins(path=PINS):
    ref = {}
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            port, pin, std = line.split()
            ref[port] = (pin, std)
    return ref


def check_file(path, ref):
    findings = []
    with open(path) as f:
        text = f.read()
    n = 0
    for pin, std, port in RE_ASSIGN.findall(text):
        port = port.strip()
        n += 1
        if port not in ref:
            findings.append("%s: port %r is not a pin on this board"
                            % (os.path.basename(path), port))
            continue
        want_pin, want_std = ref[port]
        if pin != want_pin:
            findings.append("%s: %s is on %s, not %s  <-- WRONG PIN"
                            % (os.path.basename(path), port, want_pin, pin))
        if std != want_std:
            findings.append("%s: %s should be %s, not %s"
                            % (os.path.basename(path), port, want_std, std))
    return n, findings


# (name, text, why it must be rejected) -- fed to the checker as a synthetic XDC.
SELFTESTS = [
    ("rgb_swapped",
     "set_property -dict { PACKAGE_PIN E1 IOSTANDARD LVCMOS33 } [get_ports { led0_r }]",
     "red and blue transposed"),
    ("led_shifted",
     "set_property -dict { PACKAGE_PIN J5 IOSTANDARD LVCMOS33 } [get_ports { led[0] }]",
     "an off-by-one across a bus, which looks entirely plausible"),
    ("uart_reversed",
     "set_property -dict { PACKAGE_PIN A9 IOSTANDARD LVCMOS33 } [get_ports { uart_rxd_out }]",
     "TX/RX swapped, the classic Arty UART bug"),
    ("unknown_port",
     "set_property -dict { PACKAGE_PIN E3 IOSTANDARD LVCMOS33 } [get_ports { clk_100 }]",
     "a port name this board does not have"),
    ("wrong_iostandard",
     "set_property -dict { PACKAGE_PIN C2 IOSTANDARD LVCMOS18 } [get_ports { ck_rst }]",
     "a 1.8 V standard on a 3.3 V bank"),
]


def selftest(ref) -> int:
    import tempfile
    fails = 0
    good = "set_property -dict { PACKAGE_PIN G6 IOSTANDARD LVCMOS33 } [get_ports { led0_r }]"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "good.xdc")
        open(p, "w").write(good)
        _, f = check_file(p, ref)
        print("%-18s %s" % ("GOOD", "accepted" if not f else "REJECTED %s" % f))
        if f:
            print("  FAIL: the checker rejects a correct assignment")
            fails += 1
        for name, text, why in SELFTESTS:
            p = os.path.join(d, name + ".xdc")
            open(p, "w").write(text)
            _, f = check_file(p, ref)
            print("%-18s %s   (%s)" % (name, "rejected" if f else "accepted", why))
            if not f:
                print("  FAIL: mutation %r was ACCEPTED" % name)
                fails += 1
    if fails:
        print("XDC_PIN_SELFTEST_FAIL (%d)" % fails)
        return 1
    print("XDC_PIN_SELFTEST_OK (%d mutations, all rejected)" % len(SELFTESTS))
    return 0


def main() -> int:
    ref = load_pins()
    if "--selftest" in sys.argv:
        return selftest(ref)

    # The self-test runs first, every time; it costs microseconds.
    if selftest(ref) != 0:
        return 1
    print()

    total, findings = 0, []
    files = sorted(f for f in os.listdir(CONSTR) if f.endswith(".xdc"))
    if not files:
        print("XDC_PIN_FAIL: no constraint files found")
        return 1
    for name in files:
        n, f = check_file(os.path.join(CONSTR, name), ref)
        total += n
        findings += f
        print("  %-28s %d pin assignment(s)" % (name, n))
    for f in findings:
        print("  FINDING: %s" % f)
    if findings:
        print("XDC_PIN_FAIL (%d finding(s))" % len(findings))
        return 1
    print("XDC_PIN_OK (%d assignment(s) in %d file(s) match the board pinout)"
          % (total, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
