// Meshtastic over-the-air packet decoder. A C++ port of the Meshtastic half
// of meshhunter/core/sniffer_decode.py: the 16-byte header is cleartext; the
// payload is AES-CTR with the channel key, and every modem preset's default
// channel uses the well-known default key, so on default channels we get
// NodeInfo, Position, MapReport, NeighborInfo, Traceroute and public text.

#pragma once

#include "bytes.h"

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace mesh {

constexpr uint32_t kMtBroadcast = 0xFFFFFFFF;

struct MtChannel {
    std::string name;
    Bytes key;  // empty = unencrypted channel
    uint8_t hash = 0;
};

// psk_b64 "AQ==" is the default key; see expand_meshtastic_psk().
bool mt_channel(const std::string& name, const std::string& psk_b64, MtChannel& out);
Bytes expand_meshtastic_psk(const Bytes& psk);
std::string mt_node_id(uint32_t num);  // "!a1b2c3d4"
const char* mt_port_name(int port);    // nullptr if not a known port
const char* mt_role_name(int role);

struct MtPacket {
    std::string error;
    std::string to;    // "!xxxxxxxx" or "^all"
    std::string from;  // "!xxxxxxxx"
    uint32_t from_num = 0;
    uint32_t id = 0;
    int hop_limit = 0;
    int hop_start = 0;
    bool want_ack = false;
    bool via_mqtt = false;
    std::optional<int> hops_away;
    uint8_t chan_hash = 0;
    uint8_t next_hop = 0;
    uint8_t relay_node = 0;

    bool encrypted = false;  // no known key matched
    bool pki = false;        // public-key encrypted DM
    std::string channel;
    int port = -1;
    std::string portname;
    std::string app_error;

    std::optional<std::string> text;
    std::optional<double> lat, lon;
    std::optional<int64_t> alt;
    std::string user_id, long_name, short_name, role, public_key, firmware_version;
    std::optional<uint64_t> hw_model;
    std::optional<std::vector<std::string>> neighbors;
    std::optional<std::vector<std::string>> route;
};

MtPacket decode_meshtastic(const uint8_t* raw, size_t len, const std::vector<MtChannel>& channels);

std::string describe_meshtastic(const MtPacket& p);

}  // namespace mesh
