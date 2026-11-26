#include "sniffer.h"

namespace mesh {

MultiSource::MultiSource(std::vector<std::unique_ptr<PacketSource>> radios) : radios_(std::move(radios)) {}

MultiSource::~MultiSource() { stop(); }

void MultiSource::start(PacketCallback cb) {
    // The callback only queues the packet under the engine's lock, so the
    // radios' threads can share it.
    for (auto& r : radios_) r->start(cb);
}

void MultiSource::stop() {
    for (auto& r : radios_) r->stop();
}

void MultiSource::set_profiles(std::vector<Profile> profiles, SnifferTiming timing) {
    std::vector<std::vector<Profile>> shares(radios_.size());
    for (size_t i = 0; i < profiles.size() && !shares.empty(); ++i) shares[i % shares.size()].push_back(profiles[i]);
    set_assignment(shares, timing);
}

void MultiSource::set_assignment(const std::vector<std::vector<Profile>>& shares, SnifferTiming timing) {
    for (size_t i = 0; i < radios_.size(); ++i) {
        radios_[i]->set_profiles(i < shares.size() ? shares[i] : std::vector<Profile>{}, timing);
    }
}

SnifferStatus MultiSource::status() const {
    SnifferStatus out;
    bool any_error = false, any_starting = false, any_running = false;
    std::string problems;
    for (size_t i = 0; i < radios_.size(); ++i) {
        const SnifferStatus s = radios_[i]->status();
        if (!out.source.empty()) out.source += " + ";
        out.source += s.source;
        any_error = any_error || s.state == RadioState::Error;
        any_starting = any_starting || s.state == RadioState::Starting;
        any_running = any_running || s.state != RadioState::Stopped;
        if ((s.state == RadioState::Error || s.state == RadioState::Starting) && !s.message.empty()) {
            if (!problems.empty()) problems += "  ";
            problems += s.source + ": " + s.message;
        }
        if (s.current >= 0 && s.current < static_cast<int>(s.stats.size())) {
            const std::string& key = s.stats[static_cast<size_t>(s.current)].key;
            out.current_keys.push_back(key);
            if (out.current < 0) out.current = static_cast<int>(out.stats.size()) + s.current;
        }
        out.locked = out.locked || s.locked;
        out.hops += s.hops;
        if (out.rssi_now <= -159.0f) out.rssi_now = s.rssi_now;
        out.stats.insert(out.stats.end(), s.stats.begin(), s.stats.end());
    }
    out.state = any_error      ? RadioState::Error
                : any_starting ? RadioState::Starting
                : any_running  ? RadioState::Listening
                               : RadioState::Stopped;
    out.message = problems;
    return out;
}

}  // namespace mesh
