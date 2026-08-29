/*
 * HTIF console and exit for bare-metal Spike runs, plus the two libc functions
 * the pq-crystals reference needs.  Built -nostdlib on purpose: pulling in
 * newlib would drag syscall stubs and a heap into a program whose only job is
 * to reproduce a known-answer test.
 */
#include "htif.h"

#define SYS_write 64

volatile uint64_t tohost   __attribute__((section(".tohost"), aligned(64))) = 0;
volatile uint64_t fromhost __attribute__((section(".tohost"), aligned(64))) = 0;

static uintptr_t syscall4(uintptr_t which, uintptr_t a0, uintptr_t a1, uintptr_t a2)
{
  volatile uint64_t magic[8];
  magic[0] = which; magic[1] = a0; magic[2] = a1; magic[3] = a2;
  magic[4] = 0; magic[5] = 0; magic[6] = 0; magic[7] = 0;
  __sync_synchronize();
  tohost = (uintptr_t)magic;
  while (fromhost == 0) { }
  fromhost = 0;
  __sync_synchronize();
  return (uintptr_t)magic[0];
}

/* One HTIF syscall per character would dominate the run time, so buffer.
 * 8 KiB keeps the syscall count under a thousand even for the full KAT. */
static char obuf[8192];
static size_t olen;

void htif_flush(void)
{
  if (olen) {
    syscall4(SYS_write, 1, (uintptr_t)obuf, olen);
    olen = 0;
  }
}

void htif_write(const char *buf, size_t len)
{
  while (len--) {
    obuf[olen++] = *buf++;
    if (olen == sizeof(obuf)) htif_flush();
  }
}

void htif_exit(int code)
{
  htif_flush();
  tohost = ((uint64_t)(uint32_t)code << 1) | 1;
  for (;;) { }
}

void *memcpy(void *d, const void *s, size_t n)
{
  unsigned char *dst = d; const unsigned char *src = s;
  while (n--) *dst++ = *src++;
  return d;
}

void *memset(void *d, int c, size_t n)
{
  unsigned char *dst = d;
  while (n--) *dst++ = (unsigned char)c;
  return d;
}
