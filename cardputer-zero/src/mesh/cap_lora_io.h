// Linux userspace access to the Cap LoRa-1262's SX1262 on a CardputerZero.
//
// Wiring (matches meshtastic's lora-cardputer-zero.yaml and the M5 Cap app):
//   SPI   /dev/spidev0.1 (kernel drives CE1/GPIO7; CE0 is the LCD)
//   RESET gpiochip0 line 26, BUSY line 22, DIO1/IRQ line 23
//   DIO2 = RF switch, DIO3 = 3.0 V TCXO
// Power-up, before the radio answers:
//   1. /sys/class/leds/ext_5v_out/brightness = 1 (the Cap's 5 V rail)
//   2. PI4IOE5V6408 at I2C1 0x43: P0 driven HIGH (enables the FM8625H
//      antenna switch). Only bit 0 of each register is touched, and it is
//      restored on shutdown.
//
// Everything is released in the destructor.

#pragma once

#include "sx1262.h"

#include <cstdint>
#include <string>

namespace mesh {

struct CapLoraConfig {
    std::string spi_device = "/dev/spidev0.1";
    uint32_t spi_speed_hz = 1000000;
    std::string gpio_chip = "/dev/gpiochip0";
    int reset_line = 26;
    int busy_line = 22;
    int dio1_line = 23;
    std::string power_led = "/sys/class/leds/ext_5v_out/brightness";
    std::string i2c_bus = "/dev/i2c-1";
    int expander_address = 0x43;
};

class CapLoraIo final : public RadioIo {
public:
    explicit CapLoraIo(CapLoraConfig config);
    ~CapLoraIo() override;

    CapLoraIo(const CapLoraIo&) = delete;
    CapLoraIo& operator=(const CapLoraIo&) = delete;

    // Powers the Cap and opens SPI/GPIO. Throws RadioError with a
    // user-presentable message on failure (everything opened so far is
    // released again).
    void open();
    void close();

    void transfer(const uint8_t* tx, uint8_t* rx, size_t len) override;
    bool busy() override;
    void set_reset(bool high) override;
    int dio1() override;

    // Human-readable notes from power-up ("5V rail on", "antenna switch ...").
    const std::string& power_notes() const { return power_notes_; }

private:
    void power_on();
    void power_restore();
    int request_line(int offset, bool output, int initial_value, const char* label);
    int get_line(int fd);

    CapLoraConfig config_;
    int spi_fd_ = -1;
    int reset_fd_ = -1;
    int busy_fd_ = -1;
    int dio1_fd_ = -1;
    std::string saved_brightness_;
    bool brightness_changed_ = false;
    bool expander_saved_ = false;
    uint8_t expander_regs_[3] = {};  // dir, out, hi-z
    std::string power_notes_;
};

}  // namespace mesh
