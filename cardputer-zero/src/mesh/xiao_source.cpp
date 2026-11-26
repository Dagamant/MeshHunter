// PacketSource for the XIAO nRF52840 + Wio-SX1262 sniffer firmware
// (firmware/sniffer). A port of the serial side of the desktop app's
// meshhunter/core/sniffer_worker.py (_RadioLink):
//
//   open the port -> HELLO until the firmware answers -> STOP, CLR,
//   P <id> ... per profile, T <timing>, GO -> read JSON lines.
//
// The firmware stops listening when the port is closed (DTR drops), and
// says "hello" with running=0 if it restarts under us, in which case we
// configure it again. If the port can't be opened, or goes away (USB
// unplugged), the error is shown in status() and we retry every few seconds.

#include "json.h"
#include "sniffer.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>

#if defined(__linux__) || defined(__APPLE__)
#define MESHHUNTER_HAVE_TERMIOS 1
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>
#endif

#if defined(__linux__)
#include <filesystem>
#include <fstream>
#endif

namespace mesh {
namespace {

constexpr int kRetryMs = 5000;
constexpr int kHelloTimeoutMs = 5000;
constexpr int kGoTimeoutMs = 2000;
constexpr size_t kMaxProfiles = 16;  // firmware MAX_PROFILES
constexpr size_t kMaxLine = 4096;
constexpr const char* kUsbProduct = "MeshHunter Sniffer";

using Clock = std::chrono::steady_clock;

struct SnifferError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

std::string basename_of(const std::string& path) {
    const auto slash = path.rfind('/');
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

class XiaoSource final : public PacketSource {
public:
    explicit XiaoSource(std::string device) : device_(std::move(device)) {
        if (device_.empty()) device_ = "auto";
        status_.source = "XIAO sniffer";
    }
    ~XiaoSource() override { stop(); }

    void start(PacketCallback cb) override {
        if (thread_.joinable()) return;
        callback_ = std::move(cb);
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stop_ = false;
        }
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
        if (profiles.size() > kMaxProfiles) profiles.resize(kMaxProfiles);
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
    void set_state(RadioState s, const std::string& msg) {
        std::lock_guard<std::mutex> lock(mutex_);
        status_.state = s;
        status_.message = msg;
    }

    bool stopping() {
        std::lock_guard<std::mutex> lock(mutex_);
        return stop_;
    }

    bool wait_ms(int ms) {
        std::unique_lock<std::mutex> lock(mutex_);
        return !wake_.wait_for(lock, std::chrono::milliseconds(ms), [this] { return stop_; });
    }

    void run() {
        while (!stopping()) {
            try {
                session();
            } catch (const SnifferError& e) {
                set_state(RadioState::Error, e.what());
            }
            close_port();
            if (stopping() || !wait_ms(kRetryMs)) break;
        }
        close_port();
        set_state(RadioState::Stopped, "");
    }

    void session() {
        std::string path = device_;
        if (path == "auto") {
            set_state(RadioState::Starting, "Looking for the XIAO sniffer...");
            path = find_xiao_sniffer_port();
            if (path.empty()) {
                throw SnifferError("No XIAO sniffer found. Plug it in, or set serial_device in the config.");
            }
        }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            status_.source = "XIAO " + basename_of(path);
        }
        set_state(RadioState::Starting, "Opening " + path + "...");
        open_port(path);

        const std::string hello = wait_for_hello();
        if (hello.empty()) throw SnifferError("No answer from the sniffer firmware on " + path);
        double radio = -1;
        json::get_number(hello, "radio", radio);
        if (radio != 0) {
            char buf[128];
            std::snprintf(buf, sizeof(buf), "XIAO radio failed to start (RadioLib code %d). Is the LoRa board seated?",
                          static_cast<int>(radio));
            throw SnifferError(buf);
        }
        std::string fw;
        double ver = 0;
        json::get_string(hello, "fw", fw);
        json::get_number(hello, "ver", ver);
        fw_note_ = (fw.empty() ? std::string("sniffer") : fw) + " v" + std::to_string(static_cast<int>(ver));

        {
            std::lock_guard<std::mutex> lock(mutex_);
            reconfigure_ = true;
        }
        std::string line;
        while (!stopping()) {
            bool reconfigure;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                reconfigure = reconfigure_;
            }
            if (reconfigure) configure();
            if (read_line(200, line)) handle(line);
        }
    }

    // ---- configuration ----------------------------------------------------

    std::string wait_for_hello() {
        const auto deadline = Clock::now() + std::chrono::milliseconds(kHelloTimeoutMs);
        auto next_ping = Clock::now();
        std::string line;
        while (Clock::now() < deadline && !stopping()) {
            if (Clock::now() >= next_ping) {
                send("HELLO");
                next_ping = Clock::now() + std::chrono::seconds(1);
            }
            if (read_line(200, line) && event_of(line) == "hello") return line;
        }
        return "";
    }

    void configure() {
        SnifferTiming timing;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            profiles_ = pending_profiles_;
            timing = pending_timing_;
            reconfigure_ = false;
            status_.stats.clear();
            for (const auto& p : profiles_) status_.stats.push_back(ProfileStats{p.key});
            status_.current = -1;
            status_.hops = 0;
            status_.locked = false;
        }
        send("STOP");
        send("CLR");
        if (profiles_.empty()) {
            set_state(RadioState::Listening, fw_note_ + ", no profiles selected");
            return;
        }
        for (size_t i = 0; i < profiles_.size(); ++i) {
            const Profile& p = profiles_[i];
            char buf[96];
            std::snprintf(buf, sizeof(buf), "P %zu %.4f %g %d %d %02x %u", i, p.freq, p.bw, p.sf, p.cr, p.sync,
                          static_cast<unsigned>(p.preamble));
            send(buf);
        }
        char buf[96];
        std::snprintf(buf, sizeof(buf), "T %d %d %d %d", std::max(0, timing.dwell_ms), std::max(0, timing.linger_ms),
                      std::max(0, timing.partial_linger_ms), std::max(0, timing.linger_max_ms));
        send(buf);
        send("GO");
        await_go();
        set_state(RadioState::Listening, fw_note_);
    }

    // Consume replies up to GO's "ok", so the idle hello lines the firmware
    // sent before it started aren't taken for a restart afterwards.
    void await_go() {
        const auto deadline = Clock::now() + std::chrono::milliseconds(kGoTimeoutMs);
        std::string line;
        while (Clock::now() < deadline) {
            if (!read_line(100, line)) continue;
            const std::string ev = event_of(line);
            if (ev == "err") {
                std::string msg;
                json::get_string(line, "msg", msg);
                throw SnifferError("Sniffer refused the setup: " + msg);
            }
            if (ev == "ok") {
                std::string cmd;
                json::get_string(line, "cmd", cmd);
                if (cmd == "GO") return;
            }
        }
        throw SnifferError("Sniffer did not acknowledge GO");
    }

    // ---- incoming events --------------------------------------------------

    static std::string event_of(const std::string& line) {
        std::string ev;
        json::get_string(line, "ev", ev);
        return ev;
    }

    void handle(const std::string& line) {
        const std::string ev = event_of(line);
        if (ev == "pkt") {
            on_packet(line);
        } else if (ev == "stat") {
            on_stat(line);
        } else if (ev == "rs") {
            on_rssi(line);
        } else if (ev == "hello") {
            bool running = true;
            double r = 1;
            if (json::get_number(line, "running", r)) running = r != 0;
            if (!running && !profiles_.empty()) {
                // Firmware restarted under us (brown-out, reset button).
                std::lock_guard<std::mutex> lock(mutex_);
                reconfigure_ = true;
            }
        } else if (ev == "err") {
            std::string msg;
            json::get_string(line, "msg", msg);
            std::lock_guard<std::mutex> lock(mutex_);
            status_.message = fw_note_ + ", error: " + msg;
        }
    }

    void on_packet(const std::string& line) {
        double id = -1, rssi = 0, snr = 0, crc = 0, sync = 0;
        std::string hex;
        json::get_number(line, "p", id);
        json::get_number(line, "rssi", rssi);
        json::get_number(line, "snr", snr);
        json::get_number(line, "crc", crc);
        json::get_number(line, "sw", sync);
        json::get_string(line, "hex", hex);
        RxPacket pkt;
        if (!from_hex(hex, pkt.data)) return;
        pkt.rssi = static_cast<float>(rssi);
        pkt.snr = static_cast<float>(snr);
        pkt.crc_ok = crc != 0;
        const int idx = static_cast<int>(id);
        if (idx >= 0 && idx < static_cast<int>(profiles_.size())) {
            pkt.profile = profiles_[static_cast<size_t>(idx)];
            std::lock_guard<std::mutex> lock(mutex_);
            if (idx < static_cast<int>(status_.stats.size())) {
                auto& s = status_.stats[static_cast<size_t>(idx)];
                (pkt.crc_ok ? s.rx_ok : s.rx_bad)++;
            }
            status_.current = idx;
        } else {
            // Not one of ours (stale line after a reconfigure): keep the
            // sync word so it still decodes.
            pkt.profile.key = "p" + std::to_string(idx);
            pkt.profile.name = pkt.profile.key;
            pkt.profile.sync = static_cast<uint8_t>(sync);
        }
        if (callback_) callback_(std::move(pkt));
    }

    // {"ev":"stat","cur":0,"hops":12,"nf":-110.5,
    //  "profiles":[[id,ok,crc,hdr,timeouts,pre,listen_ms],...]}
    void on_stat(const std::string& line) {
        double cur = -1, hops = 0;
        json::get_number(line, "cur", cur);
        json::get_number(line, "hops", hops);
        std::vector<std::vector<double>> rows;
        json::get_number_rows(line, "profiles", rows);
        std::lock_guard<std::mutex> lock(mutex_);
        status_.current = cur >= 0 && cur < static_cast<double>(status_.stats.size()) ? static_cast<int>(cur) : -1;
        status_.hops = static_cast<uint64_t>(std::max(0.0, hops));
        for (const auto& r : rows) {
            if (r.size() < 7 || r[0] < 0 || r[0] >= static_cast<double>(status_.stats.size())) continue;
            auto& s = status_.stats[static_cast<size_t>(r[0])];
            s.rx_ok = static_cast<uint32_t>(r[1]);
            s.rx_bad = static_cast<uint32_t>(r[2] + r[3] + r[4]);
            s.preamble_only = static_cast<uint32_t>(r[5]);
            s.listen_ms = static_cast<uint64_t>(r[6]);
        }
    }

    // {"ev":"rs","cur":0,"v":[[id,peak dBm],...]} about 5x a second.
    void on_rssi(const std::string& line) {
        double cur = -1;
        json::get_number(line, "cur", cur);
        std::vector<std::vector<double>> rows;
        json::get_number_rows(line, "v", rows);
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto& s : status_.stats) s.peak_rssi = -160.0f;
        for (const auto& r : rows) {
            if (r.size() < 2 || r[0] < 0 || r[0] >= static_cast<double>(status_.stats.size())) continue;
            status_.stats[static_cast<size_t>(r[0])].peak_rssi = static_cast<float>(r[1]);
            if (r[0] == cur) status_.rssi_now = static_cast<float>(r[1]);
        }
        if (cur >= 0 && cur < static_cast<double>(status_.stats.size())) status_.current = static_cast<int>(cur);
    }

    // ---- serial port --------------------------------------------------------

#if MESHHUNTER_HAVE_TERMIOS
    static std::string errno_text(const std::string& what) { return what + ": " + std::strerror(errno); }

    void open_port(const std::string& path) {
        const int fd = ::open(path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) {
            if (errno == EACCES) {
                throw SnifferError("Can't open " + path + ": permission denied (is the user in the dialout group?)");
            }
            throw SnifferError(errno_text("Can't open " + path));
        }
        fd_ = fd;
        // Same lock pyserial's exclusive=True takes, so the desktop app and
        // MeshHunter can't both grab one sniffer.
        if (::flock(fd_, LOCK_EX | LOCK_NB) != 0) {
            throw SnifferError(path + " is in use by another program");
        }
        termios tio{};
        if (::tcgetattr(fd_, &tio) != 0) throw SnifferError(errno_text("Not a serial port: " + path));
        ::cfmakeraw(&tio);
        ::cfsetispeed(&tio, B115200);
        ::cfsetospeed(&tio, B115200);
        tio.c_cflag |= CLOCAL | CREAD | HUPCL;  // HUPCL: drop DTR on close, which stops the firmware
        tio.c_cc[VMIN] = 0;
        tio.c_cc[VTIME] = 0;
        if (::tcsetattr(fd_, TCSANOW, &tio) != 0) throw SnifferError(errno_text("Can't configure " + path));
        int bits = TIOCM_DTR | TIOCM_RTS;
        ::ioctl(fd_, TIOCMBIS, &bits);  // the firmware only talks while DTR is up; fails harmlessly on a pty
        ::tcflush(fd_, TCIOFLUSH);
        rx_.clear();
    }

    void close_port() {
        if (fd_ < 0) return;
        const std::string stop = "STOP\n";
        (void)!::write(fd_, stop.data(), stop.size());
        ::close(fd_);
        fd_ = -1;
        rx_.clear();
    }

    void send(const std::string& line) {
        const std::string data = line + "\n";
        size_t off = 0;
        const auto deadline = Clock::now() + std::chrono::seconds(1);
        while (off < data.size()) {
            const ssize_t n = ::write(fd_, data.data() + off, data.size() - off);
            if (n > 0) {
                off += static_cast<size_t>(n);
                continue;
            }
            if (n < 0 && errno != EAGAIN && errno != EINTR) throw SnifferError(errno_text("Sniffer disconnected"));
            if (Clock::now() > deadline) throw SnifferError("Sniffer stopped reading commands");
            pollfd pfd{fd_, POLLOUT, 0};
            ::poll(&pfd, 1, 50);
        }
    }

    // One line (without the newline), waiting up to timeout_ms for it.
    bool read_line(int timeout_ms, std::string& out) {
        const auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
        while (true) {
            const auto nl = rx_.find('\n');
            if (nl != std::string::npos) {
                out = rx_.substr(0, nl);
                rx_.erase(0, nl + 1);
                if (!out.empty() && out.back() == '\r') out.pop_back();
                return true;
            }
            if (rx_.size() > kMaxLine) rx_.clear();  // garbage without newlines
            const auto left =
                std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
            if (left <= 0) return false;
            pollfd pfd{fd_, POLLIN, 0};
            const int r = ::poll(&pfd, 1, static_cast<int>(left));
            if (r < 0) {
                if (errno == EINTR) continue;
                throw SnifferError(errno_text("Sniffer read failed"));
            }
            if (r == 0) return false;
            char buf[512];
            const ssize_t n = ::read(fd_, buf, sizeof(buf));
            if (n > 0) {
                rx_.append(buf, static_cast<size_t>(n));
            } else if (n == 0 || (errno != EAGAIN && errno != EINTR)) {
                throw SnifferError("Sniffer disconnected");
            } else if (pfd.revents & (POLLHUP | POLLERR)) {
                throw SnifferError("Sniffer disconnected");
            }
        }
    }
#else
    void open_port(const std::string&) { throw SnifferError("XIAO sniffer isn't supported on this platform"); }
    void close_port() {}
    void send(const std::string&) {}
    bool read_line(int, std::string&) { return false; }
#endif

    std::string device_;
    PacketCallback callback_;
    std::thread thread_;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    bool stop_ = false;
    bool reconfigure_ = false;
    std::vector<Profile> pending_profiles_;
    SnifferTiming pending_timing_;
    SnifferStatus status_;

    // Reader-thread-only state.
    int fd_ = -1;
    std::string rx_;
    std::string fw_note_;
    std::vector<Profile> profiles_;  // as sent to the firmware; index = its profile id
};

}  // namespace

std::unique_ptr<PacketSource> make_xiao_source(const std::string& device) {
    return std::make_unique<XiaoSource>(device);
}

std::string find_xiao_sniffer_port() {
#if defined(__linux__)
    namespace fs = std::filesystem;
    std::vector<std::string> names;
    std::error_code ec;
    for (const auto& entry : fs::directory_iterator("/sys/class/tty", ec)) {
        const std::string name = entry.path().filename().string();
        if (name.rfind("ttyACM", 0) == 0) names.push_back(name);
    }
    std::sort(names.begin(), names.end());
    for (const auto& name : names) {
        // device -> the USB interface; its parent holds the product string.
        std::ifstream in("/sys/class/tty/" + name + "/device/../product");
        std::string product;
        std::getline(in, product);
        if (product.find(kUsbProduct) != std::string::npos) return "/dev/" + name;
    }
#endif
    return "";
}

}  // namespace mesh
