/*
 * NSTAR Smart Inventory System - ESP32 firmware
 *
 * Reads RC522 RFID tags, selects ADD/REMOVE with GPIO buttons, and sends
 * inventory updates to the local FastAPI service over Wi-Fi. The SSD1306
 * OLED shows the selected mode, transaction results, and backend errors.
 *
 * Hardware: ESP32 DOIT DevKit V1, RC522 (SPI), SSD1306 (I2C), two buttons.
 * Note: Set Wi-Fi credentials privately before flashing this firmware.
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "esp_log.h"
#include "driver/i2c.h"
#include "rc522.h"
#include "driver/rc522_spi.h"
#include "rc522_picc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/timers.h"
#include "freertos/semphr.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "esp_http_client.h"
#include "driver/gpio.h"
#include "cJSON.h"

static const char *TAG = "NSTAR";

/* =========================================================
   WIFI CONFIGURATION
   ========================================================= */
#include "wifi_config.h"

static EventGroupHandle_t wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

/* =========================================================
   NSTAR PIN CONFIGURATION
   ========================================================= */
/* RC522 */
#define RC522_MISO  19
#define RC522_MOSI  23
#define RC522_SCK   18
#define RC522_SDA   21
#define RC522_RST   5
/* OLED */
#define OLED_SDA      25
#define OLED_SCL      22
#define OLED_ADDRESS  0x3C
#define I2C_PORT      I2C_NUM_0
#define BUTTON_ADD     GPIO_NUM_32
#define BUTTON_REMOVE  GPIO_NUM_33

/* Mode selected by the physical buttons; included in each RFID scan request. */
static const char *current_mode = "ADD";

/* One-shot timer returns the display to READY after showing a result. */
static TimerHandle_t oled_reset_timer = NULL;

/* Prevent concurrent tasks from writing to the OLED at the same time. */
static SemaphoreHandle_t oled_mutex = NULL;

/* =========================================================
   5x7 OLED FONT (6-pixel character pitch including spacing)
   ========================================================= */

static const uint8_t font[96][5] = {
    /* SPACE */
    [0] = {0x00,0x00,0x00,0x00,0x00},
    /* % */
    ['%' - 32] = {0x62,0x64,0x08,0x13,0x23},
    /* - */
    ['-' - 32] = {0x08,0x08,0x08,0x08,0x08},
    /* : */
    [':' - 32] = {0x00,0x36,0x36,0x00,0x00},
    /* Numbers */
    ['0' - 32] = {0x3E,0x51,0x49,0x45,0x3E},
    ['1' - 32] = {0x00,0x42,0x7F,0x40,0x00},
    ['2' - 32] = {0x42,0x61,0x51,0x49,0x46},
    ['3' - 32] = {0x21,0x41,0x45,0x4B,0x31},
    ['4' - 32] = {0x18,0x14,0x12,0x7F,0x10},
    ['5' - 32] = {0x27,0x45,0x45,0x45,0x39},
    ['6' - 32] = {0x3C,0x4A,0x49,0x49,0x30},
    ['7' - 32] = {0x01,0x71,0x09,0x05,0x03},
    ['8' - 32] = {0x36,0x49,0x49,0x49,0x36},
    ['9' - 32] = {0x06,0x49,0x49,0x29,0x1E},
    /* Uppercase letters */
    ['A' - 32] = {0x7E,0x11,0x11,0x11,0x7E},
    ['B' - 32] = {0x7F,0x49,0x49,0x49,0x36},
    ['C' - 32] = {0x3E,0x41,0x41,0x41,0x22},
    ['D' - 32] = {0x7F,0x41,0x41,0x22,0x1C},
    ['E' - 32] = {0x7F,0x49,0x49,0x49,0x41},
    ['F' - 32] = {0x7F,0x09,0x09,0x09,0x01},
    ['G' - 32] = {0x3E,0x41,0x49,0x49,0x7A},
    ['H' - 32] = {0x7F,0x08,0x08,0x08,0x7F},
    ['I' - 32] = {0x00,0x41,0x7F,0x41,0x00},
    ['J' - 32] = {0x20,0x40,0x41,0x3F,0x01},
    ['K' - 32] = {0x7F,0x08,0x14,0x22,0x41},
    ['L' - 32] = {0x7F,0x40,0x40,0x40,0x40},
    ['M' - 32] = {0x7F,0x02,0x0C,0x02,0x7F},
    ['N' - 32] = {0x7F,0x04,0x08,0x10,0x7F},
    ['O' - 32] = {0x3E,0x41,0x41,0x41,0x3E},
    ['P' - 32] = {0x7F,0x09,0x09,0x09,0x06},
    ['Q' - 32] = {0x3E,0x41,0x51,0x21,0x5E},
    ['R' - 32] = {0x7F,0x09,0x19,0x29,0x46},
    ['S' - 32] = {0x46,0x49,0x49,0x49,0x31},
    ['T' - 32] = {0x01,0x01,0x7F,0x01,0x01},
    ['U' - 32] = {0x3F,0x40,0x40,0x40,0x3F},
    ['V' - 32] = {0x1F,0x20,0x40,0x20,0x1F},
    ['W' - 32] = {0x7F,0x20,0x18,0x20,0x7F},
    ['X' - 32] = {0x63,0x14,0x08,0x14,0x63},
    ['Y' - 32] = {0x03,0x04,0x78,0x04,0x03},
    ['Z' - 32] = {0x61,0x51,0x49,0x45,0x43}
};

/* =========================================================
   OLED LOW-LEVEL I2C AND TEXT HELPERS
   ========================================================= */

/* SSD1306 command bytes use 0x00 as the I2C control byte. */
static void oled_command(uint8_t command)
{
    uint8_t data[2];
    data[0] = 0x00;
    data[1] = command;
    ESP_ERROR_CHECK(
        i2c_master_write_to_device(
            I2C_PORT,
            OLED_ADDRESS,
            data,
            sizeof(data),
            pdMS_TO_TICKS(100)
        )
    );
}

/* Configure the OLED controller; retain the proven settings for this module. */
static void oled_init(void)
{
    /*
     * These initialization values come from the
     * previous OLED project that worked with this display.
     */
    oled_command(0xAE);
    oled_command(0x20);
    oled_command(0x02);       // Page addressing mode
    oled_command(0xB0);
    oled_command(0xC8);
    oled_command(0x00);
    oled_command(0x10);
    oled_command(0x40);
    oled_command(0x81);
    oled_command(0x7F);
    oled_command(0xA1);
    oled_command(0xA6);
    oled_command(0xA8);
    oled_command(0x3F);
    oled_command(0xA4);
    oled_command(0xD3);
    oled_command(0x00);
    oled_command(0xD5);
    oled_command(0x80);
    oled_command(0xD9);
    oled_command(0xF1);
    oled_command(0xDA);
    oled_command(0x12);
    oled_command(0xDB);
    oled_command(0x40);
    oled_command(0x8D);
    oled_command(0x14);
    oled_command(0xAF);
}

/* Position text using the SSD1306 page (8-pixel row) addressing mode. */
static void oled_set_cursor(int column, int page)
{
    oled_command(0xB0 + page);
    oled_command(
        0x00 + (column & 0x0F)
    );
    oled_command(
        0x10 + ((column >> 4) & 0x0F)
    );
}

/* Clear all eight display pages (128 columns per page). */
static void oled_clear(void)
{
    uint8_t buffer[129];
    buffer[0] = 0x40;
    for (int i = 1; i < 129; i++) {
        buffer[i] = 0x00;
    }
    for (int page = 0; page < 8; page++) {
        oled_set_cursor(0, page);
        ESP_ERROR_CHECK(
            i2c_master_write_to_device(
                I2C_PORT,
                OLED_ADDRESS,
                buffer,
                sizeof(buffer),
                pdMS_TO_TICKS(100)
            )
        );
    }
}

/* Send a five-column glyph followed by one blank spacing column. */
static void oled_write_char(char character)
{
    if (character < 32 || character > 127) {
        character = ' ';
    }
    uint8_t data[7];
    data[0] = 0x40;
    for (int i = 0; i < 5; i++) {
        data[i + 1] =
            font[character - 32][i];
    }
    /* Blank column between characters */
    data[6] = 0x00;
    ESP_ERROR_CHECK(
        i2c_master_write_to_device(
            I2C_PORT,
            OLED_ADDRESS,
            data,
            sizeof(data),
            pdMS_TO_TICKS(100)
        )
    );
}

static void oled_write_text(const char *text)
{
    while (*text) {
        oled_write_char(*text);
        text++;
    }
}
/* Center fixed-width text on the 128-pixel OLED. */

static void oled_write_centered(const char *text, int page)
{
    int text_width = strlen(text) * 6;
    int start_column = (128 - text_width) / 2;
    if (start_column < 0)
    {
        start_column = 0;
    }
    oled_set_cursor(start_column, page);
    oled_write_text(text);
}
/* Serialize complete screen updates across the button, RFID, and timer tasks. */

static void oled_lock(void)
{
    if (oled_mutex != NULL)
    {
        xSemaphoreTake(oled_mutex, portMAX_DELAY);
    }
}

static void oled_unlock(void)
{
    if (oled_mutex != NULL)
    {
        xSemaphoreGive(oled_mutex);
    }
}

/* =========================================================
   OLED READY SCREEN
   ========================================================= */

/* Show the currently selected operation while waiting for a new tag. */
static void oled_show_ready(void)
{
    oled_lock();

    oled_clear();
    // Yellow section: system name
    oled_write_centered("NSTAR", 1);
    // Blue section: selected mode
    if (strcmp(current_mode, "ADD") == 0)
    {
        oled_write_centered("MODE: ADD", 3);
    }
    else
    {
        oled_write_centered("MODE: REMOVE", 3);
    }
    // Blue section: status
    oled_write_centered("READY TO SCAN", 5);
    oled_unlock();
}

/* Show the action, product SKU, and quantity returned by the backend. */
static void oled_show_success(
    const char *action,
    const char *sku,
    int quantity
)
{
    oled_lock();

    oled_clear();
    // Yellow section: system name
    oled_write_centered("NSTAR", 1);
    // Blue section: action result
    if (strcmp(action, "ADD") == 0)
    {
        oled_write_centered("ADD SUCCESS", 3);
    }
    else
    {
        oled_write_centered("REMOVE SUCCESS", 3);
    }
    // Product SKU
    oled_write_centered(sku, 5);
    // Updated inventory quantity
    char quantity_text[25];
    snprintf(
        quantity_text,
        sizeof(quantity_text),
        "QTY: %d",
        quantity
    );
    oled_write_centered(quantity_text, 7);
    oled_unlock();
}

/* Display a short, operator-friendly error returned by the backend. */
static void oled_show_error(const char *message)
{
    oled_lock();

    oled_clear();
    // Yellow section: system name
    oled_write_centered("NSTAR", 1);
    // Blue section: error heading
    oled_write_centered("ERROR", 3);
    // Blue section: error message
    oled_write_centered(message, 5);
    oled_unlock();
}
/* Restore the ready screen when the one-shot result timer expires. */

static void oled_reset_timer_callback(TimerHandle_t timer)
{
    oled_show_ready();
}

/* =========================================================
   RC522 RFID READER CONFIGURATION
   ========================================================= */

static rc522_spi_config_t driver_config = {
    .host_id = SPI3_HOST,
    .bus_config = &(spi_bus_config_t) {
        .miso_io_num = RC522_MISO,
        .mosi_io_num = RC522_MOSI,
        .sclk_io_num = RC522_SCK,
    },
    .dev_config = {
        .spics_io_num = RC522_SDA,
    },
    .rst_io_num = RC522_RST,
};

static rc522_driver_handle_t driver;

static rc522_handle_t scanner;
/* Reserve space for the backend JSON reply, including its null terminator. */
#define HTTP_RESPONSE_SIZE 1024
typedef struct {
    char data[HTTP_RESPONSE_SIZE];
    size_t length;
    bool overflow;  /* True if the server sends more data than the buffer holds. */
} http_response_t;
/* Collect response chunks before parsing the backend JSON. */

static esp_err_t http_event_handler(
    esp_http_client_event_t *evt
)
{
    http_response_t *response =
        (http_response_t *)evt->user_data;
    if (evt->event_id == HTTP_EVENT_ON_DATA && response != NULL)
    {
        if (evt->data_len <= 0) {
            return ESP_OK;
        }
        size_t available =
            sizeof(response->data) - response->length - 1;
        if ((size_t)evt->data_len > available)
        {
            response->overflow = true;
            return ESP_OK;
        }
        memcpy(
            response->data + response->length,
            evt->data,
            evt->data_len
        );
        response->length += evt->data_len;
        response->data[response->length] = '\0';
    }
    return ESP_OK;
}
/* Send the scanned tag and selected inventory action to FastAPI. */

static void send_rfid_scan(const char *rfid_uid, const char *action)
{
    ESP_LOGI(TAG, "Sending RFID scan to NSTAR backend...");
    ESP_LOGI(TAG, "UID: %s", rfid_uid);
    http_response_t response = {0};
    esp_http_client_config_t config = {
        .url = "http://192.168.2.13:8000/api/inventory/scan",
        .method = HTTP_METHOD_POST,
        .timeout_ms = 5000,
        .event_handler = http_event_handler,
        .user_data = &response,
    };
    esp_http_client_handle_t client =
        esp_http_client_init(&config);
    char json_data[150];
    snprintf(
        json_data,
        sizeof(json_data),
        "{"
        "\"rfid_uid\":\"%s\","
        "\"action\":\"%s\","
        "\"device_id\":\"NSTAR-ESP32-01\""
        "}",
        rfid_uid,
        action
    );
    esp_http_client_set_header(
        client,
        "Content-Type",
        "application/json"
    );
    esp_http_client_set_post_field(
        client,
        json_data,
        strlen(json_data)
    );
    esp_err_t err =
        esp_http_client_perform(client);
    if (err == ESP_OK) {
        int status_code =
            esp_http_client_get_status_code(client);
        ESP_LOGI(
            TAG,
            "RFID POST complete! HTTP status: %d",
            status_code
        );
        if (response.overflow)
        {
            ESP_LOGE(TAG, "HTTP response exceeded buffer size");
        }
        else
        {
            ESP_LOGI(TAG, "Backend JSON: %s", response.data);
        }
        /* Parse success/error fields rather than relying only on HTTP status. */
        cJSON *root = cJSON_Parse(response.data);
        if (root == NULL)
        {
            ESP_LOGE(TAG, "Failed to parse backend JSON");
        }
        else
        {
            cJSON *success = cJSON_GetObjectItemCaseSensitive(root, "success");
            cJSON *sku = cJSON_GetObjectItemCaseSensitive(root, "sku");
            cJSON *quantity = cJSON_GetObjectItemCaseSensitive(root, "new_quantity");
            if (cJSON_IsTrue(success) &&
                cJSON_IsString(sku) &&
                cJSON_IsNumber(quantity))
            {
                ESP_LOGI(TAG, "INVENTORY UPDATE SUCCESSFUL");
                ESP_LOGI(TAG, "Product SKU: %s", sku->valuestring);
                ESP_LOGI(TAG, "New quantity: %d", quantity->valueint);
                /* The backend quantity is authoritative; do not calculate it locally. */
                oled_show_success(action, sku->valuestring, quantity->valueint);
            }
            else
            {
                cJSON *error_code = cJSON_GetObjectItemCaseSensitive(
                    root, "error_code"
                );
                if (cJSON_IsString(error_code))
                {
                    ESP_LOGW(TAG, "Inventory error: %s", error_code->valuestring);
                    if (strcmp(error_code->valuestring, "INSUFFICIENT_STOCK") == 0)
                    {
                        oled_show_error("OUT OF STOCK");
                    }
                    else if (strcmp(error_code->valuestring, "PRODUCT_NOT_FOUND") == 0)
                    {
                        oled_show_error("UNKNOWN PRODUCT");
                    }
                    else
                    {
                        oled_show_error("SCAN FAILED");
                    }
                }
                else
                {
                    ESP_LOGW(TAG, "Invalid or unsuccessful backend response");
                    oled_show_error("SCAN FAILED");
                }
            }
            if (oled_reset_timer != NULL)
            {
                xTimerReset(oled_reset_timer, 0);
            }
            cJSON_Delete(root);
        }
    }
    else {
        ESP_LOGE(
            TAG,
            "RFID POST failed: %s",
            esp_err_to_name(err)
        );
    }
    esp_http_client_cleanup(client);
}

/* =========================================================
   RFID TAG DETECTION AND SCAN DISPATCH
   ========================================================= */

static void on_picc_state_changed(
    void *arg,
    esp_event_base_t base,
    int32_t event_id,
    void *data)
{
    rc522_picc_state_changed_event_t *event =
        (rc522_picc_state_changed_event_t *)data;
    rc522_picc_t *picc = event->picc;
    /* TAG DETECTED */
    if (picc->state == RC522_PICC_STATE_ACTIVE) {
        ESP_LOGI(
            TAG,
            "=========================="
        );
        ESP_LOGI(
            TAG,
            "RFID TAG DETECTED"
        );
        ESP_LOGI(
            TAG,
            "=========================="
        );
        /* Print full RFID information */
        rc522_picc_print(picc);
        /* Convert RFID UID to string */
        char uid_string[RC522_PICC_UID_STR_BUFFER_SIZE_MAX];
        esp_err_t uid_err =
            rc522_picc_uid_to_str(
                &picc->uid,
                uid_string,
                sizeof(uid_string)
            );
        if (uid_err == ESP_OK) {
            ESP_LOGI(
                TAG,
                "RFID UID string: %s",
                uid_string
            );
            /*
             * Send the physical RFID scan
             * to the NSTAR backend.
             */
            /* Each detected tag performs one ADD or REMOVE request. */
        send_rfid_scan(uid_string, current_mode);
        }
        else {
            ESP_LOGE(
                TAG,
                "Failed to convert RFID UID to string"
            );
        }
    }
    /* TAG REMOVED */
    else if (
        picc->state == RC522_PICC_STATE_IDLE &&
        event->old_state >= RC522_PICC_STATE_ACTIVE)
    {
        ESP_LOGI(
            TAG,
            "RFID tag removed"
        );
        ESP_LOGI(
            TAG,
            "Ready for next scan..."
        );
    }
}

/* Connect on startup, retry disconnects, and signal when an IP is assigned. */
static void wifi_event_handler(
    void *arg,
    esp_event_base_t event_base,
    int32_t event_id,
    void *event_data)
{
    if (event_base == WIFI_EVENT &&
        event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    }
    else if (event_base == WIFI_EVENT &&
             event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi disconnected. Reconnecting...");
        esp_wifi_connect();
    }
    else if (event_base == IP_EVENT &&
             event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event =
            (ip_event_got_ip_t *)event_data;
        ESP_LOGI(
            TAG,
            "Wi-Fi connected! IP: " IPSTR,
            IP2STR(&event->ip_info.ip)
        );
        xEventGroupSetBits(
            wifi_event_group,
            WIFI_CONNECTED_BIT
        );
    }
}

/* Initialize station-mode Wi-Fi and wait until the ESP32 has an IP. */
static void wifi_init(void)
{
    ESP_LOGI(TAG, "Initializing Wi-Fi...");
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(
        esp_event_loop_create_default()
    );
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg =
        WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(
        esp_wifi_init(&cfg)
    );
    wifi_event_group =
        xEventGroupCreate();
    ESP_ERROR_CHECK(
        esp_event_handler_register(
            WIFI_EVENT,
            ESP_EVENT_ANY_ID,
            &wifi_event_handler,
            NULL
        )
    );
    ESP_ERROR_CHECK(
        esp_event_handler_register(
            IP_EVENT,
            IP_EVENT_STA_GOT_IP,
            &wifi_event_handler,
            NULL
        )
    );
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASSWORD,
        },
    };
    ESP_ERROR_CHECK(
        esp_wifi_set_mode(WIFI_MODE_STA)
    );
    ESP_ERROR_CHECK(
        esp_wifi_set_config(
            WIFI_IF_STA,
            &wifi_config
        )
    );
    ESP_ERROR_CHECK(
        esp_wifi_start()
    );
    ESP_LOGI(TAG, "Connecting to Wi-Fi...");
    xEventGroupWaitBits(
        wifi_event_group,
        WIFI_CONNECTED_BIT,
        pdFALSE,
        pdTRUE,
        portMAX_DELAY
    );
    ESP_LOGI(TAG, "Wi-Fi setup complete");
}

/* Startup connectivity check; this GET does not modify inventory. */
static void test_backend_connection(void)
{
    ESP_LOGI(TAG, "Testing connection to NSTAR backend...");
    esp_http_client_config_t config = {
        .url = "http://192.168.2.13:8000/",
        .method = HTTP_METHOD_GET,
        .timeout_ms = 5000,
    };
    esp_http_client_handle_t client =
        esp_http_client_init(&config);
    esp_err_t err =
        esp_http_client_perform(client);
    if (err == ESP_OK) {
        int status_code =
            esp_http_client_get_status_code(client);
        ESP_LOGI(
            TAG,
            "Backend responded! HTTP status: %d",
            status_code
        );
    }
    else {
        ESP_LOGE(
            TAG,
            "Backend connection failed: %s",
            esp_err_to_name(err)
        );
    }
    esp_http_client_cleanup(client);
}

/* Buttons are active-low: GPIO reads 0 while a button is pressed. */
static void init_buttons(void)
{
    gpio_config_t button_config = {
        .pin_bit_mask = (1ULL << BUTTON_ADD) |
                        (1ULL << BUTTON_REMOVE),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    ESP_ERROR_CHECK(gpio_config(&button_config));
    ESP_LOGI(TAG, "Buttons initialized");
}
/* Poll active-low mode buttons; cancel a pending result-screen reset on press. */

static void button_task(void *arg)
{
    int last_add = 1;
    int last_remove = 1;
    while (1)
    {
        int add = gpio_get_level(BUTTON_ADD);
        int remove = gpio_get_level(BUTTON_REMOVE);
        // ADD button pressed
        if (add == 0 && last_add == 1)
        {
            if (oled_reset_timer != NULL)
            {
                xTimerStop(oled_reset_timer, 0);
            }
            current_mode = "ADD";
            ESP_LOGI(TAG, "MODE SELECTED: ADD");
            oled_show_ready();
        }
        // REMOVE button pressed
        if (remove == 0 && last_remove == 1)
        {
            if (oled_reset_timer != NULL)
            {
                xTimerStop(oled_reset_timer, 0);
            }
            current_mode = "REMOVE";
            ESP_LOGI(TAG, "MODE SELECTED: REMOVE");
            oled_show_ready();
        }
        last_add = add;
        last_remove = remove;
        vTaskDelay(pdMS_TO_TICKS(30));
    }
}

/* =========================================================
   MAIN
   ========================================================= */

/* Initialize display, network, buttons, and RFID reader in that order. */
void app_main(void)
{
    ESP_LOGI(
        TAG,
        "================================"
    );
    ESP_LOGI(
        TAG,
        "             NSTAR"
    );
    ESP_LOGI(
        TAG,
        "================================"
    );
    /* =====================================================
       I2C / OLED SETUP
       ===================================================== */
    i2c_config_t config = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = OLED_SDA,
        .scl_io_num = OLED_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 100000
    };
    ESP_ERROR_CHECK(
        i2c_param_config(
            I2C_PORT,
            &config
        )
    );
    ESP_ERROR_CHECK(
        i2c_driver_install(
            I2C_PORT,
            config.mode,
            0,
            0,
            0
        )
    );
    ESP_LOGI(
        TAG,
        "Initializing OLED..."
    );
    oled_mutex = xSemaphoreCreateMutex();
    if (oled_mutex == NULL)
    {
        ESP_LOGE(TAG, "Failed to create OLED mutex");
        return;
    }
    oled_init();
    oled_show_ready();
    oled_reset_timer = xTimerCreate(
        "oled_reset",
        pdMS_TO_TICKS(5000),
        pdFALSE,
        NULL,
        oled_reset_timer_callback
    );
    if (oled_reset_timer == NULL)
    {
        ESP_LOGE(TAG, "Failed to create OLED reset timer");
    }
    ESP_LOGI(
        TAG,
        "OLED initialized"
    );
    /* NEW */
    wifi_init();
    test_backend_connection();
    init_buttons();
    xTaskCreate(
        button_task,
        "button_task",
        2048,
        NULL,
        5,
        NULL
    );
    /* =====================================================
       RC522 SETUP
       ===================================================== */
    ESP_LOGI(
        TAG,
        "Initializing RC522..."
    );
    ESP_ERROR_CHECK(
        rc522_spi_create(
            &driver_config,
            &driver
        )
    );
    ESP_ERROR_CHECK(
        rc522_driver_install(
            driver
        )
    );
    rc522_config_t scanner_config = {
        .driver = driver,
    };
    ESP_ERROR_CHECK(
        rc522_create(
            &scanner_config,
            &scanner
        )
    );
    ESP_ERROR_CHECK(
        rc522_register_events(
            scanner,
            RC522_EVENT_PICC_STATE_CHANGED,
            on_picc_state_changed,
            NULL
        )
    );
    ESP_ERROR_CHECK(
        rc522_start(
            scanner
        )
    );
    ESP_LOGI(
        TAG,
        "RC522 initialized"
    );
    ESP_LOGI(
        TAG,
        "================================"
    );
    ESP_LOGI(
        TAG,
        "NSTAR READY"
    );
    ESP_LOGI(
        TAG,
        "Place RFID tag on reader"
    );
    ESP_LOGI(
        TAG,
        "================================"
    );
}
