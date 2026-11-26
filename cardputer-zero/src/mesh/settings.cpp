#include "settings.h"

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <sys/stat.h>

namespace mesh {
namespace {

std::string trim(std::string v) {
    const auto not_space = [](unsigned char c) { return !std::isspace(c); };
    v.erase(v.begin(), std::find_if(v.begin(), v.end(), not_space));
    v.erase(std::find_if(v.rbegin(), v.rend(), not_space).base(), v.end());
    return v;
}

std::string lower(std::string v) {
    std::transform(v.begin(), v.end(), v.begin(), [](unsigned char c) { return std::tolower(c); });
    return v;
}

bool parse_bool(const std::string& v, bool def) {
    const auto n = lower(trim(v));
    if (n == "yes" || n == "true" || n == "1" || n == "on") return true;
    if (n == "no" || n == "false" || n == "0" || n == "off") return false;
    return def;
}

int parse_int(const std::string& v, int def) {
    char* end = nullptr;
    const long n = std::strtol(v.c_str(), &end, 10);
    return end == v.c_str() ? def : static_cast<int>(n);
}

std::vector<std::string> split_list(const std::string& v, char sep) {
    std::vector<std::string> out;
    std::stringstream ss(v);
    std::string item;
    while (std::getline(ss, item, sep)) {
        item = trim(item);
        if (!item.empty()) out.push_back(item);
    }
    return out;
}

std::string join(const std::vector<std::string>& items, const char* sep) {
    std::string out;
    for (size_t i = 0; i < items.size(); ++i) {
        if (i) out += sep;
        out += items[i];
    }
    return out;
}

std::string home_dir() {
    const char* home = std::getenv("HOME");
    return home && *home ? home : "/tmp";
}

}  // namespace

std::string expand_home(const std::string& path) {
    if (!path.empty() && path[0] == '~') return home_dir() + path.substr(1);
    return path;
}

bool make_dirs(const std::string& path) {
    if (path.empty()) return false;
    std::string cur;
    for (size_t i = 0; i < path.size(); ++i) {
        cur.push_back(path[i]);
        if (path[i] == '/' || i + 1 == path.size()) {
            if (cur == "/") continue;
            if (::mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST) return false;
        }
    }
    return true;
}

std::string user_config_path() {
    const char* xdg = std::getenv("XDG_CONFIG_HOME");
    const std::string root = xdg && xdg[0] == '/' ? xdg : home_dir() + "/.config";
    return root + "/meshhunter/meshhunter.conf";
}

std::string Settings::resolved_fixed_profile() const {
    const auto profiles = builtin_profiles(region);
    if (!fixed_profile.empty() && find_profile(profiles, fixed_profile)) return fixed_profile;
    return default_fixed_profile(region);
}

std::vector<std::string> Settings::resolved_hop_profiles() const {
    const auto profiles = builtin_profiles(region);
    std::vector<std::string> out;
    for (const auto& k : hop_profiles) {
        if (find_profile(profiles, k)) out.push_back(k);
    }
    return out.empty() ? default_hop_profiles(region) : out;
}

std::string Settings::resolved_data_dir() const {
    if (!data_dir.empty()) return expand_home(data_dir);
    return home_dir() + "/MeshHunter";
}

std::string legacy_data_dir() {
    const char* xdg = std::getenv("XDG_DATA_HOME");
    const std::string root = xdg && xdg[0] == '/' ? xdg : home_dir() + "/.local/share";
    return root + "/meshhunter";
}

std::string migrate_legacy_data_dir(const std::string& dir) {
    const std::string old = legacy_data_dir();
    struct stat st {};
    if (::stat(dir.c_str(), &st) == 0) return "";  // already there
    if (::stat(old.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) return "";
    const auto slash = dir.rfind('/');
    if (slash != std::string::npos && slash > 0) make_dirs(dir.substr(0, slash));
    if (std::rename(old.c_str(), dir.c_str()) != 0) {
        return "Couldn't move " + old + " to " + dir + ": " + std::strerror(errno);
    }
    return "Moved " + old + " to " + dir;
}

const char* source_name(SourceKind kind) {
    switch (kind) {
        case SourceKind::Xiao: return "xiao";
        case SourceKind::Replay: return "replay";
        case SourceKind::None: return "none";
        case SourceKind::CapLora: break;
    }
    return "cap";
}

std::vector<Profile> Settings::selected_profiles() const {
    const auto all = builtin_profiles(region);
    std::vector<Profile> out;
    if (hop) {
        // Hop in display order so the radio page reads top-to-bottom.
        const auto keys = resolved_hop_profiles();
        for (const auto& p : all) {
            if (std::find(keys.begin(), keys.end(), p.key) != keys.end()) out.push_back(p);
        }
    } else if (const auto* p = find_profile(all, resolved_fixed_profile())) {
        out.push_back(*p);
    }
    return out;
}

bool load_settings(const std::string& path, Settings& s, std::string& error) {
    std::ifstream in(path);
    if (!in) {
        error = std::string("cannot open: ") + std::strerror(errno);
        return false;
    }
    std::string section;
    std::string line;
    while (std::getline(in, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#' || line[0] == ';') continue;
        if (line.front() == '[' && line.back() == ']') {
            section = lower(trim(line.substr(1, line.size() - 2)));
            continue;
        }
        const auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        const std::string key = lower(trim(line.substr(0, eq)));
        const std::string value = trim(line.substr(eq + 1));

        if (section == "application") {
            if (key == "dark_mode") s.dark_mode = parse_bool(value, s.dark_mode);
        } else if (section == "radio") {
            if (key == "source") {
                const auto v = lower(value);
                s.source = v == "xiao"     ? SourceKind::Xiao
                           : v == "replay" ? SourceKind::Replay
                           : v == "none"   ? SourceKind::None
                                           : SourceKind::CapLora;
            } else if (key == "region") {
                parse_region(value, s.region);
            } else if (key == "mode") {
                s.hop = lower(value) != "fixed";
            } else if (key == "fixed_profile") {
                s.fixed_profile = value;
            } else if (key == "hop_profiles") {
                s.hop_profiles = split_list(value, ',');
            } else if (key == "dwell_ms") {
                s.timing.dwell_ms = parse_int(value, s.timing.dwell_ms);
            } else if (key == "linger_ms") {
                s.timing.linger_ms = parse_int(value, s.timing.linger_ms);
            } else if (key == "partial_linger_ms") {
                s.timing.partial_linger_ms = parse_int(value, s.timing.partial_linger_ms);
            } else if (key == "linger_max_ms") {
                s.timing.linger_max_ms = parse_int(value, s.timing.linger_max_ms);
            } else if (key == "spi_device") {
                s.spi_device = value;
            } else if (key == "serial_device") {
                s.serial_device = value.empty() ? "auto" : value;
            } else if (key == "replay_file") {
                s.replay_file = expand_home(value);
            } else if (key == "replay_interval_ms") {
                s.replay_interval_ms = std::max(50, parse_int(value, s.replay_interval_ms));
            }
        } else if (section == "gps") {
            if (key == "enabled") s.gps_enabled = parse_bool(value, s.gps_enabled);
            else if (key == "device") s.gps_device = value;
            else if (key == "baud") s.gps_baud = parse_int(value, s.gps_baud);
        } else if (section == "storage") {
            if (key == "data_dir") s.data_dir = value;
            else if (key == "raw_log") s.raw_log = parse_bool(value, s.raw_log);
        } else if (section == "channels") {
            if (key == "meshcore") s.meshcore_channels = split_list(value, ',');
            else if (key == "meshtastic") s.meshtastic_channels = split_list(value, ',');
        }
    }
    return true;
}

bool save_settings(const std::string& path, const Settings& s, std::string& error) {
    const auto slash = path.rfind('/');
    if (slash != std::string::npos) make_dirs(path.substr(0, slash));
    std::ostringstream out;
    out << "# MeshHunter for CardputerZero. Written by the app when settings change.\n\n";
    out << "[application]\n";
    out << "dark_mode=" << (s.dark_mode ? "yes" : "no") << "\n\n";
    out << "[radio]\n";
    out << "# cap = Cap LoRa-1262, xiao = XIAO sniffer over USB, replay = play back replay_file, none = no radio\n";
    out << "source=" << source_name(s.source) << "\n";
    out << "# US or EU868\n";
    out << "region=" << region_name(s.region) << "\n";
    out << "mode=" << (s.hop ? "hop" : "fixed") << "\n";
    out << "fixed_profile=" << s.fixed_profile << "\n";
    out << "hop_profiles=" << join(s.hop_profiles, ",") << "\n";
    out << "dwell_ms=" << s.timing.dwell_ms << "\n";
    out << "linger_ms=" << s.timing.linger_ms << "\n";
    out << "partial_linger_ms=" << s.timing.partial_linger_ms << "\n";
    out << "linger_max_ms=" << s.timing.linger_max_ms << "\n";
    out << "spi_device=" << s.spi_device << "\n";
    out << "# XIAO sniffer port, or auto\n";
    out << "serial_device=" << s.serial_device << "\n";
    out << "replay_file=" << s.replay_file << "\n";
    out << "replay_interval_ms=" << s.replay_interval_ms << "\n\n";
    out << "[gps]\n";
    out << "enabled=" << (s.gps_enabled ? "yes" : "no") << "\n";
    out << "device=" << s.gps_device << "\n";
    out << "baud=" << s.gps_baud << "\n\n";
    out << "[storage]\n";
    out << "# empty = ~/MeshHunter\n";
    out << "data_dir=" << s.data_dir << "\n";
    out << "raw_log=" << (s.raw_log ? "yes" : "no") << "\n\n";
    out << "[channels]\n";
    out << "# MeshCore: #hashtag or Name=<32 hex key>; Meshtastic: Name=<base64 psk>\n";
    out << "meshcore=" << join(s.meshcore_channels, ",") << "\n";
    out << "meshtastic=" << join(s.meshtastic_channels, ",") << "\n";

    const std::string tmp = path + ".tmp";
    {
        std::ofstream f(tmp, std::ios::trunc);
        if (!f) {
            error = std::string("cannot write: ") + std::strerror(errno);
            return false;
        }
        f << out.str();
        if (!f) {
            error = "write failed";
            return false;
        }
    }
    if (std::rename(tmp.c_str(), path.c_str()) != 0) {
        error = std::string("rename failed: ") + std::strerror(errno);
        return false;
    }
    return true;
}

}  // namespace mesh
