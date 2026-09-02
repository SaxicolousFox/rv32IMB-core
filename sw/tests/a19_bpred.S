# ============================================================================
# a19_bpred.S -- directed branch-predictor tests (MODS_A A19).
#
# WHY THIS FILE HAS TO EXIST, AND WHY THE RANDOM SUITE COULD NOT DO IT.
#
# tb/cosim/gen_random_prog.py emits FORWARD-ONLY branches and forward jumps, by
# design: that is what guarantees a generated program terminates and never
# traps.  The consequence, which nobody had reason to notice before A19, is
# that a random program is a straight line -- every control transfer site
# executes AT MOST ONCE.  A predictor whose entire job is to remember what a
# site did last time is therefore INERT in every random program ever generated
# here, and all eight A19 mutations escaped `random:branch` on their first run
# because of it.  Not one of them was caught, which is a much clearer signal
# than one escape would have been.
#
# So the predictor needs stimulus that REPEATS, and that is what this is.  Each
# case below is a loop, and the property under test is the CYCLE SPAN --
# tb/cosim/cycle_model.py predicts it from docs/a19-bpred-spec.md through
# model/bpred.py, which never reads the RTL.  Every mutation here is
# architecturally invisible: the commit log is byte-identical with all of them
# applied, and the span is the only thing that moves.
#
# x8 is the scratch pointer and is never written after the prologue.
# x1 (ra) is the link register and is used as one, so the RAS sees real calls.
# ============================================================================
        .section .text.init
        .globl _start
_start:
1:      auipc   x8, %pcrel_hi(scratch)
        addi    x8, x8, %pcrel_lo(1b)

# EVERY ACCUMULATOR IS INITIALISED.  Spike does not reset the register file to
# zero and this core does, so a register that is only ever added to diverges on
# its first commit -- which is what happened the first time this file ran, and
# is a test bug rather than a core bug.  The counters below are all written
# before they are read; these are the ones that are only incremented.
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
# 16 iterations.  The first pass allocates the BTB entry and moves the counter
# to weakly taken; every pass after that is predicted, and the loop's exit is
# the single mispredict.  A predictor that never allocates, never hits, or
# never carries its prediction into EX turns 15 free transfers back into 15
# two-cycle flushes -- 30 cycles the model does not predict.
        addi    x10, x0, 16
.Lloop1:
        addi    x11, x11, 1
        addi    x10, x10, -1
        bne     x10, x0, .Lloop1

# ---- case 2: a branch that alternates, so the 2-bit counter has to hysterese
# The inner branch is taken on even iterations and not on odd ones.  A 1-bit
# predictor mispredicts every one of them; a saturating 2-bit counter settles
# and mispredicts about half.  A counter that WRAPS instead of saturating loses
# the hysteresis entirely, which is the classic bug and is invisible to every
# architectural check there is.
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
# THE DOWNSIDE TERM, made observable.  Under static not-taken this branch is
# free; a predictor that allocates on a not-taken resolution, or that starts a
# fresh entry strongly taken, turns each of these into two wasted cycles.  The
# specification forbids both, and this is where that is checked rather than
# asserted.
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
# The RAS earns its place here.  `jal ra` is a CALL and pushes; `jr ra` reads
# x1 and writes x0, which is a RET and pops.  A BTB alone predicts this return
# correctly too, because there is only one caller -- which is exactly why case
# 5 exists.
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
# This is the case a BTB gets wrong and a return stack gets right.  `.Lshared`
# returns to two different places, so its BTB entry holds whichever caller was
# last -- and alternating between the two makes that entry wrong every single
# time.  With the RAS, both are predicted.  Breaking the push condition shows
# up here and almost nowhere else.
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
# The stack has to be a stack.  An outer call, an inner call, and two returns
# in the opposite order -- a single-entry return register predicts the inner
# return and then gets the outer one wrong on every iteration.
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
# `jalr x0, 0(x5)` reads x5, which is not a link register, so it is a JUMP and
# its target comes from the BTB.  Classifying it as a return would pop the
# stack for something that never pushed, and would corrupt case 6's nesting.
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
# A 256-entry BTB indexed on pc[9:2] covers exactly 1 kB, and `.align 10` puts
# both branches on a 1 kB boundary -- so both index entry 0, and neither one's
# entry survives the other.  They alternate every iteration, so each is cold
# every time it is reached and both mispredict on every pass.
#
# That is what a direct-mapped BTB does, and the point is that the MODEL has to
# know it: a model that ignored replacement would predict these as hits and
# come out sixteen cycles short.  The two `jal`s exist to step over the
# alignment padding rather than execute a kilobyte of nops twice per iteration.
#
# What this case does NOT cover is a wrong tag that MATCHES -- a false hit.
# Constructing one by hand needs two hot sites whose tags collide under the
# specific broken comparison being tested, which is tuning a program to a
# mutation rather than testing a property.  The stimulus that would cover it is
# a long random program containing loops, which gen_random_prog.py cannot emit
# (see this file's header).  The gap is recorded rather than papered over.
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
