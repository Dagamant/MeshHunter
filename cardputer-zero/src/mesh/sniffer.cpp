#include "sniffer.h"

#include "cap_lora_io.h"
#include "json.h"
#include "sx1262.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>

namespace mesh {
namespace {

constexpr uint64_t kRssiSampleMs = 20;
constexpr uint64_t kRssiReportMs = 200;
constexpr uint64_t kRssiSettleMs = 3;
constexpr uint64_t kMinDwellMs = 30;
constexpr int kRetryMs = 5000;

uint64_t now_ms() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch())
            .count());
}

// ---------------------------------------------------------------------------
// Cap LoRa-1262
// ---------------------------------------------------------------------------

class CapLoraSource final : public PacketSource {
public:
    explicit CapLoraSource(const CapLoraConfig& config) : io_(config) {
        status_.source = "Cap LoRa-1262";
    }
    ~CapLoraSource() override { stop(); }

    void start(PacketCallback cb) override {
        if (thread_.joinable()) return;
        callback_ = std::move(cb);
        stop_ = false;
        thread_ = std::thread([this] { run(); });
    }

    void stop() override {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stop_ = true;
        }
        wake_.notify_all();
        if (thread_.joinable()) thread_.join();
    }

    void set_profiles(std::vector<Profile> profiles, SnifferTiming timing) override {
        std::lock_guard<std::mutex> lock(mutex_);
        pending_profiles_ = std::move(profiles);
        pending_timing_ = timing;
        reconfigure_ = true;
    }

    SnifferStatus status() const override {
        std::lock_guard<std::mutex> lock(mutex_);
        return status_;
    }

private:
    struct Live {
        Profile profile;
        uint32_t rx_ok = 0, rx_crc = 0, hdr_err = 0, timeouts = 0, pre_only = 0;
        uint64_t listen_ms = 0;
        float peak = -160.0f;
    };

    enum class State { Idle, Listen, Locked };

    void set_state(RadioState s, const std::string& msg) {
        std::lock_guard<std::mutex> lock(mutex_);
        status_.state = s;
        status_.message = msg;
    }

    bool wait_ms(int ms) {
        std::unique_lock<std::mutex> lock(mutex_);
        return !wake_.wait_for(lock, std::chrono::milliseconds(ms), [this] { return stop_; });
    }

    bool stopping() {
        std::lock_guard<std::mutex> lock(mutex_);
        return stop_;
    }

    void run() {
        while (!stopping()) {
            set_state(RadioState::Starting, "Powering up the Cap...");
            try {
                io_.open();
                radio_ = std::make_unique<Sx1262>(io_, 3.0f, true, true);
                radio_->begin();
                set_state(RadioState::Listening, io_.power_notes());
                {
                    std::lock_guard<std::mutex> lock(mutex_);
                    reconfigure_ = true;  // (re)apply profiles to the fresh radio
                }
                loop();
            } catch (const RadioError& e) {
                set_state(RadioState::Error, e.what());
            }
            radio_.reset();
            io_.close();
            state_ = State::Idle;
            if (stopping()) break;
            if (!wait_ms(kRetryMs)) break;
        }
        set_state(RadioState::Stopped, "");
    }

    void apply_config() {
        std::vector<Profile> profiles;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            profiles = pending_profiles_;
            timing_ = pending_timing_;
            reconfigure_ = false;
        }
        live_.clear();
        for (auto& p : profiles) live_.push_back(Live{p});
        hops_ = 0;
        cur_ = 0;
        state_ = State::Idle;
        if (live_.empty()) {
            radio_->standby();
            return;
        }
        double fmin = live_[0].profile.freq, fmax = fmin;
        for (const auto& l : live_) {
            fmin = std::min(fmin, l.profile.freq);
            fmax = std::max(fmax, l.profile.freq);
        }
        radio_->calibrate_image(fmin - 1.0, fmax + 1.0);
        entered_at_ = now_ms();
        enter(0);
    }

    uint64_t dwell_for(const Profile& p) const {
        if (timing_.dwell_ms > 0) return std::max<uint64_t>(timing_.dwell_ms, kMinDwellMs);
        return std::max<uint64_t>(kMinDwellMs, static_cast<uint64_t>(std::ceil(p.symbol_ms() * 10)));
    }

    void enter(size_t idx) {
        const uint64_t now = now_ms();
        if (state_ != State::Idle && cur_ < live_.size()) live_[cur_].listen_ms += now - entered_at_;
        cur_ = idx;
        const Profile& p = live_[idx].profile;
        lingering_ = false;
        linger_until_ = 0;
        radio_->configure(p.freq, p.bw, p.sf, p.cr, p.sync, p.preamble);
        entered_at_ = now_ms();
        dwell_end_ = entered_at_ + dwell_for(p);
        start_rx();
    }

    void start_rx() {
        radio_->start_rx();
        state_ = State::Listen;
        lock_header_ = false;
    }

    void extend_linger(uint64_t ms) {
        if (live_.size() < 2) return;
        const uint64_t now = now_ms();
        if (!lingering_) {
            lingering_ = true;
            linger_start_ = now;
        }
        const uint64_t until = std::min(now + ms, linger_start_ + static_cast<uint64_t>(timing_.linger_max_ms));
        linger_until_ = std::max(linger_until_, until);
    }

    void poll_radio(uint64_t now) {
        Live& l = live_[cur_];
        const Profile& p = l.profile;
        // DIO1 carries every IRQ we care about; while it's low there's
        // nothing to read, so skip the SPI round trip.
        const int dio1 = io_.dio1();
        const uint16_t irq = (dio1 != 0) ? radio_->irq_status() : 0;

        if (irq & kIrqRxDone) {
            RxPacket pkt;
            pkt.profile = p;
            radio_->read_packet(pkt.data, pkt.rssi, pkt.snr);
            pkt.crc_ok = !(irq & kIrqCrcErr);
            if (pkt.crc_ok) {
                ++l.rx_ok;
                extend_linger(static_cast<uint64_t>(timing_.linger_ms));
            } else {
                ++l.rx_crc;
                extend_linger(static_cast<uint64_t>(timing_.partial_linger_ms));
            }
            start_rx();
            if (callback_) callback_(std::move(pkt));
            return;
        }
        if (irq & kIrqHeaderErr) {
            ++l.hdr_err;
            extend_linger(static_cast<uint64_t>(timing_.partial_linger_ms));
            start_rx();
            return;
        }
        if (state_ == State::Listen && (irq & (kIrqPreamble | kIrqHeaderValid))) {
            state_ = State::Locked;
            lock_header_ = false;
            // Rest of the preamble + sync + header. The detector fires early
            // in the preamble and senders' preambles vary, so allow 64
            // symbols or preamble+32, whichever is longer.
            lock_deadline_ =
                now + static_cast<uint64_t>(std::ceil(p.symbol_ms() * std::max(64, p.preamble + 32))) + 15;
        }
        if (state_ == State::Locked) {
            if (!lock_header_ && (irq & kIrqHeaderValid)) {
                lock_header_ = true;
                lock_deadline_ = now + static_cast<uint64_t>(std::ceil(time_on_air_ms(p.sf, p.bw, p.cr,
                                                                                      p.preamble, 255))) +
                                 50;
            }
            if (now > lock_deadline_) {
                if (lock_header_) {
                    ++l.timeouts;
                    extend_linger(static_cast<uint64_t>(timing_.partial_linger_ms));
                } else {
                    ++l.pre_only;
                }
                start_rx();
            }
            return;
        }
        if (live_.size() > 1) {
            const bool dwell_done = now >= dwell_end_;
            const bool linger_done = !lingering_ || now >= linger_until_;
            if (dwell_done && linger_done) {
                ++hops_;
                enter((cur_ + 1) % live_.size());
            }
        }
    }

    void publish_status(float rssi_now) {
        const uint64_t now = now_ms();
        std::lock_guard<std::mutex> lock(mutex_);
        status_.current = live_.empty() ? -1 : static_cast<int>(cur_);
        status_.locked = state_ == State::Locked;
        status_.hops = hops_;
        status_.rssi_now = rssi_now;
        status_.stats.resize(live_.size());
        for (size_t i = 0; i < live_.size(); ++i) {
            const Live& l = live_[i];
            ProfileStats& s = status_.stats[i];
            s.key = l.profile.key;
            s.rx_ok = l.rx_ok;
            s.rx_bad = l.rx_crc + l.hdr_err + l.timeouts;
            s.preamble_only = l.pre_only;
            s.listen_ms = l.listen_ms + ((i == cur_ && state_ != State::Idle) ? now - entered_at_ : 0);
            s.peak_rssi = l.peak;
        }
    }

    void loop() {
        uint64_t last_sample = 0, last_report = 0;
        float rssi_now = -160.0f;
        while (!stopping()) {
            bool reconfigure;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                reconfigure = reconfigure_;
            }
            if (reconfigure) apply_config();
            if (state_ == State::Idle) {
                publish_status(rssi_now);
                if (!wait_ms(50)) return;
                continue;
            }
            const uint64_t now = now_ms();
            poll_radio(now);
            if (now - last_sample >= kRssiSampleMs && now - entered_at_ >= kRssiSettleMs) {
                last_sample = now;
                rssi_now = radio_->rssi_inst();
                auto& peak = live_[cur_].peak;
                peak = std::max(peak, rssi_now);
            }
            if (now - last_report >= kRssiReportMs) {
                last_report = now;
                publish_status(rssi_now);
                for (auto& l : live_) l.peak = -160.0f;
            }
            std::this_thread::sleep_for(std::chrono::microseconds(800));
        }
    }

    CapLoraIo io_;
    std::unique_ptr<Sx1262> radio_;
    PacketCallback callback_;
    std::thread thread_;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    bool stop_ = false;
    bool reconfigure_ = false;
    std::vector<Profile> pending_profiles_;
    SnifferTiming pending_timing_;
    SnifferStatus status_;

    // Radio-thread-only state.
    SnifferTiming timing_;
    std::vector<Live> live_;
    State state_ = State::Idle;
    size_t cur_ = 0;
    uint64_t hops_ = 0;
    uint64_t entered_at_ = 0;
    uint64_t dwell_end_ = 0;
    bool lingering_ = false;
    uint64_t linger_start_ = 0;
    uint64_t linger_until_ = 0;
    bool lock_header_ = false;
    uint64_t lock_deadline_ = 0;
};

// ---------------------------------------------------------------------------
// Replay
// ---------------------------------------------------------------------------

class ReplaySource final : public PacketSource {
public:
    ReplaySource(std::string path, int interval_ms) : path_(std::move(path)), interval_ms_(interval_ms) {
        status_.source = "Replay: " + path_;
    }
    ~ReplaySource() override { stop(); }

    void start(PacketCallback cb) override {
        if (thread_.joinable()) return;
        callback_ = std::move(cb);
        stop_ = false;
        thread_ = std::thread([this] { run(); });
    }

    void stop() override {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stop_ = true;
        }
        wake_.notify_all();
        if (thread_.joinable()) thread_.join();
    }

    void set_profiles(std::vector<Profile> profiles, SnifferTiming) override {
        std::lock_guard<std::mutex> lock(mutex_);
        profiles_ = std::move(profiles);
        status_.stats.clear();
        for (const auto& p : profiles_) status_.stats.push_back(ProfileStats{p.key});
        status_.current = profiles_.empty() ? -1 : 0;
    }

    SnifferStatus status() const override {
        std::lock_guard<std::mutex> lock(mutex_);
        return status_;
    }

private:
    void run() {
        std::vector<std::string> lines;
        {
            std::ifstream in(path_);
            std::string line;
            while (std::getline(in, line)) {
                if (!line.empty()) lines.push_back(line);
            }
        }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (lines.empty()) {
                status_.state = RadioState::Error;
                status_.message = "No packets in " + path_;
                return;
            }
            status_.state = RadioState::Listening;
            status_.message = std::to_string(lines.size()) + " packets, replaying";
        }
        size_t i = 0;
        while (true) {
            {
                std::unique_lock<std::mutex> lock(mutex_);
                if (wake_.wait_for(lock, std::chrono::milliseconds(interval_ms_), [this] { return stop_; })) return;
            }
            const auto& line = lines[i++ % lines.size()];
            RxPacket pkt;
            std::string hex, key;
            double rssi = 0, snr = 0, sync = 0;
            bool crc = true;
            json::get_string(line, "hex", hex);
            json::get_string(line, "profile", key);
            json::get_number(line, "rssi", rssi);
            json::get_number(line, "snr", snr);
            json::get_number(line, "sync", sync);
            json::get_bool(line, "crc_ok", crc);
            if (!from_hex(hex, pkt.data)) continue;
            pkt.rssi = static_cast<float>(rssi);
            pkt.snr = static_cast<float>(snr);
            pkt.crc_ok = crc;
            int idx = -1;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                for (size_t k = 0; k < profiles_.size(); ++k) {
                    if (profiles_[k].key == key) idx = static_cast<int>(k);
                }
                if (idx >= 0) {
                    pkt.profile = profiles_[static_cast<size_t>(idx)];
                    auto& s = status_.stats[static_cast<size_t>(idx)];
                    (crc ? s.rx_ok : s.rx_bad)++;
                    s.peak_rssi = pkt.rssi;
                    status_.current = idx;
                }
                status_.rssi_now = pkt.rssi;
            }
            if (idx < 0) {
                // Packet from a profile not in the current list: keep its
                // sync word so it still decodes.
                pkt.profile.key = key;
                pkt.profile.name = key;
                pkt.profile.sync = static_cast<uint8_t>(sync);
            }
            if (callback_) callback_(std::move(pkt));
        }
    }

    std::string path_;
    int interval_ms_;
    PacketCallback callback_;
    std::thread thread_;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    bool stop_ = false;
    std::vector<Profile> profiles_;
    SnifferStatus status_;
};

}  // namespace

std::unique_ptr<PacketSource> make_cap_lora_source(const CapLoraConfig& config) {
    return std::make_unique<CapLoraSource>(config);
}

std::unique_ptr<PacketSource> make_replay_source(const std::string& jsonl_path, int interval_ms) {
    return std::make_unique<ReplaySource>(jsonl_path, interval_ms);
}

}  // namespace mesh
