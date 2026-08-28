/*
 * P0.3 golden-model driver.
 *
 * Reads polynomials (256 hex int16 values per line) from a file, runs the
 * INSTRUMENTED reference ntt()/invntt() over each, and writes per-layer dumps.
 *
 * Inputs are supplied by Python rather than generated here on purpose: both
 * sides must see byte-identical inputs, and duplicating a PRNG in two languages
 * is exactly the kind of "second copy of your assumptions" the plan warns about.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "params.h"
#include "ntt.h"

extern FILE *kntt_dump_fp;
extern int   kntt_dump_idx;

int main(int argc, char **argv) {
  if (argc != 4) {
    fprintf(stderr, "usage: %s <inputs.hex> <dumps.txt> <fwd|inv>\n", argv[0]);
    return 2;
  }
  const char *mode = argv[3];
  int do_fwd = (strcmp(mode, "fwd") == 0);
  if (!do_fwd && strcmp(mode, "inv") != 0) {
    fprintf(stderr, "mode must be 'fwd' or 'inv'\n");
    return 2;
  }

  FILE *in = fopen(argv[1], "r");
  if (!in) { perror("open inputs"); return 1; }
  kntt_dump_fp = fopen(argv[2], "w");
  if (!kntt_dump_fp) { perror("open dumps"); return 1; }

  int16_t r[256];
  int n = 0;
  for (;;) {
    int i, got = 0;
    for (i = 0; i < 256; i++) {
      unsigned v;
      if (fscanf(in, "%x", &v) != 1) break;
      r[i] = (int16_t)(uint16_t)v;
      got++;
    }
    if (got == 0) break;
    if (got != 256) {
      fprintf(stderr, "short polynomial at index %d (%d coeffs)\n", n, got);
      return 1;
    }
    kntt_dump_idx = n;
    if (do_fwd) ntt(r); else invntt(r);
    n++;
  }

  fclose(in);
  fclose(kntt_dump_fp);
  fprintf(stderr, "processed %d polynomials (%s)\n", n, mode);
  return 0;
}
