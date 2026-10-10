"""Button assignments ("keyRaw" values) understood by the MG5 firmware.

A keyRaw is a big-endian u32: [type, code, ...].
- type 0x00 keyboard: byte 1 = modifier mask, byte 3 = HID usage (e.g. 0x00010006 = Ctrl+C)
- type 0x01 mouse:    byte 1 = button (1 left, 2 right, 3 middle, 4 back, 5 forward, 8/9 scroll)
- type 0x02 media:    bytes 2-3 = HID consumer usage
- type 0x05 DPI:      byte 1 = 1 up, 2 down, 4 cycle
"""

from dataclasses import dataclass

DISABLED = 0x00000000
LEFT_CLICK = 0x01010100


@dataclass(frozen=True)
class Action:
    raw: int
    name: str
    group: str


ACTIONS = [
    Action(LEFT_CLICK, "Clic izquierdo", "Ratón"),
    Action(0x01020100, "Clic derecho", "Ratón"),
    Action(0x01030100, "Clic central", "Ratón"),
    Action(0x01040100, "Atrás", "Ratón"),
    Action(0x01050100, "Adelante", "Ratón"),
    Action(0x01080100, "Desplazar arriba", "Ratón"),
    Action(0x01090100, "Desplazar abajo", "Ratón"),
    Action(0x05040000, "Ciclo de DPI", "DPI"),
    Action(0x05010000, "Subir DPI", "DPI"),
    Action(0x05020000, "Bajar DPI", "DPI"),
    Action(0x020000E9, "Subir volumen", "Multimedia"),
    Action(0x020000EA, "Bajar volumen", "Multimedia"),
    Action(0x020000E2, "Silenciar", "Multimedia"),
    Action(0x020000CD, "Reproducir / pausa", "Multimedia"),
    Action(0x020000B5, "Pista siguiente", "Multimedia"),
    Action(0x020000B6, "Pista anterior", "Multimedia"),
    Action(DISABLED, "Desactivado", "Otros"),
]
ACTIONS_BY_RAW = {a.raw: a for a in ACTIONS}

# Factory assignment of each key index.
DEFAULT_KEYS = [0x01010100, 0x01020100, 0x01030100, 0x01050100, 0x01040100, 0x05040000]
BUTTON_NAMES = ["Botón izquierdo", "Botón derecho", "Rueda", "Lateral delantero", "Lateral trasero", "Botón DPI (inferior)"]

MODIFIERS = [(0x01, "Ctrl"), (0x02, "Mayús"), (0x04, "Alt"), (0x08, "Super")]

# Linux evdev keycode -> (HID usage, label). GDK reports evdev keycode + 8.
_EVDEV_TO_HID = {
    1: (0x29, "Esc"), 14: (0x2A, "Retroceso"), 15: (0x2B, "Tab"), 28: (0x28, "Intro"), 57: (0x2C, "Espacio"),
    12: (0x2D, "-"), 13: (0x2E, "="), 26: (0x2F, "["), 27: (0x30, "]"), 43: (0x31, "\\"), 39: (0x33, ";"),
    40: (0x34, "'"), 41: (0x35, "`"), 51: (0x36, ","), 52: (0x37, "."), 53: (0x38, "/"), 58: (0x39, "Bloq Mayús"),
    99: (0x46, "Impr Pant"), 70: (0x47, "Bloq Despl"), 119: (0x48, "Pausa"), 110: (0x49, "Insert"),
    102: (0x4A, "Inicio"), 104: (0x4B, "Re Pág"), 111: (0x4C, "Supr"), 107: (0x4D, "Fin"), 109: (0x4E, "Av Pág"),
    106: (0x4F, "→"), 105: (0x50, "←"), 108: (0x51, "↓"), 103: (0x52, "↑"), 86: (0x64, "<"),
}
_LETTERS = "qwertyuiop", "asdfghjkl", "zxcvbnm"
for _row, _start in zip(_LETTERS, (16, 30, 44)):
    for _i, _ch in enumerate(_row):
        _EVDEV_TO_HID[_start + _i] = (0x04 + ord(_ch) - ord("a"), _ch.upper())
for _i, _digit in enumerate("1234567890"):
    _EVDEV_TO_HID[2 + _i] = (0x1E + _i, _digit)
for _i in range(10):
    _EVDEV_TO_HID[59 + _i] = (0x3A + _i, f"F{_i + 1}")
_EVDEV_TO_HID[87] = (0x44, "F11")
_EVDEV_TO_HID[88] = (0x45, "F12")
_HID_LABELS = {usage: label for usage, label in _EVDEV_TO_HID.values()}

_MODIFIER_KEYCODES = {29, 97, 42, 54, 56, 100, 125, 126}


def key_from_evdev(evdev_code: int, modifier_mask: int) -> int | None:
    """Builds a keyboard keyRaw from a pressed key, or None if it can't be represented."""
    if evdev_code in _MODIFIER_KEYCODES or evdev_code not in _EVDEV_TO_HID:
        return None
    usage, _label = _EVDEV_TO_HID[evdev_code]
    return (modifier_mask & 0xFF) << 16 | usage


def describe(raw: int) -> str:
    if raw in ACTIONS_BY_RAW:
        return ACTIONS_BY_RAW[raw].name
    if raw >> 24 == 0:
        mods = (raw >> 16) & 0xFF
        usage = raw & 0xFF
        parts = [name for bit, name in MODIFIERS if mods & bit]
        parts.append(_HID_LABELS.get(usage, f"Tecla 0x{usage:02X}"))
        return "+".join(parts)
    return f"Personalizado (0x{raw:08X})"
