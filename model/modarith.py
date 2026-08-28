"""
Kyber / ML-KEM-768 modular arithmetic, matching pq-crystals reference bit-for-bit.

Everything here reproduces the *exact* integer behaviour of the C reference,
including the signed C types and the lazy (not fully reduced) output ranges.
Plan section 1.3 is emphatic about this: if the model normalises and the
reference does not, comparisons fail for reasons that are not real bugs.
"""

Q     = 3329
QINV  = 62209          # q^-1 mod 2^16.  Reference writes it as -3327.
BARR_V = 20159         # ((1<<26) + q/2) / q

def _i16(x: int) -> int:
    """Truncate to a C int16_t."""
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x

def _i32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x

def _asr(x: int, n: int) -> int:
    """Arithmetic shift right; Python's >> is already arithmetic for ints."""
    return x >> n

def montgomery_reduce(a: int) -> int:
    """
    C reference:
        int16_t t = (int16_t)(a * QINV);
        return (a - (int32_t)t * Q) >> 16;
    Input  : a in [-Q*2^15, Q*2^15)
    Output : congruent to a * R^-1 mod Q, in (-Q, Q).  NOT fully reduced.
    """
    a = _i32(a)
    t = _i16(a * QINV)
    return _asr(_i32(a - t * Q), 16)

def barrett_reduce(a: int) -> int:
    """
    C reference:
        int16_t t = ((int32_t)V*a + (1<<25)) >> 26;
        return a - t*Q;
    Output in [-(Q-1)/2, (Q-1)/2].
    """
    a = _i16(a)
    t = _asr(_i32(BARR_V * a + (1 << 25)), 26)
    t = _i16(t)
    return _i16(a - t * Q)

def to_mont(x: int) -> int:
    """Map x into the Montgomery domain: x * R mod Q."""
    return (x * (1 << 16)) % Q

def from_mont(x: int) -> int:
    """Map x out of the Montgomery domain: x * R^-1 mod Q, fully reduced."""
    return (x * pow(1 << 16, -1, Q)) % Q
