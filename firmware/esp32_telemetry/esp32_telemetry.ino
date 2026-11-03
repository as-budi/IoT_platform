/*
 * ESP32 firmware template: send telemetry to IoT Platform over HTTP.
 *
 * What it does
 *   - Connects to Wi-Fi and sends temperature and humidity every 10 seconds
 *     (POST /api/v1/<token>/telemetry).
 *   - At start-up, reports its firmware version, board and Wi-Fi MAC address
 *     (POST /api/v1/<token>/firmware). The report is repeated until the platform
 *     accepts it; the values then appear in the Firmware card of the device page.
 *
 * Before uploading
 *   1. Fill in WIFI_SSID and WIFI_PASSWORD.
 *   2. Check SERVER and ACCESS_TOKEN. A template downloaded from the platform
 *      already contains both; otherwise copy the access token from the device page.
 *   3. The readings are simulated (random numbers), so you can try the sketch
 *      without wiring anything. Replace readTemperature() and readHumidity() with
 *      real sensor readings, e.g. from a DHT22.
 *
 * Board: any ESP32 (board package "esp32" by Espressif Systems in Arduino IDE).
 * No extra libraries: WiFi.h, WiFiClientSecure.h and HTTPClient.h come with the
 * board package. Open Serial Monitor at 115200 baud to follow what happens.
 *
 * Documentation: open "Documentation" in the platform menu, chapter "Connect an ESP32".
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// ---- this firmware ------------------------------------------------------------------
#define FW_VERSION "1.0.0"
#define FW_BOARD   "esp32"   // esp32, esp32s2, esp32s3 or esp32c3

const char* WIFI_SSID     = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";

// Address of the platform, without a slash at the end: "https://<server-domain>",
// or "http://<address>:<port>" for a server on your local network.
// Never use 127.0.0.1: for the ESP32 that address means the ESP32 itself.
const char* SERVER       = "http://192.168.1.10:8000";
const char* ACCESS_TOKEN = "paste-the-device-access-token-here";

// Used only when SERVER starts with https://. This is the root certificate (root CA)
// that signed the server's certificate: ISRG Root X1 for servers using Let's Encrypt.
// The ESP32 uses it to make sure it talks to the real server, so the access token is
// never sent to an impostor or over an unencrypted connection.
const char* ROOT_CA = R"PEM(
-----BEGIN CERTIFICATE-----
paste ISRG Root X1 here
-----END CERTIFICATE-----
)PEM";

const unsigned long SEND_INTERVAL_MS = 10000;  // send every 10 seconds

WiFiClient plainClient;
WiFiClientSecure secureClient;
bool firmwareReported = false;   // true once the platform has accepted the firmware report

float readTemperature() { return 25.0 + random(-20, 21) / 10.0; }
float readHumidity()    { return 60.0 + random(-50, 51) / 10.0; }

bool useHttps() { return strncmp(SERVER, "https://", 8) == 0; }

void connectWifi() {
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nConnected, IP address: %s\n", WiFi.localIP().toString().c_str());
}

// Sends a JSON body to /api/v1/<token>/<endpoint> and returns the HTTP status code.
int postJson(const char* endpoint, const String& body) {
  String url = String(SERVER) + "/api/v1/" + ACCESS_TOKEN + "/" + endpoint;
  HTTPClient http;
  if (useHttps()) {
    http.begin(secureClient, url);   // TLS: the server certificate is checked against ROOT_CA
  } else {
    http.begin(plainClient, url);
  }
  http.addHeader("Content-Type", "application/json");
  int status = http.POST(body);

  // 200 = saved; 400 = wrong JSON; 401 = wrong access token; 403 = device inactive.
  // 301 = the server accepts https:// only: change SERVER to https://...
  // A negative number = no connection (with https: often a wrong ROOT_CA).
  Serial.printf("POST %s %s -> %d %s\n", endpoint, body.c_str(), status, http.getString().c_str());
  http.end();
  return status;
}

// Firmware report: version, board and Wi-Fi MAC address.
void reportFirmware() {
  String body = String("{\"version\":\"") + FW_VERSION + "\",\"board\":\"" + FW_BOARD +
                "\",\"mac\":\"" + WiFi.macAddress() + "\"}";
  firmwareReported = postJson("firmware", body) == 200;
}

void sendTelemetry(float temperature, float humidity) {
  // The JSON is built by hand, so no JSON library is needed.
  String body = "{\"temperature\":" + String(temperature, 2) +
                ",\"humidity\":" + String(humidity, 1) + "}";
  postJson("telemetry", body);
}

void setup() {
  Serial.begin(115200);
  Serial.printf("\nFirmware %s (%s)\n", FW_VERSION, FW_BOARD);
  secureClient.setCACert(ROOT_CA);
  connectWifi();
  Serial.printf("MAC address: %s\n", WiFi.macAddress().c_str());
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  if (!firmwareReported) {
    reportFirmware();   // tried again on the next round until the platform accepts it
  }
  sendTelemetry(readTemperature(), readHumidity());
  delay(SEND_INTERVAL_MS);
}
