#include "cap_lora_io.h"

#include <cerrno>
#include <chrono>
#include <cstring>
#include <thread>

#if defined(__linux__)
#include <fcntl.h>
#include <linux/gpio.h>
#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <linux/spi/spidev.h>
#include <sys/ioctl.h>
#include <unistd.h>
#endif

namespace mesh {

namespace {

// PI4IOE5V6408 registers (Diodes datasheet).
constexpr uint8_t kRegIdCtrl = 0x01;
constexpr uint8_t kRegDirection = 0x03;   // 1 = output
constexpr uint8_t kRegOutput = 0x05;      // output level
constexpr uint8_t kRegHighZ = 0x07;       // 1 = high impedance
constexpr uint8_t kManufacturerId = 0xA0;  // bits 7:5 = 101

std::string errno_text(const std::string& what) { return what + ": " + std::strerror(errno); }

#if defined(__linux__)
bool read_file(const std::string& path, std::string& out) {
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return false;
    char buf[32] = {};
    const ssize_t n = ::read(fd, buf, sizeof(buf) - 1);
    ::close(fd);
    if (n <= 0) return false;
    out.assign(buf, static_cast<size_t>(n));
    while (!out.empty() && (out.back() == '\n' || out.back() == '\r')) out.pop_back();
    return true;
}

bool write_file(const std::string& path, const std::string& value) {
    const int fd = ::open(path.c_str(), O_WRONLY | O_CLOEXEC);
    if (fd < 0) return false;
    const ssize_t n = ::write(fd, value.data(), value.size());
    const int saved = errno;
    ::close(fd);
    errno = saved;
    return n == static_cast<ssize_t>(value.size());
}

class I2cDevice {
public:
    I2cDevice(const std::string& bus, int address) {
        fd_ = ::open(bus.c_str(), O_RDWR | O_CLOEXEC);
        if (fd_ < 0) return;
        // Not I2C_SLAVE_FORCE: if a kernel driver owns the address we must
        // not touch it.
        if (ioctl(fd_, I2C_SLAVE, address) < 0) {
            ::close(fd_);
            fd_ = -1;
        }
    }
    ~I2cDevice() {
        if (fd_ >= 0) ::close(fd_);
    }
    bool ok() const { return fd_ >= 0; }
    bool read_reg(uint8_t reg, uint8_t& value) {
        if (::write(fd_, &reg, 1) != 1) return false;
        return ::read(fd_, &value, 1) == 1;
    }
    bool write_reg(uint8_t reg, uint8_t value) {
        const uint8_t buf[2] = {reg, value};
        return ::write(fd_, buf, 2) == 2;
    }

private:
    int fd_ = -1;
};
#endif

}  // namespace

CapLoraIo::CapLoraIo(CapLoraConfig config) : config_(std::move(config)) {}

CapLoraIo::~CapLoraIo() { close(); }

#if defined(__linux__)

void CapLoraIo::power_on() {
    power_notes_.clear();
    // 1. The Cap's 5 V rail (an LED-class switch on the vendor image).
    if (read_file(config_.power_led, saved_brightness_)) {
        if (saved_brightness_ == "0") {
            if (!write_file(config_.power_led, "1")) {
                throw RadioError(errno_text("Can't enable the Cap 5V rail (" + config_.power_led + ")"));
            }
            brightness_changed_ = true;
            std::this_thread::sleep_for(std::chrono::milliseconds(150));
            power_notes_ += "5V rail switched on. ";
        } else {
            power_notes_ += "5V rail already on. ";
        }
    } else {
        power_notes_ += "No ext_5v_out control; assuming the Cap is powered. ";
    }

    // 2. Antenna switch enable on the I/O expander.
    I2cDevice dev(config_.i2c_bus, config_.expander_address);
    if (!dev.ok()) {
        power_notes_ += "I/O expander not reachable (" + std::string(std::strerror(errno)) + "). ";
        return;
    }
    uint8_t id = 0, dir = 0, out = 0, hiz = 0;
    if (!dev.read_reg(kRegIdCtrl, id) || !dev.read_reg(kRegDirection, dir) || !dev.read_reg(kRegOutput, out) ||
        !dev.read_reg(kRegHighZ, hiz)) {
        power_notes_ += "I/O expander did not answer at 0x43. ";
        return;
    }
    if ((id & 0xE0) != kManufacturerId) {
        // Not the chip we expect: leave it alone rather than guess.
        char buf[64];
        std::snprintf(buf, sizeof(buf), "Unexpected I/O expander id 0x%02x; left untouched. ", id);
        power_notes_ += buf;
        return;
    }
    expander_regs_[0] = dir;
    expander_regs_[1] = out;
    expander_regs_[2] = hiz;
    expander_saved_ = true;
    // Level first, then un-tristate, then make it an output, so P0 never
    // glitches low.
    const bool ok = dev.write_reg(kRegOutput, static_cast<uint8_t>(out | 0x01)) &&
                    dev.write_reg(kRegHighZ, static_cast<uint8_t>(hiz & ~0x01)) &&
                    dev.write_reg(kRegDirection, static_cast<uint8_t>(dir | 0x01));
    power_notes_ += ok ? "Antenna switch enabled." : "Antenna switch write failed.";
}

void CapLoraIo::power_restore() {
    if (expander_saved_) {
        I2cDevice dev(config_.i2c_bus, config_.expander_address);
        uint8_t dir = 0, out = 0, hiz = 0;
        if (dev.ok() && dev.read_reg(kRegDirection, dir) && dev.read_reg(kRegOutput, out) &&
            dev.read_reg(kRegHighZ, hiz)) {
            // Only our bit; anything else may belong to another client.
            dev.write_reg(kRegDirection, static_cast<uint8_t>((dir & ~0x01) | (expander_regs_[0] & 0x01)));
            dev.write_reg(kRegHighZ, static_cast<uint8_t>((hiz & ~0x01) | (expander_regs_[2] & 0x01)));
            dev.write_reg(kRegOutput, static_cast<uint8_t>((out & ~0x01) | (expander_regs_[1] & 0x01)));
        }
        expander_saved_ = false;
    }
    if (brightness_changed_) {
        write_file(config_.power_led, saved_brightness_);
        brightness_changed_ = false;
    }
}

int CapLoraIo::request_line(int offset, bool output, int initial_value, const char* label) {
    const int chip = ::open(config_.gpio_chip.c_str(), O_RDWR | O_CLOEXEC);
    if (chip < 0) throw RadioError(errno_text("Can't open " + config_.gpio_chip));
    gpio_v2_line_request req{};
    req.offsets[0] = static_cast<__u32>(offset);
    req.num_lines = 1;
    std::snprintf(req.consumer, sizeof(req.consumer), "meshhunter-%s", label);
    req.config.flags = output ? GPIO_V2_LINE_FLAG_OUTPUT : GPIO_V2_LINE_FLAG_INPUT;
    if (output) {
        req.config.num_attrs = 1;
        req.config.attrs[0].attr.id = GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES;
        req.config.attrs[0].attr.values = initial_value ? 1 : 0;
        req.config.attrs[0].mask = 1;
    }
    const int rc = ioctl(chip, GPIO_V2_GET_LINE_IOCTL, &req);
    const int saved = errno;
    ::close(chip);
    if (rc < 0) {
        errno = saved;
        throw RadioError(errno_text(std::string("Can't claim GPIO") + std::to_string(offset) + " (" + label +
                                    ") - is another LoRa app running?"));
    }
    return req.fd;
}

int CapLoraIo::get_line(int fd) {
    if (fd < 0) return -1;
    gpio_v2_line_values v{};
    v.mask = 1;
    if (ioctl(fd, GPIO_V2_LINE_GET_VALUES_IOCTL, &v) < 0) return -1;
    return (v.bits & 1) ? 1 : 0;
}

void CapLoraIo::open() {
    close();
    try {
        power_on();

        spi_fd_ = ::open(config_.spi_device.c_str(), O_RDWR | O_CLOEXEC);
        if (spi_fd_ < 0) throw RadioError(errno_text("Can't open " + config_.spi_device));
        uint8_t mode = SPI_MODE_0;
        uint8_t bits = 8;
        uint32_t speed = config_.spi_speed_hz;
        if (ioctl(spi_fd_, SPI_IOC_WR_MODE, &mode) < 0 || ioctl(spi_fd_, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 ||
            ioctl(spi_fd_, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) {
            throw RadioError(errno_text("Can't configure " + config_.spi_device));
        }

        reset_fd_ = request_line(config_.reset_line, true, 1, "reset");
        busy_fd_ = request_line(config_.busy_line, false, 0, "busy");
        // DIO1 is an optimisation (skip SPI IRQ polls while it's low), so a
        // failure to claim it isn't fatal.
        try {
            dio1_fd_ = request_line(config_.dio1_line, false, 0, "dio1");
        } catch (const RadioError&) {
            dio1_fd_ = -1;
        }
    } catch (...) {
        close();
        throw;
    }
}

void CapLoraIo::close() {
    for (int* fd : {&spi_fd_, &reset_fd_, &busy_fd_, &dio1_fd_}) {
        if (*fd >= 0) {
            ::close(*fd);
            *fd = -1;
        }
    }
    power_restore();
}

void CapLoraIo::transfer(const uint8_t* tx, uint8_t* rx, size_t len) {
    if (spi_fd_ < 0) throw RadioError("SPI not open");
    spi_ioc_transfer xfer{};
    xfer.tx_buf = reinterpret_cast<__u64>(tx);
    xfer.rx_buf = reinterpret_cast<__u64>(rx);
    xfer.len = static_cast<__u32>(len);
    xfer.speed_hz = config_.spi_speed_hz;
    xfer.bits_per_word = 8;
    if (ioctl(spi_fd_, SPI_IOC_MESSAGE(1), &xfer) < 0) throw RadioError(errno_text("SPI transfer failed"));
}

bool CapLoraIo::busy() {
    const int v = get_line(busy_fd_);
    if (v < 0) throw RadioError("BUSY line read failed");
    return v == 1;
}

void CapLoraIo::set_reset(bool high) {
    if (reset_fd_ < 0) throw RadioError("RESET line not open");
    gpio_v2_line_values v{};
    v.mask = 1;
    v.bits = high ? 1 : 0;
    if (ioctl(reset_fd_, GPIO_V2_LINE_SET_VALUES_IOCTL, &v) < 0) throw RadioError(errno_text("RESET write failed"));
}

int CapLoraIo::dio1() { return get_line(dio1_fd_); }

#else  // !__linux__

void CapLoraIo::power_on() {}
void CapLoraIo::power_restore() {}
int CapLoraIo::request_line(int, bool, int, const char*) { return -1; }
int CapLoraIo::get_line(int) { return -1; }
void CapLoraIo::open() { throw RadioError("Cap LoRa-1262 is only available on the CardputerZero"); }
void CapLoraIo::close() {}
void CapLoraIo::transfer(const uint8_t*, uint8_t*, size_t) { throw RadioError("no radio"); }
bool CapLoraIo::busy() { return false; }
void CapLoraIo::set_reset(bool) {}
int CapLoraIo::dio1() { return -1; }

#endif

}  // namespace mesh
