/*
 * The diagnostic counters persist_counters and reset_reason keep in .extbss
 * on the rabbit (src/vm/vlog.c there). The emulator's step list has neither,
 * but crash_report's opcode reads them; weak zeros here, which a build that
 * does carry those steps overrides.
 */
#include <stdint.h>

__attribute__((weak)) int32_t nab_reset_reason, nab_reset_count;
__attribute__((weak)) int32_t nab_verify_calls, nab_verify_ok;
__attribute__((weak)) int32_t nab_flash_calls, nab_flash_error;
