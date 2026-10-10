"""GTK4 / libadwaita control panel for the Royal Kludge MG5."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import protocol as p  # noqa: E402

APP_ID = "io.github.blzkz.OpenRkMg5"
UDEV_RULE = 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="372e", ATTRS{idProduct}=="103f|1073", TAG+="uaccess"'
BATTERY_POLL_SECONDS = 60
DPI_APPLY_DELAY_MS = 500
OFFLINE_RETRY_SECONDS = 3

PERF_FLAGS = [
    (p.PERF_MOTION_SYNC, "Motion Sync", "Sincroniza los informes del sensor con el polling"),
    (p.PERF_RIPPLE_CONTROL, "Ripple Control", "Suaviza el movimiento a DPI altos"),
    (p.PERF_ANGLE_SNAPPING, "Corrección de ángulo", "Endereza las líneas horizontales y verticales"),
    (p.PERF_GLASS_MODE, "Modo cristal", "Optimiza el sensor para superficies de cristal"),
]


def _sleep_label(seconds: int) -> str:
    if seconds == 0:
        return "Desactivado"
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min"


def _rgba(rgb: tuple[int, int, int]) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.red, color.green, color.blue, color.alpha = rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1.0
    return color


def _rgb(color: Gdk.RGBA) -> tuple[int, int, int]:
    return tuple(round(c * 255) for c in (color.red, color.green, color.blue))


class DpiLevelRow(Adw.ActionRow):
    def __init__(self, window: "MainWindow", level: int, group: Gtk.CheckButton | None):
        super().__init__(title=f"Nivel {level}")
        self.window = window
        self.level = level
        self._apply_source = 0

        self.radio = Gtk.CheckButton(valign=Gtk.Align.CENTER, tooltip_text="Usar este nivel")
        if group is not None:
            self.radio.set_group(group)
        self.radio.connect("toggled", self._on_radio_toggled)
        self.add_prefix(self.radio)
        self.set_activatable_widget(self.radio)

        adjustment = Gtk.Adjustment(lower=p.DPI_MIN, upper=p.DPI_MAX, step_increment=p.DPI_STEP, page_increment=1000)
        self.spin = Gtk.SpinButton(adjustment=adjustment, numeric=True, valign=Gtk.Align.CENTER, width_chars=6)
        self.spin.connect("value-changed", self._on_dpi_changed)
        self.add_suffix(self.spin)

        self.color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.color.connect("notify::rgba", self._on_color_changed)
        self.add_suffix(self.color)

    def load(self, config: p.Config) -> None:
        self.spin.set_value(config.dpi(self.level))
        self.color.set_rgba(_rgba(config.dpi_color(self.level)))
        self.radio.set_active(config.dpi_level == self.level)

    def _on_radio_toggled(self, radio: Gtk.CheckButton) -> None:
        if radio.get_active() and not self.window.loading:
            self.window.apply(lambda m, c: m.activate_dpi_level(c, self.level))

    def _on_dpi_changed(self, spin: Gtk.SpinButton) -> None:
        if self.window.loading:
            return
        # Snap to the 50 DPI grid and wait until the user stops typing or holding the arrows.
        value = round(spin.get_value() / p.DPI_STEP) * p.DPI_STEP
        if self._apply_source:
            GLib.source_remove(self._apply_source)
        self._apply_source = GLib.timeout_add(DPI_APPLY_DELAY_MS, self._apply_dpi, value)

    def _apply_dpi(self, value: int) -> bool:
        self._apply_source = 0
        level = self.level

        def write(mouse: p.MG5, config: p.Config) -> None:
            mouse.set_dpi_value(level, value)
            if config.dpi_level == level:
                # The active level only takes effect after re-selecting it.
                mouse.activate_dpi_level(mouse.read_config(), level)

        self.window.apply(write)
        return GLib.SOURCE_REMOVE

    def _on_color_changed(self, button: Gtk.ColorDialogButton, _pspec) -> None:
        if self.window.loading:
            return
        rgb = _rgb(button.get_rgba())
        self.window.apply(lambda m, c: m.set_dpi_color(self.level, rgb))


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Panel RK MG5", default_width=560, default_height=820)
        self.mouse: p.MG5 | None = None
        self.config: p.Config | None = None
        self.loading = False
        self._event_source = 0
        self._retry_source = 0

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Volver a leer el ratón")
        refresh.connect("clicked", lambda *_: self.connect_mouse())
        header.pack_start(refresh)
        toolbar.add_top_bar(header)
        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar.set_content(self.stack)

        self.status = Adw.StatusPage(icon_name="input-mouse-symbolic")
        retry = Gtk.Button(label="Reintentar", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        retry.connect("clicked", lambda *_: self.connect_mouse())
        self.status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.rule_label = Gtk.Label(selectable=True, wrap=True, css_classes=["monospace"], visible=False)
        self.status_box.append(self.rule_label)
        self.status_box.append(retry)
        self.status.set_child(self.status_box)
        self.stack.add_named(self.status, "status")

        self.stack.add_named(self._build_settings(), "settings")

        self.connect("close-request", self._on_close)
        GLib.timeout_add_seconds(BATTERY_POLL_SECONDS, self._poll_battery)
        self.connect_mouse()

    # -- UI construction -------------------------------------------------------

    def _build_settings(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        state = Adw.PreferencesGroup(title="Estado")
        self.connection_row = Adw.ActionRow(title="Conexión")
        state.add(self.connection_row)
        self.battery_row = Adw.ActionRow(title="Batería")
        self.battery_bar = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER, width_request=140)
        self.battery_row.add_suffix(self.battery_bar)
        state.add(self.battery_row)
        page.add(state)

        dpi = Adw.PreferencesGroup(title="DPI", description="El nivel marcado es el que está en uso")
        self.level_count_row = Adw.SpinRow.new_with_range(1, p.DPI_LEVELS, 1)
        self.level_count_row.set_title("Número de niveles")
        self.level_count_row.set_subtitle("Niveles que recorre el botón DPI")
        self.level_count_row.connect("notify::value", self._on_level_count_changed)
        dpi.add(self.level_count_row)
        self.level_rows: list[DpiLevelRow] = []
        group = None
        for level in range(1, p.DPI_LEVELS + 1):
            row = DpiLevelRow(self, level, group)
            group = group or row.radio
            self.level_rows.append(row)
            dpi.add(row)
        page.add(dpi)

        sensor = Adw.PreferencesGroup(title="Sensor")
        self.rates = sorted(p.REPORT_RATES)
        self.rate_row = self._combo_row(sensor, "Frecuencia de sondeo", [f"{hz} Hz" for hz in self.rates],
                                        lambda i: lambda m, c: m.set_report_rate(self.rates[i]))
        self.rate_row.set_subtitle("Las frecuencias por encima de 1000 Hz consumen más batería")
        self.lods = list(p.LOD_HEIGHTS)
        self.lod_row = self._combo_row(sensor, "Distancia de levantamiento (LOD)", list(p.LOD_HEIGHTS.values()),
                                       lambda i: lambda m, c: m.set_lod(self.lods[i]))
        self.modes = list(p.SENSOR_MODES)
        self.mode_row = self._combo_row(sensor, "Modo del sensor", list(p.SENSOR_MODES.values()),
                                        lambda i: lambda m, c: m.set_performance(c.performance & 0x3F | self.modes[i] << 6))
        self.debounce_row = Adw.SpinRow.new_with_range(1, 20, 1)
        self.debounce_row.set_title("Debounce")
        self.debounce_row.set_subtitle("Milisegundos de filtrado de los clics")
        self.debounce_row.connect("notify::value", self._on_debounce_changed)
        sensor.add(self.debounce_row)
        self.flag_rows = []
        for mask, title, subtitle in PERF_FLAGS:
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.connect("notify::active", self._on_flag_changed, mask)
            sensor.add(row)
            self.flag_rows.append((mask, row))
        page.add(sensor)

        power = Adw.PreferencesGroup(title="Energía")
        self.sleep_times = p.SLEEP_TIMES
        self.sleep_row = self._combo_row(power, "Reposo tras inactividad", [_sleep_label(s) for s in self.sleep_times],
                                         lambda i: lambda m, c: m.set_sleep(self.sleep_times[i]))
        page.add(power)

        return page

    def _combo_row(self, group, title, labels, make_write) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))

        def on_selected(r, _pspec):
            if not self.loading:
                self.apply(make_write(r.get_selected()))

        row.connect("notify::selected", on_selected)
        group.add(row)
        return row

    # -- device lifecycle ------------------------------------------------------

    def connect_mouse(self) -> None:
        self._disconnect()
        try:
            self.mouse = p.MG5()
            # Cheap check so a sleeping mouse doesn't block the UI on every retry.
            if self.mouse.info.wireless and not self.mouse.is_online(timeout=0.3):
                raise p.MouseOffline()
            self.config = self.mouse.read_config()
            battery = self.mouse.read_battery()
        except p.DeviceNotFound:
            self._show_status("No se encuentra el ratón", "Conecta el receptor 2.4G o el cable del RK MG5.")
            return
        except PermissionError:
            self._show_status(
                "Sin permiso para acceder al ratón",
                "Añade esta regla udev en /etc/udev/rules.d/70-rk-mg5.rules, recarga udev y vuelve a conectar el receptor:",
                show_rule=True,
            )
            return
        except p.MouseOffline:
            self._show_status("El ratón no responde", "Enciéndelo o muévelo para despertarlo.")
            if not self._retry_source:
                self._retry_source = GLib.timeout_add_seconds(OFFLINE_RETRY_SECONDS, self._retry_offline)
            return
        except (p.MG5Error, OSError) as e:
            self._show_status("Error de comunicación", str(e))
            return

        self._event_source = GLib.unix_fd_add_full(
            GLib.PRIORITY_DEFAULT, self.mouse.fileno, GLib.IOCondition.IN, self._on_device_event
        )
        self.connection_row.set_subtitle("Receptor 2.4G" if self.mouse.info.wireless else "Cable USB")
        self._show_battery(battery)
        self._load_config()
        self.stack.set_visible_child_name("settings")

    def _retry_offline(self) -> bool:
        self._retry_source = 0
        self.connect_mouse()
        return GLib.SOURCE_REMOVE

    def _disconnect(self) -> None:
        if self._event_source:
            GLib.source_remove(self._event_source)
            self._event_source = 0
        if self.mouse is not None:
            self.mouse.close()
            self.mouse = None
        self.config = None

    def _on_close(self, *_args) -> bool:
        self._disconnect()
        return False

    def _show_status(self, title: str, description: str, show_rule: bool = False) -> None:
        self._disconnect()
        self.status.set_title(title)
        self.status.set_description(description)
        self.rule_label.set_label(UDEV_RULE)
        self.rule_label.set_visible(show_rule)
        self.stack.set_visible_child_name("status")

    # -- reading ---------------------------------------------------------------

    def _load_config(self) -> None:
        c = self.config
        self.loading = True
        try:
            self.level_count_row.set_value(c.dpi_level_count)
            for row in self.level_rows:
                row.load(c)
                row.set_visible(row.level <= c.dpi_level_count)
            self.rate_row.set_selected(self.rates.index(c.report_rate))
            if c.lod in self.lods:
                self.lod_row.set_selected(self.lods.index(c.lod))
            if c.sensor_mode in self.modes:
                self.mode_row.set_selected(self.modes.index(c.sensor_mode))
            self.debounce_row.set_value(c.debounce)
            for mask, row in self.flag_rows:
                row.set_active(c.flag(mask))
            seconds = c.sleep_seconds
            nearest = min(range(len(self.sleep_times)), key=lambda i: abs(self.sleep_times[i] - seconds))
            self.sleep_row.set_selected(nearest)
        finally:
            self.loading = False

    def _reload(self) -> bool:
        if self.mouse is not None:
            try:
                self.config = self.mouse.read_config()
                self._show_battery(self.mouse.read_battery())
                self._load_config()
            except (p.MG5Error, OSError):
                pass
        return GLib.SOURCE_REMOVE

    def _show_battery(self, battery: p.Battery) -> None:
        self.battery_bar.set_value(battery.percent)
        suffix = " · cargando" if battery.charging else ""
        self.battery_row.set_subtitle(f"{battery.percent} %{suffix}")

    def _poll_battery(self) -> bool:
        if self.mouse is not None:
            try:
                self._show_battery(self.mouse.read_battery())
            except (p.MG5Error, OSError):
                pass
        return GLib.SOURCE_CONTINUE

    def _on_device_event(self, _fd, _condition) -> bool:
        if self.mouse is None:
            return GLib.SOURCE_REMOVE
        try:
            for event in self.mouse.read_events():
                if event.kind == "dpi_level" and self.config is not None:
                    self.config.raw[p.OFF_DPI_LEVEL] = event.value << 4 | self.config.dpi_level_count
                    self._load_config()
                elif event.kind == "battery":
                    self._show_battery(event.value)
                elif event.kind == "connection":
                    if event.value:
                        # Woke up or came back in range: show what it has stored.
                        GLib.timeout_add(500, self._reload)
                    else:
                        self.toast("El ratón se ha desconectado del receptor")
        except OSError:
            # The receiver was unplugged.
            self._event_source = 0
            self._show_status("No se encuentra el ratón", "Conecta el receptor 2.4G o el cable del RK MG5.")
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- writing ---------------------------------------------------------------

    def apply(self, write) -> None:
        """Runs write(mouse, config), then re-reads the config so the UI shows what the mouse stored."""
        if self.mouse is None or self.config is None:
            return
        try:
            # Writes sent while the mouse sleeps reach the receiver but not the mouse, and get lost.
            if self.mouse.info.wireless and not self.mouse.is_online(timeout=0.3):
                raise p.MouseOffline()
            write(self.mouse, self.config)
            self.config = self.mouse.read_config()
        except p.MouseOffline:
            self.toast("El ratón está dormido: muévelo y vuelve a intentarlo")
        except (p.MG5Error, OSError) as e:
            self.toast(f"Error al guardar: {e}")
        self._load_config()

    def _on_level_count_changed(self, row, _pspec) -> None:
        if self.loading:
            return
        count = int(row.get_value())
        self.apply(lambda m, c: m.set_dpi_level_count(c, count))
        for level_row in self.level_rows:
            level_row.set_visible(level_row.level <= count)

    def _on_debounce_changed(self, row, _pspec) -> None:
        if not self.loading:
            ms = int(row.get_value())
            self.apply(lambda m, c: m.set_debounce(ms))

    def _on_flag_changed(self, row, _pspec, mask: int) -> None:
        if self.loading:
            return
        enabled = row.get_active()
        self.apply(lambda m, c: m.set_performance(c.performance | mask if enabled else c.performance & ~mask))

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message))


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.get_active_window() or MainWindow(self)
        window.present()


def main() -> int:
    return Application().run(sys.argv)
