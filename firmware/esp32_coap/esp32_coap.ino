/*
 * ESP32 firmware template: two-way communication with IoT Platform over CoAP (UDP).
 *
 * What it does
 *   - Sends telemetry every 10 seconds: chip temperature, Wi-Fi signal strength and
 *     the actuator state. These regular messages also keep the UDP path through
 *     your router open.
 *   - Asks for the requested actuator state every 2 seconds, switches the built-in
 *     LED on or off accordingly and reports the new state back.
 *   - Asks for new commands every 2 seconds, runs "blink" and "ping" and sends
 *     the result back.
 *     A command that has already expired ("expires_at", clock from NTP) is not run.
 *   - Reports its firmware version, board and Wi-Fi MAC address at start-up and
 *     every 10 minutes (POST api/v1/<token>/firmware).
 *
 * Before uploading
 *   1. On the platform: create the device, then add an actuator variable whose key
 *      is the same as ACTUATOR_KEY below (kind "Actuator", value type "On / off").
 *   2. Fill in WIFI_SSID and WIFI_PASSWORD. Check SERVER_HOST and ACCESS_TOKEN:
 *      a template downloaded from the platform already contains both.
 *   3. CoAP has no broker: the ESP32 must be able to reach the server directly over
 *      UDP (normally both on the same network). CoAP traffic is not encrypted, so
 *      use it only on networks you trust.
 *
 * Why ask every 2 seconds instead of using CoAP Observe? The Arduino library below
 * does not fully support Observe. Other CoAP clients can use Observe on the same
 * resources.
 *
 * Libraries (Arduino IDE: Sketch > Include Library > Manage Libraries):
 *   - "CoAP simple library" by Hirotaka Niisato
 *   - ArduinoJson by Benoit Blanchon (version 7)
 * Board: any ESP32 (board package "esp32" by Espressif Systems). Library versions
 * differ slightly; if replies look wrong, check Serial Monitor at 115200 baud.
 *
 * Documentation: open "Documentation" in the platform menu, chapter "Connect an ESP32".
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include <coap-simple.h>
#include <ArduinoJson.h>
#include <time.h>

// ---- fill in this part -------------------------------------------------------
const char* WIFI_SSID     = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";
// Address of the platform's CoAP server: an IP address such as "192.168.1.10" or a
// domain name. Never use 127.0.0.1: for the ESP32 that address means the ESP32 itself.
const char* SERVER_HOST   = "192.168.1.10";
const int   SERVER_PORT   = 5683;
const char* ACCESS_TOKEN  = "paste-the-device-access-token-here";
const char* ACTUATOR_KEY  = "pump";        // key of the actuator variable shown by the LED
// -----------------------------------------------------------------------------

// FW_TITLE and FW_VERSION must be EXACTLY the name and version you enter when uploading the .bin
// file to the platform (capital letters in the name do not matter). The name tells programs apart:
// change it when this sketch becomes a different program, e.g. "Greenhouse node".
#define FW_TITLE   "ESP32 CoAP node"
#define FW_VERSION "1.0.0"
#define FW_BOARD   "esp32"   // esp32, esp32s2, esp32s3 or esp32c3

const int LED_PIN = 2;
const unsigned long TELEMETRY_MS = 10000;
const unsigned long POLL_MS = 2000;
const unsigned long FIRMWARE_REPORT_MS = 600000;   // UDP packets can get lost: repeat every 10 minutes

WiFiUDP udp;
Coap coap(udp);
IPAddress SERVER_IP;    // SERVER_HOST looked up in setup()

String pathTelemetry;   // api/v1/<token>/telemetry
String pathDesired;     // api/v1/<token>/desired
String pathCommands;    // api/v1/<token>/commands

// Message IDs of the last GET requests, used to recognise their replies.
uint16_t desiredRequestId = 0;
uint16_t commandsRequestId = 0;

bool actuatorOn = false;
long lastCommandId = 0;           // last command that was already run
unsigned long lastTelemetry = 0, lastPoll = 0, lastFirmwareReport = 0;
bool firmwareReportSent = false;

void setActuator(bool on) {
  actuatorOn = on;
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}

uint16_t postJson(const String& path, JsonDocument& doc) {
  String text;
  serializeJson(doc, text);
  Serial.printf("-> POST %s  %s\n", path.c_str(), text.c_str());
  // CON = confirmable: the library sends the message again if no ACK arrives.
  return coap.send(SERVER_IP, SERVER_PORT, path.c_str(), COAP_CON, COAP_POST, NULL, 0,
                   (uint8_t*)text.c_str(), text.length(), COAP_APPLICATION_JSON);
}

void sendTelemetry() {
  JsonDocument doc;
  doc["chip_temperature"] = temperatureRead();
  doc["rssi"] = WiFi.RSSI();
  doc[ACTUATOR_KEY] = actuatorOn ? 1 : 0;   // reported state
  postJson(pathTelemetry, doc);
}

// Firmware report: name, version, board and Wi-Fi MAC address.
void reportFirmware() {
  JsonDocument doc;
  doc["title"] = FW_TITLE;       // firmware name: name + version identify the firmware
  doc["version"] = FW_VERSION;
  doc["board"] = FW_BOARD;
  doc["mac"] = WiFi.macAddress();
  postJson(String("api/v1/") + ACCESS_TOKEN + "/firmware", doc);
}

void handleDesired(JsonDocument& desired) {
  // {"pump": 1}. Acts only when the requested state differs from the current one.
  if (desired[ACTUATOR_KEY].isNull()) return;
  bool wanted = desired[ACTUATOR_KEY].as<float>() >= 0.5;
  if (wanted == actuatorOn) return;
  setActuator(wanted);
  JsonDocument report;
  report[ACTUATOR_KEY] = actuatorOn ? 1 : 0;
  postJson(pathTelemetry, report);
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

void handleCommands(JsonDocument& data) {
  // {"commands": [{"id": 12, "method": "blink", "params": {"times": 5},
  //                "expires_at": 1762412345, "ttl": 297}, ...]}
  for (JsonObject command : data["commands"].as<JsonArray>()) {
    long id = command["id"] | 0;
    if (id <= lastCommandId) continue;      // already run before
    lastCommandId = id;
    const char* method = command["method"] | "";
    long late = secondsExpired(command);
    if (late > 0) {
      Serial.printf("   command %ld (%s) expired %ld s ago: not run\n", id, method, late);
      continue;                             // the platform has already marked it Expired
    }
    JsonDocument result;
    if (strcmp(method, "blink") == 0) {
      int times = command["params"]["times"] | 3;
      for (int i = 0; i < times; i++) {
        setActuator(!actuatorOn); delay(150);
        setActuator(!actuatorOn); delay(150);
      }
      result["success"] = true;
      result["blinked"] = times;
    } else if (strcmp(method, "ping") == 0) {
      result["success"] = true;
      result["uptime_ms"] = millis();
    } else {
      result["success"] = false;
      result["error"] = String("Unknown method: ") + method;
    }
    postJson(String("api/v1/") + ACCESS_TOKEN + "/commands/" + id + "/response", result);
  }
}

// Called by the library for every reply from the server.
void onResponse(CoapPacket& packet, IPAddress ip, int port) {
  String body;
  for (int i = 0; i < packet.payloadlen; i++) body += (char)packet.payload[i];
  int code = (packet.code >> 5) * 100 + (packet.code & 0x1F);  // 0x45 -> 205 (2.05)
  if (code >= 400) {
    Serial.printf("<- %d.%02d  %s\n", code / 100, code % 100, body.c_str());
    return;
  }
  JsonDocument doc;
  if (body.length() == 0 || deserializeJson(doc, body)) return;
  if (packet.messageid == desiredRequestId) handleDesired(doc);
  else if (packet.messageid == commandsRequestId) handleCommands(doc);
}

void connectWifi() {
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWi-Fi connected, IP %s\n", WiFi.localIP().toString().c_str());
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  setActuator(false);

  String base = String("api/v1/") + ACCESS_TOKEN;   // no "/" at the start
  pathTelemetry = base + "/telemetry";
  pathDesired   = base + "/desired";
  pathCommands  = base + "/commands";

  connectWifi();
  // Clock from the internet (UTC), used to skip commands that have expired.
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  Serial.printf("MAC address: %s\n", WiFi.macAddress().c_str());
  // SERVER_HOST may be an IP address or a domain name; both become an IPAddress.
  while (!WiFi.hostByName(SERVER_HOST, SERVER_IP)) {
    Serial.printf("Cannot resolve %s, retrying in 5 s\n", SERVER_HOST);
    delay(5000);
  }
  Serial.printf("CoAP server %s:%d\n", SERVER_IP.toString().c_str(), SERVER_PORT);
  coap.response(onResponse);
  coap.start();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  coap.loop();   // call often: receives replies and resends unacknowledged CON messages

  unsigned long t = millis();
  if (!firmwareReportSent || t - lastFirmwareReport >= FIRMWARE_REPORT_MS) {
    firmwareReportSent = true;
    lastFirmwareReport = t;
    reportFirmware();
  }
  if (t - lastTelemetry >= TELEMETRY_MS) {
    lastTelemetry = t;
    sendTelemetry();
  }
  if (t - lastPoll >= POLL_MS) {
    lastPoll = t;
    desiredRequestId = coap.get(SERVER_IP, SERVER_PORT, pathDesired.c_str());
    commandsRequestId = coap.get(SERVER_IP, SERVER_PORT, pathCommands.c_str());
  }
}
