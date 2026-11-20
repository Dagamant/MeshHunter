// LoRa listening profiles. A port of meshhunter/core/sniffer_profiles.py,
// extended with the EU 868 band the Cap LoRa-1262 (868-923 MHz) also covers.
//
// A profile is everything the SX1262 has to match in hardware before it will
// hand us a packet: frequency, bandwidth, spreading factor, coding rate and
// sync word. The radio sits on one profile at a time, so the sniffer either
// stays fixed on one or hops between several.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace mesh {

constexpr uint8_t kSyncMeshCore = 0x12;  // RadioLib "private" sync word, as used by MeshCore
constexpr uint8_t kSyncMeshtastic = 0x2B;

// Receiver preamble length. Shorter than either network transmits (16), so
// the SX1262 locks on to both, including packets joined part-way through
// their preamble while hopping.
constexpr uint16_t kRxPreamble = 8;

enum class Family { MeshCore, Meshtastic, Other };

struct Profile {
    std::string key;
    std::string name;
    Family family = Family::Other;
    double freq = 0.0;  // MHz
    double bw = 0.0;    // kHz
    int sf = 0;
    int cr = 5;  // 5..8 (4/5..4/8)
    uint8_t sync = 0;
    uint16_t preamble = kRxPreamble;
    std::string channel_name;  // Meshtastic: channel the default key is hashed with

    double symbol_ms() const { return static_cast<double>(1u << sf) / bw; }
    std::string short_label() const;  // "SF7/62.5 910.525"
};

enum class Region { US, EU868 };

const char* region_name(Region r);
bool parse_region(const std::string& text, Region& out);

uint32_t djb2(const std::string& text);

// Default-slot frequency for a Meshtastic channel name in a band, mirroring
// firmware RadioInterface.cpp: djb2(name) % numChannels.
double meshtastic_slot_freq(const std::string& channel_name, double bw_khz, double band_start_mhz,
                            double band_end_mhz);

// Built-in profiles for a region, in display order.
std::vector<Profile> builtin_profiles(Region region);

// Default fixed profile and hop list for a region.
std::string default_fixed_profile(Region region);
std::vector<std::string> default_hop_profiles(Region region);

const Profile* find_profile(const std::vector<Profile>& profiles, const std::string& key);

}  // namespace mesh
