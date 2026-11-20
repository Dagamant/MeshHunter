#include "sx1262.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <thread>
#include <vector>

namespace mesh {
namespace {

// Opcodes
constexpr uint8_t SET_STANDBY = 0x80;
constexpr uint8_t SET_RX = 0x82;
constexpr uint8_t SET_RF_FREQUENCY = 0x86;
constexpr uint8_t SET_PACKET_TYPE = 0x8A;
constexpr uint8_t SET_MODULATION_PARAMS = 0x8B;
constexpr uint8_t SET_PACKET_PARAMS = 0x8C;
constexpr uint8_t SET_BUFFER_BASE = 0x8F;
constexpr uint8_t CALIBRATE = 0x89;
constexpr uint8_t SET_REGULATOR_MODE = 0x96;
constexpr uint8_t SET_DIO3_TCXO = 0x97;
constexpr uint8_t CALIBRATE_IMAGE = 0x98;
constexpr uint8_t SET_DIO2_RF_SWITCH = 0x9D;
constexpr uint8_t SET_DIO_IRQ_PARAMS = 0x08;
constexpr uint8_t CLEAR_IRQ = 0x02;
constexpr uint8_t CLEAR_DEVICE_ERRORS = 0x07;
constexpr uint8_t GET_IRQ = 0x12;
constexpr uint8_t GET_RX_BUFFER_STATUS = 0x13;
constexpr uint8_t GET_PACKET_STATUS = 0x14;
constexpr uint8_t GET_RSSI_INST = 0x15;
constexpr uint8_t WRITE_REGISTER = 0x0D;
constexpr uint8_t READ_REGISTER = 0x1D;
constexpr uint8_t READ_BUFFER = 0x1E;

constexpr uint16_t REG_SYNC_WORD = 0x0740;
constexpr uint16_t REG_RX_GAIN = 0x08AC;
constexpr uint8_t RX_GAIN_BOOSTED = 0x96;

uint8_t bw_code(double bw) {
    struct Entry {
        double khz;
        uint8_t code;
    };
    static const Entry kCodes[] = {{7.8, 0x00},  {10.4, 0x08}, {15.6, 0x01}, {20.8, 0x09}, {31.25, 0x02},
                                   {41.7, 0x0A}, {62.5, 0x03}, {125.0, 0x04}, {250.0, 0x05}, {500.0, 0x06}};
    for (const auto& e : kCodes) {
        if (std::fabs(e.khz - bw) < 0.05) return e.code;
    }
    throw RadioError("unsupported bandwidth " + std::to_string(bw) + " kHz");
}

uint8_t tcxo_code(float volts) {
    struct Entry {
        float v;
        uint8_t code;
    };
    static const Entry kCodes[] = {{1.6f, 0x00}, {1.7f, 0x01}, {1.8f, 0x02}, {2.2f, 0x03},
                                   {2.4f, 0x04}, {2.7f, 0x05}, {3.0f, 0x06}, {3.3f, 0x07}};
    for (const auto& e : kCodes) {
        if (std::fabs(e.v - volts) < 0.05f) return e.code;
    }
    throw RadioError("unsupported TCXO voltage");
}

}  // namespace

double time_on_air_ms(int sf, double bw, int cr, int preamble, int length, bool crc, bool explicit_hdr) {
    const double sym = static_cast<double>(1 << sf) / bw;
    const int de = sym >= 16.0 ? 1 : 0;
    const int payload_bits = 8 * length - 4 * sf + 28 + (crc ? 16 : 0) - (explicit_hdr ? 0 : 20);
    const double n_payload =
        8 + std::max(std::ceil(static_cast<double>(payload_bits) / (4 * (sf - 2 * de))), 0.0) * cr;
    return (preamble + 4.25 + n_payload) * sym;
}

Sx1262::Sx1262(RadioIo& io, float tcxo_volts, bool dio2_rf_switch, bool boosted_rx)
    : io_(io), tcxo_volts_(tcxo_volts), dio2_rf_switch_(dio2_rf_switch), boosted_rx_(boosted_rx) {}

void Sx1262::wait_busy(int timeout_ms) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    while (io_.busy()) {
        if (std::chrono::steady_clock::now() > deadline) {
            throw RadioError("SX1262 stuck busy (no radio, or Cap not powered?)");
        }
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
}

Bytes Sx1262::cmd(uint8_t opcode, std::initializer_list<uint8_t> args, size_t read) {
    wait_busy(200);
    std::vector<uint8_t> tx(1 + args.size() + read, 0);
    tx[0] = opcode;
    std::copy(args.begin(), args.end(), tx.begin() + 1);
    std::vector<uint8_t> rx(tx.size(), 0);
    io_.transfer(tx.data(), rx.data(), tx.size());
    return Bytes(rx.begin() + 1 + static_cast<long>(args.size()), rx.end());
}

void Sx1262::write_register(uint16_t addr, std::initializer_list<uint8_t> values) {
    std::vector<uint8_t> args = {static_cast<uint8_t>(addr >> 8), static_cast<uint8_t>(addr & 0xFF)};
    args.insert(args.end(), values.begin(), values.end());
    wait_busy(200);
    std::vector<uint8_t> tx;
    tx.push_back(WRITE_REGISTER);
    tx.insert(tx.end(), args.begin(), args.end());
    std::vector<uint8_t> rx(tx.size(), 0);
    io_.transfer(tx.data(), rx.data(), tx.size());
}

Bytes Sx1262::read_register(uint16_t addr, size_t count) {
    // The NOP status byte after the address is part of the args, so what's
    // left is exactly the register contents.
    return cmd(READ_REGISTER, {static_cast<uint8_t>(addr >> 8), static_cast<uint8_t>(addr & 0xFF), 0}, count);
}

void Sx1262::reset() {
    io_.set_reset(false);
    std::this_thread::sleep_for(std::chrono::microseconds(200));
    io_.set_reset(true);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    wait_busy(500);
}

void Sx1262::begin() {
    reset();
    standby();
    // The sync word register reads 0x1424 after reset on every SX126x: a
    // cheap "is there really a radio on this bus" check.
    const Bytes sw = read_register(REG_SYNC_WORD, 2);
    if (sw.size() != 2 || sw[0] != 0x14 || sw[1] != 0x24) {
        throw RadioError("no SX126x answering on SPI (sync register read " + to_hex(sw) + ")");
    }
    if (tcxo_volts_ > 0.0f) {
        constexpr uint32_t delay = 320;  // 5 ms in 15.625 us steps, like RadioLib
        cmd(SET_DIO3_TCXO, {tcxo_code(tcxo_volts_), static_cast<uint8_t>(delay >> 16),
                            static_cast<uint8_t>((delay >> 8) & 0xFF), static_cast<uint8_t>(delay & 0xFF)});
        cmd(CLEAR_DEVICE_ERRORS, {0x00, 0x00});
        cmd(CALIBRATE, {0x7F});
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        wait_busy(100);
        standby();
    }
    cmd(SET_REGULATOR_MODE, {0x01});  // DC-DC
    cmd(SET_PACKET_TYPE, {0x01});     // LoRa
    if (dio2_rf_switch_) cmd(SET_DIO2_RF_SWITCH, {0x01});
    cmd(SET_BUFFER_BASE, {0x00, 0x00});
    constexpr uint16_t irq = kIrqRxDone | kIrqPreamble | kIrqHeaderValid | kIrqHeaderErr | kIrqCrcErr | kIrqTimeout;
    cmd(SET_DIO_IRQ_PARAMS, {static_cast<uint8_t>(irq >> 8), static_cast<uint8_t>(irq & 0xFF),
                             static_cast<uint8_t>(irq >> 8), static_cast<uint8_t>(irq & 0xFF), 0, 0, 0, 0});
}

void Sx1262::standby() { cmd(SET_STANDBY, {0x00}); }

void Sx1262::calibrate_image(double freq_min_mhz, double freq_max_mhz) {
    // Datasheet 9.2.1: the band edges in 4 MHz steps.
    const auto lo = static_cast<uint8_t>(std::floor(freq_min_mhz / 4.0));
    const auto hi = static_cast<uint8_t>(std::ceil(freq_max_mhz / 4.0));
    standby();
    cmd(CALIBRATE_IMAGE, {lo, hi});
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    wait_busy(100);
}

void Sx1262::configure(double freq_mhz, double bw_khz, int sf, int cr, uint8_t sync, uint16_t preamble) {
    standby();
    const auto frf = static_cast<uint32_t>(std::llround(freq_mhz * 1e6 * static_cast<double>(1u << 25) / 32e6));
    cmd(SET_RF_FREQUENCY, {static_cast<uint8_t>((frf >> 24) & 0xFF), static_cast<uint8_t>((frf >> 16) & 0xFF),
                           static_cast<uint8_t>((frf >> 8) & 0xFF), static_cast<uint8_t>(frf & 0xFF)});
    const uint8_t ldro = static_cast<double>(1u << sf) / bw_khz >= 16.0 ? 1 : 0;
    cmd(SET_MODULATION_PARAMS, {static_cast<uint8_t>(sf), bw_code(bw_khz), static_cast<uint8_t>(cr - 4), ldro});
    // explicit header, max payload 255, CRC on, standard IQ
    cmd(SET_PACKET_PARAMS,
        {static_cast<uint8_t>(preamble >> 8), static_cast<uint8_t>(preamble & 0xFF), 0x00, 0xFF, 0x01, 0x00});
    write_register(REG_SYNC_WORD, {static_cast<uint8_t>((sync & 0xF0) | 0x04),
                                   static_cast<uint8_t>(((sync & 0x0F) << 4) | 0x04)});
}

void Sx1262::start_rx() {
    cmd(CLEAR_IRQ, {static_cast<uint8_t>(kIrqAll >> 8), static_cast<uint8_t>(kIrqAll & 0xFF)});
    if (boosted_rx_) write_register(REG_RX_GAIN, {RX_GAIN_BOOSTED});
    cmd(SET_RX, {0xFF, 0xFF, 0xFF});  // continuous
}

uint16_t Sx1262::irq_status() {
    const Bytes r = cmd(GET_IRQ, {}, 3);
    return static_cast<uint16_t>((r[1] << 8) | r[2]);
}

float Sx1262::rssi_inst() {
    const Bytes r = cmd(GET_RSSI_INST, {}, 2);
    return -static_cast<float>(r[1]) / 2.0f;
}

void Sx1262::read_packet(Bytes& data, float& rssi, float& snr) {
    const Bytes st = cmd(GET_RX_BUFFER_STATUS, {}, 3);
    const uint8_t length = st[1];
    const uint8_t start = st[2];
    const Bytes ps = cmd(GET_PACKET_STATUS, {}, 4);
    rssi = -static_cast<float>(ps[1]) / 2.0f;
    snr = static_cast<float>(static_cast<int8_t>(ps[2])) / 4.0f;
    data.clear();
    if (length) {
        const Bytes buf = cmd(READ_BUFFER, {start}, static_cast<size_t>(length) + 1);
        data.assign(buf.begin() + 1, buf.end());
    }
}

}  // namespace mesh
