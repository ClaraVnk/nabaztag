/* The WPA2 group-key path, end to end, on a build machine.
 *
 * vectors.h is generated from the specifications — IEEE 802.11's PRF-384 for
 * the KEK, RFC 3394 for the wrap, both via python-cryptography — so it knows
 * nothing about this implementation and is a fair check on it. These same
 * vectors were also run against an independently written implementation and
 * produced byte-identical results, which is why the leaner upstream one is
 * the one vendored. */
#include <stdio.h>
#include <string.h>
#include "net/aes128.h"
#include "vectors.h"

static int fails = 0;
static void ok_(int c, const char *w)
{ printf("  %s %s\n", c ? "ok  " : "FAIL", w); if(!c) fails++; }

int main(void)
{
    uint8_t plain[128], gtk[32];
    uint32_t gtk_len = 0;
    uint8_t key_id = 0;

    printf("the WPA2 group-key path, against generated vectors\n\n");

    printf("a real WPA2 message 3, 128-bit group key:\n");
    ok_(aes_key_unwrap(V_KEK, V_KEY_DATA, sizeof(V_KEY_DATA), plain) == 1,
        "unwrap accepted");
    ok_(memcmp(plain, V_PLAIN, sizeof(V_PLAIN)) == 0, "key data matches");
    ok_(rsn_find_gtk(plain, sizeof(V_KEY_DATA) - 8, gtk, sizeof(gtk),
                     &gtk_len, &key_id) == 1, "GTK KDE found");
    ok_(gtk_len == sizeof(V_GTK), "key length");
    ok_(key_id == V_KEY_INDEX, "key index");
    ok_(memcmp(gtk, V_GTK, sizeof(V_GTK)) == 0, "the group key itself");

    printf("a 256-bit key behind another KDE:\n");
    memset(gtk, 0, sizeof(gtk));
    ok_(aes_key_unwrap(V_KEK, V2_KEY_DATA, sizeof(V2_KEY_DATA), plain) == 1,
        "unwrap accepted");
    ok_(rsn_find_gtk(plain, sizeof(V2_KEY_DATA) - 8, gtk, sizeof(gtk),
                     &gtk_len, &key_id) == 1, "GTK KDE found");
    ok_(gtk_len == sizeof(V2_GTK), "key length");
    ok_(key_id == V2_KEY_INDEX, "key index");
    ok_(memcmp(gtk, V2_GTK, sizeof(V2_GTK)) == 0, "walked past the other KDE");

    printf("a wrong KEK is refused:\n");
    {
        uint8_t bad[16];
        memcpy(bad, V_KEK, 16); bad[7] ^= 1;
        ok_(aes_key_unwrap(bad, V_KEY_DATA, sizeof(V_KEY_DATA), plain) == 0,
            "refused");
    }

    printf("a tampered frame is refused:\n");
    {
        uint8_t kd[sizeof(V_KEY_DATA)];
        memcpy(kd, V_KEY_DATA, sizeof(kd)); kd[11] ^= 0x80;
        ok_(aes_key_unwrap(V_KEK, kd, sizeof(kd), plain) == 0, "refused");
    }

    printf("\n%s\n", fails ? "ECHEC" : "toute la chaine passe");
    return fails != 0;
}
