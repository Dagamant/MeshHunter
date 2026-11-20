// The sniffer receive loop: a port of meshhunter/core/usb_sniffer.py (itself
// a port of firmware/sniffer/src/main.cpp) driving the Cap's SX1262.
//
// Listen on one profile; when the radio detects a preamble/header, lock on
// until the packet completes (or times out); after traffic, linger on that
// profile a while; otherwise hop to the next profile once its dwell is up.
//
// Runs on its own thread. If the radio can't be opened or fails it reports
// the error in status() and retries every few seconds.

#pragma once

#include "bytes.h"
#include "profiles.h"

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace mesh {

struct SnifferTiming {
    int dwell_ms = 0;  // 0 = auto (10 symbols, min 30 ms)
    int linger_ms = 3000;
    int partial_linger_ms = 2000;
    int linger_max_ms = 12000;
};

struct RxPacket {
    Profile profile;
    Bytes data;
    float rssi = 0.0f;
    float snr = 0.0f;
    bool crc_ok = false;
};

struct ProfileStats {
    std::string key;
    uint32_t rx_ok = 0;
    uint32_t rx_bad = 0;  // CRC + header errors + timeouts
    uint32_t preamble_only = 0;
    uint64_t listen_ms = 0;
    float peak_rssi = -160.0f;  // strongest sample in the last report window
};

enum class RadioState { Stopped, Starting, Listening, Error };

struct SnifferStatus {
    RadioState state = RadioState::Stopped;
    std::string source;   // "Cap LoRa-1262", "Replay: file"
    std::string message;  // error text or power-up notes
    int current = -1;     // index into stats
    bool locked = false;
    uint64_t hops = 0;
    float rssi_now = -160.0f;
    std::vector<ProfileStats> stats;
};

// Common interface so the desktop build can replay a captured
// sniffer_packets.jsonl through exactly the same engine.
class PacketSource {
public:
    using PacketCallback = std::function<void(RxPacket&&)>;
    virtual ~PacketSource() = default;
    virtual void start(PacketCallback cb) = 0;
    virtual void stop() = 0;
    // Thread-safe; takes effect on the next loop iteration.
    virtual void set_profiles(std::vector<Profile> profiles, SnifferTiming timing) = 0;
    virtual SnifferStatus status() const = 0;
};

struct CapLoraConfig;

std::unique_ptr<PacketSource> make_cap_lora_source(const CapLoraConfig& config);
// Replays the "hex"/"rssi"/"snr"/"crc_ok"/"profile" fields of each line,
// one packet every `interval_ms`, looping forever.
std::unique_ptr<PacketSource> make_replay_source(const std::string& jsonl_path, int interval_ms);

}  // namespace mesh
