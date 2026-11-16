// Bluetooth bridge for the Waveshare SC bus servos (SC15 and friends).
//
// Runs on the ESP32 of the Waveshare "Servo Driver with ESP32" board (or any
// classic ESP32 plus a bus servo adapter). It owns the half-duplex servo bus,
// runs the host's bus transactions locally, and streams the position, speed,
// load, voltage and temperature of the servos it was told to watch.
//
// Protocol and host side: software/sorter/backend/hardware/bt_servo_bus.py.
//
// Frames: type, seq, body, CRC16-CCITT (little-endian) -> COBS -> 0x00.
//   host -> bridge  0x01 XFER      body: timeout_ms u8, instruction packet
//                   0x02 HELLO
//                   0x03 SET_POLL  body: period_ms u16, n u8, ids[n]
//                   0x05 HEARTBEAT
//                   0x06 CONFIG    body: watchdog_ms u16, release_on_loss u8
//   bridge -> host  0x81 XFER      body: result u8 (0 ok, 1 timeout, 2 bad), status packet
//                   0x82 HELLO     body: proto u8, major u8, minor u8, uptime_ms u32, poll_n u8, flags u8
//                   0x83 SET_POLL  body: n u8
//                   0x84 TELEMETRY body: ms u32, n u8, n x {id u8, flags u8, pos u16,
//                                        speed u16, load u16, volt u8, temp u8}
//                   0x86 CONFIG
//                   0x8F ERROR     body: code u8
//
// Needs a classic ESP32 (Bluetooth SPP). The ESP32-S3 and -C3 have no SPP.

#include <Arduino.h>
#include "BluetoothSerial.h"

#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error "Bluetooth classic is off: pick a classic ESP32 board profile"
#endif

// ---- Configuration --------------------------------------------------------

#define DEVICE_NAME "SC15-Bridge"

// Servo bus UART. These are the pins of the Waveshare ESP32 servo driver
// board's bus circuit; check them against the board's wiki before flashing.
#define BUS_RX_PIN 18
#define BUS_TX_PIN 19
#define BUS_BAUD 1000000

#define PROTO_VERSION 1
#define FW_MAJOR 1
#define FW_MINOR 0

#define DEFAULT_WATCHDOG_MS 1500
#define MAX_POLL_IDS 16
#define POLL_REG 56  // present position; 8 bytes: pos, speed, load, voltage, temperature
#define POLL_COUNT 8
#define REG_TORQUE_ENABLE 40

enum : uint8_t {
  T_XFER = 0x01, T_HELLO = 0x02, T_SET_POLL = 0x03, T_HEARTBEAT = 0x05, T_CONFIG = 0x06,
  R_XFER = 0x81, R_HELLO = 0x82, R_SET_POLL = 0x83, R_TELEMETRY = 0x84, R_CONFIG = 0x86, R_ERROR = 0x8F,
};
enum : uint8_t { XFER_OK = 0, XFER_TIMEOUT = 1, XFER_BAD = 2 };
enum : uint8_t { ERR_UNKNOWN_TYPE = 1, ERR_BAD_BODY = 2 };

// ---- State ------------------------------------------------------------------

BluetoothSerial SerialBT;

static uint8_t rxChunk[640];
static size_t rxChunkLen = 0;

static uint8_t pollIds[MAX_POLL_IDS];
static uint8_t pollN = 0;
static uint16_t pollPeriodMs = 40;
static uint8_t pollIdx = 0;
static uint32_t lastPollMs = 0;

struct Reading {
  bool valid;
  uint16_t pos, speed, load;
  uint8_t volt, temp;
};
static Reading readings[MAX_POLL_IDS];

static uint16_t watchdogMs = DEFAULT_WATCHDOG_MS;
static bool releaseOnLoss = false;
static uint32_t lastRxMs = 0;
static bool wdTripped = false;
static bool hadClient = false;

// The last XFER answer, kept so a repeated request (same seq, same body) is
// answered from memory instead of being run on the bus twice.
static uint8_t lastSeq = 0;
static uint16_t lastReqCrc = 0;
static uint8_t lastRsp[320];
static size_t lastRspLen = 0;

// ---- CRC16 and COBS -----------------------------------------------------------

static uint16_t crc16(const uint8_t* data, size_t len, uint16_t crc = 0xFFFF) {
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

static size_t cobsEncode(const uint8_t* in, size_t len, uint8_t* out) {
  size_t codeIdx = 0, o = 1;
  uint8_t code = 1;
  for (size_t i = 0; i < len; i++) {
    if (in[i] == 0) {
      out[codeIdx] = code;
      codeIdx = o++;
      code = 1;
    } else {
      out[o++] = in[i];
      if (++code == 0xFF) {
        out[codeIdx] = code;
        codeIdx = o++;
        code = 1;
      }
    }
  }
  out[codeIdx] = code;
  return o;
}

static bool cobsDecode(const uint8_t* in, size_t len, uint8_t* out, size_t& outLen) {
  size_t i = 0, o = 0;
  while (i < len) {
    uint8_t code = in[i++];
    if (code == 0) return false;
    size_t end = i + code - 1;
    if (end > len) return false;
    while (i < end) out[o++] = in[i++];
    if (code != 0xFF && i < len) out[o++] = 0;
  }
  outLen = o;
  return true;
}

// ---- Sending frames to the host ---------------------------------------------------

static void sendFrame(uint8_t type, uint8_t seq, const uint8_t* body, size_t bodyLen) {
  uint8_t raw[320];
  uint8_t enc[330];
  if (bodyLen + 4 > sizeof(raw)) return;
  raw[0] = type;
  raw[1] = seq;
  if (bodyLen) memcpy(raw + 2, body, bodyLen);
  uint16_t crc = crc16(raw, 2 + bodyLen);
  raw[2 + bodyLen] = crc & 0xFF;
  raw[3 + bodyLen] = crc >> 8;
  size_t n = cobsEncode(raw, bodyLen + 4, enc);
  enc[n++] = 0;
  SerialBT.write(enc, n);
}

static void sendError(uint8_t seq, uint8_t code) { sendFrame(R_ERROR, seq, &code, 1); }

// ---- The servo bus ------------------------------------------------------------------

static int busRead(uint32_t deadlineUs) {
  while ((int32_t)(deadlineUs - micros()) > 0) {
    if (Serial1.available()) return Serial1.read();
  }
  return Serial1.available() ? Serial1.read() : -1;
}

// One status (or echoed) packet: FF FF id len err/instr params... checksum.
static bool busReadPacket(uint8_t* out, size_t cap, size_t& outLen, uint32_t deadlineUs) {
  int b = busRead(deadlineUs);
  for (;;) {
    if (b < 0) return false;
    if (b == 0xFF) {
      int b2 = busRead(deadlineUs);
      if (b2 < 0) return false;
      if (b2 == 0xFF) break;
      b = b2;
      continue;
    }
    b = busRead(deadlineUs);
  }
  out[0] = out[1] = 0xFF;
  for (int i = 2; i < 5; i++) {
    int v = busRead(deadlineUs);
    if (v < 0) return false;
    out[i] = (uint8_t)v;
  }
  uint8_t length = out[3];
  if (length < 2) return false;
  size_t total = 4 + (size_t)length;
  if (total > cap) return false;
  for (size_t i = 5; i < total; i++) {
    int v = busRead(deadlineUs);
    if (v < 0) return false;
    out[i] = (uint8_t)v;
  }
  outLen = total;
  return true;
}

// Sends one instruction packet and returns the status packet (echo skipped).
static bool busTransact(const uint8_t* pkt, size_t len, uint32_t timeoutMs, uint8_t* rsp, size_t cap, size_t& rspLen) {
  while (Serial1.available()) Serial1.read();
  Serial1.write(pkt, len);
  Serial1.flush();
  uint32_t deadline = micros() + timeoutMs * 1000UL;
  if (!busReadPacket(rsp, cap, rspLen, deadline)) return false;
  if (rspLen == len && memcmp(rsp, pkt, len) == 0) {  // the adapter echoed our own packet
    if (!busReadPacket(rsp, cap, rspLen, deadline)) return false;
  }
  return true;
}

static uint8_t checksum(const uint8_t* p, size_t n) {
  uint8_t s = 0;
  for (size_t i = 0; i < n; i++) s += p[i];
  return (uint8_t)~s;
}

static void busWriteTorque(uint8_t id, uint8_t on) {
  uint8_t pkt[8] = {0xFF, 0xFF, id, 4, 0x03, REG_TORQUE_ENABLE, on, 0};
  pkt[7] = checksum(pkt + 2, 5);
  uint8_t rsp[16];
  size_t n;
  busTransact(pkt, sizeof(pkt), 10, rsp, sizeof(rsp), n);
}

static void pollOne(uint8_t index) {
  uint8_t id = pollIds[index];
  uint8_t pkt[8] = {0xFF, 0xFF, id, 4, 0x02, POLL_REG, POLL_COUNT, 0};
  pkt[7] = checksum(pkt + 2, 5);
  uint8_t rsp[32];
  size_t n = 0;
  Reading& r = readings[index];
  r.valid = false;
  if (!busTransact(pkt, sizeof(pkt), 10, rsp, sizeof(rsp), n)) return;
  if (n < 6 + POLL_COUNT || rsp[2] != id || rsp[4] != 0) return;
  if (rsp[n - 1] != checksum(rsp + 2, n - 3)) return;
  const uint8_t* p = rsp + 5;
  r.pos = (uint16_t)((p[0] << 8) | p[1]);
  r.speed = (uint16_t)((p[2] << 8) | p[3]);
  r.load = (uint16_t)((p[4] << 8) | p[5]);
  r.volt = p[6];
  r.temp = p[7];
  r.valid = true;
}

static void sendTelemetry() {
  uint8_t body[5 + MAX_POLL_IDS * 10];
  uint32_t ms = millis();
  memcpy(body, &ms, 4);  // little-endian on the ESP32
  body[4] = pollN;
  size_t o = 5;
  for (uint8_t i = 0; i < pollN; i++) {
    const Reading& r = readings[i];
    body[o++] = pollIds[i];
    body[o++] = r.valid ? 1 : 0;
    body[o++] = r.pos & 0xFF;
    body[o++] = r.pos >> 8;
    body[o++] = r.speed & 0xFF;
    body[o++] = r.speed >> 8;
    body[o++] = r.load & 0xFF;
    body[o++] = r.load >> 8;
    body[o++] = r.volt;
    body[o++] = r.temp;
  }
  sendFrame(R_TELEMETRY, 0, body, o);
}

static void pollStep() {
  if (pollN == 0 || !SerialBT.hasClient()) return;
  uint32_t now = millis();
  uint32_t slot = pollPeriodMs / pollN;
  if (slot == 0) slot = 1;
  if (now - lastPollMs < slot) return;
  lastPollMs = now;
  pollOne(pollIdx);
  if (++pollIdx >= pollN) {
    pollIdx = 0;
    sendTelemetry();
  }
}

// ---- Handling host frames -------------------------------------------------------------

static void handleXfer(uint8_t seq, const uint8_t* body, size_t len) {
  uint16_t reqCrc = crc16(body, len);
  if (seq != 0 && seq == lastSeq && reqCrc == lastReqCrc && lastRspLen > 0) {
    SerialBT.write(lastRsp, lastRspLen);  // a repeat: answer from memory
    return;
  }
  uint8_t out[300];
  size_t outLen = 1;
  out[0] = XFER_BAD;
  if (len >= 7) {
    uint8_t timeoutMs = body[0];
    const uint8_t* pkt = body + 1;
    size_t pktLen = len - 1;
    if (pkt[0] == 0xFF && pkt[1] == 0xFF && pktLen == (size_t)pkt[3] + 4) {
      size_t n = 0;
      bool ok = busTransact(pkt, pktLen, timeoutMs ? timeoutMs : 20, out + 1, sizeof(out) - 1, n);
      if (ok) {
        out[0] = XFER_OK;
        outLen = 1 + n;
      } else {
        out[0] = XFER_TIMEOUT;
      }
    }
  }
  // Build the frame once so it can be sent now and again on a repeat.
  uint8_t raw[320];
  uint8_t enc[330];
  raw[0] = R_XFER;
  raw[1] = seq;
  memcpy(raw + 2, out, outLen);
  uint16_t crc = crc16(raw, 2 + outLen);
  raw[2 + outLen] = crc & 0xFF;
  raw[3 + outLen] = crc >> 8;
  size_t n = cobsEncode(raw, outLen + 4, enc);
  enc[n++] = 0;
  SerialBT.write(enc, n);
  if (n <= sizeof(lastRsp)) {
    memcpy(lastRsp, enc, n);
    lastRspLen = n;
    lastSeq = seq;
    lastReqCrc = reqCrc;
  }
}

static void handleFrame(const uint8_t* chunk, size_t chunkLen) {
  uint8_t raw[640];
  size_t rawLen = 0;
  if (chunkLen > sizeof(raw) || !cobsDecode(chunk, chunkLen, raw, rawLen)) return;
  if (rawLen < 4) return;
  uint16_t got = (uint16_t)(raw[rawLen - 2] | (raw[rawLen - 1] << 8));
  if (got != crc16(raw, rawLen - 2)) return;

  uint8_t type = raw[0], seq = raw[1];
  const uint8_t* body = raw + 2;
  size_t bodyLen = rawLen - 4;

  lastRxMs = millis();
  wdTripped = false;

  switch (type) {
    case T_XFER:
      handleXfer(seq, body, bodyLen);
      break;
    case T_HELLO: {
      lastRspLen = 0;  // a new session: forget the last answer
      lastSeq = 0;
      uint8_t out[9] = {PROTO_VERSION, FW_MAJOR, FW_MINOR, 0, 0, 0, 0, pollN, 0};
      uint32_t up = millis();
      memcpy(out + 3, &up, 4);
      sendFrame(R_HELLO, seq, out, sizeof(out));
      break;
    }
    case T_SET_POLL: {
      if (bodyLen < 3 || bodyLen < (size_t)3 + body[2] || body[2] > MAX_POLL_IDS) {
        sendError(seq, ERR_BAD_BODY);
        break;
      }
      uint16_t period = (uint16_t)(body[0] | (body[1] << 8));
      pollPeriodMs = period < 10 ? 10 : (period > 1000 ? 1000 : period);
      pollN = body[2];
      memcpy(pollIds, body + 3, pollN);
      memset(readings, 0, sizeof(readings));
      pollIdx = 0;
      if (seq != 0) sendFrame(R_SET_POLL, seq, &pollN, 1);
      break;
    }
    case T_HEARTBEAT:
      break;
    case T_CONFIG: {
      if (bodyLen < 3) {
        sendError(seq, ERR_BAD_BODY);
        break;
      }
      watchdogMs = (uint16_t)(body[0] | (body[1] << 8));
      releaseOnLoss = body[2] != 0;
      sendFrame(R_CONFIG, seq, nullptr, 0);
      break;
    }
    default:
      sendError(seq, ERR_UNKNOWN_TYPE);
  }
}

static void readHost() {
  while (SerialBT.available()) {
    int b = SerialBT.read();
    if (b < 0) break;
    if (b == 0) {
      if (rxChunkLen > 0) handleFrame(rxChunk, rxChunkLen);
      rxChunkLen = 0;
    } else if (rxChunkLen < sizeof(rxChunk)) {
      rxChunk[rxChunkLen++] = (uint8_t)b;
    } else {
      rxChunkLen = 0;  // too long to be a frame: drop it
    }
  }
}

// The host went quiet: servos keep their last goal; optionally let them go.
static void watchdog() {
  bool client = SerialBT.hasClient();
  if (client != hadClient) {
    hadClient = client;
    lastRspLen = 0;
    lastSeq = 0;
    rxChunkLen = 0;
    lastRxMs = millis();
  }
  if (!client) return;
  if (!wdTripped && watchdogMs > 0 && millis() - lastRxMs > watchdogMs) {
    wdTripped = true;
    if (releaseOnLoss) {
      for (uint8_t i = 0; i < pollN; i++) busWriteTorque(pollIds[i], 0);
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial1.begin(BUS_BAUD, SERIAL_8N1, BUS_RX_PIN, BUS_TX_PIN);
  SerialBT.begin(DEVICE_NAME);
  Serial.printf("SC15 bridge '%s' ready, bus %d baud\n", DEVICE_NAME, BUS_BAUD);
}

void loop() {
  readHost();
  pollStep();
  watchdog();
}
