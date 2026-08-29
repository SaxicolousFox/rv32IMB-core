/* Host-side stand-in for htif.c, so the same KAT driver builds natively.
 * The native build is the golden reference the Spike runs are compared to. */
#include <stdio.h>
#include <stdlib.h>
#include "htif.h"

void htif_write(const char *buf, size_t len) { fwrite(buf, 1, len, stdout); }
void htif_flush(void) { fflush(stdout); }
void htif_exit(int code) { htif_flush(); exit(code); }
