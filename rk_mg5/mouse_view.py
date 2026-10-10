"""Top-down drawing of the RK MG5 with Synapse-style callouts for its buttons."""

import math
import random
from dataclasses import dataclass

import cairo
from gi.repository import GObject, Gtk

# Mouse drawing units: 60 wide x 112 long, origin at the top-left of its bounding box.
MOUSE_W = 60.0
MOUSE_H = 112.0

BODY_COLOR = (0.95, 0.95, 0.94)
BODY_EDGE = (0.78, 0.78, 0.77)
SPECKLE_COLORS = [(0.55, 0.55, 0.53), (0.68, 0.66, 0.62), (0.45, 0.47, 0.5)]
SEAM_COLOR = (0.18, 0.18, 0.18)
ACCENT = (0.18, 0.77, 0.71)
LINE_COLOR = (0.45, 0.45, 0.45)


@dataclass
class Callout:
    index: int  # key index in the mouse's key table
    anchor: tuple[float, float]  # point on the mouse, in mouse units
    side: str  # "left" or "right"
    row: float  # label vertical position, in mouse units


CALLOUTS = [
    Callout(0, (16, 20), "left", 8),
    Callout(2, (30, 21), "left", 30),
    Callout(3, (1.5, 47), "left", 52),
    Callout(4, (1.5, 58), "left", 72),
    Callout(1, (44, 20), "right", 8),
    Callout(5, (30, 92), "right", 92),
]


def _outline(cr: cairo.Context) -> None:
    """Symmetric body outline: wide front, slight waist, rounded rear."""
    right = [
        ((46, 0), (57, 5), (57.5, 24)),
        ((58, 40), (55.5, 50), (55.5, 62)),
        ((55.5, 80), (56.5, 94), (51, 104)),
        ((46, 112), (38, 112), (30, 112)),
    ]
    cr.move_to(30, 0)
    for c1, c2, end in right:
        cr.curve_to(*c1, *c2, *end)
    # Mirror back up the left side.
    points = [(30, 0)] + [p for seg in right for p in seg]
    for i in range(len(right) - 1, -1, -1):
        c1, c2, _end = right[i]
        start = points[i * 3]
        cr.curve_to(MOUSE_W - c2[0], c2[1], MOUSE_W - c1[0], c1[1], MOUSE_W - start[0], start[1])
    cr.close_path()


def _left_button(cr: cairo.Context) -> None:
    cr.move_to(29.4, 1)
    cr.line_to(29.4, 44.5)
    cr.curve_to(20, 44, 10, 41, 4.2, 37)
    cr.curve_to(3, 20, 6, 6, 20, 1.6)
    cr.close_path()


def _right_button(cr: cairo.Context) -> None:
    cr.save()
    cr.translate(MOUSE_W, 0)
    cr.scale(-1, 1)
    _left_button(cr)
    cr.restore()


def _rounded_rect(cr, x, y, w, h, r) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _wheel(cr) -> None:
    _rounded_rect(cr, 26.3, 11, 7.4, 18, 3.2)


def _side_forward(cr) -> None:
    _rounded_rect(cr, -1.6, 41, 3.6, 11, 1.6)


def _side_back(cr) -> None:
    _rounded_rect(cr, -1.6, 53, 3.6, 11, 1.6)


def _dpi_marker(cr) -> None:
    cr.new_sub_path()
    cr.arc(30, 92, 4.5, 0, 2 * math.pi)


REGIONS = {0: _left_button, 1: _right_button, 2: _wheel, 3: _side_forward, 4: _side_back, 5: _dpi_marker}


class MouseView(Gtk.Overlay):
    """Draws the mouse and places a clickable label for each button around it."""

    __gsignals__ = {"button-selected": (GObject.SignalFlags.RUN_FIRST, None, (int,))}

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("mouse-stage")
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self._draw)
        self.area.connect("resize", lambda *_: self._layout())
        self.set_child(self.area)

        self.fixed = Gtk.Fixed(can_target=True)
        self.add_overlay(self.fixed)

        self.labels: dict[int, Gtk.Button] = {}
        for callout in CALLOUTS:
            button = Gtk.Button(css_classes=["callout", callout.side])
            button.connect("clicked", self._on_label_clicked, callout.index)
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", self._on_hover, callout.index)
            hover.connect("leave", self._on_hover, None)
            button.add_controller(hover)
            self.fixed.put(button, 0, 0)
            self.labels[callout.index] = button

        self.show_callouts = True
        self.selected: int | None = None
        self.hovered: int | None = None
        self.badge: tuple[str, tuple[float, float, float]] | None = None
        rng = random.Random(5)
        self.speckles = [
            (rng.uniform(0, MOUSE_W), rng.uniform(0, MOUSE_H), rng.uniform(0.15, 0.45), rng.choice(SPECKLE_COLORS))
            for _ in range(900)
        ]

    # -- public API ------------------------------------------------------------

    def set_labels(self, names: dict[int, str]) -> None:
        for index, name in names.items():
            self.labels[index].set_label(name)
        self._layout()

    def set_callouts_visible(self, visible: bool) -> None:
        self.show_callouts = visible
        self.fixed.set_visible(visible)
        self.area.queue_draw()

    def set_selected(self, index: int | None) -> None:
        self.selected = index
        for i, button in self.labels.items():
            if i == index:
                button.add_css_class("selected")
            else:
                button.remove_css_class("selected")
        self.area.queue_draw()

    def set_badge(self, text: str | None, rgb: tuple[int, int, int] = (255, 255, 255)) -> None:
        self.badge = (text, tuple(c / 255 for c in rgb)) if text else None
        self.area.queue_draw()

    # -- geometry --------------------------------------------------------------

    def _geometry(self):
        width, height = self.area.get_width(), self.area.get_height()
        scale = min(height * 0.78 / MOUSE_H, width * 0.34 / MOUSE_W)
        ox = (width - MOUSE_W * scale) / 2
        oy = (height - MOUSE_H * scale) / 2 - (14 if self.badge else 0)
        return scale, ox, oy

    def _to_px(self, point, geometry):
        scale, ox, oy = geometry
        return ox + point[0] * scale, oy + point[1] * scale

    def _layout(self) -> None:
        geometry = self._geometry()
        scale, ox, _oy = geometry
        gap = 56
        for callout in CALLOUTS:
            button = self.labels[callout.index]
            _min, natural, _b1, _b2 = button.measure(Gtk.Orientation.HORIZONTAL, -1)
            _minh, height, _b3, _b4 = button.measure(Gtk.Orientation.VERTICAL, natural)
            _x, y = self._to_px((0, callout.row), geometry)
            if callout.side == "left":
                x = ox - gap - natural
            else:
                x = ox + MOUSE_W * scale + gap
            self.fixed.move(button, max(0, x), y - height / 2)
        self.area.queue_draw()

    # -- drawing ---------------------------------------------------------------

    def _draw(self, _area, cr: cairo.Context, width: int, height: int) -> None:
        geometry = self._geometry()
        scale, ox, oy = geometry

        cr.save()
        cr.translate(ox, oy)
        cr.scale(scale, scale)

        # Soft shadow.
        cr.save()
        cr.translate(1.5, 3)
        _outline(cr)
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.fill()
        cr.restore()

        # Body with a gentle radial shading and recycled-plastic speckles.
        _outline(cr)
        gradient = cairo.RadialGradient(30, 45, 4, 30, 55, 70)
        gradient.add_color_stop_rgb(0, *BODY_COLOR)
        gradient.add_color_stop_rgb(1, *BODY_EDGE)
        cr.set_source(gradient)
        cr.fill_preserve()
        cr.save()
        cr.clip()
        for x, y, r, color in self.speckles:
            cr.set_source_rgba(*color, 0.7)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.fill()
        cr.restore()
        cr.new_path()

        # Highlight the hovered / selected button.
        for index, alpha in ((self.hovered, 0.28), (self.selected, 0.45)):
            if index is not None and self.show_callouts:
                REGIONS[index](cr)
                cr.set_source_rgba(*ACCENT, alpha)
                cr.fill()

        # Seams between the main buttons.
        cr.set_line_width(0.45)
        cr.set_source_rgb(*SEAM_COLOR)
        cr.move_to(30, 1)
        cr.line_to(30, 45)
        cr.stroke()
        cr.move_to(4.2, 37)
        cr.curve_to(10, 41, 20, 44.6, 30, 45)
        cr.curve_to(40, 44.6, 50, 41, 55.8, 37)
        cr.stroke()

        # Scroll wheel.
        _wheel(cr)
        cr.set_source_rgb(0.08, 0.08, 0.08)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(0.4)
        for y in range(13, 28, 2):
            cr.move_to(27.5, y)
            cr.line_to(32.5, y)
        cr.stroke()

        # Side buttons poke out of the left edge.
        for draw in (_side_forward, _side_back):
            draw(cr)
            cr.set_source_rgb(0.86, 0.86, 0.85)
            cr.fill_preserve()
            cr.set_source_rgb(*SEAM_COLOR)
            cr.set_line_width(0.35)
            cr.stroke()

        # The DPI button sits underneath: show it as a dashed marker.
        if self.show_callouts:
            _dpi_marker(cr)
            cr.set_source_rgba(*SEAM_COLOR, 0.6)
            cr.set_line_width(0.4)
            cr.set_dash([1.2, 0.9])
            cr.stroke()
            cr.set_dash([])

        cr.restore()

        if self.show_callouts:
            self._draw_callout_lines(cr, geometry)

        if self.badge:
            self._draw_badge(cr, geometry, width)

    def _draw_callout_lines(self, cr, geometry) -> None:
        scale, ox, _oy = geometry
        cr.set_line_width(1)
        for callout in CALLOUTS:
            button = self.labels[callout.index]
            ax, ay = self._to_px(callout.anchor, geometry)
            _x, ly = self._to_px((0, callout.row), geometry)
            active = callout.index in (self.selected, self.hovered)
            cr.set_source_rgb(*(ACCENT if active else LINE_COLOR))
            if callout.side == "left":
                start = ox - 50
                elbow = ox - 14
            else:
                start = ox + MOUSE_W * scale + 50
                elbow = ox + MOUSE_W * scale + 14
            cr.move_to(start, ly)
            cr.line_to(elbow, ly)
            cr.line_to(ax, ay)
            cr.stroke()
            for x, y, r in ((start, ly, 2.5), (ax, ay, 3)):
                cr.arc(x, y, r, 0, 2 * math.pi)
                cr.fill()
            button.set_visible(True)

    def _draw_badge(self, cr, geometry, width) -> None:
        scale, _ox, oy = geometry
        text, rgb = self.badge
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(26)
        extents = cr.text_extents(text)
        y = oy + MOUSE_H * scale + 44
        dot = 8
        total = extents.x_advance + dot * 2 + 12
        x = (width - total) / 2
        cr.set_source_rgb(*rgb)
        cr.arc(x + dot, y - extents.height / 2 + 1, dot, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(0.93, 0.93, 0.93)
        cr.move_to(x + dot * 2 + 12, y)
        cr.show_text(text)

    # -- interaction -----------------------------------------------------------

    def _on_label_clicked(self, _button, index: int) -> None:
        self.set_selected(index)
        self.emit("button-selected", index)

    def _on_hover(self, _controller, *args) -> None:
        # "enter" passes (x, y, index); "leave" passes (index=None).
        self.hovered = args[-1]
        self.area.queue_draw()
