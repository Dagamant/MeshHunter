// MeshHunter LoRa sniffer firmware
//
// Receive-only. Listens on one LoRa "profile" (frequency + modulation +
// sync word) or hops across several, and reports everything it hears to
// the MeshHunter app over USB serial as one JSON object per line. All
// decoding (MeshCore / Meshtastic) happens host-side, so this firmware
// never needs updating to support a new packet type.
//
// Hardware: Seeed XIAO nRF52840 + Wio-SX1262 (XIAO kit). Pin numbers below
// are raw nRF GPIO numbers (see variants/xiao_nrf52840_raw).
//
// ---- Host -> device commands (newline-terminated, space-separated) ------
//   HELLO                         reply with a hello line
//   STOP                          stop listening (radio to standby)
//   CLR                           clear the profile list
//   P <id> <freq_mhz> <bw_khz> <sf> <cr> <sync_hex> <preamble>
//                                 add a profile (max MAX_PROFILES)
//   T <dwell_ms> <linger_ms> <partial_linger_ms> <linger_max_ms>
//                                 hop timing; dwell 0 = auto per profile
//   GO                            start: 1 profile = fixed, >1 = hop
//
// ---- Device -> host (JSON lines) ----------------------------------------
//   {"ev":"hello",...}            sent every second while idle
//   {"ev":"pkt",...}              a received packet (crc:1 ok, crc:0 bad)
//   {"ev":"partial",...}          signal heard but no packet (hdr/timeout)
//   {"ev":"stat",...}             per-profile counters, every 5 s
//   {"ev":"ok"/"err",...}         command replies
//
// ---- Hop behaviour -------------------------------------------------------
//   LISTEN on a profile for its dwell time. If a preamble or valid header
//   is detected, LOCK onto it until the packet finishes (or times out).
//   After a good packet, LINGER on that profile for linger_ms (each new
//   packet extends it, capped at linger_max_ms from the first hit), since
//   mesh packets are usually rebroadcast by neighbours within a second or
//   two. After a partial (header CRC error / payload CRC error / timeout
//   mid-packet) linger for partial_linger_ms hoping to catch a repeat.

#include <Arduino.h>
#include <RadioLib.h>
#include <SPI.h>

// Wio-SX1262 for XIAO: NSS=D4 DIO1=D1 RST=D2 BUSY=D3 RXEN=D5,
// DIO2 drives the TX switch, 1.8 V TCXO on DIO3.
#define PIN_LORA_NSS   4    // P0.04
#define PIN_LORA_DIO1  3    // P0.03
#define PIN_LORA_RST   28   // P0.28
#define PIN_LORA_BUSY  29   // P0.29
#define PIN_LORA_RXEN  5    // P0.05
#define LORA_TCXO_V    1.8f

#define FW_NAME     "meshhunter-sniffer"
#define FW_VERSION  1
#define MAX_PROFILES 16
#define STAT_INTERVAL_MS 5000
#define HELLO_INTERVAL_MS 1000

SX1262 radio = new Module(PIN_LORA_NSS, PIN_LORA_DIO1, PIN_LORA_RST, PIN_LORA_BUSY);

struct Profile {
  int id;
  float freq;
  float bw;
  uint8_t sf;
  uint8_t cr;
  uint8_t sync;
  uint16_t preamble;
  // counters, reset on each GO
  uint32_t rx_ok, rx_crc, hdr_err, timeouts, pre_only;
  uint32_t listen_ms;
};

static Profile profiles[MAX_PROFILES];
static int numProfiles = 0;

static uint32_t cfgDwellMs = 0;  // 0 = auto
static uint32_t cfgLingerMs = 3000;
static uint32_t cfgPartialLingerMs = 2000;
static uint32_t cfgLingerMaxMs = 12000;

enum State { IDLE, LISTEN, LOCKED };
static State state = IDLE;
static int radioInitState = RADIOLIB_ERR_UNKNOWN;
static int cur = 0;
static uint32_t dwellEnd = 0;      // when we may hop away (ignoring linger)
static uint32_t lingerUntil = 0;   // stay at least until this time
static uint32_t lingerStart = 0;   // first hit of the current linger run
static bool lingering = false;
static uint32_t lockDeadline = 0;
static bool lockHeader = false;
static uint32_t profileEnteredAt = 0;
static uint32_t lastStat = 0;

// Waterfall feed: peak instantaneous RSSI per profile, sent every
// RSSI_REPORT_MS. A profile not visited during a window is left out.
#define RSSI_REPORT_MS 200
#define RSSI_SETTLE_MS 3   // let RSSI settle after a retune before sampling
static float rsMax[MAX_PROFILES];
static bool rsHave[MAX_PROFILES];
static uint32_t lastRssiReport = 0;
static uint32_t lastHello = 0;
static uint32_t hops = 0;
static bool wasConnected = false;
static uint32_t ledOffAt = 0;

static char lineBuf[160];
static size_t lineLen = 0;

static uint8_t pktBuf[256];

// ---------------------------------------------------------------------------

static float symbolMs(const Profile &p) { return (float)(1UL << p.sf) / p.bw; }

static uint32_t dwellFor(const Profile &p) {
  if (cfgDwellMs) return cfgDwellMs;
  // Long enough for the SX1262 to detect a preamble that's already in
  // progress: ~10 symbols, never shorter than 8 ms.
  uint32_t ms = (uint32_t)ceilf(symbolMs(p) * 10.0f);
  return ms < 8 ? 8 : ms;
}

static void ledPulse(uint8_t pin, uint32_t ms) {
  digitalWrite(LED_BLUE, !LED_STATE_ON);
  digitalWrite(LED_GREEN, !LED_STATE_ON);
  digitalWrite(pin, LED_STATE_ON);
  ledOffAt = millis() + ms;
}

static void printProfileFields(const Profile &p) {
  Serial.print("\"p\":");   Serial.print(p.id);
  Serial.print(",\"f\":");  Serial.print(p.freq, 4);
  Serial.print(",\"bw\":"); Serial.print(p.bw, 2);
  Serial.print(",\"sf\":"); Serial.print(p.sf);
  Serial.print(",\"cr\":"); Serial.print(p.cr);
  Serial.print(",\"sw\":"); Serial.print(p.sync);
}

static void sendHello() {
  Serial.print("{\"ev\":\"hello\",\"fw\":\"" FW_NAME "\",\"ver\":");
  Serial.print(FW_VERSION);
  Serial.print(",\"radio\":");
  Serial.print(radioInitState);
  Serial.print(",\"running\":");
  Serial.print(state == IDLE ? 0 : 1);
  Serial.print(",\"max_profiles\":");
  Serial.print(MAX_PROFILES);
  Serial.println("}");
}

static void sendErr(const char *msg, int code = 0) {
  Serial.print("{\"ev\":\"err\",\"msg\":\"");
  Serial.print(msg);
  Serial.print("\",\"code\":");
  Serial.print(code);
  Serial.println("}");
}

static void sendOk(const char *cmd) {
  Serial.print("{\"ev\":\"ok\",\"cmd\":\"");
  Serial.print(cmd);
  Serial.println("\"}");
}

static void sendStat() {
  Serial.print("{\"ev\":\"stat\",\"ms\":");
  Serial.print(millis());
  Serial.print(",\"cur\":");
  Serial.print(state == IDLE ? -1 : profiles[cur].id);
  Serial.print(",\"hops\":");
  Serial.print(hops);
  // Instantaneous RSSI on the current profile: the noise floor when idle.
  // Around -100..-120 dBm means the receive path is alive; pinned near the
  // bottom of the scale suggests the LNA/RX switch or antenna isn't.
  Serial.print(",\"nf\":");
  Serial.print(state == IDLE ? 0.0f : radio.getRSSI(false), 1);
  Serial.print(",\"profiles\":[");
  for (int i = 0; i < numProfiles; i++) {
    Profile &p = profiles[i];
    if (i) Serial.print(",");
    // [id, rx_ok, rx_crc_err, hdr_err, timeouts, preamble_only, listen_ms]
    Serial.print("[");
    Serial.print(p.id); Serial.print(",");
    Serial.print(p.rx_ok); Serial.print(",");
    Serial.print(p.rx_crc); Serial.print(",");
    Serial.print(p.hdr_err); Serial.print(",");
    Serial.print(p.timeouts); Serial.print(",");
    Serial.print(p.pre_only); Serial.print(",");
    // include the in-progress visit, or a fixed profile would always read 0
    Serial.print(p.listen_ms + ((state != IDLE && i == cur) ? millis() - profileEnteredAt : 0));
    Serial.print("]");
  }
  Serial.println("]}");
}

static void sampleRssi() {
  if (millis() - profileEnteredAt < RSSI_SETTLE_MS) return;
  float r = radio.getRSSI(false);
  if (!rsHave[cur] || r > rsMax[cur]) rsMax[cur] = r;
  rsHave[cur] = true;
}

static void sendRssiReport() {
  Serial.print("{\"ev\":\"rs\",\"cur\":");
  Serial.print(profiles[cur].id);
  Serial.print(",\"v\":[");
  bool first = true;
  for (int i = 0; i < numProfiles; i++) {
    if (!rsHave[i]) continue;
    if (!first) Serial.print(",");
    first = false;
    Serial.print("[");
    Serial.print(profiles[i].id);
    Serial.print(",");
    Serial.print(rsMax[i], 1);
    Serial.print("]");
    rsHave[i] = false;
  }
  Serial.println("]}");
}

static void sendPartial(const Profile &p, const char *kind) {
  Serial.print("{\"ev\":\"partial\",\"kind\":\"");
  Serial.print(kind);
  Serial.print("\",");
  printProfileFields(p);
  Serial.print(",\"rssi\":");
  Serial.print(radio.getRSSI(false), 1);
  Serial.print(",\"ms\":");
  Serial.print(millis());
  Serial.println("}");
}

static const char HEX_CHARS[] = "0123456789abcdef";

static void sendPacket(const Profile &p, size_t len, bool crcOk, float rssi, float snr) {
  Serial.print("{\"ev\":\"pkt\",");
  printProfileFields(p);
  Serial.print(",\"rssi\":"); Serial.print(rssi, 1);
  Serial.print(",\"snr\":");  Serial.print(snr, 2);
  Serial.print(",\"crc\":");  Serial.print(crcOk ? 1 : 0);
  Serial.print(",\"ms\":");   Serial.print(millis());
  Serial.print(",\"hex\":\"");
  char chunk[64];
  size_t n = 0;
  for (size_t i = 0; i < len; i++) {
    chunk[n++] = HEX_CHARS[pktBuf[i] >> 4];
    chunk[n++] = HEX_CHARS[pktBuf[i] & 0x0f];
    if (n >= sizeof(chunk) - 1) { Serial.write(chunk, n); n = 0; }
  }
  if (n) Serial.write(chunk, n);
  Serial.println("\"}");
}

// ---------------------------------------------------------------------------

static bool applyProfile(const Profile &p) {
  int s = radio.standby();
  if (s == RADIOLIB_ERR_NONE) s = radio.setFrequency(p.freq, true);  // band already calibrated
  if (s == RADIOLIB_ERR_NONE) s = radio.setBandwidth(p.bw);
  if (s == RADIOLIB_ERR_NONE) s = radio.setSpreadingFactor(p.sf);
  if (s == RADIOLIB_ERR_NONE) s = radio.setCodingRate(p.cr);
  if (s == RADIOLIB_ERR_NONE) s = radio.setSyncWord(p.sync);
  if (s == RADIOLIB_ERR_NONE) s = radio.setPreambleLength(p.preamble);
  if (s != RADIOLIB_ERR_NONE) {
    sendErr("profile apply failed", s);
    return false;
  }
  return true;
}

static void startRx() {
  RadioLibIrqFlags_t flags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED);
  int s = radio.startReceive(RADIOLIB_SX126X_RX_TIMEOUT_INF, flags, RADIOLIB_IRQ_RX_DEFAULT_MASK, 0);
  if (s != RADIOLIB_ERR_NONE) sendErr("startReceive failed", s);
  state = LISTEN;
  lockHeader = false;
}

static void enterProfile(int idx) {
  uint32_t now = millis();
  if (state != IDLE && numProfiles > 0) profiles[cur].listen_ms += now - profileEnteredAt;
  cur = idx;
  profileEnteredAt = now;
  lingering = false;
  lingerUntil = 0;
  if (!applyProfile(profiles[cur])) return;
  dwellEnd = now + dwellFor(profiles[cur]);
  startRx();
}

static void stopRadio() {
  if (state != IDLE && numProfiles > 0) profiles[cur].listen_ms += millis() - profileEnteredAt;
  radio.standby();
  state = IDLE;
}

static void extendLinger(uint32_t ms) {
  if (numProfiles < 2) return;  // fixed mode never leaves anyway
  uint32_t now = millis();
  if (!lingering) {
    lingering = true;
    lingerStart = now;
  }
  uint32_t until = now + ms;
  uint32_t cap = lingerStart + cfgLingerMaxMs;
  if ((int32_t)(until - cap) > 0) until = cap;
  if ((int32_t)(until - lingerUntil) > 0) lingerUntil = until;
}

// ---------------------------------------------------------------------------

static void handleCommand(char *line) {
  char *argv[10];
  int argc = 0;
  for (char *tok = strtok(line, " \t\r"); tok && argc < 10; tok = strtok(nullptr, " \t\r")) argv[argc++] = tok;
  if (argc == 0) return;
  const char *cmd = argv[0];

  if (!strcmp(cmd, "HELLO")) {
    sendHello();
  } else if (!strcmp(cmd, "STOP")) {
    stopRadio();
    sendOk("STOP");
  } else if (!strcmp(cmd, "CLR")) {
    stopRadio();
    numProfiles = 0;
    sendOk("CLR");
  } else if (!strcmp(cmd, "P")) {
    if (argc != 8) { sendErr("P needs 7 args"); return; }
    if (numProfiles >= MAX_PROFILES) { sendErr("too many profiles"); return; }
    Profile p = {};
    p.id = atoi(argv[1]);
    p.freq = atof(argv[2]);
    p.bw = atof(argv[3]);
    p.sf = (uint8_t)atoi(argv[4]);
    p.cr = (uint8_t)atoi(argv[5]);
    p.sync = (uint8_t)strtoul(argv[6], nullptr, 16);
    p.preamble = (uint16_t)atoi(argv[7]);
    if (p.freq < 150.0f || p.freq > 960.0f || p.sf < 5 || p.sf > 12 || p.cr < 5 || p.cr > 8 || p.bw < 7.0f) {
      sendErr("bad profile");
      return;
    }
    profiles[numProfiles++] = p;
    sendOk("P");
  } else if (!strcmp(cmd, "T")) {
    if (argc != 5) { sendErr("T needs 4 args"); return; }
    cfgDwellMs = strtoul(argv[1], nullptr, 10);
    cfgLingerMs = strtoul(argv[2], nullptr, 10);
    cfgPartialLingerMs = strtoul(argv[3], nullptr, 10);
    cfgLingerMaxMs = strtoul(argv[4], nullptr, 10);
    sendOk("T");
  } else if (!strcmp(cmd, "GO")) {
    if (radioInitState != RADIOLIB_ERR_NONE) { sendErr("radio not initialised", radioInitState); return; }
    if (numProfiles == 0) { sendErr("no profiles"); return; }
    for (int i = 0; i < numProfiles; i++) {
      Profile &p = profiles[i];
      p.rx_ok = p.rx_crc = p.hdr_err = p.timeouts = p.pre_only = p.listen_ms = 0;
    }
    hops = 0;
    state = LISTEN;  // so enterProfile's bookkeeping is consistent
    profileEnteredAt = millis();
    enterProfile(0);
    lastStat = millis();
    sendOk("GO");
  } else {
    sendErr("unknown command");
  }
}

static void pollSerial() {
  while (Serial.available()) {
    int c = Serial.read();
    if (c < 0) break;
    if (c == '\n') {
      lineBuf[lineLen] = 0;
      handleCommand(lineBuf);
      lineLen = 0;
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = (char)c;
    } else {
      lineLen = 0;  // overlong line: drop it
    }
  }
}

// ---------------------------------------------------------------------------

static void pollRadio() {
  Profile &p = profiles[cur];
  uint32_t now = millis();
  uint32_t irq = radio.getIrqFlags();

  if (irq & RADIOLIB_SX126X_IRQ_RX_DONE) {
    size_t len = radio.getPacketLength();
    if (len > sizeof(pktBuf)) len = sizeof(pktBuf);
    float rssi = radio.getRSSI();
    float snr = radio.getSNR();
    int s = radio.readData(pktBuf, len);
    bool crcOk = (s == RADIOLIB_ERR_NONE);
    if (crcOk || s == RADIOLIB_ERR_CRC_MISMATCH) {
      sendPacket(p, len, crcOk, rssi, snr);
      if (crcOk) {
        p.rx_ok++;
        extendLinger(cfgLingerMs);
        ledPulse(LED_BLUE, 40);
      } else {
        p.rx_crc++;
        extendLinger(cfgPartialLingerMs);
        ledPulse(LED_GREEN, 40);
      }
    } else {
      sendErr("readData failed", s);
    }
    startRx();
    return;
  }

  if (irq & RADIOLIB_SX126X_IRQ_HEADER_ERR) {
    p.hdr_err++;
    sendPartial(p, "header");
    extendLinger(cfgPartialLingerMs);
    startRx();
    return;
  }

  if (state == LISTEN && (irq & (RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED | RADIOLIB_SX126X_IRQ_HEADER_VALID))) {
    state = LOCKED;
    lockHeader = false;
    // Wait for the rest of the preamble + sync + header before deciding it
    // was noise. The detector fires early in the preamble and senders'
    // preambles vary (MeshCore measured ~32 symbols: the header arrived 42
    // symbols after detection), so allow 64 symbols or preamble+32.
    float lockSymbols = p.preamble + 32.0f > 64.0f ? p.preamble + 32.0f : 64.0f;
    lockDeadline = now + (uint32_t)ceilf(symbolMs(p) * lockSymbols) + 5;
  }

  if (state == LOCKED) {
    if (!lockHeader && (irq & RADIOLIB_SX126X_IRQ_HEADER_VALID)) {
      lockHeader = true;
      // Longest possible packet on this profile, plus margin.
      lockDeadline = now + radio.getTimeOnAir(255) / 1000 + 50;
    }
    if ((int32_t)(now - lockDeadline) > 0) {
      if (lockHeader) {
        p.timeouts++;
        sendPartial(p, "timeout");
        extendLinger(cfgPartialLingerMs);
      } else {
        // Preamble detector fired but no header ever followed: usually a
        // packet we joined too late, sometimes noise. Counted, not lingered.
        p.pre_only++;
      }
      startRx();
    }
    return;  // never hop while a packet may be in flight
  }

  // LISTEN: hop when both the dwell and any linger have expired.
  if (numProfiles > 1) {
    bool dwellDone = (int32_t)(now - dwellEnd) >= 0;
    bool lingerDone = !lingering || (int32_t)(now - lingerUntil) >= 0;
    if (dwellDone && lingerDone) {
      hops++;
      enterProfile((cur + 1) % numProfiles);
    }
  }
}

// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);

  pinMode(LED_RED, OUTPUT);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_BLUE, OUTPUT);
  digitalWrite(LED_RED, !LED_STATE_ON);
  digitalWrite(LED_GREEN, !LED_STATE_ON);
  digitalWrite(LED_BLUE, !LED_STATE_ON);

  SPI.begin();
  radio.setRfSwitchPins(PIN_LORA_RXEN, RADIOLIB_NC);
  // MeshCore US defaults; overwritten by the first profile on GO.
  radioInitState = radio.begin(910.525f, 62.5f, 7, 5, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, 10, 8, LORA_TCXO_V, false);
  if (radioInitState == RADIOLIB_ERR_NONE) {
    radio.setDio2AsRfSwitch(true);
    radio.setCRC(2);
    radio.setRxBoostedGainMode(true);
    // Image-calibrate once for the whole US 902-928 MHz band so hopping
    // can skip per-frequency calibration.
    radio.setFrequency(915.0f);
    radio.standby();
  } else {
    digitalWrite(LED_RED, LED_STATE_ON);
  }
}

void loop() {
  uint32_t now = millis();
  bool connected = (bool)Serial;

  // Host closed the port (DTR dropped): stop, so the next connection starts
  // from a clean, known state and sees hello lines immediately.
  if (wasConnected && !connected && state != IDLE) stopRadio();
  wasConnected = connected;

  pollSerial();

  if (ledOffAt && (int32_t)(now - ledOffAt) >= 0) {
    digitalWrite(LED_BLUE, !LED_STATE_ON);
    digitalWrite(LED_GREEN, !LED_STATE_ON);
    ledOffAt = 0;
  }

  if (state == IDLE) {
    if (connected && now - lastHello >= HELLO_INTERVAL_MS) {
      lastHello = now;
      sendHello();
    }
    delay(5);
    return;
  }

  pollRadio();
  if (state != IDLE) sampleRssi();

  if (state != IDLE && now - lastRssiReport >= RSSI_REPORT_MS) {
    lastRssiReport = now;
    sendRssiReport();
  }

  if (now - lastStat >= STAT_INTERVAL_MS) {
    lastStat = now;
    sendStat();
  }
  delay(1);
}
