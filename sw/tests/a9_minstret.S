# ============================================================================
# a9_minstret.S -- minstret after a known program equals the instruction count
# Spike reports for the same program.
#
# The body is a loop, so the number counts dynamic instructions.  `probe` is
# global so the driver can find the exact commit in Spike's trace; Spike's
# commits at or above the load address are counted, not its own minstret,
# which includes the bootrom.  The multi-cycle instructions in the loop are
# the sharpest check: a DIV occupies EX for 34 cycles and minstret must count
# it exactly once, which no architectural comparison can see.
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
        mul     t2, t1, s4              # A14: 4 EX cycles, 1 retirement
        add     s2, s2, t2
        div     t3, s2, s4              # A14: 34 EX cycles, 1 retirement
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

        # A taken branch leaves MEM and WB holding bubbles for two cycles, so
        # a probe placed immediately after one reads the same value whether
        # the counter is incremented in EX or in WB; two ordinary instructions
        # refill the pipeline.
        addi    s6, s6, 1
        addi    s7, s7, 1

        # ... and one long instruction with nothing between it and the probe.
        div     s8, s2, s4

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
