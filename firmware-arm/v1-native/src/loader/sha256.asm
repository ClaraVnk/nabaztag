;; sha256.asm -- SHA-256 (FIPS 180-4) and HMAC-SHA-256 (RFC 2104) for the
;; PIC18, called from SDCC's C with the C API of sha256.h. Written for
;; size: the C version of 2026-11-24 was 4.4 KB with SDCC's pic16 port
;; (eight instructions per 32-bit add, every temporary of an expression in
;; banked RAM, a 314-byte multiply for an indexed 32-bit array); this file
;; is 1.4 KB with the two tables. Measured, not estimated: `make size`.
;;
;; How it is small:
;; - every working variable lives in the access bank (udata_acs): no BANKSEL
;;   anywhere in the round; the state and the block are reached through FSR0
;;   and FSR2 only. BSR and FSR2 are saved and restored, since SDCC keeps
;;   its frame pointer in FSR2 (FSR1 is its stack pointer, never touched;
;;   FSR0, PRODL/H, TBLPTR and TABLAT are scratch for the pic16 port).
;; - words are little-endian in RAM, as SDCC's uint32_t: an add walks the
;;   bytes with POSTINC, so one routine adds any word to any word; the
;;   message bytes are stored at blen ^ 3 to make the block its 16 words
;;   and the schedule works in that ring in place (W[t] over W[t-16]).
;; - a rotation is byte moves then single-bit rotates through the carry,
;;   right or left, whichever is shorter; the three terms of each sigma are
;;   deltas on one rotating copy (X), XORed into Y.
;; - the round variables a..h are moved down by one word at the end of the
;;   round (28 byte moves) rather than renamed: the round's code addresses
;;   them directly.
;;
;; The C API is hmac_init(key, klen), hmac_update(p, n), hmac_final(out),
;; with SDCC's pic16 convention: the arguments on its software stack, the
;; first at FSR2 + 2 once the callee has made FSR2 its frame (the prologue
;; SDCC itself emits, ENTER/LEAVE below). The hash underneath: sha_init,
;; sha_byte (the byte in WREG; it counts nothing, feed adds to sha_total),
;; sha_final; the state sha_h is eight little-endian words. CLRWDT once per
;; block: Lazare's watchdog is ~512 ms, a block 12 ms (DESIGN.md 7.5).
;; Own code from the standard's text; nothing taken from another project.

        radix   dec
        #include <p18cxxx.inc>

        global  _hmac_init, _hmac_update, _hmac_final
        global  _sha_init, _sha_byte, _sha_final, _sha_h, _sha_total

;; ---------------------------------------------------------------- RAM ---
SHA_ACS udata_acs                       ; 59 bytes of the access bank
_sha_total res 4                        ; bytes hashed, little-endian: sha_update adds to it
_sha_blen res 1                         ; bytes in the block
v       res 32                          ; the round variables a..h, 4 bytes each
x       res 4                           ; the rotating word
y       res 4                           ; a sigma, Ch or Maj
t       res 4                           ; T1
i       res 1                           ; the round
n       res 1                           ; a loop count
sav     res 3                           ; BSR, FSR2L, FSR2H around compress
cnt     res 2                           ; bytes left to feed
ptr     res 2                           ; the caller's out pointer
f2h     res 1                           ; FSR2H around an HMAC call

SHA_RAM udata                           ; anywhere in RAM, reached through an FSR
_sha_h  res 32                          ; the hash, eight little-endian words
blk     res 64                          ; the block: sixteen little-endian words, the schedule's ring
key     res 64                          ; the HMAC key, zero-padded and xored with the pad

;; a..h by name. Text macros, not EQU: gpasm's EQU of a relocatable label
;; keeps the offset in the section and loses the relocation (2026-11-25:
;; every LFSR on them pointed 0x0B too low in the test image).
#define VA (v+0)
#define VB (v+4)
#define VC (v+8)
#define VD (v+12)
#define VE (v+16)
#define VF (v+20)
#define VG (v+24)
#define VH (v+28)

;; ------------------------------------------------------------ tables ---
SHA_TAB code
;; H0, little-endian words: copied byte by byte into _sha_h.
h0:     db 0x67,0xe6,0x09,0x6a, 0x85,0xae,0x67,0xbb, 0x72,0xf3,0x6e,0x3c, 0x3a,0xf5,0x4f,0xa5
        db 0x7f,0x52,0x0e,0x51, 0x8c,0x68,0x05,0x9b, 0xab,0xd9,0x83,0x1f, 0x19,0xcd,0xe0,0x5b
;; K, little-endian words: added byte by byte with TBLRD*+.
k:      db 0x98,0x2f,0x8a,0x42, 0x91,0x44,0x37,0x71, 0xcf,0xfb,0xc0,0xb5, 0xa5,0xdb,0xb5,0xe9
        db 0x5b,0xc2,0x56,0x39, 0xf1,0x11,0xf1,0x59, 0xa4,0x82,0x3f,0x92, 0xd5,0x5e,0x1c,0xab
        db 0x98,0xaa,0x07,0xd8, 0x01,0x5b,0x83,0x12, 0xbe,0x85,0x31,0x24, 0xc3,0x7d,0x0c,0x55
        db 0x74,0x5d,0xbe,0x72, 0xfe,0xb1,0xde,0x80, 0xa7,0x06,0xdc,0x9b, 0x74,0xf1,0x9b,0xc1
        db 0xc1,0x69,0x9b,0xe4, 0x86,0x47,0xbe,0xef, 0xc6,0x9d,0xc1,0x0f, 0xcc,0xa1,0x0c,0x24
        db 0x6f,0x2c,0xe9,0x2d, 0xaa,0x84,0x74,0x4a, 0xdc,0xa9,0xb0,0x5c, 0xda,0x88,0xf9,0x76
        db 0x52,0x51,0x3e,0x98, 0x6d,0xc6,0x31,0xa8, 0xc8,0x27,0x03,0xb0, 0xc7,0x7f,0x59,0xbf
        db 0xf3,0x0b,0xe0,0xc6, 0x47,0x91,0xa7,0xd5, 0x51,0x63,0xca,0x06, 0x67,0x29,0x29,0x14
        db 0x85,0x0a,0xb7,0x27, 0x38,0x21,0x1b,0x2e, 0xfc,0x6d,0x2c,0x4d, 0x13,0x0d,0x38,0x53
        db 0x54,0x73,0x0a,0x65, 0xbb,0x0a,0x6a,0x76, 0x2e,0xc9,0xc2,0x81, 0x85,0x2c,0x72,0x92
        db 0xa1,0xe8,0xbf,0xa2, 0x4b,0x66,0x1a,0xa8, 0x70,0x8b,0x4b,0xc2, 0xa3,0x51,0x6c,0xc7
        db 0x19,0xe8,0x92,0xd1, 0x24,0x06,0x99,0xd6, 0x85,0x35,0x0e,0xf4, 0x70,0xa0,0x6a,0x10
        db 0x16,0xc1,0xa4,0x19, 0x08,0x6c,0x37,0x1e, 0x4c,0x77,0x48,0x27, 0xb5,0xbc,0xb0,0x34
        db 0xb3,0x0c,0x1c,0x39, 0x4a,0xaa,0xd8,0x4e, 0x4f,0xca,0x9c,0x5b, 0xf3,0x6f,0x2e,0x68
        db 0xee,0x82,0x8f,0x74, 0x6f,0x63,0xa5,0x78, 0x14,0x78,0xc8,0x84, 0x08,0x02,0xc7,0x8c
        db 0xfa,0xff,0xbe,0x90, 0xeb,0x6c,0x50,0xa4, 0xf7,0xa3,0xf9,0xbe, 0xf2,0x78,0x71,0xc6

;; -------------------------------------------------------------- code ---
SHA_CODE code

;; SDCC's prologue and epilogue for a function with arguments on its stack:
;; the caller's FSR2L saved on it, FSR2 made the frame; the arguments are
;; at FSR2 + 2, + 3, ... (little-endian), the caller removes them. FSR2H is
;; ours to restore (f2h) once we use FSR2 as a pointer.
ENTER   macro
        movff   FSR2L, POSTDEC1
        movff   FSR1L, FSR2L
        movff   FSR2H, f2h
        endm
LEAVE   macro
        movff   f2h, FSR2H
        movff   PREINC1, FSR2L
        return
        endm

;; TBLPTR = a code address.
TBLPTR_SET macro addr
        movlw   low addr
        movwf   TBLPTRL
        movlw   high addr
        movwf   TBLPTRH
        movlw   upper addr
        movwf   TBLPTRU
        endm

;; --- sha_init: h = H0, no bytes yet ---------------------------------------
_sha_init:
        TBLPTR_SET h0
        lfsr    0, _sha_h
        movlw   32
        movwf   n, A
init_l: tblrd*+
        movff   TABLAT, POSTINC0
        decfsz  n, F, A
        bra     init_l
        clrf    _sha_blen, A
        clrf    _sha_total, A
        clrf    _sha_total+1, A
        clrf    _sha_total+2, A
        clrf    _sha_total+3, A
        return

;; --- sha_byte: one message byte in WREG; the block compressed when full ---
_sha_byte:
        movwf   PRODL                   ; the byte, while W computes the place
        lfsr    0, blk
        movf    _sha_blen, W, A
        xorlw   3                       ; blk[blen ^ 3]: the word little-endian
        addwf   FSR0L, F, A
        movlw   0
        addwfc  FSR0H, F, A
        movff   PRODL, INDF0
        incf    _sha_blen, F, A
        btfss   _sha_blen, 6, A         ; 64?
        return
        clrf    _sha_blen, A
        ;; falls into compress

;; --- compress: h += f(h, blk) ---------------------------------------------
compress:
        movff   BSR, sav
        movff   FSR2L, sav+1
        movff   FSR2H, sav+2
        lfsr    0, _sha_h               ; a..h = h
        lfsr    2, v
        movlw   32
        rcall   copy
        TBLPTR_SET k
        clrf    i, A
round:
        ;; T = W[j], after W[j] += sigma0(W[j+1]) + sigma1(W[j+14]) + W[j+9] from round 16
        movlw   0
        rcall   wptr
        movff   POSTINC0, t
        movff   POSTINC0, t+1
        movff   POSTINC0, t+2
        movff   POSTINC0, t+3
        movf    i, W, A
        andlw   0x30
        bz      sched_x
        movlw   1
        rcall   wptr
        rcall   ld_x
        rcall   sigma0_s
        rcall   add_t_y
        movlw   14
        rcall   wptr
        rcall   ld_x
        rcall   sigma1_s
        rcall   add_t_y
        movlw   9
        rcall   wptr
        lfsr    2, t
        rcall   add_w
        movlw   0
        rcall   wptr                    ; W[j] = T
        movff   t, POSTINC0
        movff   t+1, POSTINC0
        movff   t+2, POSTINC0
        movff   t+3, POSTINC0
sched_x:
        lfsr    0, VH                   ; T += h
        lfsr    2, t
        rcall   add_w
        rcall   add_t_k                 ; T += K[i]
        lfsr    0, VE                   ; T += Sigma1(e)
        rcall   ld_x
        rcall   sigma1_b
        rcall   add_t_y
        rcall   ch                      ; T += Ch(e, f, g)
        rcall   add_t_y
        lfsr    0, t                    ; d += T: the next e
        lfsr    2, VD
        rcall   add_w
        lfsr    0, VA                   ; T += Sigma0(a)
        rcall   ld_x
        rcall   sigma0_b
        rcall   add_t_y
        rcall   maj                     ; T += Maj(a, b, c)
        rcall   add_t_y
        lfsr    0, VG+3                 ; h = g, g = f, ... b = a: 28 bytes, downwards
        lfsr    2, VH+3
        movlw   28
        movwf   n, A
shift_l:
        movf    POSTDEC0, W
        movwf   POSTDEC2
        decfsz  n, F, A
        bra     shift_l
        movff   t, VA                   ; a = T
        movff   t+1, VA+1
        movff   t+2, VA+2
        movff   t+3, VA+3
        incf    i, F, A
        movlw   64
        cpfseq  i, A
        bra     round
        lfsr    0, v                    ; h += a..h
        lfsr    2, _sha_h
        movlw   8
        movwf   n, A
fin_l:  rcall   add_w
        decfsz  n, F, A
        bra     fin_l
        clrwdt
        movff   sav, BSR
        movff   sav+1, FSR2L
        movff   sav+2, FSR2H
        return

;; --- sha_final: the padding and the length, the last block compressed -----
_sha_final:
        movlw   0x80
        rcall   _sha_byte
pad_l:  movlw   56
        cpfseq  _sha_blen, A
        bra     pad_0
        bra     pad_x
pad_0:  movlw   0
        rcall   _sha_byte
        bra     pad_l
pad_x:  movff   _sha_total, x           ; the length in bits: total << 3, 35 bits in y:x
        movff   _sha_total+1, x+1
        movff   _sha_total+2, x+2
        movff   _sha_total+3, x+3
        clrf    y, A
        movlw   3
        movwf   n, A
len_l:  bcf     STATUS, C
        rlcf    x, F, A
        rlcf    x+1, F, A
        rlcf    x+2, F, A
        rlcf    x+3, F, A
        rlcf    y, F, A
        decfsz  n, F, A
        bra     len_l
        movlw   0                       ; big-endian, 8 bytes; the last one compresses
        rcall   _sha_byte
        movlw   0
        rcall   _sha_byte
        movlw   0
        rcall   _sha_byte
        movf    y, W, A
        rcall   _sha_byte
        movf    x+3, W, A
        rcall   _sha_byte
        movf    x+2, W, A
        rcall   _sha_byte
        movf    x+1, W, A
        rcall   _sha_byte
        movf    x, W, A
        bra     _sha_byte

;; --- hmac_init(k, klen): key = k padded to 64 bytes ^ 0x36, hashed; then ^ 0x5C
_hmac_init:
        ENTER
        movlw   2
        movff   PLUSW2, FSR0L
        movlw   3
        movff   PLUSW2, FSR0H           ; FSR0 = k
        movlw   4
        movf    PLUSW2, W
        movwf   i, A                    ; klen
        lfsr    2, key
        movlw   64
        movwf   n, A
ki_l:   movlw   0
        tstfsz  i, A
        bra     ki_k
        bra     ki_s
ki_k:   decf    i, F, A
        movf    POSTINC0, W
ki_s:   xorlw   0x36
        movwf   POSTINC2
        decfsz  n, F, A
        bra     ki_l
        rcall   _sha_init
        rcall   feed_key
        lfsr    0, key                  ; ready for the outer pass: 0x36 ^ 0x5C
        movlw   64
        movwf   n, A
ko_l:   movlw   0x6A
        xorwf   POSTINC0, F
        decfsz  n, F, A
        bra     ko_l
        LEAVE

;; --- hmac_update(p, n) ------------------------------------------------------
_hmac_update:
        ENTER
        movlw   4
        movff   PLUSW2, cnt
        movlw   5
        movff   PLUSW2, cnt+1
        movlw   2
        movff   PLUSW2, ptr
        movlw   3
        movff   PLUSW2, ptr+1
        movff   ptr, FSR2L
        movff   ptr+1, FSR2H
        rcall   feed
        LEAVE

;; --- hmac_final(out): the inner digest, then the outer pass over it; the key forgotten
_hmac_final:
        ENTER
        movlw   2
        movff   PLUSW2, ptr
        movlw   3
        movff   PLUSW2, ptr+1
        rcall   _sha_final
        rcall   digest
        rcall   _sha_init
        rcall   feed_key
        movff   ptr, FSR2L
        movff   ptr+1, FSR2H
        movlw   32
        movwf   cnt, A
        clrf    cnt+1, A
        rcall   feed
        rcall   _sha_final
        rcall   digest
        lfsr    0, key
        movlw   64
        movwf   n, A
kz_l:   clrf    POSTINC0
        decfsz  n, F, A
        bra     kz_l
        LEAVE

  ifdef SHA_TEST
;; --- sha_digest(out): the plain hash's digest, for shatest.c --------------
        global  _sha_digest
_sha_digest:
        ENTER
        movlw   2
        movff   PLUSW2, ptr
        movlw   3
        movff   PLUSW2, ptr+1
        rcall   _sha_final
        rcall   digest
        LEAVE
  endif

;; feed: cnt bytes from FSR2 into the hash, counted in sha_total.
feed_key:
        lfsr    2, key
        movlw   64
        movwf   cnt, A
        clrf    cnt+1, A
feed:   movf    cnt, W, A
        addwf   _sha_total, F, A
        movf    cnt+1, W, A
        addwfc  _sha_total+1, F, A
        movlw   0
        addwfc  _sha_total+2, F, A
        addwfc  _sha_total+3, F, A
feed_l: movf    cnt, W, A
        iorwf   cnt+1, W, A
        bz      feed_x
        movf    POSTINC2, W
        rcall   _sha_byte
        decf    cnt, F, A
        btfss   STATUS, C
        decf    cnt+1, F, A
        bra     feed_l
feed_x: return

;; digest: the hash's 32 bytes to ptr, each word's bytes big-endian.
digest: movff   ptr, FSR2L
        movff   ptr+1, FSR2H
        lfsr    0, _sha_h
        movlw   8
        movwf   n, A
dg_l:   movlw   3
        movf    PLUSW0, W
        movwf   POSTINC2
        movlw   2
        movf    PLUSW0, W
        movwf   POSTINC2
        movlw   1
        movf    PLUSW0, W
        movwf   POSTINC2
        movf    POSTINC0, W
        movwf   POSTINC2
        movlw   3
        addwf   FSR0L, F
        movlw   0
        addwfc  FSR0H, F
        decfsz  n, F, A
        bra     dg_l
        return

;; --- helpers ----------------------------------------------------------------
;; W bytes from FSR0 to FSR2, upwards.
copy:   movwf   n, A
copy_l: movff   POSTINC0, POSTINC2
        decfsz  n, F, A
        bra     copy_l
        return

;; FSR0 = &blk[(i + W) & 15]: the schedule's ring.
wptr:   addwf   i, W, A
        andlw   15
        mullw   4
        lfsr    0, blk
        movf    PRODL, W
        addwf   FSR0L, F
        movlw   0
        addwfc  FSR0H, F
        return

;; X = the word at FSR0 (FSR0 moves past it).
ld_x:   movff   POSTINC0, x
        movff   POSTINC0, x+1
        movff   POSTINC0, x+2
        movff   POSTINC0, x+3
        return

;; The word at FSR2 += the word at FSR0; both move past their word.
add_w:  movf    POSTINC0, W
        addwf   POSTINC2, F
        movf    POSTINC0, W
        addwfc  POSTINC2, F
        movf    POSTINC0, W
        addwfc  POSTINC2, F
        movf    POSTINC0, W
        addwfc  POSTINC2, F
        return

;; T += Y.
add_t_y:
        movf    y, W, A
        addwf   t, F, A
        movf    y+1, W, A
        addwfc  t+1, F, A
        movf    y+2, W, A
        addwfc  t+2, F, A
        movf    y+3, W, A
        addwfc  t+3, F, A
        return

;; T += the word at TBLPTR (K[i]); TBLPTR moves past it.
add_t_k:
        tblrd*+
        movf    TABLAT, W
        addwf   t, F, A
        tblrd*+
        movf    TABLAT, W
        addwfc  t+1, F, A
        tblrd*+
        movf    TABLAT, W
        addwfc  t+2, F, A
        tblrd*+
        movf    TABLAT, W
        addwfc  t+3, F, A
        return

;; Y ^= X.   Y = X is four CLRF then this.
xor_y:  movf    x, W, A
        xorwf   y, F, A
        movf    x+1, W, A
        xorwf   y+1, F, A
        movf    x+2, W, A
        xorwf   y+2, F, A
        movf    x+3, W, A
        xorwf   y+3, F, A
        return
cp_y:   clrf    y, A
        clrf    y+1, A
        clrf    y+2, A
        clrf    y+3, A
        bra     xor_y

;; X rotated right by one byte.
ror8:   movf    x, W, A
        movff   x+1, x
        movff   x+2, x+1
        movff   x+3, x+2
        movwf   x+3, A
        return

;; X rotated right by W (0..31): whole bytes, then the remaining 0..7 bits
;; the shorter way round (5..7 right = one more byte, then 3..1 left). A
;; bit step is the carry chain through the four bytes, 8 cycles in the loop.
rorn:   movwf   n, A
rorn_b: movlw   8
        subwf   n, W, A
        bnc     rorn_r                  ; fewer than 8 left
        movwf   n, A
        rcall   ror8
        bra     rorn_b
rorn_r: movlw   5
        cpfslt  n, A                    ; n < 5: bit by bit to the right
        bra     rorn_l
        movf    n, F, A
        bz      rorn_x
rorn_1: rrcf    x, W, A                 ; bit 0 of the low byte into the carry
        rrcf    x+3, F, A
        rrcf    x+2, F, A
        rrcf    x+1, F, A
        rrcf    x, F, A
        decfsz  n, F, A
        bra     rorn_1
        return
rorn_l: rcall   ror8                    ; one byte too far, then 8 - n to the left
        movlw   8
        subwf   n, F, A
        negf    n, A
rorn_2: rlcf    x+3, W, A               ; bit 31 into the carry
        rlcf    x, F, A
        rlcf    x+1, F, A
        rlcf    x+2, F, A
        rlcf    x+3, F, A
        decfsz  n, F, A
        bra     rorn_2
rorn_x: return

;; The four sigmas of the standard, X in, Y out; X is rotated in place, so
;; each term is a delta on the previous one.
sigma1_b:                               ; ror 6 ^ ror 11 ^ ror 25
        movlw   6
        rcall   rorn
        rcall   cp_y
        movlw   5
        rcall   rorn
        rcall   xor_y
        movlw   14
        rcall   rorn
        bra     xor_y
sigma0_b:                               ; ror 2 ^ ror 13 ^ ror 22
        movlw   2
        rcall   rorn
        rcall   cp_y
        movlw   11
        rcall   rorn
        rcall   xor_y
        movlw   9
        rcall   rorn
        bra     xor_y
sigma0_s:                               ; ror 7 ^ ror 18 ^ shr 3: shr 3 is ror 3 with the top 3 bits cleared
        movlw   3
        rcall   rorn
        rcall   cp_y
        movlw   0x1F
        andwf   y+3, F, A
        movlw   4
        rcall   rorn
        rcall   xor_y
        movlw   11
        rcall   rorn
        bra     xor_y
sigma1_s:                               ; ror 17 ^ ror 19 ^ shr 10: shr 10 is ror 10 with the top 10 bits cleared
        movlw   10
        rcall   rorn
        rcall   cp_y
        clrf    y+3, A
        movlw   0x3F
        andwf   y+2, F, A
        movlw   7
        rcall   rorn
        rcall   xor_y
        movlw   2
        rcall   rorn
        bra     xor_y

;; Y = Ch(e, f, g) = g ^ (e & (f ^ g)), byte by byte.
CH_B    macro   o
        movf    VF+o, W, A
        xorwf   VG+o, W, A
        andwf   VE+o, W, A
        xorwf   VG+o, W, A
        movwf   y+o, A
        endm
ch:     CH_B    0
        CH_B    1
        CH_B    2
        CH_B    3
        return

;; Y = Maj(a, b, c) = (a & (b ^ c)) ^ (b & c), byte by byte.
MAJ_B   macro   o
        movf    VB+o, W, A
        xorwf   VC+o, W, A
        andwf   VA+o, W, A
        movwf   y+o, A
        movf    VB+o, W, A
        andwf   VC+o, W, A
        xorwf   y+o, F, A
        endm
maj:    MAJ_B   0
        MAJ_B   1
        MAJ_B   2
        MAJ_B   3
        return

        end
