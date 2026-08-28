"""
INDEPENDENT model of the Kyber / ML-KEM NTT.

This shares no code and no algorithm with the reference: no butterflies, no
Montgomery domain, no zeta table, no bit-reversal permutation applied to data.
It computes the transform directly from its *definition* using Python big
integers, which is the whole point -- "two independent models that agree is what
golden means; one model is just a second copy of your assumptions" (plan P0.3).

Definition
----------
Kyber's NTT is INCOMPLETE.  q = 3329 has a primitive 256th root of unity
(zeta = 17) but NO primitive 512th root, so X^256 + 1 does not split into linear
factors over Z_q -- it splits into 128 quadratics:

    X^256 + 1 = prod_{i=0..127} ( X^2 - zeta^(2*br7(i)+1) )

so the transform of f is the tuple of residues f mod (X^2 - gamma_i).  Writing
X^2 == gamma, we get X^(2m) == gamma^m and X^(2m+1) == gamma^m * X, hence

    f mod (X^2 - gamma) = c0 + c1*X,
        c0 = sum_m f[2m]   * gamma^m
        c1 = sum_m f[2m+1] * gamma^m

The reference stores the pair for index i at r[2i], r[2i+1].
"""
from modarith import Q

ZETA = 17
N = 256


def br7(i: int) -> int:
    return int(format(i, "07b")[::-1], 2)


# gamma_i = zeta^(2*br7(i)+1) mod q, one per output pair.
GAMMAS = [pow(ZETA, 2 * br7(i) + 1, Q) for i in range(128)]

# Precompute gamma^m and gamma^-m for m in 0..127 for each i (128x128 tables).
_GPOW    = [[pow(g, m, Q) for m in range(128)] for g in GAMMAS]
_GINVPOW = [[pow(g, -m, Q) for m in range(128)] for g in GAMMAS]


def ntt(f: list) -> list:
    """Forward NTT.  Input/output are fully reduced in [0, q)."""
    assert len(f) == N
    out = [0] * N
    for i in range(128):
        gp = _GPOW[i]
        c0 = 0
        c1 = 0
        for m in range(128):
            gm = gp[m]
            c0 += f[2 * m] * gm
            c1 += f[2 * m + 1] * gm
        out[2 * i] = c0 % Q
        out[2 * i + 1] = c1 % Q
    return out


def intt(fhat: list) -> list:
    """
    Inverse NTT by explicit CRT interpolation -- no butterflies, no Montgomery.

    The gamma_i are exactly the 128 roots of Y^128 + 1 over Z_q, so their power
    sums vanish:  sum_i gamma_i^k = 128 if k == 0 (mod 128 range), else 0, for
    k in -127..127.  Therefore the forward map is inverted directly by

        f[2m]   = (1/128) * sum_i c0_i * gamma_i^-m
        f[2m+1] = (1/128) * sum_i c1_i * gamma_i^-m

    Both facts are asserted in selftest.py rather than taken on trust.
    """
    assert len(fhat) == N
    inv128 = pow(128, -1, Q)
    out = [0] * N
    for m in range(128):
        a0 = 0
        a1 = 0
        for i in range(128):
            gim = _GINVPOW[i][m]
            a0 += fhat[2 * i] * gim
            a1 += fhat[2 * i + 1] * gim
        out[2 * m] = (a0 * inv128) % Q
        out[2 * m + 1] = (a1 * inv128) % Q
    return out


def poly_mul(a: list, b: list) -> list:
    """Schoolbook multiply in Z_q[X]/(X^256 + 1), for end-to-end cross-checks."""
    res = [0] * (2 * N)
    for i, ai in enumerate(a):
        if ai:
            for j, bj in enumerate(b):
                res[i + j] += ai * bj
    out = [0] * N
    for i in range(N):
        out[i] = (res[i] - res[i + N]) % Q     # X^N == -1
    return out
