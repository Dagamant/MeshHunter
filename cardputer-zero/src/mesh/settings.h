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

enum class SourceKind { CapLora, Replay, None };

struct Settings {
    // [application]
    bool dark_mode = true;

    // [radio]
    SourceKind source = SourceKind::CapLora;
    Region region = Region::US;
    bool hop = true;
    std::string fixed_profile;              // empty = region default
    std::vector<std::string> hop_profiles;  // empty = region default
    SnifferTiming timing;
    std::string spi_device = "/dev/spidev0.1";
    std::string replay_file;
    int replay_interval_ms = 1500;

    // [gps]
    bool gps_enabled = true;
    std::string gps_device = "/dev/ttyS0";
    int gps_baud = 115200;

    // [storage]
    std::string data_dir;  // empty = $XDG_DATA_HOME/meshhunter or ~/.local/share/meshhunter
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
};

bool load_settings(const std::string& path, Settings& settings, std::string& error);
bool save_settings(const std::string& path, const Settings& settings, std::string& error);

std::string user_config_path();  // ~/.config/meshhunter/meshhunter.conf
std::string expand_home(const std::string& path);
bool make_dirs(const std::string& path);

}  // namespace mesh
