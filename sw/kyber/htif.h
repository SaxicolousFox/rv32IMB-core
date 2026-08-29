#ifndef HTIF_H
#define HTIF_H
#include <stddef.h>
#include <stdint.h>

void htif_write(const char *buf, size_t len);   /* buffered; flushed on exit */
void htif_flush(void);
void htif_exit(int code);

/* The reference sources want these; -nostdlib means we supply them. */
void *memcpy(void *d, const void *s, size_t n);
void *memset(void *d, int c, size_t n);

#endif
