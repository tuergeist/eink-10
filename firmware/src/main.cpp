// Inkplate 10 status dashboard.
//
// Boot → Wi-Fi → GET <config_url> (Bearer READ_TOKEN) → if last_modified
// changed, GET <image_url> (Bearer READ_TOKEN) and display → deep-sleep.
//
// The config_url is stored in NVS so the server can migrate it via
// `config_url_override` in the JSON response.

#include <Arduino.h>
#include <Inkplate.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>

#include "secrets.h"

static constexpr const char *DEFAULT_CONFIG_URL =
    "http://172.16.2.158:8989/config.json";
static constexpr uint32_t DEFAULT_REFRESH_S = 300;
static constexpr uint32_t WIFI_TIMEOUT_MS = 30000;
static constexpr uint32_t RETRY_SLEEP_S = 60;
static constexpr uint32_t HTTP_TIMEOUT_MS = 20000;

Inkplate display(INKPLATE_3BIT);
Preferences prefs;

static void deepSleep(uint64_t seconds) {
    Serial.printf("[sleep] %llus\n", (unsigned long long)seconds);
    Serial.flush();
    esp_sleep_enable_timer_wakeup(seconds * 1000000ULL);
    esp_deep_sleep_start();
}

static bool connectWifi() {
    Serial.printf("[wifi] connecting to %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - start > WIFI_TIMEOUT_MS) {
            Serial.println("[wifi] timeout");
            return false;
        }
        delay(200);
    }
    Serial.printf("[wifi] ip=%s rssi=%d\n",
                  WiFi.localIP().toString().c_str(),
                  WiFi.RSSI());
    return true;
}

// Issue a bearer-authenticated GET, populate `out`. Returns true on 200.
static bool fetchString(const String &url, String &out) {
    HTTPClient http;
    if (!http.begin(url)) {
        Serial.println("[http] begin failed");
        return false;
    }
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.addHeader("Authorization", "Bearer " READ_TOKEN);
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[http] GET %s -> %d\n", url.c_str(), code);
        http.end();
        return false;
    }
    out = http.getString();
    http.end();
    return true;
}

// Bearer-authenticated GET that streams the PNG straight into the
// Inkplate decoder. Server pre-quantizes onto the 8 panel grays, so we
// keep on-device dither off for sharper text.
static bool fetchAndDrawImage(const String &url) {
    HTTPClient http;
    if (!http.begin(url)) {
        Serial.println("[img] begin failed");
        return false;
    }
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.addHeader("Authorization", "Bearer " READ_TOKEN);
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[img] GET %s -> %d\n", url.c_str(), code);
        http.end();
        return false;
    }
    int len = http.getSize();
    if (len <= 0) {
        Serial.printf("[img] bad content-length %d\n", len);
        http.end();
        return false;
    }
    bool ok = display.image.drawPngFromWeb(http.getStreamPtr(), 0, 0, len,
                                           /*dither=*/false,
                                           /*invert=*/false);
    http.end();
    return ok;
}

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.println("\n=== Inkplate 10 dashboard ===");

    display.begin();
    prefs.begin("eink", false);

    if (!connectWifi()) {
        deepSleep(RETRY_SLEEP_S);
    }

    String configUrl = prefs.getString("config_url", DEFAULT_CONFIG_URL);
    Serial.printf("[cfg] url=%s\n", configUrl.c_str());

    String body;
    if (!fetchString(configUrl, body)) {
        deepSleep(RETRY_SLEEP_S);
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, body);
    if (err) {
        Serial.printf("[cfg] parse: %s\n", err.c_str());
        deepSleep(RETRY_SLEEP_S);
    }

    const char *override = doc["config_url_override"] | (const char *)nullptr;
    if (override && strlen(override) > 0) {
        Serial.printf("[cfg] override -> %s\n", override);
        prefs.putString("config_url", override);
    }

    String lastMod = doc["last_modified"] | "";
    String storedMod = prefs.getString("last_modified", "");
    uint32_t refreshS = doc["refresh_interval_seconds"] | DEFAULT_REFRESH_S;
    String imageUrl = doc["image_url"] | "";

    Serial.printf("[cfg] last_modified server=%s stored=%s\n",
                  lastMod.c_str(), storedMod.c_str());

    if (lastMod.length() == 0) {
        Serial.println("[img] server has no image yet — skip");
    } else if (lastMod == storedMod) {
        Serial.println("[img] no change — skip refresh");
    } else if (imageUrl.length() == 0) {
        Serial.println("[img] config missing image_url — skip");
    } else {
        Serial.printf("[img] fetch %s\n", imageUrl.c_str());
        display.clearDisplay();
        if (fetchAndDrawImage(imageUrl)) {
            display.display();
            prefs.putString("last_modified", lastMod);
            Serial.println("[img] displayed");
        } else {
            Serial.println("[img] fetch/decode failed");
        }
    }

    deepSleep(refreshS);
}

void loop() {}
