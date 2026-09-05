/*
 * The handful of <string.h> functions Dhrystone and CoreMark reach for.
 *
 * These are on the benchmark's critical path -- Dhrystone's inner loop is three
 * strcpy calls of a 31-byte literal and one strcmp -- so a clever version here
 * would inflate the score without touching the core.  They are therefore
 * deliberately the obvious byte-at-a-time implementations, which is also what a
 * newlib build for a machine with no unaligned access ends up doing.  The score
 * is reported alongside this fact; see docs/a13-benchmarks.md.
 *
 * Built with -fno-builtin so GCC does not turn a definition into a call to
 * itself, and -fno-tree-loop-distribute-patterns so the memcpy loop is not
 * recognised as memcpy and replaced by one.
 */
#include <stddef.h>

void *memcpy(void *d, const void *s, size_t n)
{
    unsigned char *dp = (unsigned char *)d;
    const unsigned char *sp = (const unsigned char *)s;
    while (n--)
        *dp++ = *sp++;
    return d;
}

void *memmove(void *d, const void *s, size_t n)
{
    unsigned char *dp = (unsigned char *)d;
    const unsigned char *sp = (const unsigned char *)s;
    if (dp < sp) {
        while (n--)
            *dp++ = *sp++;
    } else {
        dp += n;
        sp += n;
        while (n--)
            *--dp = *--sp;
    }
    return d;
}

void *memset(void *d, int c, size_t n)
{
    unsigned char *dp = (unsigned char *)d;
    while (n--)
        *dp++ = (unsigned char)c;
    return d;
}

int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *x = (const unsigned char *)a;
    const unsigned char *y = (const unsigned char *)b;
    while (n--) {
        if (*x != *y)
            return (int)*x - (int)*y;
        x++; y++;
    }
    return 0;
}

size_t strlen(const char *s)
{
    const char *p = s;
    while (*p)
        p++;
    return (size_t)(p - s);
}

char *strcpy(char *d, const char *s)
{
    char *r = d;
    while ((*d++ = *s++) != '\0')
        ;
    return r;
}

int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++; b++;
    }
    return (int)(unsigned char)*a - (int)(unsigned char)*b;
}

char *strcat(char *d, const char *s)
{
    char *r = d;
    while (*d)
        d++;
    while ((*d++ = *s++) != '\0')
        ;
    return r;
}
