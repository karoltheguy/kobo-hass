"""Button layout (layout.yaml), pages and hit testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DISPLAY_KINDS = ("toggle", "value", "action", "climate", "weather")
DEFAULT_ON_STATES = ("on", "open", "unlocked", "playing", "home", "heat", "cool", "heat_cool", "auto")
DEFAULT_LONG_MS = 3000
# Longer "holds" than this are treated as a glitch (e.g. a missed finger lift
# on the Kobo), never as a long press: a long press can unlock a door.
MAX_LONG_MS = 10000

# Width of each of the - and + tap zones on a climate card.
CLIMATE_ZONE_W = 90
# Height of the row of preset buttons at the bottom of a climate card with presets.
PRESET_ROW_H = 110

# Display kinds that can show `icon` / `top_icon`.
ICON_DISPLAYS = ("toggle", "value", "action")
# An icon ending in one of these is an image file (relative to layout.yaml);
# anything else is text, usually an emoji.
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def is_image(spec: str) -> bool:
    return spec.lower().endswith(IMAGE_EXTS)


@dataclass
class Button:
    id: str
    x: int
    y: int
    w: int
    h: int
    label: str
    entity: str | None = None
    action: str | None = None  # "domain.service", e.g. "light.toggle"
    data: dict = field(default_factory=dict)
    display: str = "action"  # one of DISPLAY_KINDS
    # toggle: compare this attribute (not the state) with on_states.
    # value: show this attribute instead of the state.
    attribute: str | None = None
    unit: str = ""
    on_states: tuple[str, ...] = DEFAULT_ON_STATES
    show_status: bool = True  # toggle: draw the status line under the label
    state_labels: dict = field(default_factory=dict)  # e.g. {"off": "Closed"}
    # A second entity shown after the status, e.g. a lock's battery level.
    extra_entity: str | None = None
    extra_unit: str = ""
    # Held for at least long_ms: run long_action instead of action.
    long_action: str | None = None
    long_data: dict = field(default_factory=dict)
    long_ms: int = DEFAULT_LONG_MS
    hint: str = ""  # small print at the bottom of the card, e.g. "Hold 3 s to unlock"
    # climate: degrees per tap (default: the entity's target_temp_step, else 0.5)
    step: float | None = None
    # climate: preset modes shown as a row of buttons inside the card, e.g. (away, home)
    presets: tuple[str, ...] = ()
    preset_labels: dict = field(default_factory=dict)  # e.g. {"away": "Out"}
    # climate: an on/off eco mode. Tapping the name side of the card switches
    # between eco_preset and normal_preset; a pill shows which one is on.
    eco_preset: str | None = None
    normal_preset: str = "none"
    eco_label: str = "Eco"
    # weather: sensors for today's high and low
    high_entity: str | None = None
    low_entity: str | None = None
    # An emoji or an image path, as {state: icon}; "default" is used for other
    # states (and on action cards). icon sits between the label and the state,
    # top_icon above the label. icon_size: their height in px (default: auto).
    icon: dict = field(default_factory=dict)
    top_icon: dict = field(default_factory=dict)
    icon_size: int | None = None

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    @property
    def preset_top(self) -> int:
        """Climate card: y where the preset row starts (the card's bottom without presets)."""
        return self.y + self.h - (PRESET_ROW_H if self.presets else 0)

    def preset_rects(self) -> list[tuple[str, tuple[int, int, int, int]]]:
        """Climate card: (preset, (x0, y0, x1, y1)) for each preset button, left to right."""
        if not self.presets:
            return []
        pad, gap = 12, 12
        n = len(self.presets)
        each = (self.w - 2 * pad - (n - 1) * gap) / n
        y0, y1 = self.preset_top, self.y + self.h - pad
        return [(p, (round(self.x + pad + i * (each + gap)), y0,
                     round(self.x + pad + i * (each + gap) + each), y1))
                for i, p in enumerate(self.presets)]

    def preset_at(self, x: int, y: int) -> str | None:
        """Climate card: the preset under (x, y); the gaps around them count as the nearest."""
        if not self.presets or y < self.preset_top:
            return None
        rects = self.preset_rects()
        return min(rects, key=lambda r: 0 if r[1][0] <= x <= r[1][2]
                   else min(abs(x - r[1][0]), abs(x - r[1][2])))[0]

    def eco_hit(self, x: int, y: int) -> bool:
        """Climate card with eco_preset: (x, y) is on the name side, left of the [-] zone."""
        return (self.eco_preset is not None and y < self.preset_top
                and x < self.x + self.w - 3 * CLIMATE_ZONE_W)

    def icon_for(self, which: str, state) -> str:
        """The icon (or top_icon) to show for `state`, or "" for none."""
        icons = getattr(self, which)
        return icons.get(str(state), icons.get("default", "")) if state is not None \
            else icons.get("default", "")

    def preset_label(self, preset: str) -> str:
        return self.preset_labels.get(preset, preset.replace("_", " ").capitalize())

    def climate_zone(self, x: int) -> int:
        """For a climate card: -1 for the minus zone, +1 for plus, 0 elsewhere."""
        if x >= self.x + self.w - CLIMATE_ZONE_W:
            return 1
        if x >= self.x + self.w - 3 * CLIMATE_ZONE_W and x < self.x + self.w - 2 * CLIMATE_ZONE_W:
            return -1
        return 0

    def service(self, long: bool = False) -> tuple[str, str] | None:
        action = self.long_action if long and self.long_action else self.action
        if not action:
            return None
        domain, _, service = action.partition(".")
        return domain, service

    def service_data(self, long: bool = False) -> dict:
        data = dict(self.long_data if long and self.long_action else self.data)
        if self.entity and "entity_id" not in data:
            data["entity_id"] = self.entity
        return data

    def entities(self) -> set[str]:
        return {e for e in (self.entity, self.extra_entity, self.high_entity, self.low_entity) if e}


@dataclass
class Page:
    title: str
    buttons: list[Button]

    def hit(self, x: int, y: int) -> Button | None:
        for button in self.buttons:
            if button.contains(x, y):
                return button
        return None


@dataclass
class Layout:
    pages: list[Page]

    @property
    def title(self) -> str:
        return self.pages[0].title

    @property
    def buttons(self) -> list[Button]:
        """Every button on every page."""
        return [b for p in self.pages for b in p.buttons]

    def page(self, index: int) -> Page:
        return self.pages[index % len(self.pages)]

    def entities(self) -> list[str]:
        return sorted({e for b in self.buttons for e in b.entities()})


class LayoutError(ValueError):
    pass


def _check_action(bid: str, key: str, action) -> None:
    if action and "." not in action:
        raise LayoutError(f"{bid}: {key} must look like 'domain.service', got {action!r}")


def _parse_icon(bid: str, key: str, value, base_dir: Path | None) -> dict:
    """`icon: 💡`, `icon: bulb.png` or `icon: {on: 💡, off: 🌑, default: ...}`."""
    if value is None or value == "":
        return {}
    icons = value if isinstance(value, dict) else {"default": value}
    out = {}
    for state, spec in icons.items():
        spec = str(spec).strip()
        if is_image(spec) and base_dir is not None:
            path = Path(spec) if Path(spec).is_absolute() else base_dir / spec
            if not path.is_file():
                raise LayoutError(f"{bid}: {key} image not found: {path}")
            spec = str(path)
        out[str(state)] = spec
    return out


def _validate_button(bid: str, item: dict, display: str, w: int, h: int) -> None:
    """Reject options that don't fit the card's display kind or size."""
    if display not in DISPLAY_KINDS:
        raise LayoutError(f"{bid}: display must be one of {DISPLAY_KINDS}, got {display!r}")
    _check_action(bid, "action", item.get("action"))
    _check_action(bid, "long_action", item.get("long_action"))
    if display in ("climate", "weather") and not item.get("entity"):
        raise LayoutError(f"{bid}: display {display!r} needs an entity")
    min_w = 3 * CLIMATE_ZONE_W + 120
    if display == "climate" and w < min_w:
        raise LayoutError(f"{bid}: a climate card needs to be at least {min_w} px wide")
    for key in ("presets", "eco_preset"):
        if item.get(key) and display != "climate":
            raise LayoutError(f"{bid}: {key!r} only works on a climate card")
    presets = item.get("presets") or []
    if not isinstance(presets, list):
        raise LayoutError(f"{bid}: 'presets' must be a list, e.g. [away, home, sleep]")
    for key in ("icon", "top_icon"):
        if item.get(key) and display not in ICON_DISPLAYS:
            raise LayoutError(f"{bid}: {key!r} only works on {', '.join(ICON_DISPLAYS)} cards")
    if presets and h < PRESET_ROW_H + 110:
        raise LayoutError(f"{bid}: a climate card with presets needs to be at least "
                          f"{PRESET_ROW_H + 110} px tall")


def _parse_button(item: dict, where: str, base_dir: Path | None = None) -> Button:
    try:
        bid = str(item["id"])
        x, y, w, h = (int(v) for v in item["rect"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LayoutError(f"{where}: needs 'id' and 'rect: [x, y, w, h]' ({exc})") from exc
    display = item.get("display", "action")
    _validate_button(bid, item, display, w, h)
    action, long_action = item.get("action"), item.get("long_action")
    presets = item.get("presets") or []
    eco_preset = item.get("eco_preset")
    on_states = item.get("on_states")
    step = item.get("step")
    icon_size = item.get("icon_size")
    return Button(
        id=bid, x=x, y=y, w=w, h=h,
        label=str(item.get("label", bid)),
        entity=item.get("entity"),
        action=action,
        data=dict(item.get("data") or {}),
        display=display,
        attribute=item.get("attribute"),
        unit=str(item.get("unit", "")),
        on_states=tuple(str(s) for s in on_states) if on_states else DEFAULT_ON_STATES,
        show_status=bool(item.get("show_status", True)),
        state_labels={str(k): str(v) for k, v in (item.get("state_labels") or {}).items()},
        extra_entity=item.get("extra_entity"),
        extra_unit=str(item.get("extra_unit", "")),
        long_action=long_action,
        long_data=dict(item.get("long_data") or {}),
        long_ms=int(item.get("long_ms", DEFAULT_LONG_MS)),
        hint=str(item.get("hint", "")),
        step=float(step) if step is not None else None,
        high_entity=item.get("high_entity"),
        low_entity=item.get("low_entity"),
        presets=tuple(str(p) for p in presets),
        preset_labels={str(k): str(v) for k, v in (item.get("preset_labels") or {}).items()},
        eco_preset=str(eco_preset) if eco_preset is not None else None,
        normal_preset=str(item.get("normal_preset", "none")),
        eco_label=str(item.get("eco_label", "Eco")),
        icon=_parse_icon(bid, "icon", item.get("icon"), base_dir),
        top_icon=_parse_icon(bid, "top_icon", item.get("top_icon"), base_dir),
        icon_size=int(icon_size) if icon_size is not None else None,
    )


def parse_layout(raw: dict, base_dir: Path | None = None) -> Layout:
    """Either `pages: [{title, buttons}]` or a single page as top-level `title` + `buttons`.

    `base_dir` (the layout file's folder) resolves relative icon image paths."""
    raw_pages = raw.get("pages")
    if raw_pages is None:
        raw_pages = [{"title": raw.get("title", "Home"), "buttons": raw.get("buttons")}]
    if not raw_pages:
        raise LayoutError("'pages' is empty")
    pages, seen = [], set()
    for p, raw_page in enumerate(raw_pages):
        buttons = []
        for i, item in enumerate(raw_page.get("buttons") or []):
            where = f"page {p + 1}, button #{i + 1}"
            button = _parse_button(item, where, base_dir)
            if button.id in seen:
                raise LayoutError(f"{where}: duplicate id {button.id!r}")
            seen.add(button.id)
            buttons.append(button)
        pages.append(Page(title=str(raw_page.get("title", f"Page {p + 1}")), buttons=buttons))
    return Layout(pages=pages)


def load_layout(path: str | Path) -> Layout:
    path = Path(path)
    return parse_layout(yaml.safe_load(path.read_text()) or {}, path.parent)
