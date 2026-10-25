"""Flashes the Quiver runtime to a board over USB and copies board.json to it.

  python tools/flash.py                          # find the board, flash, copy device/board.json
  python tools/flash.py --port COM7 --keep       # a given port; don't erase first (keeps secrets.json)
  python tools/flash.py --image runtime-nano-esp32-1.0.0-flash.bin   # a downloaded release

The board must be in the ESP32-S3's ROM download mode. On an Arduino Nano ESP32: connect B1 to GND
with a jumper wire, press RST, then remove the wire (the RGB LED changes colour). Most other boards:
hold BOOT and press RESET. This overwrites Arduino's bootloader too; Arduino's "Reset the Arduino
bootloader on the Nano ESP32" article puts it back.

The image is a full flash image (bootloader, partition table, app), written at 0x0: the
firmware.bin that runtime/build.sh makes, or a release's -flash.bin. Needs esptool and mpremote
(pip install -r requirements.txt).
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.dirname(HERE)
ESPRESSIF = 0x303A
ROM_PID = 0x1001  # the ESP32-S3's built-in USB Serial/JTAG, as the ROM loader presents it


def ports():
    from serial.tools import list_ports
    return [p for p in list_ports.comports() if p.vid == ESPRESSIF]


def wait_for(cond, seconds, what):
    end = time.time() + seconds
    while time.time() < end:
        found = cond()
        if found:
            return found
        time.sleep(0.5)
    sys.exit("No %s within %d s." % (what, seconds))


def run(*cmd):
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--port", help="the board's port in download mode (default: found by USB id)")
    p.add_argument("--board", default="QUIVER_NANO_ESP32", help="which runtime/dist/<board>/firmware.bin to flash")
    p.add_argument("--image", help="a full flash image instead (written at 0x0)")
    p.add_argument("--board-json", default=os.path.join(FIRMWARE, "device", "board.json"),
                   help="copied to the board afterwards; '' for none")
    p.add_argument("--keep", action="store_true", help="don't erase the flash first (the filesystem stays)")
    a = p.parse_args()

    image = a.image or os.path.join(FIRMWARE, "runtime", "dist", a.board, "firmware.bin")
    if not os.path.exists(image):
        sys.exit("No %s. Build it with runtime/build.sh %s <version>, or pass --image." % (image, a.board))

    port = a.port
    if not port:
        rom = [x for x in ports() if x.pid == ROM_PID]
        if not rom:
            sys.exit("No board in download mode. Nano ESP32: connect B1 to GND, press RST, remove the wire. "
                     "Others: hold BOOT and press RESET. Then run this again (or give --port).")
        port = rom[0].device
    print("Flashing %s to %s" % (os.path.relpath(image), port))
    esptool = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port]
    if not a.keep:
        run(*esptool, "erase-flash")
    # A watchdog reset, not RTS: on the Nano ESP32 the RTS reset over USB Serial/JTAG lands back in
    # the ROM loader instead of starting the new firmware.
    run(*esptool, "--baud", "460800", "--after", "watchdog-reset", "write-flash", "0x0", image)

    if not a.board_json:
        return
    if not os.path.exists(a.board_json):
        print("No %s: the board runs with the defaults (Nano ESP32 wiring, https://api.quiver.app)." % a.board_json)
        return
    # MicroPython's own USB port comes up a few seconds after the reset; mpremote's Ctrl-C stops the
    # app (setup mode, waiting for the phone) so the file can be copied.
    print("Waiting for MicroPython on USB …")
    mp = wait_for(lambda: [x for x in ports() if x.pid != ROM_PID], 30, "MicroPython USB port (press RST if the board stays in download mode)")
    time.sleep(2)
    run(sys.executable, "-m", "mpremote", "connect", mp[0].device, "cp", a.board_json, ":board.json", "+", "reset")
    print("Done. The panel shows the setup screen; add the screen in the app (Enheter → Legg til enhet).")


if __name__ == "__main__":
    main()
