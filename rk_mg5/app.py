"""GTK4 / libadwaita control panel for Royal Kludge mice (MG5, experimental M30), laid out like Synapse / G Hub."""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from . import actions as a  # noqa: E402
from . import protocol as p  # noqa: E402
from .mouse_view import MouseView  # noqa: E402
from .settings import Profile, ProfileStore, Settings  # noqa: E402
from .tray import MenuItem, TrayIcon  # noqa: E402


APP_ID = "io.github.blzkz.OpenRkMg5"
APP_NAME = "Panel RK MG5"
UDEV_RULE = (
    'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="372e", ATTRS{idProduct}=="103f|1073", TAG+="uaccess"\n'
    'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0136|0138", TAG+="uaccess"'
)
NOT_FOUND = "Conecta el receptor 2.4G o el cable del RK MG5 (o del RK M30, experimental)."
BATTERY_POLL_SECONDS = 60
DPI_APPLY_DELAY_MS = 500
OFFLINE_RETRY_SECONDS = 3
PANEL_WIDTH = 400

CSS = """
@define-color accent_bg_color #2ec5b5;
@define-color accent_color #4fd8c9;
@define-color accent_fg_color #08130f;
@define-color window_bg_color #0f1011;
@define-color view_bg_color #0f1011;
@define-color headerbar_bg_color #0b0c0d;
@define-color card_bg_color #202224;

.main-area { background-color: #0f1011; }
.side-panel { background-color: #18191b; border-radius: 14px; }
.panel-title { font-size: 20px; font-weight: 800; letter-spacing: 2px; }
.panel-subtitle { color: alpha(white, 0.55); }
.section-label { font-size: 11px; font-weight: 800; letter-spacing: 1.5px; color: alpha(white, 0.5); }
.device-title { font-weight: 800; letter-spacing: 2px; }
.device-subtitle { font-size: 11px; color: alpha(white, 0.55); }
.tabbar { padding: 6px 12px 10px 12px; background-color: #0b0c0d; }
.tab { border-radius: 999px; padding: 4px 18px; font-weight: 800; letter-spacing: 1px; font-size: 12px;
       background: none; box-shadow: none; color: alpha(white, 0.7); }
.tab:hover { color: white; background-color: alpha(white, 0.06); }
.tab:checked { background-color: @accent_bg_color; color: @accent_fg_color; }
.segment { font-weight: 700; padding: 6px 4px; }
.segment:checked { background-color: @accent_bg_color; color: @accent_fg_color; }
.mouse-stage { background-image: radial-gradient(circle, alpha(white, 0.08) 1px, transparent 1.6px);
               background-size: 20px 20px; border-radius: 14px; }
.callout { background: none; box-shadow: none; color: alpha(white, 0.85); font-weight: 600; padding: 4px 8px; }
.callout:hover, .callout.selected { color: @accent_color; background: none; }
.action-row.current { background-color: alpha(@accent_bg_color, 0.14); }
.battery { font-weight: 700; }
.big-value { font-size: 34px; font-weight: 800; }
"""


def _rgba(rgb: tuple[int, int, int]) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.red, color.green, color.blue, color.alpha = rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1.0
    return color


def _rgb(color: Gdk.RGBA) -> tuple[int, int, int]:
    return tuple(round(c * 255) for c in (color.red, color.green, color.blue))


def _sleep_label(seconds: int) -> str:
    if seconds == 0:
        return "Desactivado"
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min"


def _label(text: str, css: str, **kwargs) -> Gtk.Label:
    return Gtk.Label(label=text, xalign=0, css_classes=[css], wrap=True, **kwargs)


class Segmented(Gtk.Box):
    """Row of linked toggle buttons, one per option."""

    def __init__(self, labels: list[str], on_selected):
        super().__init__(css_classes=["linked"], homogeneous=True)
        self.buttons: list[Gtk.ToggleButton] = []
        self.loading = False
        for i, text in enumerate(labels):
            button = Gtk.ToggleButton(label=text, css_classes=["segment"])
            if self.buttons:
                button.set_group(self.buttons[0])
            button.connect("toggled", self._on_toggled, i, on_selected)
            self.append(button)
            self.buttons.append(button)

    def set_selected(self, index: int) -> None:
        self.loading = True
        try:
            if 0 <= index < len(self.buttons):
                self.buttons[index].set_active(True)
        finally:
            self.loading = False

    def _on_toggled(self, button, index, on_selected) -> None:
        if button.get_active() and not self.loading:
            on_selected(index)


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

        self.color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.color.connect("notify::rgba", self._on_color_changed)
        self.add_suffix(self.color)

        self.dpi_step = p.DPI_STEP
        adjustment = Gtk.Adjustment(lower=p.DPI_MIN, upper=p.DPI_MAX, step_increment=p.DPI_STEP, page_increment=1000)
        self.spin = Gtk.SpinButton(adjustment=adjustment, numeric=True, valign=Gtk.Align.CENTER, width_chars=6)
        self.spin.connect("value-changed", self._on_dpi_changed)
        self.add_suffix(self.spin)

    def set_model(self, model: p.Model) -> None:
        self.dpi_step = model.dpi_step
        adjustment = self.spin.get_adjustment()
        adjustment.configure(adjustment.get_value(), model.dpi_min, model.dpi_max, model.dpi_step, 1000, 0)

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
        # Snap to the model's DPI grid and wait until the user stops typing or holding the arrows.
        value = round(spin.get_value() / self.dpi_step) * self.dpi_step
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
        super().__init__(application=app, title=APP_NAME, default_width=1180, default_height=780)
        self.app = app
        self.mouse: p.MG5 | p.M30 | None = None
        self.config: p.Config | p.M30Config | None = None
        self.keys: list[int] = list(a.DEFAULT_KEYS)
        self.selected_button = 1
        self.capturing_key = False
        self.loading = False
        self._event_source = 0
        self._retry_source = 0

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        toolbar.add_top_bar(self._build_tabbar())
        self.banner = Adw.Banner(revealed=False)
        toolbar.add_top_bar(self.banner)
        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar.set_content(self.stack)
        self.stack.add_named(self._build_status_page(), "status")
        self.stack.add_named(self._build_main(), "settings")

        key_capture = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        key_capture.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_capture)

        self.connect("close-request", self._on_close)
        GLib.timeout_add_seconds(BATTERY_POLL_SECONDS, self._poll_battery)
        self._select_tab("customize")
        self.connect_mouse()

    # -- UI construction -------------------------------------------------------

    def _build_header(self) -> Gtk.Widget:
        header = Adw.HeaderBar()
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.title_label = Gtk.Label(label="ROYAL KLUDGE MG5", css_classes=["device-title"])
        title_box.append(self.title_label)
        self.connection_label = Gtk.Label(label="Buscando ratón…", css_classes=["device-subtitle"])
        title_box.append(self.connection_label)
        header.set_title_widget(title_box)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Volver a leer el ratón")
        refresh.connect("clicked", lambda *_: self.connect_mouse())
        header.pack_start(refresh)

        profiles = Gtk.Box(css_classes=["linked"])
        self.profile_dropdown = Gtk.DropDown(model=Gtk.StringList(), tooltip_text="Perfil activo")
        self.profile_dropdown.connect("notify::selected", self._on_profile_selected)
        profiles.append(self.profile_dropdown)
        profile_menu = Gio.Menu()
        profile_menu.append("Nuevo perfil con la configuración actual", "win.profile-new")
        profile_menu.append("Duplicar perfil", "win.profile-duplicate")
        profile_menu.append("Renombrar perfil", "win.profile-rename")
        profile_menu.append("Eliminar perfil", "win.profile-delete")
        profiles.append(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=profile_menu,
                                       tooltip_text="Gestionar perfiles"))
        header.pack_start(profiles)
        for name, callback in (("profile-new", self._profile_new), ("profile-duplicate", self._profile_duplicate),
                               ("profile-rename", self._profile_rename), ("profile-delete", self._profile_delete)):
            action = Gio.SimpleAction(name=name)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)

        main_menu = Gio.Menu()
        main_menu.append("Preferencias", "app.preferences")
        main_menu.append("Acerca de Panel RK MG5", "app.about")
        main_menu.append("Salir", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=main_menu, primary=True,
                                       tooltip_text="Menú principal"))

        battery = self.battery_box = Gtk.Box(spacing=6, css_classes=["battery"])
        self.battery_icon = Gtk.Image(icon_name="battery-missing-symbolic")
        self.battery_label = Gtk.Label(label="—")
        battery.append(self.battery_label)
        battery.append(self.battery_icon)
        header.pack_end(battery)
        return header

    def _build_tabbar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, css_classes=["tabbar"])
        self.tabs: dict[str, Gtk.ToggleButton] = {}
        first = None
        for name, label in (("customize", "PERSONALIZAR"), ("performance", "RENDIMIENTO"),
                            ("sensor", "SENSOR"), ("power", "ENERGÍA")):
            tab = Gtk.ToggleButton(label=label, css_classes=["tab"])
            if first is not None:
                tab.set_group(first)
            first = first or tab
            tab.connect("toggled", lambda t, n=name: t.get_active() and self._select_tab(n))
            bar.append(tab)
            self.tabs[name] = tab
        box = Gtk.Box(css_classes=["tabbar"])
        box.append(Gtk.Box(hexpand=True))
        box.append(bar)
        box.append(Gtk.Box(hexpand=True))
        return box

    def _build_status_page(self) -> Gtk.Widget:
        self.status = Adw.StatusPage(icon_name="input-mouse-symbolic")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.rule_label = Gtk.Label(selectable=True, wrap=True, css_classes=["monospace"], visible=False)
        box.append(self.rule_label)
        retry = Gtk.Button(label="Reintentar", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        retry.connect("clicked", lambda *_: self.connect_mouse())
        box.append(retry)
        self.status.set_child(box)
        return self.status

    def _build_main(self) -> Gtk.Widget:
        main = Gtk.Box(spacing=16, css_classes=["main-area"])
        for side in ("start", "end", "top", "bottom"):
            getattr(main, f"set_margin_{side}")(16)

        self.panels = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        self.panels.add_named(self._panel_customize(), "customize")
        self.panels.add_named(self._panel_performance(), "performance")
        self.panels.add_named(self._panel_sensor(), "sensor")
        self.panels.add_named(self._panel_power(), "power")
        scroller = Gtk.ScrolledWindow(child=self.panels, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      width_request=PANEL_WIDTH, css_classes=["side-panel"])
        main.append(scroller)

        self.mouse_view = MouseView()
        self.mouse_view.connect("button-selected", lambda _v, i: self._select_button(i))
        main.append(self.mouse_view)
        return main

    def _panel_box(self, title: str, subtitle: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(22)
        box.append(_label(title, "panel-title"))
        box.append(_label(subtitle, "panel-subtitle"))
        return box

    def _panel_customize(self) -> Gtk.Widget:
        panel = self._panel_box("PERSONALIZAR", "Haz clic en un botón del dibujo para cambiar lo que hace.")
        self.remap_notice = _label("Este ratón no permite leer qué hace cada botón, así que la reasignación "
                                   "está desactivada para no sobrescribir su configuración a ciegas.",
                                   "panel-subtitle", visible=False)
        panel.append(self.remap_notice)
        box = self.remap_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        panel.append(box)
        self.button_title = _label("", "section-label", margin_top=8)
        box.append(self.button_title)

        self.action_rows: dict[int, Adw.ActionRow] = {}
        group_name = None
        listbox = None
        for action in a.ACTIONS:
            if action.group != group_name:
                group_name = action.group
                box.append(_label(group_name.upper(), "section-label", margin_top=6))
                listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
                listbox.connect("row-activated", lambda _l, row: self._assign(row.raw))
                box.append(listbox)
            row = Adw.ActionRow(title=action.name, activatable=True, css_classes=["action-row"])
            row.raw = action.raw
            check = Gtk.Image(icon_name="object-select-symbolic", visible=False)
            row.add_suffix(check)
            row.check = check
            listbox.append(row)
            self.action_rows[action.raw] = row

        box.append(_label("TECLADO", "section-label", margin_top=6))
        keyboard = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.key_row = Adw.ActionRow(title="Tecla o combinación", subtitle="Asigna una tecla, con Ctrl/Mayús/Alt si quieres")
        self.key_button = Gtk.Button(label="Pulsa para grabar", valign=Gtk.Align.CENTER)
        self.key_button.connect("clicked", lambda *_: self._start_key_capture())
        self.key_row.add_suffix(self.key_button)
        keyboard.append(self.key_row)
        box.append(keyboard)

        reset = Gtk.Button(label="Restaurar este botón", halign=Gtk.Align.START, css_classes=["flat"], margin_top=6)
        reset.connect("clicked", lambda *_: self._assign(a.DEFAULT_KEYS[self.selected_button]))
        box.append(reset)
        return panel

    def _panel_performance(self) -> Gtk.Widget:
        box = self._panel_box("RENDIMIENTO", "Niveles de sensibilidad que recorre el botón DPI y tasa de sondeo.")

        box.append(_label("SENSIBILIDAD (DPI)", "section-label", margin_top=8))
        dpi_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.level_count_row = Adw.SpinRow.new_with_range(1, p.DPI_LEVELS, 1)
        self.level_count_row.set_title("Número de niveles")
        self.level_count_row.connect("notify::value", self._on_level_count_changed)
        dpi_list.append(self.level_count_row)
        self.level_rows: list[DpiLevelRow] = []
        group = None
        for level in range(1, p.DPI_LEVELS + 1):
            row = DpiLevelRow(self, level, group)
            group = group or row.radio
            self.level_rows.append(row)
            dpi_list.append(row)
        box.append(dpi_list)

        box.append(_label("TASA DE SONDEO (Hz)", "section-label", margin_top=8))
        self.rates = sorted(p.REPORT_RATES)
        self.rate_segments = Segmented([str(hz) for hz in self.rates],
                                       lambda i: self.apply(lambda m, c: m.set_report_rate(self.rates[i])))
        box.append(self.rate_segments)
        self.rate_hint = _label("Por encima de 1000 Hz el ratón gasta bastante más batería.", "panel-subtitle")
        box.append(self.rate_hint)
        return box

    def _panel_sensor(self) -> Gtk.Widget:
        box = self._panel_box("SENSOR", "Ajustes del sensor óptico y de los clics.")

        box.append(_label("DISTANCIA DE LEVANTAMIENTO (LOD)", "section-label", margin_top=8))
        self.lods = list(p.LOD_HEIGHTS)
        self.lod_segments = Segmented(list(p.LOD_HEIGHTS.values()),
                                      lambda i: self.apply(lambda m, c: m.set_lod(self.lods[i])))
        box.append(self.lod_segments)

        self.mode_label = _label("MODO DEL SENSOR", "section-label", margin_top=8)
        box.append(self.mode_label)
        self.modes = list(p.SENSOR_MODES)
        self.mode_segments = Segmented(
            ["Oficina", "Rendimiento", "Competitivo"],
            lambda i: self.apply(lambda m, c: m.set_performance(c.performance & 0x3F | self.modes[i] << 6)),
        )
        box.append(self.mode_segments)

        box.append(_label("AJUSTES", "section-label", margin_top=8))
        options = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.debounce_row = Adw.SpinRow.new_with_range(1, 20, 1)
        self.debounce_row.set_title("Debounce (ms)")
        self.debounce_row.set_subtitle("Filtrado de rebotes de los clics")
        self.debounce_row.connect("notify::value", self._on_debounce_changed)
        options.append(self.debounce_row)
        self.flag_rows = []
        for mask, title, subtitle in (
            (p.PERF_MOTION_SYNC, "Motion Sync", "Sincroniza el sensor con el sondeo"),
            (p.PERF_RIPPLE_CONTROL, "Ripple Control", "Suaviza el movimiento a DPI altos"),
            (p.PERF_ANGLE_SNAPPING, "Corrección de ángulo", "Endereza las líneas rectas"),
            (p.PERF_GLASS_MODE, "Modo cristal", "Para superficies de cristal"),
        ):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.connect("notify::active", self._on_flag_changed, mask)
            options.append(row)
            self.flag_rows.append((mask, row))
        box.append(options)
        return box

    def _panel_power(self) -> Gtk.Widget:
        box = self._panel_box("ENERGÍA", "Batería y reposo del ratón.")
        self.battery_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.append(self.battery_section)
        self.battery_section.append(_label("BATERÍA", "section-label", margin_top=8))
        self.battery_big = Gtk.Label(label="—", xalign=0, css_classes=["big-value"])
        self.battery_section.append(self.battery_big)
        self.battery_bar = Gtk.LevelBar(min_value=0, max_value=100)
        self.battery_section.append(self.battery_bar)
        self.battery_state = _label("", "panel-subtitle")
        self.battery_section.append(self.battery_state)

        box.append(_label("REPOSO", "section-label", margin_top=12))
        power = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.sleep_times = list(p.SLEEP_TIMES)
        self.sleep_row = Adw.ComboRow(title="Dormir tras inactividad",
                                      model=Gtk.StringList.new([_sleep_label(s) for s in self.sleep_times]))
        self.sleep_row.connect("notify::selected", self._on_sleep_changed)
        power.append(self.sleep_row)
        box.append(power)
        return box

    def _select_tab(self, name: str) -> None:
        self.tabs[name].set_active(True)
        self.panels.set_visible_child_name(name)
        remap = self.mouse is None or self.mouse.model.key_remap
        self.mouse_view.set_callouts_visible(name == "customize" and remap)
        self._update_badge()

    # -- device lifecycle ------------------------------------------------------

    def connect_mouse(self) -> None:
        self._disconnect()
        try:
            self.mouse = p.open_device()
            # Cheap check so a sleeping mouse doesn't block the UI on every retry.
            if self.mouse.info.wireless and not self.mouse.is_online(timeout=0.3):
                raise p.MouseOffline()
            self.config = self.mouse.read_config()
            self.keys = self._read_keys()
            battery = self.mouse.read_battery() if self.mouse.model.battery else None
        except p.DeviceNotFound:
            self._show_status("No se encuentra el ratón", NOT_FOUND)
            return
        except PermissionError:
            self._show_status(
                "Sin permiso para acceder al ratón",
                "Añade esta regla udev en /etc/udev/rules.d/70-rk-mg5.rules, recarga udev y vuelve a conectar el receptor:",
                show_rule=True,
            )
            return
        except p.MouseOffline:
            self._show_status("El ratón está dormido", "Muévelo para despertarlo; el panel se conectará solo.")
            if not self._retry_source:
                self._retry_source = GLib.timeout_add_seconds(OFFLINE_RETRY_SECONDS, self._retry_offline)
            return
        except (p.MG5Error, OSError) as e:
            self._show_status("Error de comunicación", str(e))
            return

        self._event_source = GLib.unix_fd_add_full(
            GLib.PRIORITY_DEFAULT, self.mouse.fileno, GLib.IOCondition.IN, self._on_device_event
        )
        self.connection_label.set_label("Receptor 2.4G" if self.mouse.info.wireless else "Cable USB")
        self._set_model(self.mouse.model)
        if battery is not None:
            self._show_battery(battery)
        self._load_config()
        self._load_keys()
        self.stack.set_visible_child_name("settings")
        self._ensure_profile()

    def _read_keys(self) -> list[int]:
        # Without a way to read the buttons, keep the factory layout just for the labels.
        return self.mouse.read_keys() if self.mouse.model.key_remap else list(a.DEFAULT_KEYS)

    def _set_model(self, model: p.Model) -> None:
        """Shows only the options the connected model has."""
        self.app.model_name = model.name
        self.title_label.set_label("ROYAL KLUDGE " + model.name.split()[-1])
        self.banner.set_title(f"Soporte experimental para el {model.name}: aún sin probar con un ratón real")
        self.banner.set_revealed(model.experimental)
        self.battery_box.set_visible(model.battery)
        self.battery_section.set_visible(model.battery)
        self.remap_notice.set_visible(not model.key_remap)
        self.remap_box.set_visible(model.key_remap)
        self.mouse_view.set_callouts_visible(model.key_remap and self.panels.get_visible_child_name() == "customize")
        self.loading = True
        try:
            for row in self.level_rows:
                row.set_model(model)
            for hz, button in zip(self.rates, self.rate_segments.buttons):
                button.set_visible(hz in model.report_rates)
            self.rate_hint.set_visible(max(model.report_rates) > 1000)
            self.mode_label.set_visible(model.sensor_mode)
            self.mode_segments.set_visible(model.sensor_mode)
            for mask, row in self.flag_rows:
                row.set_visible(mask != p.PERF_GLASS_MODE or model.glass_mode)
            self.debounce_row.get_adjustment().set_upper(model.debounce_max)
            if list(model.sleep_times) != self.sleep_times:
                self.sleep_times = list(model.sleep_times)
                self.sleep_row.set_model(Gtk.StringList.new([_sleep_label(s) for s in self.sleep_times]))
        finally:
            self.loading = False

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
        if self.app.settings.close_to_tray and self.app.tray is not None:
            self.set_visible(False)
            return True
        self.app.quit_app()
        return True

    def _show_status(self, title: str, description: str, show_rule: bool = False) -> None:
        self._disconnect()
        self.connection_label.set_label("Sin conexión")
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
            self.rate_segments.set_selected(self.rates.index(c.report_rate))
            if c.lod in self.lods:
                self.lod_segments.set_selected(self.lods.index(c.lod))
            if c.sensor_mode in self.modes:
                self.mode_segments.set_selected(self.modes.index(c.sensor_mode))
            self.debounce_row.set_value(c.debounce)
            for mask, row in self.flag_rows:
                row.set_active(c.flag(mask))
            seconds = c.sleep_seconds
            nearest = min(range(len(self.sleep_times)), key=lambda i: abs(self.sleep_times[i] - seconds))
            self.sleep_row.set_selected(nearest)
        finally:
            self.loading = False
        self._update_badge()

    def _load_keys(self) -> None:
        self.mouse_view.set_labels({i: a.describe(raw) for i, raw in enumerate(self.keys)})
        self._select_button(self.selected_button)

    def _update_badge(self) -> None:
        if self.config is None or self.panels.get_visible_child_name() != "performance":
            self.mouse_view.set_badge(None)
            return
        level = self.config.dpi_level
        self.mouse_view.set_badge(f"{self.config.dpi(level)} DPI", self.config.dpi_color(level))

    def _reload(self) -> bool:
        if self.mouse is not None:
            try:
                self.config = self.mouse.read_config()
                self.keys = self._read_keys()
                if self.mouse.model.battery:
                    self._show_battery(self.mouse.read_battery())
                self._load_config()
                self._load_keys()
            except (p.MG5Error, OSError):
                pass
        return GLib.SOURCE_REMOVE

    def _show_battery(self, battery: p.Battery) -> None:
        percent = battery.percent
        level = "full" if percent >= 90 else "good" if percent >= 40 else "low" if percent >= 15 else "caution"
        charging = "-charging" if battery.charging else ""
        self.battery_icon.set_from_icon_name(f"battery-{level}{charging}-symbolic")
        self.battery_label.set_label(f"{battery.percent} %")
        self.battery_big.set_label(f"{battery.percent} %")
        self.battery_bar.set_value(battery.percent)
        self.battery_state.set_label("Cargando" if battery.charging else "Funcionando con batería")
        self.app.update_battery(battery)

    def _poll_battery(self) -> bool:
        if self.mouse is not None and self.mouse.model.battery:
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
            self._show_status("No se encuentra el ratón", NOT_FOUND)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- writing ---------------------------------------------------------------

    def apply(self, write) -> bool:
        """Runs write(mouse, config), then re-reads so the UI shows what the mouse stored."""
        if self.mouse is None or self.config is None:
            return False
        ok = False
        try:
            # Writes sent while the mouse sleeps reach the receiver but not the mouse, and get lost.
            if self.mouse.info.wireless and not self.mouse.is_online(timeout=0.3):
                raise p.MouseOffline()
            write(self.mouse, self.config)
            # Some changes make the mouse re-link; give it a moment before reading back.
            if not self.mouse.wait_online():
                raise p.MouseOffline()
            self.config = self.mouse.read_config()
            self.keys = self._read_keys()
            ok = True
            self._autosave_profile()
        except p.MouseOffline:
            self.toast("El ratón está dormido: muévelo y vuelve a intentarlo")
        except (p.MG5Error, OSError) as e:
            self.toast(f"Error al guardar: {e}")
        self._load_config()
        return ok

    def _on_level_count_changed(self, row, _pspec) -> None:
        if self.loading:
            return
        count = int(row.get_value())
        self.apply(lambda m, c: m.set_dpi_level_count(c, count))

    def _on_debounce_changed(self, row, _pspec) -> None:
        if not self.loading:
            ms = int(row.get_value())
            self.apply(lambda m, c: m.set_debounce(ms))

    def _on_flag_changed(self, row, _pspec, mask: int) -> None:
        if self.loading:
            return
        enabled = row.get_active()
        self.apply(lambda m, c: m.set_performance(c.performance | mask if enabled else c.performance & ~mask))

    def _on_sleep_changed(self, row, _pspec) -> None:
        if not self.loading:
            seconds = self.sleep_times[row.get_selected()]
            self.apply(lambda m, c: m.set_sleep(seconds))

    # -- button remapping ------------------------------------------------------

    def _select_button(self, index: int) -> None:
        self.selected_button = index
        self.mouse_view.set_selected(index)
        current = self.keys[index]
        self.button_title.set_label(f"{a.BUTTON_NAMES[index].upper()} · {a.describe(current)}")
        for raw, row in self.action_rows.items():
            row.check.set_visible(raw == current)
            if raw == current:
                row.add_css_class("current")
            else:
                row.remove_css_class("current")
        is_key = current >> 24 == 0 and current != a.DISABLED
        self.key_row.set_subtitle(a.describe(current) if is_key else "Asigna una tecla, con Ctrl/Mayús/Alt si quieres")
        self._stop_key_capture()

    def _assign(self, raw: int) -> None:
        index = self.selected_button
        # Never leave the mouse without a left click.
        if raw != a.LEFT_CLICK and self.keys[index] == a.LEFT_CLICK and self.keys.count(a.LEFT_CLICK) == 1:
            self.toast("Asigna antes el clic izquierdo a otro botón")
            return
        self.apply(lambda m, c: m.set_key(index, raw))
        self._load_keys()

    def _start_key_capture(self) -> None:
        self.capturing_key = True
        self.key_button.set_label("Pulsa una tecla…")
        self.key_button.add_css_class("suggested-action")

    def _stop_key_capture(self) -> None:
        self.capturing_key = False
        self.key_button.set_label("Pulsa para grabar")
        self.key_button.remove_css_class("suggested-action")

    def _on_key_pressed(self, _controller, _keyval, keycode, state) -> bool:
        if not self.capturing_key:
            return False
        mods = 0
        if state & Gdk.ModifierType.CONTROL_MASK:
            mods |= 0x01
        if state & Gdk.ModifierType.SHIFT_MASK:
            mods |= 0x02
        if state & Gdk.ModifierType.ALT_MASK:
            mods |= 0x04
        if state & Gdk.ModifierType.SUPER_MASK:
            mods |= 0x08
        raw = a.key_from_evdev(keycode - 8, mods)
        if raw is None:
            # Plain modifier press or unsupported key: keep waiting.
            return True
        self._stop_key_capture()
        self._assign(raw)
        return True

    # -- profiles --------------------------------------------------------------

    def refresh_profiles(self) -> None:
        names = self.app.profiles.names
        self.loading = True
        try:
            model = self.profile_dropdown.get_model()
            current = [model.get_string(i) for i in range(model.get_n_items())] if model is not None else []
            if current != names:
                self.profile_dropdown.set_model(Gtk.StringList.new(names))
            active = self.app.settings.active_profile
            self.profile_dropdown.set_selected(names.index(active) if active in names else Gtk.INVALID_LIST_POSITION)
        finally:
            self.loading = False
        self.app.refresh_tray()

    def _ensure_profile(self) -> None:
        """First run: turn whatever the mouse has into the default profile."""
        if self.app.profiles.names:
            return
        profile = Profile.from_mouse("Predeterminado", self.config, self.keys, self.mouse.model.name)
        self.app.profiles.save(profile)
        self.app.settings.active_profile = profile.name
        self.app.settings.save()
        self.refresh_profiles()

    def _autosave_profile(self) -> None:
        name = self.app.settings.active_profile
        profile = self.app.profiles.get(name) if name else None
        # Never overwrite a profile of another model with this mouse's settings.
        if profile is not None and self.config is not None and profile.model == self.mouse.model.name:
            self.app.profiles.save(Profile.from_mouse(name, self.config, self.keys, profile.model))

    def _on_profile_selected(self, dropdown, _pspec) -> None:
        if self.loading:
            return
        index = dropdown.get_selected()
        names = self.app.profiles.names
        if 0 <= index < len(names) and names[index] != self.app.settings.active_profile:
            self.switch_profile(names[index])

    def switch_profile(self, name: str) -> bool:
        profile = self.app.profiles.get(name)
        if profile is None:
            return False
        previous = self.app.settings.active_profile
        # Don't autosave into the old profile while writing the new one.
        self.app.settings.active_profile = ""
        ok = self.apply(lambda m, c: profile.apply_to(m))
        self.app.settings.active_profile = name if ok else previous
        self.app.settings.save()
        if ok:
            self._load_keys()
            self.toast(f"Perfil «{name}» aplicado")
        self.refresh_profiles()
        return ok

    def _ask_name(self, heading: str, initial: str, on_done) -> None:
        dialog = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry(text=initial, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("ok", "Aceptar")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")

        def on_response(_dialog, response):
            name = entry.get_text().strip()
            if response == "ok" and name:
                on_done(name)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _profile_new(self) -> None:
        if self.config is None or self.mouse is None:
            self.toast("Conecta el ratón para crear un perfil")
            return
        config, keys, model = self.config, list(self.keys), self.mouse.model.name

        def create(name):
            name = self.app.profiles.unique_name(name)
            self.app.profiles.save(Profile.from_mouse(name, config, keys, model))
            self.app.settings.active_profile = name
            self.app.settings.save()
            self.refresh_profiles()

        self._ask_name("Nuevo perfil", self.app.profiles.unique_name("Perfil"), create)

    def _profile_duplicate(self) -> None:
        source = self.app.profiles.get(self.app.settings.active_profile)
        if source is None:
            return

        def duplicate(name):
            name = self.app.profiles.unique_name(name)
            self.app.profiles.save(Profile(**{**source.__dict__, "name": name}))
            self.app.settings.active_profile = name
            self.app.settings.save()
            self.refresh_profiles()

        self._ask_name("Duplicar perfil", self.app.profiles.unique_name(source.name), duplicate)

    def _profile_rename(self) -> None:
        old = self.app.settings.active_profile
        if old not in self.app.profiles.profiles:
            return

        def rename(new):
            if new != old:
                new = self.app.profiles.unique_name(new)
                self.app.profiles.rename(old, new)
                self.app.settings.active_profile = new
                self.app.settings.save()
            self.refresh_profiles()

        self._ask_name("Renombrar perfil", old, rename)

    def _profile_delete(self) -> None:
        name = self.app.settings.active_profile
        if name not in self.app.profiles.profiles:
            return
        if len(self.app.profiles.names) == 1:
            self.toast("No puedes borrar el único perfil")
            return
        dialog = Adw.AlertDialog(heading=f"¿Eliminar el perfil «{name}»?",
                                 body="El ratón se queda con su configuración actual.")
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("delete", "Eliminar")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_dialog, response):
            if response == "delete":
                self.app.profiles.delete(name)
                self.app.settings.active_profile = self.app.profiles.names[0]
                self.app.settings.save()
                self.refresh_profiles()

        dialog.connect("response", on_response)
        dialog.present(self)

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message))


class Application(Adw.Application):
    def __init__(self, start_hidden: bool = False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.start_hidden = start_hidden
        self.settings = Settings.load()
        self.profiles = ProfileStore()
        self.tray: TrayIcon | None = None
        self.window: MainWindow | None = None
        self.battery: p.Battery | None = None
        self.model_name = p.MG5_MODEL.name
        self._low_battery_notified = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        install_desktop_integration()
        Gtk.Window.set_default_icon_name(APP_ID)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        for name, callback in (("preferences", self.show_preferences), ("about", self.show_about),
                               ("quit", self.quit_app), ("show", self.show_window)):
            action = Gio.SimpleAction(name=name)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

        try:
            self.tray = TrayIcon("open-rk-mg5", APP_NAME, self.toggle_window)
            self.tray.start()
            # Keep running while the window is hidden in the tray.
            self.hold()
        except GLib.Error:
            self.tray = None

    def do_activate(self):
        first = self.window is None
        if first:
            self.window = MainWindow(self)
            self.window.refresh_profiles()
        if first and (self.start_hidden or self.settings.start_hidden) and self.tray is not None:
            return
        self.show_window()

    # -- window / tray ---------------------------------------------------------

    def show_window(self) -> None:
        if self.window is None:
            return
        token = self.tray.take_activation_token() if self.tray is not None else ""
        if token:
            # Lets the compositor raise and focus us despite focus-stealing prevention.
            self.window.set_startup_id(token)
        elif self.window.get_visible() and not self.window.is_active():
            # Without a token Wayland won't raise an existing window, but a re-mapped one is shown on top.
            self.window.set_visible(False)
        self.window.set_visible(True)
        self.window.present()

    def toggle_window(self) -> None:
        if self.window is not None and self.window.get_visible() and self.window.is_active():
            self.window.set_visible(False)
        else:
            self.show_window()

    def quit_app(self) -> None:
        if self.window is not None:
            self.window._disconnect()
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
            self.release()
        self.quit()

    def update_battery(self, battery: p.Battery) -> None:
        self.battery = battery
        low = battery.percent <= self.settings.low_battery_threshold and not battery.charging
        if low and self.settings.low_battery_notify and not self._low_battery_notified:
            notification = Gio.Notification.new("Batería baja en el RK MG5")
            notification.set_body(f"Queda un {battery.percent} %. Conecta el cable para cargarlo.")
            notification.set_icon(Gio.ThemedIcon.new("battery-caution"))
            self.send_notification("low-battery", notification)
            self._low_battery_notified = True
        elif not low:
            self._low_battery_notified = False
            self.withdraw_notification("low-battery")
        self.refresh_tray()

    def refresh_tray(self) -> None:
        if self.tray is None:
            return
        battery = self.battery
        low = battery is not None and battery.percent <= self.settings.low_battery_threshold and not battery.charging
        if battery is None:
            icon, battery_text = APP_ID, "Batería: desconocida"
        elif battery.charging:
            icon, battery_text = "battery-good-charging", f"Batería: {battery.percent} % (cargando)"
        elif low:
            icon, battery_text = "battery-caution", f"Batería baja: {battery.percent} %"
        else:
            icon, battery_text = APP_ID, f"Batería: {battery.percent} %"
        self.tray.set_icon(icon)
        self.tray.set_status("NeedsAttention" if low else "Active")
        active = self.settings.active_profile
        self.tray.set_tooltip(self.model_name, f"{battery_text}\nPerfil: {active or '—'}")

        items = [
            MenuItem("Mostrar panel", self.show_window, icon=APP_ID),
            MenuItem(battery_text, enabled=False, icon="battery-caution" if low else "battery-good"),
            MenuItem(separator=True),
            MenuItem("Perfiles", enabled=False),
        ]
        for name in self.profiles.names:
            items.append(MenuItem(name, lambda n=name: self._switch_from_tray(n), radio=True, checked=name == active))
        items += [
            MenuItem(separator=True),
            MenuItem("Preferencias", self._preferences_from_tray, icon="preferences-system"),
            MenuItem("Salir", self.quit_app, icon="application-exit"),
        ]
        self.tray.set_menu(items)

    def _switch_from_tray(self, name: str) -> None:
        if self.window is None or name == self.settings.active_profile:
            return
        if not self.window.switch_profile(name):
            notification = Gio.Notification.new("No se pudo cambiar de perfil")
            notification.set_body("El ratón está dormido o desconectado. Muévelo y vuelve a intentarlo.")
            self.send_notification("profile-error", notification)

    def _preferences_from_tray(self) -> None:
        self.show_window()
        self.show_preferences()

    # -- dialogs ---------------------------------------------------------------

    def show_preferences(self) -> None:
        dialog = Adw.PreferencesDialog(title="Preferencias")
        page = Adw.PreferencesPage()
        dialog.add(page)

        def switch(group, title, subtitle, attr):
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=getattr(self.settings, attr))
            row.connect("notify::active", lambda r, _p: self._set_setting(attr, r.get_active()))
            group.add(row)
            return row

        behaviour = Adw.PreferencesGroup(title="Comportamiento")
        tray_row = switch(behaviour, "Minimizar a la bandeja al cerrar",
                          "La app sigue en la bandeja vigilando la batería", "close_to_tray")
        switch(behaviour, "Iniciar con la sesión", "Arranca al entrar en el escritorio", "autostart")
        switch(behaviour, "Iniciar oculto en la bandeja", "No abre la ventana al arrancar", "start_hidden")
        if self.tray is None:
            tray_row.set_sensitive(False)
            tray_row.set_subtitle("No hay bandeja del sistema disponible")
        page.add(behaviour)

        battery = Adw.PreferencesGroup(title="Batería")
        switch(battery, "Avisar con batería baja", "Muestra una notificación y cambia el icono de la bandeja",
               "low_battery_notify")
        threshold = Adw.SpinRow.new_with_range(5, 50, 5)
        threshold.set_title("Umbral de batería baja (%)")
        threshold.set_value(self.settings.low_battery_threshold)
        threshold.connect("notify::value", lambda r, _p: self._set_setting("low_battery_threshold", int(r.get_value())))
        battery.add(threshold)
        page.add(battery)

        dialog.present(self.window)

    def _set_setting(self, attr: str, value) -> None:
        setattr(self.settings, attr, value)
        self.settings.save()
        if attr.startswith("low_battery"):
            self._low_battery_notified = False
            if self.battery is not None:
                self.update_battery(self.battery)

    def show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            developer_name="blzkz",
            version=__version__,
            comments=(
                "Panel de control de código abierto para el ratón Royal Kludge MG5 en Linux: "
                "DPI, sondeo, sensor, energía, botones y perfiles."
            ),
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 blzkz",
            developers=["blzkz", "Claude (Anthropic)"],
        )
        about.add_legal_section(
            "Marcas",
            None,
            Gtk.License.CUSTOM,
            "Royal Kludge y MG5 son marcas de sus respectivos propietarios. "
            "Este proyecto no está afiliado ni respaldado por Royal Kludge.",
        )
        about.add_legal_section(
            "Protocolo",
            None,
            Gtk.License.CUSTOM,
            "El protocolo HID se ha documentado a partir del configurador web oficial y de pruebas "
            "con el propio ratón, con fines de interoperabilidad.",
        )
        about.add_credit_section("Tecnologías", ["GTK 4", "libadwaita", "PyGObject"])
        about.present(self.window)


def install_desktop_integration() -> None:
    """When running from a source checkout, installs the .desktop file and icon under ~/.local.

    Wayland compositors pick the taskbar icon by matching the app id to an installed .desktop file.
    Distribution packages install these system-wide, so this only runs from the source tree.
    """
    source_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(source_root, "data")
    if not os.path.isdir(data_dir):
        return
    data_home = GLib.get_user_data_dir()
    icon_src = os.path.join(data_dir, "icons", f"{APP_ID}.svg")
    icon_dst = os.path.join(data_home, "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg")
    desktop_dst = os.path.join(data_home, "applications", f"{APP_ID}.desktop")
    with open(os.path.join(data_dir, f"{APP_ID}.desktop")) as f:
        desktop = f.read().replace("Exec=python3 -m rk_mg5", f"Exec=env PYTHONPATH={source_root} {sys.executable} -m rk_mg5")
    try:
        with open(icon_src, "rb") as f:
            icon = f.read()
        for path, content in ((icon_dst, icon), (desktop_dst, desktop.encode())):
            try:
                with open(path, "rb") as f:
                    if f.read() == content:
                        continue
            except OSError:
                pass
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(content)
    except OSError:
        pass


def main() -> int:
    argv = [arg for arg in sys.argv if arg != "--hidden"]
    return Application(start_hidden="--hidden" in sys.argv).run(argv)
