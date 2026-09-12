#!/usr/bin/env python3
"""
Build sw/tests/a4_checksum.S, run it on the RTL, check the result.

The expected value comes from two places, neither of them the RTL: a Python
model of the program's arithmetic over data literals parsed out of the .S
file, and Spike running the same ELF.  Both must agree before the RTL is
started; a disagreement between them is reported as its own failure.
"""
import os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
import spike_asm   # noqa: E402

ASM = os.path.join(ROOT, "sw/tests/a4_checksum.S")
LD = os.path.join(ROOT, "sw/tests/link.ld")
RTL = [
    os.path.join(ROOT, "rtl/core/rv32i_pkg.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_decode.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_alu.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_immgen.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_regfile.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_forward.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_hazard.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_branch.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_csr.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_muldiv.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_bitmanip.sv"),
    # One source list, imported by test_riscv_tests.py, the cosim harnesses
    # and the mutation runner.
    os.path.join(ROOT, "rtl/core/rvntt_seed.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_entropy.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_entropy_health.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_bpred.sv"),
    os.path.join(ROOT, "rtl/core/rvntt_core.sv"),
    os.path.join(ROOT, "rtl/soc/rvntt_ram.sv"),
    os.path.join(ROOT, "rtl/soc/rvntt_core_sim_top.sv"),
]
TB = os.path.join(ROOT, "tb/unit/tb_core.cpp")

MASK = 0xFFFFFFFF
BASE = 0x80000000


def u32(x):
    return x & MASK


def s32(x):
    x &= MASK
    return x - (1 << 32) if x & 0x80000000 else x


def parse_data_words(path):
    """Pull the .word literals of checksum_data out of the assembly source."""
    text = open(path).read()
    m = re.search(r"^checksum_data:\s*\n(.*?)^\s*\.align", text,
                  re.S | re.M)
    if not m:
        raise RuntimeError("could not find checksum_data in " + path)
    words = []
    for line in m.group(1).splitlines():
        line = line.split("#")[0].strip()
        if not line.startswith(".word"):
            continue
        for tok in line[len(".word"):].split(","):
            tok = tok.strip()
            if tok:
                words.append(int(tok, 0))
    if not words:
        raise RuntimeError("checksum_data has no .word literals")
    return words


def model(words):
    """
    The program's arithmetic, written from the assembly rather than from the
    RTL.  Keep this in step with sw/tests/a4_checksum.S; Spike is the referee if
    the two ever disagree.
    """
    acc = 0
    for w in words:
        acc = u32((acc << 1) | (acc >> 31))     # rotl 1
        acc = u32(acc + w)

    # The load/store tail: every readback is folded back into the accumulator.
    s3 = acc                        # sw / lw       -- full word
    s4 = acc & 0xFF                 # sb / lbu      -- zero-extended byte
    s5 = acc & 0xFFFF               # sh / lhu      -- zero-extended halfword
    s6 = u32(-0x80)                 # sb 0x80 / lb  -- 0x80 sign-extends
    s7 = u32(-3)                    # sh -3 / lh    -- sign-extended halfword
    s8 = 0x44332211                 # four sb into one word, read back as lw
    s9 = 0x22                       # lbu from lane 1
    s10 = 0x44                      # lbu from lane 3

    for v in (s3, s4, s5, s6, s7, s8, s9, s10):
        acc = u32(acc + v)
    return acc


def build_elf(tmp):
    elf = os.path.join(tmp, "a4_checksum.elf")
    r = subprocess.run(
        ["riscv-none-elf-gcc", "-march=rv32i_zicsr", "-mabi=ilp32",
         "-nostdlib", "-nostartfiles", "-T", LD, "-o", elf, ASM],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError("assembling a4_checksum.S failed")
    return elf


def elf_to_hex(elf, tmp):
    """Flatten the loadable image to one 32-bit word per line for $readmemh."""
    binf = os.path.join(tmp, "a4_checksum.bin")
    r = subprocess.run(["riscv-none-elf-objcopy", "-O", "binary", elf, binf],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError("objcopy failed")
    raw = open(binf, "rb").read()
    raw += b"\x00" * ((-len(raw)) % 4)
    hexf = os.path.join(tmp, "a4_checksum.hex")
    with open(hexf, "w") as f:
        for i in range(0, len(raw), 4):
            f.write("%08x\n" % int.from_bytes(raw[i:i + 4], "little"))
    return hexf, len(raw) // 4


def spike_final_reg(elf, reg):
    """
    Value of x<reg> according to Spike at the moment the program stops.

    The scan stops at the trap handler's first instruction: Spike carries on
    past the ECALL into the handler and spins until it notices the HTIF write.
    The marker is the handler, not the ECALL, because --log-commits prints no
    line for a trapping instruction.
    """
    rc, trace, text = spike_asm.run(elf, isa=spike_asm.ISA_BASE, log_commits=True)
    if not trace:
        raise RuntimeError("Spike produced no commit trace:\n" + text[-2000:])
    handler = spike_asm.symbol(elf, "trap_handler")
    if handler is None:
        raise RuntimeError("no trap_handler symbol in the ELF")

    val = 0
    n_prog = 0
    for pc, _word, writes in trace:
        if pc == handler:
            # The ECALL traps on both sides and --stop-pc is exclusive, so both
            # count the same instructions.  Commits below the load address are
            # Spike's bootrom (5 instructions at 0x1000), which the RTL never runs.
            return val, n_prog
        if pc >= BASE:
            n_prog += 1
        for rd, v in writes:
            if rd == reg:
                val = v
    raise RuntimeError(f"Spike never reached trap_handler in {len(trace)} "
                       "commits -- the program did not take its ECALL")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        words = parse_data_words(ASM)
        expect = model(words)

        elf = build_elf(tmp)
        spike_val, n_retired = spike_final_reg(elf, 9)     # x9 = s1
        handler = spike_asm.symbol(elf, "trap_handler")

        print(f"data words parsed from {os.path.relpath(ASM, ROOT)}: {len(words)}")
        print(f"python model : 0x{expect:08x}")
        print(f"spike        : 0x{spike_val:08x}  "
              f"({n_retired} instructions retired)")

        if spike_val != expect:
            print("REFERENCE MISMATCH: the Python model and Spike disagree, so the "
                  "expectation is wrong before the RTL is even involved.")
            print("  Fix model() in this file or sw/tests/a4_checksum.S -- "
                  "do not adjust the RTL to match either one.")
            return 1

        hexf, nwords = elf_to_hex(elf, tmp)
        print(f"image        : {nwords} words")

        build = os.path.join(tmp, "obj")
        cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
               "--top-module", "rvntt_core_sim_top",
               "--Mdir", build, "--prefix", "Vrvntt_core_sim_top",
               "-GINIT_FILE=\"%s\"" % hexf,
               "-GWORDS=%d" % max(nwords, 4096)] + RTL + [TB]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(r.stdout.decode("utf-8", "replace"))
            return 1

        exe = os.path.join(build, "Vrvntt_core_sim_top")
        r = subprocess.run([exe, "--expect", "0x%08x" % expect, "--reg", "9",
                            "--expect-retired", str(n_retired),
                            "--stop-pc", "0x%08x" % handler],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = r.stdout.decode("utf-8", "replace")
        print(out.strip())
        return 0 if (r.returncode == 0 and "CORE_TB_OK" in out) else 1


if __name__ == "__main__":
    sys.exit(main())
