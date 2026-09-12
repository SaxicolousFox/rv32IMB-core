# ============================================================================
# a9_csr.S -- directed Zicsr and trap tests.
#
# Self-checking through riscv-tests' protocol: a store of 1 to `tohost` is
# pass, anything else is `(case << 1) | 1`.  Not a lockstep program, because
# counter values cannot agree with Spike (its mcycle advances once per
# instruction and its minstret counts the bootrom).
# ============================================================================
        .section .text.init
        .globl _start
_start:
        la      t0, trap_vector
        csrw    mtvec, t0
        li      gp, 0                   # gp = the case number under test

#define FAILCASE(n)  li gp, n; j fail

# ---- case 2: CSRRS with rs1 = x0 MUST NOT WRITE ---------------------------
# The read-modify-write is suppressed entirely, which is what makes
# `csrr rd, <read-only csr>` a legal encoding rather than a fault.
        li      t0, 0x12345678
        csrw    mscratch, t0
        csrrs   t1, mscratch, x0        # reads, must not write
        bne     t1, t0, 20f             # the read value itself must be right
        csrr    t2, mscratch
        beq     t2, t0, 1f
20:     FAILCASE(2)
1:

# ---- case 3: CSRRW with rd = x0 -------------------------------------------
# It must still WRITE.  (The "must not read" half of the rule has no observable
# consequence on this core: no CSR here has a read side effect.  The observable
# part is that suppressing the read must not suppress the write with it.)
        li      t0, 0x0f0f0f0f
        csrrw   x0, mscratch, t0
        csrr    t1, mscratch
        beq     t1, t0, 1f
        FAILCASE(3)
1:

# ---- case 4: CSRRC clears exactly the set bits ----------------------------
        li      t0, -1
        csrw    mscratch, t0
        li      t1, 0x0000ffff
        csrrc   t2, mscratch, t1        # t2 = old = -1
        csrr    t3, mscratch
        li      t4, 0xffff0000
        bne     t3, t4, 40f
        bne     t2, t0, 40f
        j       1f
40:     FAILCASE(4)
1:

# ---- case 5: the immediate forms use a 5-bit ZERO-EXTENDED uimm -----------
# Not a register, and not sign-extended: `csrrsi x0, mscratch, 31` must set
# exactly bits 4:0.
        csrwi   mscratch, 0
        csrrsi  x0, mscratch, 31
        csrr    t0, mscratch
        li      t1, 31
        beq     t0, t1, 1f
        FAILCASE(5)
1:
# CSRRSI with uimm == 0 must not write, for the same reason as case 2.
        li      t0, 0x5a5a5a5a
        csrw    mscratch, t0
        csrrsi  t1, mscratch, 0
        csrr    t2, mscratch
        bne     t2, t0, 50f
        beq     t1, t0, 1f
50:     FAILCASE(5)
1:

# ---- case 6: an unknown CSR is an illegal instruction ---------------------
# The handler below records mcause and skips the faulting instruction, so
# execution resumes on the next line and each case reads as straight-line code.
        li      t3, 0                   # t3 = 1 once the handler has run
        li      t4, 2                   # expected mcause: illegal instruction
        csrr    t0, 0x3f0
        bne     t3, x0, 1f
        FAILCASE(6)
1:

# ---- case 7: writing a READ-ONLY CSR is illegal, reading it is not --------
# `cycle` is read-only by address (bits 11:10 == 11).  csrrs with x0 does not
# write, so it must succeed; csrrw always writes, so it must trap.
        li      t3, 0
        csrrs   t0, cycle, x0           # legal: no write
        bne     t3, x0, 70f             # must NOT have trapped
        li      t3, 0
        li      t4, 2
        csrrw   t0, cycle, x0           # illegal: CSRRW always writes
        bne     t3, x0, 1f
70:     FAILCASE(7)
1:

# ---- case 8: mcycle rolls over into mcycleh -------------------------------
        li      t0, -1
        csrw    mcycleh, x0
        csrw    mcycle, t0              # 0x00000000_ffffffff
        nop
        nop
        nop
        csrr    t1, mcycleh
        li      t2, 1
        beq     t1, t2, 1f
        FAILCASE(8)
1:

# ---- case 9: minstret advances by exactly one per instruction -------------
        csrr    s0, minstret
        addi    zero, zero, 0
        addi    zero, zero, 0
        addi    zero, zero, 0
        addi    zero, zero, 0
        csrr    s2, minstret
        sub     s3, s2, s0              # 1 (the first csrr) + 4 nops = 5
        li      t0, 5
        beq     s3, t0, 1f
        FAILCASE(9)
1:

# ---- case 10: EBREAK traps with cause 3 and mepc at the EBREAK ------------
        li      t3, 0
        li      t4, 3
        la      t5, 10f
10:     ebreak
        bne     t3, x0, 1f
        FAILCASE(10)
1:      bne     t6, t5, 100f            # t6 = mepc recorded by the handler
        j       1f
100:    FAILCASE(10)
1:

# ---- case 11: a misaligned load traps with cause 4 and mtval = the address
        li      t3, 0
        li      t4, 4
        la      s4, scratch
        addi    s5, s4, 1               # deliberately odd
        lw      t0, 0(s5)
        bne     t3, x0, 1f
        FAILCASE(11)
1:      bne     s6, s5, 110f            # s6 = mtval recorded by the handler
        j       1f
110:    FAILCASE(11)
1:

# ---- case 12: a misaligned STORE traps, and must not write memory ---------
        li      t3, 0
        li      t4, 6
        li      t0, 0x11111111
        sw      t0, 0(s4)               # aligned: this one lands
        addi    s5, s4, 2
        li      t1, 0x22222222
        sh      t1, 1(s5)               # address is scratch+3: misaligned
        bne     t3, x0, 1f
        FAILCASE(12)
1:      lw      t2, 0(s4)
        li      t0, 0x11111111
        beq     t2, t0, 1f              # the faulting store left memory alone
        FAILCASE(12)
1:

# ---- case 13: MRET returns to mepc and restores MIE -----------------------
        # MIE clear and MPIE set, so the swap has somewhere to move a value
        # from.
        li      t0, 0x80
        csrw    mstatus, t0
        la      t0, 130f
        csrw    mepc, t0
        csrsi   mstatus, 0              # CSRRSI with uimm 0: must not write
        mret
        FAILCASE(13)                    # must be skipped
130:    csrr    t0, mstatus
        andi    t1, t0, 0x88            # MIE | MPIE
        li      t2, 0x88
        beq     t1, t2, 1f              # MRET set MPIE and restored MIE from it
        FAILCASE(13)
1:

# ---- pass -----------------------------------------------------------------
        li      gp, 1
        j       done

fail:
        slli    gp, gp, 1
        ori     gp, gp, 1
done:
        la      t0, tohost
        sw      gp, 0(t0)
2:      j       2b

# ---------------------------------------------------------------------------
# The trap handler records mcause in t3 (as a flag), mepc in t6 and mtval in
# s6, checks mcause against the t4 the test set beforehand, and resumes at the
# instruction after the faulting one.
# ---------------------------------------------------------------------------
        .align 2
trap_vector:
        csrr    t2, mcause
        bne     t2, t4, trap_wrong_cause
        csrr    t6, mepc
        csrr    s6, mtval
        addi    t2, t6, 4
        csrw    mepc, t2
        li      t3, 1
        mret
trap_wrong_cause:
        slli    gp, gp, 1
        ori     gp, gp, 1
        ori     gp, gp, 0x100           # 0x100 marks "wrong mcause"
        la      t0, tohost
        sw      gp, 0(t0)
3:      j       3b

        .section .data
        .align 4
        .globl scratch
scratch:
        .word 0, 0, 0, 0, 0, 0, 0, 0

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
