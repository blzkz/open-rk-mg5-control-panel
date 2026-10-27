"""Royal Kludge mouse configuration protocols.

- MG5: "BeiYing" protocol, feature report 3 with 63-byte 0x50 frames.
- M30 (experimental, untested on hardware): feature report 9 with 519-byte frames that read and
  write the whole 400-byte config block.

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



@dataclass(frozen=True)
class Model:
    """What each supported mouse offers. The panel adapts its options to this."""

    name: str
    protocol: str  # "mg5" or "m30"
    vendor_id: int
    dongle_pid: int
    wired_pid: int
    dpi_min: int
    dpi_max: int
    dpi_step: int
    report_rates: tuple[int, ...]
    sleep_times: tuple[int, ...]
    debounce_max: int
    battery: bool
    sensor_mode: bool
    glass_mode: bool
    key_remap: bool
    experimental: bool = False
    # False when the buttons can be written but not read back: the panel keeps the layout it last wrote.
    key_readback: bool = True

    @property
    def product_ids(self) -> tuple[int, int]:
        return (self.dongle_pid, self.wired_pid)


MG5_MODEL = Model(
    name="RK MG5", protocol="mg5", vendor_id=VENDOR_ID, dongle_pid=PRODUCT_ID_DONGLE, wired_pid=PRODUCT_ID_WIRED,
    dpi_min=DPI_MIN, dpi_max=DPI_MAX, dpi_step=DPI_STEP, report_rates=tuple(sorted(REPORT_RATES)),
    sleep_times=tuple(SLEEP_TIMES), debounce_max=20, battery=True, sensor_mode=True, glass_mode=True, key_remap=True,
)
# From the official web configurator (assets/index-BSYKKems.js). Nobody has tested it on a real M30 yet.
M30_MODEL = Model(
    name="RK M30", protocol="m30", vendor_id=0x258A, dongle_pid=0x0138, wired_pid=0x0136,
    dpi_min=100, dpi_max=24000, dpi_step=100, report_rates=(125, 250, 500, 1000),
    sleep_times=(0, 60, 120, 180, 300, 600, 900, 1800, 3600), debounce_max=15,
    battery=False, sensor_mode=False, glass_mode=False, key_remap=True, experimental=True, key_readback=False,
)
MODELS = (MG5_MODEL, M30_MODEL)


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
    model: Model = MG5_MODEL

    @property
    def wireless(self) -> bool:
        return self.product_id == self.model.dongle_pid


def _is_config_interface(model: Model, descriptor: bytes) -> bool:
    if model.protocol == "m30":
        # 0x258A is a generic SinoWealth vendor id: also require the vendor page and feature report 9.
        return b"\x06\x00\xff" in descriptor and b"\x85\x09" in descriptor
    # Config interface: vendor usage page 0xFF00.
    return descriptor[:3] == b"\x06\x00\xff"


def find_device(models: tuple[Model, ...] = MODELS) -> DeviceInfo:
    for dev in enumerate_hidraw():
        for model in models:
            if dev.vendor_id != model.vendor_id or dev.product_id not in model.product_ids:
                continue
            if _is_config_interface(model, dev.report_descriptor):
                return DeviceInfo(dev.path, dev.product_id, model)
    names = " / ".join(m.name for m in models)
    raise DeviceNotFound(f"No se encuentra el {names} (ni receptor ni cable)")


def open_device(info: DeviceInfo | None = None) -> "MG5 | M30":
    """Opens whichever supported mouse is connected, with the backend for its protocol."""
    info = info or find_device()
    return M30(info) if info.model.protocol == "m30" else MG5(info)


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
        self.info = info or find_device((MG5_MODEL,))
        self.dev = Hidraw(self.info.path)

    @property
    def model(self) -> Model:
        return self.info.model

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

    def wait_online(self, timeout: float = 4.0) -> bool:
        """Waits for the mouse to answer again, e.g. after a polling-rate or sensor-mode change makes it re-link."""
        if self.info.product_id != PRODUCT_ID_DONGLE:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.is_online(timeout=0.3):
                    return True
            except MouseOffline:
                pass
            time.sleep(0.1)
        return False

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

    def set_dpi_level_raw(self, value: int) -> None:
        """Writes the DPI level byte as is: active level in bits 7-4, level count in bits 3-0."""
        self.write_config(OFF_DPI_LEVEL, bytes([value]))

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


# -- RK M30 (experimental) ---------------------------------------------------------
#
# Taken from the official web configurator (assets/index-BSYKKems.js, class "_ye"); never tested
# on a real M30. Every write reads the device's current 400-byte block, changes only the bytes of
# the setting, writes the block back and reads it again to check that the mouse stored it.

M30_REPORT_ID = 9
M30_FRAME_LEN = 519
M30_PAYLOAD_OFFSET = 7
M30_CONFIG_LEN = 400

M30_CMD_SET_KEYS = 0x01
M30_CMD_SET_CONFIG = 0x04
M30_CMD_GET_CONFIG = 0x44
M30_CMD_FW_VERSION = 0x48

# Config block offsets.
M30_OFF_REPORT_RATE = 2
M30_OFF_DPI_LEVEL = 3
M30_OFF_LOD = 4
M30_OFF_DEBOUNCE = 5  # bits 3-0; bit 7 = motion sync
M30_OFF_FLAGS = 6  # bit 0 = angle snapping, bit 7 = ripple control
M30_OFF_DPI_X = 7
M30_OFF_DPI_Y = 27
M30_OFF_DPI_COLORS = 47
M30_OFF_SLEEP = 74  # minutes

M30_REPORT_RATES = {125: 1, 250: 2, 500: 3, 1000: 4}
M30_REPORT_RATE_BY_ID = {v: k for k, v in M30_REPORT_RATES.items()}

# Button table sent with 0x01: one big-endian u32 keyRaw per button (left, right, middle, forward,
# back, DPI), zero-padded to 80 bytes. Same keyRaw values as the MG5 (see actions.py).
M30_KEY_TABLE_LEN = 80
M30_LEFT_CLICK = 0x01010100
M30_DEFAULT_KEYS = (0x01010100, 0x01020100, 0x01030100, 0x01050100, 0x01040100, 0x05040000)


def encode_m30_dpi(dpi: int) -> int:
    dpi = max(M30_MODEL.dpi_min, min(M30_MODEL.dpi_max, round(dpi / 100) * 100))
    return dpi // 100 - 1


def decode_m30_dpi(raw: int) -> int:
    return (raw + 1) * 100


def m30_checksum(frame: bytes) -> int:
    """Sum of every byte after the checksum except the last one of a full frame, as the official app does."""
    return sum(frame[1 : M30_FRAME_LEN - 1]) & 0xFF


def build_m30_frame(cmd: int, para: int, payload: bytes = b"") -> bytes:
    frame = bytearray(M30_FRAME_LEN)
    frame[1] = cmd
    frame[2] = para
    frame[3] = 1  # number of packages
    frame[4] = 0  # package index
    frame[5:7] = len(payload).to_bytes(2, "little")
    frame[M30_PAYLOAD_OFFSET : M30_PAYLOAD_OFFSET + len(payload)] = payload
    frame[0] = m30_checksum(frame)
    return bytes(frame)


def build_m30_key_table(keys: list[int]) -> bytes:
    if len(keys) != KEY_COUNT:
        raise MG5Error(f"Hacen falta {KEY_COUNT} botones, no {len(keys)}")
    # Never leave the mouse without a left click.
    if M30_LEFT_CLICK not in keys:
        raise MG5Error("Algún botón tiene que ser el clic izquierdo")
    table = bytearray(M30_KEY_TABLE_LEN)
    for i, raw in enumerate(keys):
        table[i * 4 : i * 4 + 4] = raw.to_bytes(4, "big")
    return bytes(table)


def parse_m30_config(resp: bytes) -> bytearray:
    """Extracts the config block from a 0x44 response, refusing anything that doesn't look right.

    The block read here is the base of every write, so a bad read must never reach the mouse.
    """
    end = M30_PAYLOAD_OFFSET + M30_CONFIG_LEN
    if len(resp) < end or resp[1] != M30_CMD_GET_CONFIG:
        raise MG5Error(f"Respuesta de configuración del M30 demasiado corta ({len(resp)} bytes)")
    if resp[0] != m30_checksum(resp):
        raise MG5Error("Respuesta de configuración del M30 con checksum incorrecto")
    block = bytearray(resp[M30_PAYLOAD_OFFSET:end])
    if not any(block) or all(b == 0xFF for b in block):
        raise MG5Error("El M30 ha devuelto un bloque de configuración vacío")
    level, count = block[M30_OFF_DPI_LEVEL] >> 4, block[M30_OFF_DPI_LEVEL] & 0x0F
    if not (1 <= count <= DPI_LEVELS and 1 <= level <= count):
        raise MG5Error(f"Bloque de configuración del M30 no válido (nivel DPI 0x{block[M30_OFF_DPI_LEVEL]:02x})")
    return block


@dataclass
class M30Config:
    """Same interface as Config, over the M30's 400-byte block."""

    raw: bytearray

    @property
    def report_rate(self) -> int:
        return M30_REPORT_RATE_BY_ID.get(self.raw[M30_OFF_REPORT_RATE] & 0x0F, 1000)

    @property
    def dpi_level(self) -> int:
        return self.raw[M30_OFF_DPI_LEVEL] >> 4

    @property
    def dpi_level_count(self) -> int:
        return self.raw[M30_OFF_DPI_LEVEL] & 0x0F

    def dpi(self, level: int) -> int:
        i = M30_OFF_DPI_X + (level - 1) * 2
        return decode_m30_dpi(int.from_bytes(self.raw[i : i + 2], "little"))

    def dpi_color(self, level: int) -> tuple[int, int, int]:
        i = M30_OFF_DPI_COLORS + (level - 1) * 3
        return tuple(self.raw[i : i + 3])

    @property
    def lod(self) -> int:
        return self.raw[M30_OFF_LOD] & 0x0F

    @property
    def debounce(self) -> int:
        return self.raw[M30_OFF_DEBOUNCE] & 0x0F

    @property
    def performance(self) -> int:
        """The M30 flags mapped onto the MG5's performance byte (PERF_* masks); no sensor mode or glass mode."""
        value = 0
        if self.raw[M30_OFF_DEBOUNCE] & 0x80:
            value |= PERF_MOTION_SYNC
        if self.raw[M30_OFF_FLAGS] & 0x01:
            value |= PERF_ANGLE_SNAPPING
        if self.raw[M30_OFF_FLAGS] & 0x80:
            value |= PERF_RIPPLE_CONTROL
        return value

    @property
    def sensor_mode(self) -> int:
        return 0

    def flag(self, mask: int) -> bool:
        return bool(self.performance & mask)

    @property
    def sleep_seconds(self) -> int:
        return self.raw[M30_OFF_SLEEP] * 60


def m30_changes_for_performance(value: int) -> dict[int, tuple[int, int]]:
    """Translates a PERF_* byte into M30 config bytes: {offset: (value, mask)}."""
    motion = 0x80 if value & PERF_MOTION_SYNC else 0
    flags = (0x01 if value & PERF_ANGLE_SNAPPING else 0) | (0x80 if value & PERF_RIPPLE_CONTROL else 0)
    return {M30_OFF_DEBOUNCE: (motion, 0x80), M30_OFF_FLAGS: (flags, 0x81)}


def apply_m30_changes(block: bytes, changes: dict[int, tuple[int, int]]) -> bytearray:
    """Returns a copy of `block` with only the masked bits of each changed byte replaced."""
    new = bytearray(block)
    for offset, (value, mask) in changes.items():
        new[offset] = new[offset] & ~mask & 0xFF | value & mask
    return new


class M30:
    """Experimental backend for the RK M30. Same interface as MG5."""

    def __init__(self, info: DeviceInfo | None = None):
        self.info = info or find_device((M30_MODEL,))
        self.dev = Hidraw(self.info.path)
        # The M30 can't report its buttons: this is the layout the panel last wrote, or assumes
        # (the active profile's or the factory one). The app seeds it after connecting.
        self.keys = list(M30_DEFAULT_KEYS)

    @property
    def model(self) -> Model:
        return self.info.model

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

    def _request(self, cmd: int, para: int, payload: bytes, tries: int, interval: float) -> bytes | None:
        """Sends a frame and polls report 9 until a response for the same command shows up."""
        self.dev.send_feature_report(M30_REPORT_ID, build_m30_frame(cmd, para, payload))
        for _ in range(tries):
            time.sleep(interval)
            resp = self.dev.get_feature_report(M30_REPORT_ID, M30_FRAME_LEN)
            if len(resp) >= 2 and resp[1] == cmd:
                return resp
        return None

    # -- reads ---------------------------------------------------------------

    def is_online(self, timeout: float = 1.0) -> bool:
        # The M30 has no online query; a config read that times out raises MouseOffline instead.
        return True

    def wait_online(self, timeout: float = 4.0) -> bool:
        return True

    def read_battery(self) -> Battery | None:
        return None

    def read_config(self) -> M30Config:
        resp = self._request(M30_CMD_GET_CONFIG, 0, bytes(M30_CONFIG_LEN), tries=8, interval=0.05)
        if resp is None:
            raise MouseOffline("El ratón no responde (¿apagado o fuera de alcance?)")
        return M30Config(parse_m30_config(resp))

    def read_keys(self) -> list[int]:
        """Not read from the mouse (it has no command for it): see `keys`."""
        return list(self.keys)

    def read_firmware(self) -> tuple[str, str] | None:
        """(mouse, receiver) firmware versions; the receiver one is only meaningful over 2.4G."""
        resp = self._request(M30_CMD_FW_VERSION, 1, b"\x00", tries=30, interval=0.02)
        if resp is None or len(resp) < M30_PAYLOAD_OFFSET + 4:
            return None
        v = resp[M30_PAYLOAD_OFFSET : M30_PAYLOAD_OFFSET + 4]
        return f"{v[0]:02x}{v[1]:02x}", f"{v[2]:02x}{v[3]:02x}"

    # -- writes --------------------------------------------------------------

    def _modify(self, changes: dict[int, tuple[int, int]]) -> None:
        """Read-modify-write of the config block, verified by reading it back."""
        current = self.read_config().raw
        block = apply_m30_changes(current, changes)
        if block == current:
            return
        # Same order as the official app: cmdPara 0, then 1 if the mouse didn't keep the change.
        for para in (0, 1):
            self._request(M30_CMD_SET_CONFIG, para, bytes(block), tries=4, interval=0.04)
            time.sleep(0.1)
            stored = self.read_config().raw
            if all(stored[off] & mask == block[off] & mask for off, (_value, mask) in changes.items()):
                return
        raise MG5Error("El RK M30 no ha guardado el cambio (la relectura no coincide)")

    def set_report_rate(self, hz: int) -> None:
        self._modify({M30_OFF_REPORT_RATE: (M30_REPORT_RATES[hz], 0x0F)})

    def set_lod(self, lod: int) -> None:
        self._modify({M30_OFF_LOD: (lod, 0x0F)})

    def set_debounce(self, ms: int) -> None:
        # Only 4 bits: the official app offers up to 20 ms, which wraps around to 0..4.
        self._modify({M30_OFF_DEBOUNCE: (max(1, min(M30_MODEL.debounce_max, ms)), 0x0F)})

    def set_performance(self, value: int) -> None:
        self._modify(m30_changes_for_performance(value))

    def set_sleep(self, seconds: int) -> None:
        self._modify({M30_OFF_SLEEP: (seconds // 60, 0xFF)})

    def set_keys(self, keys: list[int]) -> None:
        """Writes the whole button table, as the official app does. There is no way to read it back."""
        table = build_m30_key_table(keys)
        # A config read first, so a sleeping or absent mouse fails here instead of losing the write.
        self.read_config()
        self._request(M30_CMD_SET_KEYS, 0, table, tries=4, interval=0.04)
        self.keys = list(keys)

    def set_key(self, index: int, raw: int) -> None:
        keys = list(self.keys)
        keys[index] = raw
        self.set_keys(keys)

    def set_dpi_value(self, level: int, dpi: int) -> None:
        raw = encode_m30_dpi(dpi).to_bytes(2, "little")
        changes = {}
        for base in (M30_OFF_DPI_X, M30_OFF_DPI_Y):
            offset = base + (level - 1) * 2
            changes[offset] = (raw[0], 0xFF)
            changes[offset + 1] = (raw[1], 0xFF)
        self._modify(changes)

    def set_dpi_color(self, level: int, rgb: tuple[int, int, int]) -> None:
        offset = M30_OFF_DPI_COLORS + (level - 1) * 3
        self._modify({offset + i: (rgb[i], 0xFF) for i in range(3)})

    def set_dpi_level_count(self, config: M30Config, count: int) -> None:
        active = min(config.dpi_level, count)
        self.set_dpi_level_raw(active << 4 | count)

    def set_dpi_level_raw(self, value: int) -> None:
        self._modify({M30_OFF_DPI_LEVEL: (value, 0xFF)})

    def activate_dpi_level(self, config: M30Config, level: int) -> None:
        self.set_dpi_level_raw(level << 4 | config.dpi_level_count)

    # -- events --------------------------------------------------------------

    def read_events(self) -> list[Event]:
        """The M30 reports nothing we know how to decode; just drain the queue."""
        while self.dev.read(M30_FRAME_LEN + 1) is not None:
            pass
        return []
