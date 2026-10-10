"""System tray icon via the StatusNotifierItem + com.canonical.dbusmenu D-Bus protocols.

GTK4 has no tray API; KDE Plasma (and GNOME with the AppIndicator extension) host
StatusNotifierItems, so we speak the protocol directly with Gio.
"""

import os
from dataclasses import dataclass, field
from typing import Callable

from gi.repository import Gio, GLib

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>
"""

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"


@dataclass
class MenuItem:
    label: str = ""
    callback: Callable[[], None] | None = None
    enabled: bool = True
    separator: bool = False
    radio: bool = False
    checked: bool = False
    icon: str = ""
    children: list["MenuItem"] = field(default_factory=list)


def _props(item: MenuItem) -> dict:
    if item.separator:
        return {"type": GLib.Variant("s", "separator")}
    props = {"label": GLib.Variant("s", item.label), "enabled": GLib.Variant("b", item.enabled)}
    if item.icon:
        props["icon-name"] = GLib.Variant("s", item.icon)
    if item.radio:
        props["toggle-type"] = GLib.Variant("s", "radio")
        props["toggle-state"] = GLib.Variant("i", 1 if item.checked else 0)
    if item.children:
        props["children-display"] = GLib.Variant("s", "submenu")
    return props


class TrayIcon:
    def __init__(self, app_id: str, title: str, on_activate: Callable[[], None]):
        self.app_id = app_id
        self.title = title
        self.on_activate = on_activate
        self.icon_name = "input-mouse"
        self.tooltip_title = title
        self.tooltip_text = ""
        self.status = "Active"
        self.revision = 1
        self.items: dict[int, MenuItem] = {}
        self.root = MenuItem(children=[])
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.connection: Gio.DBusConnection | None = None
        self._registrations = []
        self._name_id = 0
        self._watcher_id = 0

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._registrations = [
            self.connection.register_object(ITEM_PATH, sni, self._on_sni_call, self._on_sni_property, None),
            self.connection.register_object(MENU_PATH, menu, self._on_menu_call, self._on_menu_property, None),
        ]
        self._name_id = Gio.bus_own_name_on_connection(
            self.connection, self.bus_name, Gio.BusNameOwnerFlags.NONE, None, None
        )
        # Re-register whenever the tray host (e.g. plasmashell) restarts.
        self._watcher_id = Gio.bus_watch_name_on_connection(
            self.connection, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
            lambda *_: self._register_with_watcher(), None,
        )

    def stop(self) -> None:
        if self.connection is None:
            return
        for registration in self._registrations:
            self.connection.unregister_object(registration)
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
        if self._watcher_id:
            Gio.bus_unwatch_name(self._watcher_id)
        self.connection = None

    def _register_with_watcher(self) -> None:
        self.connection.call(
            "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
            "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.bus_name,)), None,
            Gio.DBusCallFlags.NONE, -1, None, None,
        )

    # -- public API ------------------------------------------------------------

    def set_icon(self, icon_name: str) -> None:
        if icon_name != self.icon_name:
            self.icon_name = icon_name
            self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewIcon")

    def set_tooltip(self, title: str, text: str) -> None:
        if (title, text) != (self.tooltip_title, self.tooltip_text):
            self.tooltip_title, self.tooltip_text = title, text
            self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip")

    def set_status(self, status: str) -> None:
        """"Active", "Passive" or "NeedsAttention"."""
        if status != self.status:
            self.status = status
            self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewStatus", GLib.Variant("(s)", (status,)))

    def set_menu(self, items: list[MenuItem]) -> None:
        self.root = MenuItem(children=items)
        self.revision += 1
        self._emit(MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated", GLib.Variant("(ui)", (self.revision, 0)))

    # -- D-Bus plumbing --------------------------------------------------------

    def _emit(self, path, interface, signal, params=None) -> None:
        if self.connection is not None:
            self.connection.emit_signal(None, path, interface, signal, params)

    def _on_sni_property(self, _conn, _sender, _path, _iface, name):
        values = {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", self.app_id),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", self.status),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self.icon_name),
            "IconThemePath": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", self.icon_name),
            "OverlayIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (self.icon_name, [], self.tooltip_title, self.tooltip_text)),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(name)

    def _on_sni_call(self, _conn, _sender, _path, _iface, method, _params, invocation) -> None:
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self.on_activate)
        invocation.return_value(None)

    def _on_menu_property(self, _conn, _sender, _path, _iface, name):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(name)

    def _layout(self, item: MenuItem, item_id: int, next_id: list[int]) -> GLib.Variant:
        self.items[item_id] = item
        children = []
        for child in item.children:
            next_id[0] += 1
            children.append(self._layout(child, next_id[0], next_id))
        props = _props(item) if item_id else {"children-display": GLib.Variant("s", "submenu")}
        return GLib.Variant("(ia{sv}av)", (item_id, props, children))

    def _on_menu_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method == "GetLayout":
            self.items = {}
            layout = self._layout(self.root, 0, [0])
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("u", self.revision), layout))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            if not self.items:
                self._layout(self.root, 0, [0])
            result = [(i, _props(self.items[i])) for i in (ids or self.items) if i in self.items and i]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            props = _props(self.items.get(item_id, MenuItem()))
            invocation.return_value(GLib.Variant("(v)", (props.get(name, GLib.Variant("s", "")),)))
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            self._dispatch(item_id, event_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            (events,) = params.unpack()
            for item_id, event_id, _data, _ts in events:
                self._dispatch(item_id, event_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _dispatch(self, item_id: int, event_id: str) -> None:
        item = self.items.get(item_id)
        if event_id == "clicked" and item is not None and item.callback is not None and item.enabled:
            GLib.idle_add(item.callback)
