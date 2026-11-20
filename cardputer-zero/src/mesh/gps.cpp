#include "gps.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <vector>

#if defined(__linux__)
#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>
#endif

namespace mesh {
namespace {

std::vector<std::string> split_fields(const std::string& body) {
    std::vector<std::string> out;
    size_t start = 0;
    while (true) {
        const size_t comma = body.find(',', start);
        if (comma == std::string::npos) {
            out.push_back(body.substr(start));
            return out;
        }
        out.push_back(body.substr(start, comma - start));
        start = comma + 1;
    }
}

std::optional<double> to_double(const std::string& s) {
    if (s.empty()) return std::nullopt;
    char* end = nullptr;
    const double v = std::strtod(s.c_str(), &end);
    if (end == s.c_str()) return std::nullopt;
    return v;
}

// ddmm.mmmm + hemisphere -> signed degrees.
std::optional<double> nmea_coord(const std::string& value, const std::string& hemi) {
    const auto raw = to_double(value);
    if (!raw || hemi.empty()) return std::nullopt;
    const double deg = static_cast<int>(*raw / 100);
    const double minutes = *raw - deg * 100;
    double result = deg + minutes / 60.0;
    if (hemi == "S" || hemi == "W") result = -result;
    return result;
}

std::string format_time(const std::string& hhmmss) {
    if (hhmmss.size() < 6) return {};
    return hhmmss.substr(0, 2) + ":" + hhmmss.substr(2, 2) + ":" + hhmmss.substr(4, 2);
}

std::string format_date(const std::string& ddmmyy) {
    if (ddmmyy.size() != 6) return {};
    return "20" + ddmmyy.substr(4, 2) + "-" + ddmmyy.substr(2, 2) + "-" + ddmmyy.substr(0, 2);
}

}  // namespace

bool nmea_checksum_ok(const std::string& sentence) {
    if (sentence.size() < 4 || sentence[0] != '$') return false;
    const size_t star = sentence.rfind('*');
    if (star == std::string::npos || star + 3 > sentence.size()) return false;
    uint8_t sum = 0;
    for (size_t i = 1; i < star; ++i) sum ^= static_cast<uint8_t>(sentence[i]);
    const unsigned long expected = std::strtoul(sentence.substr(star + 1, 2).c_str(), nullptr, 16);
    return sum == expected;
}

bool parse_nmea(const std::string& line, NmeaUpdate& out) {
    std::string sentence = line;
    while (!sentence.empty() && (sentence.back() == '\r' || sentence.back() == '\n')) sentence.pop_back();
    if (!nmea_checksum_ok(sentence)) return false;
    const size_t star = sentence.rfind('*');
    const auto f = split_fields(sentence.substr(1, star - 1));
    if (f.empty() || f[0].size() < 5) return false;
    const std::string type = f[0].substr(f[0].size() - 3);
    out = NmeaUpdate{};

    if (type == "GGA" && f.size() >= 10) {
        out.is_gga = true;
        out.utc_time = format_time(f[1]);
        const int quality = std::atoi(f[6].c_str());
        out.valid = quality > 0;
        out.satellites = std::atoi(f[7].c_str());
        out.hdop = to_double(f[8]);
        if (out.valid) {
            out.lat = nmea_coord(f[2], f[3]);
            out.lon = nmea_coord(f[4], f[5]);
            out.alt_m = to_double(f[9]);
        }
        return true;
    }
    if (type == "RMC" && f.size() >= 10) {
        out.is_rmc = true;
        out.utc_time = format_time(f[1]);
        out.valid = f[2] == "A";
        out.utc_date = format_date(f[9]);
        if (out.valid) {
            out.lat = nmea_coord(f[3], f[4]);
            out.lon = nmea_coord(f[5], f[6]);
            if (const auto knots = to_double(f[7])) out.speed_kmh = *knots * 1.852;
        }
        return true;
    }
    if (type == "TXT" && f.size() >= 5) {
        out.text = f[4];
        return true;
    }
    return false;
}

GpsReader::~GpsReader() { stop(); }

void GpsReader::start(const std::string& device, int baud) {
    stop();
    device_ = device;
    baud_ = baud;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stop_ = false;
        status_ = GpsStatus{};
        status_.enabled = true;
    }
    thread_ = std::thread([this] { run(); });
}

void GpsReader::stop() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stop_ = true;
    }
    wake_.notify_all();
    if (thread_.joinable()) thread_.join();
    std::lock_guard<std::mutex> lock(mutex_);
    status_.port_open = false;
}

bool GpsReader::wait_ms(int ms) {
    std::unique_lock<std::mutex> lock(mutex_);
    return !wake_.wait_for(lock, std::chrono::milliseconds(ms), [this] { return stop_; });
}

GpsStatus GpsReader::status() const {
    std::lock_guard<std::mutex> lock(mutex_);
    GpsStatus s = status_;
    const auto now = std::chrono::steady_clock::now();
    s.receiving = s.port_open && last_sentence_.time_since_epoch().count() != 0 &&
                  now - last_sentence_ < std::chrono::seconds(3);
    if (last_fix_.time_since_epoch().count() != 0) {
        s.fix_age_s = std::chrono::duration<double>(now - last_fix_).count();
        if (s.fix_age_s > 10.0) s.has_fix = false;
    }
    return s;
}

std::optional<GpsFix> GpsReader::fix(double max_age_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (last_fix_.time_since_epoch().count() == 0) return std::nullopt;
    if (std::chrono::steady_clock::now() - last_fix_ > std::chrono::duration<double>(max_age_s)) return std::nullopt;
    return GpsFix{status_.lat, status_.lon};
}

void GpsReader::feed(const std::string& sentence) {
    NmeaUpdate u;
    if (!parse_nmea(sentence, u)) return;
    std::lock_guard<std::mutex> lock(mutex_);
    const auto now = std::chrono::steady_clock::now();
    last_sentence_ = now;
    if (!u.text.empty()) {
        status_.message = u.text;
        return;
    }
    if (!u.utc_time.empty()) status_.utc_time = u.utc_time;
    if (!u.utc_date.empty()) status_.utc_date = u.utc_date;
    if (u.satellites) status_.satellites = *u.satellites;
    if (u.hdop) status_.hdop = *u.hdop;
    if (u.speed_kmh) status_.speed_kmh = *u.speed_kmh;
    if (u.alt_m) status_.alt_m = *u.alt_m;
    if (u.valid && u.lat && u.lon) {
        status_.lat = *u.lat;
        status_.lon = *u.lon;
        status_.has_fix = true;
        last_fix_ = now;
    } else if (u.is_gga || u.is_rmc) {
        if (!u.valid && (last_fix_.time_since_epoch().count() == 0 || now - last_fix_ > std::chrono::seconds(10))) {
            status_.has_fix = false;
        }
    }
}

#if defined(__linux__)

namespace {

speed_t baud_constant(int baud) {
    switch (baud) {
        case 4800: return B4800;
        case 9600: return B9600;
        case 19200: return B19200;
        case 38400: return B38400;
        case 57600: return B57600;
        case 230400: return B230400;
        case 115200:
        default: return B115200;
    }
}

int open_serial(const std::string& path, int baud) {
    const int fd = ::open(path.c_str(), O_RDONLY | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    termios tio{};
    if (tcgetattr(fd, &tio) == 0) {
        cfmakeraw(&tio);
        cfsetispeed(&tio, baud_constant(baud));
        cfsetospeed(&tio, baud_constant(baud));
        tio.c_cflag |= CLOCAL | CREAD;
        tcsetattr(fd, TCSANOW, &tio);
    }
    return fd;
}

}  // namespace

void GpsReader::run() {
    std::vector<std::string> candidates;
    if (device_.empty() || device_ == "auto") {
        candidates = {"/dev/serial0", "/dev/ttyS0", "/dev/ttyAMA0"};
    } else {
        candidates = {device_};
    }
    while (true) {
        int fd = -1;
        std::string used;
        int last_errno = 0;
        for (const auto& c : candidates) {
            fd = open_serial(c, baud_);
            if (fd >= 0) {
                used = c;
                break;
            }
            last_errno = errno;
        }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            status_.port_open = fd >= 0;
            status_.device = used.empty() ? candidates.front() : used;
            if (fd < 0) status_.message = std::string("Can't open GPS port: ") + std::strerror(last_errno);
        }
        if (fd >= 0) {
            std::string buf;
            char chunk[256];
            while (true) {
                {
                    std::lock_guard<std::mutex> lock(mutex_);
                    if (stop_) break;
                }
                pollfd pfd{fd, POLLIN, 0};
                const int rc = ::poll(&pfd, 1, 200);
                if (rc < 0 && errno != EINTR) break;
                if (rc <= 0) continue;
                const ssize_t n = ::read(fd, chunk, sizeof(chunk));
                if (n < 0 && errno != EAGAIN && errno != EINTR) break;
                if (n <= 0) continue;
                buf.append(chunk, static_cast<size_t>(n));
                size_t nl;
                while ((nl = buf.find('\n')) != std::string::npos) {
                    feed(buf.substr(0, nl));
                    buf.erase(0, nl + 1);
                }
                // No newline in 4 KB: not NMEA, or badly out of sync.
                if (buf.size() > 4096) buf.erase(0, buf.size() - 256);
            }
            ::close(fd);
            std::lock_guard<std::mutex> lock(mutex_);
            status_.port_open = false;
        }
        if (!wait_ms(5000)) return;
    }
}

#else

void GpsReader::run() {
    std::lock_guard<std::mutex> lock(mutex_);
    status_.message = "GPS needs Linux";
}

#endif

}  // namespace mesh
