/*
 * Contoh firmware ESP32: komunikasi dua arah dengan platform lewat CoAP (UDP).
 *
 *   - POST telemetri setiap 10 detik: suhu chip, sinyal Wi-Fi, keadaan aktuator.
 *     Kiriman berkala ini juga menjaga jalur UDP di router tetap terbuka.
 *   - GET desired state setiap 2 detik -> LED bawaan menyala/padam -> lapor balik.
 *   - GET daftar command setiap 2 detik -> jalankan "blink"/"ping" -> POST hasilnya.
 *
 * Mengapa GET berkala, bukan Observe? Pustaka Arduino di bawah tidak mendukung
 * Observe sepenuhnya. Observe tetap bisa dicoba dengan scripts/fake_coap_device.py
 * atau `coap-client -s`. Lihat docs/tahap-06-coap.md.
 *
 * Persiapan:
 *   1. Dashboard: buat device, salin access token, tambahkan variabel aktuator
 *      dengan key sama dengan ACTUATOR_KEY (kind "Actuator", "On / off").
 *   2. Mac: python manage.py coap_server   (izinkan koneksi masuk bila ditanya macOS)
 *   3. ESP32 dan Mac harus SATU jaringan: CoAP tidak memakai broker.
 *
 * Pustaka (Arduino IDE > Library Manager):
 *   - "CoAP simple library" oleh Hirotaka Niisato
 *   - ArduinoJson oleh Benoit Blanchon (versi 7)
 * Board: ESP32. Contoh ini belum diuji di semua versi pustaka; periksa Serial
 * Monitor (115200) bila ada balasan yang tidak sesuai.
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include <coap-simple.h>
#include <ArduinoJson.h>

// ---- isi bagian ini ---------------------------------------------------------
const char* WIFI_SSID     = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";
IPAddress   SERVER_IP(192, 168, 1, 10);   // alamat Mac di Wi-Fi (COAP_PUBLIC_HOST)
const int   SERVER_PORT   = 5683;
const char* ACCESS_TOKEN  = "paste-the-device-access-token-here";
const char* ACTUATOR_KEY  = "pump";        // key aktuator yang dikendalikan LED
// -----------------------------------------------------------------------------

const int LED_PIN = 2;
const unsigned long TELEMETRY_MS = 10000;
const unsigned long POLL_MS = 2000;

WiFiUDP udp;
Coap coap(udp);

String pathTelemetry;   // api/v1/<token>/telemetry
String pathDesired;     // api/v1/<token>/desired
String pathCommands;    // api/v1/<token>/commands

// Nomor pesan (message ID) permintaan GET terakhir, untuk mengenali balasannya.
uint16_t desiredRequestId = 0;
uint16_t commandsRequestId = 0;

bool actuatorOn = false;
long lastCommandId = 0;           // command terakhir yang sudah dijalankan
unsigned long lastTelemetry = 0, lastPoll = 0;

void setActuator(bool on) {
  actuatorOn = on;
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}

uint16_t postJson(const String& path, JsonDocument& doc) {
  String text;
  serializeJson(doc, text);
  Serial.printf("-> POST %s  %s\n", path.c_str(), text.c_str());
  // CON = confirmable: pustaka mengulang kiriman bila tidak ada ACK.
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

void handleDesired(JsonDocument& desired) {
  // {"pump": 1}. Hanya bertindak bila keadaan yang diminta berbeda.
  if (desired[ACTUATOR_KEY].isNull()) return;
  bool wanted = desired[ACTUATOR_KEY].as<float>() >= 0.5;
  if (wanted == actuatorOn) return;
  setActuator(wanted);
  JsonDocument report;
  report[ACTUATOR_KEY] = actuatorOn ? 1 : 0;
  postJson(pathTelemetry, report);
}

void handleCommands(JsonDocument& data) {
  // {"commands": [{"id": 12, "method": "blink", "params": {"times": 5}}, ...]}
  for (JsonObject command : data["commands"].as<JsonArray>()) {
    long id = command["id"] | 0;
    if (id <= lastCommandId) continue;      // sudah dijalankan sebelumnya
    lastCommandId = id;
    const char* method = command["method"] | "";
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

// Dipanggil pustaka untuk setiap balasan dari server.
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

  String base = String("api/v1/") + ACCESS_TOKEN;   // tanpa "/" di depan
  pathTelemetry = base + "/telemetry";
  pathDesired   = base + "/desired";
  pathCommands  = base + "/commands";

  connectWifi();
  coap.response(onResponse);
  coap.start();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  coap.loop();   // WAJIB sering dipanggil: menerima balasan dan mengulang kiriman CON

  unsigned long t = millis();
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
