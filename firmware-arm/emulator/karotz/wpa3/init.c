/*
 * The proof inside the guest, as its init: an access point on wlan0
 * (hostapd), a station on wlan1 (wpa_supplicant), both on
 * mac80211_hwsim's radios; then whether the station connected, the log
 * lines that say how, and power off. A program and not a busybox script:
 * busybox does not link with zig for ARMv5 (2026-11-17).
 */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

static void run(char *const argv[])
{
    pid_t p = fork();
    if (p == 0) {
        execv(argv[0], argv);
        _exit(127);
    }
    waitpid(p, NULL, 0);    /* -B: each daemonizes, the parent returns */
}

static int has(const char *path, const char *what)
{
    char line[1024];
    FILE *f = fopen(path, "r");
    int found = 0;
    if (!f)
        return 0;
    while (!found && fgets(line, sizeof line, f))
        found = strstr(line, what) != NULL;
    fclose(f);
    return found;
}

/* the lines of a log that say how SAE, the association and the keys went */
/* the last lines of a log, when it did not connect */
static void tail(const char *title, const char *path, int n)
{
    static char lines[64][512];
    char line[512];
    int count = 0;
    FILE *f = fopen(path, "r");
    printf("== %s, its last lines\n", title);
    if (!f) {
        printf("(no %s)\n", path);
        return;
    }
    while (fgets(line, sizeof line, f))
        if (!strstr(line, "hexdump"))
            strcpy(lines[count++ % 64], line);
    fclose(f);
    for (int i = count > n ? count - n : 0; i < count; i++)
        fputs(lines[i % 64], stdout);
}

static void show(const char *title, const char *path, const char *const keys[], int max)
{
    char line[1024];
    FILE *f = fopen(path, "r");
    int n = 0;
    printf("== %s\n", title);
    if (!f)
        return;
    while (n < max && fgets(line, sizeof line, f)) {
        if (strstr(line, "hexdump") || strstr(line, "counter =") || strstr(line, "pwd-seed"))
            continue;       /* SAE's hunting-and-pecking rounds, 40 lines */
        for (int i = 0; keys[i]; i++)
            if (strstr(line, keys[i])) {
                fputs(line, stdout);
                n++;
                break;
            }
    }
    fclose(f);
}

int main(void)
{
    struct utsname u;
    mount("proc", "/proc", "proc", 0, NULL);
    mount("sys", "/sys", "sysfs", 0, NULL);
    mount("tmp", "/tmp", "tmpfs", 0, NULL);
    mount("dev", "/dev", "devtmpfs", 0, NULL);   /* /dev/urandom for OpenSSL */
    /* the initramfs has no /dev/console (no mknod in a rootless build):
     * the output goes to devtmpfs's */
    int c = open("/dev/console", O_RDWR);
    if (c >= 0) {
        dup2(c, 0); dup2(c, 1); dup2(c, 2);
    }
    uname(&u);
    printf("WPA3: kernel %s\n", u.release);
    fflush(stdout);

    char *ap[] = {"/sbin/hostapd", "-B", "-f", "/tmp/hostapd.log", "-d", "/etc/hostapd.conf", NULL};
    run(ap);
    sleep(3);
    char *sta[] = {"/sbin/wpa_supplicant", "-B", "-i", "wlan1", "-D", "nl80211",
                   "-c", "/etc/wpa_supplicant.conf", "-f", "/tmp/wpa.log", "-d", NULL};
    run(sta);

    int s = 0;
    while (s < 45 && !has("/tmp/wpa.log", "CTRL-EVENT-CONNECTED")) {
        sleep(1);
        s++;
    }
    const char *const sk[] = {"KEY_MGMT", "SAE group", "SAE: State", "MFP", "MGMT group",
                              "CTRL-EVENT-CONNECTED", "Key negotiation", "IGTK",
                              "nl80211: Authenticate", "nl80211: Associate",
                              "not supported", "Unsupported", "Could not", "failed", NULL};
    const char *const ak[] = {"SAE: State", "AP-STA", "associated",
                              "pairwise key", "EAPOL-4WAY", NULL};
    show("station", "/tmp/wpa.log", sk, 45);
    show("access point", "/tmp/hostapd.log", ak, 20);
    if (!has("/tmp/wpa.log", "CTRL-EVENT-CONNECTED")) {
        tail("station", "/tmp/wpa.log", 25);
        tail("access point", "/tmp/hostapd.log", 25);
    }
    printf("WPA3-RESULT: %s after %ds\n",
           has("/tmp/wpa.log", "CTRL-EVENT-CONNECTED") ? "connected" : "not connected", s);
    fflush(stdout);
    sync();
    reboot(RB_POWER_OFF);
    return 0;
}
