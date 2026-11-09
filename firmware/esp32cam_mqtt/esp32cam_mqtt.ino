/*
 * ESP32-CAM (AI-Thinker) firmware template: two-way communication with IoT Platform over MQTT,
 * JPEG snapshots over HTTP, optional TLS and over-the-air (OTA) firmware updates.
 *
 * Everything the ESP32 MQTT template does
 *   - Telemetry every SEND_INTERVAL_MS: Wi-Fi signal strength, free PSRAM, lamp state.
 *   - Follows the requested state of the actuator "lamp" (the small red LED, GPIO33).
 *     The platform may send only the actuators that changed: missing keys are left alone.
 *   - Commands "blink" {"times": 5} and "ping"; expired commands are not run (clock from NTP).
 *   - "online" on connect (and every 5 minutes), "offline" by MQTT Last Will.
 *   - Reports firmware version, board and Wi-Fi MAC address on every connection.
 *   - Firmware updates started from the device page (command "ota").
 *   - Confirms the JSON of a rule sent with "Publish to devices".
 *
 * Camera (tahap 27a)
 *   - Takes a JPEG picture and sends it with HTTP POST to <SERVER>/api/v1/<token>/snapshot:
 *       every snapshot_interval seconds (0 = only on request), and
 *       on the command "capture" (button "Capture now" on the device page, or a rule action
 *       "Send command" with method "capture"). The command is answered after the upload:
 *       {"success": true, "snapshot": 12, "size": 23456}.
 *   - Settings are actuators on the platform (add them as variables, kind "Actuator"):
 *       snapshot_interval  number, seconds between pictures (0 = off)        default SNAPSHOT_INTERVAL_S
 *       flash              on / off: use the white flash LED (GPIO4) while taking a picture
 *       framesize          number, 5 = QVGA 320x240, 8 = VGA 640x480, 9 = SVGA, 10 = XGA ... 13 = UXGA
 *       quality            number, JPEG quality 10 (best, largest) ... 63 (smallest)
 *     Each new value is reported back as telemetry, so the platform shows the device is in sync.
 *   - Pictures are NOT sent over MQTT: the platform's broker accepts messages of at most 64 KB.
 *
 * Before uploading
 *   1. On the platform: create the device with board "ESP32-CAM" (add the variable "lamp",
 *      kind "Actuator", value type "On / off", and the optional camera actuators above).
 *   2. Fill in WIFI_SSID and WIFI_PASSWORD. Check SERVER, MQTT_HOST, MQTT_PORT, USE_TLS,
 *      TOPIC_PREFIX and ACCESS_TOKEN: a template downloaded from the platform already contains them.
 *   3. Arduino IDE, Tools: Board "AI Thinker ESP32-CAM", Partition Scheme
 *      "Minimal SPIFFS (1.9MB APP with OTA/190KB SPIFFS)", PSRAM "Enabled".
 *      The ESP32-CAM has no USB port: use a USB-to-serial adapter (5V, GND, U0R, U0T) and connect
 *      IO0 to GND while uploading, then disconnect IO0 and press RESET. Power: 5 V, at least 1 A.
 *
 * Updating this device over the air later
 *   Change FW_VERSION, Sketch > Export Compiled Binary, upload the .ino.bin file (NOT .merged.bin)
 *   on the platform's Firmware page with board "ESP32-CAM", then "Update firmware" on the device page.
 *
 * Libraries: PubSubClient by Nick O'Leary and ArduinoJson 7 by Benoit Blanchon. The camera driver
 * (esp_camera.h), WiFiClientSecure, HTTPClient and HTTPUpdate come with the board package "esp32".
 * Serial Monitor: 115200 baud.
 *
 * Documentation: open "Documentation" in the platform menu, chapters "Cameras (ESP32-CAM)",
 * "Connect an ESP32" and "Firmware updates".
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include "esp_camera.h"

// ---- this firmware ----------------------------------------------------------------
// Must be EXACTLY the version you enter when uploading the .bin file to the platform.
#define FW_VERSION "1.0.0"
#define FW_BOARD   "esp32cam"   // board "ESP32-CAM" on the platform

// ---- fill in this part -------------------------------------------------------------
const char* WIFI_SSID     = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";

// Web address of the platform, without a slash at the end (pictures are sent here).
const char* SERVER       = "http://192.168.1.10:8000";
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

const int LED_PIN = 33;               // small red LED on the back, lights when LOW
const int FLASH_PIN = 4;              // white flash LED, lights when HIGH (very bright)
const unsigned long SEND_INTERVAL_MS = 10000;
const long SNAPSHOT_INTERVAL_S = 300; // default: one picture every 5 minutes (0 = only on request)
// "online" heartbeat, see the ESP32 MQTT template.
const unsigned long STATUS_EVERY_MS = 5UL * 60 * 1000;
unsigned long lastStatus = 0;

// AI-Thinker ESP32-CAM camera pins (OV2640).
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

#if USE_TLS
WiFiClientSecure network;
#else
WiFiClient network;
#endif
PubSubClient mqtt(network);

String topicTelemetry, topicDesired, topicCommands, topicErrors, topicFirmware, topicStatus, topicRules;
bool lampOn = false;
unsigned long lastSend = 0;

// Camera settings (actuators on the platform, see the comment at the top).
bool cameraReady = false;
long snapshotIntervalS = SNAPSHOT_INTERVAL_S;
bool flashOn = false;
int frameSize = FRAMESIZE_VGA;    // 8
int jpegQuality = 12;
unsigned long lastSnapshot = 0;
// A "capture" command is answered after the upload, in loop() (the upload takes a while).
String captureCommandId;
String lastCaptureId;             // a command can arrive twice (QoS 1): take the picture only once

// An accepted update waits to be run in loop(), not inside the MQTT callback.
long otaUpdateId = 0;
long lastOtaUpdateId = 0;
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

// ---- camera ------------------------------------------------------------------------------

bool startCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;     // always the newest picture, not an old one in the buffer
  if (psramFound()) {
    config.frame_size = FRAMESIZE_UXGA;      // largest size the buffers must hold; set lower below
    config.jpeg_quality = jpegQuality;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    config.frame_size = FRAMESIZE_SVGA;
    config.jpeg_quality = 12;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x (check the board type and the camera cable)\n", err);
    return false;
  }
  sensor_t* sensor = esp_camera_sensor_get();
  sensor->set_framesize(sensor, (framesize_t)frameSize);
  sensor->set_quality(sensor, jpegQuality);
  return true;
}

bool useHttps() { return strncmp(SERVER, "https://", 8) == 0; }

// Takes a picture and sends it. Returns the snapshot id from the platform, or -1 (error in `error`).
long captureAndUpload(const char* source, const String& commandId, String& error, size_t& size) {
  if (!cameraReady) { error = "Camera not ready"; return -1; }
  if (flashOn) { digitalWrite(FLASH_PIN, HIGH); delay(120); }
  camera_fb_t* fb = esp_camera_fb_get();      // the first frame may be old (exposure, flash): skip it
  if (fb) esp_camera_fb_return(fb);
  fb = esp_camera_fb_get();
  if (flashOn) digitalWrite(FLASH_PIN, LOW);
  if (!fb) { error = "Capture failed"; return -1; }
  size = fb->len;

  String url = String(SERVER) + "/api/v1/" + ACCESS_TOKEN + "/snapshot";
  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  if (useHttps()) {
    secure.setCACert(ROOT_CA);
    http.begin(secure, url);
  } else {
    http.begin(plain, url);
  }
  http.setTimeout(15000);
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("X-Source", source);
  if (commandId.length()) http.addHeader("X-Command-Id", commandId);
  int status = http.POST(fb->buf, fb->len);
  String body = http.getString();
  http.end();
  esp_camera_fb_return(fb);

  Serial.printf("Snapshot %u bytes -> HTTP %d %s\n", (unsigned)size, status, body.c_str());
  if (status != 201) {
    // 413 too large (lower framesize/quality), 429 too often, 401 wrong token, < 0 no connection.
    error = "Upload failed: HTTP " + String(status);
    JsonDocument reply;
    if (!deserializeJson(reply, body) && !reply["error"].isNull()) error += String(" ") + (const char*)reply["error"];
    return -1;
  }
  JsonDocument reply;
  if (deserializeJson(reply, body)) return 0;
  return reply["id"] | 0L;
}

// ---- requested state & commands -----------------------------------------------------------

void setLamp(bool on) {
  lampOn = on;
  digitalWrite(LED_PIN, on ? LOW : HIGH);    // the red LED lights when LOW
}

void sendTelemetry() {
  JsonDocument doc;
  doc["rssi"] = WiFi.RSSI();
  doc["free_psram"] = (long)ESP.getFreePsram();
  doc["lamp"] = lampOn ? 1 : 0;
  publishJson(topicTelemetry, doc);
}

// Applies the requested states that are present in the message and reports them back.
void handleDesired(JsonDocument& desired) {
  JsonDocument report;
  if (!desired["lamp"].isNull()) {
    setLamp(desired["lamp"].as<float>() >= 0.5);
    report["lamp"] = lampOn ? 1 : 0;
  }
  if (!desired["flash"].isNull()) {
    flashOn = desired["flash"].as<float>() >= 0.5;
    report["flash"] = flashOn ? 1 : 0;
  }
  if (!desired["snapshot_interval"].isNull()) {
    snapshotIntervalS = max(0L, (long)desired["snapshot_interval"].as<float>());
    lastSnapshot = millis();
    report["snapshot_interval"] = snapshotIntervalS;
  }
  sensor_t* sensor = cameraReady ? esp_camera_sensor_get() : nullptr;
  if (!desired["framesize"].isNull()) {
    frameSize = constrain((int)desired["framesize"].as<float>(), (int)FRAMESIZE_QQVGA, (int)FRAMESIZE_UXGA);
    if (sensor) sensor->set_framesize(sensor, (framesize_t)frameSize);
    report["framesize"] = frameSize;
  }
  if (!desired["quality"].isNull()) {
    jpegQuality = constrain((int)desired["quality"].as<float>(), 10, 63);
    if (sensor) sensor->set_quality(sensor, jpegQuality);
    report["quality"] = jpegQuality;
  }
  if (report.size() > 0) publishJson(topicTelemetry, report);
}

// ---- command expiry --------------------------------------------------------------------------
// Every command carries "expires_at" (Unix time in seconds, UTC) and "ttl" (seconds left when
// the platform sent it). The platform does not send expired commands, and the MQTT broker drops
// commands that expire while waiting in its queue; this check catches the rest, e.g. a command
// that arrived late. The clock is set by NTP (configTime in setup). Until the clock is set, a
// command is run as before.
const long CLOCK_TOLERANCE_S = 2;     // allowance for a clock that is slightly off

bool clockIsSet() {
  return time(nullptr) > 1700000000;  // later than November 2023: NTP has answered
}

// Seconds since the command expired (> 0 = expired), or 0 when unknown / still valid.
template <typename T>
long secondsExpired(const T& command) {
  long expiresAt = command["expires_at"] | 0L;
  if (expiresAt <= 0 || !clockIsSet()) return 0;
  long late = (long)time(nullptr) - expiresAt;
  return late > CLOCK_TOLERANCE_S ? late : 0;
}

void handleCommand(const String& id, JsonDocument& command) {
  const char* method = command["method"] | "";
  long late = secondsExpired(command);
  if (late > 0) {
    Serial.printf("   command %s (%s) expired %ld s ago: not run\n", id.c_str(), method, late);
    return;
  }
  JsonDocument result;
  if (strcmp(method, "capture") == 0) {
    // Answered in loop() after the picture has been sent (see runCapture).
    if (id != lastCaptureId) captureCommandId = lastCaptureId = id;
    return;
  } else if (strcmp(method, "ota") == 0) {
    long update = command["params"]["update"] | 0L;
    const char* version = command["params"]["version"] | "";
    if (update == lastOtaUpdateId) return;
    if (strcmp(version, FW_VERSION) == 0) {
      result["success"] = false;
      result["error"] = String("Already running ") + FW_VERSION;
    } else if (otaUpdateId != 0) {
      result["success"] = false;
      result["error"] = "Another update is running";
    } else {
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

// A "capture" command: take the picture, send it, then answer the command.
void runCapture() {
  String id = captureCommandId;
  captureCommandId = "";
  String error;
  size_t size = 0;
  long snapshot = captureAndUpload("command", id, error, size);
  JsonDocument result;
  if (snapshot >= 0) {
    result["success"] = true;
    result["snapshot"] = snapshot;
    result["size"] = (long)size;
    lastSnapshot = millis();
  } else {
    result["success"] = false;
    result["error"] = error;
  }
  publishJson(topicCommands + id + "/response", result);
}

// ---- rule JSON ("Publish to devices" on a rule page) -------------------------------------
// The platform sends the rule as JSON to <prefix>/<token>/rules/<rule id> (retained), and
// waits for a reply on the same topic + "/ack". Every device of the rule gets the SAME message:
//   {"publish": {"id": 3, "rev": "9f3a1c07"}, "model": "ECA", "name": "...",
//    "devices": [{"name": "Pump", "mac": "AA:BB:CC:00:00:03"}, ...], "eca": {...}, "actions": [...]}
// In a "compact" message, "device" in conditions and actions is the position in "devices"
// (0, 1, ...) instead of the device name. This board finds itself by its MAC address.
// This sketch only confirms that the JSON arrived and could be read; it does not run the rule.
// An empty message means the rule was withdrawn (no reply).

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
    // Find this board in "devices" by its Wi-Fi MAC address.
    JsonArray devices = doc["devices"].as<JsonArray>();
    String mine = WiFi.macAddress();
    int self = -1;
    bool anyMac = false;
    for (size_t i = 0; i < devices.size(); i++) {
      const char* mac = devices[i]["mac"] | "";
      if (*mac) anyMac = true;
      if (mine.equalsIgnoreCase(mac)) self = (int)i;
    }
    if (anyMac && self < 0) {
      reply["ok"] = false;
      reply["error"] = "MAC " + mine + " is not in the rule";
    } else {
      reply["ok"] = true;
      reply["rule"] = (const char*)(doc["name"] | "");
      Serial.printf("   rule %s \"%s\" received (%u bytes, %u devices, this board is #%d)\n", ruleId.c_str(),
                    (const char*)(doc["name"] | "?"), length, (unsigned)devices.size(), self);
    }
  }
  publishJson(topicRules + ruleId + "/ack", reply);
}

void onMessage(char* topic, byte* payload, unsigned int length) {
  String t(topic);
  if (t.startsWith(topicRules)) {
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
    String clientId = "esp32cam-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.printf("Connecting to MQTT %s:%u as %s ... ", MQTT_HOST, MQTT_PORT, clientId.c_str());
    if (mqtt.connect(clientId.c_str(), ACCESS_TOKEN, ACCESS_TOKEN,
                     topicStatus.c_str(), 1, true, "offline")) {
      Serial.println("ok");
      mqtt.subscribe(topicDesired.c_str(), 1);
      mqtt.subscribe((topicCommands + "+").c_str(), 1);
      mqtt.subscribe(topicErrors.c_str(), 0);
      mqtt.subscribe((topicRules + "+").c_str(), 1);
      mqtt.publish(topicStatus.c_str(), "online", true);
      lastStatus = millis();
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
  pinMode(FLASH_PIN, OUTPUT);
  digitalWrite(FLASH_PIN, LOW);
  setLamp(false);
  cameraReady = startCamera();

  String base = String(TOPIC_PREFIX) + "/" + ACCESS_TOKEN;
  topicTelemetry = base + "/telemetry";
  topicDesired   = base + "/desired";
  topicCommands  = base + "/commands/";
  topicErrors    = base + "/errors";
  topicFirmware  = base + "/firmware";
  topicStatus    = base + "/status";
  topicRules     = base + "/rules/";

  connectWifi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");
#if USE_TLS
  network.setCACert(ROOT_CA);
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(8192);
  lastSnapshot = millis();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  if (otaUpdateId != 0) {
    runOta();
    return;
  }
  if (captureCommandId.length()) {
    runCapture();
  } else if (snapshotIntervalS > 0 && millis() - lastSnapshot >= (unsigned long)snapshotIntervalS * 1000UL) {
    lastSnapshot = millis();
    String error;
    size_t size = 0;
    if (captureAndUpload("periodic", "", error, size) < 0) Serial.printf("Snapshot: %s\n", error.c_str());
  }
  if (millis() - lastSend >= SEND_INTERVAL_MS) {
    lastSend = millis();
    sendTelemetry();
  }
  if (millis() - lastStatus >= STATUS_EVERY_MS) {
    lastStatus = millis();
    mqtt.publish(topicStatus.c_str(), "online", true);
  }
}
