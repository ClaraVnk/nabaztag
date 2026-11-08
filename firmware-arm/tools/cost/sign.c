/* Host-side throwaway signer (never the real key). Builds against upstream
 * tweetnacl.c with gcc. Usage:
 *   sign keygen pk.bin sk.bin
 *   sign sign sk.bin msg.bin sig.bin      (64-byte detached signature) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tweetnacl.h"

void randombytes(unsigned char *p, unsigned long long n)
{
    FILE *f = fopen("/dev/urandom", "rb");
    if (!f || fread(p, 1, n, f) != n) { perror("urandom"); exit(2); }
    fclose(f);
}
static unsigned char *slurp(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb"); unsigned char *b;
    if (!f) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END); *len = (size_t)ftell(f); rewind(f);
    b = malloc(*len + 1); if (fread(b, 1, *len, f) != *len) exit(2);
    fclose(f); return b;
}
static void dump(const char *path, const void *p, size_t n)
{
    FILE *f = fopen(path, "wb"); if (!f || fwrite(p, 1, n, f) != n) { perror(path); exit(2); } fclose(f);
}
int main(int argc, char **argv)
{
    if (argc == 4 && !strcmp(argv[1], "keygen")) {
        unsigned char pk[32], sk[64];
        crypto_sign_keypair(pk, sk);
        dump(argv[2], pk, 32); dump(argv[3], sk, 64);
        return 0;
    }
    if (argc == 5 && !strcmp(argv[1], "sign")) {
        size_t skl, ml; unsigned char *sk = slurp(argv[2], &skl), *m = slurp(argv[3], &ml);
        unsigned char *sm = malloc(ml + 64); unsigned long long sml;
        if (skl != 64) return 2;
        crypto_sign(sm, &sml, m, ml, sk);
        dump(argv[4], sm, 64);
        return 0;
    }
    fprintf(stderr, "usage\n"); return 1;
}
