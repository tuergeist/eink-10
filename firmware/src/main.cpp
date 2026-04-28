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
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <time.h>
#include <sys/time.h>

#include "secrets.h"

// EINK_CHANNEL is set per platformio.ini env (e.g. "inkplate10", "inkplate6").
// Falls back to "inkplate10" if someone builds without the define.
#ifndef EINK_CHANNEL
#define EINK_CHANNEL "inkplate10"
#endif

// EINK_HAS_BATTERY = 1 means we trust display.readBattery() to reflect cell
// voltage and can use voltage thresholds to detect Low / USB-only states.
// = 0 means no battery is wired up — readBattery() returns whatever the
// charger IC's BAT pin floats to (typically ~USB voltage), which is
// indistinguishable from "full battery". In that case we skip the
// threshold logic and always classify as Usb so the badge accurately
// reflects "running on USB, no cell to drain".
#ifndef EINK_HAS_BATTERY
#define EINK_HAS_BATTERY 1
#endif

static constexpr const char *DEFAULT_CONFIG_URL =
    "https://eink.ein-service.de/c/" EINK_CHANNEL "/config.json";
static constexpr uint32_t DEFAULT_REFRESH_S = 300;
static constexpr uint32_t WIFI_TIMEOUT_MS = 30000;
static constexpr uint32_t RETRY_SLEEP_S = 60;
static constexpr uint32_t HTTP_TIMEOUT_MS = 20000;
static constexpr uint32_t NTP_TIMEOUT_MS = 5000;
// Europe/Berlin POSIX TZ. CEST/CET DST rules; works without tzdata files.
static constexpr const char *TZ_RULE = "CET-1CEST,M3.5.0,M10.5.0/3";

// Battery thresholds (Li-Po single cell). Below BATT_USB_ONLY we assume
// there's no battery installed — the board runs from USB and we don't show
// a low-battery indicator. Below BATT_LOW (3.4 V) we extend the sleep cycle
// to stretch the remaining capacity.
static constexpr float BATT_USB_ONLY = 2.5f;
static constexpr float BATT_LOW = 3.4f;
static constexpr uint32_t LOW_BAT_REFRESH_MULT = 6;

Inkplate display(INKPLATE_3BIT);
Preferences prefs;

// Reused for every HTTPS request this cycle — saves the TLS handshake state.
static WiFiClientSecure secureClient;
static bool secureReady = false;

static void initSecureClient() {
    if (secureReady) return;
    // TLS without cert verification. The bearer token is the primary
    // authentication; TLS still encrypts traffic against passive sniffers.
    // Harden by pinning the Let's Encrypt ISRG Root X1 once it's worth the
    // rotation hassle.
    secureClient.setInsecure();
    secureReady = true;
}

// HTTPClient.begin() that picks plain HTTP or HTTPS+CA-bundle from the URL.
static bool httpBegin(HTTPClient &http, const String &url) {
    if (url.startsWith("https://")) {
        initSecureClient();
        return http.begin(secureClient, url);
    }
    return http.begin(url);
}

static void deepSleep(uint64_t seconds) {
    Serial.printf("[sleep] %llus\n", (unsigned long long)seconds);
    Serial.flush();
    // Cut power to the EPD rails + idle the SD card so nothing keeps drawing
    // while the ESP32 sleeps. display.display() turns off the panel by
    // default, but einkOff() is idempotent and explicit.
    display.einkOff();
    display.sdCardSleep();
    esp_sleep_enable_timer_wakeup(seconds * 1000000ULL);
    esp_deep_sleep_start();
}

// Returns 0.0 when no battery is detected (USB-only). Otherwise the cell
// voltage. The MCP23017 + voltage divider need a couple of ms to settle
// after wake; the Inkplate library handles that internally.
static float batteryVolts() {
    return display.readBattery();
}

// Note: enum values use mixed case because Arduino headers `#define LOW 0x0`
// and `#define HIGH 0x1` as preprocessor macros — those expand inside any
// identifier, even scoped enum members.
enum class PowerState { Ok, Low, Usb };

static PowerState classifyPower(float v) {
#if EINK_HAS_BATTERY
    if (v < BATT_USB_ONLY) return PowerState::Usb;
    if (v < BATT_LOW) return PowerState::Low;
    return PowerState::Ok;
#else
    (void)v;
    return PowerState::Usb;
#endif
}

static const char *powerStateName(PowerState s) {
    switch (s) {
        case PowerState::Ok:  return "OK";
        case PowerState::Low: return "LOW";
        case PowerState::Usb: return "USB";
    }
    return "?";
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
    if (!httpBegin(http, url)) {
        Serial.println("[http] begin failed");
        return false;
    }
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);
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

// Battery icon, ~36×16 px, drawn at top-left of the supplied origin.
// Renders an empty cell with a small slice of charge on the left, evoking
// the universal "battery low" iconography.
static void drawBatteryLowIcon(int x, int y) {
    constexpr int bodyW = 30, bodyH = 14;
    // Double-line outline for visibility on dithered backgrounds.
    display.drawRect(x,     y,     bodyW, bodyH, 0);
    display.drawRect(x + 1, y + 1, bodyW - 2, bodyH - 2, 0);
    // Positive terminal stub.
    display.fillRect(x + bodyW, y + 4, 3, bodyH - 8, 0);
    // ~15% charge worth of fill, anchored on the left.
    display.fillRect(x + 3, y + 3, 4, bodyH - 6, 0);
}

// USB trident icon, ~22×28 px. Classic three-pronged USB symbol with a
// solid stem, an arrow tip on top, and circle/square branch terminators.
static void drawUsbIcon(int x, int y) {
    int cx = x + 11;  // stem center
    // Vertical stem
    display.fillRect(cx - 1, y + 4, 3, 22, 0);
    // Arrow head pointing up
    display.fillTriangle(cx, y, cx - 5, y + 6, cx + 5, y + 6, 0);
    // Right branch ending in a small filled square
    display.drawLine(cx + 1, y + 12, cx + 8, y + 12, 0);
    display.drawLine(cx + 8, y + 12, cx + 8, y + 18, 0);
    display.fillRect(cx + 6, y + 18, 5, 4, 0);
    // Left branch ending in a small filled circle
    display.drawLine(cx,     y + 16, cx - 8, y + 16, 0);
    display.drawLine(cx - 8, y + 16, cx - 8, y + 22, 0);
    display.fillCircle(cx - 8, y + 22, 3, 0);
}

// Top-right power-status badge: low-battery icon when on a draining cell,
// USB-trident icon when no battery is detected, nothing when healthy.
// Only rendered when we're already redrawing the display for a new image
// — we don't force a refresh just to update the badge.
static void drawPowerStatusOverlay(PowerState s) {
    if (s == PowerState::Ok) return;

    constexpr int margin = 12;
    constexpr int padding = 6;
    int iconW, iconH;
    if (s == PowerState::Usb) { iconW = 22; iconH = 28; }
    else                       { iconW = 33; iconH = 14; }
    int boxW = iconW + padding * 2;
    int boxH = iconH + padding * 2;
    int boxX = display.width() - boxW - margin;
    int boxY = margin;

    display.fillRect(boxX, boxY, boxW, boxH, 7);
    if (s == PowerState::Usb) {
        drawUsbIcon(boxX + padding, boxY + padding);
    } else {
        drawBatteryLowIcon(boxX + padding, boxY + padding);
    }
    Serial.printf("[batt] overlay symbol=%s\n", powerStateName(s));
}

// Pull NTP time, return true if we got a plausible epoch.
static bool ntpSync() {
    configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
    setenv("TZ", TZ_RULE, 1);
    tzset();

    uint32_t start = millis();
    time_t now = 0;
    while (millis() - start < NTP_TIMEOUT_MS) {
        time(&now);
        if (now > 1700000000) {  // ≥ 2023-11 ⇒ real time
            Serial.printf("[ntp] synced epoch=%lld\n", (long long)now);
            return true;
        }
        delay(100);
    }
    Serial.println("[ntp] timeout");
    return false;
}

// Draw the current local time at bottom-right of the freshly fetched image.
// Renders into the framebuffer; caller is responsible for display.display().
static void drawClockOverlay() {
    if (!ntpSync()) return;

    time_t now = time(nullptr);
    struct tm lt;
    localtime_r(&now, &lt);
    char buf[24];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M", &lt);

    display.setTextSize(2);
    display.setTextWrap(false);
    int16_t x1, y1;
    uint16_t tw, th;
    display.getTextBounds(buf, 0, 0, &x1, &y1, &tw, &th);

    constexpr int padding = 6;
    constexpr int margin = 12;
    int boxW = tw + padding * 2;
    int boxH = th + padding * 2;
    int boxX = display.width() - boxW - margin;
    int boxY = display.height() - boxH - margin;

    // White background under the text so it's readable on any image; no border.
    display.fillRect(boxX, boxY, boxW, boxH, 7);
    display.setTextColor(0, 7);
    display.setCursor(boxX + padding - x1, boxY + padding - y1);
    display.print(buf);
    Serial.printf("[clock] overlay '%s'\n", buf);
}

// Bearer-authenticated GET that streams the PNG straight into the
// Inkplate decoder. Server pre-quantizes onto the 8 panel grays, so we
// keep on-device dither off for sharper text.
static bool fetchAndDrawImage(const String &url) {
    HTTPClient http;
    if (!httpBegin(http, url)) {
        Serial.println("[img] begin failed");
        return false;
    }
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);
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

    float battV = batteryVolts();
    PowerState pstate = classifyPower(battV);
    Serial.printf("[batt] %.2fV state=%s\n", battV, powerStateName(pstate));

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
    bool overlayClock = doc["overlay_clock"] | false;

    Serial.printf("[cfg] last_modified server=%s stored=%s overlay_clock=%d\n",
                  lastMod.c_str(), storedMod.c_str(), overlayClock);

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
            if (overlayClock) {
                drawClockOverlay();
            }
            drawPowerStatusOverlay(pstate);
            display.display();
            prefs.putString("last_modified", lastMod);
            Serial.println("[img] displayed");
        } else {
            Serial.println("[img] fetch/decode failed");
        }
    }

    if (pstate == PowerState::Low) {
        refreshS *= LOW_BAT_REFRESH_MULT;
        Serial.printf("[batt] low → stretched sleep to %us\n", refreshS);
    }
    deepSleep(refreshS);
}

void loop() {}
