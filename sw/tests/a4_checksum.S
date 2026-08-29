# =============================================================================
# A4 acceptance program: a NOP-padded, straight-line checksum over an array.
#
# Plan A4 asks for "a NOP-padded program that computes something nontrivial (a
# checksum over an array, say) [that] produces the right value in a register,
# verified by a $display at ECALL".  This is that program.
#
# THE PADDING RULES, which this file obeys everywhere:
#
#   * Every RAW dependency is separated by 3 NOPs.  The A4 core has no
#     forwarding, so a result is only visible to ID once it has reached WB.
#   * That also satisfies the load-use rule (no dependent use within 2
#     instructions of a load), so no extra load padding is needed.
#   * No branches and no jumps before the ECALL.  The A4 core's PC is pc+4,
#     always; control flow arrives in A8.  rvntt_core's `dbg_unsupported`
#     output fires if this rule is ever broken, so it cannot rot silently.
#
# Two macro expansions are written out by hand because their natural forms
# violate the padding rules:
#
#   * `la reg, sym` expands to auipc+addi with a ZERO-separation RAW on reg.
#   * `li reg, big` expands to lui+addi with the same problem.  Every `li` here
#     is small enough to be a single addi.
#
# The checksum is rotate-left-1 then add, so word ORDER matters: a plain sum or
# XOR would still pass if the address generator walked the array backwards or
# read one element repeatedly.  The tail then exercises every load and store
# width, including the sign-extending ones, and folds each readback into the
# same accumulator -- so one register carries evidence that all of it worked.
#
# The expected value is NOT written down here.  tb/unit/test_core_verilator.py
# parses the .word literals out of this file and computes it, and Spike runs the
# same ELF as an independent third opinion.
# =============================================================================

        .section .text.init
        .globl _start

# One checksum step over the element at \off(s0):
#   acc = rotl(acc, 1) + mem[s0 + off]
        .macro STEP off
        lw      t0, \off(s0)
        nop
        nop
        nop
        slli    t1, s1, 1
        nop
        nop
        nop
        srli    t2, s1, 31
        nop
        nop
        nop
        or      s1, t1, t2
        nop
        nop
        nop
        add     s1, s1, t0
        nop
        nop
        nop
        .endm

_start:
        # s0 = &checksum_data.  Hand-expanded `la`: auipc and addi have a RAW
        # on s0 with no separation at all in the assembler's expansion.
1:      auipc   s0, %pcrel_hi(checksum_data)
        nop
        nop
        nop
        addi    s0, s0, %pcrel_lo(1b)
        nop
        nop
        nop

        # s2 = &scratch, likewise hand-expanded.
2:      auipc   s2, %pcrel_hi(scratch)
        nop
        nop
        nop
        addi    s2, s2, %pcrel_lo(2b)
        nop
        nop
        nop

        addi    s1, x0, 0               # acc = 0
        nop
        nop
        nop

        STEP 0
        STEP 4
        STEP 8
        STEP 12
        STEP 16
        STEP 20
        STEP 24
        STEP 28
        STEP 32
        STEP 36
        STEP 40
        STEP 44
        STEP 48
        STEP 52
        STEP 56
        STEP 60

        # ---- exercise every store/load width against the accumulator --------
        # word
        sw      s1, 0(s2)
        nop
        nop
        nop
        lw      s3, 0(s2)
        nop
        nop
        nop

        # byte, zero-extended
        sb      s1, 4(s2)
        nop
        nop
        nop
        lbu     s4, 4(s2)
        nop
        nop
        nop

        # halfword, zero-extended
        sh      s1, 8(s2)
        nop
        nop
        nop
        lhu     s5, 8(s2)
        nop
        nop
        nop

        # byte, SIGN-extended, at a NONZERO byte offset.  0x80 stored and read
        # back with lb must become 0xFFFFFF80; with lbu it would be 0x00000080,
        # and the two differ by 0xFFFFFF00, far too large to look like rounding.
        addi    t3, x0, 0x80
        nop
        nop
        nop
        sb      t3, 13(s2)
        nop
        nop
        nop
        lb      s6, 13(s2)
        nop
        nop
        nop

        # Four distinct bytes into the four lanes of ONE word, then the whole
        # word read back.  This is the part that exercises byte-lane selection:
        # every earlier store and load in this program happens to sit at byte
        # offset 0, where a store that forgets to replicate its data across the
        # lanes, a load that always takes lane 0, and a RAM that ignores its
        # byte enables are all indistinguishable from correct.  Fault injection
        # found exactly those three escaping before this block existed.
        #
        # Reading the full word back also catches collateral damage: a byte
        # enable applied to the wrong lane corrupts a neighbour, which a
        # per-byte readback would miss.
        addi    t3, x0, 0x11
        nop
        nop
        nop
        sb      t3, 20(s2)
        nop
        nop
        nop
        addi    t3, x0, 0x22
        nop
        nop
        nop
        sb      t3, 21(s2)
        nop
        nop
        nop
        addi    t3, x0, 0x33
        nop
        nop
        nop
        sb      t3, 22(s2)
        nop
        nop
        nop
        addi    t3, x0, 0x44
        nop
        nop
        nop
        sb      t3, 23(s2)
        nop
        nop
        nop
        lw      s8, 20(s2)              # must be 0x44332211
        nop
        nop
        nop
        lbu     s9, 21(s2)              # lane 1
        nop
        nop
        nop
        lbu     s10, 23(s2)             # lane 3
        nop
        nop
        nop

        # halfword, SIGN-extended, at a nonzero halfword offset so the upper
        # half of the word is selected.
        addi    t4, x0, -3
        nop
        nop
        nop
        sh      t4, 18(s2)
        nop
        nop
        nop
        lh      s7, 18(s2)
        nop
        nop
        nop

        # ---- fold every readback into the accumulator ----------------------
        add     s1, s1, s3
        nop
        nop
        nop
        add     s1, s1, s4
        nop
        nop
        nop
        add     s1, s1, s5
        nop
        nop
        nop
        add     s1, s1, s6
        nop
        nop
        nop
        add     s1, s1, s7
        nop
        nop
        nop
        add     s1, s1, s8
        nop
        nop
        nop
        add     s1, s1, s9
        nop
        nop
        nop
        add     s1, s1, s10
        nop
        nop
        nop

        # ---- arm Spike's trap handler, then stop ---------------------------
        # `csrw mtvec, t0` is csrrw x0, mtvec, t0: it writes no register, so the
        # A4 core (which has no CSR file) and Spike agree on architectural state
        # even though only Spike acts on it.  rvntt_core excludes exactly this
        # case from dbg_unsupported.
3:      auipc   t0, %pcrel_hi(trap_handler)
        nop
        nop
        nop
        addi    t0, t0, %pcrel_lo(3b)
        nop
        nop
        nop
        csrw    mtvec, t0
        nop
        nop
        nop

        ecall                           # the RTL testbench stops here

        # Spike continues into the handler below and exits through HTIF.
        .text
        .align 2
        .globl trap_handler
trap_handler:
        addi    t0, x0, 1               # HTIF: (0 << 1) | 1 -> exit code 0
4:      auipc   t1, %pcrel_hi(tohost)
        addi    t1, t1, %pcrel_lo(4b)
        sw      t0, 0(t1)
9:      j       9b

# -----------------------------------------------------------------------------
# Data.  These literals are the single source of truth for the expected result:
# tb/unit/test_core_verilator.py parses them out of this file rather than
# carrying its own copy.
#
# The pattern is deliberately not a ramp or a constant.  A ramp checksums
# correctly even if the address generator is broken in ways that preserve the
# multiset of values read; these are unrelated to each other and to their index.
# -----------------------------------------------------------------------------
        .section .data
        .align 2
        .globl checksum_data
checksum_data:
        .word 0x9E3779B9, 0x85EBCA6B, 0xC2B2AE35, 0x27D4EB2F
        .word 0x165667B1, 0xD3A2646C, 0xFD7046C5, 0xB55A4F09
        .word 0x00000000, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF
        .word 0x00000001, 0xFFFFFFFE, 0xDEADBEEF, 0xCAFEF00D

        .align 2
        .globl scratch
scratch:
        .space 32

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
