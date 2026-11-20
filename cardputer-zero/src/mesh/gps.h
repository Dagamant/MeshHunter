// GPS for "where was I when I heard this node" (the logger_lat/logger_lon
// CSV columns). The Cap LoRa-1262's ATGM336H talks NMEA at 115200 8N1 on
// the CardputerZero's /dev/ttyS0 (serial0).

#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

namespace mesh {

struct GpsFix {
    double lat = 0.0;
    double lon = 0.0;
};

struct GpsStatus {
    bool enabled = false;
    bool port_open = false;
    bool receiving = false;  // valid NMEA seen in the last few seconds
    bool has_fix = false;
    double lat = 0.0;
    double lon = 0.0;
    double alt_m = 0.0;
    int satellites = 0;
    double hdop = 0.0;
    double speed_kmh = 0.0;
    std::string utc_time;  // "HH:MM:SS"
    std::string utc_date;  // "YYYY-MM-DD"
    std::string device;
    std::string message;   // error / antenna notes
    double fix_age_s = -1.0;
};

// --- pure parsing (unit-tested) -------------------------------------------

bool nmea_checksum_ok(const std::string& sentence);

struct NmeaUpdate {
    bool is_gga = false;
    bool is_rmc = false;
    bool valid = false;  // GGA quality > 0 / RMC status 'A'
    std::optional<double> lat, lon, alt_m, hdop, speed_kmh;
    std::optional<int> satellites;
    std::string utc_time, utc_date;
    std::string text;  // $xxTXT payload (e.g. "ANTENNA OPEN")
};

// Parses GGA, RMC and TXT sentences with any talker id. Returns false for
// anything else or a bad checksum.
bool parse_nmea(const std::string& sentence, NmeaUpdate& out);

// --- reader thread ----------------------------------------------------------

class GpsReader {
public:
    GpsReader() = default;
    ~GpsReader();

    // device "auto" tries /dev/serial0 then /dev/ttyS0.
    void start(const std::string& device, int baud);
    void stop();

    GpsStatus status() const;
    // Current fix if it's fresher than max_age_s.
    std::optional<GpsFix> fix(double max_age_s = 10.0) const;

    // Feed one sentence directly (tests / replay).
    void feed(const std::string& sentence);

private:
    void run();
    bool wait_ms(int ms);

    std::string device_;
    int baud_ = 115200;
    std::thread thread_;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    bool stop_ = false;
    GpsStatus status_;
    std::chrono::steady_clock::time_point last_fix_{};
    std::chrono::steady_clock::time_point last_sentence_{};
};

}  // namespace mesh
