// The MeshHunter engine: owns the packet source (Cap LoRa-1262 or replay),
// the GPS reader and the node tables, decodes every packet on a worker
// thread and records what it hears exactly like the desktop app's
// SnifferWorker does:
//
// - MeshCore adverts with valid signatures and real coordinates ->
//   repeaters.csv / chat_nodes.csv / room_nodes.csv / sensor_nodes.csv and
//   all_contacts.csv (same columns, so the desktop app can upload them)
// - Meshtastic nodes -> meshtastic_nodes.csv
// - every packet, raw + decoded -> sniffer_packets.jsonl
//
// The UI thread only ever reads snapshots (nodes(), feed(), radio(), ...)
// and polls revision() to know when to refresh.

#pragma once

#include "csv_table.h"
#include "gps.h"
#include "meshcore.h"
#include "meshtastic.h"
#include "settings.h"
#include "sniffer.h"

#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <ctime>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace mesh {

struct NodeView {
    std::string id;    // MeshCore node_id (16 hex) or Meshtastic "!xxxxxxxx"
    std::string name;
    std::string kind;  // "Repeater", "Chat", "Room", "Sensor", "Meshtastic"
    bool meshtastic = false;
    bool has_gps = false;
    double lat = 0.0;
    double lon = 0.0;
    bool has_signal = false;
    float rssi = 0.0f;
    float snr = 0.0f;
    std::time_t last_heard = 0;
    uint32_t packets = 0;
    bool logged = false;  // written to a CSV
    bool is_new = false;  // first time ever (not in the CSVs before this session)
    std::string detail;   // hw/role for Meshtastic, public key prefix for MeshCore
};

struct FeedItem {
    std::string time;  // HH:MM:SS
    char proto = '?';  // 'C' MeshCore, 'T' Meshtastic, '?' other sync word, '!' bad CRC
    std::string text;
    float rssi = 0.0f;
    float snr = 0.0f;
    std::string profile;
};

struct EngineCounters {
    uint64_t packets = 0;
    uint64_t bad_crc = 0;
    uint64_t adverts = 0;
    uint64_t bad_signatures = 0;
    size_t logged_meshcore = 0;  // rows across the four node CSVs
    size_t known_meshtastic = 0;
    size_t heard_session = 0;
    size_t new_session = 0;
    std::string data_dir;
    std::string storage_error;
};

class Engine {
public:
    using PersistFn = std::function<void(const Settings&)>;

    Engine(Settings settings, PersistFn persist);
    ~Engine();

    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    void start();
    void stop();

    uint32_t revision() const { return revision_.load(); }
    std::vector<NodeView> nodes() const;  // most recently heard first
    std::vector<FeedItem> feed() const;   // newest first
    SnifferStatus radio() const;
    GpsStatus gps() const;
    EngineCounters counters() const;

    Settings settings() const;
    std::vector<Profile> region_profiles() const;
    void set_hop(bool hop);
    void set_fixed_profile(const std::string& key);
    void toggle_hop_profile(const std::string& key);
    void set_region(Region region);
    void set_dark_mode(bool dark);

    // Synchronously decode/record one packet (tests).
    void process_packet(const RxPacket& pkt);
    void flush();

private:
    void worker();
    void apply_radio_settings();
    void persist_settings();
    void load_tables();
    void open_raw_log();
    void build_channels();

    void on_meshcore(const RxPacket& pkt, const McPacket& d);
    void on_meshtastic(const RxPacket& pkt, const MtPacket& d);
    void record_meshcore(const McAdvert& a, const std::string& path_hex, float rssi);
    void push_feed(FeedItem item);
    void write_raw(const RxPacket& pkt, const std::string& decoded_json);
    void flush_locked(bool force);

    mutable std::mutex mutex_;  // guards everything below except the queue
    Settings settings_;
    PersistFn persist_;
    std::unique_ptr<PacketSource> source_;
    GpsReader gps_;

    std::vector<McChannel> mc_channels_;
    std::vector<MtChannel> mt_channels_;

    std::map<int, std::unique_ptr<CsvTable>> mc_tables_;  // by adv type
    std::unique_ptr<CsvTable> all_contacts_;
    std::unique_ptr<CsvTable> mt_table_;
    std::FILE* raw_log_ = nullptr;
    std::time_t last_flush_ = 0;

    std::map<std::string, NodeView> heard_;
    std::deque<FeedItem> feed_;
    EngineCounters counters_;
    std::atomic<uint32_t> revision_{0};

    // Packet queue (radio thread -> worker).
    std::mutex queue_mutex_;
    std::condition_variable queue_cv_;
    std::deque<RxPacket> queue_;
    bool stop_ = false;
    std::thread worker_;
    bool started_ = false;
};

}  // namespace mesh
