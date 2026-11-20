// MeshCore over-the-air packet decoder. A C++ port of the MeshCore half of
// meshhunter/core/sniffer_decode.py: header/route/path are always cleartext,
// ADVERTs are signed (and the signature is checked, so a corrupted packet
// that slipped past the LoRa CRC can't create a bogus node), and group text
// on a channel whose key we know is decrypted.

#pragma once

#include "bytes.h"

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace mesh {

constexpr int kMcPayloadReq = 0x00;
constexpr int kMcPayloadResponse = 0x01;
constexpr int kMcPayloadTextMsg = 0x02;
constexpr int kMcPayloadAck = 0x03;
constexpr int kMcPayloadAdvert = 0x04;
constexpr int kMcPayloadGrpTxt = 0x05;
constexpr int kMcPayloadAnonReq = 0x07;
constexpr int kMcPayloadPath = 0x08;

// MeshCore ADV_TYPE_* (same numbering as the desktop app's constants.py).
constexpr int kAdvTypeChat = 1;
constexpr int kAdvTypeRepeater = 2;
constexpr int kAdvTypeRoom = 3;
constexpr int kAdvTypeSensor = 4;

const char* mc_route_name(int route_type);
const char* mc_payload_name(int payload_type);  // "UNK<n>" for unknown types
const char* adv_type_name(int adv_type);        // "Chat"/"Repeater"/... or ""

struct McChannel {
    std::string name;
    std::array<uint8_t, 16> key{};
    uint8_t hash = 0;
};

// The default Public channel, plus "#name" hashtag channels whose key is
// derived from the name itself (sha256("#name")[:16]).
McChannel mc_public_channel();
bool mc_hashtag_channel(const std::string& name, McChannel& out);
// key_text is 32 hex chars or base64 of 16 bytes.
bool mc_keyed_channel(const std::string& name, const std::string& key_text, McChannel& out);

struct McAdvert {
    std::string public_key;  // 64 hex chars
    uint32_t timestamp = 0;
    bool sig_ok = false;
    uint8_t flags = 0;
    int type = 0;
    bool has_location = false;
    double lat = 0.0;
    double lon = 0.0;
    std::string name;
};

struct McPacket {
    std::string error;  // non-empty when the packet couldn't be fully parsed
    int route_type = 0;
    int payload_type = 0;
    int payload_ver = 0;
    std::string transport_codes;
    int path_len = 0;
    int path_hash_size = 1;
    std::string path;  // hex
    uint32_t pkt_hash = 0;

    bool has_advert = false;
    McAdvert advert;

    // GRP_TXT
    std::string chan_hash;  // 2 hex chars
    std::string chan_name;  // set once decrypted
    uint32_t sender_timestamp = 0;
    std::string message;

    // REQ/RESPONSE/TEXT_MSG/PATH/ANON_REQ
    std::string dest_hash;
    std::string src_hash;
    std::string sender_key;
};

McPacket decode_meshcore(const uint8_t* raw, size_t len, const std::vector<McChannel>& channels);

// One-line summary for the live feed ("ADVERT Repeater 'Name' (lat,lon)").
std::string describe_meshcore(const McPacket& p);

}  // namespace mesh
