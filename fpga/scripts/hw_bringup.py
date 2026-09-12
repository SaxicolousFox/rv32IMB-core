#!/usr/bin/env python3
"""
The hardware loop: program the Arty over JTAG, capture its UART, parse the
capture.  Vivado's hardware manager runs in batch from WSL through cmd.exe; the
COM port is opened by a PowerShell helper whose output file is read via /mnt/c.
The program on the board repeats its report block forever, so the board is
programmed first and the port opened afterwards.

Exit codes: 0 with a `SOC_HW_SKIP:` line when the board or the bitstream is
absent (the regression reports SKIP), 1 when hardware genuinely failed.
"""
import argparse, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb", "fpga"))

VIVADO_WIN = os.environ.get(
    "VIVADO_WIN", r"C:\AMDDesignTools\2025.2\Vivado\bin\vivado.bat")
STAGE_WIN  = os.environ.get("STAGE_WIN_HW", r"C:\Users\liamf\rv32imb-core-hw")
STAGE_WSL  = os.environ.get("STAGE_WSL_HW", "/mnt/c/Users/liamf/rv32imb-core-hw")
BIT_DEFAULT = os.path.join(ROOT, "fpga/build/soc/rvntt_soc_top.bit")
PARSER_DEFAULT = os.path.join(ROOT, "tb/fpga/parse_soc_uart.py")

# Every directory whose contents end up inside a bitstream, for the
# stale-bitstream warning.  The upstream checkouts are pristine by policy.
SOURCE_DIRS = ("rtl", "sw/soc", "sw/bench", "fpga/constraints", "fpga/generated",
               "toolchain/riscv-tests/benchmarks/dhrystone",
               "toolchain/coremark")


def run(cmd, **kw):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    return r.returncode, r.stdout.decode("utf-8", "replace")


# The Arty's FT2232H: channel A is JTAG, channel B is the USB-UART bridge.
ARTY_PNP = "FTDIBUS*VID_0403+PID_6010*"


def find_arty_port():
    """The Arty's COM port (e.g. 'COM7'), or None.  PowerShell rather than
    Vivado, which takes ~40 s to report an absent board."""
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

    # -log/-journal must precede -tclargs: everything after -tclargs is argv.
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
    ap.add_argument("--send-byte", type=lambda s: int(s, 0), default=0x5A,
                    help="byte to inject into the UART; -1 to send nothing")
    ap.add_argument("--parser", default=PARSER_DEFAULT)
    ap.add_argument("--parser-arg", action="append", default=None,
                    help="extra argument for the parser; repeatable")
    ap.add_argument("--out-name", default="uart.log",
                    help="capture file name under the Windows staging dir")
    ap.add_argument("--no-program", action="store_true",
                    help="capture from whatever is already configured")
    ap.add_argument("--keep", default=None, help="copy the capture here")
    ap.add_argument("--regress", action="store_true",
                    help="skip unless RVNTT_HW=1 (used by the regression)")
    a = ap.parse_args()

    # Opt-in from the regression: this reconfigures the FPGA.
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
    # A bitstream older than the newest source is probably a leftover from a
    # build that failed timing (which writes no .bit).  Warn, do not refuse.
    if not a.no_program and os.path.exists(a.bit):
        newest = 0.0
        for d in SOURCE_DIRS:
            for root, _, files in os.walk(os.path.join(ROOT, d)):
                for f in files:
                    newest = max(newest, os.path.getmtime(os.path.join(root, f)))
        age = newest - os.path.getmtime(a.bit)
        if age > 0:
            print("WARNING: %s is %.0f s OLDER than the newest source file.\n"
                  "         A build that fails timing writes no bitstream, so this\n"
                  "         may be a leftover from a previous design."
                  % (os.path.relpath(a.bit, ROOT), age))

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

    send = None if a.send_byte < 0 else a.send_byte
    print("=== capturing %ds from %s (%s) ==="
          % (a.seconds, port,
             "injecting 0x%02X" % send if send is not None else "receive only"))
    cap = capture(a.seconds, send, port, a.out_name)
    if cap is None:
        print("SOC_HW_FAIL: serial capture failed")
        return 1

    if a.keep:
        os.makedirs(os.path.dirname(os.path.abspath(a.keep)), exist_ok=True)
        with open(cap, "rb") as f, open(a.keep, "wb") as g:
            g.write(f.read())
        print("capture kept at %s" % os.path.relpath(a.keep, ROOT))

    pargs = a.parser_arg
    if pargs is None:
        pargs = ([] if send is None else ["--expect-echo", "0x%02X" % send])
    rc, out = run([sys.executable, a.parser, cap] + pargs)
    print(out.strip())
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
