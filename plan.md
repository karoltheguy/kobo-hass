---

Plan: e-ink Home Assistant control panel on a Kobo Touch (N905C)

Goal

Turn a Kobo Touch N905C into a wall or desk control panel for Home Assistant. It shows a dashboard (states of lights, scenes, climate and so on), and tapping a button runs an HA action.

Constraints

- Hardware: Kobo Touch N905C. 800 MHz single-core ARM (i.MX507), 256 MB RAM, 600×800 e-ink screen with 16 shades of grey, zForce IR touchscreen, no front light, old Linux (kernel 2.6.35, BusyBox). It probably has a physical Home button.
- Already tried: KoboWM was too slow and too clunky, so no desktop or window-manager approach.
- TLS: the Kobo's BusyBox wget probably can't do modern HTTPS. Use plain HTTP on the LAN.

Architecture: thin client

┌──────── Server (Python, always-on box) ────────┐        ┌──── Kobo (shell) ────┐
│ reads HA states over the REST API              │  PNG   │ fetches and shows    │
│ draws a 600x800 greyscale PNG with Pillow      │ ─────► │ the PNG with fbink   │
│ keeps the list of button areas (from layout)   │        │                      │
│ tap (x,y) → button → HA action                 │ ◄───── │ reads a tap, sends   │
│ stores the HA token                            │  HTTP  │ x,y to the server    │
└────────────────────────────────────────────────┘        └──────────────────────┘
All the logic lives on the server. The Kobo only shows images and reports taps. The HA token never goes on the Kobo.

Part 1: server (Python)

- Stack: Python 3, Pillow, requests or httpx, and a small web framework (FastAPI or Flask).
- Config:
  - config.yaml holds the HA URL, the token (or an env var for it) and the Kobo's screen size.
  - layout.yaml lists the buttons. Each one has an ID, a rectangle (x, y, w, h), a label, an HA entity, the action to call (e.g. light.toggle) and how to show its state (on/off fill, a value such as a temperature, an icon).
- HA API:
  - Read a state: GET /api/states/<entity_id>
  - Run an action: POST /api/services/<domain>/<service> with body {"entity_id": ...}
  - Send header Authorization: Bearer <token>
- Endpoints:
  - GET /dashboard.png: draws the current dashboard. Greyscale, 600×800, high contrast, big type, large tap targets (at least ~120 px).
  - GET /version: a cheap number or hash that changes whenever the rendered state changes. The Kobo polls it every few seconds so changes made elsewhere (phone, automations) show up.
  - POST /tap?x=..&y=..: finds the button under the tap, runs its action, waits a little for HA to update, then bumps the version. It returns what kind of screen update to do next (partial or full).
- Dev preview: an HTML page or CLI command that shows the PNG and lets you click it to simulate taps, so the dashboard can be built without the Kobo.
- Later: subscribe to HA's WebSocket API for instant state changes instead of polling.

Part 2: Kobo client (BusyBox shell, no Python)

Files go in /mnt/onboard/.adds/hadash/: hadash.sh, an fbink binary built for Kobo (NiLuJe's releases), and the WiFi scripts.

1. Stop Nickel. Kill nickel and its helpers (sickel, hindenburg, fickel and so on, depending on firmware). Nickel is what puts the device to sleep and turns WiFi off, so without it the device stays awake.
2. Bring WiFi up. Load the WiFi driver module, start wpa_supplicant with Nickel's saved config, then run udhcpc. Borrow KOReader's Kobo enable-wifi.sh and obtain-ip.sh, which already handle the per-model module names.
3. Main loop:
   - Download the PNG with wget, then fbink -g file=/tmp/dash.png.
   - Wait for a tap, map its coordinates, and wget --post-data to /tap.
   - In between, poll /version and redraw when it changes.
   - Do a full refresh (fbink -f) every N updates, or every few minutes, to clear ghosting. Use partial refreshes otherwise.
4. Reading taps from /dev/input/event1:
   - Each record is a 16-byte input_event on 32-bit ARM: timeval (8 bytes), type (u16), code (u16), value (s32).
   - Read it with dd bs=16 + od + awk. Collect ABS_X/ABS_Y (or ABS_MT_POSITION_X/_Y) and fire on finger lift (BTN_TOUCH value 0, or SYN_REPORT after ABS_MT_TRACKING_ID = -1).
   - The zForce touchscreen's axes are usually swapped and/or mirrored relative to the screen. Expect something like screen_x = y, screen_y = 800 - x, and find the real mapping in a calibration step.
   - Fallback if shell parsing is flaky: a tiny C helper built with NiLuJe's Kobo toolchain that blocks until a tap and prints x y.
5. Exit: the Home button (check which /dev/input/eventN it's on) or a long-press in one corner. On exit, stop WiFi and restart Nickel. Rebooting also works.
6. Launch: add a NickelMenu entry that runs hadash.sh. First check that NickelMenu supports your firmware version. If it doesn't, use KFMon.
7. Power: keep it plugged in. With WiFi always on, the battery won't last.

Milestones

1. Server MVP: draws a static layout from layout.yaml with live HA states, plus the dev preview with click-to-tap.
2. Kobo display: SSH or telnet in (e.g. via NiLuJe's dropbear package), stop Nickel by hand, bring WiFi up, show a fetched PNG with fbink.
3. Touch calibration: script that prints raw tap coordinates. Tap the four corners and work out the mapping.
4. Full loop: tap → server → HA action → redraw. Add the refresh strategy and /version polling.
5. Polish: NickelMenu launcher, exit gesture, recovery when WiFi drops or the server is down (show an error screen and retry), and a visual press response (invert the button right away).
6. Optional: WebSocket push from HA, multiple pages or tabs, a clock or weather area.

Still to decide

- Which machine runs the server (the HA host, a Pi, another always-on machine)?
- The first set of HA entity IDs and what each button should do.
- The Kobo's firmware version (matters for NickelMenu and for the list of processes to kill).
- Does the Kobo get a fixed IP, or does it find the server by hostname?

Useful references

- FBInk: github.com/NiLuJe/FBInk
- NickelMenu: github.com/pgaskin/NickelMenu
- KOReader's Kobo platform scripts (WiFi bring-up, stopping Nickel): github.com/koreader/koreader, under platform/kobo/
- MobileRead Kobo developer forum: NiLuJe's Kobo packages (dropbear SSH, toolchain, Python)
- hass-lovelace-kindle-screensaver: renders HA dashboards to PNG for e-readers. Display only, but useful as a reference.

---
