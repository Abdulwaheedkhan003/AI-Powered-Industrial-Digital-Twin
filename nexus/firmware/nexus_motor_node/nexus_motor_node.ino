/*
  nexus_motor_node.ino  —  firmware v3.0.0 for the NEXUS proof-of-concept node.

  STATUS: written without access to the hardware. NOT compiled, NOT flashed,
  NOT tested. The NEXUS backend also works with the original sketch and with
  motor_monitor_v2; this version adds what those cannot do:

    * commands from the backend, each answered with an acknowledgement
    * a buzzer alert limited to 2 seconds
    * a sequence number on every telemetry line (lets the PC count lost lines)
    * optional RPM from an A3144 Hall sensor

  It does NOT control the motor. There is no command that changes motor speed
  or drives an arbitrary pin, and none should be added without a hardware
  safety review. This board is not a safety controller.

  ---------------------------------------------------------------- protocol
  Serial, 115200 baud, one line per message, fields separated by TAB.

  Node -> PC
    telemetry  Vibration:0.12 Temperature_C:30.25 Gas_ADC:612 Current_A:0.148 LDR_DO:1 [RPM:1480] [LDR_ADC:..] [Lidar_cm:..] Seq:123
               A field is left out when its sensor read failed. Nothing is replaced by 0.
    burst      A  burst_no  micros  ax  ay  az           raw counts, 4096 = 1 g
    ack        ACK  command_id  OK|ERR  detail
    status     STATUS  device=ESP32-NODE-01  fw=3.0.0  buzzer=0|1  rpm=0|1  ...

  PC -> Node
    CMD  seq  command_id  NAME  argument
      PING            argument ignored
      REQUEST_STATUS  argument ignored; replies with a STATUS line, then the ACK
      BUZZER_ALERT    argument = duration in ms, 50..2000

    A command is refused (ACK ... ERR) if the name is unknown, the argument is
    out of range, the line is malformed, or seq is not greater than the last
    accepted seq (a repeated or replayed command).
    The last accepted seq is forgotten when the board resets.
*/
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <math.h>

#define DEVICE_ID "ESP32-NODE-01"
#define FW_VERSION "3.0.0"

// ---- Pins copied unchanged from the original sketch ------------------------
#define DS_PIN 4
#define MQ_PIN 36
#define ACS_PIN 34
#define LDR_PIN 39        // LDR module digital output (DO)
#define MPU 0x68          // I2C on SDA 21 / SCL 22

// ---- Optional hardware: I do not know your wiring. --------------------------
// Set USE_... to 1 only after replacing the placeholder pin with the real one.
#define USE_BUZZER 0
#define BUZZER_PIN 25        // PLACEHOLDER. Active buzzer, HIGH = on.

#define USE_HALL_RPM 0
#define HALL_PIN 27          // PLACEHOLDER. A3144 output (open collector, needs a pull-up).
#define PULSES_PER_REV 1     // magnets passing the sensor per shaft revolution

#define USE_LDR_ADC 0
#define LDR_AO_PIN 35        // PLACEHOLDER. Must be an ADC1 pin (32, 33, 34, 35, 36, 39).

#define USE_LIDAR 0          // TF-Luna in UART mode (its pin 5 left unconnected)
#define LIDAR_RX_PIN 16      // PLACEHOLDER. ESP32 pin wired to the TF-Luna TX wire.
#define LIDAR_TX_PIN 17      // PLACEHOLDER. ESP32 pin wired to the TF-Luna RX wire.

// ---- Timing and limits --------------------------------------------------------
#define SLOW_PERIOD_MS 2000
#define BURST_EVERY 3
#define BURST_SAMPLES 256
#define BURST_RATE_HZ 500
#define BUZZER_MIN_MS 50
#define BUZZER_MAX_MS 2000   // hard upper limit, enforced here as well as on the PC

OneWire ow(DS_PIN);
DallasTemperature ds(&ow);

float sensitivity = 0.185;   // V per A, ACS712-5A at the sensor output (unverified)
float zeroVoltage = 0;

int16_t bx[BURST_SAMPLES], by[BURST_SAMPLES], bz[BURST_SAMPLES];
uint32_t bt[BURST_SAMPLES];
uint32_t burstNo = 0, nextSlow = 0, telemetrySeq = 0;
uint8_t slowCount = 0;
bool firstField = true;

uint32_t lastCommandSeq = 0;
uint32_t buzzerOffAt = 0;
bool buzzerOn = false;
char lineBuf[96];
uint8_t lineLen = 0;

volatile uint32_t hallPulses = 0;
uint32_t lastRpmMs = 0;

#if USE_HALL_RPM
void IRAM_ATTR onHallPulse() { hallPulses++; }
#endif

void writeMpu(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

float readAcsVolts(int samples) {
  uint32_t sum = 0;
  for (int i = 0; i < samples; i++) sum += analogReadMilliVolts(ACS_PIN);
  return (sum / (float)samples) / 1000.0;
}

void sendStatus() {
  Serial.printf("STATUS\tdevice=%s\tfw=%s\tbuzzer=%d\trpm=%d\tldr_adc=%d\tlidar=%d\tuptime_ms=%lu\n",
                DEVICE_ID, FW_VERSION, USE_BUZZER, USE_HALL_RPM, USE_LDR_ADC, USE_LIDAR,
                (unsigned long)millis());
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  pinMode(LDR_PIN, INPUT);

#if USE_BUZZER
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);          // buzzer is off after every reset
#endif
#if USE_HALL_RPM
  pinMode(HALL_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(HALL_PIN), onHallPulse, FALLING);
#endif

  ds.begin();
  ds.setWaitForConversion(false);
  ds.requestTemperatures();

  Wire.begin(21, 22);
  Wire.setClock(400000);
  writeMpu(0x6B, 0x00);   // wake up
  writeMpu(0x1C, 0x10);   // accelerometer range +/-8 g (4096 counts per g)
  writeMpu(0x1A, 0x01);   // low-pass filter about 184 Hz

#if USE_LIDAR
  Serial2.begin(115200, SERIAL_8N1, LIDAR_RX_PIN, LIDAR_TX_PIN);
#endif

  // Zero the ACS712. The motor MUST be off while the board starts.
  long s = 0;
  for (int i = 0; i < 200; i++) {
    s += analogReadMilliVolts(ACS_PIN);
    delay(5);
  }
  zeroVoltage = (s / 200.0) / 1000.0;

  sendStatus();
  nextSlow = millis();
  lastRpmMs = millis();
}

// ---------------------------------------------------------------- commands
void ack(const char *id, bool ok, const char *detail) {
  Serial.print("ACK\t");
  Serial.print(id);
  Serial.print(ok ? "\tOK\t" : "\tERR\t");
  Serial.println(detail);
}

void handleCommand(char *line) {
  // CMD <seq> <id> <NAME> <arg>
  char *fields[5];
  int n = 0;
  char *p = strtok(line, "\t");
  while (p && n < 5) { fields[n++] = p; p = strtok(NULL, "\t"); }
  if (n < 4 || strcmp(fields[0], "CMD") != 0) return;      // not a command line: ignore silently
  const char *id = fields[2];
  uint32_t seq = strtoul(fields[1], NULL, 10);
  long arg = (n == 5) ? strtol(fields[4], NULL, 10) : 0;

  if (seq == 0 || seq <= lastCommandSeq) { ack(id, false, "sequence not newer than last accepted"); return; }

  if (strcmp(fields[3], "PING") == 0) {
    lastCommandSeq = seq;
    ack(id, true, "pong");
  } else if (strcmp(fields[3], "REQUEST_STATUS") == 0) {
    lastCommandSeq = seq;
    sendStatus();
    ack(id, true, "status sent");
  } else if (strcmp(fields[3], "BUZZER_ALERT") == 0) {
    if (arg < BUZZER_MIN_MS || arg > BUZZER_MAX_MS) { ack(id, false, "duration out of range"); return; }
#if USE_BUZZER
    lastCommandSeq = seq;
    digitalWrite(BUZZER_PIN, HIGH);
    buzzerOn = true;
    buzzerOffAt = millis() + (uint32_t)arg;
    ack(id, true, "buzzer on");
#else
    ack(id, false, "no buzzer configured in firmware");
#endif
  } else {
    ack(id, false, "unknown command");
  }
}

void pollSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      lineBuf[lineLen] = 0;
      if (lineLen > 0) handleCommand(lineBuf);
      lineLen = 0;
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0;                       // over-long line: drop it
    }
  }
}

void serviceBuzzer() {
#if USE_BUZZER
  // The buzzer always switches itself off, whether or not the PC is still connected.
  if (buzzerOn && (int32_t)(millis() - buzzerOffAt) >= 0) {
    digitalWrite(BUZZER_PIN, LOW);
    buzzerOn = false;
  }
#endif
}

// ---------------------------------------------------------------- sensors
bool readAccelRaw(int16_t &x, int16_t &y, int16_t &z) {
  Wire.beginTransmission(MPU);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU, 6) != 6) return false;
  uint8_t hi, lo;
  hi = Wire.read(); lo = Wire.read(); x = (int16_t)((hi << 8) | lo);
  hi = Wire.read(); lo = Wire.read(); y = (int16_t)((hi << 8) | lo);
  hi = Wire.read(); lo = Wire.read(); z = (int16_t)((hi << 8) | lo);
  return true;
}

#if USE_LIDAR
// TF-Luna frame: 0x59 0x59 distL distH ampL ampH tempL tempH checksum
bool readLidar(int &distCm) {
  while (Serial2.available()) Serial2.read();
  uint8_t f[9];
  int idx = 0;
  uint32_t start = millis();
  while (millis() - start < 60) {
    if (!Serial2.available()) continue;
    uint8_t b = Serial2.read();
    if (idx < 2) {
      if (b == 0x59) f[idx++] = b; else idx = 0;
      continue;
    }
    f[idx++] = b;
    if (idx == 9) {
      uint8_t sum = 0;
      for (int i = 0; i < 8; i++) sum += f[i];
      if (sum != f[8]) { idx = 0; continue; }
      int amp = f[4] | (f[5] << 8);
      if (amp < 100 || amp == 65535) return false;
      distCm = f[2] | (f[3] << 8);
      return true;
    }
  }
  return false;
}
#endif

void field(const char *key) {
  if (!firstField) Serial.print('\t');
  firstField = false;
  Serial.print(key);
  Serial.print(':');
}

void sendSlowLine() {
  firstField = true;

  int16_t rx, ry, rz;
  if (readAccelRaw(rx, ry, rz)) {
    float x = rx / 4096.0, y = ry / 4096.0, z = rz / 4096.0;
    field("Vibration");
    Serial.print(fabs(sqrt(x * x + y * y + z * z) - 1.0) * 9.80665, 2);
  }

  float t = ds.getTempCByIndex(0);
  ds.requestTemperatures();
  if (t != DEVICE_DISCONNECTED_C) {
    field("Temperature_C");
    Serial.print(t, 2);
  }

  field("Gas_ADC");
  Serial.print(analogRead(MQ_PIN));

  field("Current_A");
  Serial.print(fabs((readAcsVolts(20) - zeroVoltage) / sensitivity), 3);

  field("LDR_DO");
  Serial.print(!digitalRead(LDR_PIN));

#if USE_HALL_RPM
  {
    uint32_t now = millis();
    noInterrupts();
    uint32_t pulses = hallPulses;
    hallPulses = 0;
    interrupts();
    uint32_t dt = now - lastRpmMs;
    lastRpmMs = now;
    if (dt > 0) {
      field("RPM");
      Serial.print((pulses * 60000.0) / (dt * (float)PULSES_PER_REV), 1);
    }
  }
#endif

#if USE_LDR_ADC
  field("LDR_ADC");
  Serial.print(analogRead(LDR_AO_PIN));
#endif

#if USE_LIDAR
  int dist;
  if (readLidar(dist)) {
    field("Lidar_cm");
    Serial.print(dist);
  }
#endif

  field("Seq");
  Serial.print(++telemetrySeq);
  Serial.println();
}

void sendBurst() {
  const uint32_t period = 1000000UL / BURST_RATE_HZ;
  uint32_t next = micros();
  for (int i = 0; i < BURST_SAMPLES; i++) {
    while ((int32_t)(micros() - next) < 0) {}
    next += period;
    bt[i] = micros();
    if (!readAccelRaw(bx[i], by[i], bz[i])) return;
  }
  serviceBuzzer();                       // a burst takes about half a second
  burstNo++;
  for (int i = 0; i < BURST_SAMPLES; i++) {
    Serial.printf("A\t%lu\t%lu\t%d\t%d\t%d\n", (unsigned long)burstNo,
                  (unsigned long)bt[i], bx[i], by[i], bz[i]);
    if ((i & 31) == 0) serviceBuzzer();
  }
}

void loop() {
  pollSerial();
  serviceBuzzer();
  if ((int32_t)(millis() - nextSlow) < 0) return;
  nextSlow += SLOW_PERIOD_MS;

  sendSlowLine();
  if (++slowCount >= BURST_EVERY) {
    slowCount = 0;
    sendBurst();
  }
}
