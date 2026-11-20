#include "bytes.h"

#include <chrono>
#include <cstdio>
#include <ctime>

namespace mesh {

std::string to_hex(const uint8_t* data, size_t len) {
    static const char* kDigits = "0123456789abcdef";
    std::string out;
    out.reserve(len * 2);
    for (size_t i = 0; i < len; ++i) {
        out.push_back(kDigits[data[i] >> 4]);
        out.push_back(kDigits[data[i] & 0x0F]);
    }
    return out;
}

namespace {
int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
}  // namespace

bool from_hex(const std::string& hex, Bytes& out) {
    out.clear();
    if (hex.size() % 2) return false;
    out.reserve(hex.size() / 2);
    for (size_t i = 0; i < hex.size(); i += 2) {
        const int hi = hex_value(hex[i]);
        const int lo = hex_value(hex[i + 1]);
        if (hi < 0 || lo < 0) {
            out.clear();
            return false;
        }
        out.push_back(static_cast<uint8_t>((hi << 4) | lo));
    }
    return true;
}

uint32_t read_le32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) | (static_cast<uint32_t>(p[2]) << 16) |
           (static_cast<uint32_t>(p[3]) << 24);
}

int32_t read_le32s(const uint8_t* p) { return static_cast<int32_t>(read_le32(p)); }

uint64_t read_le64(const uint8_t* p) {
    return static_cast<uint64_t>(read_le32(p)) | (static_cast<uint64_t>(read_le32(p + 4)) << 32);
}

std::string format_float(double value, int decimals) {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.*f", decimals, value);
    std::string s(buf);
    const auto dot = s.find('.');
    if (dot == std::string::npos) return s + ".0";
    while (s.size() > dot + 2 && s.back() == '0') s.pop_back();
    if (s == "-0.0") s = "0.0";
    return s;
}

std::string local_timestamp() {
    const std::time_t now = std::time(nullptr);
    std::tm tm{};
    localtime_r(&now, &tm);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &tm);
    return buf;
}

std::string local_iso_timestamp_ms() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t secs = std::chrono::system_clock::to_time_t(now);
    const auto ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() % 1000;
    std::tm tm{};
    localtime_r(&secs, &tm);
    char buf[40];
    const size_t n = std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tm);
    std::snprintf(buf + n, sizeof(buf) - n, ".%03d", static_cast<int>(ms));
    return buf;
}

std::string sanitize_utf8(const uint8_t* data, size_t len) {
    static const char* kReplacement = "\xEF\xBF\xBD";
    std::string out;
    out.reserve(len);
    size_t i = 0;
    while (i < len) {
        const uint8_t c = data[i];
        size_t need = 0;
        uint32_t cp = 0;
        if (c < 0x80) {
            out.push_back(static_cast<char>(c));
            ++i;
            continue;
        } else if ((c & 0xE0) == 0xC0) {
            need = 1;
            cp = c & 0x1F;
        } else if ((c & 0xF0) == 0xE0) {
            need = 2;
            cp = c & 0x0F;
        } else if ((c & 0xF8) == 0xF0) {
            need = 3;
            cp = c & 0x07;
        } else {
            out += kReplacement;
            ++i;
            continue;
        }
        bool ok = i + need < len;
        for (size_t k = 1; ok && k <= need; ++k) {
            if ((data[i + k] & 0xC0) != 0x80) {
                ok = false;
                break;
            }
            cp = (cp << 6) | (data[i + k] & 0x3F);
        }
        // Reject overlong encodings and surrogates.
        if (ok && ((need == 1 && cp < 0x80) || (need == 2 && cp < 0x800) || (need == 3 && cp < 0x10000) ||
                   (cp >= 0xD800 && cp <= 0xDFFF) || cp > 0x10FFFF)) {
            ok = false;
        }
        if (!ok) {
            out += kReplacement;
            ++i;
            continue;
        }
        out.append(reinterpret_cast<const char*>(data + i), need + 1);
        i += need + 1;
    }
    return out;
}

}  // namespace mesh
