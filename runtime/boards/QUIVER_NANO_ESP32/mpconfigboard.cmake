# The Quiver runtime for the Arduino Nano ESP32 (ESP32-S3, 16 MiB flash, 8 MiB octal PSRAM):
# MicroPython with two OTA app partitions and rollback, and the boot loader and app frozen in.
include(boards/mpconfigboard_esp32s3_common.cmake)

list(APPEND SDKCONFIG_DEFAULTS
    boards/sdkconfig.240mhz
    boards/sdkconfig.spiram_oct
    boards/sdkconfig.flash_qio_80m
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

set(MICROPY_FROZEN_MANIFEST ${MICROPY_BOARD_DIR}/../../manifest.py)
