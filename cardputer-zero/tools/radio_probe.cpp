// Command-line smoke test for the Cap LoRa-1262 path, without the UI:
// powers the Cap, initialises the SX1262 and listens (receive only) on the
// given profiles for N seconds, printing every decoded packet and the
// per-profile stats.
//
//   meshhunter_radio_probe [seconds] [profile_key ...]
//   e.g. meshhunter_radio_probe 60 meshcore_us mt_longfast

#include "cap_lora_io.h"
#include "engine.h"
#include "gps.h"
#include "meshcore.h"
#include "meshtastic.h"
#include "profiles.h"
#include "sniffer.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>

int main(int argc, char** argv) {
    const int seconds = argc > 1 ? std::atoi(argv[1]) : 30;
    std::vector<mesh::Profile> profiles;
    std::vector<mesh::Profile> all = mesh::builtin_profiles(mesh::Region::US);
    const auto eu = mesh::builtin_profiles(mesh::Region::EU868);
    all.insert(all.end(), eu.begin(), eu.end());
    for (int i = 2; i < argc; ++i) {
        if (const auto* p = mesh::find_profile(all, argv[i])) profiles.push_back(*p);
        else std::fprintf(stderr, "unknown profile %s\n", argv[i]);
    }
    if (profiles.empty()) profiles.push_back(*mesh::find_profile(all, "meshcore_us"));

    const std::vector<mesh::McChannel> mc = {mesh::mc_public_channel()};
    std::vector<mesh::MtChannel> mt;
    for (const char* n : {"LongFast", "MediumFast", "MediumSlow", "ShortFast", "ShortSlow"}) {
        mesh::MtChannel c;
        mesh::mt_channel(n, "AQ==", c);
        mt.push_back(c);
    }

    mesh::CapLoraConfig cfg;
    auto source = mesh::make_cap_lora_source(cfg);
    source->set_profiles(profiles, mesh::SnifferTiming{});
    source->start([&](mesh::RxPacket&& pkt) {
        std::string text;
        if (!pkt.crc_ok) text = "CRC error";
        else if (pkt.profile.sync == mesh::kSyncMeshCore)
            text = mesh::describe_meshcore(mesh::decode_meshcore(pkt.data.data(), pkt.data.size(), mc));
        else if (pkt.profile.sync == mesh::kSyncMeshtastic)
            text = mesh::describe_meshtastic(mesh::decode_meshtastic(pkt.data.data(), pkt.data.size(), mt));
        std::printf("RX %-22s rssi=%6.1f snr=%5.2f len=%3zu  %s\n", pkt.profile.key.c_str(), pkt.rssi, pkt.snr,
                    pkt.data.size(), text.c_str());
        std::fflush(stdout);
    });

    mesh::GpsReader gps;
    gps.start("/dev/ttyS0", 115200);

    const char* state_names[] = {"stopped", "starting", "listening", "error"};
    for (int t = 0; t < seconds; ++t) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        if (t % 5 == 4 || t == 0) {
            const auto s = source->status();
            std::printf("[%3ds] radio=%s rssi_now=%.1f hops=%llu %s\n", t + 1,
                        state_names[static_cast<int>(s.state)], s.rssi_now,
                        static_cast<unsigned long long>(s.hops), s.message.c_str());
            for (const auto& p : s.stats) {
                std::printf("        %-22s ok=%u bad=%u pre=%u listen=%llus\n", p.key.c_str(), p.rx_ok, p.rx_bad,
                            p.preamble_only, static_cast<unsigned long long>(p.listen_ms / 1000));
            }
            const auto g = gps.status();
            std::printf("        gps dev=%s open=%d rx=%d fix=%d sats=%d utc=%s %s\n", g.device.c_str(), g.port_open,
                        g.receiving, g.has_fix, g.satellites, g.utc_time.c_str(), g.message.c_str());
            std::fflush(stdout);
        }
    }
    source->stop();
    gps.stop();
    return 0;
}
