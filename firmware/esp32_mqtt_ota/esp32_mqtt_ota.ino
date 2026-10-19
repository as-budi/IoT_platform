/*
 * Contoh firmware ESP32 dengan pembaruan over-the-air (OTA), tahap 9.
 * Dasarnya sama dengan firmware/esp32_mqtt (telemetri, desired state "lamp",
 * command "blink"/"ping"), ditambah:
 *
 *   - Saat tersambung, melaporkan versi firmware-nya ke <prefix>/<token>/firmware.
 *   - Menerima command "ota":
 *       {"id": 12, "method": "ota", "params": {"update": 7, "version": "1.1.0",
 *        "url": "http://192.168.1.10:8000/api/v1/ota/<token-unduhan>.bin",
 *        "md5": "...", "size": 912345}}
 *   - Mengunduh file lewat HTTP dengan pustaka HTTPUpdate (bawaan core ESP32).
 *     HTTPUpdate menulis ke partisi OTA CADANGAN dan memeriksa MD5 dari header
 *     x-MD5. Firmware lama tetap utuh sampai semuanya berhasil.
 *   - Melaporkan setiap langkah: DOWNLOADING (dengan %), DOWNLOADED, VERIFIED,
 *     UPDATING, lalu restart. Setelah menyala, firmware BARU melaporkan versinya;
 *     laporan itulah yang membuat platform menandai pembaruan UPDATED.
 *
 * Cara mencoba (lihat docs/tahap-09-ota.md):
 *   1. Isi pengaturan di bawah, FW_VERSION = "1.0.0", lalu upload lewat kabel USB
 *      SEKALI. Pastikan Tools > Partition Scheme mendukung OTA
 *      (mis. "Default 4MB with spiffs"): harus ada dua partisi aplikasi.
 *   2. Ubah FW_VERSION menjadi "1.1.0" (dan ubah perilaku apa saja, mis. LED_PIN
 *      atau interval), lalu Sketch > Export Compiled Binary. Upload file
 *      .bin-nya di halaman Firmware dengan versi 1.1.0, board ESP32.
 *   3. Di halaman device, pilih 1.1.0 dan klik "Update firmware".
 *
 * Penting: URL unduhan harus bisa dijangkau ESP32. Jalankan server dengan
 *   python manage.py runall 0.0.0.0:8000
 * dan isi OTA_BASE_URL=http://<IP-Wi-Fi-komputer>:8000 di .env.
 *
 * Pustaka (Library Manager): PubSubClient (Nick O'Leary), ArduinoJson 7.
 * HTTPClient dan HTTPUpdate sudah termasuk paket board "esp32" by Espressif.
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>

// ---- versi firmware INI ---------------------------------------------------------
// Harus SAMA PERSIS dengan versi yang diisi saat mengunggah .bin di platform.
#define FW_VERSION "1.0.0"
#define FW_BOARD   "esp32"   // esp32, esp32s2, esp32s3, esp32c3 (lihat devices.models.Board)

// ---- isi bagian ini ---------------------------------------------------------------
const char* WIFI_SSID     = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";
const char* MQTT_HOST     = "broker.emqx.io";
const uint16_t MQTT_PORT  = 1883;
const char* TOPIC_PREFIX  = "paste-MQTT_TOPIC_PREFIX-here";
const char* ACCESS_TOKEN  = "paste-the-device-access-token-here";

// ---- server produksi (docs/deploy-lightsail.md) -------------------------------------
// USE_TLS 1: MQTT terenkripsi ke broker Mosquitto milik server (port 8883), dan unduhan
// OTA lewat https://. Isi juga MQTT_HOST = domain server, MQTT_PORT = 8883, dan ROOT_CA.
// USE_TLS 0: seperti sebelumnya (broker publik/lokal tanpa enkripsi, port 1883).
#define USE_TLS 0

// Sertifikat akar (root CA) yang menandatangani sertifikat Let's Encrypt server.
// Salin isinya dari https://letsencrypt.org/certificates/ (bagian "Root Certificates",
// ISRG Root X1, format PEM), termasuk baris BEGIN/END. Dengan ini ESP32 bisa
// memastikan ia benar-benar berbicara dengan server Anda, bukan penyamar.
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

String topicTelemetry, topicDesired, topicCommands, topicErrors, topicFirmware, topicStatus;
bool lampOn = false;
unsigned long lastSend = 0;

// Pembaruan yang menunggu dijalankan di loop() (bukan di dalam callback MQTT,
// karena unduhan memakan waktu puluhan detik).
long otaUpdateId = 0;           // 0 = tidak ada
long lastOtaUpdateId = 0;       // command bisa terkirim ulang (QoS 1): jalankan sekali saja
String otaUrl;
int otaLastPercent = -1;

void publishJson(const String& topic, JsonDocument& doc) {
  String text;
  serializeJson(doc, text);
  mqtt.publish(topic.c_str(), text.c_str());
  Serial.printf("-> %s  %s\n", topic.c_str(), text.c_str());
}

// ---- laporan firmware ----------------------------------------------------------------

void reportFirmwareVersion() {
  JsonDocument doc;
  doc["version"] = FW_VERSION;
  doc["board"] = FW_BOARD;
  publishJson(topicFirmware, doc);
}

void reportOtaState(const char* state, int progress = -1, const String& error = "") {
  JsonDocument doc;
  doc["update"] = otaUpdateId;
  doc["state"] = state;
  if (progress >= 0) doc["progress"] = progress;
  if (error.length()) doc["error"] = error;
  publishJson(topicFirmware, doc);
  mqtt.loop();  // kirim sekarang, jangan menunggu putaran loop() berikutnya
}

// Dipanggil HTTPUpdate selama mengunduh. Lapor setiap kenaikan 10 %.
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

  httpUpdate.rebootOnUpdate(false); // restart sendiri, SETELAH melapor
  httpUpdate.onProgress(onOtaProgress);
  // update() mengunduh, menulis ke partisi cadangan, dan memeriksa MD5 (header x-MD5).
  // Bila gagal di tengah jalan, partisi aktif (firmware lama) tidak tersentuh.
  t_httpUpdate_return result;
  if (otaUrl.startsWith("https://")) {
    WiFiClientSecure https;
    https.setCACert(ROOT_CA);       // file firmware hanya diterima dari server yang sah
    result = httpUpdate.update(https, otaUrl);
  } else {
    WiFiClient http;
    result = httpUpdate.update(http, otaUrl);
  }

  if (result == HTTP_UPDATE_OK) {
    reportOtaState("DOWNLOADED");
    reportOtaState("VERIFIED");       // MD5 cocok, image sudah ditulis dan ditandai untuk boot
    reportOtaState("UPDATING");
    for (int i = 0; i < 10; i++) { mqtt.loop(); delay(50); }  // pastikan laporan terkirim
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

// ---- desired state & command (sama dengan esp32_mqtt) -----------------------------------

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
    if (update == lastOtaUpdateId) return;            // kiriman ulang: sudah ditangani
    if (strcmp(version, FW_VERSION) == 0) {
      result["success"] = false;
      result["error"] = String("Already running ") + FW_VERSION;
    } else if (otaUpdateId != 0) {
      result["success"] = false;
      result["error"] = "Another update is running";
    } else {
      // Terima dulu; unduhan dijalankan di loop() setelah jawaban ini terkirim.
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

void onMessage(char* topic, byte* payload, unsigned int length) {
  String t(topic);
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

// ---- koneksi --------------------------------------------------------------------------------

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
    // Broker milik server menolak klien tanpa akun. Username = password = access token;
    // ACL broker hanya mengizinkan topik yang memuat token ini (manage.py mqtt_accounts).
    // Broker publik (broker.emqx.io) mengabaikan akun ini, jadi aman dipakai di keduanya.
    // Last Will (tahap 16): bila koneksi putus tanpa pamit (listrik mati, Wi-Fi hilang),
    // broker mengirim "offline" (retained) ke topik status atas nama device ini.
    if (mqtt.connect(clientId.c_str(), ACCESS_TOKEN, ACCESS_TOKEN,
                     topicStatus.c_str(), 1, true, "offline")) {
      Serial.println("ok");
      mqtt.publish(topicStatus.c_str(), "online", true);   // retained: sesi dimulai
      mqtt.subscribe(topicDesired.c_str(), 1);
      mqtt.subscribe((topicCommands + "+").c_str(), 1);
      mqtt.subscribe(topicErrors.c_str(), 0);
      // Setiap kali tersambung (termasuk setelah restart karena OTA): laporkan versi.
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

  connectWifi();
#if USE_TLS
  network.setCACert(ROOT_CA);
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(1024);  // command ota memuat URL panjang
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  if (otaUpdateId != 0) {
    runOta();
    return;
  }
  if (millis() - lastSend >= SEND_INTERVAL_MS) {
    lastSend = millis();
    sendTelemetry();
  }
}
