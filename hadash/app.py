"""HTTP endpoints for the Kobo client and the browser preview."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from flask import Flask, Response, request

from .config import Config
from .ha import FakeHA, HAClient, HAError
from .layout import MAX_LONG_MS, Button, Layout, LayoutError, load_layout
from .render import NAV_H, Renderer, Scene, build_scene, to_png

log = logging.getLogger(__name__)

TAP_ERROR_TTL = 30.0


class Dashboard:
    """Holds the layout, caches HA states and turns taps into service calls."""

    def __init__(self, config: Config, ha=None):
        self.config = config
        self.ha = ha or (FakeHA() if config.mock else HAClient(config.ha_url, config.ha_token))
        self.renderer = Renderer(config.width, config.height,
                                 config.font_path, config.bold_font_path,
                                 config.icon_font_path)
        self._lock = threading.Lock()
        self._layout: Layout | None = None
        self._layout_mtime: float | None = None
        self._layout_error: str | None = None
        self._states: dict[str, dict] = {}
        self._states_error: str | None = None
        self._states_at = 0.0
        self._tap_error: tuple[str, float] | None = None
        self._page = 0  # one Kobo, so the current page lives here

    # -- layout -------------------------------------------------------------

    def layout(self) -> Layout | None:
        """Reload layout.yaml whenever it changes on disk, so edits show up live."""
        path = Path(self.config.layout_path)
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            self._layout, self._layout_error = None, f"cannot read {path}: {exc.strerror}"
            return None
        if mtime != self._layout_mtime:
            self._layout_mtime = mtime
            try:
                self._layout, self._layout_error = load_layout(path), None
                log.info("loaded layout %s (%d pages, %d buttons)", path,
                         len(self._layout.pages), len(self._layout.buttons))
            except (LayoutError, OSError, ValueError) as exc:
                self._layout, self._layout_error = None, f"{path.name}: {exc}"
                log.error("layout error: %s", exc)
        return self._layout

    # -- states -------------------------------------------------------------

    def _refresh_states(self, layout: Layout, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._states_at < self.config.state_cache_ttl:
            return
        try:
            self._states = self.ha.get_states(layout.entities())
            self._states_error = None
        except HAError as exc:
            self._states_error = str(exc)
            log.warning("%s", exc)
        self._states_at = now

    def scene(self, force: bool = False) -> Scene | None:
        with self._lock:
            layout = self.layout()
            if layout is None:
                return None
            self._refresh_states(layout, force)
            if self._tap_error and time.monotonic() - self._tap_error[1] >= TAP_ERROR_TTL:
                self._tap_error = None
            index = self._page % len(layout.pages)
            page, count = layout.pages[index], len(layout.pages)
            if self._states_error:
                return build_scene(page, {}, "Home Assistant unreachable", stale=True,
                                   index=index, count=count)
            return build_scene(page, self._states,
                               self._tap_error[0] if self._tap_error else None,
                               index=index, count=count)

    def version(self) -> str:
        scene = self.scene()
        return scene.version() if scene else f"err-{hash(self._layout_error) & 0xffffff:06x}"

    def png(self, pressed: str | None = None) -> bytes:
        scene = self.scene()
        if scene is None:
            return to_png(self.renderer.render_error(self._layout_error or "no layout"))
        return to_png(self.renderer.render(self._layout.page(scene.page), scene, pressed=pressed))

    # -- taps ---------------------------------------------------------------

    def _fail(self, message: str) -> None:
        log.warning("%s", message)
        self._tap_error = (message, time.monotonic())

    def _climate_call(self, button, zone: int) -> tuple[str, str, dict] | None:
        """set_temperature one step down (zone -1) or up (+1) from the current target."""
        attrs = (self._states.get(button.entity) or {}).get("attributes", {})
        target = attrs.get("temperature")
        if target is None:
            self._fail(f"{button.label}: no target temperature to change")
            return None
        step = button.step or attrs.get("target_temp_step") or 0.5
        new = float(target) + zone * float(step)
        lo, hi = attrs.get("min_temp"), attrs.get("max_temp")
        if lo is not None:
            new = max(new, float(lo))
        if hi is not None:
            new = min(new, float(hi))
        return "climate", "set_temperature", {"entity_id": button.entity, "temperature": round(new, 1)}

    def press(self, x: int, y: int) -> tuple[str, int] | None:
        """A finger just landed at (x, y). If it's on a button with a long press,
        return (button id, long_ms) so the Kobo can show hold feedback."""
        layout = self.layout()
        if layout is None:
            return None
        if len(layout.pages) > 1 and y >= self.config.height - NAV_H:
            return None
        button = layout.page(self._page).hit(x, y)
        if button is None or button.long_action is None:
            return None
        return button.id, button.long_ms

    def tap(self, x: int, y: int, ms: int = 0) -> tuple[str, str | None]:
        """Run the action under (x, y), held for `ms`. Returns (refresh kind, button id)."""
        layout = self.layout()
        if layout is None:
            return "none", None
        if len(layout.pages) > 1 and y >= self.config.height - NAV_H:
            return self._nav_tap(layout, x)
        button = layout.page(self._page).hit(x, y)
        if button is None:
            return "none", None

        before = self.scene(force=True)
        call = self._button_call(button, x, y, ms)
        if isinstance(call, str):  # nothing to call; just the refresh kind
            return call, button.id
        self._run_call(button, call, before)
        return "partial", button.id

    def _nav_tap(self, layout: Layout, x: int) -> tuple[str, str | None]:
        """A tap on the page bar: the outer thirds flip pages, the middle does nothing."""
        third = self.config.width // 3
        if third <= x < self.config.width - third:
            return "none", None
        step = -1 if x < third else 1
        with self._lock:
            self._page = (self._page + step) % len(layout.pages)
            self._tap_error = None
        # A whole new screen: full refresh, so the old page doesn't ghost.
        return "full", "_nav_prev" if step < 0 else "_nav_next"

    def _preset_for(self, button: Button, x: int, y: int) -> str | None:
        """The preset a tap on a climate card asks for: a preset button, or the eco toggle."""
        preset = button.preset_at(x, y)
        if preset is None and button.eco_hit(x, y):
            attrs = (self._states.get(button.entity) or {}).get("attributes", {})
            on = attrs.get("preset_mode") == button.eco_preset
            preset = button.normal_preset if on else button.eco_preset
        return preset

    def _is_long_press(self, button: Button, ms: int) -> bool:
        if button.long_action is None:
            return False
        if ms > MAX_LONG_MS:
            log.warning("%s: ignoring implausible %d ms press, treating as a tap",
                        button.id, ms)
            return False
        return ms >= button.long_ms

    def _button_call(self, button: Button, x: int, y: int,
                     ms: int) -> tuple[str, str, dict] | str:
        """The (domain, service, data) a tap asks for, or the refresh kind when
        there is nothing to call."""
        preset = self._preset_for(button, x, y)
        if preset is not None:
            return "climate", "set_preset_mode", {"entity_id": button.entity,
                                                  "preset_mode": preset}
        if button.display == "climate":
            zone = button.climate_zone(x)
            if zone == 0:
                return "none"
            return self._climate_call(button, zone) or "partial"
        long = self._is_long_press(button, ms)
        service = button.service(long)
        if service is None:
            return "none"
        return (*service, button.service_data(long))

    def _run_call(self, button: Button, call: tuple[str, str, dict], before: Scene | None) -> None:
        domain, service, data = call
        try:
            self.ha.call_service(domain, service, data)
            self._tap_error = None
        except HAError as exc:
            self._fail(f"{button.label}: action failed")
            log.warning("%s", exc)
            return
        # Give HA a moment to apply the change so the next render already shows it.
        deadline = time.monotonic() + self.config.tap_settle_timeout
        while time.monotonic() < deadline:
            after = self.scene(force=True)
            if before is None or after is None or after.version() != before.version():
                break
            time.sleep(0.2)


PREVIEW_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>hadash preview</title>
<style>
 body { font-family: sans-serif; background: #ddd; margin: 0; padding: 16px; }
 .wrap { display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }
 img { width: %(w)dpx; height: %(h)dpx; border: 12px solid #222; border-radius: 8px;
       cursor: crosshair; background: #fff; image-rendering: pixelated; }
 #log { font: 13px monospace; white-space: pre; max-width: 420px; }
 label { display: block; margin-bottom: 8px; }
</style></head>
<body><div class="wrap">
 <img id="screen" src="dashboard.png" alt="dashboard">
 <div>
  <label><input type="checkbox" id="eink" checked> simulate e-ink flash on refresh</label>
  <label>Shift-click = long press (3.5 s)</label>
  <div id="log"></div>
 </div>
</div>
<script>
const img = document.getElementById('screen');
const logEl = document.getElementById('log');
let version = null;
function log(msg) { logEl.textContent = new Date().toLocaleTimeString() + '  ' + msg + '\\n' + logEl.textContent; }
function reload(pressed) {
  const flash = document.getElementById('eink').checked;
  if (flash) img.style.filter = 'invert(1)';
  const q = pressed ? '&pressed=' + encodeURIComponent(pressed) : '';
  img.src = 'dashboard.png?t=' + Date.now() + q;
  img.onload = () => { img.style.filter = ''; };
}
img.addEventListener('click', async (e) => {
  const r = img.getBoundingClientRect();
  const sx = %(w)d / (r.width - 24), sy = %(h)d / (r.height - 24);
  const x = Math.round((e.clientX - r.left - 12) * sx), y = Math.round((e.clientY - r.top - 12) * sy);
  const ms = e.shiftKey ? 3500 : 100;  // shift-click = long press
  const resp = await fetch('tap?x=' + x + '&y=' + y + '&ms=' + ms, {method: 'POST'});
  const text = (await resp.text()).trim();
  log('tap ' + x + ',' + y + ' -> ' + text);
  const [kind, v, id] = text.split(' ');
  version = v;
  if (kind !== 'none') reload();
});
setInterval(async () => {
  const v = (await (await fetch('version')).text()).trim();
  if (version !== null && v !== version) { log('version ' + v + ' (changed elsewhere)'); reload(); }
  version = v;
}, 3000);
</script></body></html>
"""


def create_app(config: Config, ha=None) -> Flask:
    app = Flask(__name__)
    dash = Dashboard(config, ha)
    app.config["dashboard"] = dash

    def text(body: str) -> Response:
        return Response(body + "\n", mimetype="text/plain",
                        headers={"Cache-Control": "no-store"})

    @app.get("/dashboard.png")
    def dashboard_png():
        return Response(dash.png(pressed=request.args.get("pressed")), mimetype="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.get("/version")
    def version():
        return text(dash.version())

    @app.route("/tap", methods=["GET", "POST"])
    def tap():
        # GET is accepted too: BusyBox wget without --post-data support can still tap.
        try:
            x, y = int(request.args["x"]), int(request.args["y"])
            ms = int(request.args.get("ms", 0))  # how long the finger was down
        except (KeyError, ValueError):
            return text("error bad-coordinates"), 400
        kind, button_id = dash.tap(x, y, ms)
        # One line, space separated, easy to split in shell: "<refresh> <version> [button]"
        return text(" ".join(p for p in (kind, dash.version(), button_id) if p))

    @app.get("/press")
    def press():
        # "long <button-id> <ms>" when holding here does something, else "none".
        try:
            x, y = int(request.args["x"]), int(request.args["y"])
        except (KeyError, ValueError):
            return text("error bad-coordinates"), 400
        hit = dash.press(x, y)
        return text(f"long {hit[0]} {hit[1]}" if hit else "none")

    @app.get("/")
    def preview():
        return Response(PREVIEW_HTML % {"w": config.width, "h": config.height},
                        mimetype="text/html")

    return app
