/* Mbed TLS 3.6: the smallest TLS 1.3 client configuration that check_config
 * accepts, for a flash-size reading on ARM7TDMI. One suite
 * (TLS_AES_128_GCM_SHA256), X25519 only, ephemeral key exchange only, no
 * TLS 1.2, no DTLS, no tickets, no PSK, no renegotiation, no ALPN, no
 * debug, no error strings, no file I/O. X.509 parsing stays because the
 * ephemeral mode will not build without it -- the pinned-bytes design the
 * rabbit would use has no equivalent here, so x509*.o is reported apart. */
#define MBEDTLS_SSL_TLS_C
#define MBEDTLS_SSL_CLI_C
#define MBEDTLS_SSL_PROTO_TLS1_3
#define MBEDTLS_SSL_TLS1_3_KEY_EXCHANGE_MODE_EPHEMERAL_ENABLED
#define MBEDTLS_SSL_KEEP_PEER_CERTIFICATE
#define MBEDTLS_SSL_MAX_FRAGMENT_LENGTH
#define MBEDTLS_SSL_IN_CONTENT_LEN 2048
#define MBEDTLS_SSL_OUT_CONTENT_LEN 2048

#define MBEDTLS_PSA_CRYPTO_C
#define MBEDTLS_PSA_CRYPTO_CONFIG
#define MBEDTLS_USE_PSA_CRYPTO
#define MBEDTLS_PSA_CRYPTO_EXTERNAL_RNG
#define MBEDTLS_ENTROPY_C
#define MBEDTLS_CTR_DRBG_C

#define MBEDTLS_MD_C
#define MBEDTLS_SHA256_C
#define MBEDTLS_HKDF_C
#define MBEDTLS_CIPHER_C
#define MBEDTLS_AES_C
#define MBEDTLS_GCM_C
#define MBEDTLS_AES_ROM_TABLES
#define MBEDTLS_AES_FEWER_TABLES

#define MBEDTLS_BIGNUM_C
#define MBEDTLS_ECP_C
#define MBEDTLS_ECDH_C
#define MBEDTLS_ECDSA_C
#define MBEDTLS_ECP_DP_CURVE25519_ENABLED
#define MBEDTLS_ECP_DP_SECP256R1_ENABLED
#define MBEDTLS_ECP_NIST_OPTIM
#define MBEDTLS_ASN1_PARSE_C
#define MBEDTLS_ASN1_WRITE_C
#define MBEDTLS_OID_C
#define MBEDTLS_PK_C
#define MBEDTLS_PK_PARSE_C
#define MBEDTLS_X509_USE_C
#define MBEDTLS_X509_CRT_PARSE_C
#define MBEDTLS_PLATFORM_C
#define MBEDTLS_PLATFORM_NO_STD_FUNCTIONS
#define MBEDTLS_PLATFORM_MEMORY
#define MBEDTLS_NO_PLATFORM_ENTROPY
#define MBEDTLS_ENTROPY_HARDWARE_ALT
#define MBEDTLS_PLATFORM_SNPRINTF_MACRO snprintf
#define MBEDTLS_PLATFORM_VSNPRINTF_MACRO vsnprintf
#define MBEDTLS_PLATFORM_PRINTF_MACRO printf
#define MBEDTLS_PLATFORM_FPRINTF_MACRO fprintf
#define MBEDTLS_PLATFORM_CALLOC_MACRO calloc
#define MBEDTLS_PLATFORM_FREE_MACRO free
#define MBEDTLS_PLATFORM_EXIT_MACRO exit
#define MBEDTLS_PLATFORM_SETBUF_MACRO setbuf
