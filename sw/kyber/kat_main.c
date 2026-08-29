/*
 * ML-KEM-768 known-answer-test driver, byte-identical to the reference's
 * test/test_vectors.c, but able to run bare-metal on Spike and to summarise its
 * own output as a digest.
 *
 * Why not just run the reference binary: it needs printf and a hosted libc.
 * The output format here is character-for-character the same, so the text this
 * produces can be diffed against the official tvecs768 file, and the digest
 * mode -- which absorbs exactly that byte stream into SHAKE256 -- lets a
 * 10000-iteration run report 32 bytes instead of 46 MB.
 */
#include <stddef.h>
#include <stdint.h>

#include "params.h"
#include "kem.h"
#include "randombytes.h"
#include "fips202.h"
#include "htif.h"

#ifndef NTESTS
#define NTESTS 10
#endif

/* Deterministic randombytes, as in test/test_vectors.c: a SHAKE128 stream
 * seeded by absorbing the empty string.  Built from the public API rather than
 * copying the reference's literal state initialiser. */
static keccak_state rngstate;
static int rng_ready = 0;

void randombytes(uint8_t *x, size_t xlen)
{
  if (!rng_ready) {
    shake128_init(&rngstate);
    shake128_finalize(&rngstate);
    rng_ready = 1;
  }
  shake128_squeeze(x, xlen, &rngstate);
}

/* ------------------------------------------------------- output plumbing -- */
#ifdef KAT_DIGEST
static keccak_state digest_state;
static void out_init(void) { shake256_init(&digest_state); }
static void out(const char *s, size_t n)
{
  shake256_absorb(&digest_state, (const uint8_t *)s, n);
}
#else
static void out_init(void) { }
static void out(const char *s, size_t n) { htif_write(s, n); }
#endif

static void out_str(const char *s)
{
  size_t n = 0;
  while (s[n]) n++;
  out(s, n);
}

static const char hexdig[] = "0123456789abcdef";

static void out_hex(const uint8_t *b, size_t n)
{
  char buf[64];
  size_t k = 0;
  for (size_t i = 0; i < n; i++) {
    buf[k++] = hexdig[b[i] >> 4];
    buf[k++] = hexdig[b[i] & 0xF];
    if (k == sizeof(buf)) { out(buf, k); k = 0; }
  }
  if (k) out(buf, k);
}

static void field(const char *label, const uint8_t *b, size_t n)
{
  out_str(label);
  out_hex(b, n);
  out_str("\n");
}

/* --------------------------------------------------------------------------- */
static uint8_t pk[CRYPTO_PUBLICKEYBYTES];
static uint8_t sk[CRYPTO_SECRETKEYBYTES];
static uint8_t ct[CRYPTO_CIPHERTEXTBYTES];
static uint8_t key_a[CRYPTO_BYTES];
static uint8_t key_b[CRYPTO_BYTES];

int main(void)
{
  out_init();

  for (unsigned i = 0; i < NTESTS; i++) {
    crypto_kem_keypair(pk, sk);
    field("Public Key: ", pk, CRYPTO_PUBLICKEYBYTES);
    field("Secret Key: ", sk, CRYPTO_SECRETKEYBYTES);

    crypto_kem_enc(ct, key_b, pk);
    field("Ciphertext: ", ct, CRYPTO_CIPHERTEXTBYTES);
    field("Shared Secret B: ", key_b, CRYPTO_BYTES);

    crypto_kem_dec(key_a, ct, sk);
    field("Shared Secret A: ", key_a, CRYPTO_BYTES);

    /* The reference aborts if the two shared secrets differ; do the same, so a
     * wrong NTT cannot be mistaken for a merely-different KAT. */
    for (unsigned j = 0; j < CRYPTO_BYTES; j++)
      if (key_a[j] != key_b[j]) {
        out_str("ERROR: shared secrets differ\n");
        htif_flush();
        return 1;
      }

    /* Decapsulation of an invalid (random) ciphertext: implicit rejection. */
    randombytes(ct, CRYPTO_CIPHERTEXTBYTES);
    crypto_kem_dec(key_a, ct, sk);
    field("Pseudorandom shared Secret A: ", key_a, CRYPTO_BYTES);
  }

#ifdef KAT_DIGEST
  {
    uint8_t d[32];
    shake256_finalize(&digest_state);
    shake256_squeeze(d, sizeof(d), &digest_state);
    htif_write("DIGEST ", 7);
    /* out_hex writes to the digest in this build, so print directly. */
    for (unsigned i = 0; i < sizeof(d); i++) {
      char two[2] = { hexdig[d[i] >> 4], hexdig[d[i] & 0xF] };
      htif_write(two, 2);
    }
    htif_write("\n", 1);
  }
#endif
  htif_flush();
  return 0;
}
