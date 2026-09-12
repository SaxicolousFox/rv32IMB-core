# ============================================================================
# a19_bpred.S -- directed branch-predictor tests.
#
# gen_random_prog.py emits forward-only control flow, so every transfer site
# in a random program executes at most once and a predictor is inert there.
# Each case below is a loop, and the property under test is the cycle span,
# which tb/cosim/cycle_model.py predicts through model/bpred.py.  Every
# predictor bug is architecturally invisible: the commit log is unchanged and
# only the span moves.
#
# x8 is the scratch pointer and is never written after the prologue.
# x1 (ra) is the link register and is used as one, so the RAS sees real calls.
# ============================================================================
        .section .text.init
        .globl _start
_start:
1:      auipc   x8, %pcrel_hi(scratch)
        addi    x8, x8, %pcrel_lo(1b)

# Every accumulator is initialised: Spike does not reset the register file to
# zero and this core does.
        addi    x11, x0, 0
        addi    x15, x0, 0
        addi    x17, x0, 0
        addi    x19, x0, 0
        addi    x21, x0, 0
        addi    x23, x0, 0
        addi    x24, x0, 0
        addi    x26, x0, 0
        addi    x28, x0, 0
        addi    x30, x0, 0

# ---- case 1: a strongly-taken backward branch ------------------------------
# 16 iterations.  The first pass allocates the BTB entry; every pass after
# that is predicted, and the loop's exit is the single mispredict.
        addi    x10, x0, 16
.Lloop1:
        addi    x11, x11, 1
        addi    x10, x10, -1
        bne     x10, x0, .Lloop1

# ---- case 2: a branch that alternates, so the 2-bit counter has to hysterese
# The inner branch is taken on even iterations and not on odd ones; a
# saturating 2-bit counter settles and mispredicts about half, a counter that
# wraps loses the hysteresis entirely.
        addi    x12, x0, 20
        addi    x13, x0, 0
.Lloop2:
        andi    x14, x13, 1
        beq     x14, x0, .Lalt
        addi    x15, x15, 1
.Lalt:
        addi    x13, x13, 1
        addi    x12, x12, -1
        bne     x12, x0, .Lloop2

# ---- case 3: a never-taken branch inside a hot loop ------------------------
# Under static not-taken this branch is free; a predictor that allocates on a
# not-taken resolution, or starts a fresh entry strongly taken, wastes two
# cycles per pass.
        addi    x16, x0, 24
.Lloop3:
        bne     x0, x0, .Lnever        # never taken, ever
        addi    x17, x17, 1
        addi    x16, x16, -1
        bne     x16, x0, .Lloop3
        beq     x0, x0, .Lafter3
.Lnever:
        addi    x17, x0, -1            # unreachable; a wrong answer if reached
.Lafter3:

# ---- case 4: a called leaf function, in a loop -----------------------------
# `jal ra` is a CALL and pushes; `jr ra` reads x1 and writes x0, a RET, and
# pops.  A BTB alone predicts this too (one caller), hence case 5.
        addi    x18, x0, 12
.Lloop4:
        jal     x1, .Lleaf
        addi    x18, x18, -1
        bne     x18, x0, .Lloop4
        beq     x0, x0, .Lafter4
.Lleaf:
        addi    x19, x19, 1
        jalr    x0, 0(x1)
.Lafter4:

# ---- case 5: ONE function, TWO call sites --------------------------------
# `.Lshared` returns to two different places, so its BTB entry holds
# whichever caller was last; with the RAS both are predicted.
        addi    x20, x0, 10
.Lloop5:
        jal     x1, .Lshared
        jal     x1, .Lshared2
        addi    x20, x20, -1
        bne     x20, x0, .Lloop5
        beq     x0, x0, .Lafter5
.Lshared2:
        jal     x0, .Lshared_body      # a plain jump: no link, so no push
.Lshared:
.Lshared_body:
        addi    x21, x21, 1
        jalr    x0, 0(x1)
.Lafter5:

# ---- case 6: two-deep calls in a loop --------------------------------------
# An outer call, an inner call, and two returns in the opposite order.
        addi    x22, x0, 10
.Lloop6:
        jal     x1, .Louter
        addi    x22, x22, -1
        bne     x22, x0, .Lloop6
        beq     x0, x0, .Lafter6
.Louter:
        addi    x2, x1, 0              # save ra by hand; no stack in this test
        jal     x1, .Linner
        addi    x23, x23, 1
        jalr    x0, 0(x2)
.Linner:
        addi    x24, x24, 1
        jalr    x0, 0(x1)
.Lafter6:

# ---- case 7: an indirect jump that is NOT a return -------------------------
# `jalr x0, 0(x5)` reads x5, not a link register, so it is a JUMP whose
# target comes from the BTB, and it must not pop the stack.
        addi    x25, x0, 8
.Lloop7:
2:      auipc   x5, %pcrel_hi(.Ltbl)
        addi    x5, x5, %pcrel_lo(2b)
        jalr    x0, 0(x5)
.Ltbl:
        addi    x26, x26, 1
        addi    x25, x25, -1
        bne     x25, x0, .Lloop7

# ---- case 8: two hot branches that SHARE a BTB index -----------------------
# A 256-entry BTB indexed on pc[9:2] covers exactly 1 kB, and `.align 10`
# puts both branches on a 1 kB boundary, so both index entry 0 and each is
# cold every time it is reached.  The model has to know that.  The two
# `jal`s step over the alignment padding.  A false hit (a wrong tag that
# matches) is not covered here.
        addi    x27, x0, 8
        jal     x0, .LblkA
        .align  10
.LblkA:
        bne     x27, x0, .LA1          # site A -- taken, BTB index 0
        jal     x0, .Lafter8
.LA1:
        addi    x28, x28, 1
        jal     x0, .LblkB
        .align  10
.LblkB:
        bne     x27, x0, .LB1          # site B -- taken, BTB index 0 as well
        jal     x0, .Lafter8
.LB1:
        addi    x30, x30, 1
        addi    x27, x27, -1
        jal     x0, .LblkA
.Lafter8:

# ---- epilogue --------------------------------------------------------------
3:      auipc   t0, %pcrel_hi(trap_handler)
        addi    t0, t0, %pcrel_lo(3b)
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
4:      auipc   t1, %pcrel_hi(tohost)
        addi    t1, t1, %pcrel_lo(4b)
        sw      t0, 0(t1)
9:      j       9b

        .section .data
        .align 4
        .globl scratch
scratch:
        .word 0x8899aabb, 0x0f1e2d3c, 0xdeadbeef, 0x7f80817e
        .word 0x00000000, 0xffffffff, 0x5a5a5a5a, 0xa5a5a5a5

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
