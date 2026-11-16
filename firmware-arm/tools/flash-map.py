#!/usr/bin/env python3
"""Where the flash goes: an ELF's flash symbols, grouped by what they do.

    python3 tools/flash-map.py bin/<mode>/Nab.elf [bin/<mode>/Nab.bin]

Needs arm-none-eabi-nm. The groups are name patterns, so a few symbols land
in the wrong one; "other" lists the biggest unclassified ones to check. With
Nab.bin it also counts the text (config pages: HTML, CSS, JS) inside the
embedded bytecode, `dumpbc`.
"""
import re
import subprocess
import sys

FLASH = (0x08000000, 0x08020000)
GROUPS = [
    ("embedded bytecode (dumpbc)", r"^dumpbc$"),
    ("Metal VM", r"^(interp|vm|sys|bc_|op_|exec|tab_|loader|mem|gc|vmem)"),
    ("TLS, X.509, RSA, ECDSA, P-256", r"(tls|x509|rsa|ecdsa|p256|nab_bn|asn1|cert|hs_|aead|gcm|ghash|hkdf|sha256|poly|chacha|^K$|^add$|^block$|jac_|^dbl$|^P384$|fe_mul)"),
    ("Ed25519, X25519, SHA-512 (TweetNaCl)", r"(tweet|25519|sha512|^modL|^reduce|^car25519|^sel25519|^inv25519|^pow2523|^gf|^vn|^crypto_|nab_kx|verify)"),
    ("WPA2/WPA3: EAPOL, SAE, AES, SHA-1", r"(eapol|sae|wpa|rsn|aes|cmac|SHA|sha1|hmac|prf|pbkdf|kdf|h2e|sswu|wrap|ccmp|commit_from_pwe)"),
    ("Wi-Fi: rt2501, 802.11", r"(rt2501|ieee80211|80211|scan|assoc|beacon|probe|RegTable)"),
    ("USB host", r"(usb|hcd|ohci|urb)"),
    ("IP, DHCP, DNS, TCP, HTTP, mDNS", r"(ip_|tcp|udp|dhcp|dns|mdns|arp|http|net|eth|icmp|checksum)"),
    ("audio, LEDs, motors, RFID", r"(audio|vlsi|vs10|play|rec|mic|led|motor|ear|rfid|cr14|i2c|adc|pwm|sound|ADPCM|intensity)"),
]


def main():
    elf = sys.argv[1]
    nm = subprocess.run(["arm-none-eabi-nm", "-S", elf], capture_output=True, text=True, check=True)
    tot = {g: 0 for g, _ in GROUPS}
    tot["other"] = 0
    other = []
    dumpbc = None
    for line in nm.stdout.splitlines():
        p = line.split()
        if len(p) < 4:
            continue
        addr, size, name = int(p[0], 16), int(p[1], 16), p[3]
        if not FLASH[0] <= addr < FLASH[1] or name == "_etext":
            continue
        if name == "dumpbc":
            dumpbc = (addr, size)
        for g, rx in GROUPS:
            if re.search(rx, name, re.I):
                tot[g] += size
                break
        else:
            tot["other"] += size
            other.append((size, name))
    for g, v in sorted(tot.items(), key=lambda x: -x[1]):
        print(f"{v:7}  {g}")
    print(f"{sum(tot.values()):7}  named symbols in flash")
    other.sort(reverse=True)
    print("other, biggest:", ", ".join(f"{n} {s}" for s, n in other[:15]))
    if len(sys.argv) > 2 and dumpbc:
        image = open(sys.argv[2], "rb").read()
        bc = image[dumpbc[0] - FLASH[0]:dumpbc[0] - FLASH[0] + dumpbc[1]]
        texts = re.findall(rb"[\t\n\r -~]{12,}", bc)
        pages = sum(len(t) for t in texts if re.search(rb"[<{;=]", t))
        print(f"dumpbc {len(bc)} bytes: {sum(len(t) for t in texts)} of text, ~{pages} of it pages (HTML, CSS, JS)")


if __name__ == "__main__":
    main()
