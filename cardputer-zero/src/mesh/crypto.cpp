#include "crypto.h"

#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/sha.h>

#include <memory>

namespace mesh::crypto {
namespace {

struct CipherCtxDeleter {
    void operator()(EVP_CIPHER_CTX* ctx) const { EVP_CIPHER_CTX_free(ctx); }
};
struct MdCtxDeleter {
    void operator()(EVP_MD_CTX* ctx) const { EVP_MD_CTX_free(ctx); }
};
struct PkeyDeleter {
    void operator()(EVP_PKEY* key) const { EVP_PKEY_free(key); }
};

bool run_cipher(const EVP_CIPHER* cipher, const uint8_t* key, const uint8_t* iv, bool encrypt, const uint8_t* in,
                size_t len, uint8_t* out) {
    std::unique_ptr<EVP_CIPHER_CTX, CipherCtxDeleter> ctx(EVP_CIPHER_CTX_new());
    if (!ctx) return false;
    if (EVP_CipherInit_ex(ctx.get(), cipher, nullptr, key, iv, encrypt ? 1 : 0) != 1) return false;
    EVP_CIPHER_CTX_set_padding(ctx.get(), 0);
    int written = 0;
    if (len > 0 && EVP_CipherUpdate(ctx.get(), out, &written, in, static_cast<int>(len)) != 1) return false;
    int tail = 0;
    if (EVP_CipherFinal_ex(ctx.get(), out + written, &tail) != 1) return false;
    return static_cast<size_t>(written + tail) == len;
}

}  // namespace

bool ed25519_verify(const uint8_t public_key[32], const uint8_t* message, size_t message_len,
                    const uint8_t signature[64]) {
    std::unique_ptr<EVP_PKEY, PkeyDeleter> key(
        EVP_PKEY_new_raw_public_key(EVP_PKEY_ED25519, nullptr, public_key, 32));
    if (!key) return false;
    std::unique_ptr<EVP_MD_CTX, MdCtxDeleter> ctx(EVP_MD_CTX_new());
    if (!ctx) return false;
    if (EVP_DigestVerifyInit(ctx.get(), nullptr, nullptr, nullptr, key.get()) != 1) return false;
    return EVP_DigestVerify(ctx.get(), signature, 64, message, message_len) == 1;
}

Sha256Digest sha256(const uint8_t* data, size_t len) {
    Sha256Digest out{};
    SHA256(data, len, out.data());
    return out;
}

Sha256Digest hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* data, size_t len) {
    Sha256Digest out{};
    unsigned int out_len = 0;
    HMAC(EVP_sha256(), key, static_cast<int>(key_len), data, len, out.data(), &out_len);
    return out;
}

bool aes128_ecb_decrypt(const uint8_t key[16], const uint8_t* in, size_t len, uint8_t* out) {
    if (len % 16) return false;
    return run_cipher(EVP_aes_128_ecb(), key, nullptr, false, in, len, out);
}

bool aes_ctr(const uint8_t* key, size_t key_len, const uint8_t iv[16], const uint8_t* in, size_t len,
             uint8_t* out) {
    const EVP_CIPHER* cipher = key_len == 16 ? EVP_aes_128_ctr() : key_len == 32 ? EVP_aes_256_ctr() : nullptr;
    if (!cipher) return false;
    return run_cipher(cipher, key, iv, true, in, len, out);
}

}  // namespace mesh::crypto
