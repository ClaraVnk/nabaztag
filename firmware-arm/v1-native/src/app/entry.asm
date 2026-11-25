;; entry.asm -- the application's reset vector, at APP_BASE: SDCC's crt0
;; puts its own at 0x0000 (the loader's block, left out of the image), and
;; --ivt-loc moves only the interrupt vectors. gpasm -p DEV -D APP_BASE=... (Makefile).
        extern  __startup
APPVEC  code    APP_BASE
        goto    __startup
        end
