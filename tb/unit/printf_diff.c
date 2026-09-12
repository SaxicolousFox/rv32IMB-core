/*
 * Differential driver for sw/bench/bench_io.c's printf: for each case it
 * emits the bench formatter's output and glibc's on adjacent lines, and
 * tb/unit/test_bench_printf.py diffs the pairs.  The conversions covered are
 * the ones Dhrystone and CoreMark use.  %p and unknown conversions are not
 * compared: bench_io.c defines its own behaviour for both.
 */
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include "bench_io.h"

static char ref[512];

#define BOTH_I(fmt, val)                                                     \
    do {                                                                     \
        printf("B|"); bench_printf(fmt, val); printf("|\n");                 \
        snprintf(ref, sizeof ref, fmt, val);                                 \
        printf("G|%s|\n", ref);                                              \
    } while (0)

#define BOTH_0(fmt)                                                          \
    do {                                                                     \
        printf("B|"); bench_printf(fmt); printf("|\n");                      \
        snprintf(ref, sizeof ref, fmt);                                      \
        printf("G|%s|\n", ref);                                              \
    } while (0)

int main(void)
{
    static const int ints[] = { 0, 1, -1, 7, 42, -42, 9999, -9999,
                                32767, -32768, 1000000, -1000000,
                                INT_MAX, INT_MIN };
    static const unsigned uints[] = { 0u, 1u, 0xFFu, 0x1234u, 0xDEADBEEFu,
                                      0xFFFFFFFFu, 1000000000u };
    static const char *strs[] = { "", "x", "hello", "DHRYSTONE PROGRAM" };
    static const char *ifmt[] = { "%d", "%5d", "%-5d", "%05d", "%12d", "%i" };
    static const char *ufmt[] = { "%u", "%8u", "%08u", "%-8u" };
    static const char *xfmt[] = { "%x", "%X", "%04x", "%04X", "%8x", "%-8x",
                                  "%08x" };
    static const char *sfmt[] = { "%s", "%10s", "%-10s" };
    unsigned i, j;

    for (i = 0; i < sizeof ifmt / sizeof *ifmt; i++)
        for (j = 0; j < sizeof ints / sizeof *ints; j++)
            BOTH_I(ifmt[i], ints[j]);

    for (i = 0; i < sizeof ufmt / sizeof *ufmt; i++)
        for (j = 0; j < sizeof uints / sizeof *uints; j++)
            BOTH_I(ufmt[i], uints[j]);

    for (i = 0; i < sizeof xfmt / sizeof *xfmt; i++)
        for (j = 0; j < sizeof uints / sizeof *uints; j++)
            BOTH_I(xfmt[i], uints[j]);

    for (i = 0; i < sizeof sfmt / sizeof *sfmt; i++)
        for (j = 0; j < sizeof strs / sizeof *strs; j++)
            BOTH_I(sfmt[i], strs[j]);

    for (j = 0; j < 128; j++)
        BOTH_I("%c", (int)('!' + (j % 90)));

    /* The `l` modifier, which is how CoreMark prints its tick counts.  On this
     * host long is 64-bit and on the target it is 32-bit; the values below all
     * fit in 32 bits, so the two agree and the test stays meaningful for both. */
    for (j = 0; j < sizeof uints / sizeof *uints; j++) {
        BOTH_I("%lu", (unsigned long)uints[j]);
        BOTH_I("%lx", (unsigned long)uints[j]);
        BOTH_I("%08lx", (unsigned long)uints[j]);
    }
    for (j = 0; j < sizeof ints / sizeof *ints; j++)
        BOTH_I("%ld", (long)ints[j]);

    BOTH_0("plain text with no conversions");
    BOTH_0("100%% done");
    BOTH_I("mixed %d and text", 17);
    BOTH_I("[%s]", (const char *)NULL);   /* glibc prints (null) too */
    return 0;
}
