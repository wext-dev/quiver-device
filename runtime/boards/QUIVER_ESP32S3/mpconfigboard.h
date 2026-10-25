#define MICROPY_HW_BOARD_NAME               "Quiver runtime (ESP32-S3)"
#define MICROPY_HW_MCU_NAME                 "ESP32-S3"
#define MICROPY_PY_NETWORK_HOSTNAME_DEFAULT "quiver"

// The REPL and log on UART0 too (TX0/RX0), not only native USB: boards with a USB-UART bridge,
// and a serial console while debugging.
#define MICROPY_HW_ENABLE_UART_REPL         (1)
