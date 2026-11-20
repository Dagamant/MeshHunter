#include "meshcore.h"

#include "base64.h"
#include "crypto.h"

#include <cstdio>
#include <cstring>

namespace mesh {
namespace {

constexpr std::array<uint8_t, 16> kPublicChannelKey = {0x8b, 0x33, 0x87, 0xe9, 0xc5, 0xcd, 0xea, 0x6a,
                                                       0xc9, 0xe5, 0xed, 0xba, 0xa1, 0x15, 0xcd, 0x72};

McChannel make_channel(const std::string& name, const uint8_t key[16]) {
    McChannel c;
    c.name = name;
    std::memcpy(c.key.data(), key, 16);
    c.hash = crypto::sha256(key, 16)[0];
    return c;
}

std::string hex_byte(uint8_t b) { return to_hex(&b, 1); }

void decode_advert(const uint8_t* payload, size_t len, McPacket& d) {
    if (len < 101) {
        d.error = "short advert";
        return;
    }
    const uint8_t* pubkey = payload;
    const uint8_t* ts = payload + 32;
    const uint8_t* sig = payload + 36;
    const uint8_t* app = payload + 100;
    const size_t app_len = len - 100;

    d.has_advert = true;
    McAdvert& a = d.advert;
    a.public_key = to_hex(pubkey, 32);
    a.timestamp = read_le32(ts);

    // Signed message = pubkey + timestamp + app data.
    Bytes signed_msg;
    signed_msg.reserve(36 + app_len);
    signed_msg.insert(signed_msg.end(), pubkey, pubkey + 36);
    signed_msg.insert(signed_msg.end(), app, app + app_len);
    a.sig_ok = crypto::ed25519_verify(pubkey, signed_msg.data(), signed_msg.size(), sig);

    const uint8_t flags = app[0];
    a.flags = flags;
    a.type = flags & 0x0F;
    size_t pos = 1;
    if (flags & 0x10) {
        if (pos + 8 > app_len) {
            d.error = "truncated advert location";
            return;
        }
        a.has_location = true;
        a.lat = read_le32s(app + pos) / 1e6;
        a.lon = read_le32s(app + pos + 4) / 1e6;
        pos += 8;
    }
    if (flags & 0x20) pos += 2;
    if (flags & 0x40) pos += 2;
    if ((flags & 0x80) && pos <= app_len) {
        // Python: decode("utf-8", "replace").strip("\x00")
        size_t start = pos;
        size_t end = app_len;
        while (start < end && app[start] == 0) ++start;
        while (end > start && app[end - 1] == 0) --end;
        a.name = sanitize_utf8(app + start, end - start);
    }
}

void decode_grp_txt(const uint8_t* payload, size_t len, const std::vector<McChannel>& channels, McPacket& d) {
    if (len < 3) {
        d.error = "short group packet";
        return;
    }
    const uint8_t chan_hash = payload[0];
    const uint8_t* mac = payload + 1;
    const uint8_t* cipher = payload + 3;
    const size_t cipher_len = len - 3;
    d.chan_hash = hex_byte(chan_hash);
    for (const auto& chan : channels) {
        if (chan.hash != chan_hash) continue;
        uint8_t hmac_key[32] = {};
        std::memcpy(hmac_key, chan.key.data(), 16);
        const auto digest = crypto::hmac_sha256(hmac_key, sizeof(hmac_key), cipher, cipher_len);
        if (digest[0] != mac[0] || digest[1] != mac[1] || cipher_len % 16) continue;
        Bytes plain(cipher_len);
        if (!crypto::aes128_ecb_decrypt(chan.key.data(), cipher, cipher_len, plain.data())) continue;
        if (plain.size() < 5) continue;
        d.chan_name = chan.name;
        d.sender_timestamp = read_le32(plain.data());
        size_t end = plain.size();
        while (end > 5 && plain[end - 1] == 0) --end;
        d.message = sanitize_utf8(plain.data() + 5, end - 5);
        return;
    }
}

}  // namespace

const char* mc_route_name(int route_type) {
    static const char* kNames[] = {"TC_FLOOD", "FLOOD", "DIRECT", "TC_DIRECT"};
    return route_type >= 0 && route_type < 4 ? kNames[route_type] : "?";
}

const char* mc_payload_name(int payload_type) {
    switch (payload_type) {
        case 0x00: return "REQ";
        case 0x01: return "RESPONSE";
        case 0x02: return "TEXT_MSG";
        case 0x03: return "ACK";
        case 0x04: return "ADVERT";
        case 0x05: return "GRP_TXT";
        case 0x06: return "GRP_DATA";
        case 0x07: return "ANON_REQ";
        case 0x08: return "PATH";
        case 0x09: return "TRACE";
        case 0x0A: return "MULTIPART";
        case 0x0B: return "CONTROL";
        case 0x0F: return "RAW_CUSTOM";
        default: break;
    }
    static thread_local char buf[8];
    std::snprintf(buf, sizeof(buf), "UNK%d", payload_type);
    return buf;
}

const char* adv_type_name(int adv_type) {
    switch (adv_type) {
        case kAdvTypeChat: return "Chat";
        case kAdvTypeRepeater: return "Repeater";
        case kAdvTypeRoom: return "Room";
        case kAdvTypeSensor: return "Sensor";
        default: return "";
    }
}

McChannel mc_public_channel() { return make_channel("Public", kPublicChannelKey.data()); }

bool mc_hashtag_channel(const std::string& name, McChannel& out) {
    if (name.size() < 2 || name[0] != '#') return false;
    const auto digest = crypto::sha256(reinterpret_cast<const uint8_t*>(name.data()), name.size());
    out = make_channel(name, digest.data());
    return true;
}

bool mc_keyed_channel(const std::string& name, const std::string& key_text, McChannel& out) {
    Bytes key;
    if (key_text.size() == 32 || key_text.size() == 64) {
        if (!from_hex(key_text, key)) return false;
    } else if (!base64_decode(key_text, key)) {
        return false;
    }
    if (key.size() < 16) return false;
    out = make_channel(name, key.data());
    return true;
}

McPacket decode_meshcore(const uint8_t* raw, size_t len, const std::vector<McChannel>& channels) {
    McPacket d;
    if (len < 2) {
        d.error = "short packet";
        return d;
    }
    const uint8_t header = raw[0];
    d.route_type = header & 0x03;
    d.payload_type = (header >> 2) & 0x0F;
    d.payload_ver = header >> 6;
    size_t pos = 1;
    if (d.route_type == 0 || d.route_type == 3) {
        if (pos + 4 > len) {
            d.error = "truncated transport codes";
            return d;
        }
        d.transport_codes = to_hex(raw + pos, 4);
        pos += 4;
    }
    if (pos >= len) {
        d.error = "truncated path";
        return d;
    }
    const uint8_t path_byte = raw[pos++];
    d.path_hash_size = (path_byte >> 6) + 1;
    d.path_len = path_byte & 0x3F;
    const size_t path_bytes = static_cast<size_t>(d.path_len) * d.path_hash_size;
    if (pos + path_bytes > len) {
        d.error = "truncated path";
        return d;
    }
    d.path = to_hex(raw + pos, path_bytes);
    pos += path_bytes;

    const uint8_t* payload = raw + pos;
    const size_t payload_len = len - pos;
    const auto digest = crypto::sha256(payload, payload_len);
    d.pkt_hash = read_le32(digest.data());

    switch (d.payload_type) {
        case kMcPayloadAdvert:
            decode_advert(payload, payload_len, d);
            break;
        case kMcPayloadGrpTxt:
            decode_grp_txt(payload, payload_len, channels, d);
            break;
        case kMcPayloadReq:
        case kMcPayloadResponse:
        case kMcPayloadTextMsg:
        case kMcPayloadPath:
            if (payload_len >= 2) {
                d.dest_hash = hex_byte(payload[0]);
                d.src_hash = hex_byte(payload[1]);
            }
            break;
        case kMcPayloadAnonReq:
            if (payload_len >= 33) {
                d.dest_hash = hex_byte(payload[0]);
                d.sender_key = to_hex(payload + 1, 32);
            }
            break;
        default:
            break;
    }
    return d;
}

std::string describe_meshcore(const McPacket& p) {
    std::string out = mc_payload_name(p.payload_type);
    if (p.has_advert) {
        const auto& a = p.advert;
        const char* type = adv_type_name(a.type);
        if (*type) {
            out += ' ';
            out += type;
        }
        out += " '";
        out += a.name.empty() ? a.public_key.substr(0, 8) : a.name;
        out += "'";
        if (a.has_location && (a.lat != 0.0 || a.lon != 0.0)) out += " +GPS";
        if (!a.sig_ok) out += " BAD-SIG";
    } else if (!p.chan_name.empty()) {
        out += " #" + p.chan_name + ": " + p.message;
    } else if (!p.chan_hash.empty()) {
        out += " chan " + p.chan_hash;
    } else if (!p.dest_hash.empty()) {
        out += " " + (p.src_hash.empty() ? std::string("?") : p.src_hash) + ">" + p.dest_hash;
    }
    if (p.path_len) out += " hops:" + std::to_string(p.path_len);
    if (!p.error.empty()) out += " (" + p.error + ")";
    return out;
}

}  // namespace mesh
