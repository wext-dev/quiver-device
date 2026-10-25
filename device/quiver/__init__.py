"""Quiver screen firmware. The device is generic: it gets online, pairs with a home, and draws the
frames the API renders for its exact panel. It knows nothing about shoes, runs or views.
Contract: docs/device-protocol.md."""

FIRMWARE = "3.0.0"
# The board releases are built for when neither the runtime nor board.json names one.
MODEL = "nano-esp32"
# Raised in a release that fixes a security problem: the loader then refuses anything older.
SECURITY = 1
