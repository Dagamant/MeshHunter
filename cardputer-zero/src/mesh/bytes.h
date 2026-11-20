// Small byte/hex/number-formatting helpers shared by the mesh decoders and
// the CSV/JSON writers.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace mesh {

using Bytes = std::vector<uint8_t>;

std::string to_hex(const uint8_t* data, size_t len);
inline std::string to_hex(const Bytes& data) { return to_hex(data.data(), data.size()); }
// Returns false (and leaves out empty) on odd length or a non-hex digit.
bool from_hex(const std::string& hex, Bytes& out);

uint32_t read_le32(const uint8_t* p);
int32_t read_le32s(const uint8_t* p);
uint64_t read_le64(const uint8_t* p);

// Python-style float text for CSV compatibility with the desktop app:
// fixed `decimals`, then trailing zeros trimmed but at least one decimal
// kept ("38.1", "-81.11461", "38.0").
std::string format_float(double value, int decimals);

// "2026-11-08 18:51:27" in local time.
std::string local_timestamp();
// "2026-11-08T18:51:27.212" in local time.
std::string local_iso_timestamp_ms();

// Strict UTF-8 cleanup: invalid sequences become U+FFFD, like Python's
// decode("utf-8", "replace"), so names never break the CSV/JSON writers.
std::string sanitize_utf8(const uint8_t* data, size_t len);

}  // namespace mesh
