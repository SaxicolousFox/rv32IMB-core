# ============================================================================
# a7_loaduse.S -- directed load-use interlock tests.
#
# Checked two ways: the commit log against Spike catches a stall that was
# missing; the cycle span against tb/cosim/cycle_model.py catches a stall that
# should not have happened.  Half the cases are therefore "must NOT stall"
# cases, which only the span can see.
#
# `ptrs` holds linker-resolved pointers into `scratch`, so a value loaded and
# then used as an address is a real, aligned address.  x8 is the scratch
# pointer and is never written after the prologue.
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

        # ---- case 1: load -> dependent ALU on rs1.  MUST STALL. ----------
        lw      x9, 0(x8)
        addi    x10, x9, 3

        # ---- case 2: load -> dependent ALU on rs2.  MUST STALL. ----------
        lw      x9, 4(x8)
        sub     x10, x2, x9

        # ---- case 3: load -> dependent STORE DATA.  MUST STALL. ----------
        # The store's rs2 never reaches the ALU.
        lw      x9, 8(x8)
        sw      x9, 32(x8)

        # ---- case 4: load -> dependent STORE ADDRESS.  MUST STALL. -------
        lw      x11, 64(x8)
        sw      x2, 4(x11)

        # ---- case 5: load -> dependent LOAD ADDRESS.  MUST STALL. --------
        # Two loads back to back, the second addressing through the first.
        lw      x12, 64(x8)
        lw      x13, 8(x12)

        # ---- case 6: BOTH sources are the loaded register.  MUST STALL. --
        lw      x14, 12(x8)
        add     x15, x14, x14

        # ---- case 7: distance 2.  MUST NOT STALL. ------------------------
        # The load is in WB by the time the consumer reaches EX.
        lw      x16, 16(x8)
        nop
        add     x17, x16, x2

        # ---- case 8: unrelated consumer.  MUST NOT STALL. ----------------
        lw      x18, 20(x8)
        add     x19, x2, x3

        # ---- case 9: `lw x0`.  MUST NOT STALL. ---------------------------
        # rd == x0 matches every instruction that names x0 as a source,
        # including every `nop`.
        lw      x0, 24(x8)
        add     x20, x0, x2
        lw      x0, 24(x8)
        nop

        # ---- case 10: a NON-SOURCE register field.  MUST NOT STALL. ------
        # LUI has no rs1 operand; insn[19:15] is part of its immediate and
        # here reads as x21, immediately after a load into x21.
        lw      x21, 28(x8)
        lui     x22, 8

        # AUIPC has the same shape, and reaches the hazard unit through a
        # different decoder path (SRCA_PC rather than ALU_PASS_B).
        lw      x1, 0(x8)
        auipc   x23, 8

        # ---- case 11: a chain of dependent loads.  STALLS EACH TIME. -----
        lw      x24, 64(x8)
        lw      x24, 0(x24)
        andi    x24, x24, 60
        add     x24, x24, x8
        lw      x25, 0(x24)

        # ---- case 12: an ALU result at distance 1.  MUST NOT STALL. ------
        add     x26, x2, x3
        add     x27, x26, x2
        sub     x28, x27, x26

        # ---- epilogue ----------------------------------------------------
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
        .word 0x00000001, 0xffffffff, 0x5a5a5a5a, 0xa5a5a5a5
        .word 0x12345678, 0x9abcdef0, 0x0000ff00, 0xff0000ff
        .word 0x80000000, 0x7fffffff, 0x00008000, 0x00007fff
# Offset 64 from `scratch`: pointers the program can load and then dereference.
ptrs:   .word scratch
        .word scratch + 8

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
