// Host-side tests for the radio-independent mesh code. Plain main(), no
// framework.
//
//   mesh_tests                      unit tests only
//   mesh_tests <sniffer_packets.jsonl>
//       also re-decodes every captured packet and compares against the
//       "decoded" fields the desktop (Python) decoder wrote, so the C++ port
//       is checked against real traffic.

#include "bytes.h"
#include "csv_table.h"
#include "engine.h"
#include "gps.h"
#include "json.h"
#include "meshcore.h"
#include "meshtastic.h"
#include "profiles.h"
#include "sx1262.h"

#include <openssl/evp.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <thread>
#include <vector>

#if defined(__linux__)
#include <atomic>
#include <chrono>
#include <fcntl.h>
#include <mutex>
#include <poll.h>
#include <unistd.h>
#endif

namespace {

int failures = 0;
int checks = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        ++checks;                                                            \
        if (!(cond)) {                                                       \
            ++failures;                                                      \
            std::fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #cond); \
        }                                                                    \
    } while (0)

void test_profiles() {
    // Same numbers as the desktop's sniffer_profiles.py for the US band.
    const auto us = mesh::builtin_profiles(mesh::Region::US);
    const auto* lf = mesh::find_profile(us, "mt_longfast");
    CHECK(lf && std::fabs(lf->freq - 906.875) < 1e-6);
    const auto* mc = mesh::find_profile(us, "meshcore_us");
    CHECK(mc && mc->sync == 0x12 && std::fabs(mc->freq - 910.525) < 1e-6);
    const auto eu = mesh::builtin_profiles(mesh::Region::EU868);
    const auto* eulf = mesh::find_profile(eu, "mt_eu_longfast");
    CHECK(eulf && std::fabs(eulf->freq - 869.525) < 1e-6);
    CHECK(!mesh::find_profile(eu, "mt_eu_shortturbo"));  // 500 kHz doesn't fit EU868
}

void test_format() {
    CHECK(mesh::format_float(38.154288, 6) == "38.154288");
    CHECK(mesh::format_float(-81.11461, 6) == "-81.11461");
    CHECK(mesh::format_float(-26.0, 1) == "-26.0");
    CHECK(mesh::format_float(12.25, 2) == "12.25");
    CHECK(mesh::format_float(38.0, 6) == "38.0");
}

void test_csv() {
    const auto rows = mesh::parse_csv("a,b,c\r\n1,\"x, \"\"y\"\"\",3\r\n\r\n4,,6\n");
    CHECK(rows.size() == 3);
    CHECK(rows[1][1] == "x, \"y\"");
    CHECK(rows[2][1].empty() && rows[2][2] == "6");
    CHECK(mesh::csv_field("a,b") == "\"a,b\"");
    CHECK(mesh::csv_field(" Gauley") == " Gauley");
}

void test_nmea() {
    mesh::NmeaUpdate u;
    CHECK(mesh::parse_nmea("$GNGGA,,,,,,0,00,25.5,,,,,,*64", u));
    CHECK(u.is_gga && !u.valid && u.satellites && *u.satellites == 0);
    CHECK(mesh::parse_nmea("$GNRMC,,V,,,,,,,,,,N,V*37", u));
    CHECK(u.is_rmc && !u.valid);
    CHECK(mesh::parse_nmea("$GPTXT,01,01,01,ANTENNA OPEN*25", u));
    CHECK(u.text == "ANTENNA OPEN");
    CHECK(!mesh::parse_nmea("$GNGGA,,,,,,0,00,25.5,,,,,,*65", u));  // bad checksum
    // A valid fix (checksum computed for this sentence).
    std::string body = "GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,";
    unsigned sum = 0;
    for (char c : body) sum ^= static_cast<unsigned char>(c);
    char tail[8];
    std::snprintf(tail, sizeof(tail), "*%02X", sum);
    CHECK(mesh::parse_nmea("$" + body + tail, u));
    CHECK(u.valid && u.lat && std::fabs(*u.lat - 48.1173) < 1e-4 && u.lon && std::fabs(*u.lon - 11.516667) < 1e-4);
    CHECK(u.alt_m && std::fabs(*u.alt_m - 545.4) < 1e-6 && *u.satellites == 8);
}

void test_meshcore_channels() {
    const auto pub = mesh::mc_public_channel();
    CHECK(pub.hash == 0x11);  // matches chan_hash "11" for Public in captures
    mesh::McChannel tag;
    CHECK(mesh::mc_hashtag_channel("#test", tag));
    CHECK(!mesh::mc_hashtag_channel("test", tag));
}

void test_time_on_air() {
    // SF7/125/4:5, 8 symbol preamble, 20 bytes: ~56.6 ms.
    const double toa = mesh::time_on_air_ms(7, 125.0, 5, 8, 20);
    CHECK(toa > 55.0 && toa < 58.0);
}

// Compare against the Python decoder's output in each record.
void test_golden(const std::string& path) {
    std::ifstream in(path);
    if (!in) {
        std::fprintf(stderr, "can't open %s\n", path.c_str());
        ++failures;
        return;
    }
    const auto mc_channels = std::vector<mesh::McChannel>{mesh::mc_public_channel()};
    std::vector<mesh::MtChannel> mt_channels;
    for (const char* n : {"LongFast", "MediumFast", "MediumSlow", "ShortFast", "ShortSlow", "ShortTurbo", "LongMod",
                          "LongSlow"}) {
        mesh::MtChannel c;
        mesh::mt_channel(n, "AQ==", c);
        mt_channels.push_back(c);
    }
    std::string line;
    int records = 0, compared = 0, adverts = 0, decrypted = 0, mt = 0;
    while (std::getline(in, line)) {
        ++records;
        std::string hex;
        mesh::Bytes raw;
        if (!mesh::json::get_string(line, "hex", hex) || !mesh::from_hex(hex, raw)) continue;
        const auto dpos = line.find("\"decoded\":");
        if (dpos == std::string::npos) continue;
        const std::string dec = line.substr(dpos);
        std::string proto;
        mesh::json::get_string(dec, "proto", proto);
        const int before = failures;
        if (proto == "meshcore") {
            const auto d = mesh::decode_meshcore(raw.data(), raw.size(), mc_channels);
            std::string s;
            double n;
            bool b;
            if (mesh::json::get_string(dec, "payload_typename", s)) CHECK(s == mesh::mc_payload_name(d.payload_type));
            if (mesh::json::get_string(dec, "path", s)) CHECK(s == d.path);
            if (mesh::json::get_number(dec, "pkt_hash", n)) CHECK(static_cast<uint32_t>(n) == d.pkt_hash);
            if (mesh::json::get_string(dec, "adv_key", s)) {
                ++adverts;
                CHECK(d.has_advert && s == d.advert.public_key);
                if (mesh::json::get_bool(dec, "sig_ok", b)) CHECK(b == d.advert.sig_ok);
                if (mesh::json::get_number(dec, "adv_lat", n)) CHECK(std::fabs(n - d.advert.lat) < 1e-9);
                if (mesh::json::get_number(dec, "adv_lon", n)) CHECK(std::fabs(n - d.advert.lon) < 1e-9);
                if (mesh::json::get_string(dec, "adv_name", s) && s.find('?') == std::string::npos) {
                    CHECK(s == d.advert.name);
                }
            }
            if (mesh::json::get_string(dec, "chan_name", s)) {
                ++decrypted;
                CHECK(s == d.chan_name);
                std::string msg;
                if (mesh::json::get_string(dec, "message", msg) && msg.find('?') == std::string::npos) {
                    CHECK(msg == d.message);
                }
            }
        } else if (proto == "meshtastic") {
            ++mt;
            const auto d = mesh::decode_meshtastic(raw.data(), raw.size(), mt_channels);
            std::string s;
            bool b;
            if (mesh::json::get_string(dec, "from", s)) CHECK(s == d.from);
            if (mesh::json::get_string(dec, "to", s)) CHECK(s == d.to);
            if (mesh::json::get_string(dec, "portname", s)) CHECK(s == d.portname);
            if (mesh::json::get_bool(dec, "encrypted", b)) CHECK(b == d.encrypted);
            if (mesh::json::get_string(dec, "long_name", s) && s.find('?') == std::string::npos) {
                CHECK(s == d.long_name);
            }
            if (mesh::json::get_string(dec, "text", s) && s.find('?') == std::string::npos) {
                CHECK(d.text && s == *d.text);
            }
        } else {
            continue;
        }
        ++compared;
        if (failures != before) std::fprintf(stderr, "  mismatch in record %d: %s\n", records, hex.c_str());
    }
    std::printf("golden: %d records, %d compared (%d adverts, %d decrypted group texts, %d meshtastic)\n", records,
                compared, adverts, decrypted, mt);
}

// Signs a synthetic repeater advert (with location) using a throwaway
// Ed25519 key, runs it through the Engine and checks the desktop-format
// CSVs it writes; a second advert without location must not be logged.
mesh::Bytes signed_advert(EVP_PKEY* key, uint8_t flags, int32_t lat_e6, int32_t lon_e6, const std::string& name) {
    uint8_t pub[32];
    size_t pub_len = sizeof(pub);
    EVP_PKEY_get_raw_public_key(key, pub, &pub_len);
    mesh::Bytes app{flags};
    auto le32 = [&](uint32_t v) {
        for (int i = 0; i < 4; ++i) app.push_back(static_cast<uint8_t>(v >> (8 * i)));
    };
    if (flags & 0x10) {
        le32(static_cast<uint32_t>(lat_e6));
        le32(static_cast<uint32_t>(lon_e6));
    }
    app.insert(app.end(), name.begin(), name.end());
    const uint32_t ts = 1794200000;
    mesh::Bytes msg(pub, pub + 32);
    for (int i = 0; i < 4; ++i) msg.push_back(static_cast<uint8_t>(ts >> (8 * i)));
    msg.insert(msg.end(), app.begin(), app.end());

    uint8_t sig[64];
    size_t sig_len = sizeof(sig);
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    EVP_DigestSignInit(ctx, nullptr, nullptr, nullptr, key);
    EVP_DigestSign(ctx, sig, &sig_len, msg.data(), msg.size());
    EVP_MD_CTX_free(ctx);

    // header: FLOOD route (1), ADVERT payload (4); then an empty path.
    mesh::Bytes pkt{static_cast<uint8_t>(0x01 | (0x04 << 2)), 0x00};
    pkt.insert(pkt.end(), pub, pub + 32);
    pkt.insert(pkt.end(), msg.begin() + 32, msg.begin() + 36);
    pkt.insert(pkt.end(), sig, sig + 64);
    pkt.insert(pkt.end(), app.begin(), app.end());
    return pkt;
}

std::string read_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

void test_engine_records_advert() {
    char dir_template[] = "/tmp/mesh_tests_XXXXXX";
    const char* dir = mkdtemp(dir_template);
    CHECK(dir != nullptr);
    if (!dir) return;

    EVP_PKEY* key = nullptr;
    EVP_PKEY_CTX* kctx = EVP_PKEY_CTX_new_id(EVP_PKEY_ED25519, nullptr);
    EVP_PKEY_keygen_init(kctx);
    EVP_PKEY_keygen(kctx, &key);
    EVP_PKEY_CTX_free(kctx);

    mesh::Settings settings;
    settings.source = mesh::SourceKind::None;
    settings.gps_enabled = false;
    settings.raw_log = false;
    settings.data_dir = dir;
    {
        mesh::Engine engine(settings, nullptr);
        engine.start();
        mesh::RxPacket pkt;
        pkt.profile = *mesh::find_profile(mesh::builtin_profiles(mesh::Region::US), "meshcore_us");
        pkt.crc_ok = true;
        pkt.rssi = -88.5f;
        pkt.snr = 7.25f;
        pkt.data = signed_advert(key, 0x92, 45500000, -122600000, "Test Repeater");  // repeater+loc+name
        engine.process_packet(pkt);
        pkt.data = signed_advert(key, 0x81, 0, 0, "No Location");  // chat, no location
        engine.process_packet(pkt);

        const auto counters = engine.counters();
        CHECK(counters.adverts == 2);
        CHECK(counters.bad_signatures == 0);
        CHECK(counters.logged_meshcore == 1);
        engine.stop();
    }
    EVP_PKEY_free(key);

    const std::string base = dir;
    const std::string repeaters = read_file(base + "/repeaters.csv");
    CHECK(repeaters.rfind("node_id,node_type,name,lat,lon,rssi,first_seen,type,network,public_key,"
                          "last_heard,logger_lat,logger_lon,uploaded_wdgwars,uploaded_ingest\r\n", 0) == 0);
    CHECK(repeaters.find(",REPEATER,Test Repeater,45.5,-122.6,-88.5,") != std::string::npos);
    CHECK(repeaters.find(",MESHCORE,meshcore,") != std::string::npos);
    CHECK(read_file(base + "/all_contacts.csv").find("Test Repeater") != std::string::npos);
    CHECK(read_file(base + "/chat_nodes.csv").find("No Location") == std::string::npos);

    std::error_code ec;
    std::filesystem::remove_all(base, ec);
}

void test_json_rows() {
    std::vector<std::vector<double>> rows;
    CHECK(mesh::json::get_number_rows(R"({"ev":"stat","profiles":[[0,5,1,2,0,3,1200],[1, 0,0,0,0,0,800]]})",
                                      "profiles", rows));
    CHECK(rows.size() == 2);
    CHECK(rows.size() == 2 && rows[0].size() == 7 && rows[0][1] == 5 && rows[0][6] == 1200);
    CHECK(rows.size() == 2 && rows[1].size() == 7 && rows[1][0] == 1 && rows[1][6] == 800);
    CHECK(mesh::json::get_number_rows(R"({"ev":"rs","cur":0,"v":[[0,-98.5]]})", "v", rows));
    CHECK(rows.size() == 1 && rows[0].size() == 2 && rows[0][1] == -98.5);
    CHECK(mesh::json::get_number_rows(R"({"v":[]})", "v", rows) && rows.empty());
    CHECK(!mesh::json::get_number_rows(R"({"v":3})", "v", rows));
}

void test_settings_storage() {
    char dir_template[] = "/tmp/mesh_home_XXXXXX";
    const char* home = mkdtemp(dir_template);
    CHECK(home != nullptr);
    if (!home) return;
    const std::string base = home;
    const char* old_home = std::getenv("HOME");
    const std::string saved_home = old_home ? old_home : "";
    setenv("HOME", home, 1);
    unsetenv("XDG_DATA_HOME");

    mesh::Settings s;
    CHECK(s.resolved_data_dir() == base + "/MeshHunter");
    s.data_dir = "~/elsewhere";
    CHECK(s.resolved_data_dir() == base + "/elsewhere");

    // Old data is moved to ~/MeshHunter once, and left alone after that.
    CHECK(mesh::migrate_legacy_data_dir(base + "/MeshHunter").empty());  // nothing to move
    mesh::make_dirs(base + "/.local/share/meshhunter");
    std::ofstream(base + "/.local/share/meshhunter/repeaters.csv") << "node_id\r\n";
    CHECK(!mesh::migrate_legacy_data_dir(base + "/MeshHunter").empty());
    CHECK(std::filesystem::exists(base + "/MeshHunter/repeaters.csv"));
    CHECK(!std::filesystem::exists(base + "/.local/share/meshhunter"));
    mesh::make_dirs(base + "/.local/share/meshhunter");
    CHECK(mesh::migrate_legacy_data_dir(base + "/MeshHunter").empty());  // target exists
    CHECK(std::filesystem::exists(base + "/.local/share/meshhunter"));

    // source=xiao and serial_device survive a save/load.
    mesh::Settings x;
    x.source = mesh::SourceKind::Xiao;
    x.serial_device = "/dev/ttyACM3";
    std::string error;
    const std::string conf = base + "/meshhunter.conf";
    CHECK(mesh::save_settings(conf, x, error));
    mesh::Settings y;
    CHECK(mesh::load_settings(conf, y, error));
    CHECK(y.source == mesh::SourceKind::Xiao);
    CHECK(y.serial_device == "/dev/ttyACM3");

    if (old_home) setenv("HOME", saved_home.c_str(), 1);
    std::error_code ec;
    std::filesystem::remove_all(base, ec);
}

#if defined(__linux__)
// Drives the XIAO source against a fake sniffer firmware on a pty.
void test_xiao_source() {
    const int master = posix_openpt(O_RDWR | O_NOCTTY);
    CHECK(master >= 0);
    if (master < 0) return;
    grantpt(master);
    unlockpt(master);
    const std::string slave = ptsname(master);

    std::mutex mu;
    std::vector<std::string> commands;
    std::atomic<bool> done{false};
    std::thread firmware([&] {
        auto say = [&](const std::string& line) {
            const std::string data = line + "\n";
            (void)!write(master, data.data(), data.size());
        };
        std::string buf;
        while (!done) {
            pollfd pfd{master, POLLIN, 0};
            if (poll(&pfd, 1, 50) <= 0 || !(pfd.revents & POLLIN)) continue;
            char tmp[256];
            const ssize_t n = read(master, tmp, sizeof(tmp));
            if (n <= 0) continue;
            buf.append(tmp, static_cast<size_t>(n));
            size_t nl;
            while ((nl = buf.find('\n')) != std::string::npos) {
                std::string cmd = buf.substr(0, nl);
                buf.erase(0, nl + 1);
                if (!cmd.empty() && cmd.back() == '\r') cmd.pop_back();
                {
                    std::lock_guard<std::mutex> lock(mu);
                    commands.push_back(cmd);
                }
                const std::string word = cmd.substr(0, cmd.find(' '));
                if (word == "HELLO") {
                    say(R"({"ev":"hello","fw":"meshhunter-sniffer","ver":3,"radio":0,"running":0,"max_profiles":16})");
                } else if (word == "GO") {
                    say(R"({"ev":"ok","cmd":"GO"})");
                    say(R"({"ev":"pkt","p":1,"f":906.8750,"bw":250.00,"sf":11,"cr":5,"sw":43,"rssi":-97.5,)"
                        R"("snr":6.25,"crc":1,"ms":1234,"hex":"DEADBEEF01"})");
                    say(R"({"ev":"stat","ms":5000,"cur":1,"hops":42,"nf":-112.0,)"
                        R"("profiles":[[0,3,1,0,1,4,2000],[1,7,0,0,0,2,3000]]})");
                    say(R"({"ev":"rs","cur":1,"v":[[0,-120.5],[1,-101.0]]})");
                } else {
                    say(R"({"ev":"ok","cmd":")" + word + R"("})");
                }
            }
        }
    });

    const auto profiles = mesh::builtin_profiles(mesh::Region::US);
    std::vector<mesh::Profile> selected = {*mesh::find_profile(profiles, "meshcore_us"),
                                           *mesh::find_profile(profiles, "mt_longfast")};
    auto source = mesh::make_xiao_source(slave);
    source->set_profiles(selected, mesh::SnifferTiming{});
    std::mutex pkt_mu;
    std::vector<mesh::RxPacket> got;
    source->start([&](mesh::RxPacket&& pkt) {
        std::lock_guard<std::mutex> lock(pkt_mu);
        got.push_back(std::move(pkt));
    });
    mesh::SnifferStatus status;
    for (int i = 0; i < 60; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        status = source->status();
        std::lock_guard<std::mutex> lock(pkt_mu);
        if (!got.empty() && status.hops == 42 && status.rssi_now > -110) break;
    }
    {
        std::lock_guard<std::mutex> lock(pkt_mu);
        CHECK(got.size() == 1);
        if (!got.empty()) {
            CHECK(got[0].profile.key == "mt_longfast");
            CHECK(got[0].profile.sync == mesh::kSyncMeshtastic);
            CHECK(got[0].crc_ok);
            CHECK(got[0].rssi == -97.5f);
            CHECK(got[0].snr == 6.25f);
            CHECK(mesh::to_hex(got[0].data) == "deadbeef01");
        }
    }
    CHECK(status.state == mesh::RadioState::Listening);
    CHECK(status.source == "XIAO " + slave.substr(slave.rfind('/') + 1));
    CHECK(status.hops == 42);
    CHECK(status.current == 1);
    CHECK(status.stats.size() == 2);
    if (status.stats.size() == 2) {
        CHECK(status.stats[0].rx_ok == 3 && status.stats[0].rx_bad == 2 && status.stats[0].preamble_only == 4);
        CHECK(status.stats[1].rx_ok == 7 && status.stats[1].listen_ms == 3000);
        CHECK(status.stats[1].peak_rssi == -101.0f);
    }
    CHECK(status.rssi_now == -101.0f);
    source->stop();
    done = true;
    firmware.join();
    close(master);

    std::lock_guard<std::mutex> lock(mu);
    const auto has = [&](const std::string& c) {
        return std::find(commands.begin(), commands.end(), c) != commands.end();
    };
    CHECK(has("HELLO") && has("CLR") && has("GO") && has("T 0 3000 2000 12000"));
    CHECK(has("P 0 910.5250 62.5 7 5 12 8"));
    char p1[96];
    std::snprintf(p1, sizeof(p1), "P 1 %.4f 250 11 5 2b 8", selected[1].freq);
    CHECK(has(p1));
    CHECK(!commands.empty() && commands.back() == "STOP");  // sent on close
}
#endif

}  // namespace

int main(int argc, char** argv) {
    test_profiles();
    test_format();
    test_csv();
    test_nmea();
    test_meshcore_channels();
    test_time_on_air();
    test_engine_records_advert();
    test_json_rows();
    test_settings_storage();
#if defined(__linux__)
    test_xiao_source();
#endif
    if (argc > 1) test_golden(argv[1]);
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
