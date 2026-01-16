/***************************************************************************
   SMART FAN CONTROLLER - ESP32 38-Pin
   Receives air quality data from Gateway via Painless Mesh
   Controls dual fan speed based on pollution levels
   Displays status on 2.42" OLED (I2C: 0x3C)
***************************************************************************/

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "painlessMesh.h"
#include <ArduinoJson.h>

// ================= MESH CONFIGURATION =================
#define   MESH_PREFIX     "AirQualityMesh"
#define   MESH_PASSWORD   "AirQuality2024"
#define   MESH_PORT       5555

// ================= OLED CONFIGURATION (2.42" 128x64) =================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SCREEN_ADDRESS 0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ================= FAN PINS =================
#define FAN1_PWM_PIN  4      // PWM pin to fan 1 control
#define FAN1_TACH_PIN 15     // RPM feedback fan 1
#define FAN2_PWM_PIN  5      // PWM pin to fan 2 control
#define FAN2_TACH_PIN 16     // RPM feedback fan 2

// ================= PWM SETTINGS =================
int pwmFreq = 25000;         // 25 kHz
int pwmResolution = 8;       // Duty 0-255

// ================= POLLUTION THRESHOLDS =================
// PM2.5 (µg/m³) - WHO Guidelines
#define PM25_SAFE      12.0
#define PM25_LOW       35.0
#define PM25_MEDIUM    55.0
#define PM25_HIGH      150.0

// PM10 (µg/m³) - WHO Guidelines
#define PM10_SAFE      50.0
#define PM10_LOW       100.0
#define PM10_MEDIUM    150.0
#define PM10_HIGH      250.0

// TVOC (ppb) - Indoor Air Quality
#define TVOC_SAFE      220
#define TVOC_LOW       660
#define TVOC_MEDIUM    2200
#define TVOC_HIGH      5500

// eCO2 (ppm) - Indoor Air Quality
#define ECO2_SAFE      800
#define ECO2_LOW       1000
#define ECO2_MEDIUM    1500
#define ECO2_HIGH      2000

// CO (ppm) - OSHA Standards
#define CO_SAFE        9.0
#define CO_LOW         25.0
#define CO_MEDIUM      50.0
#define CO_HIGH        100.0

// NO2 (ppb) - EPA Standards
#define NO2_SAFE       53.0
#define NO2_LOW        100.0
#define NO2_MEDIUM     200.0
#define NO2_HIGH       400.0

// ================= FAN SPEED LEVELS =================
#define FAN_OFF        0      // 0% - Safe air quality
#define FAN_LOW        204    // 80% PWM - Low pollution
#define FAN_MEDIUM     230    // 90% PWM - Medium pollution
#define FAN_HIGH       255    // 100% PWM - High pollution

// ================= RPM CONSTANTS =================
#define MAX_RPM        3300   // Maximum fan RPM at 100%

// ================= GLOBAL VARIABLES =================
struct AirQualityData {
  float pm25;
  float pm10;
  int tvoc;
  int eco2;
  float co;
  float no2;
  unsigned long lastUpdate;
  bool dataValid;
};

AirQualityData airData;
int currentFanSpeed = 0;
String currentPollutant = "NONE";
String pollutionLevel = "SAFE";
unsigned long fan1_rpm = 0;
unsigned long fan2_rpm = 0;
bool gatewayConnected = false;

// ================= MESH =================
Scheduler userScheduler;
painlessMesh mesh;

// ================= FUNCTION PROTOTYPES =================
void receivedCallback(uint32_t from, String &msg);
void newConnectionCallback(uint32_t nodeId);
void changedConnectionCallback();
int calculateFanSpeed();
void setFanSpeed(int duty);
unsigned long calculateRPM(int duty);
void updateDisplay();
void sendIdentification();
Task taskUpdateDisplay(TASK_SECOND * 2, TASK_FOREVER, &updateDisplay);
Task taskSendIdentification(TASK_SECOND * 10, TASK_FOREVER, &sendIdentification);

void setup() {
  Serial.begin(115200);
  delay(1000);

  // Initialize air quality data
  airData.dataValid = false;
  airData.lastUpdate = 0;

  // Initialize OLED
  if(!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("SSD1306 allocation failed"));
    for(;;);
  }
  
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("Smart Fan Controller");
  display.println("====================");
  display.println("Initializing...");
  display.println("");
  display.println("Joining mesh");
  display.println("network...");
  display.display();
  delay(2000);

  // Setup PWM for both fans
  // Fan 1 - PWM Channel 0
  ledcSetup(0, pwmFreq, pwmResolution);
  ledcAttachPin(FAN1_PWM_PIN, 0);
  
  // Fan 2 - PWM Channel 1
  ledcSetup(1, pwmFreq, pwmResolution);
  ledcAttachPin(FAN2_PWM_PIN, 1);

  // Tach inputs
  pinMode(FAN1_TACH_PIN, INPUT_PULLUP);
  pinMode(FAN2_TACH_PIN, INPUT_PULLUP);

  // Start with fans off
  setFanSpeed(FAN_OFF);

  // Initialize Mesh Network
  Serial.println("Initializing Mesh Network...");
  mesh.setDebugMsgTypes(ERROR | STARTUP | CONNECTION);
  mesh.init(MESH_PREFIX, MESH_PASSWORD, &userScheduler, MESH_PORT);
  mesh.onReceive(&receivedCallback);
  mesh.onNewConnection(&newConnectionCallback);
  mesh.onChangedConnections(&changedConnectionCallback);

  userScheduler.addTask(taskUpdateDisplay);
  taskUpdateDisplay.enable();
  
  userScheduler.addTask(taskSendIdentification);
  taskSendIdentification.enable();

  Serial.println("=== Smart Fan Controller Ready ===");
  Serial.printf("Fan Controller Mesh ID: %u\n", mesh.getNodeId());
  Serial.println("Waiting for Gateway connection...");
  
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("Fan Controller Ready");
  display.println("====================");
  display.println("");
  display.print("Mesh ID: ");
  display.println(mesh.getNodeId());
  display.println("");
  display.println("Waiting for");
  display.println("Gateway...");
  display.display();
}

void loop() {
  mesh.update();
  
  // Check if data is stale (no update for 30 seconds)
  if (airData.dataValid && (millis() - airData.lastUpdate > 30000)) {
    airData.dataValid = false;
    gatewayConnected = false;
    setFanSpeed(FAN_OFF);
    currentPollutant = "NO DATA";
    pollutionLevel = "OFFLINE";
    Serial.println(">>> Gateway connection lost - data timeout");
  }
}

void sendIdentification() {
  // Broadcast identification message to gateway
  StaticJsonDocument<256> doc;
  doc["type"] = "FAN_CONTROLLER";
  doc["id"] = mesh.getNodeId();
  doc["version"] = "1.0";
  
  String msg;
  serializeJson(doc, msg);
  mesh.sendBroadcast(msg);
  
  Serial.println(">>> Sent identification broadcast");
}

void receivedCallback(uint32_t from, String &msg) {
  Serial.printf("\n>>> Received message from mesh node %u\n", from);
  
  StaticJsonDocument<512> doc;
  DeserializationError error = deserializeJson(doc, msg);
  
  if (error) {
    Serial.print("JSON parse error: ");
    Serial.println(error.c_str());
    return;
  }
  
  // Check if it's air quality data from gateway
  if (doc.containsKey("cmd") && doc["cmd"] == "AIR_DATA") {
    gatewayConnected = true;
    
    // Update air quality data
    airData.pm25 = doc["pm25"];
    airData.pm10 = doc["pm10"];
    airData.tvoc = doc["tvoc"];
    airData.eco2 = doc["eco2"];
    airData.co = doc["co"];
    airData.no2 = doc["no2"];
    airData.lastUpdate = millis();
    airData.dataValid = true;
    
    // Calculate and set fan speed
    int newFanSpeed = calculateFanSpeed();
    if (newFanSpeed != currentFanSpeed) {
      setFanSpeed(newFanSpeed);
    }
    
    // Debug output
    Serial.println("=== Air Quality Update Received ===");
    Serial.printf("From Mesh Node: %u\n", from);
    Serial.printf("PM2.5: %.1f | PM10: %.1f\n", airData.pm25, airData.pm10);
    Serial.printf("TVOC: %d | eCO2: %d\n", airData.tvoc, airData.eco2);
    Serial.printf("CO: %.1f | NO2: %.1f\n", airData.co, airData.no2);
    Serial.printf("Fan Speed: %d%% | Critical: %s\n", 
                  map(currentFanSpeed, 0, 255, 0, 100), 
                  currentPollutant.c_str());
  }
}

void newConnectionCallback(uint32_t nodeId) {
  Serial.printf(">>> New mesh connection: %u\n", nodeId);
  
  display.clearDisplay();
  display.setCursor(0, 20);
  display.setTextSize(1);
  display.println("NEW CONNECTION");
  display.print("Node: ");
  display.println(nodeId);
  display.display();
  delay(1000);
}

void changedConnectionCallback() {
  Serial.printf(">>> Mesh connections changed. Total nodes: %d\n", mesh.getNodeList().size());
}

int calculateFanSpeed() {
  if (!airData.dataValid) {
    currentPollutant = "NO DATA";
    pollutionLevel = "OFFLINE";
    return FAN_OFF;
  }
  
  int maxLevel = 0; // 0=safe, 1=low, 2=medium, 3=high
  String maxPollutant = "NONE";
  
  // Check PM2.5
  if (airData.pm25 >= PM25_HIGH) {
    maxLevel = 3;
    maxPollutant = "PM2.5";
  } else if (airData.pm25 >= PM25_MEDIUM && maxLevel < 2) {
    maxLevel = 2;
    maxPollutant = "PM2.5";
  } else if (airData.pm25 >= PM25_LOW && maxLevel < 1) {
    maxLevel = 1;
    maxPollutant = "PM2.5";
  }
  
  // Check PM10
  if (airData.pm10 >= PM10_HIGH) {
    maxLevel = 3;
    maxPollutant = "PM10";
  } else if (airData.pm10 >= PM10_MEDIUM && maxLevel < 2) {
    maxLevel = 2;
    maxPollutant = "PM10";
  } else if (airData.pm10 >= PM10_LOW && maxLevel < 1) {
    maxLevel = 1;
    maxPollutant = "PM10";
  }
  
  // Check TVOC
  if (airData.tvoc >= TVOC_HIGH) {
    maxLevel = 3;
    maxPollutant = "TVOC";
  } else if (airData.tvoc >= TVOC_MEDIUM && maxLevel < 2) {
    maxLevel = 2;
    maxPollutant = "TVOC";
  } else if (airData.tvoc >= TVOC_LOW && maxLevel < 1) {
    maxLevel = 1;
    maxPollutant = "TVOC";
  }
  
  // Check eCO2
  if (airData.eco2 >= ECO2_HIGH) {
    maxLevel = 3;
    maxPollutant = "eCO2";
  } else if (airData.eco2 >= ECO2_MEDIUM && maxLevel < 2) {
    maxLevel = 2;
    maxPollutant = "eCO2";
  } else if (airData.eco2 >= ECO2_LOW && maxLevel < 1) {
    maxLevel = 1;
    maxPollutant = "eCO2";
  }
  
  // Check CO
  if (airData.co >= CO_HIGH) {
    maxLevel = 3;
    maxPollutant = "CO";
  } else if (airData.co >= CO_MEDIUM && maxLevel < 2) {
    maxLevel = 2;
    maxPollutant = "CO";
  } else if (airData.co >= CO_LOW && maxLevel < 1) {
    maxLevel = 1;
    maxPollutant = "CO";
  }
  
  // Check NO2
  if (airData.no2 >= NO2_HIGH) {
    maxLevel = 3;
    maxPollutant = "NO2";
  } else if (airData.no2 >= NO2_MEDIUM && maxLevel < 2) {
    maxLevel = 2;
    maxPollutant = "NO2";
  } else if (airData.no2 >= NO2_LOW && maxLevel < 1) {
    maxLevel = 1;
    maxPollutant = "NO2";
  }
  
  // Set current pollutant and level
  currentPollutant = maxPollutant;
  
  // Return appropriate fan speed
  switch(maxLevel) {
    case 3:
      pollutionLevel = "HIGH";
      return FAN_HIGH;    // 100% PWM
    case 2:
      pollutionLevel = "MEDIUM";
      return FAN_MEDIUM;  // 90% PWM
    case 1:
      pollutionLevel = "LOW";
      return FAN_LOW;     // 80% PWM
    default:
      pollutionLevel = "SAFE";
      return FAN_OFF;     // 0% PWM
  }
}

void setFanSpeed(int duty) {
  currentFanSpeed = duty;
  ledcWrite(0, duty);  // Channel 0 for Fan 1
  ledcWrite(1, duty);  // Channel 1 for Fan 2
  
  // Calculate RPM based on PWM duty cycle
  if (duty > 0) {
    fan1_rpm = calculateRPM(duty);
    fan2_rpm = calculateRPM(duty);
  } else {
    fan1_rpm = 0;
    fan2_rpm = 0;
  }
  
  Serial.printf(">>> Fan speed changed to %d%% (PWM: %d)\n", 
                map(duty, 0, 255, 0, 100), duty);
}

unsigned long calculateRPM(int duty) {
  // Calculate RPM based on PWM duty cycle
  // Max RPM is 3300 at 100% (255 PWM)
  // RPM scales linearly with duty cycle
  if (duty == 0) return 0;
  
  unsigned long rpm = (unsigned long)((duty / 255.0) * MAX_RPM);
  return rpm;
}

void updateDisplay() {
  display.clearDisplay();
  
  // Title
  display.setTextSize(1);
  display.setCursor(5, 0);
  display.println("AIR QUALITY MONITOR");
  display.drawLine(0, 10, 128, 10, SSD1306_WHITE);
  
  // Connection Status
  display.setCursor(0, 13);
  display.print("Gateway: ");
  if (gatewayConnected) {
    display.println("ONLINE");
  } else {
    display.println("OFFLINE");
  }
  
  // Pollution Level (Large Text)
  display.setTextSize(2);
  display.setCursor(0, 24);
  
  if (pollutionLevel == "SAFE") {
    display.println("  SAFE");
  } else if (pollutionLevel == "LOW") {
    display.println("  LOW");
  } else if (pollutionLevel == "MEDIUM") {
    display.println(" MEDIUM");
  } else if (pollutionLevel == "HIGH") {
    display.println("  HIGH!");
  } else {
    display.println("OFFLINE");
  }
  
  display.drawLine(0, 42, 128, 42, SSD1306_WHITE);
  
  // Fan Speed and Critical Pollutant
  display.setTextSize(1);
  display.setCursor(0, 45);
  display.print("Fan: ");
  if (currentFanSpeed == 0) {
    display.print("OFF");
  } else {
    display.print(map(currentFanSpeed, 0, 255, 0, 100));
    display.print("%");
  }
  
  display.print(" | ");
  display.println(currentPollutant);
  
  display.setCursor(0, 55);
  display.print("RPM: ");
  display.print(fan1_rpm);
  display.print("/");
  display.print(MAX_RPM);
  display.print(" ");
  display.print(fan2_rpm);
  display.print("/");
  display.println(MAX_RPM);
  
  display.display();
}