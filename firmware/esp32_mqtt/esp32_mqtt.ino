/*
 * Contoh firmware ESP32: komunikasi dua arah dengan platform lewat MQTT.
 *
 *   - Mengirim telemetri setiap 10 detik: suhu chip, kekuatan sinyal Wi-Fi,
 *     dan keadaan lampu (LED bawaan board).
 *   - Menerima desired state "lamp" -> LED menyala/padam -> melapor balik.
 *   - Menerima command: "blink" {"times": 5} dan "ping" -> mengirim hasilnya.
 *
 * Persiapan di dashboard:
 *   1. Buat device, salin access token-nya (bagian "Connect a device").
 *   2. Tambahkan variabel key "lamp", kind "Actuator", value type "On / off".
 *   3. Jalankan:  python manage.py mqtt_bridge
 *
 * Pustaka (Arduino IDE > Library Manager):
 *   - PubSubClient  oleh Nick O'Leary
 *   - ArduinoJson   oleh Benoit Blanchon (versi 7)
 * Board: ESP32 (paket "esp32" by Espressif).
 *
 * Berbeda dengan versi HTTP, ESP32 tidak perlu tahu alamat komputer Anda:
 * ESP32 dan Django sama-sama tersambung ke broker di internet.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ---- isi bagian ini ---------------------------------------------------------
const char* WIFI_SSID     = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";

// Harus SAMA dengan MQTT_HOST, MQTT_PORT, dan MQTT_TOPIC_PREFIX di file .env.
const char* MQTT_HOST     = "broker.emqx.io";
const uint16_t MQTT_PORT  = 1883;
const char* TOPIC_PREFIX  = "paste-MQTT_TOPIC_PREFIX-here";
const char* ACCESS_TOKEN  = "paste-the-device-access-token-here";
// -----------------------------------------------------------------------------

const int LED_PIN = 2;                          // LED bawaan pada banyak board ESP32
const unsigned long SEND_INTERVAL_MS = 10000;

WiFiClient network;
PubSubClient mqtt(network);

String topicTelemetry;  // <prefix>/<token>/telemetry
String topicDesired;    // <prefix>/<token>/desired
String topicCommands;   // <prefix>/<token>/commands/   (+ nomor command)
String topicErrors;     // <prefix>/<token>/errors

bool lampOn = false;
unsigned long lastSend = 0;

void publishJson(const String& topic, JsonDocument& doc) {
  String text;
  serializeJson(doc, text);
  mqtt.publish(topic.c_str(), text.c_str());
  Serial.printf("-> %s  %s\n", topic.c_str(), text.c_str());
}

void setLamp(bool on) {
  lampOn = on;
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}

// Reported state: keadaan lampu yang SEBENARNYA, dikirim sebagai telemetri biasa.
void reportLamp() {
  JsonDocument doc;
  doc["lamp"] = lampOn ? 1 : 0;
  publishJson(topicTelemetry, doc);
}

void sendTelemetry() {
  JsonDocument doc;
  doc["chip_temperature"] = temperatureRead();  // sensor suhu internal ESP32 (°C, perkiraan)
  doc["rssi"] = WiFi.RSSI();                    // kekuatan sinyal Wi-Fi (dBm)
  doc["lamp"] = lampOn ? 1 : 0;
  publishJson(topicTelemetry, doc);
}

void handleDesired(JsonDocument& desired) {
  // Pola 1: {"lamp": 1}. Key lain (aktuator yang tidak dimiliki board ini) diabaikan.
  if (!desired["lamp"].isNull()) {
    setLamp(desired["lamp"].as<float>() >= 0.5);
    reportLamp();
  }
}

void handleCommand(const String& id, JsonDocument& command) {
  // Pola 2: {"id": 12, "method": "blink", "params": {"times": 5}}
  const char* method = command["method"] | "";
  JsonDocument result;
  if (strcmp(method, "blink") == 0) {
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
  } else {
    result["success"] = false;
    result["error"] = String("Unknown method: ") + method;
  }
  publishJson(topicCommands + id + "/response", result);
}

// Dipanggil PubSubClient untuk setiap pesan dari topik yang di-subscribe.
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
    // Client ID harus unik di broker; dibuat dari alamat MAC chip.
    String clientId = "esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.printf("Connecting to MQTT %s:%u as %s ... ", MQTT_HOST, MQTT_PORT, clientId.c_str());
    if (mqtt.connect(clientId.c_str())) {
      Serial.println("ok");
      // Subscribe setelah SETIAP koneksi. Desired state di-retain oleh broker,
      // jadi langsung diterima begitu subscribe -> lampu kembali ke keadaan yang diminta.
      mqtt.subscribe(topicDesired.c_str(), 1);
      mqtt.subscribe((topicCommands + "+").c_str(), 1);
      mqtt.subscribe(topicErrors.c_str(), 0);
    } else {
      Serial.printf("failed (state %d), retrying in 5 s\n", mqtt.state());
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  setLamp(false);

  String base = String(TOPIC_PREFIX) + "/" + ACCESS_TOKEN;
  topicTelemetry = base + "/telemetry";
  topicDesired   = base + "/desired";
  topicCommands  = base + "/commands/";
  topicErrors    = base + "/errors";

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(1024);  // bawaan 256 byte terlalu kecil untuk command ber-parameter
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();  // WAJIB sering dipanggil: menerima pesan dan menjaga koneksi

  if (millis() - lastSend >= SEND_INTERVAL_MS) {
    lastSend = millis();
    sendTelemetry();
  }
}
