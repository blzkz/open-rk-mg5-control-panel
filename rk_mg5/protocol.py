"""Royal Kludge MG5 configuration protocol ("BeiYing" protocol, feature report 3).

See docs/PROTOCOL.md for the reverse-engineered details.
"""

import time
from dataclasses import dataclass, field

from .hidraw import Hidraw, enumerate_hidraw

VENDOR_ID = 0x372E
PRODUCT_ID_DONGLE = 0x103F
PRODUCT_ID_WIRED = 0x1073
PRODUCT_IDS = (PRODUCT_ID_DONGLE, PRODUCT_ID_WIRED)

REPORT_ID = 3
REPORT_LEN = 63
EVENT_REPORT_ID = 9
EVENT_MAGIC = 0xFA

HEADER = 0x50

CMD_GET_ONLINE = 0x47
CMD_READ = 0x4F
CMD_SET_DPI = 0x3A
CMD_WRITE_CONFIG = 0x35
CMD_SET_KEY = 0x30
CMD_FACTORY_RESET = 0x06

# CMD_READ addresses data in pages of 10 bytes. Bit 7 of the offset selects the region.
PAGE_SIZE = 10
REGION_KEYS = 0x00
REGION_DEVICE = 0x80
# Inside REGION_DEVICE: bytes 0..29 hold live status, the 64-byte config block follows.
CONFIG_START = 30
CONFIG_LEN = 64
KEY_COUNT = 6

# Config block offsets.
OFF_REPORT_RATE = 1
OFF_DPI_LEVEL = 2
OFF_LOD = 3
OFF_DEBOUNCE = 4
OFF_PERFORMANCE = 5
OFF_SLEEP = 8
OFF_DPI_VALUES = 11
OFF_DPI_COLORS = 27
DPI_LEVELS = 6

REPORT_RATES = {125: 3, 250: 2, 500: 1, 1000: 0, 2000: 6, 4000: 5, 8000: 4}
REPORT_RATE_BY_ID = {v: k for k, v in REPORT_RATES.items()}

LOD_HEIGHTS = {1: "0,7 mm", 2: "1,0 mm", 3: "2,0 mm"}
SENSOR_MODES = {0: "Oficina (ahorro)", 1: "Alto rendimiento", 2: "Competitivo"}
SLEEP_TIMES = [0, 30, 60, 120, 180, 300, 600, 900, 1800, 3600]

DPI_MIN = 50
DPI_MAX = 42000
DPI_STEP = 50

PERF_ANGLE_SNAPPING = 0x01
PERF_GLASS_MODE = 0x02
PERF_RIPPLE_CONTROL = 0x10
PERF_MOTION_SYNC = 0x20



class MG5Error(Exception):
    pass


class DeviceNotFound(MG5Error):
    pass


class MouseOffline(MG5Error):
    pass


def encode_dpi(dpi: int) -> int:
    dpi = max(DPI_MIN, min(DPI_MAX, dpi))
    return dpi // 50 - 1 if dpi <= 30000 else dpi


def decode_dpi(raw: int) -> int:
    if raw > 42000:
        raw = 42000
    return (raw + 1) * 50 if raw < 600 else raw


@dataclass
class Config:
    raw: bytearray

    @property
    def report_rate(self) -> int:
        return REPORT_RATE_BY_ID.get(self.raw[OFF_REPORT_RATE] & 0x07, 1000)

    @property
    def dpi_level(self) -> int:
        return self.raw[OFF_DPI_LEVEL] >> 4

    @property
    def dpi_level_count(self) -> int:
        return self.raw[OFF_DPI_LEVEL] & 0x0F

    def dpi(self, level: int) -> int:
        i = OFF_DPI_VALUES + (level - 1) * 2
        return decode_dpi(int.from_bytes(self.raw[i : i + 2], "little"))

    def dpi_color(self, level: int) -> tuple[int, int, int]:
        i = OFF_DPI_COLORS + (level - 1) * 3
        return tuple(self.raw[i : i + 3])

    @property
    def lod(self) -> int:
        return self.raw[OFF_LOD] & 0x0F

    @property
    def debounce(self) -> int:
        return self.raw[OFF_DEBOUNCE]

    @property
    def performance(self) -> int:
        return self.raw[OFF_PERFORMANCE]

    @property
    def sensor_mode(self) -> int:
        return self.performance >> 6

    def flag(self, mask: int) -> bool:
        return bool(self.performance & mask)

    @property
    def sleep_seconds(self) -> int:
        return self.raw[OFF_SLEEP] * 30


@dataclass
class Battery:
    percent: int
    charging: bool


@dataclass
class Event:
    kind: str  # "connection", "dpi_level" or "battery"
    value: object = None


@dataclass
class DeviceInfo:
    path: str
    product_id: int

    @property
    def wireless(self) -> bool:
        return self.product_id == PRODUCT_ID_DONGLE


def find_device() -> DeviceInfo:
    for dev in enumerate_hidraw():
        if dev.vendor_id != VENDOR_ID or dev.product_id not in PRODUCT_IDS:
            continue
        # Config interface: vendor usage page 0xFF00.
        if dev.report_descriptor[:3] == b"\x06\x00\xff":
            return DeviceInfo(dev.path, dev.product_id)
    raise DeviceNotFound("No se encuentra el RK MG5 (ni receptor ni cable)")


def build_frame(cmd: int, sn: int, offset: int, payload: bytes = b"") -> bytes:
    report = bytearray(REPORT_LEN)
    report[1] = HEADER
    report[2] = len(payload)
    report[3] = sn
    report[4] = cmd
    report[5] = offset
    report[6 : 6 + len(payload)] = payload
    report[0] = sum(report[1:-1]) & 0xFF
    return bytes(report)


class MG5:
    def __init__(self, info: DeviceInfo | None = None):
        self.info = info or find_device()
        self.dev = Hidraw(self.info.path)

    def close(self) -> None:
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def fileno(self) -> int:
        return self.dev.fd

    # -- transport -----------------------------------------------------------

    def _transact(self, cmd: int, sn: int, offset: int, payload: bytes = b"", timeout: float = 1.0) -> bytes:
        """Sends a command and waits for its response.

        Responses (without report id) look like [0x50, status, len, sn, page/offset, data...].
        While idle the device answers with a short 4-byte report instead.
        """
        self.dev.send_feature_report(REPORT_ID, build_frame(cmd, sn, offset, payload))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.02)
            resp = self.dev.get_feature_report(REPORT_ID, REPORT_LEN)
            if len(resp) >= 8 and resp[0] == HEADER and resp[3] == sn:
                if resp[1] != 0:
                    raise MG5Error(f"El ratón rechazó el comando 0x{cmd:02x} (estado {resp[1]})")
                return resp
        raise MouseOffline("El ratón no responde (¿apagado o fuera de alcance?)")

    def read_page(self, region: int, page: int) -> bytes:
        resp = self._transact(CMD_READ, 2, region | page)
        if resp[4] != page:
            raise MG5Error(f"Respuesta inesperada al leer la página {page}")
        return resp[5 : 5 + PAGE_SIZE]

    def read_bytes(self, region: int, start: int, length: int) -> bytes:
        first = start // PAGE_SIZE
        last = (start + length - 1) // PAGE_SIZE
        data = b"".join(self.read_page(region, p) for p in range(first, last + 1))
        skip = start - first * PAGE_SIZE
        return data[skip : skip + length]

    # -- reads ---------------------------------------------------------------

    def is_online(self, timeout: float = 1.0) -> bool:
        """Asks the 2.4G receiver whether the mouse is awake and paired."""
        resp = self._transact(CMD_GET_ONLINE, 1, 0, timeout=timeout)
        return resp[5] == 1

    def read_battery(self) -> Battery:
        value = self.read_page(REGION_DEVICE, 1)[0]
        return Battery(percent=value & 0x7F, charging=bool(value >> 7))

    def read_config(self) -> Config:
        return Config(bytearray(self.read_bytes(REGION_DEVICE, CONFIG_START, CONFIG_LEN)))

    def read_keys(self) -> list[int]:
        data = self.read_bytes(REGION_KEYS, 0, KEY_COUNT * 4)
        return [int.from_bytes(data[i * 4 : i * 4 + 4], "big") for i in range(KEY_COUNT)]

    # -- writes --------------------------------------------------------------

    def write_config(self, offset: int, data: bytes) -> None:
        self._transact(CMD_WRITE_CONFIG, 49, offset, bytes(data))

    def set_report_rate(self, hz: int) -> None:
        rate_id = REPORT_RATES[hz]
        self.write_config(OFF_REPORT_RATE, bytes([rate_id | rate_id << 4]))

    def set_lod(self, lod: int) -> None:
        self.write_config(OFF_LOD, bytes([lod & 0x0F]))

    def set_debounce(self, ms: int) -> None:
        self.write_config(OFF_DEBOUNCE, bytes([max(1, min(20, ms))]))

    def set_performance(self, value: int) -> None:
        self.write_config(OFF_PERFORMANCE, bytes([value & 0xFF]))

    def set_sleep(self, seconds: int) -> None:
        self.write_config(OFF_SLEEP, bytes([seconds // 30]))

    def set_key(self, index: int, raw: int) -> None:
        """Assigns an action (see actions.py) to button `index` (0..5)."""
        self._transact(CMD_SET_KEY, 32, index * 4, raw.to_bytes(4, "big"))

    def set_dpi_value(self, level: int, dpi: int) -> None:
        offset = OFF_DPI_VALUES + (level - 1) * 2
        self.write_config(offset, encode_dpi(dpi).to_bytes(2, "little"))

    def set_dpi_color(self, level: int, rgb: tuple[int, int, int]) -> None:
        self.write_config(OFF_DPI_COLORS + (level - 1) * 3, bytes(rgb))

    def set_dpi_level_count(self, config: Config, count: int) -> None:
        active = min(config.dpi_level, count)
        self.write_config(OFF_DPI_LEVEL, bytes([active << 4 | count]))

    def activate_dpi_level(self, config: Config, level: int) -> None:
        """Switches the active DPI level the same way the official app does."""
        payload = bytearray(6)
        payload[0] = level << 4 | config.dpi_level_count
        payload[1:3] = encode_dpi(config.dpi(level)).to_bytes(2, "little")
        payload[3:6] = bytes(config.dpi_color(level))
        self._transact(CMD_SET_DPI, 80, 0, bytes(payload))

    # -- events --------------------------------------------------------------

    def read_events(self) -> list[Event]:
        """Drains pending input reports and returns the recognised events."""
        events = []
        while (data := self.dev.read(64)) is not None:
            if len(data) < 4 or data[0] != EVENT_REPORT_ID or data[1] != EVENT_MAGIC:
                continue
            kind, value = data[2], data[3]
            if kind == 2:
                events.append(Event("connection", value == 1))
            elif kind == 3:
                events.append(Event("dpi_level", value))
            elif kind == 5:
                events.append(Event("battery", Battery(value & 0x7F, bool(value >> 7))))
        return events
