# =============================================================================
# a4_checksum.S -- a NOP-padded, straight-line checksum over an array.
#
# Padding rules: every RAW dependency is separated by 3 NOPs, which also
# satisfies the load-use rule; no branches or jumps before the ECALL.  `la`
# and `li` are hand-expanded where their natural forms would violate them.
#
# The checksum is rotate-left-1 then add, so word order matters.  The tail
# exercises every load and store width, including the sign-extending ones,
# and folds each readback into the same accumulator.
#
# The expected value is not written down here: tb/unit/test_core_verilator.py
# parses the .word literals out of this file and computes it, and Spike runs
# the same ELF as an independent third opinion.
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
        # s0 = &checksum_data, hand-expanded `la`.
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

        # byte, SIGN-extended, at a NONZERO byte offset: 0x80 must become
        # 0xFFFFFF80.
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
        # word read back: exercises byte-lane selection, which every earlier
        # access at byte offset 0 cannot, and catches a byte enable applied to
        # the wrong lane.
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
        # `csrw mtvec, t0` writes no register, so only Spike acts on it.
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
# Data.  These literals are the single source of truth for the expected result;
# tb/unit/test_core_verilator.py parses them out of this file.  The pattern is
# deliberately not a ramp or a constant.
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
