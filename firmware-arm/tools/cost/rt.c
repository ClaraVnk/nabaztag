/* Freestanding runtime for the cost harness: no libc, Linux user-mode
 * syscalls only (qemu-arm). The byte loops are the cost class of the
 * firmware's own memcpy/memset and they are inside every measurement. */
#include <stdint.h>
#include <stddef.h>

void *memcpy(void *d, const void *s, size_t n)
{
    unsigned char *q = d; const unsigned char *p = s;
    while (n--) *q++ = *p++;
    return d;
}
void *memmove(void *d, const void *s, size_t n)
{
    unsigned char *q = d; const unsigned char *p = s;
    if (q < p) { while (n--) *q++ = *p++; }
    else { q += n; p += n; while (n--) *--q = *--p; }
    return d;
}
void *memset(void *d, int c, size_t n)
{
    unsigned char *q = d;
    while (n--) *q++ = (unsigned char)c;
    return d;
}
int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *p = a, *q = b;
    while (n--) { if (*p != *q) return *p - *q; p++; q++; }
    return 0;
}
size_t strlen(const char *s) { size_t n = 0; while (s[n]) n++; return n; }

static void sys_exit(int code)
{
    register int r0 __asm__("r0") = code;
    register int r7 __asm__("r7") = 1;
    __asm__ volatile ("svc 0" : : "r"(r0), "r"(r7) : "memory");
    for (;;) ;
}
void sys_write(const void *p, unsigned n)
{
    register int r0 __asm__("r0") = 1;
    register const void *r1 __asm__("r1") = p;
    register unsigned r2 __asm__("r2") = n;
    register int r7 __asm__("r7") = 4;
    __asm__ volatile ("svc 0" : : "r"(r0), "r"(r1), "r"(r2), "r"(r7) : "memory");
}

/* TweetNaCl wants the symbol even for verify-only; never called here
 * (nab_sig.c defines it too, so only when nab_sig.c is not linked). */
#ifndef HAVE_NAB_SIG
void randombytes(unsigned char *p, unsigned long long n) { (void)p; (void)n; }
/* The watchdog feed the verify_wdt patch calls from TweetNaCl's loops;
 * CLR_WDT on the rabbit, a counted no-op here (count.py keys on it). */
void nab_verify_tick(void) { __asm__ volatile ("" ::: "memory"); }
#endif

int bench(void);
void _start(void) { sys_exit(bench() ? 0 : 1); }
