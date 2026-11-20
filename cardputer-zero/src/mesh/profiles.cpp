#include "profiles.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>

namespace mesh {
namespace {

struct Preset {
    const char* name;
    double bw;
    int sf;
    int cr;
};

constexpr Preset kMeshtasticPresets[] = {
    {"LongFast", 250, 11, 5},  {"MediumFast", 250, 9, 5}, {"MediumSlow", 250, 10, 5},
    {"ShortFast", 250, 7, 5},  {"ShortSlow", 250, 8, 5},  {"ShortTurbo", 500, 7, 5},
    {"LongMod", 125, 11, 8},   {"LongSlow", 125, 12, 8},
};

Profile meshcore_profile(const char* key, const char* name, double freq, double bw, int sf, int cr) {
    Profile p;
    p.key = key;
    p.name = name;
    p.family = Family::MeshCore;
    p.freq = freq;
    p.bw = bw;
    p.sf = sf;
    p.cr = cr;
    p.sync = kSyncMeshCore;
    return p;
}

struct Band {
    double start;
    double end;
};

Band band_for(Region r) {
    switch (r) {
        case Region::EU868: return {869.4, 869.65};
        case Region::US:
        default: return {902.0, 928.0};
    }
}

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
    return s;
}

}  // namespace

std::string Profile::short_label() const {
    char buf[48];
    std::snprintf(buf, sizeof(buf), "SF%d/%g %.3f", sf, bw, freq);
    return buf;
}

const char* region_name(Region r) { return r == Region::EU868 ? "EU868" : "US"; }

bool parse_region(const std::string& text, Region& out) {
    const auto t = lower(text);
    if (t == "us" || t == "us915" || t == "na") {
        out = Region::US;
        return true;
    }
    if (t == "eu" || t == "eu868" || t == "eu_868" || t == "uk") {
        out = Region::EU868;
        return true;
    }
    return false;
}

uint32_t djb2(const std::string& text) {
    uint32_t h = 5381;
    for (unsigned char c : text) h = ((h << 5) + h + c);
    return h;
}

double meshtastic_slot_freq(const std::string& channel_name, double bw_khz, double band_start_mhz,
                            double band_end_mhz) {
    const double spacing = bw_khz / 1000.0;
    const int num_channels = static_cast<int>(std::floor((band_end_mhz - band_start_mhz) / spacing + 1e-9));
    if (num_channels <= 0) return 0.0;
    const uint32_t slot = djb2(channel_name) % static_cast<uint32_t>(num_channels);
    const double freq = band_start_mhz + spacing / 2 + slot * spacing;
    return std::round(freq * 10000.0) / 10000.0;
}

std::vector<Profile> builtin_profiles(Region region) {
    std::vector<Profile> out;
    const Band band = band_for(region);
    if (region == Region::US) {
        // MeshCore's recommended US/Canada setting.
        out.push_back(meshcore_profile("meshcore_us", "MeshCore US", 910.525, 62.5, 7, 5));
    } else {
        // MeshCore EU/UK: the current "narrow" recommendation and the older
        // wide setting, both still common.
        out.push_back(meshcore_profile("meshcore_eu_narrow", "MeshCore EU Narrow", 869.618, 62.5, 8, 8));
        out.push_back(meshcore_profile("meshcore_eu", "MeshCore EU", 869.525, 250, 11, 5));
    }
    const std::string key_prefix = region == Region::US ? "mt_" : "mt_eu_";
    for (const auto& p : kMeshtasticPresets) {
        const double freq = meshtastic_slot_freq(p.name, p.bw, band.start, band.end);
        if (freq <= 0.0) continue;  // preset doesn't fit this band (e.g. ShortTurbo in EU868)
        Profile prof;
        prof.key = key_prefix + lower(p.name);
        prof.name = std::string("Meshtastic ") + p.name;
        prof.family = Family::Meshtastic;
        prof.freq = freq;
        prof.bw = p.bw;
        prof.sf = p.sf;
        prof.cr = p.cr;
        prof.sync = kSyncMeshtastic;
        prof.channel_name = p.name;
        out.push_back(prof);
    }
    return out;
}

std::string default_fixed_profile(Region region) {
    return region == Region::US ? "meshcore_us" : "meshcore_eu_narrow";
}

std::vector<std::string> default_hop_profiles(Region region) {
    if (region == Region::US) return {"meshcore_us", "mt_longfast", "mt_mediumfast"};
    return {"meshcore_eu_narrow", "meshcore_eu", "mt_eu_longfast"};
}

const Profile* find_profile(const std::vector<Profile>& profiles, const std::string& key) {
    for (const auto& p : profiles) {
        if (p.key == key) return &p;
    }
    return nullptr;
}

}  // namespace mesh
