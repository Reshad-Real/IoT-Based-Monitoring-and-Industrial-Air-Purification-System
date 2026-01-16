/***************************************************************************
   AIR QUALITY MONITORING SYSTEM - ESP32 WITH MESH
   NODE 3 - CCS811 + PMS5003 + ADS1115 - CALIBRATION VERSION
   
   FEATURES:
   - Manual calibration for all sensors
   - Fixed garbage values from uninitialized sensors
   - Added proper sensor initialization checks
   - Added data validation before display
***************************************************************************/

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <HardwareSerial.h>
#include <Adafruit_CCS811.h>
#include <Adafruit_ADS1X15.h>
#include "painlessMesh.h"
#include <ArduinoJson.h>

// ================= MESH CONFIGURATION =================
#define   MESH_PREFIX     "AirQualityMesh"
#define   MESH_PASSWORD   "AirQuality2024"
#define   MESH_PORT       5555
#define   NODE_ID         3

// ================= CALIBRATION CONSTANTS =================
// PM2.5 & PM10 Calibration
const float PM25_OFFSET = 0.0;        // Add/subtract ug/m3
const float PM25_MULTIPLIER = 0.0314;    // Multiply factor
const float PM10_OFFSET = 0.0;        // Add/subtract ug/m3
const float PM10_MULTIPLIER = 0.148;    // Multiply factor

// eCO2 & TVOC Calibration
const int ECO2_OFFSET = 0;            // Add/subtract ppm
const float ECO2_MULTIPLIER = 1.0;    // Multiply factor
const int TVOC_OFFSET = 1;            // Add/subtract ppb
const float TVOC_MULTIPLIER = 1.0;    // Multiply factor

// CO & NO2 Calibration
const float CO_SLOPE = 0.94;           // Voltage to ppm conversion
const float CO_OFFSET = 0.0;          // Add/subtract ppm
const float NO2_SLOPE = 850.0;        // Voltage to ppb conversion
const float NO2_OFFSET = 0.0;         // Add/subtract ppb

// ================= PMS5003 Configuration =================
#define PMS_RX 16  // RX pin (connect to TX of PMS5003)
#define PMS_TX 17  // TX pin (connect to RX of PMS5003)
HardwareSerial pmsSerial(1); // Use UART1

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

struct pms5003data {
  uint16_t framelen;
  uint16_t pm10_standard, pm25_standard, pm100_standard;
  uint16_t pm10_env, pm25_env, pm100_env;
  uint16_t particles_03um, particles_05um, particles_10um, particles_25um, particles_50um, particles_100um;
  uint16_t unused;
  uint16_t checksum;
};

struct pms5003data data;

// ================= SENSORS =================
Adafruit_CCS811 ccs;
Adafruit_ADS1115 ads;

// ================= SENSOR STATUS FLAGS =================
bool pmsReady = false;
bool ccsReady = false;
bool adsReady = false;

// ================= SENSOR DATA (CALIBRATED VALUES) =================
uint16_t eCO2 = 0;
uint16_t TVOC = 0;
float CO_voltage, NO2_voltage, CO_ppm = 0, NO2_ppb = 0;
float pm25_calibrated = 0, pm10_calibrated = 0;

// Previous valid values for comparison
uint16_t prev_pm25 = 0;
uint16_t prev_pm10 = 0;
int prev_eCO2 = 400;
int prev_TVOC = 0;

// Track sensor warm-up
unsigned long startTime = 0;
bool sensorsWarmedUp = false;
const unsigned long WARMUP_TIME = 30000; // 30 seconds warm-up

// Track last successful reads
unsigned long lastSuccessfulPMSRead = 0;
unsigned long lastSuccessfulCCSRead = 0;
unsigned long lastSuccessfulADSRead = 0;

// ================= MESH =================
Scheduler userScheduler;
painlessMesh mesh;

// ================= RECEIVED DATA TRACKING =================
int lastReceivedNodeId = 0;
unsigned long lastReceivedTime = 0;
const unsigned long MESH_INDICATOR_DURATION = 2000;

// ================= FUNCTION PROTOTYPES =================
void sendSensorData();
void readAndDisplaySensors();
boolean readPMSdata(Stream *s);
void receivedCallback(uint32_t from, String &msg);
void newConnectionCallback(uint32_t nodeId);
void changedConnectionCallback();
void printSensorDataSerial();
void updateDisplay();
bool isValidPMData();
bool isValidGasData();
float applyCalibration(float value, float offset, float multiplier);
int applyCalibration(int value, int offset, float multiplier);

// ================= TASKS =================
Task taskSendData(TASK_SECOND * 20, TASK_FOREVER, &sendSensorData);
Task taskReadSensors(TASK_SECOND * 2, TASK_FOREVER, &readAndDisplaySensors);

void setup() {
  Serial.begin(115200);
  delay(2000); // Give serial time to initialize
  
  Serial.println("\n\n========================================");
  Serial.println("Air Quality Monitor - Node " + String(NODE_ID));
  Serial.println("========================================");
  
  startTime = millis();
  
  // Initialize I2C with explicit pins
  Wire.begin();
  Wire.setClock(100000); // 100kHz for stability
  
  // Initialize PMS5003 Serial (UART1 on ESP32)
  pmsSerial.begin(9600, SERIAL_8N1, PMS_RX, PMS_TX);

  // Initialize OLED
  Serial.print("Initializing OLED...");
  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("FAILED!");
    for(;;);
  }
  Serial.println("OK");
  
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0,0);
  display.println("Air Quality Monitor");
  display.print("Node ");
  display.println(NODE_ID);
  display.println("Initializing...");
  display.display();
  delay(1000);

  // Initialize ADS1115 (0x48)
  Serial.print("Initializing ADS1115 (0x48)...");
  delay(100);
  if (ads.begin()) {
    ads.setGain(GAIN_ONE);
    adsReady = true;
    Serial.println("OK");
  } else {
    Serial.println("FAILED!");
  }

  // Initialize CCS811 (0x5A)
  Serial.print("Initializing CCS811 (0x5A)...");
  delay(100);
  if (ccs.begin()) {
    Serial.println("OK");
    // Wait for sensor to be available
    int waitCount = 0;
    while(!ccs.available() && waitCount < 50) {
      delay(100);
      waitCount++;
    }
    if (ccs.available()) {
      ccsReady = true;
      Serial.println("CCS811 ready.");
    } else {
      Serial.println("CCS811 timeout!");
    }
  } else {
    Serial.println("FAILED!");
  }

  // Initialize Mesh
  Serial.println("Initializing Mesh Network...");
  mesh.setDebugMsgTypes(ERROR | STARTUP | CONNECTION);
  mesh.init(MESH_PREFIX, MESH_PASSWORD, &userScheduler, MESH_PORT);
  mesh.onReceive(&receivedCallback);
  mesh.onNewConnection(&newConnectionCallback);
  mesh.onChangedConnections(&changedConnectionCallback);

  userScheduler.addTask(taskSendData);
  userScheduler.addTask(taskReadSensors);
  taskSendData.enable();
  taskReadSensors.enable();

  Serial.println("Setup complete!");
  Serial.println("Sensor warm-up: 30 seconds");
  Serial.println("========================================\n");
  
  // Show initialization status
  display.clearDisplay();
  display.setCursor(0,0);
  display.println("Sensor Status:");
  display.print("PMS5003: Waiting");
  display.setCursor(0,16);
  display.print("CCS811: ");
  display.println(ccsReady ? "OK" : "FAIL");
  display.print("ADS1115: ");
  display.println(adsReady ? "OK" : "FAIL");
  display.setCursor(0,56);
  display.println("Warming up...");
  display.display();
  delay(3000);
}

void loop() {
  mesh.update();
  
  // Check if warm-up period is complete
  if (!sensorsWarmedUp && (millis() - startTime > WARMUP_TIME)) {
    sensorsWarmedUp = true;
    Serial.println(">>> Sensors warmed up and ready!");
  }
}

// ================= CALIBRATION FUNCTIONS =================
float applyCalibration(float value, float offset, float multiplier) {
  return (value * multiplier) + offset;
}

int applyCalibration(int value, int offset, float multiplier) {
  return (int)((value * multiplier) + offset);
}

void readAndDisplaySensors() {
  bool dataUpdated = false;

  // Read PMS5003
  if (readPMSdata(&pmsSerial)) {
    if (isValidPMData()) {
      pmsReady = true;
      lastSuccessfulPMSRead = millis();
      
      // Debug: Print raw PM values
      Serial.printf("PMS RAW: PM2.5=%d, PM10=%d\n", data.pm25_standard, data.pm100_standard);
      
      // Apply calibration to PM values
      pm25_calibrated = applyCalibration((float)data.pm25_standard, PM25_OFFSET, PM25_MULTIPLIER);
      pm10_calibrated = applyCalibration((float)data.pm100_standard, PM10_OFFSET, PM10_MULTIPLIER);
      
      // Ensure non-negative values
      if (pm25_calibrated < 0) pm25_calibrated = 0;
      if (pm10_calibrated < 0) pm10_calibrated = 0;
      
      prev_pm25 = (uint16_t)pm25_calibrated;
      prev_pm10 = (uint16_t)pm10_calibrated;
      dataUpdated = true;
    } else {
      Serial.println("WARNING: Invalid PM data detected");
      Serial.printf("PM VALUES: PM2.5=%d, PM10=%d (rejected)\n", data.pm25_standard, data.pm100_standard);
    }
  } else {
    // Debug: No data available
    if (millis() - lastSuccessfulPMSRead > 10000 && lastSuccessfulPMSRead > 0) {
      Serial.println("WARNING: No PMS data for 10+ seconds");
    }
  }
  
  // Read CCS811 (only if sensor is ready and warmed up)
  if (ccsReady && sensorsWarmedUp) {
    if (ccs.available()) {
      if (!ccs.readData()) {
        uint16_t newECO2 = ccs.geteCO2();
        uint16_t newTVOC = ccs.getTVOC();
        
        // Validate and apply calibration to gas sensor data
        if (newECO2 >= 400 && newECO2 < 65000 && newECO2 != 65535) {
          eCO2 = applyCalibration((int)newECO2, ECO2_OFFSET, ECO2_MULTIPLIER);
          if (eCO2 < 400) eCO2 = 400; // Minimum atmospheric CO2
          prev_eCO2 = eCO2;
          dataUpdated = true;
        }
        if (newTVOC >= 0 && newTVOC < 65000 && newTVOC != 65535) {
          TVOC = applyCalibration((int)newTVOC, TVOC_OFFSET, TVOC_MULTIPLIER);
          if (TVOC < 0) TVOC = 0; // Ensure non-negative
          prev_TVOC = TVOC;
          dataUpdated = true;
        }
        
        if (dataUpdated) {
          lastSuccessfulCCSRead = millis();
        }
      } else {
        Serial.println("ERROR: Failed to read from CCS811!");
      }
    }
  } else if (!sensorsWarmedUp) {
    // During warm-up, use baseline values
    eCO2 = 400;
    TVOC = 0;
  }

  // Read CO & NO2 (only if sensor is ready)
  if (adsReady) {
    int16_t adc0 = ads.readADC_SingleEnded(0);
    int16_t adc1 = ads.readADC_SingleEnded(1);
    
    // Validate and calibrate ADC readings
    if (adc0 >= 0 && adc0 < 32767) {
      CO_voltage = adc0 * 0.1875 / 1000.0;
      CO_ppm = (CO_voltage * CO_SLOPE) + CO_OFFSET;
      if (CO_ppm < 0) CO_ppm = 0;
      if (CO_ppm > 1000) CO_ppm = 0; // Cap at reasonable value
    }
    
    if (adc1 >= 0 && adc1 < 32767) {
      NO2_voltage = adc1 * 0.1875 / 1000.0;
      NO2_ppb = (NO2_voltage * NO2_SLOPE) + NO2_OFFSET;
      if (NO2_ppb < 0) NO2_ppb = 0;
      if (NO2_ppb > 2000) NO2_ppb = 0; // Cap at reasonable value
    }
    
    lastSuccessfulADSRead = millis();
  }

  // Always update display
  updateDisplay();

  // Print to Serial if data was updated
  if (dataUpdated && sensorsWarmedUp) {
    printSensorDataSerial();
  }
}

bool isValidPMData() {
  // Check for reasonable PM values
  if (data.pm25_standard > 1000 || data.pm100_standard > 2000) {
    return false;
  }
  // Check if values are not all zeros (sensor error)
  if (data.pm25_standard == 0 && data.pm100_standard == 0 && 
      data.pm10_standard == 0) {
    return false;
  }
  return true;
}

bool isValidGasData() {
  // Check if CCS811 is returning valid data
  // CCS811 returns 0 or very high values (near 65535) when not ready
  if (eCO2 == 65535 || TVOC == 65535) {
    return false;
  }
  if (eCO2 == 0 && TVOC == 0) {
    return false; // Both zero indicates sensor error
  }
  // Check for reasonable ranges
  if (eCO2 < 400 || eCO2 > 65000) {
    return false;
  }
  if (TVOC > 65000) {
    return false;
  }
  return true;
}

void updateDisplay() {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
  
  // Header with mesh info and sensor status
  display.print("N");
  display.print(NODE_ID);
  display.print(" | M:");
  display.print(mesh.getNodeList().size());
  
  // Show warm-up status
  if (!sensorsWarmedUp) {
    display.print(" WARM");
  }
  
  // Show brief mesh receive indicator
  if (lastReceivedNodeId > 0 && (millis() - lastReceivedTime < MESH_INDICATOR_DURATION)) {
    display.print(" <");
    display.print(lastReceivedNodeId);
  }
  
  display.println();
  display.println("-----------------");
  
  // Display calibrated sensor data with status indicators
  display.print("PM2.5: ");
  if (pmsReady) {
    display.print(pm25_calibrated, 2);
  } else {
    display.print("---");
  }
  display.println(" ug/m3");
  
  display.print("PM10 : ");
  if (pmsReady) {
    display.print(pm10_calibrated, 2);
  } else {
    display.print("---");
  }
  display.println(" ug/m3");
  
  display.print("eCO2 : ");
  if (ccsReady && sensorsWarmedUp) {
    display.print(eCO2);
  } else {
    display.print("---");
  }
  display.println(" ppm");
  
  display.print("TVOC : ");
  if (ccsReady && sensorsWarmedUp) {
    display.print(TVOC);
  } else {
    display.print("---");
  }
  display.println(" ppb");
  
  display.print("CO   : ");
  if (adsReady) {
    display.print(CO_ppm, 2);
  } else {
    display.print("---");
  }
  display.println(" ppm");
  
  display.print("NO2  : ");
  if (adsReady) {
    display.print(NO2_ppb, 2);
  } else {
    display.print("---");
  }
  display.println(" ppb");
  
  display.display();
}

void printSensorDataSerial() {
  Serial.println("========================================");
  Serial.print("Node ID: ");
  Serial.print(NODE_ID);
  Serial.print(" | Mesh Nodes: ");
  Serial.print(mesh.getNodeList().size());
  Serial.print(" | Uptime: ");
  Serial.print((millis() - startTime) / 1000);
  Serial.println("s");
  Serial.println("----------------------------------------");
  
  // Particulate Matter
  Serial.print("PM2.5       : ");
  if (pmsReady) {
    Serial.print(pm25_calibrated, 2);
    Serial.println(" ug/m3");
  } else {
    Serial.println("NOT READY");
  }
  
  Serial.print("PM10        : ");
  if (pmsReady) {
    Serial.print(pm10_calibrated, 2);
    Serial.println(" ug/m3");
  } else {
    Serial.println("NOT READY");
  }
  
  // Gas Sensors
  Serial.print("eCO2        : ");
  if (ccsReady && sensorsWarmedUp) {
    Serial.print(eCO2);
    Serial.println(" ppm");
  } else {
    Serial.println(sensorsWarmedUp ? "NOT READY" : "WARMING UP");
  }
  
  Serial.print("TVOC        : ");
  if (ccsReady && sensorsWarmedUp) {
    Serial.print(TVOC);
    Serial.println(" ppb");
  } else {
    Serial.println(sensorsWarmedUp ? "NOT READY" : "WARMING UP");
  }
  
  Serial.print("CO          : ");
  if (adsReady) {
    Serial.print(CO_ppm, 2);
    Serial.println(" ppm");
  } else {
    Serial.println("NOT READY");
  }
  
  Serial.print("NO2         : ");
  if (adsReady) {
    Serial.print(NO2_ppb, 2);
    Serial.println(" ppb");
  } else {
    Serial.println("NOT READY");
  }
  
  Serial.println("========================================\n");
}

void sendSensorData() {
  // Only send data if sensors are warmed up
  if (!sensorsWarmedUp || !pmsReady) {
    Serial.println(">>> Skipping data send - sensors not ready");
    return;
  }
  
  // Create JSON document with calibrated values
  StaticJsonDocument<512> doc;
  doc["nodeId"] = NODE_ID;
  doc["nodeType"] = "CCS811";
  doc["pm25"] = pm25_calibrated;
  doc["pm10"] = pm10_calibrated;
  doc["tvoc"] = TVOC;
  doc["eco2"] = eCO2;
  doc["co"] = CO_ppm;
  doc["no2"] = NO2_ppb;
  doc["timestamp"] = millis();

  String msg;
  serializeJson(doc, msg);
  mesh.sendBroadcast(msg);
  
  Serial.println(">>> Data sent to mesh network");
}

void receivedCallback(uint32_t from, String &msg) {
  Serial.printf("<<< Received from %u: %s\n", from, msg.c_str());
  
  // Parse JSON
  StaticJsonDocument<512> doc;
  DeserializationError error = deserializeJson(doc, msg);
  
  if (!error) {
    int senderNodeId = doc["nodeId"];
    String nodeType = doc["nodeType"].as<String>();
    
    // Update received indicator
    lastReceivedNodeId = senderNodeId;
    lastReceivedTime = millis();
    
    Serial.printf("Data from Node %d (%s)\n", senderNodeId, nodeType.c_str());
    Serial.printf("PM2.5: %.2f, eCO2: %d ppm\n", 
                  doc["pm25"].as<float>(), 
                  doc["eco2"].as<int>());
  }
}

void newConnectionCallback(uint32_t nodeId) {
  Serial.printf(">>> New Connection: nodeId = %u\n", nodeId);
}

void changedConnectionCallback() {
  Serial.printf(">>> Changed connections. Nodes: %d\n", mesh.getNodeList().size());
}

boolean readPMSdata(Stream *s) {
  if (!s->available()) {
    return false;
  }
  
  // Read a byte at a time until we get to the special '0x42' start-byte
  if (s->peek() != 0x42) {
    s->read();
    return false;
  }
  
  // Now read all 32 bytes
  if (s->available() < 32) {
    return false;
  }
    
  uint8_t buffer[32];    
  uint16_t sum = 0;
  s->readBytes(buffer, 32);
  
  // Get checksum ready
  for (uint8_t i = 0; i < 30; i++) {
    sum += buffer[i];
  }
  
  // The data comes in endian'd, this solves it so it works on all platforms
  uint16_t buffer_u16[15];
  for (uint8_t i = 0; i < 15; i++) {
    buffer_u16[i] = buffer[2 + i*2 + 1];
    buffer_u16[i] += (buffer[2 + i*2] << 8);
  }
  
  // Put it into a nice struct :)
  memcpy((void *)&data, (void *)buffer_u16, 30);
  
  if (sum != data.checksum) {
    Serial.println("Checksum failure");
    return false;
  }
  
  // Success!
  return true;
}