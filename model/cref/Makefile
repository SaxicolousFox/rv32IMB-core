# Builds the instrumented reference NTT driver (plan P0.3).
#
# The pq-crystals checkout stays PRISTINE: we compile our instrumented copy of
# ntt.c (model/cref/ntt_dump.c, generated from it; see
# patches/kyber-ref-dump-ntt.patch) against the reference's other sources.  That
# way the reference's own KATs remain valid and unmodified.
KYBER_REF ?= ../../toolchain/kyber/ref
CC        ?= cc
CFLAGS    ?= -O2 -Wall -Wextra -DKYBER_K=3 -DDUMP_NTT -I$(KYBER_REF)

SRCS = ntt_dump.c dump_main.c $(KYBER_REF)/reduce.c

.PHONY: all clean
all: ntt_dump

ntt_dump: $(SRCS)
	$(CC) $(CFLAGS) $(SRCS) -o $@

clean:
	rm -f ntt_dump
