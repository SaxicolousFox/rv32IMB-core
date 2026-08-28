# Smoke test: proves Spike executes RV32I and exits via HTIF.
# Computes 5+7 and checks it is 12; exits 0 on success, 1 on failure.
        .section .text.init
        .globl _start
_start:
        li      a0, 5
        li      a1, 7
        add     a2, a0, a1
        li      a3, 12
        beq     a2, a3, pass
fail:   li      t0, 3                   # HTIF: (1 << 1) | 1  -> exit code 1
        j       done
pass:   li      t0, 1                   # HTIF: (0 << 1) | 1  -> exit code 0
done:   la      t1, tohost
        sw      t0, 0(t1)
1:      j       1b

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
