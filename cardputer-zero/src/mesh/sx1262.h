// Minimal receive-only SX1262 driver, ported from meshhunter/core/sx1262.py.
// Opcodes and register addresses are from the Semtech SX1261/2 datasheet
// (rev 2.1); the init sequence mirrors RadioLib, so this radio behaves the
// same as the XIAO and USB sniffers the desktop app drives.
//
// The bus is abstracted behind RadioIo so the same driver runs over Linux
// spidev + GPIO character devices on the CardputerZero (cap_lora_io.*).

#pragma once

#include "bytes.h"

#include <cstdint>
#include <stdexcept>
#include <string>

namespace mesh {

struct RadioError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

class RadioIo {
public:
    virtual ~RadioIo() = default;
    // One full-duplex SPI transaction with chip-select asserted throughout.
    virtual void transfer(const uint8_t* tx, uint8_t* rx, size_t len) = 0;
    virtual bool busy() = 0;
    virtual void set_reset(bool high) = 0;
    // DIO1 level: 1/0, or -1 if the line isn't wired up (then IRQ status is
    // always polled over SPI).
    virtual int dio1() = 0;
};

constexpr uint16_t kIrqRxDone = 0x0002;
constexpr uint16_t kIrqPreamble = 0x0004;
constexpr uint16_t kIrqHeaderValid = 0x0010;
constexpr uint16_t kIrqHeaderErr = 0x0020;
constexpr uint16_t kIrqCrcErr = 0x0040;
constexpr uint16_t kIrqTimeout = 0x0200;
constexpr uint16_t kIrqAll = 0x03FF;

// LoRa time on air in ms (Semtech AN1200.13; SF7-12, cr = 5..8 for 4/5..4/8).
double time_on_air_ms(int sf, double bw, int cr, int preamble, int length, bool crc = true, bool explicit_hdr = true);

class Sx1262 {
public:
    Sx1262(RadioIo& io, float tcxo_volts, bool dio2_rf_switch, bool boosted_rx);

    // Reset and configure for LoRa receive. Throws RadioError if no SX126x
    // answers.
    void begin();
    void standby();
    // Image calibration for the band [freq_min, freq_max] MHz, so hopping
    // inside it can retune without recalibrating.
    void calibrate_image(double freq_min_mhz, double freq_max_mhz);
    void configure(double freq_mhz, double bw_khz, int sf, int cr, uint8_t sync, uint16_t preamble);
    void start_rx();
    uint16_t irq_status();
    float rssi_inst();
    // Payload of the packet just received, plus its RSSI/SNR.
    void read_packet(Bytes& data, float& rssi, float& snr);

private:
    void wait_busy(int timeout_ms);
    Bytes cmd(uint8_t opcode, std::initializer_list<uint8_t> args, size_t read = 0);
    void write_register(uint16_t addr, std::initializer_list<uint8_t> values);
    Bytes read_register(uint16_t addr, size_t count);
    void reset();

    RadioIo& io_;
    float tcxo_volts_;
    bool dio2_rf_switch_;
    bool boosted_rx_;
};

}  // namespace mesh
