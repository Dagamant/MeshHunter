// Thin wrappers over OpenSSL libcrypto (present in the CardputerZero BSP)
// for the handful of primitives the MeshCore and Meshtastic decoders need.

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace mesh::crypto {

using Sha256Digest = std::array<uint8_t, 32>;

bool ed25519_verify(const uint8_t public_key[32], const uint8_t* message, size_t message_len,
                    const uint8_t signature[64]);

Sha256Digest sha256(const uint8_t* data, size_t len);

Sha256Digest hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* data, size_t len);

// AES-128-ECB decrypt, no padding. `len` must be a multiple of 16.
bool aes128_ecb_decrypt(const uint8_t key[16], const uint8_t* in, size_t len, uint8_t* out);

// AES-CTR (128- or 256-bit key) with a full 16-byte initial counter block.
bool aes_ctr(const uint8_t* key, size_t key_len, const uint8_t iv[16], const uint8_t* in, size_t len,
             uint8_t* out);

}  // namespace mesh::crypto
