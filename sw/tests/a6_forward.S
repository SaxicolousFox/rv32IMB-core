# ============================================================================
# a6_forward.S -- directed forwarding tests.
#
# Checked by diffing the whole commit log against Spike, so no expected value
# is written down here; what this file has to get right is the hazard
# distances, counted from the consumer:
#   1  producer is in MEM  -> FWD_MEM
#   2  producer is in WB   -> FWD_WB
#   3  producer wrote the register file on the edge the consumer read it
#      -> the register file's write-through
#
# `nop` is `addi x0, x0, 0`, so padding with it never creates a dependency.
# x8 is the scratch pointer and is never written after the prologue.
# ============================================================================
        .section .text.init
        .globl _start
_start:
        # ---- prologue, and case 0 in its own right -----------------------
        # `la x8, scratch` is auipc+addi with the addi reading the auipc's rd
        # at distance 1.
1:      auipc   x8, %pcrel_hi(scratch)
        addi    x8, x8, %pcrel_lo(1b)

        # ---- seeds.  Each addi reads its lui at distance 1. --------------
        lui     x2, 0x11111
        addi    x2, x2, 0x111
        lui     x3, 0x22222
        addi    x3, x3, 0x222
        lui     x4, 0x33333
        addi    x4, x4, 0x333
        lui     x5, 0x44444
        addi    x5, x5, 0x444

        # ---- case 1: the priority case -----------------------------------
        # Two producers of x1 are in flight at once; the consumer must take
        # the younger one, in MEM.  The two write different values.
        add     x1, x2, x3
        add     x1, x4, x5
        add     x6, x1, x0

        # ---- case 2: distance 2 only -- FWD_WB ---------------------------
        add     x1, x2, x3
        nop
        add     x7, x1, x0

        # ---- case 3: distance 3 -- the register file's write-through -----
        # No forwarding path is involved here; this is the case that must NOT
        # regress when the forwarding network is added.
        add     x1, x4, x2
        nop
        nop
        add     x7, x1, x0

        # ---- case 4: the rs2 port, at both distances ---------------------
        add     x1, x2, x5
        add     x7, x0, x1
        add     x1, x3, x5
        nop
        sub     x7, x0, x1

        # ---- case 5: BOTH operands forwarded, from DIFFERENT stages ------
        # rs1 comes from WB and rs2 from MEM in the same instruction.  A
        # forwarding unit that shares one select between the two ports gets
        # this wrong and gets everything above right.
        add     x1, x2, x3
        add     x17, x4, x5
        add     x6, x1, x17

        # ---- case 6: the STORE DATA operand ------------------------------
        # rs2 of a store does not pass through the ALU; observed by loading it
        # back.
        add     x1, x2, x4
        sw      x1, 0(x8)
        nop
        nop
        lw      x9, 0(x8)

        # ---- case 7: the STORE ADDRESS operand ---------------------------
        addi    x10, x8, 16
        sb      x2, 0(x10)
        nop
        nop
        lbu     x9, 16(x8)

        # ---- case 8: rd == x0 SUPPRESSION --------------------------------
        # The producer nominally writes x0.  x0 must still read as zero one and
        # two instructions later; a forwarding unit missing the rd != 0 term
        # delivers x2+x3 instead, to both of these.
        add     x0, x2, x3
        add     x11, x0, x0
        addi    x12, x0, 7

        # ---- case 9: no PHANTOM forwarding into a field that is not a source
        # LUI has no rs1 operand -- insn[19:15] is part of its immediate.  This
        # LUI's immediate makes those bits read as x1, immediately after x1 was
        # written.  The result must be the immediate, untouched.
        add     x1, x2, x3
        lui     x13, 8

        # ---- case 10: a forwarded SHIFT AMOUNT ---------------------------
        # rs2 reaches the shifter as a 5-bit shamt rather than as a word, so it
        # is a separate consumer of the same forwarded value.
        addi    x14, x0, 5
        sll     x15, x2, x14
        addi    x14, x0, 31
        srl     x15, x3, x14

        # ---- case 11: a chain, every link at distance 1 ------------------
        addi    x16, x0, 1
        addi    x16, x16, 1
        addi    x16, x16, 1
        addi    x16, x16, 1
        addi    x16, x16, 1

        # ---- case 12: a LOAD's result at distance 2 ----------------------
        # The load is in WB by then, and FWD_WB carries load data.
        lw      x18, 4(x8)
        nop
        add     x19, x18, x0
        lbu     x18, 3(x8)
        nop
        xor     x19, x18, x2

        # ---- case 13: forwarding into an address computation --------------
        # The forwarded value becomes the RAM's address in the same cycle, not
        # just an ALU input.
        andi    x10, x2, 60
        add     x10, x10, x8
        lw      x9, 0(x10)

        # ---- epilogue: arm Spike's trap handler, then stop ---------------
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

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
