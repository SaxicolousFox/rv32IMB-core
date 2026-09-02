# ============================================================================
# a14_muldiv.S -- directed M-extension tests (MODS_A A14).
#
# Checked the same two ways every directed program here is, and it takes both:
#
#   * the commit log is diffed against Spike, which is the reference for every
#     value below -- including the three cases the specification legislates for
#     and every implementation gets wrong;
#   * the CYCLE SPAN is compared against tb/cosim/cycle_model.py, which is what
#     makes the LATENCY a checked property rather than an assumption.  A
#     multiplier that took three cycles instead of four, or a divider that
#     terminated early on a small dividend, would produce a byte-identical
#     commit log.
#
# NO M INSTRUCTION MAY TRAP.  RISC-V division raises no exceptions at all, so
# neither a zero divisor nor the -2^31 / -1 overflow is an exception here.  That
# is checked structurally rather than asserted: this program's stop condition is
# reaching `trap_handler`, so a core that trapped would stop there and Spike --
# which does not trap -- would carry on, and the two logs would diverge in
# length at exactly the offending instruction.
#
# THE ORDER OF THE CASES IS NOT ARBITRARY.  The value cases come first, so that
# a wrong answer is reported before a timing disagreement; the hazard cases come
# after, because a hazard test over an arithmetic unit that is already wrong
# tells you nothing.
#
# x8 is the scratch pointer throughout and is never written after the prologue.
# ============================================================================
        .section .text.init
        .globl _start
_start:
1:      auipc   x8, %pcrel_hi(scratch)
        addi    x8, x8, %pcrel_lo(1b)

        # ================================================================
        # 1. All eight operations, ordinary operands, BACK TO BACK.
        # ================================================================
        # Back-to-back is its own case: the unit deasserts `done` and the next
        # instruction's first EX cycle is the very next cycle, so a sequencer
        # that needed an idle cycle between operations would either lose one or
        # cost an extra cycle the model does not predict.
        li      x9,  0x12345678
        li      x10, 0x0000abcd
        mul     x11, x9, x10
        mulh    x12, x9, x10
        mulhsu  x13, x9, x10
        mulhu   x14, x9, x10
        div     x15, x9, x10
        divu    x16, x9, x10
        rem     x17, x9, x10
        remu    x18, x9, x10

        # ================================================================
        # 2. The sign matrix, which is the only thing separating the four
        #    multiplies from one another.
        # ================================================================
        # With rs1 = -1 and rs2 = 0xFFFFFFFF the three high-half multiplies give
        # three DIFFERENT answers -- 0x00000000, 0xFFFFFFFF and 0xFFFFFFFE --
        # so an implementation that sign-extends the wrong operand cannot land
        # on the right one by accident.  A test using small positive values
        # would give the same answer for all three.
        li      x9,  -1
        li      x10, -1
        mulh    x11, x9, x10            # (-1) * (-1)          -> 0x00000000
        mulhsu  x12, x9, x10            # (-1) * 4294967295    -> 0xFFFFFFFF
        mulhu   x13, x9, x10            # 4294967295 squared   -> 0xFFFFFFFE
        mul     x14, x9, x10            # low half agrees for all four -> 1

        li      x9,  0x7fffffff
        li      x10, 0x80000000
        mulh    x15, x9, x10
        mulhsu  x16, x9, x10
        mulhu   x17, x9, x10
        mulh    x18, x10, x9            # the operands the other way round:
        mulhsu  x19, x10, x9            # MULHSU is the one that is not
        mulhu   x20, x10, x9            # symmetric, and this is where it shows

        # ================================================================
        # 3. DIVISION BY ZERO.  Quotient all ones, remainder the DIVIDEND.
        # ================================================================
        # The remainder is the original signed dividend, not its magnitude,
        # which is why the negative case below is not redundant.
        li      x9, 0x0badf00d
        div     x11, x9, x0             # -> 0xFFFFFFFF
        divu    x12, x9, x0             # -> 0xFFFFFFFF
        rem     x13, x9, x0             # -> 0x0BADF00D
        remu    x14, x9, x0             # -> 0x0BADF00D
        li      x9, -12345
        div     x15, x9, x0             # -> 0xFFFFFFFF
        rem     x16, x9, x0             # -> -12345, sign intact
        divu    x17, x9, x0             # -> 0xFFFFFFFF
        remu    x18, x9, x0             # -> 0xFFFFCFC7
        div     x19, x0, x0             # 0 / 0 is still -1
        rem     x20, x0, x0             # ... and remainder 0

        # ================================================================
        # 4. SIGNED OVERFLOW: -2^31 / -1.
        # ================================================================
        # The quotient is -2^31 and the remainder is 0, and NEITHER TRAPS.  In
        # this implementation both fall out of the general path -- |-2^31| is
        # 0x80000000 in 32-bit two's complement and the sign rule says the
        # quotient is positive, so it is never negated -- which is precisely why
        # it is tested rather than reasoned about.  "It happens to work" is a
        # claim, and this is the check on it.
        li      x9,  0x80000000
        li      x10, -1
        div     x11, x9, x10            # -> 0x80000000
        rem     x12, x9, x10            # -> 0
        divu    x13, x9, x10            # unsigned: 2^31 / (2^32-1) -> 0
        remu    x14, x9, x10            # ... remainder 0x80000000
        li      x10, 1
        div     x15, x9, x10            # -2^31 / 1, the neighbouring case
        rem     x16, x9, x10

        # ================================================================
        # 5. ROUNDING TOWARD ZERO, all four sign combinations.
        # ================================================================
        # This is the case a divider built on floor division gets wrong, and it
        # is wrong in a way that looks like an off-by-one rather than like a
        # sign bug: -7/2 is -3 remainder -1, not -4 remainder 1.  The identity
        # to hold is  q * b + r == a  with  sign(r) == sign(a).
        li      x9,  7
        li      x10, 2
        div     x11, x9, x10            #  7 /  2 ->  3 r  1
        rem     x12, x9, x10
        li      x9, -7
        div     x13, x9, x10            # -7 /  2 -> -3 r -1
        rem     x14, x9, x10
        li      x10, -2
        div     x15, x9, x10            # -7 / -2 ->  3 r -1
        rem     x16, x9, x10
        li      x9,  7
        div     x17, x9, x10            #  7 / -2 -> -3 r  1
        rem     x18, x9, x10

        # ================================================================
        # 6. Degenerate operands.
        # ================================================================
        li      x9, 0x0f0f0f0f
        mul     x11, x0, x9             # x0 as a source
        div     x12, x0, x9
        rem     x13, x0, x9
        mulhu   x14, x0, x0
        mul     x15, x9, x9             # the same register twice
        div     x16, x9, x9             # -> 1
        rem     x17, x9, x9             # -> 0
        # rd == x0.  The result is discarded but the instruction still occupies
        # EX for its full latency; a unit that skipped the operation when
        # nothing could read it would be faster, change no architectural state,
        # and be visible ONLY to the cycle model.
        div     x0, x9, x10
        mul     x0, x9, x10

        # ================================================================
        # 7. THE OPERAND-CAPTURE CASE.  A producer at distance 1 into a DIVIDE.
        # ================================================================
        # This is the shape rvntt_muldiv.sv's header is about.  During the
        # divide's 33 stall cycles the producer drains out of MEM and then out
        # of WB, so FWD_MEM stops matching after cycle 1 and FWD_WB after cycle
        # 2, and the mux falls back to a register-file value that is two
        # instructions stale.  A unit that re-read its inputs every cycle
        # computes the right answer for four cycles and the wrong one for the
        # remaining thirty.
        #
        # It has to be a DIVIDE.  With the four-cycle multiplier the fallback is
        # only reached on the last cycle, and an operand captured then is still
        # -- by luck -- capable of being right.
        li      x2, 1000
        li      x3, 7
        li      x4, 3
        add     x9, x2, x3              # 1007
        div     x11, x9, x4             # rs1 from FWD_MEM on the start cycle
        add     x9, x2, x3
        div     x12, x4, x9             # ... and the same for rs2
        add     x9, x2, x3
        sub     x10, x2, x4             # 997
        div     x13, x9, x10            # both operands forwarded, distances 2 and 1
        add     x9, x2, x3
        nop
        div     x14, x9, x4             # distance 2: FWD_WB on the start cycle
        add     x9, x2, x3
        nop
        nop
        div     x15, x9, x4             # distance 3: the register file's write-through
        # The same shape into a MULTIPLY.  Four cycles is short enough that the
        # forwarding mux only reaches its stale fallback on the last one, so
        # this case is weaker than the divides above -- and it is still needed,
        # because the multiplier's operand registers are separate logic from
        # the divider's and a capture bug can live in one and not the other.
        add     x9, x2, x3
        mul     x17, x9, x4
        sub     x10, x2, x4
        mul     x18, x9, x10

        # ================================================================
        # 8. LOAD-USE into an M instruction.
        # ================================================================
        # Two mechanisms in series: the interlock bubbles one cycle so the load
        # is in WB, and the unit then captures from FWD_WB.
        lw      x9, 0(x8)
        mul     x11, x9, x9
        lw      x9, 4(x8)
        div     x12, x9, x4
        lw      x9, 8(x8)
        lw      x10, 12(x8)
        rem     x13, x9, x10

        # ================================================================
        # 9. An M result CONSUMED at each distance, and by each consumer kind.
        # ================================================================
        li      x9, 0x00010001
        li      x10, 0x00000101
        mul     x11, x9, x10
        add     x12, x11, x11           # distance 1 -> FWD_MEM
        mul     x13, x9, x10
        nop
        add     x14, x13, x13           # distance 2 -> FWD_WB
        div     x15, x9, x10
        nop
        nop
        add     x16, x15, x15           # distance 3 -> write-through
        # The store-data operand, which does not go through the ALU at all and
        # is therefore the one a forwarding fix can half-miss.
        mul     x17, x9, x10
        sw      x17, 40(x8)
        # ... and as an ADDRESS, which puts the multi-cycle result on the path
        # into the RAM's address register.
        li      x9, 4
        mul     x10, x9, x9             # 16
        add     x11, x8, x10
        lw      x12, 0(x11)
        sw      x12, 4(x11)
        # A divide immediately before a BRANCH on its result: the comparator
        # reads the forwarded operands, so this is the control-flow equivalent
        # of the store-data case.
        li      x9, 100
        li      x10, 7
        div     x11, x9, x10            # 14
        addi    x12, x0, 14
        beq     x11, x12, 2f            # must be taken
        li      x13, 0xbad
2:      rem     x14, x9, x10            # 2 -- an M instruction in a branch shadow
        div     x15, x9, x10

        # ================================================================
        # 10. Back-to-back divides, and a divide immediately after a jump.
        # ================================================================
        li      x9, 0x76543210
        li      x10, 0x00000abc
        div     x11, x9, x10
        div     x12, x11, x10
        rem     x13, x12, x10
        mul     x14, x13, x13
        jal     x0, 3f
        li      x15, 0xbad
3:      div     x16, x9, x10            # the first instruction after a redirect

        # ---- arm Spike's trap handler, then stop -------------------------
4:      auipc   t0, %pcrel_hi(trap_handler)
        addi    t0, t0, %pcrel_lo(4b)
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
5:      auipc   t1, %pcrel_hi(tohost)
        addi    t1, t1, %pcrel_lo(5b)
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
