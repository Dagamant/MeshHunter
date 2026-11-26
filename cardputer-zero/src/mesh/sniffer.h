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
    std::string radio;  // which radio heard it: "cap", "xiao", "replay"
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
    std::string source;   // "Cap LoRa-1262", "XIAO ttyACM0", "Replay: file"
    std::string message;  // error text or power-up notes
    int current = -1;     // index into stats
    // Profile key each radio is on right now (several with source=both).
    std::vector<std::string> current_keys;
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

// Several radios as one source (source=both: the Cap and a XIAO). Each
// radio listens on its own share of the profiles; packets from all of them
// go to the one callback, and status() merges theirs.
class MultiSource final : public PacketSource {
public:
    explicit MultiSource(std::vector<std::unique_ptr<PacketSource>> radios);
    ~MultiSource() override;
    void start(PacketCallback cb) override;
    void stop() override;
    // Deals `profiles` out round-robin. Prefer set_assignment().
    void set_profiles(std::vector<Profile> profiles, SnifferTiming timing) override;
    // One profile list per radio, in the order they were given.
    void set_assignment(const std::vector<std::vector<Profile>>& shares, SnifferTiming timing);
    SnifferStatus status() const override;
    size_t radio_count() const { return radios_.size(); }

private:
    std::vector<std::unique_ptr<PacketSource>> radios_;
};

struct CapLoraConfig;

std::unique_ptr<PacketSource> make_cap_lora_source(const CapLoraConfig& config);
// A Seeed XIAO nRF52840 + Wio-SX1262 running firmware/sniffer, over USB
// serial. `device` is a tty path, or "auto" to pick the first port whose USB
// product is "MeshHunter Sniffer". The firmware does the hop/lock/linger
// loop itself; this only configures it and reads its JSON lines.
std::unique_ptr<PacketSource> make_xiao_source(const std::string& device);
// The first connected XIAO sniffer's tty ("/dev/ttyACM0"), or "" if none
// (Linux only: reads the USB product string from sysfs).
std::string find_xiao_sniffer_port();
// Replays the "hex"/"rssi"/"snr"/"crc_ok"/"profile" fields of each line,
// one packet every `interval_ms`, looping forever.
std::unique_ptr<PacketSource> make_replay_source(const std::string& jsonl_path, int interval_ms);

}  // namespace mesh
