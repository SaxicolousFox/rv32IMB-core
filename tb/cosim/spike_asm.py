"""
Helpers for building bare-metal RV32 programs that Spike runs and this harness
reads back through --log-commits.

The commit log is not a convenience here: it is the exact artifact the A5
cosimulation differ will compare RTL against, so exercising it now means the
format is already proven by the time Track A needs it.
"""
import os, re, subprocess, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LD   = os.path.join(ROOT, "sw", "tests", "link.ld")

ISA_XKNTT = "rv32i_zicsr_zicntr_xkntt0p1"
ISA_BASE  = "rv32i_zicsr_zicntr"

# A bare-metal program that takes a trap with no handler installed loops
# forever re-taking it.  Every program therefore installs a handler that exits
# with a distinctive code, which turns "did this encoding trap?" into an exit
# status instead of a string match on Spike's stderr.
TRAP_EXIT_CODE = 42

PROLOGUE = """        .section .text.init
        .globl _start
_start:
        la      t0, trap_handler
        csrw    mtvec, t0
"""

TRAP_HANDLER = """
        .text
        .align 2
trap_handler:
        li      t0, (%d << 1) | 1
        la      t1, tohost
        sw      t0, 0(t1)
8:      j       8b
""" % TRAP_EXIT_CODE

# The other handler: step over the faulting instruction and carry on.  This is
# what lets one program probe thousands of candidate encodings in a single run,
# which is plan A3's decoder-comparison method applied to Spike.
TRAP_HANDLER_SKIP = """
        .text
        .align 2
        .globl trap_handler
trap_handler:
        csrr    t0, mepc
        addi    t0, t0, 4
        csrw    mepc, t0
        mret
"""

EPILOGUE = """
        li      t0, 1                   # HTIF: exit code 0
        la      t1, tohost
        sw      t0, 0(t1)
9:      j       9b

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
"""

# `core   0: 3 0x80000000 (0x00500513) x10 0x00000005`
COMMIT_RE = re.compile(
    r"^core\s+\d+:\s+\d+\s+0x([0-9a-f]+)\s+\((0x[0-9a-f]+)\)(.*)$")
WRITE_RE = re.compile(r"\bx(\d+)\s+(0x[0-9a-f]+)")


def build(body, tmp, name="prog", data="", trap_mode="exit"):
    """Assemble and link a bare-metal program from an instruction body."""
    src = os.path.join(tmp, name + ".S")
    elf = os.path.join(tmp, name + ".elf")
    with open(src, "w") as f:
        # Data goes AFTER the epilogue: the epilogue leaves us in .tohost,
        # and a .data table emitted mid-.text would be executed.
        handler = TRAP_HANDLER if trap_mode == "exit" else TRAP_HANDLER_SKIP
        f.write(PROLOGUE + body + EPILOGUE + handler + data)
    r = subprocess.run(
        ["riscv-none-elf-gcc", "-march=rv32i_zicsr", "-mabi=ilp32",
         "-nostdlib", "-nostartfiles", "-T", LD, "-o", elf, src],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise RuntimeError(f"build failed:\n{r.stdout.decode()}\n--- source ---\n"
                           + open(src).read())
    return elf


def run(elf, isa=ISA_XKNTT, log_commits=True, timeout=600):
    """Run under Spike.  Returns (returncode, [(pc, insn_word, [(rd, val)])])."""
    cmd = ["spike", f"--isa={isa}"]
    if log_commits:
        cmd.append("--log-commits")
    cmd.append(elf)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout)
    text = r.stdout.decode("utf-8", "replace")
    trace = []
    for line in text.splitlines():
        m = COMMIT_RE.match(line)
        if not m:
            continue
        pc, word, tail = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
        writes = [(int(a), int(b, 16)) for a, b in WRITE_RE.findall(tail)]
        trace.append((pc, word, writes))
    return r.returncode, trace, text


def symbol(elf, name):
    """Address of a symbol in the linked ELF."""
    r = subprocess.run(["riscv-none-elf-nm", elf], stdout=subprocess.PIPE)
    for line in r.stdout.decode().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == name:
            return int(parts[0], 16)
    return None


def skipped_traps(elf, trace):
    """
    Faulting PCs, for programs built with trap_mode="skip".

    --log-commits does not print exceptions -- a trapping instruction simply
    has no commit line -- so the handler's own `csrr t0, mepc` is what makes
    the trap observable.  Reading it back out of the commit log means the trap
    set comes from the same artifact as everything else.
    """
    handler = symbol(elf, "trap_handler")
    if handler is None:
        return None
    return [val for pc, _w, writes in trace if pc == handler
            for _r, val in writes]


def data_words(label, words):
    """Emit a .data table."""
    out = [f"        .section .data\n        .align 2\n{label}:"]
    for i in range(0, len(words), 8):
        out.append("        .word " + ", ".join(f"0x{w:08x}" for w in words[i:i+8]))
    out.append(f"{label}_end:")
    return "\n".join(out) + "\n"
