# ============================================================================
# a9_minstret.S -- plan A9's done-when, made into a program.
#
# "minstret after a known program equals the instruction count Spike reports for
# the same program."  The body below is deliberately a LOOP rather than
# straight-line code, so the number is a count of DYNAMIC instructions and not
# just the size of the .text -- a counter wired to the fetch stream, or one that
# counted flushed instructions, would agree with the static count and disagree
# here.
#
# `probe` is global so the driver can find the exact commit in Spike's trace
# whose index is the expected value.  Comparing against Spike's own minstret
# would not do: Spike counts the five bootrom instructions at 0x1000 that this
# core never executes, so the two differ by a constant that would have to be
# hardcoded somewhere.  Counting Spike's commits at or above the load address
# has no such constant in it.
# ============================================================================
        .section .text.init
        .globl _start
_start:
        la      s0, data
        li      s2, 0                   # running sum
        li      s3, 0                   # index
        li      s4, 8                   # count

.Lloop:
        slli    t0, s3, 2
        add     t0, s0, t0
        lw      t1, 0(t0)
        add     s2, s2, t1
        addi    s3, s3, 1
        blt     s3, s4, .Lloop          # taken 7 times, then falls through

        # A not-taken branch and a taken one, so the flushed instructions have
        # a chance to be miscounted.
        beq     s2, x0, .Lskip
        addi    s5, s5, 1
.Lskip:
        bne     s2, x0, .Ljoin
        addi    s5, s5, 1               # squashed
.Ljoin:

        # THESE TWO ARE LOAD-BEARING.  A taken branch leaves MEM and WB holding
        # bubbles for two cycles, so a probe placed immediately after one reads
        # the same value whether the counter is incremented in EX or in WB --
        # and a mutation that moves it to WB passes.  Two ordinary instructions
        # refill the pipeline, and the difference becomes visible.
        addi    s6, s6, 1
        addi    s7, s7, 1

        .globl  probe
probe:  csrr    s1, minstret

        li      gp, 1
        la      t0, tohost
        sw      gp, 0(t0)
1:      j       1b

        .section .data
        .align 4
data:   .word 0x00000001, 0x00000002, 0x00000004, 0x00000008
        .word 0x00000010, 0x00000020, 0x00000040, 0x00000080

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
