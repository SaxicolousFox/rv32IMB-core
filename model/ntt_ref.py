"""
Bit-exact Python transcription of the pq-crystals reference NTT.

This model deliberately reproduces the reference's *lazy* (unreduced) int16
arithmetic exactly, so it can be compared against the C per-layer dumps
value-for-value.  It is NOT an independent check of the mathematics -- see
ntt_math.py for that.  Its job is localisation: telling you which layer broke.

The zeta table is GENERATED here from the root of unity, then checked against the
reference's literal array (plan B0: "Do not hand-copy it").
"""
from modarith import Q, montgomery_reduce, barrett_reduce, _i16

MONT = (1 << 16) % Q          # 2^16 mod q == 2285 == -1044 centred
ROOT_OF_UNITY = 17
F_INVNTT = 1441               # mont^2 / 128


def br7(i: int) -> int:
    """7-bit reversal."""
    return int(format(i, "07b")[::-1], 2)


def _centre(x: int) -> int:
    """Reference keeps zetas in (-q/2, q/2)."""
    if x > Q // 2:
        x -= Q
    if x < -(Q // 2):
        x += Q
    return x


def fqmul(a: int, b: int) -> int:
    """Reference fqmul: montgomery_reduce(a*b), i.e. a*b*R^-1 mod q."""
    return montgomery_reduce(_i16(a) * _i16(b))


def gen_zetas() -> list:
    """Reproduce the reference's init_ntt() zeta generation."""
    tmp = [0] * 128
    tmp[0] = MONT
    step = (MONT * ROOT_OF_UNITY) % Q
    for i in range(1, 128):
        tmp[i] = fqmul(tmp[i - 1], step)
    return [_centre(tmp[br7(i)] % Q) for i in range(128)]


ZETAS = gen_zetas()


def ntt(r: list, layers: list = None) -> list:
    """
    Forward NTT, in place on a copy.  If `layers` is a list, one snapshot per
    layer is appended to it (7 entries), matching the C DUMP_NTT hooks.
    """
    r = list(r)
    k = 1
    length = 128
    while length >= 2:
        start = 0
        while start < 256:
            zeta = ZETAS[k]; k += 1
            for j in range(start, start + length):
                t = fqmul(zeta, r[j + length])
                r[j + length] = _i16(r[j] - t)
                r[j] = _i16(r[j] + t)
            start = j + 1 + length
        if layers is not None:
            layers.append(list(r))
        length >>= 1
    return r


def invntt(r: list, layers: list = None) -> list:
    """Inverse NTT including the final mont^2/128 scaling."""
    r = list(r)
    k = 127
    length = 2
    while length <= 128:
        start = 0
        while start < 256:
            zeta = ZETAS[k]; k -= 1
            for j in range(start, start + length):
                t = r[j]
                r[j] = barrett_reduce(_i16(t + r[j + length]))
                r[j + length] = _i16(r[j + length] - t)
                r[j + length] = fqmul(zeta, r[j + length])
            start = j + 1 + length
        if layers is not None:
            layers.append(list(r))
        length <<= 1
    for j in range(256):
        r[j] = fqmul(r[j], F_INVNTT)
    return r
