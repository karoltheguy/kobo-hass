import io
from pathlib import Path

import pytest
from PIL import Image

from hadash.app import create_app
from hadash.config import Config, load_config
from hadash.ha import FakeHA, HAError
from hadash.layout import LayoutError, parse_layout
from hadash.render import build_scene

ROOT = Path(__file__).resolve().parent.parent

LAYOUT = """
title: Test
buttons:
  - id: lamp
    rect: [0, 100, 200, 150]
    label: Lamp
    entity: light.lamp
    action: light.toggle
    display: toggle
  - id: temp
    rect: [200, 100, 200, 150]
    label: Temp
    entity: sensor.temp
    unit: "°"
    display: value
"""


class FailingHA(FakeHA):
    def __init__(self, fail_reads=False, fail_calls=False):
        super().__init__()
        self.fail_reads, self.fail_calls = fail_reads, fail_calls

    def get_states(self, entity_ids):
        if self.fail_reads:
            raise HAError("connection refused")
        return super().get_states(entity_ids)

    def call_service(self, domain, service, data):
        if self.fail_calls:
            raise HAError("500")
        super().call_service(domain, service, data)


@pytest.fixture
def make_client(tmp_path):
    def make(ha=None, layout=LAYOUT):
        (tmp_path / "layout.yaml").write_text(layout)
        cfg = Config(mock=True, layout_path=tmp_path / "layout.yaml",
                     state_cache_ttl=0, tap_settle_timeout=0.5)
        ha = ha or FakeHA()
        ha._states["light.lamp"] = {"entity_id": "light.lamp", "state": "off", "attributes": {}}
        return create_app(cfg, ha).test_client(), ha
    return make


def test_layout_hit_and_service_data():
    layout = parse_layout({"buttons": [
        {"id": "a", "rect": [10, 10, 100, 100], "entity": "light.a", "action": "light.toggle"},
        {"id": "b", "rect": [10, 110, 100, 100], "action": "light.turn_off",
         "data": {"entity_id": "all"}},
    ]})
    page = layout.pages[0]
    assert page.hit(10, 10).id == "a"
    assert page.hit(109, 109).id == "a"
    assert page.hit(110, 50) is None
    assert page.hit(50, 150).id == "b"
    assert page.hit(10, 10).service_data() == {"entity_id": "light.a"}
    assert page.hit(50, 150).service_data() == {"entity_id": "all"}


@pytest.mark.parametrize("raw, msg", [
    ({"buttons": [{"id": "a"}]}, "rect"),
    ({"buttons": [{"id": "a", "rect": [0, 0, 1, 1]}, {"id": "a", "rect": [0, 0, 1, 1]}]}, "duplicate"),
    ({"buttons": [{"id": "a", "rect": [0, 0, 1, 1], "display": "dial"}]}, "display"),
    ({"buttons": [{"id": "a", "rect": [0, 0, 1, 1], "action": "toggle"}]}, "domain.service"),
])
def test_layout_validation(raw, msg):
    with pytest.raises(LayoutError, match=msg):
        parse_layout(raw)


def test_example_files_load():
    cfg = load_config(ROOT / "config.example.yaml")
    assert (cfg.width, cfg.height) == (600, 800)
    from hadash.layout import load_layout
    layout = load_layout(ROOT / "layout.example.yaml")
    for b in layout.buttons:
        assert b.w >= 120 and b.h >= 120, b.id
        assert b.x + b.w <= 600 and b.y + b.h <= 800, b.id


def test_version_tracks_displayed_state_only():
    layout = parse_layout({"buttons": [
        {"id": "t", "rect": [0, 0, 150, 150], "entity": "sensor.t", "display": "value"}]})
    s1 = build_scene(layout.pages[0], {"sensor.t": {"state": "20", "attributes": {"x": 1}}})
    s2 = build_scene(layout.pages[0], {"sensor.t": {"state": "20", "attributes": {"x": 2}}})
    s3 = build_scene(layout.pages[0], {"sensor.t": {"state": "21", "attributes": {}}})
    assert s1.version() == s2.version() != s3.version()


def test_png_is_greyscale_screen_size(make_client):
    client, _ = make_client()
    resp = client.get("/dashboard.png")
    img = Image.open(io.BytesIO(resp.data))
    assert resp.mimetype == "image/png"
    assert img.size == (600, 800) and img.mode == "L"


def test_tap_toggles_and_bumps_version(make_client):
    client, ha = make_client()
    v1 = client.get("/version").text.strip()
    resp = client.post("/tap?x=50&y=150").text.split()
    assert resp[0] == "partial" and resp[2] == "lamp"
    assert ha._states["light.lamp"]["state"] == "on"
    assert resp[1] != v1
    assert client.get("/version").text.strip() == resp[1]


def test_tap_outside_or_on_display_only_does_nothing(make_client):
    client, ha = make_client()
    assert client.post("/tap?x=500&y=50").text.split()[0] == "none"
    assert client.post("/tap?x=300&y=150").text.split()[::2] == ["none", "temp"]
    assert client.get("/tap?x=a&y=1").status_code == 400


def test_ha_down_shows_banner_not_crash(make_client):
    client, _ = make_client(FailingHA(fail_reads=True))
    assert client.get("/dashboard.png").status_code == 200
    assert client.get("/version").status_code == 200


def test_failed_action_changes_version(make_client):
    client, _ = make_client(FailingHA(fail_calls=True))
    v1 = client.get("/version").text.strip()
    kind, v2, _ = client.post("/tap?x=50&y=150").text.split()
    assert kind == "partial" and v2 != v1  # error banner is part of the scene


def test_bad_layout_renders_error_screen(make_client):
    client, _ = make_client(layout="buttons: [{id: x}]")
    assert client.get("/dashboard.png").status_code == 200
    assert client.get("/version").text.startswith("err-")


PAGES = """
pages:
  - title: One
    buttons:
      - id: lock
        rect: [0, 100, 300, 200]
        label: Door
        entity: lock.door
        action: lock.lock
        long_action: lock.unlock
        display: toggle
        on_states: [unlocked]
        extra_entity: sensor.door_battery
        extra_unit: "%"
        hint: Hold 3 s to unlock
      - id: garage
        rect: [300, 100, 300, 200]
        label: Garage
        entity: binary_sensor.garage
        display: toggle
        state_labels: {"off": Closed, "on": Open}
  - title: Two
    buttons:
      - id: heat
        rect: [20, 100, 560, 150]
        label: Office
        entity: climate.office
        display: climate
      - id: away
        rect: [20, 300, 180, 130]
        label: Away
        entity: climate.office
        attribute: preset_mode
        on_states: [away]
        show_status: false
        action: climate.set_preset_mode
        data: {preset_mode: away}
        display: toggle
      - id: weather
        rect: [20, 450, 560, 150]
        entity: weather.home
        high_entity: sensor.high
        low_entity: sensor.low
        display: weather
"""


def seed_pages(ha):
    ha._states.update({
        "lock.door": {"state": "locked", "attributes": {}},
        "sensor.door_battery": {"state": "75.0", "attributes": {}},
        "binary_sensor.garage": {"state": "off", "attributes": {}},
        "climate.office": {"state": "heat", "attributes": {
            "current_temperature": 20.5, "temperature": 18.0, "target_temp_step": 0.5,
            "min_temp": 7, "max_temp": 18.5, "preset_mode": "home"}},
        "weather.home": {"state": "clear-night", "attributes": {"temperature": 8.5}},
        "sensor.high": {"state": "11", "attributes": {}},
        "sensor.low": {"state": "3", "attributes": {}},
    })


def test_pages_parse_and_single_page_still_works():
    layout = parse_layout({"pages": [{"title": "A", "buttons": []}, {"title": "B"}]})
    assert [p.title for p in layout.pages] == ["A", "B"]
    assert layout.page(2).title == "A"  # wraps
    old = parse_layout({"title": "Solo", "buttons": [{"id": "a", "rect": [0, 0, 130, 130]}]})
    assert len(old.pages) == 1 and old.title == "Solo"
    with pytest.raises(LayoutError, match="duplicate"):
        parse_layout({"pages": [{"buttons": [{"id": "a", "rect": [0, 0, 1, 1]}]},
                                {"buttons": [{"id": "a", "rect": [0, 0, 1, 1]}]}]})


def test_nav_bar_changes_page_with_full_refresh(make_client):
    client, ha = make_client(layout=PAGES)
    seed_pages(ha)
    v1 = client.get("/version").text.strip()
    kind, v2, bid = client.post("/tap?x=550&y=760").text.split()
    assert (kind, bid) == ("full", "_nav_next") and v2 != v1
    # page two: the climate card is where the lock was
    assert client.post("/tap?x=40&y=150").text.split()[::2] == ["none", "heat"]
    assert client.post("/tap?x=550&y=760").text.split()[0] == "full"  # wraps to page one
    assert client.post("/tap?x=300&y=760").text.split()[0] == "none"  # page label
    assert client.post("/tap?x=20&y=760").text.split()[2] == "_nav_prev"
    assert client.get("/dashboard.png").status_code == 200


def test_lock_needs_long_press_to_unlock(make_client):
    client, ha = make_client(layout=PAGES)
    seed_pages(ha)
    calls = []
    real = ha.call_service
    ha.call_service = lambda d, s, data: (calls.append((d, s)), real(d, s, data))
    client.post("/tap?x=100&y=150&ms=200")
    client.post("/tap?x=100&y=150&ms=2900")
    client.post("/tap?x=100&y=150")
    assert calls == [("lock", "lock")] * 3
    client.post("/tap?x=100&y=150&ms=13006")  # missed lift on the Kobo, not a hold
    assert calls[-1] == ("lock", "lock")
    client.post("/tap?x=100&y=150&ms=3100")
    assert calls[-1] == ("lock", "unlock")


def test_climate_minus_plus_steps_and_clamps(make_client):
    client, ha = make_client(layout=PAGES)
    seed_pages(ha)
    client.post("/tap?x=550&y=760")  # page two
    # card is x 20..580: plus zone 490..580, minus zone 310..400, middle does nothing
    client.post("/tap?x=350&y=170")
    assert ha._states["climate.office"]["attributes"]["temperature"] == 17.5
    client.post("/tap?x=450&y=170")
    assert ha._states["climate.office"]["attributes"]["temperature"] == 17.5
    for _ in range(3):
        client.post("/tap?x=560&y=170")
    assert ha._states["climate.office"]["attributes"]["temperature"] == 18.5  # max_temp


def test_climate_without_target_shows_error(make_client):
    client, ha = make_client(layout=PAGES)
    seed_pages(ha)
    ha._states["climate.office"]["attributes"]["temperature"] = None
    client.post("/tap?x=550&y=760")
    v1 = client.get("/version").text.strip()
    kind, v2, _ = client.post("/tap?x=560&y=170").text.split()
    assert kind == "partial" and v2 != v1


def test_new_card_views():
    layout = parse_layout(__import__("yaml").safe_load(PAGES))
    states = {
        "lock.door": {"state": "locked", "attributes": {}},
        "sensor.door_battery": {"state": "75.0", "attributes": {}},
        "binary_sensor.garage": {"state": "off", "attributes": {}},
        "climate.office": {"state": "heat", "attributes": {
            "current_temperature": 20.5, "temperature": 18.0, "current_humidity": 40,
            "preset_mode": "away"}},
        "weather.home": {"state": "clear-night", "attributes": {"temperature": 8.5}},
        "sensor.high": {"state": "11", "attributes": {}},
        "sensor.low": {"state": "3", "attributes": {}},
    }
    one = {v.id: v for v in build_scene(layout.pages[0], states).buttons}
    assert one["lock"].value == "Locked · 75%" and not one["lock"].active
    assert one["garage"].value == "Closed"
    two = {v.id: v for v in build_scene(layout.pages[1], states, index=1, count=2).buttons}
    assert (two["heat"].value, two["heat"].detail) == ("18°", "Now 20.5°  40%")
    assert two["away"].active and two["away"].value == ""
    assert (two["weather"].label, two["weather"].value, two["weather"].detail) == \
        ("Clear night", "8.5°", "High 11° · Low 3°")
    states["weather.home"]["state"] = "partlycloudy"
    two = {v.id: v for v in build_scene(layout.pages[1], states, index=1, count=2).buttons}
    assert two["weather"].label == "Partly cloudy"


def test_press_reports_long_press_buttons_only(make_client):
    client, ha = make_client(layout=PAGES)
    seed_pages(ha)
    assert client.get("/press?x=100&y=150").text.split() == ["long", "lock", "3000"]
    assert client.get("/press?x=400&y=150").text.strip() == "none"  # garage: no long action
    assert client.get("/press?x=550&y=760").text.strip() == "none"  # page bar
    assert client.get("/press?x=5&y=5").text.strip() == "none"
    assert client.get("/press?x=a&y=1").status_code == 400
    client.post("/tap?x=550&y=760")  # page two has no long-press buttons
    assert client.get("/press?x=100&y=150").text.strip() == "none"


PRESETS = """
title: Presets
buttons:
  - id: heat
    rect: [20, 100, 560, 250]
    label: Office
    entity: climate.office
    display: climate
    presets: [away, home, sleep]
    preset_labels: {sleep: Night}
"""


def test_climate_presets_row(make_client):
    client, ha = make_client(layout=PRESETS)
    seed_pages(ha)
    attrs = ha._states["climate.office"]["attributes"]
    # preset row is y 240..338; three buttons across x 32..568
    client.post("/tap?x=60&y=290")
    assert attrs["preset_mode"] == "away" and attrs["temperature"] == 18.0
    client.post("/tap?x=550&y=290")  # under the plus zone, but in the preset row
    assert attrs["preset_mode"] == "sleep" and attrs["temperature"] == 18.0
    client.post("/tap?x=560&y=170")  # plus, above the row
    assert attrs["temperature"] == 18.5 and attrs["preset_mode"] == "sleep"
    layout = parse_layout(__import__("yaml").safe_load(PRESETS))
    view = build_scene(layout.pages[0], ha._states).buttons[0]
    assert view.preset == "sleep" and layout.pages[0].buttons[0].preset_label("sleep") == "Night"
    assert client.get("/dashboard.png").status_code == 200


def test_presets_validation():
    base = {"id": "h", "rect": [0, 0, 560, 250], "entity": "climate.x", "display": "climate"}
    with pytest.raises(LayoutError, match="only works on a climate"):
        parse_layout({"buttons": [{**base, "display": "toggle", "presets": ["away"]}]})
    with pytest.raises(LayoutError, match="tall"):
        parse_layout({"buttons": [{**base, "rect": [0, 0, 560, 150], "presets": ["away"]}]})


ECO = """
title: Eco
buttons:
  - id: heat
    rect: [20, 100, 560, 140]
    label: Office
    entity: climate.office
    display: climate
    eco_preset: Energy heat
"""


def test_climate_eco_toggle(make_client):
    client, ha = make_client(layout=ECO)
    seed_pages(ha)
    attrs = ha._states["climate.office"]["attributes"]
    attrs["preset_mode"] = "none"
    client.post("/tap?x=100&y=170")  # name side
    assert attrs["preset_mode"] == "Energy heat"
    layout = parse_layout(__import__("yaml").safe_load(ECO))
    attrs["temperature"] = None  # as HA reports it in eco
    view = build_scene(layout.pages[0], ha._states).buttons[0]
    assert view.preset == "Energy heat" and view.value == "--" and "Energy" not in view.detail
    assert client.get("/dashboard.png").status_code == 200
    client.post("/tap?x=100&y=170")
    assert attrs["preset_mode"] == "none"
    client.post("/tap?x=450&y=170")  # target number: still does nothing
    assert attrs["preset_mode"] == "none"
    with pytest.raises(LayoutError, match="only works on a climate"):
        parse_layout({"buttons": [{"id": "x", "rect": [0, 0, 200, 200],
                                   "display": "toggle", "eco_preset": "eco"}]})


def test_icons_follow_state_and_resolve_images(tmp_path):
    Image.new("RGBA", (32, 32), (0, 0, 0, 255)).save(tmp_path / "bulb.png")
    (tmp_path / "layout.yaml").write_text("""
buttons:
  - id: lamp
    rect: [0, 100, 270, 170]
    label: Lamp
    entity: light.lamp
    display: toggle
    top_icon: bulb.png
    icon: {"on": "💡", default: "🌑"}
  - id: scene
    rect: [300, 100, 270, 170]
    label: Night
    display: action
    icon: "🌙"
""")
    from hadash.layout import load_layout
    from hadash.render import Renderer
    layout = load_layout(tmp_path / "layout.yaml")
    page = layout.pages[0]
    assert page.buttons[0].top_icon == {"default": str(tmp_path / "bulb.png")}
    on = build_scene(page, {"light.lamp": {"state": "on", "attributes": {}}})
    off = build_scene(page, {"light.lamp": {"state": "off", "attributes": {}}})
    stale = build_scene(page, {}, stale=True)
    assert on.buttons[0].icon == "💡" and off.buttons[0].icon == "🌑"
    assert stale.buttons[0].icon == "🌑" and off.buttons[1].icon == "🌙"
    assert on.version() != off.version()
    r = Renderer(600, 800)
    plain = parse_layout({"buttons": [{**b, "icon": None, "top_icon": None} for b in [
        {"id": "lamp", "rect": [0, 100, 270, 170], "label": "Lamp", "entity": "light.lamp",
         "display": "toggle"},
        {"id": "scene", "rect": [300, 100, 270, 170], "label": "Night", "display": "action"}]]})
    with_icons = r.render(page, on)
    without = r.render(plain.pages[0], build_scene(plain.pages[0], {"light.lamp": {
        "state": "on", "attributes": {}}}))
    assert with_icons.tobytes() != without.tobytes()
    # the black square image is inverted on the black "on" card: no pure black inside it
    assert with_icons.getpixel((135, 125)) == 255


def test_icon_validation(tmp_path):
    (tmp_path / "layout.yaml").write_text(
        "buttons: [{id: a, rect: [0, 0, 200, 200], display: toggle, icon: nope.png}]")
    from hadash.layout import load_layout
    with pytest.raises(LayoutError, match="not found"):
        load_layout(tmp_path / "layout.yaml")
    with pytest.raises(LayoutError, match="only works on"):
        parse_layout({"buttons": [{"id": "h", "rect": [0, 0, 560, 150], "entity": "climate.x",
                                   "display": "climate", "icon": "🔥"}]})
