// BLE gate-servo bridge for the Waveshare "Servo Driver with ESP32" board.
//
// Replaces Waveshare's stock firmware. Drives Waveshare SC15 (SCSCL-protocol)
// bus servos over the board's built-in servo UART, and exposes a small
// line-based text protocol over BLE (Nordic UART Service UUIDs, so any
// generic BLE serial terminal app can talk to it for debugging) instead of
// the raw SC bus protocol. All servo intelligence — open/closed/blocked
// detection, auto ID assignment, calibration, factory reset — lives here;
// the host only ever sends short gate-level commands and reads back
// gate-level state. See README.md in this directory for the protocol and
// the one-at-a-time servo onboarding flow.
//
// Servo bus pins (GPIO 18/19) and 1 Mbps baud rate are Waveshare's own
// defaults for this board (confirmed from their stock ServoDriver.ino),
// not something this firmware invented.

#include <Arduino.h>
#include <NimBLEDevice.h>
#include <SCServo.h>

// -- Servo bus -------------------------------------------------------------

#define S_RXD 18
#define S_TXD 19
#define SERVO_BAUD 1000000

SCSCL sc;

// -- Gate <-> servo ID mapping ----------------------------------------------
//
// Gate 1 is servo ID 11, gate 2 is servo ID 12, and so on. The offset keeps
// IDs 1-10 free (1 is the factory-default ID every new servo boots with, so
// it also doubles as "not yet learned").
#define GATE_ID_OFFSET 10
#define MIN_GATE_ID (1 + GATE_ID_OFFSET)
#define MAX_GATE_ID 60
#define FACTORY_ID 1

static inline uint8_t idForGate(int gate) { return (uint8_t)(gate + GATE_ID_OFFSET); }
static inline int gateForId(uint8_t id) { return (int)id - GATE_ID_OFFSET; }

// -- Motion / detection tuning -----------------------------------------------
//
// Mirrors software/sorter/backend/hardware/waveshare_servo.py so the wired
// and BLE paths behave the same way.
#define MOVE_TIME_MS 300
#define MOVE_SETTLE_MS 150
#define POSITION_TOLERANCE 15
#define BLOCK_LOAD_THRESHOLD 300

#define CAL_STEP_SIZE 30
#define CAL_STEP_TIME_MS 500
#define CAL_SETTLE_MS 600
#define CAL_LOAD_THRESHOLD 300
#define CAL_STALL_CHECKS 3
#define CAL_MARGIN 5

// -- BLE: Nordic UART Service UUIDs -----------------------------------------

#define SVC_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHR_RX_UUID "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  // host writes commands here
#define CHR_TX_UUID "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // device notifies responses here

#define BLE_NOTIFY_CHUNK 20

NimBLECharacteristic *gTxCharacteristic = nullptr;
String gRxLineBuffer;

// ---------------------------------------------------------------------------
// Low-level servo helpers (thin wrappers over the vendored SCServo library)
// ---------------------------------------------------------------------------

bool pingServo(uint8_t id) { return sc.Ping(id) != -1; }

void setTorque(uint8_t id, bool enable) { sc.EnableTorque(id, enable ? 1 : 0); }

bool readLimits(uint8_t id, uint16_t &minV, uint16_t &maxV) {
  int lo = sc.readWord(id, SCSCL_MIN_ANGLE_LIMIT_L);
  if (lo == -1) return false;
  int hi = sc.readWord(id, SCSCL_MAX_ANGLE_LIMIT_L);
  if (hi == -1) return false;
  minV = (uint16_t)lo;
  maxV = (uint16_t)hi;
  return true;
}

bool isCalibrated(uint16_t minV, uint16_t maxV) {
  if (maxV <= minV) return false;
  if (maxV - minV < 20) return false;
  if (minV == 0 && maxV == 1023) return false;  // untouched factory range
  return true;
}

bool setLimits(uint8_t id, uint16_t minV, uint16_t maxV) {
  sc.unLockEprom(id);
  delay(10);
  int a = sc.writeWord(id, SCSCL_MIN_ANGLE_LIMIT_L, minV);
  int b = sc.writeWord(id, SCSCL_MAX_ANGLE_LIMIT_L, maxV);
  delay(10);
  sc.LockEprom(id);
  return a != -1 && b != -1;
}

// Returns false if the servo did not answer.
bool readPositionAndLoad(uint8_t id, int &pos, int &load) {
  if (sc.FeedBack(id) == -1) return false;
  pos = sc.ReadPos(-1);
  load = sc.ReadLoad(-1);
  return pos != -1;
}

void moveTo(uint8_t id, uint16_t position, uint16_t timeMs) { sc.WritePos(id, position, timeMs, 0); }

// ---------------------------------------------------------------------------
// Calibration — ported from calibrate_servo() in waveshare_servo.py so both
// the wired and BLE backends behave identically. Blocks for several seconds
// to tens of seconds; see README's "Known limitations" about this running on
// the Arduino loop task.
// ---------------------------------------------------------------------------

int findLimit(uint8_t id, int startPos, int direction) {
  int best = startPos;
  int target = startPos;
  int lastPos = -1;
  int stallCount = 0;

  while (true) {
    int nextTarget = target + direction * CAL_STEP_SIZE;
    nextTarget = max(0, min(1023, nextTarget));
    if (nextTarget == target) return best;  // hit 0 or 1023
    target = nextTarget;

    moveTo(id, (uint16_t)target, CAL_STEP_TIME_MS);
    delay(CAL_SETTLE_MS);

    bool brokeOut = false;
    for (int i = 0; i < 5; i++) {
      delay(100);
      int pos, load;
      if (!readPositionAndLoad(id, pos, load)) continue;

      if ((direction < 0 && pos < best) || (direction > 0 && pos > best)) best = pos;

      if (abs(pos - target) < 10) {
        brokeOut = true;
        break;  // reached target, take the next step
      }

      if (lastPos != -1 && abs(pos - lastPos) < 2) {
        stallCount++;
      } else {
        stallCount = 0;
      }
      lastPos = pos;

      if (stallCount >= CAL_STALL_CHECKS || (abs(load) > CAL_LOAD_THRESHOLD && stallCount >= 2)) {
        return best;
      }
    }
    (void)brokeOut;
  }
}

bool calibrateServo(uint8_t id, uint16_t &safeMin, uint16_t &safeMax) {
  setLimits(id, 0, 1023);
  delay(20);
  setTorque(id, true);
  delay(20);

  int pos, load;
  if (!readPositionAndLoad(id, pos, load)) return false;

  int calMin = findLimit(id, pos, -1);
  int calMax = findLimit(id, calMin, +1);

  int span = calMax - calMin;
  if (span < 20) {
    setTorque(id, false);
    return false;
  }

  int margin = min((int)CAL_MARGIN, span / 4);
  safeMin = (uint16_t)(calMin + margin);
  safeMax = (uint16_t)(calMax - margin);

  setLimits(id, safeMin, safeMax);

  int center = (safeMin + safeMax) / 2;
  moveTo(id, (uint16_t)center, 500);
  delay(500);
  setTorque(id, false);
  return true;
}

// ---------------------------------------------------------------------------
// Gate-level command handlers
// ---------------------------------------------------------------------------

String cmdOpenClose(int gate, bool open) {
  uint8_t id = idForGate(gate);
  uint16_t minV, maxV;
  if (!readLimits(id, minV, maxV) || !isCalibrated(minV, maxV)) {
    return String(gate) + " ERR UNCALIBRATED";
  }
  uint16_t target = open ? minV : maxV;

  setTorque(id, true);
  moveTo(id, target, MOVE_TIME_MS);
  delay(MOVE_TIME_MS + MOVE_SETTLE_MS);

  int pos, load;
  if (!readPositionAndLoad(id, pos, load)) {
    setTorque(id, false);
    return String(gate) + " ERR OFFLINE";
  }

  bool arrived = abs(pos - (int)target) <= POSITION_TOLERANCE;
  setTorque(id, false);

  if (arrived) return String(gate) + (open ? " OPEN" : " CLOSED");
  return String(gate) + " BLOCKED";
}

String cmdStatus(int gate) {
  uint8_t id = idForGate(gate);
  uint16_t minV, maxV;
  if (!readLimits(id, minV, maxV)) return String(gate) + " ERR OFFLINE";
  if (!isCalibrated(minV, maxV)) return String(gate) + " ERR UNCALIBRATED";

  int pos, load;
  if (!readPositionAndLoad(id, pos, load)) return String(gate) + " ERR OFFLINE";

  int distOpen = abs(pos - (int)minV);
  int distClosed = abs(pos - (int)maxV);
  if (min(distOpen, distClosed) > POSITION_TOLERANCE) return String(gate) + " UNKNOWN";
  return String(gate) + (distOpen <= distClosed ? " OPEN" : " CLOSED");
}

String cmdLearn() {
  if (!pingServo(FACTORY_ID)) return "LEARN NONE";

  int nextId = MIN_GATE_ID;
  for (int id = MIN_GATE_ID; id <= MAX_GATE_ID; id++) {
    if (pingServo((uint8_t)id)) nextId = id + 1;
  }
  if (nextId > MAX_GATE_ID) return "LEARN ERR FULL";

  sc.unLockEprom(FACTORY_ID);
  delay(10);
  int result = sc.writeByte(FACTORY_ID, SCSCL_ID, (uint8_t)nextId);
  delay(10);
  sc.LockEprom((uint8_t)nextId);
  if (result == -1) return "LEARN ERR SETID";

  int gate = gateForId((uint8_t)nextId);
  uint16_t safeMin, safeMax;
  if (!calibrateServo((uint8_t)nextId, safeMin, safeMax)) {
    return "LEARNED " + String(gate) + " NOCAL";
  }
  return "LEARNED " + String(gate) + " OK";
}

String cmdCalibrate(int gate) {
  uint8_t id = idForGate(gate);
  if (!pingServo(id)) return "CALIBRATE ERR " + String(gate) + " OFFLINE";
  uint16_t safeMin, safeMax;
  if (!calibrateServo(id, safeMin, safeMax)) return "CALIBRATE ERR " + String(gate);
  return "CALIBRATED " + String(gate);
}

String cmdResetAll() {
  int count = 0;
  for (int id = FACTORY_ID; id <= MAX_GATE_ID; id++) {
    if (id > FACTORY_ID && id < MIN_GATE_ID) continue;  // 2..10 are never used
    if (!pingServo((uint8_t)id)) continue;

    sc.unLockEprom((uint8_t)id);
    delay(10);
    sc.writeWord((uint8_t)id, SCSCL_MIN_ANGLE_LIMIT_L, 0);
    sc.writeWord((uint8_t)id, SCSCL_MAX_ANGLE_LIMIT_L, 1023);
    if (id != FACTORY_ID) {
      sc.writeByte((uint8_t)id, SCSCL_ID, FACTORY_ID);
      delay(10);
      sc.LockEprom(FACTORY_ID);
    } else {
      delay(10);
      sc.LockEprom(FACTORY_ID);
    }
    count++;
    delay(10);
  }
  return "RESETALL OK " + String(count);
}

// Shared by the explicit LIST command and the unsolicited inventory report
// sent once a client subscribes to notifications (see TxCallbacks below).
String scanGateIds() {
  String gates;
  for (int id = MIN_GATE_ID; id <= MAX_GATE_ID; id++) {
    if (pingServo((uint8_t)id)) {
      gates += " " + String(gateForId((uint8_t)id));
    }
  }
  return gates;
}

String cmdList() { return "GATES" + scanGateIds(); }

// ---------------------------------------------------------------------------
// Command dispatch
// ---------------------------------------------------------------------------

String handleCommand(const String &line) {
  String trimmed = line;
  trimmed.trim();
  if (trimmed.length() == 0) return "";

  int firstSpace = trimmed.indexOf(' ');
  String verb = firstSpace == -1 ? trimmed : trimmed.substring(0, firstSpace);
  String rest = firstSpace == -1 ? String("") : trimmed.substring(firstSpace + 1);
  verb.toUpperCase();

  if (verb == "OPEN" || verb == "CLOSE") {
    int gate = rest.toInt();
    if (gate <= 0) return "ERR BAD_GATE";
    return cmdOpenClose(gate, verb == "OPEN");
  }
  if (verb == "STATUS") {
    int gate = rest.toInt();
    if (gate <= 0) return "ERR BAD_GATE";
    return cmdStatus(gate);
  }
  if (verb == "CALIBRATE") {
    int gate = rest.toInt();
    if (gate <= 0) return "ERR BAD_GATE";
    return cmdCalibrate(gate);
  }
  if (verb == "LEARN") return cmdLearn();
  if (verb == "RESETALL") return cmdResetAll();
  if (verb == "LIST") return cmdList();
  if (verb == "PING") return "PONG";

  return "ERR UNKNOWN_CMD";
}

// ---------------------------------------------------------------------------
// BLE plumbing
// ---------------------------------------------------------------------------

void sendNotify(const String &payload) {
  if (gTxCharacteristic == nullptr) return;
  String withNewline = payload + "\n";
  size_t len = withNewline.length();
  const char *data = withNewline.c_str();
  for (size_t offset = 0; offset < len; offset += BLE_NOTIFY_CHUNK) {
    size_t chunkLen = min((size_t)BLE_NOTIFY_CHUNK, len - offset);
    gTxCharacteristic->setValue((uint8_t *)(data + offset), chunkLen);
    gTxCharacteristic->notify();
    delay(5);  // let the link drain between notifications
  }
}

class RxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *pCharacteristic) override {
    std::string value = pCharacteristic->getValue();
    for (char c : value) {
      if (c == '\n' || c == '\r') {
        if (gRxLineBuffer.length() > 0) {
          String response = handleCommand(gRxLineBuffer);
          if (response.length() > 0) sendNotify(response);
          gRxLineBuffer = "";
        }
      } else {
        gRxLineBuffer += c;
        if (gRxLineBuffer.length() > 120) gRxLineBuffer = "";  // guard against junk
      }
    }
  }
};

// Sent once per connection, as soon as the host subscribes to notifications
// (not from onConnect: notify() before the client's CCCD write would be
// silently dropped). Lets the host learn which gates are reachable without
// having to ask first.
void sendInventory() { sendNotify("READY GATES" + scanGateIds()); }

class TxCallbacks : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic *pCharacteristic, ble_gap_conn_desc *desc, uint16_t subValue) override {
    if (subValue > 0) sendInventory();
  }
};

void setupBle() {
  NimBLEDevice::init("SORTER-SERVO-BLE");
  NimBLEServer *server = NimBLEDevice::createServer();
  NimBLEService *service = server->createService(SVC_UUID);

  gTxCharacteristic = service->createCharacteristic(CHR_TX_UUID, NIMBLE_PROPERTY::NOTIFY);
  gTxCharacteristic->setCallbacks(new TxCallbacks());

  NimBLECharacteristic *rxCharacteristic =
      service->createCharacteristic(CHR_RX_UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  rxCharacteristic->setCallbacks(new RxCallbacks());

  service->start();

  NimBLEAdvertising *advertising = NimBLEDevice::getAdvertising();
  advertising->addServiceUUID(SVC_UUID);
  advertising->start();
}

// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);

  Serial1.begin(SERVO_BAUD, SERIAL_8N1, S_RXD, S_TXD);
  sc.pSerial = &Serial1;

  setupBle();

  Serial.println("esp32_ble_servo_bridge ready");
}

void loop() {
  // All work happens in RxCallbacks::onWrite(), triggered by BLE writes.
  delay(10);
}
