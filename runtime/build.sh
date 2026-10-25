#!/usr/bin/env bash
# Builds the Quiver runtime for one board: pinned MicroPython with OTA partitions, rollback, and the
# loader and app frozen in. Runs in Docker so the toolchain is the same everywhere (CI uses it too).
#
#   runtime/build.sh QUIVER_NANO_ESP32 1.0.0
#   runtime/build.sh QUIVER_ESP32S3 1.0.0
#
# Output in runtime/dist/<board>/: micropython.bin (the app image: the OTA payload) and
# firmware.bin (bootloader, partition table and app, for flashing an empty board at 0x0). Then make
# the release with: python tools/package.py runtime --version … --board … --chip … \
#   --image …/micropython.bin --flash …/firmware.bin
set -euo pipefail

MICROPYTHON=v1.29.0
IDF_IMAGE=espressif/idf:v5.5.1

board="${1:?Usage: build.sh <QUIVER_NANO_ESP32|QUIVER_ESP32S3> <version>}"
version="${2:?Usage: build.sh <board> <version>}"
here="$(cd "$(dirname "$0")" && pwd)"
firmware="$(dirname "$here")"
case "$board" in
  QUIVER_NANO_ESP32) name=nano-esp32 chip=ESP32-S3 ;;
  QUIVER_ESP32S3) name=esp32s3 chip=ESP32-S3 ;;
  *) echo "Unknown board $board" >&2; exit 2 ;;
esac

"$(command -v python3 || command -v python)" "$here/stage.py" --version "$version" --board "$name" --chip "$chip" >/dev/null
mkdir -p "$here/build/cache" "$here/dist/$board"
# MSYS_NO_PATHCONV: Git Bash on Windows would otherwise rewrite the container paths.
MSYS_NO_PATHCONV=1 docker run --rm -v "$firmware:/quiver" -v "$here/build/cache:/cache" "$IDF_IMAGE" bash -euc "
  git config --global --add safe.directory '*'
  [ -d /cache/micropython ] || git clone --depth 1 --branch $MICROPYTHON https://github.com/micropython/micropython /cache/micropython
  cd /cache/micropython
  make -C mpy-cross -j\$(nproc) >/dev/null
  cd ports/esp32
  cp /quiver/runtime/boards/$board/partitions-*.csv .
  make submodules BOARD_DIR=/quiver/runtime/boards/$board >/dev/null
  make -j\$(nproc) BOARD_DIR=/quiver/runtime/boards/$board
  cp build-$board/micropython.bin build-$board/firmware.bin /quiver/runtime/dist/$board/
"
echo "$here/dist/$board: micropython.bin (OTA payload), firmware.bin (flash at 0x0)"
