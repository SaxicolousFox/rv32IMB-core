# ============================================================================
# a8_control.S -- directed control-hazard tests.
#
# Checked against Spike two ways: the commit log for architectural state and
# the cycle span for timing.  A flush one slot too shallow shows up in both;
# one slot too deep only in the span.
#
# x8 is the scratch pointer and is never written after the prologue.
# ============================================================================
        .section .text.init
        .globl _start
_start:
1:      auipc   x8, %pcrel_hi(scratch)
        addi    x8, x8, %pcrel_lo(1b)

        lui     x2, 0x11111
        addi    x2, x2, 0x111
        lui     x3, 0x22222
        addi    x3, x3, 0x222
        addi    x4, x0, -1              # 0xffffffff: negative signed, huge unsigned
        addi    x5, x0, 1

# ---- case 1: all six conditions, both directions ---------------------------
# x4 and x5 make the signed/unsigned distinction visible: blt and bltu on the
# same operands must disagree.
        beq     x2, x2, .Lt1
        addi    x10, x0, 99             # squashed
.Lt1:   beq     x2, x3, .Ln1
        addi    x10, x0, 1              # executed: the branch was not taken
.Ln1:   bne     x2, x3, .Lt2
        addi    x11, x0, 99             # squashed
.Lt2:   bne     x2, x2, .Ln2
        addi    x11, x0, 1
.Ln2:   blt     x4, x5, .Lt3            # -1 < 1 signed: taken
        addi    x12, x0, 99             # squashed
.Lt3:   blt     x5, x4, .Ln3            # not taken
        addi    x12, x0, 1
.Ln3:   bltu    x4, x5, .Ln4            # 0xffffffff < 1 unsigned: NOT taken
        addi    x13, x0, 1
.Ln4:   bltu    x5, x4, .Lt4            # taken
        addi    x13, x0, 99             # squashed
.Lt4:   bge     x5, x4, .Lt5            # 1 >= -1 signed: taken
        addi    x14, x0, 99             # squashed
.Lt5:   bge     x4, x5, .Ln5            # not taken
        addi    x14, x0, 1
.Ln5:   bgeu    x4, x5, .Lt6            # unsigned: taken
        addi    x15, x0, 99             # squashed
.Lt6:   bgeu    x5, x4, .Ln6            # not taken
        addi    x15, x0, 1
.Ln6:

# ---- case 2: a branch on a value computed one instruction earlier ----------
# The comparator reads the forwarded operands, not the register file.
        sub     x16, x2, x2             # zero, via the ALU rather than a literal
        beq     x16, x0, .Lf1           # distance 1: taken
        addi    x17, x0, 99             # squashed
.Lf1:   sub     x16, x2, x3             # non-zero
        nop
        beq     x16, x0, .Lf2           # distance 2: NOT taken
        addi    x17, x0, 1
.Lf2:

# ---- case 2b: the forwarded value on the rs2 port --------------------------
# Case 2 leaves x0 on rs2, so the comparator's rs2 forwarding path needs its
# own case.
        sub     x16, x3, x3             # zero
        beq     x0, x16, .Lf5           # rs2 at distance 1: taken
        addi    x17, x0, 99             # squashed
.Lf5:   sub     x16, x2, x3             # non-zero
        nop
        bne     x0, x16, .Lf6           # rs2 at distance 2: taken
        addi    x17, x0, 98             # squashed
.Lf6:

# ---- case 2c: both operands forwarded, from DIFFERENT stages ---------------
# rs1 from WB and rs2 from MEM in the same comparison.
        add     x16, x2, x3
        add     x17, x2, x3             # equal value, one stage younger
        beq     x16, x17, .Lf7          # taken
        addi    x18, x0, 97             # squashed
.Lf7:

# ---- case 3: a branch on a value LOADED one instruction earlier ------------
# One stall for the load-use hazard, then two more for the redirect.  Both
# mechanisms in the same instruction pair.
        lw      x18, 0(x8)
        bne     x18, x0, .Lf3
        addi    x19, x0, 99             # squashed
.Lf3:   lw      x18, 16(x8)             # a word that is zero
        beq     x18, x0, .Lf4
        addi    x19, x0, 98             # squashed
.Lf4:

# ---- case 4: JAL -- the target and the link value --------------------------
        jal     x20, .Lj1
        addi    x21, x0, 99             # squashed
        addi    x21, x0, 98             # squashed
.Lj1:   addi    x21, x0, 1

# JAL with rd == x0 is the plain `j`: it must redirect and write nothing.
        jal     x0, .Lj2
        addi    x22, x0, 99             # squashed
.Lj2:   addi    x22, x0, 1

# ---- case 5: JALR, with a nonzero offset and an odd sum --------------------
# JALR clears bit 0 of the computed target.  The offset is 1 on an aligned
# base, so the sum is odd and clearing bit 0 lands on the aligned target.
        la      x23, .Ljr1
        jalr    x24, 1(x23)
        addi    x25, x0, 99             # squashed
        addi    x25, x0, 98             # squashed
.Ljr1:  addi    x25, x0, 1

# JALR whose base register is FORWARDED at distance 1, and whose offset is a
# nonzero multiple of four.
        la      x26, .Ljr2
        addi    x26, x26, -8
        jalr    x27, 8(x26)
        addi    x28, x0, 99             # squashed
.Ljr2:  addi    x28, x0, 1

# ---- case 6: a taken branch in the shadow of a taken branch ----------------
# The second branch is squashed, so it must neither retire nor redirect.
        beq     x0, x0, .Lsh1
        beq     x0, x0, .Lshadow_bad
        jal     x0, .Lshadow_bad
.Lsh1:  addi    x29, x0, 1
        jal     x0, .Lsh2
.Lshadow_bad:
        addi    x29, x0, 99
.Lsh2:  addi    x30, x0, 1

# ---- case 7: a branch whose operands both come from x0 --------------------
        beq     x0, x0, .Lz1
        addi    x31, x0, 99             # squashed
.Lz1:   addi    x31, x0, 1

# ---- epilogue --------------------------------------------------------------
2:      auipc   t0, %pcrel_hi(trap_handler)
        addi    t0, t0, %pcrel_lo(2b)
        csrw    mtvec, t0
        nop
        nop
        nop
        ecall

        .text
        .align 2
        .globl trap_handler
trap_handler:
        addi    t0, x0, 1
3:      auipc   t1, %pcrel_hi(tohost)
        addi    t1, t1, %pcrel_lo(3b)
        sw      t0, 0(t1)
9:      j       9b

        .section .data
        .align 4
        .globl scratch
scratch:
        .word 0x8899aabb, 0x0f1e2d3c, 0xdeadbeef, 0x7f80817e
        .word 0x00000000, 0xffffffff, 0x5a5a5a5a, 0xa5a5a5a5
        .word 0x12345678, 0x9abcdef0, 0x0000ff00, 0xff0000ff
        .word 0x80000000, 0x7fffffff, 0x00008000, 0x00007fff

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
