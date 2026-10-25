"""CPython stand-in for MicroPython's esp32 module: records what deep sleep would wake on."""

WAKEUP_ALL_LOW, WAKEUP_ANY_HIGH = False, True
armed = None  # ("ext0", pin, level) or ("ext1", [pins], level)


def wake_on_ext0(pin, level):
    global armed
    armed = ("ext0", pin.n, level)


def wake_on_ext1(pins, level):
    global armed
    armed = ("ext1", [p.n for p in pins], level)


class Partition:
    """The OTA partitions: a runtime update is written to the next one, which boots on set_boot()."""

    RUNNING, BOOT = 1, 0
    BLOCK = 4096
    BLOCKS = 768  # 3 MiB
    flashed = {}  # partition name → bytearray written
    boot = None  # the partition set to boot next
    valid = False  # mark_app_valid_cancel_rollback() was called

    def __init__(self, which, name="ota_0"):
        self.name = name

    def get_next_update(self):
        return Partition(None, "ota_1" if self.name == "ota_0" else "ota_0")

    def ioctl(self, op, arg):
        return {4: Partition.BLOCKS, 5: Partition.BLOCK}[op]

    def writeblocks(self, n, buf):
        data = Partition.flashed.setdefault(self.name, bytearray())
        if len(data) < (n + 1) * Partition.BLOCK:
            data.extend(bytes((n + 1) * Partition.BLOCK - len(data)))
        data[n * Partition.BLOCK:(n + 1) * Partition.BLOCK] = buf

    def set_boot(self):
        Partition.boot = self.name

    @staticmethod
    def mark_app_valid_cancel_rollback():
        Partition.valid = True
