/* Random and adversarial input into the parser, under the sanitizers.
 *
 * This code reads bytes an access point sent, so the interesting question is
 * not "does it decode a good frame" — the vectors answer that — but "what
 * does it do with a bad one". Every rejection path matters: the upstream code
 * had none, and installed whatever it found. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "net/aes128.h"

static uint32_t seed = 0x1d92164f;

static uint8_t rnd(void)
{
    seed = seed * 1103515245u + 12345u;
    return (uint8_t)(seed >> 16);
}

int main(void)
{
    uint8_t kek[16], kd[256], gtk[32], plain[256];
    uint32_t gtk_len;
    uint8_t idx;
    unsigned i, n, round;
    unsigned accepted = 0;

    /* 1. Random key data under a random KEK. The unwrap's integrity value is
          64 bits, so an accept here would be a one-in-18-quintillion fluke —
          or a bug. */
    for(round = 0; round < 200000; round++)
    {
        for(i = 0; i < 16; i++) kek[i] = rnd();
        n = 16 + 8 * (rnd() % 16);
        for(i = 0; i < n; i++) kd[i] = rnd();
        if(aes_key_unwrap(kek, kd, n, plain) == 1)
            accepted++;
    }
    printf("random key data: %u accepted out of 200000\n", accepted);
    if(accepted) { printf("FAIL\n"); return 1; }

    /* 2. Random *plaintext* straight into the KDE walker, which is where a
          length field can point past the buffer. Any outcome is fine except
          reading out of bounds — the sanitizers are the assertion here. */
    for(round = 0; round < 200000; round++)
    {
        n = 1 + (rnd() % sizeof(plain));
        for(i = 0; i < n; i++) plain[i] = rnd();
        /* Bias towards well-formed-looking elements, so the walker is
           actually exercised rather than bailing on the first byte. */
        if(round % 2) plain[0] = 0xDD;
        rsn_find_gtk(plain, n, gtk, sizeof(gtk), &gtk_len, &idx);
    }
    printf("random key data elements: no out-of-bounds access\n");

    /* 3. Every truncation of a frame that IS valid: a real AP's frame cut
          short by a bad radio is the realistic hostile input. */
    for(n = 0; n <= 64; n++)
    {
        memset(kd, 0xDD, sizeof(kd));
        if(n >= 16 && (n % 8) == 0)
            aes_key_unwrap(kek, kd, n, plain);
        rsn_find_gtk(kd, n, gtk, sizeof(gtk), &gtk_len, &idx);
    }
    printf("every truncation: no out-of-bounds access\n");

    /* 4. A KDE whose length byte is maximal, at every offset. */
    for(n = 8; n <= 128; n += 8)
    {
        for(i = 0; i + 2 <= n; i++)
        {
            memset(plain, 0, n);
            plain[i] = 0xDD;
            plain[i + 1] = 0xFF;
            rsn_find_gtk(plain, n, gtk, sizeof(gtk), &gtk_len, &idx);
        }
    }
    printf("maximal length fields: no out-of-bounds access\n");

    /* 5. The output buffer is never written past what the caller offered. */
    {
        uint8_t small[8];
        memset(plain, 0, sizeof(plain));
        plain[0] = 0xDD; plain[1] = 0x16; plain[2] = 0x00; plain[3] = 0x0F;
        plain[4] = 0xAC; plain[5] = 0x01;
        if(rsn_find_gtk(plain, 24, small, sizeof(small), &gtk_len, &idx) != 0)
        { printf("FAIL a 16-byte key fitted into 8 bytes\n"); return 1; }
        printf("a key larger than the buffer: refused\n");
    }

    printf("\ntout passe\n");
    return 0;
}
