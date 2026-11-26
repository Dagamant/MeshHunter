#include "engine.h"

#include "cap_lora_io.h"
#include "json.h"

#include <algorithm>
#include <cstring>

namespace mesh {
namespace {

constexpr size_t kFeedMax = 200;
constexpr size_t kQueueMax = 256;
constexpr int kFlushIntervalS = 3;

// repeaters.csv / chat_nodes.csv / room_nodes.csv / sensor_nodes.csv; must
// match NODE_CSV_FIELDS in the desktop app's core/constants.py.
const std::vector<std::string> kNodeFields = {
    "node_id",    "node_type",  "name",       "lat",        "lon",
    "rssi",       "first_seen", "type",       "network",    "public_key",
    "last_heard", "logger_lat", "logger_lon", "uploaded_wdgwars", "uploaded_ingest",
};
const std::vector<std::string> kAllContactsFields = {
    "node_id",  "name",     "adv_type",    "type_label", "public_key", "adv_lat",    "adv_lon",
    "has_gps",  "out_path", "last_advert", "lastmod",    "first_seen", "last_heard",
};
const std::vector<std::string> kMeshtasticFields = {
    "node_id", "long_name", "short_name", "hw_model", "role",    "public_key", "lat",        "lon",       "alt",
    "rssi",    "snr",       "hops_away",  "profile",  "packets", "first_seen", "last_heard", "logger_lat", "logger_lon",
};

struct TypeInfo {
    int adv_type;
    const char* file;
    const char* wire_label;  // node_type column
};
constexpr TypeInfo kTypes[] = {
    {kAdvTypeRepeater, "repeaters.csv", "REPEATER"},
    {kAdvTypeChat, "chat_nodes.csv", "CHAT"},
    {kAdvTypeRoom, "room_nodes.csv", "ROOM"},
    {kAdvTypeSensor, "sensor_nodes.csv", "SENSOR"},
};

std::string clock_time() {
    const std::time_t now = std::time(nullptr);
    std::tm tm{};
    localtime_r(&now, &tm);
    char buf[16];
    std::strftime(buf, sizeof(buf), "%H:%M:%S", &tm);
    return buf;
}

// The strongest RSSI heard so far, as the desktop's _best_rssi() keeps it.
std::string best_rssi(const std::string& existing, float rssi) {
    if (!existing.empty()) {
        char* end = nullptr;
        const double prev = std::strtod(existing.c_str(), &end);
        if (end != existing.c_str() && prev >= rssi) return existing;
    }
    return format_float(rssi, 1);
}

std::string mc_decoded_json(const McPacket& d) {
    json::ObjectWriter w;
    w.str("proto", "meshcore")
        .integer("route_type", d.route_type)
        .str("route_typename", mc_route_name(d.route_type))
        .integer("payload_type", d.payload_type)
        .str("payload_typename", mc_payload_name(d.payload_type))
        .integer("path_len", d.path_len)
        .str("path", d.path)
        .integer("pkt_hash", d.pkt_hash);
    if (d.has_advert) {
        const auto& a = d.advert;
        w.str("adv_key", a.public_key).integer("adv_timestamp", a.timestamp).boolean("sig_ok", a.sig_ok);
        w.integer("adv_type", a.type);
        if (a.has_location) w.num("adv_lat", a.lat, 6).num("adv_lon", a.lon, 6);
        if (!a.name.empty()) w.str("adv_name", a.name);
    }
    if (!d.chan_hash.empty()) w.str("chan_hash", d.chan_hash);
    if (!d.chan_name.empty()) w.str("chan_name", d.chan_name).str("message", d.message);
    if (!d.dest_hash.empty()) w.str("dest_hash", d.dest_hash);
    if (!d.src_hash.empty()) w.str("src_hash", d.src_hash);
    if (!d.error.empty()) w.str("error", d.error);
    return w.done();
}

std::string mt_decoded_json(const MtPacket& d) {
    json::ObjectWriter w;
    w.str("proto", "meshtastic");
    if (!d.error.empty()) return w.str("error", d.error).done();
    w.str("to", d.to).str("from", d.from).integer("id", d.id).integer("hop_limit", d.hop_limit);
    if (d.hops_away) w.integer("hops_away", *d.hops_away);
    w.integer("chan_hash", d.chan_hash);
    if (d.encrypted) w.boolean("encrypted", true);
    if (d.pki) w.boolean("pki", true);
    if (!d.channel.empty()) w.str("channel", d.channel);
    if (d.port >= 0) w.integer("port", d.port).str("portname", d.portname);
    if (d.text) w.str("text", *d.text);
    if (!d.long_name.empty()) w.str("long_name", d.long_name);
    if (!d.short_name.empty()) w.str("short_name", d.short_name);
    if (!d.role.empty()) w.str("role", d.role);
    if (d.hw_model) w.integer("hw_model", static_cast<long long>(*d.hw_model));
    if (d.lat) w.num("lat", *d.lat, 7).num("lon", *d.lon, 7);
    if (d.alt) w.integer("alt", *d.alt);
    if (!d.app_error.empty()) w.str("app_error", d.app_error);
    return w.done();
}

}  // namespace

Engine::Engine(Settings settings, PersistFn persist) : settings_(std::move(settings)), persist_(std::move(persist)) {
    build_channels();
    load_tables();
}

Engine::~Engine() { stop(); }

void Engine::build_channels() {
    mc_channels_ = {mc_public_channel()};
    for (const auto& entry : settings_.meshcore_channels) {
        McChannel c;
        const auto eq = entry.find('=');
        if (eq == std::string::npos ? mc_hashtag_channel(entry, c)
                                    : mc_keyed_channel(entry.substr(0, eq), entry.substr(eq + 1), c)) {
            mc_channels_.push_back(c);
        }
    }
    // Every modem preset's default primary channel (named after the
    // preset, default key).
    mt_channels_.clear();
    for (const char* name : {"LongFast", "MediumFast", "MediumSlow", "ShortFast", "ShortSlow", "ShortTurbo",
                             "LongMod", "LongSlow"}) {
        MtChannel c;
        if (mt_channel(name, "AQ==", c)) mt_channels_.push_back(c);
    }
    for (const auto& entry : settings_.meshtastic_channels) {
        const auto eq = entry.find('=');
        MtChannel c;
        if (eq != std::string::npos && mt_channel(entry.substr(0, eq), entry.substr(eq + 1), c)) {
            mt_channels_.push_back(c);
        }
    }
}

void Engine::load_tables() {
    const std::string dir = settings_.resolved_data_dir();
    counters_.data_dir = dir;
    // The data used to live in ~/.local/share/meshhunter; bring it along
    // the first time we run with the ~/MeshHunter default.
    if (settings_.data_dir.empty()) counters_.storage_note = migrate_legacy_data_dir(dir);
    if (!make_dirs(dir)) counters_.storage_error = "Can't create " + dir + ": " + std::strerror(errno);
    for (const auto& t : kTypes) {
        auto table = std::make_unique<CsvTable>(dir + "/" + t.file, kNodeFields, "node_id", "name");
        table->load();
        mc_tables_[t.adv_type] = std::move(table);
    }
    all_contacts_ = std::make_unique<CsvTable>(dir + "/all_contacts.csv", kAllContactsFields, "node_id", "name");
    all_contacts_->load();
    mt_table_ = std::make_unique<CsvTable>(dir + "/meshtastic_nodes.csv", kMeshtasticFields, "node_id", "node_id");
    mt_table_->load();
    counters_.logged_meshcore = 0;
    for (const auto& kv : mc_tables_) counters_.logged_meshcore += kv.second->size();
    counters_.known_meshtastic = mt_table_->size();
}

void Engine::open_raw_log() {
    if (!settings_.raw_log || raw_log_) return;
    raw_log_ = std::fopen((counters_.data_dir + "/sniffer_packets.jsonl").c_str(), "a");
}

void Engine::start() {
    if (started_) return;
    started_ = true;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        open_raw_log();
        if (settings_.source == SourceKind::CapLora) {
            CapLoraConfig cfg;
            cfg.spi_device = settings_.spi_device;
            source_ = make_cap_lora_source(cfg);
        } else if (settings_.source == SourceKind::Both) {
            // Cap first: in fixed mode it takes the fixed profile.
            CapLoraConfig cfg;
            cfg.spi_device = settings_.spi_device;
            std::vector<std::unique_ptr<PacketSource>> radios;
            radios.push_back(make_cap_lora_source(cfg));
            radios.push_back(make_xiao_source(settings_.serial_device));
            auto multi = std::make_unique<MultiSource>(std::move(radios));
            multi_ = multi.get();
            source_ = std::move(multi);
        } else if (settings_.source == SourceKind::Xiao) {
            source_ = make_xiao_source(settings_.serial_device);
            // The Cap's GPS still works with a XIAO doing the listening,
            // but nothing else powers the hat in this mode.
            if (settings_.gps_enabled) cap_rail_switched_ = cap_power_rail_on();
        } else if (settings_.source == SourceKind::Replay) {
            source_ = make_replay_source(settings_.replay_file, settings_.replay_interval_ms);
        }
    }
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        stop_ = false;
    }
    worker_ = std::thread([this] { worker(); });
    if (source_) {
        apply_radio_settings();
        source_->start([this](RxPacket&& pkt) {
            {
                std::lock_guard<std::mutex> lock(queue_mutex_);
                if (queue_.size() >= kQueueMax) queue_.pop_front();
                queue_.push_back(std::move(pkt));
            }
            queue_cv_.notify_one();
        });
    }
    if (settings_.gps_enabled) gps_.start(settings_.gps_device, settings_.gps_baud);
}

void Engine::stop() {
    if (!started_) return;
    started_ = false;
    if (source_) source_->stop();
    gps_.stop();
    if (cap_rail_switched_) {
        cap_power_rail_off();
        cap_rail_switched_ = false;
    }
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        stop_ = true;
    }
    queue_cv_.notify_all();
    if (worker_.joinable()) worker_.join();
    std::lock_guard<std::mutex> lock(mutex_);
    flush_locked(true);
    if (raw_log_) {
        std::fclose(raw_log_);
        raw_log_ = nullptr;
    }
}

void Engine::worker() {
    while (true) {
        RxPacket pkt;
        bool have = false;
        {
            std::unique_lock<std::mutex> lock(queue_mutex_);
            queue_cv_.wait_for(lock, std::chrono::seconds(1), [this] { return stop_ || !queue_.empty(); });
            if (!queue_.empty()) {
                pkt = std::move(queue_.front());
                queue_.pop_front();
                have = true;
            } else if (stop_) {
                return;
            }
        }
        if (have) process_packet(pkt);
        std::lock_guard<std::mutex> lock(mutex_);
        flush_locked(false);
    }
}

void Engine::process_packet(const RxPacket& pkt) {
    std::lock_guard<std::mutex> lock(mutex_);
    ++counters_.packets;
    if (!pkt.crc_ok) {
        ++counters_.bad_crc;
        write_raw(pkt, "");
        push_feed({clock_time(), '!', "CRC error, " + std::to_string(pkt.data.size()) + " bytes", pkt.rssi, pkt.snr,
                   pkt.profile.name});
        return;
    }
    if (pkt.profile.sync == kSyncMeshCore) {
        const McPacket d = decode_meshcore(pkt.data.data(), pkt.data.size(), mc_channels_);
        write_raw(pkt, mc_decoded_json(d));
        push_feed({clock_time(), 'C', describe_meshcore(d), pkt.rssi, pkt.snr, pkt.profile.name});
        on_meshcore(pkt, d);
    } else if (pkt.profile.sync == kSyncMeshtastic) {
        const MtPacket d = decode_meshtastic(pkt.data.data(), pkt.data.size(), mt_channels_);
        write_raw(pkt, mt_decoded_json(d));
        std::string text = describe_meshtastic(d);
        if (const auto it = heard_.find(d.from); it != heard_.end() && d.long_name.empty() &&
                                                 it->second.name != it->second.id) {
            text += " (" + it->second.name + ")";
        }
        push_feed({clock_time(), 'T', text, pkt.rssi, pkt.snr, pkt.profile.name});
        on_meshtastic(pkt, d);
    } else {
        write_raw(pkt, "");
        char buf[64];
        std::snprintf(buf, sizeof(buf), "LoRa sync 0x%02x, %zu bytes", pkt.profile.sync, pkt.data.size());
        push_feed({clock_time(), '?', buf, pkt.rssi, pkt.snr, pkt.profile.name});
    }
}

void Engine::push_feed(FeedItem item) {
    feed_.push_front(std::move(item));
    if (feed_.size() > kFeedMax) feed_.pop_back();
    ++revision_;
}

void Engine::write_raw(const RxPacket& pkt, const std::string& decoded_json) {
    if (!raw_log_) return;
    json::ObjectWriter w;
    w.str("t", local_iso_timestamp_ms())
        .str("radio", pkt.radio.empty() ? source_name(settings_.source) : pkt.radio)
        .str("profile", pkt.profile.key)
        .num("freq", pkt.profile.freq)
        .num("bw", pkt.profile.bw)
        .integer("sf", pkt.profile.sf)
        .integer("cr", pkt.profile.cr)
        .integer("sync", pkt.profile.sync)
        .num("rssi", pkt.rssi, 2)
        .num("snr", pkt.snr, 2)
        .boolean("crc_ok", pkt.crc_ok)
        .str("hex", to_hex(pkt.data));
    if (const auto fix = gps_.fix()) w.num("logger_lat", fix->lat, 6).num("logger_lon", fix->lon, 6);
    if (!decoded_json.empty()) w.raw("decoded", decoded_json);
    const std::string line = w.done() + "\n";
    std::fwrite(line.data(), 1, line.size(), raw_log_);
    std::fflush(raw_log_);
}

void Engine::on_meshcore(const RxPacket& pkt, const McPacket& d) {
    if (!d.has_advert || !d.error.empty()) return;
    ++counters_.adverts;
    const McAdvert& a = d.advert;
    if (!a.sig_ok) {
        ++counters_.bad_signatures;
        return;
    }
    const std::string node_id = a.public_key.substr(0, 16);
    const bool has_coords = a.lat != 0.0 || a.lon != 0.0;

    bool known_before = false;
    for (const auto& kv : mc_tables_) known_before = known_before || kv.second->contains(node_id);

    auto [it, inserted] = heard_.try_emplace(node_id);
    NodeView& n = it->second;
    if (inserted) {
        n.id = node_id;
        n.is_new = !known_before && has_coords;
        ++counters_.heard_session;
    }
    n.name = a.name.empty() ? node_id : a.name;
    n.kind = adv_type_name(a.type);
    if (n.kind.empty()) n.kind = "Type " + std::to_string(a.type);
    n.has_gps = has_coords;
    n.lat = a.lat;
    n.lon = a.lon;
    n.has_signal = true;
    n.rssi = pkt.rssi;
    n.snr = pkt.snr;
    n.last_heard = std::time(nullptr);
    ++n.packets;
    n.detail = a.public_key.substr(0, 16);

    if (has_coords) {
        record_meshcore(a, d.path, pkt.rssi);
        if (mc_tables_.count(a.type)) {
            n.logged = true;
            if (n.is_new && inserted) ++counters_.new_session;
        }
    }
    ++revision_;
}

void Engine::record_meshcore(const McAdvert& a, const std::string& path_hex, float rssi) {
    const std::string now = local_timestamp();
    const std::string node_id = a.public_key.substr(0, 16);
    const std::string name = a.name.empty() ? node_id : a.name;
    const auto fix = gps_.fix();

    // all_contacts.csv: every type with coordinates.
    {
        bool created = false;
        CsvRow& row = all_contacts_->upsert(node_id, &created);
        if (created) row["first_seen"] = now;
        row["name"] = name;
        row["adv_type"] = std::to_string(a.type);
        row["type_label"] = adv_type_name(a.type)[0] ? adv_type_name(a.type) : std::to_string(a.type);
        row["public_key"] = a.public_key;
        row["adv_lat"] = format_float(a.lat, 6);
        row["adv_lon"] = format_float(a.lon, 6);
        row["has_gps"] = "yes";
        row["out_path"] = path_hex;
        row["last_advert"] = std::to_string(a.timestamp);
        row["lastmod"] = "";
        row["last_heard"] = now;
    }

    const auto table_it = mc_tables_.find(a.type);
    if (table_it == mc_tables_.end()) return;
    const char* wire_label = "";
    for (const auto& t : kTypes) {
        if (t.adv_type == a.type) wire_label = t.wire_label;
    }
    bool created = false;
    CsvRow& row = table_it->second->upsert(node_id, &created);
    if (created) {
        // Where *we* were when this node was first logged; a known node
        // keeps its original logger location.
        row["first_seen"] = now;
        row["logger_lat"] = fix ? format_float(fix->lat, 6) : "";
        row["logger_lon"] = fix ? format_float(fix->lon, 6) : "";
        ++counters_.logged_meshcore;
    }
    row["node_type"] = wire_label;
    row["name"] = name;
    row["lat"] = format_float(a.lat, 6);
    row["lon"] = format_float(a.lon, 6);
    row["rssi"] = best_rssi(row["rssi"], rssi);
    row["type"] = "MESHCORE";
    row["network"] = "meshcore";
    row["public_key"] = a.public_key;
    row["last_heard"] = now;
    // New rows are flushed straight away; updates ride the debounce.
    if (created) flush_locked(true);
}

void Engine::on_meshtastic(const RxPacket& pkt, const MtPacket& d) {
    if (!d.error.empty() || d.from.empty() || d.from_num == 0 || d.from_num == kMtBroadcast) return;
    const std::string now = local_timestamp();
    const auto fix = gps_.fix();
    bool created = false;
    CsvRow& row = mt_table_->upsert(d.from, &created);
    if (created) {
        row["first_seen"] = now;
        row["packets"] = "0";
        row["logger_lat"] = fix ? format_float(fix->lat, 6) : "";
        row["logger_lon"] = fix ? format_float(fix->lon, 6) : "";
        counters_.known_meshtastic = mt_table_->size();
    }
    row["packets"] = std::to_string(std::atoi(row["packets"].c_str()) + 1);
    row["last_heard"] = now;
    row["profile"] = pkt.profile.key;
    // Signal/hop data only describes *this* node when we heard it directly
    // (0 hops); a relayed packet's RSSI is the relayer's.
    const bool direct = !d.hops_away || *d.hops_away == 0;
    if (direct) {
        row["rssi"] = format_float(pkt.rssi, 2);
        row["snr"] = format_float(pkt.snr, 2);
    }
    if (d.hops_away) row["hops_away"] = std::to_string(*d.hops_away);
    if (!d.long_name.empty()) row["long_name"] = d.long_name;
    if (!d.short_name.empty()) row["short_name"] = d.short_name;
    if (d.hw_model) row["hw_model"] = std::to_string(*d.hw_model);
    if (!d.role.empty()) row["role"] = d.role;
    if (!d.public_key.empty()) row["public_key"] = d.public_key;
    if (d.lat) {
        row["lat"] = format_float(*d.lat, 7);
        row["lon"] = format_float(*d.lon, 7);
    }
    if (d.alt) row["alt"] = std::to_string(*d.alt);
    if (created) flush_locked(true);

    auto [it, inserted] = heard_.try_emplace(d.from);
    NodeView& n = it->second;
    if (inserted) {
        n.id = d.from;
        n.meshtastic = true;
        n.kind = "Meshtastic";
        n.is_new = created;
        n.logged = true;
        ++counters_.heard_session;
        if (created) ++counters_.new_session;
    }
    const std::string& long_name = row["long_name"];
    n.name = long_name.empty() ? d.from : long_name;
    if (!row["lat"].empty()) {
        n.has_gps = true;
        n.lat = std::atof(row["lat"].c_str());
        n.lon = std::atof(row["lon"].c_str());
    }
    if (direct) {
        n.has_signal = true;
        n.rssi = pkt.rssi;
        n.snr = pkt.snr;
    }
    n.last_heard = std::time(nullptr);
    ++n.packets;
    n.detail = row["role"];
    ++revision_;
}

void Engine::flush_locked(bool force) {
    const std::time_t now = std::time(nullptr);
    if (!force && now - last_flush_ < kFlushIntervalS) return;
    last_flush_ = now;
    std::string error;
    auto save = [&](CsvTable& t) {
        if (t.dirty() && !t.save(&error)) counters_.storage_error = error;
    };
    for (auto& kv : mc_tables_) save(*kv.second);
    save(*all_contacts_);
    save(*mt_table_);
}

void Engine::flush() {
    std::lock_guard<std::mutex> lock(mutex_);
    flush_locked(true);
}

std::vector<NodeView> Engine::nodes() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<NodeView> out;
    out.reserve(heard_.size());
    for (const auto& kv : heard_) out.push_back(kv.second);
    std::sort(out.begin(), out.end(), [](const NodeView& a, const NodeView& b) {
        if (a.last_heard != b.last_heard) return a.last_heard > b.last_heard;
        return a.name < b.name;
    });
    return out;
}

std::vector<FeedItem> Engine::feed() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return {feed_.begin(), feed_.end()};
}

SnifferStatus Engine::radio() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!source_) {
        SnifferStatus s;
        s.source = "No radio";
        s.message = "Radio disabled in settings (source=none)";
        return s;
    }
    return source_->status();
}

GpsStatus Engine::gps() const { return gps_.status(); }

EngineCounters Engine::counters() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return counters_;
}

Settings Engine::settings() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return settings_;
}

std::vector<Profile> Engine::region_profiles() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return builtin_profiles(settings_.region);
}

void Engine::apply_radio_settings() {
    std::vector<Profile> profiles;
    SnifferTiming timing;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        profiles = settings_.selected_profiles();
        timing = settings_.timing;
    }
    if (multi_) {
        std::vector<std::vector<Profile>> shares;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            for (size_t i = 0; i < multi_->radio_count(); ++i) shares.push_back(settings_.radio_profiles(i));
        }
        multi_->set_assignment(shares, timing);
    } else if (source_) {
        source_->set_profiles(std::move(profiles), timing);
    }
    ++revision_;
}

void Engine::persist_settings() {
    Settings copy;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        copy = settings_;
    }
    if (persist_) persist_(copy);
}

void Engine::set_hop(bool hop) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        settings_.hop = hop;
    }
    apply_radio_settings();
    persist_settings();
}

void Engine::set_fixed_profile(const std::string& key) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        settings_.fixed_profile = key;
    }
    apply_radio_settings();
    persist_settings();
}

void Engine::toggle_hop_profile(const std::string& key) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto keys = settings_.resolved_hop_profiles();
        const auto it = std::find(keys.begin(), keys.end(), key);
        if (it != keys.end()) {
            if (keys.size() == 1) return;  // keep at least one
            keys.erase(it);
        } else {
            keys.push_back(key);
        }
        settings_.hop_profiles = keys;
    }
    apply_radio_settings();
    persist_settings();
}

void Engine::cycle_radio_profile(const std::string& key) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        // Start from what the radios are on now (the automatic split the
        // first time), so one change doesn't reshuffle the rest.
        std::vector<std::string> cap, xiao;
        for (const auto& p : settings_.radio_profiles(0)) cap.push_back(p.key);
        for (const auto& p : settings_.radio_profiles(1)) xiao.push_back(p.key);
        const auto in = [&](std::vector<std::string>& v) { return std::find(v.begin(), v.end(), key) != v.end(); };
        const auto drop = [&](std::vector<std::string>& v) { v.erase(std::remove(v.begin(), v.end(), key), v.end()); };
        if (in(cap)) {
            drop(cap);
            xiao.push_back(key);
        } else if (in(xiao)) {
            drop(xiao);
        } else {
            cap.push_back(key);
        }
        settings_.cap_profiles = cap;
        settings_.xiao_profiles = xiao;
    }
    apply_radio_settings();
    persist_settings();
}

void Engine::set_region(Region region) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (settings_.region == region) return;
        settings_.region = region;
        settings_.fixed_profile.clear();
        settings_.hop_profiles.clear();
        settings_.cap_profiles.clear();
        settings_.xiao_profiles.clear();
    }
    apply_radio_settings();
    persist_settings();
}

void Engine::set_dark_mode(bool dark) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (settings_.dark_mode == dark) return;
        settings_.dark_mode = dark;
    }
    persist_settings();
}

}  // namespace mesh
