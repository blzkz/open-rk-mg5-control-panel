"""App preferences and mouse profiles, stored as JSON under ~/.config/open-rk-mg5."""

import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields

from gi.repository import GLib

from . import protocol as p

CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "open-rk-mg5")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
PROFILES_DIR = os.path.join(CONFIG_DIR, "profiles")
# Inside Flatpak the config dir is sandboxed; the autostart entry must go to the host's ~/.config.
AUTOSTART_FILE = os.path.expanduser("~/.config/autostart/io.github.blzkz.OpenRkMg5.desktop")


def _launch_command() -> str:
    if os.environ.get("FLATPAK_ID"):
        return f"flatpak run {os.environ['FLATPAK_ID']}"
    if shutil.which("rk-mg5-panel"):
        return "rk-mg5-panel"
    package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"env PYTHONPATH={package_dir} {sys.executable} -m rk_mg5"


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


@dataclass
class Settings:
    close_to_tray: bool = True
    start_hidden: bool = False
    autostart: bool = False
    low_battery_notify: bool = True
    low_battery_threshold: int = 20
    active_profile: str = ""

    @classmethod
    def load(cls) -> "Settings":
        try:
            with open(SETTINGS_FILE) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        _write_json(SETTINGS_FILE, asdict(self))
        self._sync_autostart()

    def _sync_autostart(self) -> None:
        if not self.autostart:
            if os.path.exists(AUTOSTART_FILE):
                os.remove(AUTOSTART_FILE)
            return
        os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
        with open(AUTOSTART_FILE, "w") as f:
            f.write(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Panel RK MG5\n"
                "Comment=Configura el ratón Royal Kludge MG5\n"
                f"Exec={_launch_command()} --hidden\n"
                "Icon=input-mouse\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )


@dataclass
class Profile:
    """Everything the mouse stores: buttons, DPI, polling, sensor and power settings."""

    name: str
    report_rate: int = 1000
    dpi_level_count: int = 6
    active_dpi_level: int = 1
    dpi_levels: list[int] = field(default_factory=lambda: [800, 1600, 3200, 6400, 12800, 26000])
    dpi_colors: list[str] = field(default_factory=lambda: ["#00ff00", "#ff0000", "#00ffff", "#ff00ff", "#0000ff", "#ffffff"])
    lod: int = 2
    debounce: int = 5
    sensor_mode: int = 1
    motion_sync: bool = True
    ripple_control: bool = True
    angle_snapping: bool = False
    glass_mode: bool = False
    sleep_seconds: int = 300
    buttons: list[str] = field(default_factory=lambda: ["0x01010100", "0x01020100", "0x01030100",
                                                         "0x01050100", "0x01040100", "0x05040000"])

    @classmethod
    def from_mouse(cls, name: str, config: p.Config, keys: list[int]) -> "Profile":
        return cls(
            name=name,
            report_rate=config.report_rate,
            dpi_level_count=config.dpi_level_count,
            active_dpi_level=config.dpi_level,
            dpi_levels=[config.dpi(i) for i in range(1, p.DPI_LEVELS + 1)],
            dpi_colors=["#%02x%02x%02x" % config.dpi_color(i) for i in range(1, p.DPI_LEVELS + 1)],
            lod=config.lod,
            debounce=config.debounce,
            sensor_mode=config.sensor_mode,
            motion_sync=config.flag(p.PERF_MOTION_SYNC),
            ripple_control=config.flag(p.PERF_RIPPLE_CONTROL),
            angle_snapping=config.flag(p.PERF_ANGLE_SNAPPING),
            glass_mode=config.flag(p.PERF_GLASS_MODE),
            sleep_seconds=config.sleep_seconds,
            buttons=[f"0x{k:08x}" for k in keys],
        )

    @property
    def performance_byte(self) -> int:
        value = self.sensor_mode << 6
        for enabled, mask in ((self.motion_sync, p.PERF_MOTION_SYNC), (self.ripple_control, p.PERF_RIPPLE_CONTROL),
                              (self.angle_snapping, p.PERF_ANGLE_SNAPPING), (self.glass_mode, p.PERF_GLASS_MODE)):
            if enabled:
                value |= mask
        return value

    def apply_to(self, mouse: p.MG5) -> None:
        """Writes only the settings that differ from what the mouse currently has."""
        config = mouse.read_config()
        keys = mouse.read_keys()
        for index, raw in enumerate(int(b, 16) for b in self.buttons):
            if keys[index] != raw:
                mouse.set_key(index, raw)
        for level in range(1, p.DPI_LEVELS + 1):
            dpi = self.dpi_levels[level - 1]
            if config.dpi(level) != dpi:
                mouse.set_dpi_value(level, dpi)
            rgb = tuple(int(self.dpi_colors[level - 1][i:i + 2], 16) for i in (1, 3, 5))
            if config.dpi_color(level) != rgb:
                mouse.set_dpi_color(level, rgb)
        if config.report_rate != self.report_rate:
            mouse.set_report_rate(self.report_rate)
        if config.lod != self.lod:
            mouse.set_lod(self.lod)
        if config.debounce != self.debounce:
            mouse.set_debounce(self.debounce)
        if config.performance != self.performance_byte:
            mouse.set_performance(self.performance_byte)
        if config.sleep_seconds != self.sleep_seconds:
            mouse.set_sleep(self.sleep_seconds)
        count = max(1, min(p.DPI_LEVELS, self.dpi_level_count))
        active = max(1, min(count, self.active_dpi_level))
        if config.dpi_level_count != count:
            mouse.write_config(p.OFF_DPI_LEVEL, bytes([config.dpi_level << 4 | count]))
        # Selecting the level makes the new DPI value take effect.
        mouse.activate_dpi_level(mouse.read_config(), active)


def _profile_path(name: str) -> str:
    slug = re.sub(r"[^\w.-]+", "_", name.strip()).strip("_") or "perfil"
    return os.path.join(PROFILES_DIR, f"{slug}.json")


class ProfileStore:
    def __init__(self):
        self.profiles: dict[str, Profile] = {}
        self.reload()

    def reload(self) -> None:
        self.profiles = {}
        if not os.path.isdir(PROFILES_DIR):
            return
        known = {f.name for f in fields(Profile)}
        for entry in sorted(os.listdir(PROFILES_DIR)):
            if not entry.endswith(".json"):
                continue
            try:
                with open(os.path.join(PROFILES_DIR, entry)) as f:
                    data = json.load(f)
                profile = Profile(**{k: v for k, v in data.items() if k in known})
            except (OSError, ValueError, TypeError):
                continue
            self.profiles[profile.name] = profile

    @property
    def names(self) -> list[str]:
        return sorted(self.profiles, key=str.casefold)

    def get(self, name: str) -> Profile | None:
        return self.profiles.get(name)

    def save(self, profile: Profile) -> None:
        _write_json(_profile_path(profile.name), asdict(profile))
        self.profiles[profile.name] = profile

    def delete(self, name: str) -> None:
        self.profiles.pop(name, None)
        path = _profile_path(name)
        if os.path.exists(path):
            os.remove(path)

    def rename(self, old: str, new: str) -> Profile:
        profile = self.profiles[old]
        self.delete(old)
        profile.name = new
        self.save(profile)
        return profile

    def unique_name(self, base: str) -> str:
        name, n = base, 2
        while name in self.profiles or os.path.exists(_profile_path(name)):
            name = f"{base} {n}"
            n += 1
        return name
