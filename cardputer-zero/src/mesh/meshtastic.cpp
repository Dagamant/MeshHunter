#include "meshtastic.h"

#include "base64.h"
#include "crypto.h"

#include <cstdio>
#include <cstring>
#include <map>
#include <stdexcept>

namespace mesh {
namespace {

const uint8_t kDefaultPsk[16] = {0xD4, 0xF1, 0xBB, 0x3A, 0x20, 0x29, 0x07, 0x59,
                                 0xF0, 0xBC, 0xFF, 0xAB, 0xCF, 0x4E, 0x69, 0x01};

uint8_t xor_hash(const uint8_t* data, size_t len) {
    uint8_t h = 0;
    for (size_t i = 0; i < len; ++i) h ^= data[i];
    return h;
}

// -- minimal protobuf wire-format reader ------------------------------------

struct PbError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct PbValue {
    int wire = 0;
    uint64_t num = 0;  // varint / fixed32 / fixed64
    Bytes bytes;       // wire type 2
};

using PbFields = std::map<int, std::vector<PbValue>>;

uint64_t read_varint(const Bytes& buf, size_t& pos) {
    uint64_t result = 0;
    int shift = 0;
    while (true) {
        if (pos >= buf.size()) throw PbError("truncated varint");
        const uint8_t b = buf[pos++];
        result |= static_cast<uint64_t>(b & 0x7F) << shift;
        if (!(b & 0x80)) return result;
        shift += 7;
        if (shift > 63) throw PbError("varint too long");
    }
}

// Raises PbError on anything malformed -- which is also how a wrong
// decryption key is detected.
PbFields pb_fields(const Bytes& buf) {
    PbFields fields;
    size_t pos = 0;
    const size_t n = buf.size();
    while (pos < n) {
        const uint64_t key = read_varint(buf, pos);
        const int field = static_cast<int>(key >> 3);
        const int wire = static_cast<int>(key & 7);
        if (field == 0) throw PbError("field 0");
        PbValue v;
        v.wire = wire;
        if (wire == 0) {
            v.num = read_varint(buf, pos);
        } else if (wire == 1) {
            if (pos + 8 > n) throw PbError("truncated fixed64");
            v.num = read_le64(buf.data() + pos);
            pos += 8;
        } else if (wire == 2) {
            const uint64_t ln = read_varint(buf, pos);
            if (ln > n - pos) throw PbError("truncated bytes");
            v.bytes.assign(buf.begin() + static_cast<long>(pos), buf.begin() + static_cast<long>(pos + ln));
            pos += ln;
        } else if (wire == 5) {
            if (pos + 4 > n) throw PbError("truncated fixed32");
            v.num = read_le32(buf.data() + pos);
            pos += 4;
        } else {
            throw PbError("wire type " + std::to_string(wire));
        }
        fields[field].push_back(std::move(v));
    }
    return fields;
}

const PbValue* first(const PbFields& f, int num) {
    const auto it = f.find(num);
    return it == f.end() || it->second.empty() ? nullptr : &it->second.front();
}

uint64_t first_num(const PbFields& f, int num, uint64_t def = 0) {
    const auto* v = first(f, num);
    return v && v->wire != 2 ? v->num : def;
}

std::string first_str(const PbFields& f, int num) {
    const auto* v = first(f, num);
    if (!v || v->wire != 2) return {};
    return sanitize_utf8(v->bytes.data(), v->bytes.size());
}

bool coords(const PbFields& f, int lat_field, int lon_field, double& lat, double& lon) {
    const auto* la = first(f, lat_field);
    const auto* lo = first(f, lon_field);
    if (!la || !lo || la->wire == 2 || lo->wire == 2) return false;
    const auto lat_i = static_cast<int32_t>(static_cast<uint32_t>(la->num));
    const auto lon_i = static_cast<int32_t>(static_cast<uint32_t>(lo->num));
    if (lat_i == 0 && lon_i == 0) return false;
    lat = lat_i / 1e7;
    lon = lon_i / 1e7;
    return true;
}

std::string role_text(uint64_t role) {
    const char* name = mt_role_name(static_cast<int>(role));
    return name ? name : std::to_string(role);
}

void decode_app(int port, const Bytes& payload, MtPacket& d) {
    if (port == 1) {
        d.text = sanitize_utf8(payload.data(), payload.size());
    } else if (port == 3) {
        const auto f = pb_fields(payload);
        double lat, lon;
        if (coords(f, 1, 2, lat, lon)) {
            d.lat = lat;
            d.lon = lon;
        }
        if (const auto* alt = first(f, 3); alt && alt->wire != 2) {
            d.alt = static_cast<int64_t>(alt->num);  // int32 varint, sign-extended to 64 bits
        }
    } else if (port == 4) {
        const auto f = pb_fields(payload);
        d.user_id = first_str(f, 1);
        d.long_name = first_str(f, 2);
        d.short_name = first_str(f, 3);
        d.hw_model = first_num(f, 5, 0);
        d.role = role_text(first_num(f, 7, 0));
        if (const auto* pk = first(f, 8); pk && pk->wire == 2 && !pk->bytes.empty()) {
            d.public_key = to_hex(pk->bytes);
        }
    } else if (port == 71) {
        const auto f = pb_fields(payload);
        std::vector<std::string> out;
        if (const auto it = f.find(4); it != f.end()) {
            for (const auto& raw_n : it->second) {
                if (raw_n.wire != 2) continue;
                const auto nf = pb_fields(raw_n.bytes);
                out.push_back(mt_node_id(static_cast<uint32_t>(first_num(nf, 1, 0))));
            }
        }
        d.neighbors = out;
    } else if (port == 73) {
        const auto f = pb_fields(payload);
        d.long_name = first_str(f, 1);
        d.short_name = first_str(f, 2);
        d.role = role_text(first_num(f, 3, 0));
        d.hw_model = first_num(f, 4, 0);
        d.firmware_version = first_str(f, 5);
        double lat, lon;
        if (coords(f, 9, 10, lat, lon)) {
            d.lat = lat;
            d.lon = lon;
        }
    } else if (port == 70) {
        const auto f = pb_fields(payload);
        std::vector<std::string> route;
        if (const auto it = f.find(1); it != f.end()) {
            for (const auto& v : it->second) {
                if (v.wire == 2) {  // packed repeated fixed32
                    for (size_t i = 0; i + 4 <= v.bytes.size(); i += 4) {
                        route.push_back(mt_node_id(read_le32(v.bytes.data() + i)));
                    }
                } else {
                    route.push_back(mt_node_id(static_cast<uint32_t>(v.num)));
                }
            }
        }
        d.route = route;
    }
}

}  // namespace

Bytes expand_meshtastic_psk(const Bytes& psk) {
    if (psk.empty()) return {};
    if (psk.size() == 1) {
        const uint8_t idx = psk[0];
        if (idx == 0) return {};
        Bytes key(kDefaultPsk, kDefaultPsk + 16);
        key[15] = static_cast<uint8_t>(key[15] + idx - 1);
        return key;
    }
    Bytes key = psk;
    if (key.size() < 16) key.resize(16, 0);
    else if (key.size() > 16 && key.size() < 32) key.resize(32, 0);
    return key;
}

bool mt_channel(const std::string& name, const std::string& psk_b64, MtChannel& out) {
    Bytes psk;
    if (!base64_decode(psk_b64, psk)) return false;
    out.name = name;
    out.key = expand_meshtastic_psk(psk);
    out.hash = static_cast<uint8_t>(xor_hash(reinterpret_cast<const uint8_t*>(name.data()), name.size()) ^
                                    xor_hash(out.key.data(), out.key.size()));
    return true;
}

std::string mt_node_id(uint32_t num) {
    char buf[12];
    std::snprintf(buf, sizeof(buf), "!%08x", num);
    return buf;
}

const char* mt_port_name(int port) {
    switch (port) {
        case 0: return "UNKNOWN";
        case 1: return "TEXT";
        case 2: return "REMOTE_HW";
        case 3: return "POSITION";
        case 4: return "NODEINFO";
        case 5: return "ROUTING";
        case 6: return "ADMIN";
        case 7: return "TEXT_COMPRESSED";
        case 8: return "WAYPOINT";
        case 9: return "AUDIO";
        case 10: return "DETECTION_SENSOR";
        case 32: return "REPLY";
        case 33: return "IP_TUNNEL";
        case 34: return "PAXCOUNTER";
        case 64: return "SERIAL";
        case 65: return "STORE_FORWARD";
        case 66: return "RANGE_TEST";
        case 67: return "TELEMETRY";
        case 68: return "ZPS";
        case 69: return "SIMULATOR";
        case 70: return "TRACEROUTE";
        case 71: return "NEIGHBORINFO";
        case 72: return "ATAK_PLUGIN";
        case 73: return "MAP_REPORT";
        case 74: return "POWERSTRESS";
        default: return nullptr;
    }
}

const char* mt_role_name(int role) {
    static const char* kRoles[] = {"CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_CLIENT", "REPEATER",
                                   "TRACKER", "SENSOR", "TAK", "CLIENT_HIDDEN", "LOST_AND_FOUND",
                                   "TAK_TRACKER", "ROUTER_LATE", "CLIENT_BASE"};
    return role >= 0 && role < 13 ? kRoles[role] : nullptr;
}

MtPacket decode_meshtastic(const uint8_t* raw, size_t len, const std::vector<MtChannel>& channels) {
    MtPacket d;
    if (len < 16) {
        d.error = "short packet";
        return d;
    }
    const uint32_t dest = read_le32(raw);
    const uint32_t sender = read_le32(raw + 4);
    const uint32_t pkt_id = read_le32(raw + 8);
    const uint8_t flags = raw[12];
    d.to = dest == kMtBroadcast ? "^all" : mt_node_id(dest);
    d.from = mt_node_id(sender);
    d.from_num = sender;
    d.id = pkt_id;
    d.hop_limit = flags & 0x07;
    d.want_ack = flags & 0x08;
    d.via_mqtt = flags & 0x10;
    d.hop_start = flags >> 5;
    d.chan_hash = raw[13];
    d.next_hop = raw[14];
    d.relay_node = raw[15];
    if (d.hop_start) d.hops_away = d.hop_start - d.hop_limit;

    const Bytes payload(raw + 16, raw + len);
    // nonce = packet id (u64 LE) + sender (u32 LE) + 4 zero bytes
    uint8_t nonce[16] = {};
    std::memcpy(nonce, raw + 8, 4);
    std::memcpy(nonce + 8, raw + 4, 4);

    for (const auto& chan : channels) {
        if (chan.hash != d.chan_hash) continue;
        Bytes plain(payload.size());
        if (!chan.key.empty()) {
            if (!crypto::aes_ctr(chan.key.data(), chan.key.size(), nonce, payload.data(), payload.size(),
                                 plain.data())) {
                continue;
            }
        } else {
            plain = payload;
        }
        PbFields data;
        int port = -1;
        try {
            data = pb_fields(plain);
            const auto* port_v = first(data, 1);
            const auto* body = first(data, 2);
            if (!port_v || port_v->wire == 2 || !mt_port_name(static_cast<int>(port_v->num)) ||
                (body && body->wire != 2)) {
                continue;  // not a Data message
            }
            port = static_cast<int>(port_v->num);
        } catch (const PbError&) {
            continue;
        }
        d.channel = chan.name;
        d.port = port;
        d.portname = mt_port_name(port);
        const auto* body = first(data, 2);
        try {
            decode_app(port, body ? body->bytes : Bytes{}, d);
        } catch (const PbError& e) {
            d.app_error = e.what();
        }
        return d;
    }

    d.encrypted = true;
    if (d.chan_hash == 0 && dest != kMtBroadcast) d.pki = true;
    return d;
}

std::string describe_meshtastic(const MtPacket& p) {
    if (!p.error.empty()) return "MT " + p.error;
    std::string out = p.portname.empty() ? std::string("MT") : p.portname;
    out += " " + p.from;
    if (p.encrypted) {
        out += p.pki ? " (pki-dm)" : " (encrypted)";
    }
    if (!p.long_name.empty()) out += " '" + p.long_name + "'";
    if (p.text) out += ": " + *p.text;
    if (p.lat) out += " +GPS";
    if (p.hops_away) out += " hops:" + std::to_string(*p.hops_away);
    return out;
}

}  // namespace mesh
