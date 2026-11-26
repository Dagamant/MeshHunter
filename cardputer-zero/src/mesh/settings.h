// Application settings, stored as a small INI file (the template's format):
// /etc/meshhunter.conf holds the packaged defaults and
// ~/.config/meshhunter/meshhunter.conf the user's copy, written whenever a
// setting is changed from the UI.

#pragma once

#include "profiles.h"
#include "sniffer.h"

#include <string>
#include <vector>

namespace mesh {

// Both = the Cap and a XIAO together, sharing the profiles out.
enum class SourceKind { CapLora, Xiao, Both, Replay, None };

struct Settings {
    // [application]
    bool dark_mode = true;

    // [radio]
    SourceKind source = SourceKind::CapLora;
    Region region = Region::US;
    bool hop = true;
    std::string fixed_profile;              // empty = region default
    std::vector<std::string> hop_profiles;  // empty = region default
    // source=both: the profiles each radio listens on (a radio with one
    // stays on it, with several it hops). Both empty = share the hop list
    // out between them (see assigned_profiles()).
    std::vector<std::string> cap_profiles;
    std::vector<std::string> xiao_profiles;
    SnifferTiming timing;
    std::string spi_device = "/dev/spidev0.1";
    // XIAO sniffer serial port; "auto" = the first port whose USB product
    // is "MeshHunter Sniffer".
    std::string serial_device = "auto";
    std::string replay_file;
    int replay_interval_ms = 1500;

    // [gps]
    bool gps_enabled = true;
    std::string gps_device = "/dev/ttyS0";
    int gps_baud = 115200;

    // [storage]
    std::string data_dir;  // empty = ~/MeshHunter
    bool raw_log = true;

    // [channels] extra decode keys
    // "#hashtag" or "Name=<32 hex | base64>" for MeshCore; "Name=<base64 psk>" for Meshtastic.
    std::vector<std::string> meshcore_channels;
    std::vector<std::string> meshtastic_channels;

    // Resolved helpers.
    std::string resolved_fixed_profile() const;
    std::vector<std::string> resolved_hop_profiles() const;
    std::string resolved_data_dir() const;
    // The profiles the radio should listen on, in hop order.
    std::vector<Profile> selected_profiles() const;
    // selected_profiles() shared between `radios` radios, as the desktop
    // app's _auto_assign does: hop mode deals the hop list out round-robin;
    // fixed mode puts the fixed profile on the first radio and the next
    // hop-list profiles on the others, one each.
    std::vector<std::vector<Profile>> assigned_profiles(size_t radios) const;
    // source=both: what radio 0 (Cap) and radio 1 (XIAO) listen on, from
    // cap_profiles / xiao_profiles, or assigned_profiles(2) if both are empty.
    std::vector<Profile> radio_profiles(size_t radio) const;
};

bool load_settings(const std::string& path, Settings& settings, std::string& error);
bool save_settings(const std::string& path, const Settings& settings, std::string& error);

std::string user_config_path();  // ~/.config/meshhunter/meshhunter.conf
// Where data went before ~/MeshHunter: $XDG_DATA_HOME/meshhunter or ~/.local/share/meshhunter.
std::string legacy_data_dir();
// Moves legacy_data_dir() to `dir` if `dir` doesn't exist yet. Returns a
// note on what happened, or "" if there was nothing to move.
std::string migrate_legacy_data_dir(const std::string& dir);
const char* source_name(SourceKind kind);  // "cap", "xiao", "replay", "none"
std::string expand_home(const std::string& path);
bool make_dirs(const std::string& path);

}  // namespace mesh
