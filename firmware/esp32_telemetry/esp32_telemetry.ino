/*
 * Contoh firmware ESP32: mengirim telemetri ke platform lewat HTTP POST.
 *
 * Yang perlu diisi: nama & password Wi-Fi, alamat server, dan access token
 * (disalin dari halaman device di dashboard, bagian "Connect a device").
 *
 * Contoh ini membaca sensor tiruan (angka acak) supaya bisa langsung dicoba
 * tanpa merangkai sensor. Ganti readTemperature()/readHumidity() dengan
 * pembacaan sensor sungguhan, mis. DHT22.
 *
 * Board: ESP32 (paket "esp32" by Espressif di Arduino IDE). Tidak perlu
 * pustaka tambahan: WiFi.h dan HTTPClient.h sudah termasuk.
 */
#include <WiFi.h>
#include <HTTPClient.h>

const char* WIFI_SSID     = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";

// Alamat komputer yang menjalankan Django, dilihat dari jaringan Wi-Fi yang
// sama. Jangan pakai 127.0.0.1: bagi ESP32, itu adalah dirinya sendiri.
const char* SERVER       = "http://192.168.1.10:8000";
const char* ACCESS_TOKEN = "paste-the-device-access-token-here";

const unsigned long SEND_INTERVAL_MS = 10000;  // kirim tiap 10 detik

float readTemperature() { return 25.0 + random(-20, 21) / 10.0; }
float readHumidity()    { return 60.0 + random(-50, 51) / 10.0; }

void connectWifi() {
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nConnected, IP address: %s\n", WiFi.localIP().toString().c_str());
}

void sendTelemetry(float temperature, float humidity) {
  String url = String(SERVER) + "/api/v1/" + ACCESS_TOKEN + "/telemetry";

  // Payload JSON disusun manual supaya tidak butuh pustaka JSON.
  String body = "{\"temperature\":" + String(temperature, 2) +
                ",\"humidity\":" + String(humidity, 1) + "}";

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  int status = http.POST(body);

  // 200 = tersimpan; 400 = payload salah; 401 = token salah; 403 = device nonaktif.
  Serial.printf("POST %s -> %d %s\n", body.c_str(), status, http.getString().c_str());
  http.end();
}

void setup() {
  Serial.begin(115200);
  connectWifi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  sendTelemetry(readTemperature(), readHumidity());
  delay(SEND_INTERVAL_MS);
}
