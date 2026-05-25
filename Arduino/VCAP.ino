// Arduino UNO VCAP sampler.
// Compile: arduino-cli compile --fqbn arduino:avr:uno .\Arduino
// Upload:  arduino-cli upload --fqbn arduino:avr:uno -p COM3 .\Arduino

const unsigned long SAMPLE_INTERVAL_MS = 10UL;
const int SENSOR_PIN = A0;
const float ADC_REFERENCE_VOLTAGE = 5.0;
const float ADC_MAX_VALUE = 1023.0;

unsigned long lastSampleTime = 0;

void setupVcapSampler() {
  Serial.begin(115200);
  while (!Serial) {
    ; // Wait for boards with native USB. UNO continues immediately.
  }

  Serial.println("Time(ms),ADC,Voltage(V)");
}

void loopVcapSampler() {
  unsigned long now = millis();

  if (now - lastSampleTime < SAMPLE_INTERVAL_MS) {
    return;
  }

  lastSampleTime = now;

  int adcValue = analogRead(SENSOR_PIN);
  float voltage = adcValue * (ADC_REFERENCE_VOLTAGE / ADC_MAX_VALUE);

  Serial.print(now);
  Serial.print(",");
  Serial.print(adcValue);
  Serial.print(",");
  Serial.println(voltage, 3);
}
