# Kobo client

BusyBox shell scripts that show the server's PNG with FBInk and send taps back.
Nothing here needs Python on the Kobo.

The client knows nothing about buttons or entities. What's on screen and what each
tap does is set in `layout.yaml` on the server; see
[Layout](../README.md#layout) in the main README. Layout changes show up on the
next poll, with no need to copy anything to the Kobo.

| File | What it does |
|---|---|
| `hadash.sh` | The dashboard: stops Nickel, draws, sends taps, polls `/version`, exits on Home |
| `calibrate.sh` | Works out how touch coordinates map to the screen, writes `touch.conf` |
| `lib.sh` | Shared helpers (config, input reading, FBInk, Nickel, WiFi) |
| `touch.awk` | Turns raw `input_event`s into `tap x y ...` lines |
| `calib.awk` | Turns three corner taps into swap/mirror/scale settings |
| `hadash.conf.example` | Settings; copy to `hadash.conf` |
| `nm/hadash` | NickelMenu launcher entry |

## Install

Plug the Kobo in over USB and copy into `.adds/hadash/` on the Kobo drive:

```
.adds/hadash/
  hadash.sh  calibrate.sh  lib.sh  touch.awk  calib.awk
  hadash.conf        (from hadash.conf.example, with SERVER set)
  fbink              (ARM FBInk binary WITH image support, see below)
```

FBInk must be built with image support. The `fbink` inside KOReader's Kobo
package is not ("Image support is disabled in this FBInk build!"). A static
musl build works on the N905C's 2.6.35 kernel and can be made with Zig:

```sh
git clone --depth 1 --branch v1.25.0 --recurse-submodules --shallow-submodules \
    https://github.com/NiLuJe/FBInk.git && cd FBInk
ZIG="uvx --from ziglang python -m ziglang"
# wrapper scripts, since the Makefile wants single-word tools
printf '#!/bin/sh\nexec %s cc -target arm-linux-musleabihf -mcpu=cortex_a8 "$@"\n' "$ZIG" > /tmp/arm-cc
printf '#!/bin/sh\nexec %s ar "$@"\n' "$ZIG" > /tmp/arm-ar
printf '#!/bin/sh\nexec %s ranlib "$@"\n' "$ZIG" > /tmp/arm-ranlib
chmod +x /tmp/arm-cc /tmp/arm-ar /tmp/arm-ranlib
M="CC=/tmp/arm-cc AR=/tmp/arm-ar RANLIB=/tmp/arm-ranlib STRIP=true KOBO=true"
make $M libi2c.built && make $M LDFLAGS=-static static   # -> Release/fbink
```

`SERVER` can be an IP address or a normal DNS name (e.g.
`http://kobo.example.net:8090`); the Kobo uses the DNS server it gets from
DHCP and resolves the name on every request, so repointing the name in DNS
switches servers. mDNS `.local` names don't work. A fixed IP for the Kobo
itself (a DHCP reservation) still helps for telnet/SSH.

## Bring-up on the device (milestones 2 and 3)

Over SSH or telnet (NiLuJe's dropbear package, or the telnet trick):

1. **Check the input devices.** `cat /proc/bus/input/devices`. Note which
   `eventN` is the zForce touchscreen and which has the Home key, and fix
   `TOUCH_DEV` / `KEY_DEV` if they differ from event1 / event0.
2. **Check raw taps.** `sh calibrate.sh --raw` with Nickel still running. Tap
   a few times and make sure you get exactly one `tap` line per tap. If you
   get none, run `od -An -v -tu2 /dev/input/event1` while tapping and send me
   the output: the parser may need another event code.
3. **Show an image.** `HADASH_FG=1 sh hadash.sh --keep-nickel` draws the
   dashboard over Nickel without stopping it. Ctrl-C to stop. Check the image
   isn't rotated or offset.
4. **Calibrate.** `sh calibrate.sh`. It stops Nickel, asks for three corner
   taps, saves `touch.conf`, then draws an `x` wherever you tap. Hold 1.5 s
   to finish. Reboot afterwards to get Nickel back.
5. **Full run.** `HADASH_FG=1 sh hadash.sh` with WiFi already connected in
   Nickel. Tap buttons, change things from your phone, press Home.

`/tmp/hadash/log` has every tap with its raw and mapped coordinates.

## Launching from NickelMenu

Check that NickelMenu supports your firmware, install it, then copy
`nm/hadash` to `.adds/nm/hadash`. An "HA Dashboard" item appears in the main
menu. Turn WiFi on in Nickel before launching: the script keeps Nickel's
connection and only tries its own bring-up if the server can't be reached.

## Exiting

Press Home, or hold a finger for 1.5 s in the top-left corner (the title bar).
`EXIT_ACTION` picks what happens next: `reboot` (default), `nickel`
(restarts Nickel, untested) or `none`.

## What hasn't been tested on a real Kobo

Everything here was run on a desktop with FIFOs standing in for the input
devices and a stub FBInk. These parts depend on the device and are the most
likely to need changes:

- Which events the zForce panel actually sends (the parser accepts BTN_TOUCH,
  ABS_PRESSURE and multitouch tracking IDs for finger lift).
- BusyBox applets: `od -An -tu2` (falls back to `od -d`), `usleep`, `setsid`,
  `wget -T`, `fflush()` in awk.
- `wifi_up` in `lib.sh` (module paths and wpa_supplicant options are the usual
  Kobo ones, not checked on the N905C).
- The Nickel process list and `start_nickel`.
- FBInk options: `-X`/`-Y` pixel offsets for the calibration markers, and how
  the framebuffer's rotation looks after Nickel is gone.
