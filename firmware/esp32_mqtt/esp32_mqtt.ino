/*
 * ESP32 firmware template: two-way communication with IoT Platform over MQTT,
 * with optional TLS encryption and over-the-air (OTA) firmware updates.
 *
 * What it does
 *   - Sends telemetry every 10 seconds: chip temperature, Wi-Fi signal strength
 *     and the state of the lamp (the built-in LED).
 *   - Follows the requested state of the actuator "lamp": switches the LED on or
 *     off and reports the new state back.
 *   - Runs the commands "blink" {"times": 5} and "ping" and sends the result back.
 *   - Announces "online" when it connects; the broker announces "offline" for it
 *     when the connection is lost (MQTT Last Will).
 *   - Reports its firmware version, board and Wi-Fi MAC address every time it
 *     connects, e.g. {"version": "1.0.0", "board": "esp32", "mac": "24:6F:28:AA:BB:CC"}.
 *   - Installs firmware updates started from the device page (command "ota"):
 *     the file is written to the spare OTA partition and checked (MD5) before the
 *     ESP32 restarts into it; the running firmware stays intact until then. Each
 *     step is reported, and the new firmware reports its version after it starts,
 *     which is how the platform knows the update succeeded.
 *   - Receives the JSON of a rule sent with "Publish to devices" on the rule page and
 *     confirms it on .../rules/<rule id>/ack, e.g. {"publish": 3, "ok": true, "size": 2154}.
 *     It does not run the rule yet.
 *
 * Before uploading
 *   1. On the platform: create the device and add a variable with key "lamp",
 *      kind "Actuator", value type "On / off".
 *   2. Fill in WIFI_SSID and WIFI_PASSWORD. Check MQTT_HOST, MQTT_PORT, USE_TLS,
 *      TOPIC_PREFIX and ACCESS_TOKEN: a template downloaded from the platform
 *      already contains the right values.
 *   3. Arduino IDE: Tools > Partition Scheme must support OTA, e.g.
 *      "Default 4MB with spiffs".
 *
 * Updating this device over the air later
 *   1. Upload this sketch once over USB with FW_VERSION "1.0.0".
 *   2. Change FW_VERSION (e.g. "1.1.0") and whatever else you need, then
 *      Sketch > Export Compiled Binary.
 *   3. Upload the .bin file on the platform's Firmware page with the same version
 *      and board, then choose it under "Update firmware" on the device page.
 *
 * Libraries (Arduino IDE: Sketch > Include Library > Manage Libraries):
 * PubSubClient by Nick O'Leary and ArduinoJson 7 by Benoit Blanchon.
 * WiFiClientSecure, HTTPClient and HTTPUpdate come with the board package
 * "esp32" by Espressif Systems. Serial Monitor: 115200 baud.
 *
 * Documentation: open "Documentation" in the platform menu, chapters
 * "Connect an ESP32" and "Firmware updates".
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>

// ---- this firmware ----------------------------------------------------------------
// Must be EXACTLY the version you enter when uploading the .bin file to the platform.
#define FW_VERSION "1.0.0"
#define FW_BOARD   "esp32"   // esp32, esp32s2, esp32s3 or esp32c3

// ---- fill in this part -------------------------------------------------------------
const char* WIFI_SSID     = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";

// Broker address, port and topic prefix of the platform (see the device page, MQTT tab).
const char* MQTT_HOST     = "broker.emqx.io";
const uint16_t MQTT_PORT  = 1883;
const char* TOPIC_PREFIX  = "paste-the-topic-prefix-here";
const char* ACCESS_TOKEN  = "paste-the-device-access-token-here";

// ---- encryption ---------------------------------------------------------------------
// USE_TLS 1: encrypted MQTT (usually port 8883), and firmware updates are downloaded
// over https:// only. The server is checked against ROOT_CA below.
// USE_TLS 0: no encryption (usually port 1883). Use it only on networks you trust.
#define USE_TLS 0

// Root certificate (root CA) that signed the server's certificate: ISRG Root X1 for
// servers using Let's Encrypt (PEM format, including the BEGIN/END lines). With it the
// ESP32 makes sure it talks to the real server and not to an impostor.
const char* ROOT_CA = R"PEM(
-----BEGIN CERTIFICATE-----
paste ISRG Root X1 here
-----END CERTIFICATE-----
)PEM";
// -----------------------------------------------------------------------------------

const int LED_PIN = 2;
const unsigned long SEND_INTERVAL_MS = 10000;

#if USE_TLS
WiFiClientSecure network;
#else
WiFiClient network;
#endif
PubSubClient mqtt(network);

String topicTelemetry, topicDesired, topicCommands, topicErrors, topicFirmware, topicStatus, topicRules;
bool lampOn = false;
unsigned long lastSend = 0;

// An accepted update waits to be run in loop(), not inside the MQTT callback,
// because the download takes tens of seconds.
long otaUpdateId = 0;           // 0 = none
long lastOtaUpdateId = 0;       // a command can arrive twice (QoS 1): run it only once
String otaUrl;
int otaLastPercent = -1;

void publishJson(const String& topic, JsonDocument& doc) {
  String text;
  serializeJson(doc, text);
  mqtt.publish(topic.c_str(), text.c_str());
  Serial.printf("-> %s  %s\n", topic.c_str(), text.c_str());
}

// ---- firmware report -----------------------------------------------------------------

void reportFirmwareVersion() {
  JsonDocument doc;
  doc["version"] = FW_VERSION;
  doc["board"] = FW_BOARD;
  doc["mac"] = WiFi.macAddress();   // Wi-Fi MAC address, e.g. "24:6F:28:AA:BB:CC"
  publishJson(topicFirmware, doc);
}

void reportOtaState(const char* state, int progress = -1, const String& error = "") {
  JsonDocument doc;
  doc["update"] = otaUpdateId;
  doc["state"] = state;
  if (progress >= 0) doc["progress"] = progress;
  if (error.length()) doc["error"] = error;
  publishJson(topicFirmware, doc);
  mqtt.loop();  // send now, do not wait for the next loop()
}

// Called by HTTPUpdate while downloading. Reports every 10 % of progress.
void onOtaProgress(int current, int total) {
  int percent = total > 0 ? (int)((long long)current * 100 / total) : 0;
  if (percent >= otaLastPercent + 10 || percent == 100) {
    otaLastPercent = percent;
    reportOtaState("DOWNLOADING", percent);
  }
}

void runOta() {
  Serial.printf("OTA: downloading %s\n", otaUrl.c_str());
  otaLastPercent = -1;
  reportOtaState("DOWNLOADING", 0);

  httpUpdate.rebootOnUpdate(false); // restart ourselves, AFTER reporting
  httpUpdate.onProgress(onOtaProgress);
  // update() downloads the file, writes it to the spare partition and checks the MD5
  // (x-MD5 header). If anything fails, the running firmware is left untouched.
  t_httpUpdate_return result;
  if (otaUrl.startsWith("https://")) {
    WiFiClientSecure https;
    https.setCACert(ROOT_CA);       // firmware is accepted only from the real server
    result = httpUpdate.update(https, otaUrl);
  } else {
#if USE_TLS
    // With TLS on, never install firmware that was downloaded without encryption.
    // The update fails; ask the platform administrator to serve downloads over https://.
    reportOtaState("FAILED", -1, "Refusing http:// download in TLS mode");
    otaUpdateId = 0;
    return;
#else
    WiFiClient http;
    result = httpUpdate.update(http, otaUrl);
#endif
  }

  if (result == HTTP_UPDATE_OK) {
    reportOtaState("DOWNLOADED");
    reportOtaState("VERIFIED");       // MD5 matches, image written and marked for boot
    reportOtaState("UPDATING");
    for (int i = 0; i < 10; i++) { mqtt.loop(); delay(50); }  // make sure the reports are sent
    Serial.println("OTA: restarting into the new firmware");
    ESP.restart();
  } else {
    String error = result == HTTP_UPDATE_NO_UPDATES ? String("Server sent no update")
                                                    : httpUpdate.getLastErrorString();
    Serial.printf("OTA failed: %s\n", error.c_str());
    reportOtaState("FAILED", -1, error);
  }
  otaUpdateId = 0;
}

// ---- requested state & commands -----------------------------------------------------------

void setLamp(bool on) {
  lampOn = on;
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}

void reportLamp() {
  JsonDocument doc;
  doc["lamp"] = lampOn ? 1 : 0;
  publishJson(topicTelemetry, doc);
}

void sendTelemetry() {
  JsonDocument doc;
  doc["chip_temperature"] = temperatureRead();
  doc["rssi"] = WiFi.RSSI();
  doc["lamp"] = lampOn ? 1 : 0;
  publishJson(topicTelemetry, doc);
}

void handleDesired(JsonDocument& desired) {
  if (!desired["lamp"].isNull()) {
    setLamp(desired["lamp"].as<float>() >= 0.5);
    reportLamp();
  }
}

void handleCommand(const String& id, JsonDocument& command) {
  const char* method = command["method"] | "";
  JsonDocument result;
  if (strcmp(method, "ota") == 0) {
    long update = command["params"]["update"] | 0L;
    const char* version = command["params"]["version"] | "";
    if (update == lastOtaUpdateId) return;            // sent again: already handled
    if (strcmp(version, FW_VERSION) == 0) {
      result["success"] = false;
      result["error"] = String("Already running ") + FW_VERSION;
    } else if (otaUpdateId != 0) {
      result["success"] = false;
      result["error"] = "Another update is running";
    } else {
      // Accept first; the download runs in loop() after this answer has been sent.
      otaUpdateId = lastOtaUpdateId = update;
      otaUrl = String(command["params"]["url"] | "");
      result["success"] = true;
      result["message"] = "Update accepted";
    }
  } else if (strcmp(method, "blink") == 0) {
    int times = command["params"]["times"] | 3;
    for (int i = 0; i < times; i++) {
      setLamp(!lampOn); delay(150);
      setLamp(!lampOn); delay(150);
    }
    result["success"] = true;
    result["blinked"] = times;
  } else if (strcmp(method, "ping") == 0) {
    result["success"] = true;
    result["uptime_ms"] = millis();
    result["version"] = FW_VERSION;
  } else {
    result["success"] = false;
    result["error"] = String("Unknown method: ") + method;
  }
  publishJson(topicCommands + id + "/response", result);
}

// ---- rule JSON ("Publish to devices" on a rule page) -------------------------------------
// The platform sends the whole rule as JSON to <prefix>/<token>/rules/<rule id> (retained),
// and waits for a reply on .../ack. This sketch only confirms that the JSON arrived and could
// be read; it does not run the rule. An empty message means the rule was withdrawn.

void handleRule(const String& ruleId, byte* payload, unsigned int length) {
  if (length == 0) {
    Serial.printf("   rule %s withdrawn\n", ruleId.c_str());
    return;
  }
  JsonDocument doc;
  JsonDocument reply;
  DeserializationError error = deserializeJson(doc, payload, length);
  reply["publish"] = error ? 0 : (long)(doc["publish"]["id"] | 0);
  reply["size"] = length;
  if (error) {
    reply["ok"] = false;
    reply["error"] = String("JSON error: ") + error.c_str();
  } else {
    reply["ok"] = true;
    reply["rule"] = (const char*)(doc["rule"]["name"] | "");
    Serial.printf("   rule %s \"%s\" received (%u bytes, %u devices)\n", ruleId.c_str(),
                  (const char*)(doc["rule"]["name"] | "?"), length, (unsigned)doc["rule"]["devices"].size());
  }
  publishJson(topicRules + ruleId + "/ack", reply);
}

void onMessage(char* topic, byte* payload, unsigned int length) {
  String t(topic);
  if (t.startsWith(topicRules)) {          // may be large: do not print the whole JSON
    Serial.printf("<- %s  (%u bytes)\n", topic, length);
    handleRule(t.substring(topicRules.length()), payload, length);
    return;
  }
  Serial.printf("<- %s  %.*s\n", topic, (int)length, (const char*)payload);
  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) {
    Serial.println("   (not valid JSON, ignored)");
    return;
  }
  if (t == topicDesired) {
    handleDesired(doc);
  } else if (t.startsWith(topicCommands)) {
    handleCommand(t.substring(topicCommands.length()), doc);
  } else if (t == topicErrors) {
    Serial.printf("   PLATFORM REJECTED A MESSAGE: %s\n", (const char*)(doc["error"] | "?"));
  }
}

// ---- connection ----------------------------------------------------------------------------

void connectWifi() {
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWi-Fi connected, IP %s\n", WiFi.localIP().toString().c_str());
}

void connectMqtt() {
  while (!mqtt.connected()) {
    String clientId = "esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.printf("Connecting to MQTT %s:%u as %s ... ", MQTT_HOST, MQTT_PORT, clientId.c_str());
    // Username = password = access token. The platform's broker only lets this device
    // use topics that contain its own token; brokers without accounts ignore the login.
    // Last Will: if the connection is lost without a goodbye (power cut, Wi-Fi gone),
    // the broker publishes "offline" (retained) to the status topic for this device.
    if (mqtt.connect(clientId.c_str(), ACCESS_TOKEN, ACCESS_TOKEN,
                     topicStatus.c_str(), 1, true, "offline")) {
      Serial.println("ok");
      mqtt.publish(topicStatus.c_str(), "online", true);   // retained: the session has started
      // Subscribe after EVERY connection. The broker keeps (retains) the requested state,
      // so it arrives right after subscribing and the lamp returns to the requested state.
      mqtt.subscribe(topicDesired.c_str(), 1);
      mqtt.subscribe((topicCommands + "+").c_str(), 1);
      mqtt.subscribe(topicErrors.c_str(), 0);
      mqtt.subscribe((topicRules + "+").c_str(), 1);   // rule JSON (retained)
      // On every connection (including the restart after an update): report the version.
      reportFirmwareVersion();
    } else {
      Serial.printf("failed (state %d), retrying in 5 s\n", mqtt.state());
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial.printf("\nFirmware %s (%s)\n", FW_VERSION, FW_BOARD);
  pinMode(LED_PIN, OUTPUT);
  setLamp(false);

  String base = String(TOPIC_PREFIX) + "/" + ACCESS_TOKEN;
  topicTelemetry = base + "/telemetry";
  topicDesired   = base + "/desired";
  topicCommands  = base + "/commands/";
  topicErrors    = base + "/errors";
  topicFirmware  = base + "/firmware";
  topicStatus    = base + "/status";
  topicRules     = base + "/rules/";

  connectWifi();
#if USE_TLS
  network.setCACert(ROOT_CA);
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  // Largest message the ESP32 can receive. Rule JSON can be several kilobytes; a larger
  // message is dropped by the library without any error.
  mqtt.setBufferSize(8192);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();  // call often: receives messages and keeps the connection alive

  if (otaUpdateId != 0) {
    runOta();
    return;
  }
  if (millis() - lastSend >= SEND_INTERVAL_MS) {
    lastSend = millis();
    sendTelemetry();
  }
}
