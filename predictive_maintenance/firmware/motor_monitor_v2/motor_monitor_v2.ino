/*
  motor_monitor_v2.ino  —  PROPOSED update of the original sketch.

  STATUS: written without access to the hardware. NOT compiled, NOT flashed,
  NOT tested. Keep your original sketch; the PC application works with it too.

  What changes compared with the original:
    1. Raw 3-axis acceleration bursts (256 samples at 500 Hz) are sent every
       6 s so the PC can compute windowed RMS and an FFT.
    2. Optional TF-Luna LiDAR distance   (off until you set the pins below).
    3. Optional analog LDR light level   (off until you set the pin below).
    4. A failed sensor read now OMITS the field instead of printing 0, so a
       disconnected DS18B20 can no longer look like 0 °C.
    5. The DS18B20 no longer blocks the loop; slow lines go out every 2 s.
    6. Current is the average of 20 ADC readings instead of one.

  Serial output (115200 baud), two kinds of line:
    Vibration:0.12<TAB>Temperature_C:30.25<TAB>Gas_ADC:612<TAB>Current_A:0.148<TAB>LDR_DO:1[<TAB>LDR_ADC:2400][<TAB>Lidar_cm:35]
    A<TAB>burst_no<TAB>micros<TAB>ax<TAB>ay<TAB>az        (raw counts, 4096 = 1 g)
*/
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <math.h>

// ---- Pins copied unchanged from the original sketch ------------------------
#define DS_PIN 4
#define MQ_PIN 36
#define ACS_PIN 34
#define LDR_PIN 39        // LDR module digital output (DO)
#define MPU 0x68          // I2C on SDA 21 / SCL 22

// ---- NEW connections: I do not know your wiring. ---------------------------
// Set USE_... to 1 only after replacing the pin numbers with the real ones.
#define USE_LDR_ADC 0
#define LDR_AO_PIN 35     // PLACEHOLDER. Must be an ADC1 pin (32, 33, 34, 35, 36, 39).

#define USE_LIDAR 0       // TF-Luna in UART mode (its pin 5 left unconnected)
#define LIDAR_RX_PIN 16   // PLACEHOLDER. ESP32 pin wired to the TF-Luna TX wire.
#define LIDAR_TX_PIN 17   // PLACEHOLDER. ESP32 pin wired to the TF-Luna RX wire.

// ---- Timing ----------------------------------------------------------------
#define SLOW_PERIOD_MS 2000   // one slow-sensor line every 2 s
#define BURST_EVERY 3         // one acceleration burst every 3rd slow line
#define BURST_SAMPLES 256
#define BURST_RATE_HZ 500

OneWire ow(DS_PIN);
DallasTemperature ds(&ow);

float sensitivity = 0.185;    // V per A, ACS712-5A at the sensor output (unverified)
float zeroVoltage = 0;

int16_t bx[BURST_SAMPLES], by[BURST_SAMPLES], bz[BURST_SAMPLES];
uint32_t bt[BURST_SAMPLES];
uint32_t burstNo = 0;
uint32_t nextSlow = 0;
uint8_t slowCount = 0;
bool firstField = true;

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

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  pinMode(LDR_PIN, INPUT);

  ds.begin();
  ds.setWaitForConversion(false);   // do not block the loop for 750 ms
  ds.requestTemperatures();

  Wire.begin(21, 22);
  Wire.setClock(400000);
  writeMpu(0x6B, 0x00);   // wake up
  writeMpu(0x1C, 0x10);   // accelerometer range +/-8 g (4096 counts per g)
  writeMpu(0x1A, 0x01);   // low-pass filter about 184 Hz, limits aliasing at 500 Hz

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

  nextSlow = millis();
}

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
  while (Serial2.available()) Serial2.read();   // throw away old frames
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
      if (amp < 100 || amp == 65535) return false;   // datasheet: unreliable
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

  // Same quantity as the original sketch: |‖a‖ - 1 g| of one sample, in m/s².
  int16_t rx, ry, rz;
  if (readAccelRaw(rx, ry, rz)) {
    float x = rx / 4096.0, y = ry / 4096.0, z = rz / 4096.0;
    field("Vibration");
    Serial.print(fabs(sqrt(x * x + y * y + z * z) - 1.0) * 9.80665, 2);
  }

  float t = ds.getTempCByIndex(0);   // result of the request made 2 s ago
  ds.requestTemperatures();          // start the next conversion
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

  Serial.println();
}

void sendBurst() {
  const uint32_t period = 1000000UL / BURST_RATE_HZ;
  uint32_t next = micros();
  // Sample first, print afterwards, so printing cannot disturb the timing.
  for (int i = 0; i < BURST_SAMPLES; i++) {
    while ((int32_t)(micros() - next) < 0) {}
    next += period;
    bt[i] = micros();
    if (!readAccelRaw(bx[i], by[i], bz[i])) return;   // drop an incomplete burst
  }
  burstNo++;
  for (int i = 0; i < BURST_SAMPLES; i++) {
    Serial.printf("A\t%lu\t%lu\t%d\t%d\t%d\n", (unsigned long)burstNo,
                  (unsigned long)bt[i], bx[i], by[i], bz[i]);
  }
}

void loop() {
  if ((int32_t)(millis() - nextSlow) < 0) return;
  nextSlow += SLOW_PERIOD_MS;

  sendSlowLine();
  if (++slowCount >= BURST_EVERY) {
    slowCount = 0;
    sendBurst();
  }
}
