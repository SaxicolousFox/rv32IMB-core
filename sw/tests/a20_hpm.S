# ============================================================================
# a20_hpm.S -- directed Zihpm hardware-performance-counter tests.
#
# Self-checking through riscv-tests' protocol (a store of 1 to `tohost` is
# pass; anything else is `(case << 1) | 1`), because Spike has no model of
# this core's stalls.  This checks the CSR-level contract: the registers
# exist, are WARL in the right places, read zero where they must, do not trap
# where they must not, and mcountinhibit inhibits.  Event attribution is
# checked by tb/perf/run_stall_profile.py against the simulation instrument.
#
# The event numbering is rv32i_pkg::HPM_EV_*, repeated as literals; if the
# two disagree, case 6 stops counting.
# ============================================================================
        .section .text.init
        .globl _start
_start:
        la      t0, trap_vector
        csrw    mtvec, t0
        li      gp, 0

#define FAILCASE(n)  li gp, n; j fail

#define EV_LOADUSE     1
#define EV_EXSTALL     2
#define EV_REDIRECT    3
#define EV_MISPREDICT  4
#define EV_BTB_HIT     5
#define EV_XFER_TAKEN  6

# ---- case 2: the six implemented counters are readable and writable --------
# Distinct values, so a decoder collapsing two indices onto one register
# shows up as a read-back mismatch.
        li      t0, 0x11110003
        csrw    0xB03, t0               # mhpmcounter3
        li      t0, 0x11110004
        csrw    0xB04, t0
        li      t0, 0x11110005
        csrw    0xB05, t0
        li      t0, 0x11110006
        csrw    0xB06, t0
        li      t0, 0x11110007
        csrw    0xB07, t0
        li      t0, 0x11110008
        csrw    0xB08, t0               # mhpmcounter8
        csrr    t1, 0xB03
        li      t2, 0x11110003
        bne     t1, t2, 20f
        csrr    t1, 0xB08
        li      t2, 0x11110008
        beq     t1, t2, 1f
20:     FAILCASE(2)
1:

# ---- case 3: counters 9..31 are DECODED, read zero, and do not trap --------
# An unimplemented counter must read zero, not fault.
        csrr    t1, 0xB09               # mhpmcounter9  -- must read 0
        bnez    t1, 30f
        csrr    t1, 0xB1F               # mhpmcounter31 -- must read 0
        bnez    t1, 30f
        csrr    t1, 0x329               # mhpmevent9    -- must read 0
        bnez    t1, 30f
        li      t0, -1                  # and a write must be accepted+ignored
        csrw    0xB09, t0
        csrr    t1, 0xB09
        beqz    t1, 1f
30:     FAILCASE(3)
1:

# ---- case 4: mcountinhibit, including the TM bit that must read back zero ---
        li      t0, -1                  # try to set every bit
        csrw    0x320, t0
        csrr    t1, 0x320
        andi    t2, t1, 2               # bit 1 (TM) has no mtime to inhibit
        bnez    t2, 40f
        andi    t2, t1, 1               # bit 0 (CY) must have taken
        beqz    t2, 40f
        li      t2, 0x1F8               # bits 3..8, the six HPM counters
        and     t3, t1, t2
        beq     t3, t2, 1f
40:     FAILCASE(4)
1:

# ---- case 5: mcountinhibit ACTUALLY INHIBITS ------------------------------
# CY is still set from case 4.
        csrr    t1, mcycle
        addi    x0, x0, 0
        addi    x0, x0, 0
        addi    x0, x0, 0
        csrr    t2, mcycle
        bne     t1, t2, 50f             # frozen: the two reads must be equal
        csrw    0x320, x0               # release everything
        csrr    t1, mcycle
        addi    x0, x0, 0
        csrr    t2, mcycle
        bne     t1, t2, 1f              # and now it must move again
50:     FAILCASE(5)
1:

# ---- case 6: an event actually counts ------------------------------------
# EV_EXSTALL against a divide: 33 stall cycles, deterministic.  An
# inequality; the exact value is the cross-validation's job.
        csrw    0x320, x0               # nothing inhibited
        li      t0, EV_EXSTALL
        csrw    0x323, t0               # mhpmevent3 = multi-cycle EX stalls
        csrw    0xB03, x0               # mhpmcounter3 = 0
        csrw    0xB83, x0
        li      t0, 1000003
        li      t1, 7
        div     t2, t0, t1
        div     t3, t0, t1
        csrr    t4, 0xB03
        li      t5, 60                  # two divides is ~66 stall cycles
        bgeu    t4, t5, 1f
        FAILCASE(6)
1:

# ---- case 7: event 0 counts NOTHING --------------------------------------
# A counter whose selector is 0 must not accumulate.
        li      t0, 0
        csrw    0x324, t0               # mhpmevent4 = HPM_EV_NONE
        csrw    0xB04, x0
        csrw    0xB84, x0
        li      t0, 1000003
        li      t1, 7
        div     t2, t0, t1
        div     t3, t0, t1
        csrr    t4, 0xB04
        beqz    t4, 1f
        FAILCASE(7)
1:

# ---- case 8: mhpmevent is WARL over 0..6 ---------------------------------
# An out-of-range write must leave the selector unchanged rather than
# programming a counter to count an event that does not exist.
        li      t0, EV_BTB_HIT
        csrw    0x325, t0               # mhpmevent5 = 5, a legal value
        li      t0, 15                  # illegal: HPM_EV_MAX is 6
        csrw    0x325, t0
        csrr    t1, 0x325
        li      t2, EV_BTB_HIT
        beq     t1, t2, 1f
        FAILCASE(8)
1:

# ---- case 9: the 32-bit halves are independent, and carry -----------------
# Writing the low half must not disturb the high one, and an increment at
# 0xFFFFFFFF must carry.
        li      t0, EV_EXSTALL
        csrw    0x326, t0               # mhpmevent6 = EX stalls
        li      t0, 0x99999999
        csrw    0xB86, t0               # high half
        li      t0, -1                  # low half = 0xFFFFFFFF
        csrw    0xB06, t0
        csrr    t1, 0xB86
        li      t2, 0x99999999
        bne     t1, t2, 90f             # the high half must have survived
        li      t0, 1000003
        li      t1, 7
        div     t2, t0, t1              # >= 1 stall cycle, so it must carry
        csrr    t1, 0xB86
        li      t2, 0x9999999A
        beq     t1, t2, 1f
90:     FAILCASE(9)
1:

# ---- case 10: the user-mode shadows read through, and are READ-ONLY -------
        csrw    0x320, x0
        li      t0, EV_EXSTALL
        csrw    0x327, t0               # mhpmevent7
        li      t0, 0x5A5A0000
        csrw    0xB07, t0
        csrr    t1, 0xC07               # hpmcounter7 -- the user shadow
        li      t2, 0x5A5A0000
        bne     t1, t2, 100f
        # A write to the shadow must be an illegal instruction.  mcause 2.
        li      t4, 2
        li      t3, 0
        csrw    0xC07, t0
        beqz    t3, 100f                # the trap handler sets t3
        j       1f
100:    FAILCASE(10)
1:

# ---- pass -----------------------------------------------------------------
        li      gp, 0
        li      t0, 1
        la      t1, tohost
        sw      t0, 0(t1)
2:      j       2b

fail:
        slli    gp, gp, 1
        ori     gp, gp, 1
        la      t0, tohost
        sw      gp, 0(t0)
3:      j       3b

# The handler mirrors a9_csr.S's: check mcause against the t4 the case set,
# set t3 so the case can see the trap happened, and resume AFTER the faulting
# instruction so each case reads as straight-line code.
        .align 2
trap_vector:
        csrr    t2, mcause
        bne     t2, t4, trap_wrong_cause
        csrr    t6, mepc
        addi    t2, t6, 4
        csrw    mepc, t2
        li      t3, 1
        mret
trap_wrong_cause:
        slli    gp, gp, 1
        ori     gp, gp, 1
        ori     gp, gp, 0x100
        la      t0, tohost
        sw      gp, 0(t0)
4:      j       4b

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
