/*
 * The A13 console and its printf.
 *
 * Scope is deliberately exactly what Dhrystone and CoreMark ask for and nothing
 * more -- %c %s %d %i %u %x %X %p and the `l` length modifier, with `-`, `0` and
 * a numeric width.  CoreMark's "0x%04x" CRC lines and its "%lu" tick counts are
 * the two that matter for the RESULT; the rest is prose.  An unsupported
 * conversion prints itself back rather than being skipped, so a format this does
 * not handle shows up in the capture as `%q` instead of vanishing.
 *
 * BENCH_HOST swaps the UART for stdout so the identical code can be diffed
 * against glibc; see bench_io.h.
 */
#include <stdarg.h>
#include <stddef.h>
#include "bench_io.h"

#ifdef BENCH_HOST

#include <stdio.h>
#include <time.h>

void bench_putc(char c) { fputc(c, stdout); }

/* Nanoseconds, not cycles.  This exists only so Dhrystone's "measured time too
 * small" loop terminates on the functional host run; nothing derives a score
 * from it, and bench_main.c prints host=1 so the parser cannot. */
unsigned int bench_mcycle(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned int)(ts.tv_sec * 1000000000ull + ts.tv_nsec);
}
unsigned int bench_minstret(void) { return 0; }

#else

#define MMIO_BASE   0x40000000u
#define UART_TX     (*(volatile unsigned int *)(MMIO_BASE + 0x00))
#define UART_STAT   (*(volatile unsigned int *)(MMIO_BASE + 0x04))
#define STAT_TX_READY 0x1u

void bench_putc(char c)
{
    while (!(UART_STAT & STAT_TX_READY))
        ;
    UART_TX = (unsigned char)c;
}

unsigned int bench_mcycle(void)
{
    unsigned int v;
    __asm__ volatile ("csrr %0, mcycle" : "=r"(v));
    return v;
}

unsigned int bench_minstret(void)
{
    unsigned int v;
    __asm__ volatile ("csrr %0, minstret" : "=r"(v));
    return v;
}

#endif

void bench_puts(const char *s)
{
    while (*s)
        bench_putc(*s++);
}

/* Longest is 32-bit octal-free decimal / hex plus a sign: 11 characters. */
static int emit_pad(int n, char pad)
{
    int i;
    for (i = 0; i < n; i++)
        bench_putc(pad);
    return n > 0 ? n : 0;
}

static int emit_num(unsigned long v, unsigned base, int upper,
                    int neg, int width, int zero, int left)
{
    static const char lo[] = "0123456789abcdef";
    static const char up[] = "0123456789ABCDEF";
    const char *digits = upper ? up : lo;
    char buf[24];
    int n = 0, pad, out = 0, i;

    if (v == 0)
        buf[n++] = '0';
    while (v) {
        buf[n++] = digits[v % base];
        v /= base;
    }
    if (neg)
        buf[n++] = '-';

    pad = width - n;
    /* A '0' pad and a '-' flag cannot both apply; C says '-' wins. */
    if (!left && zero) {
        /* Zero padding goes AFTER the sign, which is why the sign is emitted
         * here rather than left in buf: "%05d" of -42 is "-0042", not "0-042". */
        if (neg) { bench_putc('-'); out++; n--; }
        out += emit_pad(pad, '0');
    } else if (!left) {
        out += emit_pad(pad, ' ');
    }
    for (i = n - 1; i >= 0; i--) {
        bench_putc(buf[i]);
        out++;
    }
    if (left)
        out += emit_pad(pad, ' ');
    return out;
}

static int vbench_printf(const char *fmt, va_list ap)
{
    int out = 0;

    while (*fmt) {
        int left = 0, zero = 0, width = 0, lng = 0;
        char c;

        if (*fmt != '%') {
            bench_putc(*fmt++);
            out++;
            continue;
        }
        fmt++;
        for (;;) {
            if (*fmt == '-')      { left = 1; fmt++; }
            else if (*fmt == '0') { zero = 1; fmt++; }
            else break;
        }
        while (*fmt >= '0' && *fmt <= '9')
            width = width * 10 + (*fmt++ - '0');
        while (*fmt == 'l' || *fmt == 'h' || *fmt == 'z') {
            if (*fmt == 'l' || *fmt == 'z')
                lng = 1;
            fmt++;
        }
        c = *fmt++;
        switch (c) {
        case 'd':
        case 'i': {
            long v = lng ? va_arg(ap, long) : (long)va_arg(ap, int);
            unsigned long u = (v < 0) ? (unsigned long)(-(v + 1)) + 1u
                                      : (unsigned long)v;
            out += emit_num(u, 10, 0, v < 0, width, zero, left);
            break;
        }
        case 'u': {
            unsigned long v = lng ? va_arg(ap, unsigned long)
                                  : (unsigned long)va_arg(ap, unsigned int);
            out += emit_num(v, 10, 0, 0, width, zero, left);
            break;
        }
        case 'x':
        case 'X': {
            unsigned long v = lng ? va_arg(ap, unsigned long)
                                  : (unsigned long)va_arg(ap, unsigned int);
            out += emit_num(v, 16, c == 'X', 0, width, zero, left);
            break;
        }
        case 'p': {
            unsigned long v = (unsigned long)(size_t)va_arg(ap, void *);
            bench_puts("0x");
            out += 2 + emit_num(v, 16, 0, 0, width, zero, left);
            break;
        }
        case 'c': {
            int v = va_arg(ap, int);
            if (!left) out += emit_pad(width - 1, ' ');
            bench_putc((char)v);
            out++;
            if (left) out += emit_pad(width - 1, ' ');
            break;
        }
        case 's': {
            const char *s = va_arg(ap, const char *);
            int n = 0, i;
            if (!s) s = "(null)";
            while (s[n]) n++;
            if (!left) out += emit_pad(width - n, ' ');
            for (i = 0; i < n; i++) bench_putc(s[i]);
            out += n;
            if (left) out += emit_pad(width - n, ' ');
            break;
        }
        case '%':
            bench_putc('%');
            out++;
            break;
        case '\0':
            /* Trailing '%': emit it and stop, rather than reading past the end. */
            bench_putc('%');
            return out + 1;
        default:
            /* Unknown conversion: print it verbatim so it is visible in the
             * capture.  No va_arg is consumed, so everything after it is
             * garbage -- which is the point: it must not look plausible. */
            bench_putc('%');
            bench_putc(c);
            out += 2;
            break;
        }
    }
    return out;
}

int bench_printf(const char *fmt, ...)
{
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vbench_printf(fmt, ap);
    va_end(ap);
    return n;
}

#ifndef BENCH_HOST
int printf(const char *fmt, ...)
{
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vbench_printf(fmt, ap);
    va_end(ap);
    return n;
}
#endif
