/*
 * ESP32-C3 (with or without a small OLED) firmware template: two-way communication with
 * IoT Platform over MQTT, with optional TLS encryption and over-the-air (OTA) updates.
 * Made for ESP32-C3 boards with a 0.42" 72x40 SSD1306 OLED on I2C (SDA = GPIO5, SCL = GPIO6),
 * a BOOT button on GPIO9 and a blue LED on GPIO8. Set OLED to false for a board without a display.
 *
 * Everything the ESP32 MQTT template does
 *   - Sends telemetry every SEND_INTERVAL_MS: chip temperature, Wi-Fi signal strength and the
 *     state of the lamp (the on-board LED).
 *   - Follows the requested state of the actuator "lamp" and reports the new state back.
 *     The platform may send only the actuators that changed: keys that are missing are left alone.
 *   - Runs the commands "blink" {"times": 5} and "ping" and sends the result back.
 *     A command that has already expired ("expires_at", clock from NTP) is not run.
 *   - Announces "online" when it connects (and every 5 minutes); the broker announces "offline"
 *     for it when the connection is lost (MQTT Last Will).
 *   - Reports its firmware version, board and Wi-Fi MAC address every time it connects.
 *   - Installs firmware updates started from the device page (command "ota").
 *   - Confirms the JSON of a rule sent with "Publish to devices" on a rule page.
 *
 * Only with OLED true
 *   - Command "oled" {"x": 0, "y": 0, "msg": "Hello"}: shows the text on the display.
 *     x, y = top-left corner of the text in pixels (the display is 72 x 40); "\n" in msg starts
 *     a new line. Optional "ms": how long it stays (default OLED_MESSAGE_MS; 0 = until the button
 *     is pressed). Any button press closes the message.
 *   - A menu on the display, used with the BOOT button:
 *        press once          next menu item
 *        press and hold      open the item (in Mode: choose the highlighted mode)
 *        press twice         back
 *     Menu items:
 *        Display    the actuator values; "received ..." appears for a moment when data arrives
 *        Firmware   firmware version, board and MAC address
 *        Mode       Auto   = telemetry every SEND_INTERVAL_MS
 *                   Manual = telemetry only when you open "Telemetry"
 *                   (the choice is kept after a restart)
 *        Telemetry  sends telemetry once, then returns to Display
 *     The board starts in Display. Press twice there to see the menu.
 *
 * Before uploading
 *   1. On the platform: create the device and add a variable with key "lamp",
 *      kind "Actuator", value type "On / off".
 *   2. Fill in WIFI_SSID and WIFI_PASSWORD. Check MQTT_HOST, MQTT_PORT, USE_TLS,
 *      TOPIC_PREFIX and ACCESS_TOKEN: a template downloaded from the platform (Documentation >
 *      Connect an ESP32, device with board "ESP32-C3 OLED") already contains the right values.
 *   3. Set OLED (true / false). Other display: change the U8G2 line under "display".
 *   4. Arduino IDE, Tools: Board "ESP32C3 Dev Module", USB CDC On Boot "Enabled" (for the
 *      Serial Monitor over USB), Partition Scheme with OTA, e.g. "Default 4MB with spiffs".
 *
 * Updating this device over the air later
 *   1. Upload this sketch once over USB with FW_VERSION "1.0.0".
 *   2. Change FW_VERSION (e.g. "1.1.0"), then Sketch > Export Compiled Binary.
 *   3. Upload the .ino.bin file (NOT .ino.merged.bin) on the platform's Firmware page with the
 *      same name (FW_TITLE), version and board "ESP32-C3 OLED", then choose it under "Update firmware" on the device page.
 *
 * Libraries (Arduino IDE: Sketch > Include Library > Manage Libraries):
 * PubSubClient by Nick O'Leary, ArduinoJson 7 by Benoit Blanchon, and with OLED true also
 * U8g2 by oliver. WiFiClientSecure, HTTPClient, HTTPUpdate and Preferences come with the board
 * package "esp32" by Espressif Systems. Serial Monitor: 115200 baud.
 *
 * Documentation: open "Documentation" in the platform menu, chapters
 * "Connect an ESP32" and "Firmware updates".
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include <Preferences.h>

// ---- display ------------------------------------------------------------------------
// true  = the board has an OLED: command "oled" and the menu with the BOOT button.
// false = no display: the U8g2 library is not needed, telemetry is always automatic.
#define OLED true

#if OLED
#include <Wire.h>
#include <U8g2lib.h>
const int OLED_SDA = 5;
const int OLED_SCL = 6;
// 0.42" 72x40 OLED. Another display: pick its line from the U8g2 constructor list
// (https://github.com/olikraus/u8g2/wiki/u8g2setupcpp); the screens below are laid out for 72x40.
U8G2_SSD1306_72X40_ER_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);
const int BUTTON_PIN = 9;                          // BOOT button, LOW when pressed
const unsigned long OLED_MESSAGE_MS = 15000;       // command "oled": shown this long (0 = until a press)
#endif

// ---- this firmware ----------------------------------------------------------------
// FW_TITLE and FW_VERSION must be EXACTLY the name and version you enter when uploading the .bin
// file to the platform (capital letters in the name do not matter). The name tells programs apart:
// change it when this sketch becomes a different program, e.g. "Greenhouse node".
#define FW_TITLE   "ESP32-C3 OLED node"
#define FW_VERSION "1.0.0"
#define FW_BOARD   "esp32c3_oled"   // board "ESP32-C3 OLED" on the platform

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

const int LED_PIN = 8;                 // blue on-board LED of the ESP32-C3 OLED board
const bool LED_ACTIVE_LOW = true;      // this LED lights when the pin is LOW
const unsigned long SEND_INTERVAL_MS = 10000;   // telemetry interval in Auto mode
// "online" is repeated on the status topic as a heartbeat. The platform marks a device whose MQTT
// session is open as offline after 15 minutes without any message (DEVICE_MQTT_SILENT_SECONDS),
// in case the broker's Last Will never arrives.
const unsigned long STATUS_EVERY_MS = 5UL * 60 * 1000;
unsigned long lastStatus = 0;

#if USE_TLS
WiFiClientSecure network;
#else
WiFiClient network;
#endif
PubSubClient mqtt(network);
Preferences prefs;

String topicTelemetry, topicDesired, topicCommands, topicErrors, topicFirmware, topicStatus, topicRules;
bool lampOn = false;
unsigned long lastSend = 0;
bool autoTelemetry = true;      // Mode: true = Auto, false = Manual (saved in flash, see setup)

// An accepted update waits to be run in loop(), not inside the MQTT callback,
// because the download takes tens of seconds.
long otaUpdateId = 0;           // 0 = none
long lastOtaUpdateId = 0;       // a command can arrive twice (QoS 1): run it only once
String otaUrl;
int otaLastPercent = -1;

// Screen functions used by the MQTT part. Without an OLED they do nothing.
void showNotice(const String& text, const String& label = "received:");
void showStatus(const String& line1, const String& line2 = "");
void showOtaProgress(int percent);

void publishJson(const String& topic, JsonDocument& doc) {
  String text;
  serializeJson(doc, text);
  mqtt.publish(topic.c_str(), text.c_str());
  Serial.printf("-> %s  %s\n", topic.c_str(), text.c_str());
}

// ---- firmware report -----------------------------------------------------------------

void reportFirmwareVersion() {
  JsonDocument doc;
  doc["title"] = FW_TITLE;       // firmware name: name + version identify the firmware
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
    showOtaProgress(percent);
  }
}

void runOta() {
  Serial.printf("OTA: downloading %s\n", otaUrl.c_str());
  otaLastPercent = -1;
  reportOtaState("DOWNLOADING", 0);
  showOtaProgress(0);

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
    showStatus("Restarting", "new firmware");
    ESP.restart();
  } else {
    String error = result == HTTP_UPDATE_NO_UPDATES ? String("Server sent no update")
                                                    : httpUpdate.getLastErrorString();
    Serial.printf("OTA failed: %s\n", error.c_str());
    reportOtaState("FAILED", -1, error);
    showNotice("failed", "update:");
  }
  otaUpdateId = 0;
}

// ---- requested state & commands -----------------------------------------------------------

void setLamp(bool on) {
  lampOn = on;
  digitalWrite(LED_PIN, (on != LED_ACTIVE_LOW) ? HIGH : LOW);
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
    showNotice(String("lamp ") + (lampOn ? "ON" : "OFF"));
  }
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

#if OLED
void showMessage(int x, int y, const String& msg, unsigned long ms);
#endif

void handleCommand(const String& id, JsonDocument& command) {
  const char* method = command["method"] | "";
  long late = secondsExpired(command);
  if (late > 0) {
    // Not run and not answered: the platform has already marked the command Expired.
    Serial.printf("   command %s (%s) expired %ld s ago: not run\n", id.c_str(), method, late);
    return;
  }
  JsonDocument result;
  if (strcmp(method, "ota") == 0) {
    long update = command["params"]["update"] | 0L;
    const char* version = command["params"]["version"] | "";
    const char* title = command["params"]["title"] | "";
    if (update == lastOtaUpdateId) return;            // sent again: already handled
    // Same name AND version = already running. Same version of another program is a real update.
    if (strcmp(version, FW_VERSION) == 0 && strcasecmp(title, FW_TITLE) == 0) {
      result["success"] = false;
      result["error"] = String("Already running ") + FW_TITLE + " " + FW_VERSION;
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
    showNotice("cmd blink");
    int times = command["params"]["times"] | 3;
    for (int i = 0; i < times; i++) {
      setLamp(!lampOn); delay(150);
      setLamp(!lampOn); delay(150);
    }
    result["success"] = true;
    result["blinked"] = times;
  } else if (strcmp(method, "ping") == 0) {
    showNotice("cmd ping");
    result["success"] = true;
    result["uptime_ms"] = millis();
    result["version"] = FW_VERSION;
  } else if (strcmp(method, "oled") == 0) {
#if OLED
    // {"x": 0, "y": 0, "msg": "Hello"}: x, y = top-left corner of the text (pixels).
    JsonVariant params = command["params"];
    if (params["msg"].isNull()) {
      result["success"] = false;
      result["error"] = "Missing \"msg\"";
    } else {
      int x = params["x"] | 0;
      int y = params["y"] | 0;
      unsigned long ms = params["ms"] | OLED_MESSAGE_MS;
      String msg;
      if (params["msg"].is<const char*>()) msg = params["msg"].as<const char*>();
      else serializeJson(params["msg"], msg);           // a number or other value: shown as JSON
      showMessage(x, y, msg, ms);
      result["success"] = true;
      result["shown"] = msg;
    }
#else
    result["success"] = false;
    result["error"] = "This board has no OLED (OLED is false in the firmware)";
#endif
  } else {
    result["success"] = false;
    result["error"] = String("Unknown method: ") + method;
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
  if (t.startsWith(topicRules)) {          // may be large: do not print the whole JSON
    Serial.printf("<- %s  (%u bytes)\n", topic, length);
    showNotice("rule " + t.substring(topicRules.length()));
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
    showNotice("error");
  }
}

// ---- display and BOOT button (OLED true) ----------------------------------------------------
#if OLED

// Fonts: SMALL 5x8 (14 characters per line), TEXT 6x10 (12), BIG 7x13 bold (10).
#define FONT_SMALL u8g2_font_5x8_tr
#define FONT_TEXT  u8g2_font_6x10_tr
#define FONT_BIG   u8g2_font_7x13B_tr

enum Screen { SCR_MENU, SCR_DISPLAY, SCR_FIRMWARE, SCR_MODE, SCR_MESSAGE, SCR_STATUS };
const char* MENU_ITEMS[] = {"Display", "Firmware", "Mode", "Telemetry"};
const int MENU_COUNT = 4;

Screen screen = SCR_DISPLAY;      // the board starts on Display
Screen beforeMessage = SCR_DISPLAY;
int menuIndex = 0;                // highlighted menu item
bool modeChoiceAuto = true;       // highlighted choice inside Mode
String noticeLabel, noticeText;   // e.g. "received:" + "lamp ON", shown on Display
unsigned long noticeUntil = 0;
const unsigned long NOTICE_MS = 2000;
String messageText;               // command "oled"
int messageX = 0, messageY = 0;
unsigned long messageUntil = 0;   // 0 = until a button press
String statusLine1, statusLine2;  // Wi-Fi / MQTT / update progress
bool redraw = true;
unsigned long lastDraw = 0;

void drawCentered(const char* text, int y) {
  int w = u8g2.getStrWidth(text);
  u8g2.drawStr(max(0, (u8g2.getDisplayWidth() - w) / 2), y, text);
}

void drawMenu() {
  u8g2.setFont(FONT_SMALL);
  char head[16];
  snprintf(head, sizeof head, "MENU     %d/%d", menuIndex + 1, MENU_COUNT);
  u8g2.drawStr(0, 0, head);
  u8g2.setFont(FONT_BIG);
  u8g2.drawBox(0, 10, u8g2.getDisplayWidth(), 15);
  u8g2.setDrawColor(0);
  drawCentered(MENU_ITEMS[menuIndex], 11);
  u8g2.setDrawColor(1);
  u8g2.setFont(FONT_SMALL);
  u8g2.drawStr(0, 31, "hold = open");   // 72 px: 14 small characters
}

void drawDisplay() {
  u8g2.setFont(FONT_SMALL);
  String top = String(mqtt.connected() ? "online" : "offline") + (autoTelemetry ? "  AUTO" : "  MAN");
  u8g2.drawStr(0, 0, top.c_str());
  u8g2.drawHLine(0, 9, u8g2.getDisplayWidth());
  if (millis() < noticeUntil) {
    // Data has just arrived: say what, then return to the actuator values.
    u8g2.setFont(FONT_SMALL);
    u8g2.drawStr(0, 13, noticeLabel.c_str());
    u8g2.setFont(FONT_TEXT);
    u8g2.drawStr(0, 24, noticeText.substring(0, 12).c_str());
    return;
  }
  // Actuator values. Add a line here for every actuator you add to this firmware.
  u8g2.setFont(FONT_TEXT);
  u8g2.drawStr(0, 16, "Lamp");
  u8g2.setFont(FONT_BIG);
  const char* state = lampOn ? "ON" : "OFF";
  u8g2.drawStr(u8g2.getDisplayWidth() - u8g2.getStrWidth(state), 14, state);
}

void drawFirmware() {
  u8g2.setFont(FONT_SMALL);
  u8g2.drawStr(0, 0, "FIRMWARE");
  u8g2.setFont(FONT_TEXT);
  u8g2.drawStr(0, 10, "v" FW_VERSION);
  u8g2.setFont(FONT_SMALL);
  u8g2.drawStr(0, 22, FW_BOARD);
  String mac = WiFi.macAddress();            // AA:BB:CC:DD:EE:FF -> ..DD:EE:FF
  u8g2.drawStr(0, 31, (".." + mac.substring(9)).c_str());
}

void drawMode() {
  u8g2.setFont(FONT_SMALL);
  u8g2.drawStr(0, 0, "MODE  hold=set");
  u8g2.setFont(FONT_TEXT);
  char line[16];
  snprintf(line, sizeof line, "%c Auto %lus", modeChoiceAuto ? '>' : ' ', SEND_INTERVAL_MS / 1000);
  u8g2.drawStr(0, 11, line);
  snprintf(line, sizeof line, "%c Manual", modeChoiceAuto ? ' ' : '>');
  u8g2.drawStr(0, 23, line);
  // * = the mode in use now
  u8g2.drawStr(u8g2.getDisplayWidth() - 6, autoTelemetry ? 11 : 23, "*");
}

void drawMessage() {
  u8g2.setFont(FONT_TEXT);
  int y = messageY, start = 0;
  while (start <= (int)messageText.length()) {
    int end = messageText.indexOf('\n', start);
    if (end < 0) end = messageText.length();
    u8g2.drawStr(messageX, y, messageText.substring(start, end).c_str());
    y += 10;
    start = end + 1;
  }
}

void drawStatus() {
  u8g2.setFont(FONT_TEXT);
  u8g2.drawStr(0, 8, statusLine1.c_str());
  u8g2.setFont(FONT_SMALL);
  u8g2.drawStr(0, 22, statusLine2.c_str());
}

void drawScreen() {
  u8g2.clearBuffer();
  u8g2.setFontPosTop();             // y = top of the text
  switch (screen) {
    case SCR_MENU:     drawMenu(); break;
    case SCR_DISPLAY:  drawDisplay(); break;
    case SCR_FIRMWARE: drawFirmware(); break;
    case SCR_MODE:     drawMode(); break;
    case SCR_MESSAGE:  drawMessage(); break;
    case SCR_STATUS:   drawStatus(); break;
  }
  u8g2.sendBuffer();
  lastDraw = millis();
  redraw = false;
}

void showNotice(const String& text, const String& label) {
  noticeLabel = label;
  noticeText = text;
  noticeUntil = millis() + NOTICE_MS;
  redraw = true;
}

// Used while the board waits (Wi-Fi, MQTT, firmware update): drawn at once.
void showStatus(const String& line1, const String& line2) {
  statusLine1 = line1;
  statusLine2 = line2;
  screen = SCR_STATUS;
  drawScreen();
}

void showOtaProgress(int percent) {
  showStatus("Updating", String(percent) + "% keep on");
}

void showMessage(int x, int y, const String& msg, unsigned long ms) {
  if (screen != SCR_MESSAGE) beforeMessage = screen;
  messageText = msg;
  messageX = x;
  messageY = y;
  messageUntil = ms ? millis() + ms : 0;
  screen = SCR_MESSAGE;
  redraw = true;
}

void setMode(bool automatic) {
  autoTelemetry = automatic;
  prefs.putBool("auto", automatic);         // kept after a restart
  lastSend = millis();
  Serial.printf("Mode: %s\n", automatic ? "auto" : "manual");
}

// ---- BOOT button: one press, two presses, press and hold ----
// int, not an enum type: Arduino IDE puts function prototypes at the top of the sketch.
const int PRESS_NONE = 0, PRESS_ONCE = 1, PRESS_TWICE = 2, PRESS_HOLD = 3;
const unsigned long DEBOUNCE_MS = 30;
const unsigned long HOLD_MS = 700;        // held at least this long = "press and hold"
const unsigned long DOUBLE_MS = 350;      // second press within this time = "press twice"

bool buttonDown = false;
unsigned long buttonChanged = 0, pressedAt = 0, releasedAt = 0;
bool holdReported = false;
int clicks = 0;

int readButton() {
  bool down = digitalRead(BUTTON_PIN) == LOW;
  unsigned long now = millis();
  if (down != buttonDown && now - buttonChanged >= DEBOUNCE_MS) {
    buttonDown = down;
    buttonChanged = now;
    if (down) {
      pressedAt = now;
      holdReported = false;
    } else if (!holdReported) {
      clicks++;
      releasedAt = now;
      if (clicks >= 2) { clicks = 0; return PRESS_TWICE; }
    }
  }
  if (buttonDown && !holdReported && now - pressedAt >= HOLD_MS) {
    holdReported = true;
    clicks = 0;
    return PRESS_HOLD;
  }
  if (!buttonDown && clicks == 1 && now - releasedAt > DOUBLE_MS) {
    clicks = 0;
    return PRESS_ONCE;
  }
  return PRESS_NONE;
}

void openMenuItem() {
  switch (menuIndex) {
    case 0: screen = SCR_DISPLAY; break;
    case 1: screen = SCR_FIRMWARE; break;
    case 2: screen = SCR_MODE; modeChoiceAuto = autoTelemetry; break;
    case 3:                                   // Telemetry: send once, then Display
      screen = SCR_DISPLAY;
      if (mqtt.connected()) {
        sendTelemetry();
        lastSend = millis();
        showNotice("sent", "telemetry:");
      } else {
        showNotice("no MQTT", "telemetry:");
      }
      break;
  }
}

void handleButton() {
  int press = readButton();
  if (press == PRESS_NONE) return;
  redraw = true;
  if (screen == SCR_MESSAGE) {               // any press closes a message from the platform
    screen = beforeMessage;
    return;
  }
  switch (screen) {
    case SCR_MENU:
      if (press == PRESS_ONCE) menuIndex = (menuIndex + 1) % MENU_COUNT;
      else if (press == PRESS_HOLD) openMenuItem();
      else if (press == PRESS_TWICE) screen = SCR_DISPLAY;
      break;
    case SCR_MODE:
      if (press == PRESS_ONCE) modeChoiceAuto = !modeChoiceAuto;
      else if (press == PRESS_HOLD) { setMode(modeChoiceAuto); screen = SCR_MENU; }
      else if (press == PRESS_TWICE) screen = SCR_MENU;   // back without a change
      break;
    default:                                  // Display, Firmware: press twice = back to the menu
      if (press == PRESS_TWICE) {
        menuIndex = screen == SCR_FIRMWARE ? 1 : 0;
        screen = SCR_MENU;
      }
      break;
  }
}

void setupOled() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Wire.begin(OLED_SDA, OLED_SCL);
  u8g2.begin();
  u8g2.setBusClock(400000);
  autoTelemetry = prefs.getBool("auto", true);
  showStatus("IoT " FW_VERSION, "starting...");
}

void loopOled() {
  handleButton();
  if (screen == SCR_STATUS && mqtt.connected()) { screen = SCR_DISPLAY; redraw = true; }
  if (screen == SCR_MESSAGE && messageUntil && millis() > messageUntil) {
    screen = beforeMessage;
    redraw = true;
  }
  // Redraw on change, and every half second for "online" and the end of a notice.
  if (redraw || millis() - lastDraw >= 500) drawScreen();
}

#else   // no OLED: the screen functions do nothing
void showNotice(const String&, const String&) {}
void showStatus(const String&, const String&) {}
void showOtaProgress(int) {}
#endif

// ---- connection ----------------------------------------------------------------------------

void connectWifi() {
  Serial.printf("Connecting to %s", WIFI_SSID);
  showStatus("Wi-Fi...", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWi-Fi connected, IP %s\n", WiFi.localIP().toString().c_str());
}

void connectMqtt() {
  while (!mqtt.connected()) {
    String clientId = "esp32c3-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.printf("Connecting to MQTT %s:%u as %s ... ", MQTT_HOST, MQTT_PORT, clientId.c_str());
    showStatus("MQTT...", MQTT_HOST);
    // Username = password = access token. The platform's broker only lets this device
    // use topics that contain its own token; brokers without accounts ignore the login.
    // Last Will: if the connection is lost without a goodbye (power cut, Wi-Fi gone),
    // the broker publishes "offline" (retained) to the status topic for this device.
    if (mqtt.connect(clientId.c_str(), ACCESS_TOKEN, ACCESS_TOKEN,
                     topicStatus.c_str(), 1, true, "offline")) {
      Serial.println("ok");
      // Subscribe after EVERY connection. The broker keeps (retains) the requested state,
      // so it arrives right after subscribing and the lamp returns to the requested state.
      mqtt.subscribe(topicDesired.c_str(), 1);
      mqtt.subscribe((topicCommands + "+").c_str(), 1);
      mqtt.subscribe(topicErrors.c_str(), 0);
      mqtt.subscribe((topicRules + "+").c_str(), 1);   // rule JSON (retained)
      // "online" AFTER subscribing: the platform sends waiting commands as soon as the device
      // is online, so the subscription to commands must already be in place.
      mqtt.publish(topicStatus.c_str(), "online", true);   // retained: the session has started
      lastStatus = millis();
      // On every connection (including the restart after an update): report the version.
      reportFirmwareVersion();
    } else {
      Serial.printf("failed (state %d), retrying in 5 s\n", mqtt.state());
      showStatus("MQTT failed", "retry in 5 s");
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial.printf("\nFirmware %s %s (%s)\n", FW_TITLE, FW_VERSION, FW_BOARD);
  pinMode(LED_PIN, OUTPUT);
  setLamp(false);
  prefs.begin("iot", false);        // flash storage for the telemetry mode
#if OLED
  setupOled();
#endif

  String base = String(TOPIC_PREFIX) + "/" + ACCESS_TOKEN;
  topicTelemetry = base + "/telemetry";
  topicDesired   = base + "/desired";
  topicCommands  = base + "/commands/";
  topicErrors    = base + "/errors";
  topicFirmware  = base + "/firmware";
  topicStatus    = base + "/status";
  topicRules     = base + "/rules/";

  connectWifi();
  // Clock from the internet (UTC), used to skip commands that have expired.
  configTime(0, 0, "pool.ntp.org", "time.google.com");
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
  // Auto mode: telemetry every SEND_INTERVAL_MS. Manual mode (OLED menu): only from "Telemetry".
  if (autoTelemetry && millis() - lastSend >= SEND_INTERVAL_MS) {
    lastSend = millis();
    sendTelemetry();
  }
  if (millis() - lastStatus >= STATUS_EVERY_MS) {
    lastStatus = millis();
    mqtt.publish(topicStatus.c_str(), "online", true);   // heartbeat
  }
#if OLED
  loopOled();
#endif
}
