"""Feeds real binary input_event structs through od + the Kobo awk scripts."""

import struct
import subprocess
from pathlib import Path

import pytest

KOBO = Path(__file__).resolve().parent.parent / "kobo"

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_PRESSURE = 0, 1, 24
ABS_MT_X, ABS_MT_Y, ABS_MT_TRACKING_ID = 53, 54, 57
BTN_TOUCH = 330

# Both od flavours lib.sh may pick: GNU-style, and traditional BusyBox `od -d`.
OD_COMMANDS = [["od", "-An", "-v", "-tu2"], ["od", "-d"]]


def ev(t_ms, type_, code, value):
    """struct input_event on 32-bit ARM: timeval (2x u32), u16 type, u16 code, s32 value."""
    return struct.pack("<IIHHi", t_ms // 1000, (t_ms % 1000) * 1000, type_, code, value)


def syn(t):
    return ev(t, EV_SYN, 0, 0)


def btn_touch_tap(rx, ry, t0=1_700_000_000_000, hold=100, drift=0):
    """zForce style: ABS_X/ABS_Y + BTN_TOUCH, with an extra report mid-hold."""
    return b"".join([
        ev(t0, EV_ABS, ABS_X, rx), ev(t0, EV_ABS, ABS_Y, ry),
        ev(t0, EV_KEY, BTN_TOUCH, 1), syn(t0),
        ev(t0 + hold // 2, EV_ABS, ABS_X, rx + drift), syn(t0 + hold // 2),
        ev(t0 + hold, EV_KEY, BTN_TOUCH, 0), syn(t0 + hold),
    ])


def mt_tap(rx, ry, t0=1_700_000_000_000, hold=100):
    """Multitouch protocol B: tracking id, positions, tracking id -1 on lift."""
    return b"".join([
        ev(t0, EV_ABS, ABS_MT_TRACKING_ID, 7), ev(t0, EV_ABS, ABS_MT_X, rx),
        ev(t0, EV_ABS, ABS_MT_Y, ry), syn(t0),
        ev(t0 + hold, EV_ABS, ABS_MT_TRACKING_ID, -1), syn(t0 + hold),
    ])


def pressure_tap(rx, ry, t0=1_700_000_000_000, hold=100):
    return b"".join([
        ev(t0, EV_ABS, ABS_X, rx), ev(t0, EV_ABS, ABS_Y, ry),
        ev(t0, EV_ABS, ABS_PRESSURE, 100), syn(t0),
        ev(t0 + hold, EV_ABS, ABS_PRESSURE, 0), syn(t0 + hold),
    ])


def run_touch(data, od=None, **mapping):
    text = subprocess.run(od or OD_COMMANDS[0], input=data, capture_output=True, check=True).stdout
    args = ["awk"]
    for k, v in mapping.items():
        args += ["-v", f"{k}={v}"]
    out = subprocess.run(args + ["-f", str(KOBO / "touch.awk")], input=text,
                         capture_output=True, check=True).stdout.decode()
    return [line.split() for line in out.splitlines()]


@pytest.mark.parametrize("od", OD_COMMANDS, ids=["od-tu2", "od-d"])
@pytest.mark.parametrize("make_tap", [btn_touch_tap, mt_tap, pressure_tap])
def test_one_tap_per_lift(od, make_tap):
    taps = run_touch(make_tap(100, 200) + make_tap(300, 400, t0=1_700_000_005_000), od=od)
    assert [t[:3] for t in taps] == [["tap", "100", "200"], ["tap", "300", "400"]]


def test_duration_raw_and_lift_time():
    (tap,) = run_touch(btn_touch_tap(10, 20, t0=1_700_000_000_250, hold=1600))
    assert tap == ["tap", "10", "20", "1600", "10", "20", str(1_700_000_001)]


def test_events_split_across_reads_keep_state():
    # Each od run prints one batch; awk sees them as one stream.
    data = btn_touch_tap(50, 60)
    od = OD_COMMANDS[0]
    text = b"".join(
        subprocess.run(od, input=data[i:i + 32], capture_output=True, check=True).stdout
        for i in range(0, len(data), 32)
    )
    out = subprocess.run(["awk", "-f", str(KOBO / "touch.awk")], input=text,
                         capture_output=True, check=True).stdout.decode().split()
    assert out[:3] == ["tap", "50", "60"]


def test_reports_position_at_lift():
    (tap,) = run_touch(btn_touch_tap(100, 200, drift=15))
    assert tap[1:3] == ["115", "200"]


def test_swap_and_mirror():
    # The plan's guess for zForce: screen_x = raw_y, screen_y = 800 - raw_x.
    (tap,) = run_touch(btn_touch_tap(700, 150), swap=1, mirror_y=1)
    assert tap[1:3] == ["150", "99"]


def test_scaling_and_clamping():
    (tap,) = run_touch(btn_touch_tap(2048, 4095), raw_w=4096, raw_h=4096)
    assert tap[1:3] == ["300", "799"]


# -- calibration ---------------------------------------------------------------

W, H = 600, 800

# Device models: screen (x, y) -> raw (x, y)
DEVICES = {
    "identity": lambda x, y: (x, y),
    "zforce-guess": lambda x, y: (H - 1 - y, x),          # swap + mirror_y
    "swap-mirror-x": lambda x, y: (y, W - 1 - x),
    "scaled-4096": lambda x, y: (x * 4096 // W, y * 4096 // H),
    "swapped-scaled-mirrored": lambda x, y: ((H - 1 - y) * 1024 // H, x * 768 // W),
}


def calibrate(tl, tr, bl):
    out = subprocess.run(
        ["awk", "-v", f"W={W}", "-v", f"H={H}", "-v", f"tl={tl[0]} {tl[1]}",
         "-v", f"tr={tr[0]} {tr[1]}", "-v", f"bl={bl[0]} {bl[1]}",
         "-f", str(KOBO / "calib.awk")],
        capture_output=True, check=True).stdout.decode()
    return dict(line.split("=") for line in out.split())


@pytest.mark.parametrize("name", DEVICES)
def test_calibration_round_trip(name):
    device = DEVICES[name]
    inset = 14  # where the + markers sit, roughly
    conf = calibrate(device(inset, inset), device(W - inset, inset), device(inset, H - inset))
    mapping = dict(swap=conf["TOUCH_SWAP"], mirror_x=conf["TOUCH_MIRROR_X"],
                   mirror_y=conf["TOUCH_MIRROR_Y"], raw_w=conf["TOUCH_RAW_W"],
                   raw_h=conf["TOUCH_RAW_H"])
    for sx, sy in [(30, 30), (570, 40), (300, 400), (50, 760), (580, 780)]:
        (tap,) = run_touch(btn_touch_tap(*device(sx, sy)), **mapping)
        x, y = int(tap[1]), int(tap[2])
        assert abs(x - sx) <= 8 and abs(y - sy) <= 8, (name, (sx, sy), (x, y), conf)


def test_missed_lift_does_not_become_a_long_press():
    """Seen on the N905C: a hold's lift got lost, and the next quick tap came out
    as a 13 s press. A new BTN_TOUCH down must restart the clock."""
    t0 = 1_700_000_000_000
    first_without_lift = b"".join([
        ev(t0, EV_ABS, ABS_X, 100), ev(t0, EV_ABS, ABS_Y, 200),
        ev(t0, EV_ABS, ABS_PRESSURE, 101), ev(t0, EV_KEY, BTN_TOUCH, 1), syn(t0),
    ])
    taps = run_touch(first_without_lift + btn_touch_tap(300, 400, t0=t0 + 13_000, hold=80))
    assert [(t[1], t[2], t[3]) for t in taps] == [("300", "400", "80")]


def test_real_zforce_hold_is_one_long_press():
    """Events recorded from a 3.9 s hold on the N905C (pressure 100/101 wobble, tiny drift)."""
    t0 = 1_700_000_000_000
    data = b"".join([
        ev(t0, EV_ABS, ABS_Y, 447), ev(t0, EV_ABS, ABS_X, 354),
        ev(t0, EV_ABS, ABS_PRESSURE, 101), ev(t0, EV_KEY, BTN_TOUCH, 1), syn(t0),
        ev(t0 + 448, EV_ABS, ABS_PRESSURE, 100), syn(t0 + 448),
        ev(t0 + 693, EV_ABS, ABS_Y, 446), ev(t0 + 693, EV_ABS, ABS_PRESSURE, 101), syn(t0 + 693),
        ev(t0 + 3860, EV_ABS, ABS_Y, 444), ev(t0 + 3860, EV_ABS, ABS_PRESSURE, 100), syn(t0 + 3860),
        ev(t0 + 3891, EV_ABS, ABS_PRESSURE, 0), ev(t0 + 3891, EV_KEY, BTN_TOUCH, 0), syn(t0 + 3891),
    ])
    taps = run_touch(data)
    assert len(taps) == 1 and taps[0][3] == "3891"


def test_down_lines_only_when_asked():
    tap = btn_touch_tap(100, 200, hold=4000)
    assert [t[0] for t in run_touch(tap)] == ["tap"]  # calibration sees taps only
    lines = run_touch(tap, downs=1)
    assert [t[:4] for t in lines] == [["down", "100", "200", "0"], ["tap", "100", "200", "4000"]]


def test_down_reported_once_per_press_even_with_pressure_wobble():
    t0 = 1_700_000_000_000
    data = b"".join([
        ev(t0, EV_ABS, ABS_X, 50), ev(t0, EV_ABS, ABS_Y, 60),
        ev(t0, EV_ABS, ABS_PRESSURE, 101), ev(t0, EV_KEY, BTN_TOUCH, 1), syn(t0),
        ev(t0 + 400, EV_ABS, ABS_PRESSURE, 100), syn(t0 + 400),
        ev(t0 + 700, EV_ABS, ABS_X, 51), ev(t0 + 700, EV_ABS, ABS_PRESSURE, 101), syn(t0 + 700),
        ev(t0 + 3000, EV_ABS, ABS_PRESSURE, 0), ev(t0 + 3000, EV_KEY, BTN_TOUCH, 0), syn(t0 + 3000),
    ])
    assert [t[0] for t in run_touch(data, downs=1)] == ["down", "tap"]
