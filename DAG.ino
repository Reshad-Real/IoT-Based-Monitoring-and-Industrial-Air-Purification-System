/***************************************************************************
   DATA AGGREGATION GATEWAY - ESP32
   Receives data from all sensor nodes via mesh network
   Forwards aggregated data to Fan Controller (Central Control System)
   Displays summary on 0.96" OLED (I2C: 0x3C)
   
   NEW FEATURES:
   - Forwards air quality data to Fan Controller
   - Tracks Fan Controller connection status
   - Aggregates worst-case pollutant values for fan control
   - Sends data immediately upon receiving from ANY node
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


// ================= OLED CONFIGURATION =================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);


// ================= NODE DATA STORAGE =================
struct NodeData {
  int nodeId;
  String nodeType;
  float pm25;
  float pm10;
  float temp;
  float humidity;
  int aqi;
  int tvoc;
  int eco2;
  float co;
  float no2;
  unsigned long lastUpdate;
  bool active;
  bool dataValid;
};


NodeData nodes[3]; // Store data for 3 nodes
unsigned long lastDisplayUpdate = 0;
const unsigned long DISPLAY_INTERVAL = 3000; // Rotate display every 3 seconds
int currentDisplayNode = 0;
bool showSummary = true;
bool forceDisplayUpdate = false;


// ================= FAN CONTROLLER TRACKING =================
uint32_t fanControllerNodeId = 0;  // Mesh ID of fan controller
bool fanControllerConnected = false;
unsigned long lastFanDataSent = 0;
const unsigned long FAN_DATA_INTERVAL = 2000; // Send data every 2 seconds minimum


// ================= MESH =================
Scheduler userScheduler;
painlessMesh mesh;


// ================= FUNCTION PROTOTYPES =================
void receivedCallback(uint32_t from, String &msg);
void newConnectionCallback(uint32_t nodeId);
void changedConnectionCallback();
void updateDisplay();
void displayNodeData(int nodeIndex);
void displaySummary();
bool validateNodeData(int nodeIndex);
void initializeNodeData(int nodeIndex);
void sendDataToFanController();
void aggregateAndSendToFan();
Task taskUpdateDisplay(TASK_MILLISECOND * 100, TASK_FOREVER, &updateDisplay);
Task taskSendToFanController(TASK_SECOND * 5, TASK_FOREVER, &aggregateAndSendToFan);


void setup() {
  Serial.begin(115200);
  delay(1000);
 
  Serial.println("\n\n========================================");
  Serial.println("Data Aggregation Gateway");
  Serial.println("with Fan Controller Integration");
  Serial.println("========================================");
 
  // Initialize node data storage with safe defaults
  for (int i = 0; i < 3; i++) {
    initializeNodeData(i);
  }


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
  display.setCursor(0, 0);
  display.println("Data Aggregation");
  display.println("Gateway");
  display.println("=================");
  display.println("Initializing...");
  display.display();
  delay(2000);


  // Initialize Mesh Network
  Serial.println("Initializing Mesh Network...");
  mesh.setDebugMsgTypes(ERROR | STARTUP | CONNECTION);
  mesh.init(MESH_PREFIX, MESH_PASSWORD, &userScheduler, MESH_PORT);
  mesh.onReceive(&receivedCallback);
  mesh.onNewConnection(&newConnectionCallback);
  mesh.onChangedConnections(&changedConnectionCallback);


  userScheduler.addTask(taskUpdateDisplay);
  taskUpdateDisplay.enable();
 
  userScheduler.addTask(taskSendToFanController);
  taskSendToFanController.enable();


  Serial.println("Gateway ready!");
  Serial.printf("Gateway Mesh ID: %u\n", mesh.getNodeId());
  Serial.println("Waiting for sensor nodes and fan controller...");
  Serial.println("========================================\n");
 
  // Show initial summary
  displaySummary();
}


void loop() {
  mesh.update();
}


void initializeNodeData(int nodeIndex) {
  nodes[nodeIndex].nodeId = nodeIndex + 1;
  nodes[nodeIndex].nodeType = "Unknown";
  nodes[nodeIndex].pm25 = 0.0;
  nodes[nodeIndex].pm10 = 0.0;
  nodes[nodeIndex].temp = 0.0;
  nodes[nodeIndex].humidity = 0.0;
  nodes[nodeIndex].aqi = 0;
  nodes[nodeIndex].tvoc = 0;
  nodes[nodeIndex].eco2 = 0;
  nodes[nodeIndex].co = 0.0;
  nodes[nodeIndex].no2 = 0.0;
  nodes[nodeIndex].lastUpdate = 0;
  nodes[nodeIndex].active = false;
  nodes[nodeIndex].dataValid = false;
}


bool validateNodeData(int nodeIndex) {
  // Check if data is within reasonable ranges
  if (nodes[nodeIndex].pm25 < 0 || nodes[nodeIndex].pm25 > 2000) return false;
  if (nodes[nodeIndex].pm10 < 0 || nodes[nodeIndex].pm10 > 3000) return false;
  if (nodes[nodeIndex].eco2 < 0 || nodes[nodeIndex].eco2 > 65000) return false;
  if (nodes[nodeIndex].tvoc < 0 || nodes[nodeIndex].tvoc > 65000) return false;
  if (nodes[nodeIndex].co < 0 || nodes[nodeIndex].co > 1000) return false;
  if (nodes[nodeIndex].no2 < 0 || nodes[nodeIndex].no2 > 2000) return false;
 
  // If node has temp/humidity, validate those too
  if (nodes[nodeIndex].nodeType == "ENS160_AHT21") {
    if (nodes[nodeIndex].temp < -40 || nodes[nodeIndex].temp > 85) return false;
    if (nodes[nodeIndex].humidity < 0 || nodes[nodeIndex].humidity > 100) return false;
  }
 
  return true;
}


void aggregateAndSendToFan() {
  // Check if fan controller is connected
  if (!fanControllerConnected || fanControllerNodeId == 0) {
    Serial.println(">>> Fan controller not connected, cannot send data");
    return;
  }
 
  // Find the worst-case values from all active sensor nodes
  float maxPM25 = 0.0;
  float maxPM10 = 0.0;
  int maxTVOC = 0;
  int maxECO2 = 0;
  float maxCO = 0.0;
  float maxNO2 = 0.0;
  int activeNodeCount = 0;
 
  for (int i = 0; i < 3; i++) {
    if (nodes[i].active && nodes[i].dataValid) {
      activeNodeCount++;
      if (nodes[i].pm25 > maxPM25) maxPM25 = nodes[i].pm25;
      if (nodes[i].pm10 > maxPM10) maxPM10 = nodes[i].pm10;
      if (nodes[i].tvoc > maxTVOC) maxTVOC = nodes[i].tvoc;
      if (nodes[i].eco2 > maxECO2) maxECO2 = nodes[i].eco2;
      if (nodes[i].co > maxCO) maxCO = nodes[i].co;
      if (nodes[i].no2 > maxNO2) maxNO2 = nodes[i].no2;
    }
  }
 
  // Send data even if only 1 node is active (REMOVED: requirement for all nodes)
  if (activeNodeCount == 0) {
    Serial.println(">>> No active sensor nodes, not sending to fan controller");
    return;
  }
 
  // Create JSON message for fan controller
  StaticJsonDocument<512> doc;
  doc["cmd"] = "AIR_DATA";
  doc["pm25"] = maxPM25;
  doc["pm10"] = maxPM10;
  doc["tvoc"] = maxTVOC;
  doc["eco2"] = maxECO2;
  doc["co"] = maxCO;
  doc["no2"] = maxNO2;
  doc["activeNodes"] = activeNodeCount;
  doc["timestamp"] = millis();
 
  String msg;
  serializeJson(doc, msg);
 
  // Send to fan controller
  mesh.sendSingle(fanControllerNodeId, msg);
 
  Serial.println("\n>>> Sent aggregated data to Fan Controller");
  Serial.printf("Target Node ID: %u\n", fanControllerNodeId);
  Serial.printf("Active Sensors: %d/3\n", activeNodeCount);
  Serial.printf("Max PM2.5: %.1f | Max PM10: %.1f\n", maxPM25, maxPM10);
  Serial.printf("Max TVOC: %d | Max eCO2: %d\n", maxTVOC, maxECO2);
  Serial.printf("Max CO: %.1f | Max NO2: %.1f\n", maxCO, maxNO2);
  Serial.println();
 
  lastFanDataSent = millis();
}


void updateDisplay() {
  unsigned long currentTime = millis();
 
  // Check for inactive nodes (no data for 60 seconds)
  for (int i = 0; i < 3; i++) {
    if (nodes[i].active && (currentTime - nodes[i].lastUpdate > 60000)) {
      nodes[i].active = false;
      nodes[i].dataValid = false;
      Serial.printf("Node %d marked inactive\n", nodes[i].nodeId);
    }
  }
 
  // Force immediate update if new data received
  bool shouldUpdate = forceDisplayUpdate || (currentTime - lastDisplayUpdate >= DISPLAY_INTERVAL);
 
  if (shouldUpdate) {
    lastDisplayUpdate = currentTime;
    forceDisplayUpdate = false;
   
    if (showSummary) {
      displaySummary();
      showSummary = false;
    } else {
      // Find next active node to display
      bool foundActive = false;
      for (int attempt = 0; attempt < 3; attempt++) {
        if (nodes[currentDisplayNode].active && nodes[currentDisplayNode].dataValid) {
          displayNodeData(currentDisplayNode);
          foundActive = true;
          currentDisplayNode = (currentDisplayNode + 1) % 3;
          break;
        }
        currentDisplayNode = (currentDisplayNode + 1) % 3;
      }
     
      if (!foundActive) {
        displaySummary(); // Show summary if no active nodes
      }
     
      showSummary = true; // Next cycle show summary
    }
  }
}


void displaySummary() {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
 
  display.println("GATEWAY SUMMARY");
  display.println("================");
 
  int activeNodes = 0;
  for (int i = 0; i < 3; i++) {
    if (nodes[i].active && nodes[i].dataValid) activeNodes++;
  }
 
  display.print("Sensors: ");
  display.print(activeNodes);
  display.println("/3");
 
  display.print("Fan Ctrl: ");
  if (fanControllerConnected) {
    display.println("ONLINE");
  } else {
    display.println("OFFLINE");
  }
 
  display.print("Mesh: ");
  display.println(mesh.getNodeList().size());
  display.println("----------------");
 
  // Show status of each node
  for (int i = 0; i < 3; i++) {
    display.print("N");
    display.print(nodes[i].nodeId);
    display.print(": ");
    if (nodes[i].active && nodes[i].dataValid) {
      display.println("OK");
    } else {
      display.println("--");
    }
  }
 
  display.display();
 
  Serial.println("=== GATEWAY SUMMARY ===");
  Serial.printf("Active Sensors: %d/3\n", activeNodes);
  Serial.printf("Fan Controller: %s\n", fanControllerConnected ? "ONLINE" : "OFFLINE");
  Serial.printf("Mesh Size: %d\n", mesh.getNodeList().size());
}


void displayNodeData(int nodeIndex) {
  if (!nodes[nodeIndex].active || !nodes[nodeIndex].dataValid) return;
 
  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
 
  display.print("NODE ");
  display.print(nodes[nodeIndex].nodeId);
  display.print(" (");
  display.print(nodes[nodeIndex].nodeType);
  display.println(")");
  display.println("================");
 
  display.print("PM2.5: ");
  display.print(nodes[nodeIndex].pm25, 2);
  display.println(" ug/m3");
 
  display.print("PM10 : ");
  display.print(nodes[nodeIndex].pm10, 2);
  display.println(" ug/m3");
 
  display.print("eCO2 : ");
  display.print(nodes[nodeIndex].eco2);
  display.println(" ppm");
 
  display.print("TVOC : ");
  display.print(nodes[nodeIndex].tvoc);
  display.println(" ppb");
 
  display.display();
 
  Serial.printf("=== NODE %d DATA ===\n", nodes[nodeIndex].nodeId);
  Serial.printf("Type: %s\n", nodes[nodeIndex].nodeType.c_str());
  Serial.printf("PM2.5: %.2f ug/m3, PM10: %.2f ug/m3\n", nodes[nodeIndex].pm25, nodes[nodeIndex].pm10);
  Serial.printf("eCO2: %d ppm, TVOC: %d ppb\n", nodes[nodeIndex].eco2, nodes[nodeIndex].tvoc);
  Serial.printf("CO: %.2f ppm, NO2: %.2f ppb\n", nodes[nodeIndex].co, nodes[nodeIndex].no2);
 
  if (nodes[nodeIndex].nodeType == "ENS160_AHT21") {
    Serial.printf("Temp: %.2f°C, Humidity: %.2f%%\n", nodes[nodeIndex].temp, nodes[nodeIndex].humidity);
    Serial.printf("AQI: %d\n", nodes[nodeIndex].aqi);
  }
  Serial.println();
}


void receivedCallback(uint32_t from, String &msg) {
  Serial.printf("\n>>> Received from mesh node %u\n", from);
 
  // Parse JSON
  StaticJsonDocument<1536> doc;
  DeserializationError error = deserializeJson(doc, msg);
 
  if (error) {
    Serial.print("JSON parse error: ");
    Serial.println(error.c_str());
    return;
  }
 
  // Check if it's a fan controller identification message
  if (doc.containsKey("type") && doc["type"] == "FAN_CONTROLLER") {
    fanControllerNodeId = from;
    fanControllerConnected = true;
    Serial.println(">>> FAN CONTROLLER IDENTIFIED <<<");
    Serial.printf("Fan Controller Mesh ID: %u\n", fanControllerNodeId);
    Serial.printf("Version: %s\n", doc["version"].as<const char*>());
   
    // Send current data immediately (even if not all nodes are active)
    aggregateAndSendToFan();
   
    forceDisplayUpdate = true;
    return;
  }
 
  // Check if it's cumulative data (array format)
  if (doc.containsKey("nodes")) {
    JsonArray nodesArray = doc["nodes"].as<JsonArray>();
    Serial.printf("Processing cumulative data with %d nodes\n", nodesArray.size());
   
    bool dataUpdated = false;
   
    for (JsonObject nodeData : nodesArray) {
      if (!nodeData.containsKey("nodeId")) {
        Serial.println("  ✗ Missing nodeId, skipping");
        continue;
      }
     
      int nodeId = nodeData["nodeId"];
      if (nodeId < 1 || nodeId > 3) {
        Serial.printf("  ✗ Invalid nodeId: %d, skipping\n", nodeId);
        continue;
      }
     
      int nodeIndex = nodeId - 1;
     
      // Update node data
      nodes[nodeIndex].nodeId = nodeId;
      nodes[nodeIndex].nodeType = nodeData["nodeType"] | "Unknown";
      nodes[nodeIndex].pm25 = nodeData["pm25"] | 0.0;
      nodes[nodeIndex].pm10 = nodeData["pm10"] | 0.0;
      nodes[nodeIndex].tvoc = nodeData["tvoc"] | 0;
      nodes[nodeIndex].eco2 = nodeData["eco2"] | 0;
      nodes[nodeIndex].co = nodeData["co"] | 0.0;
      nodes[nodeIndex].no2 = nodeData["no2"] | 0.0;
      nodes[nodeIndex].lastUpdate = millis();
     
      // Get additional data if available
      if (nodeData.containsKey("temp")) {
        nodes[nodeIndex].temp = nodeData["temp"] | 0.0;
        nodes[nodeIndex].humidity = nodeData["humidity"] | 0.0;
        nodes[nodeIndex].aqi = nodeData["aqi"] | 0;
      }
     
      // Validate data before marking as active
      if (validateNodeData(nodeIndex)) {
        nodes[nodeIndex].active = true;
        nodes[nodeIndex].dataValid = true;
        dataUpdated = true;
        Serial.printf("  ✓ Node %d (%s) data validated and updated\n", nodeId, nodes[nodeIndex].nodeType.c_str());
       
        // Send individual node data to Serial for Python dashboard
        StaticJsonDocument<512> individualDoc;
        individualDoc["nodeId"] = nodeId;
        individualDoc["nodeType"] = nodes[nodeIndex].nodeType;
        individualDoc["pm25"] = nodes[nodeIndex].pm25;
        individualDoc["pm10"] = nodes[nodeIndex].pm10;
        individualDoc["tvoc"] = nodes[nodeIndex].tvoc;
        individualDoc["eco2"] = nodes[nodeIndex].eco2;
        individualDoc["co"] = nodes[nodeIndex].co;
        individualDoc["no2"] = nodes[nodeIndex].no2;
       
        if (nodeData.containsKey("temp")) {
          individualDoc["temp"] = nodes[nodeIndex].temp;
          individualDoc["humidity"] = nodes[nodeIndex].humidity;
          individualDoc["aqi"] = nodes[nodeIndex].aqi;
        }
       
        String jsonOutput;
        serializeJson(individualDoc, jsonOutput);
        Serial.println(jsonOutput);  // Send to Python
      } else {
        Serial.printf("  ✗ Node %d data validation failed\n", nodeId);
        nodes[nodeIndex].dataValid = false;
      }
    }
   
    // CHANGED: Send to fan controller immediately if ANY data was updated
    if (dataUpdated && fanControllerConnected) {
      Serial.println(">>> Data updated from nodes, triggering immediate send to CCU");
      aggregateAndSendToFan();
    }
   
    forceDisplayUpdate = true;
   
  } else if (doc.containsKey("nodeId")) {
    // Single node data format
    int nodeId = doc["nodeId"];
    if (nodeId < 1 || nodeId > 3) {
      Serial.printf("Invalid node ID: %d\n", nodeId);
      return;
    }
   
    int nodeIndex = nodeId - 1;
   
    // Update node data
    nodes[nodeIndex].nodeId = nodeId;
    nodes[nodeIndex].nodeType = doc["nodeType"] | "Unknown";
    nodes[nodeIndex].pm25 = doc["pm25"] | 0.0;
    nodes[nodeIndex].pm10 = doc["pm10"] | 0.0;
    nodes[nodeIndex].tvoc = doc["tvoc"] | 0;
    nodes[nodeIndex].eco2 = doc["eco2"] | 0;
    nodes[nodeIndex].co = doc["co"] | 0.0;
    nodes[nodeIndex].no2 = doc["no2"] | 0.0;
    nodes[nodeIndex].lastUpdate = millis();
   
    // Get additional data if available
    if (doc.containsKey("temp")) {
      nodes[nodeIndex].temp = doc["temp"] | 0.0;
      nodes[nodeIndex].humidity = doc["humidity"] | 0.0;
      nodes[nodeIndex].aqi = doc["aqi"] | 0;
    }
   
    // Validate data before marking as active
    if (validateNodeData(nodeIndex)) {
      nodes[nodeIndex].active = true;
      nodes[nodeIndex].dataValid = true;
      Serial.printf("✓ Node %d (%s) data validated and stored\n", nodeId, nodes[nodeIndex].nodeType.c_str());
     
      // Send to Python dashboard
      String jsonOutput;
      serializeJson(doc, jsonOutput);
      Serial.println(jsonOutput);
     
      // CHANGED: Send to fan controller immediately upon receiving ANY valid node data
      if (fanControllerConnected) {
        Serial.println(">>> New node data received, triggering immediate send to CCU");
        aggregateAndSendToFan();
      }
     
      forceDisplayUpdate = true;
    } else {
      Serial.printf("✗ Node %d data validation failed\n", nodeId);
      nodes[nodeIndex].dataValid = false;
    }
  } else {
    Serial.println("✗ Unknown message format");
  }
}


void newConnectionCallback(uint32_t nodeId) {
  Serial.printf(">>> New Connection: nodeId = %u\n", nodeId);
 
  display.clearDisplay();
  display.setCursor(0, 20);
  display.setTextSize(1);
  display.println("NEW CONNECTION");
  display.print("Mesh Node ID: ");
  display.println(nodeId);
  display.display();
  delay(1500);
 
  forceDisplayUpdate = true;
}


void changedConnectionCallback() {
  Serial.printf(">>> Connections changed. Total mesh nodes: %d\n", mesh.getNodeList().size());
 
  // Check if fan controller disconnected
  if (fanControllerConnected) {
    bool stillConnected = false;
    SimpleList<uint32_t> nodeList = mesh.getNodeList();
    for (SimpleList<uint32_t>::iterator it = nodeList.begin(); it != nodeList.end(); ++it) {
      if (*it == fanControllerNodeId) {
        stillConnected = true;
        break;
      }
    }
   
    if (!stillConnected) {
      fanControllerConnected = false;
      fanControllerNodeId = 0;
      Serial.println(">>> Fan Controller disconnected");
    }
  }
 
  forceDisplayUpdate = true;
}