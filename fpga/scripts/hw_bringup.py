#!/usr/bin/env python3
"""
The A12 hardware loop, with no human in it.

    program the board over JTAG  ->  capture the UART  ->  parse the capture

Each of the three is something the agent side can drive: Vivado's hardware
manager runs in batch from WSL through cmd.exe, and the COM port is opened by a
PowerShell helper whose output file is readable through /mnt/c.  What is left for
a person is the part that genuinely needs eyes: the LEDs, and deciding whether
the board should be plugged in at all.

Ordering note: the program on the board REPEATS its report block forever, so
programming and capturing do not have to be interleaved.  The board is
configured first and the port opened afterwards, which avoids having two
processes racing for the FT2232.

Exit codes follow the project's third-party-checkout convention (riscv_tests,
riscof, riscv-formal): 0 with a `SOC_HW_SKIP:` line when the board or the
bitstream is absent, 1 when hardware genuinely failed.  tb/run_regress.py reads
that line and reports SKIP -- a missing board must never FAIL, and must never
look like a passing test either.
"""
import argparse, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb", "fpga"))

VIVADO_WIN = os.environ.get(
    "VIVADO_WIN", r"C:\AMDDesignTools\2025.2\Vivado\bin\vivado.bat")
STAGE_WIN  = os.environ.get("STAGE_WIN_HW", r"C:\Users\liamf\rvntt-hw")
STAGE_WSL  = os.environ.get("STAGE_WSL_HW", "/mnt/c/Users/liamf/rvntt-hw")
BIT_DEFAULT = os.path.join(ROOT, "fpga/build/soc/rvntt_soc_top.bit")


def run(cmd, **kw):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    return r.returncode, r.stdout.decode("utf-8", "replace")


# The Arty's FT2232H exposes TWO interfaces on one USB device: channel A is the
# JTAG programmer and channel B is the USB-UART bridge.  They share a serial
# number with an A/B suffix, which is what makes this identification exact
# rather than "some COM port exists" -- a USB modem or an Arduino would satisfy
# the loose test and then fail confusingly at the first read.
ARTY_PNP = "FTDIBUS*VID_0403+PID_6010*"


def find_arty_port():
    """The Arty's COM port (e.g. 'COM7'), or None if it is not plugged in.

    PowerShell rather than Vivado: Vivado takes about forty seconds to tell you
    the board is absent, and this runs on every regression.
    """
    rc, out = run(["powershell.exe", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_PnPEntity | Where-Object { "
                   "$_.PNPDeviceID -like '%s' -and $_.Name -match 'COM[0-9]+' } "
                   "| ForEach-Object { $_.Name }" % ARTY_PNP])
    m = re.search(r"COM(\d+)", out)
    return "COM%s" % m.group(1) if m else None


def program(bit):
    os.makedirs(STAGE_WSL, exist_ok=True)
    for f in ("program_soc.tcl", "serial_capture.ps1"):
        src = os.path.join(ROOT, "fpga/scripts", f)
        with open(src, "rb") as a, open(os.path.join(STAGE_WSL, f), "wb") as b:
            b.write(a.read())
    win_bit = os.path.join(STAGE_WIN, "soc.bit")
    with open(bit, "rb") as a, open(os.path.join(STAGE_WSL, "soc.bit"), "wb") as b:
        b.write(a.read())

    # -log/-journal MUST precede -tclargs: everything after -tclargs is handed to
    # the script as argv.  Getting this backwards is a trap this project has
    # already paid for once (see fpga/scripts/elab_core.sh).
    cmd = ("cd /d %s && %s -mode batch -log program.log -journal program.jou "
           "-source program_soc.tcl -tclargs %s"
           % (STAGE_WIN, VIVADO_WIN, win_bit.replace("\\", "/")))
    rc, out = run(["cmd.exe", "/c", cmd], cwd=STAGE_WSL)
    for line in out.splitlines():
        if line.startswith(("=== device", "=== DONE", "PROGRAM_")):
            print(line)
    if "PROGRAM_OK" not in out:
        sys.stderr.write(out[-3000:])
        return False
    return True


def capture(seconds, send_byte, port, out_name="uart.log"):
    win_out = os.path.join(STAGE_WIN, out_name)
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", os.path.join(STAGE_WIN, "serial_capture.ps1"),
           "-Port", port, "-Out", win_out, "-Seconds", str(seconds)]
    if send_byte is not None:
        cmd += ["-SendByte", str(send_byte)]
    rc, out = run(cmd, cwd=STAGE_WSL)
    print(out.strip().splitlines()[-1] if out.strip() else "(no capture output)")
    path = os.path.join(STAGE_WSL, out_name)
    if rc != 0 or not os.path.exists(path):
        sys.stderr.write(out[-2000:])
        return None
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default=BIT_DEFAULT)
    ap.add_argument("--port", default=os.environ.get("ARTY_COM"),
                    help="COM port; auto-detected from the FT2232 by default")
    ap.add_argument("--seconds", type=int, default=8)
    ap.add_argument("--send-byte", type=lambda s: int(s, 0), default=0x5A)
    ap.add_argument("--no-program", action="store_true",
                    help="capture from whatever is already configured")
    ap.add_argument("--keep", default=None, help="copy the capture here")
    ap.add_argument("--regress", action="store_true",
                    help="skip unless RVNTT_HW=1 (used by the regression)")
    a = ap.parse_args()

    # Opt-in from the regression.  Running this reconfigures the FPGA, which is a
    # side effect `make regress` has no business having by default -- the board
    # may be showing something else, and a test that silently reprograms hardware
    # is a surprise, not a check.  It still SKIPs loudly, so it cannot rot into
    # "we have no hardware test".
    if a.regress and os.environ.get("RVNTT_HW") != "1":
        print("SOC_HW_SKIP: opt-in -- this test PROGRAMS the Arty over JTAG.\n"
              "  Plug the board in, then:  RVNTT_HW=1 python3 tb/run_regress.py\n"
              "  or run it directly:       python3 fpga/scripts/hw_bringup.py")
        return 0

    port = a.port or find_arty_port()
    if port is None:
        print("SOC_HW_SKIP: no Arty enumerated -- the board is not connected.\n"
              "  Plug the Arty into USB (it appears as both a JTAG programmer\n"
              "  and a USB-UART bridge) and re-run.")
        return 0
    print("=== Arty found on %s ===" % port)
    if not a.no_program and not os.path.exists(a.bit):
        print("SOC_HW_SKIP: no bitstream at %s.\n"
              "  Build one with:  bash fpga/scripts/build_soc.sh"
              % os.path.relpath(a.bit, ROOT))
        return 0

    if not a.no_program:
        print("=== programming %s ===" % os.path.relpath(a.bit, ROOT))
        if not program(a.bit):
            print("SOC_HW_FAIL: programming failed")
            return 1

    print("=== capturing %ds from %s (injecting 0x%02X) ==="
          % (a.seconds, port, a.send_byte))
    cap = capture(a.seconds, a.send_byte, port)
    if cap is None:
        print("SOC_HW_FAIL: serial capture failed")
        return 1

    if a.keep:
        os.makedirs(os.path.dirname(os.path.abspath(a.keep)), exist_ok=True)
        with open(cap, "rb") as f, open(a.keep, "wb") as g:
            g.write(f.read())
        print("capture kept at %s" % os.path.relpath(a.keep, ROOT))

    rc, out = run([sys.executable,
                   os.path.join(ROOT, "tb/fpga/parse_soc_uart.py"), cap,
                   "--expect-echo", "0x%02X" % a.send_byte])
    print(out.strip())
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
