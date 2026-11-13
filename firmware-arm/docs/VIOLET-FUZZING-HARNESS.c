/* Phase 4 — harnais ASan reproduisant fidèlement les boucles non bornées
 * trouvées en Phase 1 (ieee80211.c:ieee80211_input_mgt et
 * usbhcore.c:usbh_get_descriptor_all). Le code des boucles est recopié tel
 * quel du firmware d'origine ; seul l'environnement (tampons de taille exacte,
 * allouage sur le tas) est ajouté pour qu'ASan voie les débordements.
 *
 *   gcc -fsanitize=address,undefined -g -O0 harness.c -o harness
 *   ./harness <cas>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* --- constantes du firmware (inc/net/ieee80211.h) --- */
#define IEEE80211_ELEMID_SSID   0
#define IEEE80211_ELEMID_RATES  1
#define IEEE80211_ELEMID_RSN    48
#define IEEE80211_OUI_LEN       4
struct scan_result { char ssid[33]; uint16_t encryption; uint32_t rateset; };
static const uint8_t wpa2_oui[4] = {0x00,0x0f,0xac,0x04};

/* ====================================================================
 * A. Boucle d'analyse des IE de balise (ieee80211.c:1378-1495, extrait).
 *    frame = tampon de TAILLE EXACTE 'length' -> toute lecture au-delà
 *    de frame_end est hors de l'allocation et ASan la voit.
 * ==================================================================== */
static void ieee80211_ie_walk(uint8_t *frame, uint32_t length)
{
  struct scan_result scan_result;
  uint8_t *frame_current = frame;
  uint8_t *frame_end = frame + length;
  uint32_t i;
  scan_result.ssid[0] = 0;
  scan_result.rateset = 0;

  while (frame_current < frame_end) {       /* <-- ne garde que l'octet [0] */
    switch (frame_current[0]) {
      case IEEE80211_ELEMID_SSID:
        if (frame_current[1] < sizeof(scan_result.ssid)) {   /* borne la DEST */
          for (i = 0; i < frame_current[1]; i++)             /* pas la SOURCE */
            scan_result.ssid[i] = frame_current[i+2];        /* OOB read ici */
          scan_result.ssid[i] = 0;
        }
        break;
      case IEEE80211_ELEMID_RATES:
        for (i = 0; i < frame_current[1]; i++)
          scan_result.rateset |= frame_current[i+2];         /* OOB read ici */
        break;
      case IEEE80211_ELEMID_RSN: {
        uint8_t *current = &frame_current[2];
        uint16_t count = (current[0]) | (current[1] << 8);   /* compteur trame */
        current += 2;
        for (i = 0; i < count; i++) {                        /* NON BORNÉ */
          if (memcmp(current, wpa2_oui, IEEE80211_OUI_LEN-1) == 0)  /* OOB read */
            scan_result.encryption |= 1;
          current += IEEE80211_OUI_LEN;
        }
        break;
      }
      default: break;
    }
    frame_current += (frame_current[1] + 2);
  }
  printf("  [A] parcouru, ssid=\"%s\" rateset=0x%x\n", scan_result.ssid,
         scan_result.rateset);
}

/* ====================================================================
 * B. memcpy piloté par bLength (usbhcore.c:usbh_get_descriptor_all:371).
 *    destination = struct de taille fixe ; bLength vient du périphérique.
 * ==================================================================== */
struct usb_configuration_descriptor { /* ~9 octets fil + chaînage */
  uint8_t bLength, bDescriptorType; uint16_t wTotalLength;
  uint8_t bNumInterfaces, bConfigurationValue, iConfiguration, bmAttributes, bMaxPower;
  void *next; void *interface; void *otg;  /* champs de chaînage (comme le code) */
};
static void usb_descriptor_copy(uint8_t *ptr /* du périphérique */)
{
  uint8_t bLength = *ptr;
  struct usb_configuration_descriptor *cfg =
      malloc(sizeof(struct usb_configuration_descriptor));
  cfg->next = NULL; cfg->interface = NULL; cfg->otg = NULL;
  memcpy(cfg, ptr, bLength);     /* OVERFLOW si bLength > sizeof(*cfg) */
  printf("  [B] copié bLength=%u dans struct de %zu octets\n",
         bLength, sizeof(*cfg));
  free(cfg);
}

/* ---- corpus d'entrées malformées ---- */
static void case_ssid_truncated(void)
{
  /* IE SSID annonçant 40 octets mais la trame s'arrête après 4 */
  uint8_t tail[6] = { IEEE80211_ELEMID_SSID, 40, 'A','B','C','D' };
  uint8_t *f = malloc(6); memcpy(f, tail, 6);
  printf("[A1] SSID tronqué (len=40, 4 dispos)\n");
  ieee80211_ie_walk(f, 6);
  free(f);
}
static void case_rsn_huge_count(void)
{
  /* IE RSN : id=48, len=6 ; le compteur lu à frame_current[2..3] = 0x7fff */
  uint8_t tail[10] = { IEEE80211_ELEMID_RSN, 6, 0xff,0x7f, 0,0,0,0,0,0 };
  uint8_t *f = malloc(10); memcpy(f, tail, 10);
  printf("[A2] RSN count=0x7fff (boucle OUI non bornée)\n");
  ieee80211_ie_walk(f, 10);
  free(f);
}
static void case_rates_truncated(void)
{
  uint8_t tail[4] = { IEEE80211_ELEMID_RATES, 50, 0x82, 0x84 };
  uint8_t *f = malloc(4); memcpy(f, tail, 4);
  printf("[A3] RATES tronqué (len=50, 2 dispos)\n");
  ieee80211_ie_walk(f, 4);
  free(f);
}
static void case_usb_overflow(void)
{
  uint8_t desc[255]; memset(desc, 0xBB, sizeof desc);
  desc[0] = 255;  /* bLength malveillant */
  desc[1] = 2;    /* CONFIGURATION */
  printf("[B1] descripteur USB bLength=255 dans struct fixe\n");
  usb_descriptor_copy(desc);
}

int main(int argc, char **argv)
{
  const char *c = argc > 1 ? argv[1] : "all";
  if (!strcmp(c,"A1")||!strcmp(c,"all")) case_ssid_truncated();
  if (!strcmp(c,"A2")||!strcmp(c,"all")) case_rsn_huge_count();
  if (!strcmp(c,"A3")||!strcmp(c,"all")) case_rates_truncated();
  if (!strcmp(c,"B1")||!strcmp(c,"all")) case_usb_overflow();
  printf("fin (aucun ASan déclenché)\n");
  return 0;
}
