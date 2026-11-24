"""Draw the dashboard as a greyscale PNG sized for the Kobo screen."""

from __future__ import annotations

import hashlib
import io
import json
import logging
from dataclasses import asdict, dataclass, replace

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .layout import CLIMATE_ZONE_W, Button, Page, is_image

log = logging.getLogger(__name__)

# The panel shows 16 grey levels; stick to values that map exactly onto them.
BLACK = 0x00
DARK = 0x44
GREY = 0x88
WHITE = 0xFF

HEADER_H = 64
NAV_H = 90  # page bar at the bottom, only when there is more than one page
BORDER = 4
RADIUS = 14
MIN_FONT_SIZE = 14  # text shrinks to fit, but never below this

FONT_CANDIDATES = {
    False: [
        "DejaVuSans.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
    ],
    True: [
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/google-noto/NotoSans-Bold.ttf",
    ],
}
# Emoji icons: monochrome fonts only, colour emoji turn to mud on e-ink.
ICON_FONT_CANDIDATES = [
    "/usr/share/fonts/google-noto-emoji-fonts/NotoEmoji-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
    "/usr/share/fonts/gdouros-symbola/Symbola.ttf",
    "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf",
    "Symbola.ttf",
]
# Variation selectors ask for colour/text presentation; the mono fonts draw them as boxes.
_VARIATION_SELECTORS = dict.fromkeys(map(ord, "\ufe0e\ufe0f"))


@dataclass(frozen=True)
class ButtonView:
    """Everything that ends up on screen for one button. Also feeds the version hash."""

    id: str
    label: str
    value: str  # big text for value/climate/weather, small status line for toggle
    active: bool  # drawn inverted (black fill)
    unavailable: bool
    detail: str = ""  # climate: "Now 20.5°"; weather: "High 11° · Low 3°"
    preset: str = ""  # climate with presets or eco_preset: the current preset_mode
    icon: str = ""  # emoji or image path, between the label and the state
    top_icon: str = ""  # emoji or image path, above the label


@dataclass(frozen=True)
class Scene:
    title: str
    error: str | None
    buttons: tuple[ButtonView, ...]
    page: int = 0
    pages: int = 1

    def version(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]


def _format_value(raw, unit: str) -> str:
    if raw is None:
        return "?"
    if isinstance(raw, float):
        raw = f"{raw:.1f}".rstrip("0").rstrip(".")
    return f"{raw}{unit}"


def _pretty(text) -> str:
    return str(text).replace("_", " ").replace("-", " ").capitalize()


def _state_number(state: dict | None):
    """A sensor's state as a float, or None when it isn't a number."""
    try:
        return float((state or {}).get("state"))
    except (TypeError, ValueError):
        return None


def _toggle_view(button: Button, s, attrs: dict, unavailable: bool,
                 states: dict[str, dict]) -> ButtonView:
    compared = attrs.get(button.attribute) if button.attribute else s
    active = not unavailable and str(compared) in button.on_states
    if not button.show_status:
        value = ""
    elif unavailable:
        value = "unavailable"
    else:
        value = button.state_labels.get(str(compared), _pretty(compared))
        num = _state_number(states.get(button.extra_entity)) if button.extra_entity else None
        if num is not None:
            value += f" · {_format_value(num, button.extra_unit)}"
    return ButtonView(button.id, button.label, value, active, unavailable)


def _value_view(button: Button, s, attrs: dict, unavailable: bool,
                states: dict[str, dict]) -> ButtonView:
    raw = attrs.get(button.attribute) if button.attribute else s
    return ButtonView(button.id, button.label, _format_value(raw, button.unit), False, unavailable)


def _climate_view(button: Button, s, attrs: dict, unavailable: bool,
                  states: dict[str, dict]) -> ButtonView:
    target = attrs.get("temperature")
    detail = f"Now {_format_value(attrs.get('current_temperature'), '°')}"
    if attrs.get("current_humidity") is not None:
        detail += f"  {_format_value(attrs['current_humidity'], '%')}"
    if unavailable:
        value = "?"
    elif target is None:
        # No target while in some presets (e.g. "Energy heat"); show the preset instead,
        # unless the eco pill already says so.
        value = "--"
        if attrs.get("preset_mode") and attrs["preset_mode"] != button.eco_preset:
            detail += f" · {attrs['preset_mode']}"
    else:
        value = _format_value(float(target), "°")
    has_presets = button.presets or button.eco_preset
    preset = "" if unavailable or not has_presets else str(attrs.get("preset_mode") or "")
    return ButtonView(button.id, button.label, value, False, unavailable, detail, preset)


def _weather_view(button: Button, s, attrs: dict, unavailable: bool,
                  states: dict[str, dict]) -> ButtonView:
    value = "?" if unavailable else _format_value(attrs.get("temperature"), "°")
    parts = []
    for name, eid in (("High", button.high_entity), ("Low", button.low_entity)):
        num = _state_number(states.get(eid)) if eid else None
        if num is not None:
            parts.append(f"{name} {_format_value(num, '°')}")
    label = "unavailable" if unavailable else _pretty(s)
    return ButtonView(button.id, label, value, False, unavailable, " · ".join(parts))


_VIEWS = {
    "toggle": _toggle_view,
    "value": _value_view,
    "climate": _climate_view,
    "weather": _weather_view,
}


def _with_icons(view: ButtonView, button: Button, icon_state) -> ButtonView:
    if not (button.icon or button.top_icon):
        return view
    return replace(view, icon=button.icon_for("icon", icon_state),
                   top_icon=button.icon_for("top_icon", icon_state))


def button_view(button: Button, state: dict | None,
                states: dict[str, dict] | None = None) -> ButtonView:
    if state is None and button.entity:
        return _with_icons(ButtonView(button.id, button.label, "?", False, True), button, None)
    s = (state or {}).get("state")
    attrs = (state or {}).get("attributes", {})
    view = _VIEWS.get(button.display)
    if view is None:  # action
        return _with_icons(ButtonView(button.id, button.label, "", False, False), button, None)
    unavailable = s in ("unavailable", "unknown")
    # Icons follow the same value the toggle compares with on_states.
    icon_state = attrs.get(button.attribute) if (
        button.display == "toggle" and button.attribute and not unavailable) else s
    return _with_icons(view(button, s, attrs, unavailable, states or {}), button, icon_state)


def build_scene(page: Page, states: dict[str, dict], error: str | None = None,
                stale: bool = False, index: int = 0, count: int = 1) -> Scene:
    """`stale` means no states could be read: entity buttons are greyed out with '?'."""
    views = tuple(
        _with_icons(ButtonView(b.id, b.label, "?", False, True), b, None) if stale and b.entity
        else button_view(b, states.get(b.entity) if b.entity else None, states)
        for b in page.buttons
    )
    return Scene(page.title, error, views, index, count)


class Renderer:
    def __init__(self, width: int, height: int, font_path: str | None = None,
                 bold_font_path: str | None = None, icon_font_path: str | None = None):
        self.width = width
        self.height = height
        self.font_path = font_path
        self.bold_font_path = bold_font_path
        self.icon_font_path = icon_font_path
        self._fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
        self._icon_fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._images: dict[tuple, Image.Image | None] = {}

    def font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = self._load_font(size, bold)
        return self._fonts[key]

    def _load_font(self, size: int, bold: bool) -> ImageFont.FreeTypeFont:
        override = self.bold_font_path if bold else self.font_path
        for candidate in ([override] if override else []) + FONT_CANDIDATES[bold]:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
        return ImageFont.load_default(size)

    def icon_font(self, size: int) -> ImageFont.FreeTypeFont:
        if size not in self._icon_fonts:
            for candidate in ([self.icon_font_path] if self.icon_font_path else []) \
                    + ICON_FONT_CANDIDATES:
                try:
                    self._icon_fonts[size] = ImageFont.truetype(candidate, size)
                    break
                except OSError:
                    continue
            else:  # no emoji font: plain symbols still work in DejaVu
                self._icon_fonts[size] = self.font(size)
        return self._icon_fonts[size]

    # -- icons ------------------------------------------------------------

    def _icon_image(self, path: str, w: int, h: int, inverted: bool,
                    faded: bool) -> Image.Image | None:
        """The image scaled to fit w x h, on the card's background, in the panel's 16 greys."""
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            return None
        key = (path, mtime, w, h, inverted, faded)
        if key not in self._images:
            if len(self._images) > 64:
                self._images.clear()
            try:
                with Image.open(path) as src:
                    rgba = src.convert("RGBA")
            except (OSError, ValueError) as exc:
                log.warning("icon %s: %s", path, exc)
                self._images[key] = None
                return None
            flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            flat.alpha_composite(rgba)
            img = ImageOps.contain(flat.convert("L"), (w, h), Image.Resampling.LANCZOS)
            if inverted:  # dark icons stay readable on a black card
                img = ImageOps.invert(img)
            elif faded:  # unavailable: wash out to grey like the text
                img = img.point(lambda p: GREY + p * (WHITE - GREY) // 255)
            self._images[key] = img.point(lambda p: round(p / 17) * 17)
        return self._images[key]

    def _draw_icon(self, img, draw, box, spec: str, fg: int, inverted: bool, faded: bool):
        x, y, w, h = box
        if w <= 0 or h <= 0:
            return
        if is_image(spec):
            icon = self._icon_image(spec, w, h, inverted, faded)
            if icon is None:  # missing or unreadable file: say so on the card
                self._text_block(draw, box, "?", h, True, fg, max_lines=1)
                return
            img.paste(icon, (x + (w - icon.width) // 2, y + (h - icon.height) // 2))
            return
        text = spec.translate(_VARIATION_SELECTORS)
        size = max(h, MIN_FONT_SIZE)
        while True:
            font = self.icon_font(size)
            x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
            if (x1 - x0 <= w and y1 - y0 <= h) or size <= MIN_FONT_SIZE:
                break
            size -= 2
        # Centre the glyph's ink, not its line box: emoji sit oddly on the baseline.
        draw.text((x + (w - (x1 - x0)) // 2 - x0, y + (h - (y1 - y0)) // 2 - y0), text,
                  font=font, fill=fg)

    # -- text helpers -----------------------------------------------------

    def _wrap(self, draw, text: str, font, max_w: int) -> list[str]:
        words, lines, line = text.split(), [], ""
        for word in words:
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= max_w or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        return lines or [""]

    def _fit(self, draw, text, max_w, max_h, start, bold, max_lines=2):
        """Largest font (from `start` down) where `text` fits in max_lines within the box."""
        size = start
        while True:
            font = self.font(size, bold)
            lines = self._wrap(draw, text, font, max_w)
            line_h = font.getbbox("Ag")[3]
            fits_w = all(draw.textlength(l, font=font) <= max_w for l in lines)
            if (fits_w and len(lines) <= max_lines and line_h * len(lines) * 1.15 <= max_h) \
                    or size <= MIN_FONT_SIZE:
                return font, lines, int(line_h * 1.15)
            size -= 2

    def _text_block(self, draw, box, text, start, bold, fill, max_lines=2):
        x, y, w, h = box
        font, lines, line_h = self._fit(draw, text, w, h, start, bold, max_lines)
        top = y + (h - line_h * len(lines)) // 2
        for i, line in enumerate(lines):
            draw.text((x + w // 2, top + i * line_h + line_h // 2), line,
                      font=font, fill=fill, anchor="mm")

    # -- drawing ----------------------------------------------------------

    def _draw_button(self, img, draw, b: Button, v: ButtonView, pressed: bool = False):
        rect = (b.x, b.y, b.x + b.w - 1, b.y + b.h - 1)
        inverted = v.active != pressed
        edge = GREY if v.unavailable else BLACK
        fg = WHITE if inverted else (GREY if v.unavailable else BLACK)
        draw.rounded_rectangle(rect, RADIUS, fill=BLACK if inverted else WHITE,
                               outline=edge, width=BORDER)
        pad = 12
        if b.hint:
            hint_h = 26
            self._text_block(draw, (b.x + pad, b.y + b.h - pad - hint_h, b.w - 2 * pad, hint_h),
                             b.hint, 18, False, fg, max_lines=1)
            b = replace(b, h=b.h - hint_h)
        inner_w = b.w - 2 * pad
        if b.display == "climate" and b.presets:
            self._draw_presets(draw, b, v, fg, BLACK if inverted else WHITE)
            self._draw_climate(draw, replace(b, h=b.preset_top - b.y), v, fg)
        elif b.display == "climate":
            self._draw_climate(draw, b, v, fg)
        elif b.display == "weather":
            text_w = inner_w - 170
            self._text_block(draw, (b.x + pad, b.y + pad, text_w, (b.h - 2 * pad) * 3 // 5),
                             v.label, 34, True, fg, max_lines=1)
            if v.detail:
                self._text_block(draw, (b.x + pad, b.y + pad + (b.h - 2 * pad) * 3 // 5, text_w,
                                        (b.h - 2 * pad) * 2 // 5), v.detail, 24, False, fg,
                                 max_lines=1)
            self._text_block(draw, (b.x + b.w - pad - 170, b.y + pad, 170, b.h - 2 * pad),
                             v.value, 60, True, fg, max_lines=1)
        else:
            self._draw_stack(img, draw, b, v, fg, inverted)

    def _draw_stack(self, img, draw, b: Button, v: ButtonView, fg: int, inverted: bool):
        """toggle, value and action cards, top to bottom:
        [top_icon] label [icon] status-or-value."""
        pad = 12
        x, w = b.x + pad, b.w - 2 * pad
        top, bottom = b.y + pad, b.y + b.h - pad
        icons = bool(v.top_icon) + bool(v.icon)
        icon_h = 0
        if icons:
            icon_h = b.icon_size or min(64, round((bottom - top) * (0.3 if icons == 1 else 0.22)))
            icon_h = max(0, min(icon_h, (bottom - top - 40) // icons))  # leave room for text
        if v.top_icon:
            self._draw_icon(img, draw, (x, top, w, icon_h), v.top_icon, fg, inverted,
                            v.unavailable)
            top += icon_h
        if b.display == "value":
            label_h = min(44, b.h // 3)
            self._text_block(draw, (x, top, w, label_h), v.label, 26, False, fg, max_lines=1)
            top += label_h
            if v.icon:
                self._draw_icon(img, draw, (x, top, w, icon_h), v.icon, fg, inverted,
                                v.unavailable)
                top += icon_h
            self._text_block(draw, (x, top, w, bottom - top), v.value, 64, True, fg,
                             max_lines=1)
            return
        status_h = min(36, b.h // 4) if b.display == "toggle" and v.value else 0
        label_h = bottom - top - status_h - (icon_h if v.icon else 0)
        self._text_block(draw, (x, top, w, label_h), v.label, 34, True, fg)
        if v.icon:
            self._draw_icon(img, draw, (x, top + label_h, w, icon_h), v.icon, fg, inverted,
                            v.unavailable)
        if status_h:
            self._text_block(draw, (x, bottom - status_h, w, status_h), v.value, 24, False,
                             fg, max_lines=1)

    def _draw_climate(self, draw, b: Button, v: ButtonView, fg: int):
        """Name and current temperature on the left; [-] target [+] on the right."""
        pad = 12
        zones_x = b.x + b.w - 3 * CLIMATE_ZONE_W
        left_w = zones_x - b.x - 2 * pad
        inner_h = b.h - 2 * pad
        self._text_block(draw, (b.x + pad + 6, b.y + pad, left_w, inner_h * 11 // 20),
                         v.label, 30, True, fg)
        detail_y, detail_h = b.y + pad + inner_h * 11 // 20, inner_h * 9 // 20
        detail_w = left_w
        if b.eco_preset:
            # The eco pill sits at the right end of the "Now 20.5°" line, filled when on.
            pill_w, pill_h = 76, min(40, detail_h)
            px1 = zones_x - pad
            py0 = detail_y + (detail_h - pill_h) // 2
            pill = (px1 - pill_w, py0, px1, py0 + pill_h)
            on = v.preset == b.eco_preset
            bg = BLACK if fg == WHITE else WHITE
            draw.rounded_rectangle(pill, pill_h // 2, fill=fg if on else bg, outline=fg, width=3)
            self._text_block(draw, (pill[0] + 4, pill[1] + 2, pill_w - 8, pill_h - 4),
                             b.eco_label, 22, True, bg if on else fg, max_lines=1)
            detail_w = left_w - pill_w - 8
        self._text_block(draw, (b.x + pad + 6, detail_y, detail_w, detail_h),
                         v.detail, 24, False, fg, max_lines=1)
        minus = (zones_x, b.y + pad, zones_x + CLIMATE_ZONE_W - 8, b.y + b.h - pad)
        plus = (b.x + b.w - CLIMATE_ZONE_W + 4, b.y + pad, b.x + b.w - pad, b.y + b.h - pad)
        # No target to change (e.g. in eco mode): grey out - and +.
        zone_fg = GREY if v.value in ("--", "?") and fg == BLACK else fg
        for rect, sign in ((minus, "−"), (plus, "+")):  # noqa: RUF001 (a real minus sign)
            draw.rounded_rectangle(rect, 10, outline=zone_fg, width=3)
            draw.text(((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2), sign,
                      font=self.font(56, True), fill=zone_fg, anchor="mm")
        self._text_block(draw, (zones_x + CLIMATE_ZONE_W - 4, b.y + pad, CLIMATE_ZONE_W + 4,
                                inner_h), v.value, 40, True, fg, max_lines=1)

    def _draw_presets(self, draw, b: Button, v: ButtonView, fg: int, bg: int):
        """The preset row of a climate card: the current preset is drawn filled."""
        for preset, rect in b.preset_rects():
            on = preset == v.preset
            draw.rounded_rectangle(rect, 10, fill=fg if on else bg, outline=fg, width=3)
            x0, y0, x1, y1 = rect
            self._text_block(draw, (x0 + 6, y0 + 6, x1 - x0 - 12, y1 - y0 - 12),
                             b.preset_label(preset), 30, True, bg if on else fg, max_lines=1)

    def _draw_nav(self, draw, scene: Scene, pressed: str | None):
        top = self.height - NAV_H
        draw.line((0, top, self.width, top), fill=BLACK, width=3)
        third = self.width // 3
        for side, x0, direction in (("prev", 0, -1), ("next", self.width - third, 1)):
            inv = pressed == f"_nav_{side}"
            rect = (x0 + 16, top + 14, x0 + third - 16, self.height - 14)
            draw.rounded_rectangle(rect, RADIUS, fill=BLACK if inv else WHITE,
                                   outline=BLACK, width=BORDER)
            # Drawn, not a glyph: the usual fonts have no arrow characters.
            cx, cy, r = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2, 18
            tip, back = cx + direction * r, cx - direction * r
            draw.polygon([(tip, cy), (back, cy - r), (back, cy + r)],
                         fill=WHITE if inv else BLACK)
        draw.text((self.width // 2, top + NAV_H // 2), f"{scene.page + 1} / {scene.pages}",
                  font=self.font(30, True), fill=BLACK, anchor="mm")

    def render(self, page: Page, scene: Scene, pressed: str | None = None) -> Image.Image:
        img = Image.new("L", (self.width, self.height), WHITE)
        draw = ImageDraw.Draw(img)

        draw.rectangle((0, 0, self.width, HEADER_H - 1), fill=BLACK)
        draw.text((20, HEADER_H // 2), scene.title, font=self.font(36, True),
                  fill=WHITE, anchor="lm")
        if scene.pages > 1:
            draw.text((self.width - 20, HEADER_H // 2), f"{scene.page + 1}/{scene.pages}",
                      font=self.font(26), fill=WHITE, anchor="rm")

        for b, v in zip(page.buttons, scene.buttons, strict=True):
            self._draw_button(img, draw, b, v, pressed=(b.id == pressed))

        bottom = self.height
        if scene.pages > 1:
            self._draw_nav(draw, scene, pressed)
            bottom -= NAV_H

        if scene.error:
            bar_h = 56
            top = bottom - bar_h
            draw.rectangle((0, top, self.width, bottom), fill=DARK)
            self._text_block(draw, (12, top, self.width - 24, bar_h), scene.error,
                             22, False, WHITE, max_lines=2)
        return img

    def render_error(self, message: str) -> Image.Image:
        img = Image.new("L", (self.width, self.height), WHITE)
        draw = ImageDraw.Draw(img)
        self._text_block(draw, (40, self.height // 2 - 60, self.width - 80, 60),
                         "Dashboard error", 40, True, BLACK, max_lines=1)
        self._text_block(draw, (40, self.height // 2 + 10, self.width - 80, 200),
                         message, 24, False, BLACK, max_lines=6)
        return img


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
