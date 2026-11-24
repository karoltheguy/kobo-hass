# shellcheck shell=sh
# The settings below are read by the scripts that source this file.
# shellcheck disable=SC2034
# Shared helpers for hadash.sh and calibrate.sh. BusyBox ash, no bashisms.
# Sourced, not run.

HADASH_DIR=${HADASH_DIR:-/mnt/onboard/.adds/hadash}
RUN=${RUN:-/tmp/hadash}
mkdir -p "$RUN"

# -- config ---------------------------------------------------------------

SERVER=""
SECRET=""
SCREEN_W=600
SCREEN_H=800
TOUCH_DEV=/dev/input/event1
KEY_DEV=/dev/input/event0
TOUCH_SWAP=0
TOUCH_MIRROR_X=0
TOUCH_MIRROR_Y=0
TOUCH_RAW_W=0
TOUCH_RAW_H=0
POLL_SECS=3
OFFLINE_AFTER=3
NIGHTLY_FULL=03:00
FULL_EVERY=10
FULL_SECS=300
EXIT_ACTION=reboot
LONGPRESS_MS=1500

[ -f "$HADASH_DIR/hadash.conf" ] && . "$HADASH_DIR/hadash.conf"
# Written by calibrate.sh; measured values win over hadash.conf.
[ -f "$HADASH_DIR/touch.conf" ] && . "$HADASH_DIR/touch.conf"

FBINK_BIN="$HADASH_DIR/fbink"
[ -x "$FBINK_BIN" ] || FBINK_BIN=fbink
# FBInk reads text from stdin when it isn't a terminal. Our draw calls often
# run inside a pipeline fed by the touch reader, where that read never ends
# (seen on the device: `fbink -c -f` stuck in pipe_wait). Always give it
# /dev/null. Callers keep using "$FBINK", which now names this function.
fbink_run() { "$FBINK_BIN" "$@" </dev/null; }
FBINK=fbink_run

log() {
    echo "$(date '+%H:%M:%S') $*" >> "$RUN/log"
    # Keep the log small; /tmp is RAM.
    if [ "$(wc -c < "$RUN/log")" -gt 65536 ]; then
        tail -n 200 "$RUN/log" > "$RUN/log.tmp" && mv "$RUN/log.tmp" "$RUN/log"
    fi
}

msleep() {
    # usleep is a BusyBox applet on most firmware; fall back to whole seconds.
    usleep $(($1 * 1000)) 2>/dev/null || sleep $((($1 + 999) / 1000))
}

# -- HTTP -----------------------------------------------------------------

# Old BusyBox wget may lack -T (timeout). Use it when it's there, so a dead
# server can't hang the loop forever.
WGET_T=""
if wget --help 2>&1 | grep -q -- '-T '; then WGET_T="-T 10"; fi

# with_key <path>  The path with the server's shared secret added (?key=... or
# &key=...) when SECRET is set. Every request to the server goes through this.
with_key() {
    if [ -z "$SECRET" ]; then
        echo "$1"
        return
    fi
    case $1 in
        *\?*) echo "$1&key=$SECRET" ;;
        *) echo "$1?key=$SECRET" ;;
    esac
}

# http_get <path> <outfile>   (outfile "-" prints to stdout)
http_get() {
    # shellcheck disable=SC2086
    # Keep wget's complaint (DNS, timeout, refused...) for the failure log.
    wget -q $WGET_T -O "$2" "$SERVER$(with_key "$1")" 2>"$RUN/wget.err"
}

# last_http_error  One line describing why the last http_get failed.
last_http_error() {
    tr '\n' ' ' <"$RUN/wget.err" 2>/dev/null | cut -c1-120
}

# -- screen ---------------------------------------------------------------

# fb_show <png> <full: 0|1>
fb_show() {
    if [ "$2" = 1 ]; then
        "$FBINK" -q -f -g file="$1"
    else
        "$FBINK" -q -g file="$1"
    fi
}

# fb_message <text...>  Clears the screen and prints centred text.
fb_message() {
    "$FBINK" -q -c -f
    "$FBINK" -q -m -M -S 2 "$@"
}

# -- input ----------------------------------------------------------------

# Pick an od invocation that prints 8 unsigned 16-bit values per 16 bytes.
if echo | od -An -v -tu2 >/dev/null 2>&1; then
    OD="od -An -v -tu2"
else
    OD="od -d"   # traditional BusyBox od: address column + unsigned shorts
fi

# read_events <device>
# Streams events as text lines. Each dd does one read(), which returns every
# event queued so far, and od exits after each batch, so its output is
# flushed straight away (a long-running od would sit on a 4 KB buffer).
# The device stays open on fd 3 for the whole loop: evdev only queues events
# for an open file, so reopening it per read (dd if=...) lost whatever came
# in between, e.g. the finger lift at the end of a long press (seen on the
# N905C: a 3 s hold was never reported, the next tap came out as 13 s).
read_events() {
    exec 3<"$1" || return
    while :; do
        # od dies of SIGPIPE once nobody reads the stream; stop then.
        dd bs=4096 count=1 <&3 2>/dev/null | $OD || break
    done
    exec 3<&-
}

# read_taps [raw]  Prints "tap x y duration_ms raw_x raw_y lift_sec" per finger lift.
# With "raw", the mapping is skipped (used by calibration). Without it, a
# "down x y 0 raw_x raw_y sec" line also comes as soon as a finger lands.
read_taps() {
    if [ "$1" = raw ]; then
        read_events "$TOUCH_DEV" | awk -v W="$SCREEN_W" -v H="$SCREEN_H" \
            -f "$HADASH_DIR/touch.awk"
    else
        read_events "$TOUCH_DEV" | awk -v W="$SCREEN_W" -v H="$SCREEN_H" \
            -v swap="$TOUCH_SWAP" -v mirror_x="$TOUCH_MIRROR_X" \
            -v mirror_y="$TOUCH_MIRROR_Y" -v raw_w="$TOUCH_RAW_W" \
            -v raw_h="$TOUCH_RAW_H" -v downs=1 -f "$HADASH_DIR/touch.awk"
    fi
}

# on_home_key <command...>  Runs the command when the Home button (KEY_HOME,
# code 102) is pressed. The command runs inside the pipeline: waiting for the
# pipeline to end would also wait for the dd reader, which only notices the
# pipe is gone after one more input event.
on_home_key() {
    read_events "$KEY_DEV" | awk '
        { off = NF - 8 }
        off >= 0 && $(off + 5) == 1 && $(off + 6) == 102 && $(off + 7) == 1 { print "home"; fflush() }
    ' | while read -r _; do "$@"; done
}

# -- Nickel ---------------------------------------------------------------

# Copy the variables Nickel was started with (PLATFORM, WIFI_MODULE,
# INTERFACE, ...). The WiFi bring-up and a Nickel restart both need them.
import_nickel_env() {
    pid=$(pidof nickel 2>/dev/null | cut -d' ' -f1)
    [ -n "$pid" ] && [ -r "/proc/$pid/environ" ] || return 0
    tr '\0' '\n' < "/proc/$pid/environ" | while IFS='=' read -r k v; do
        case $k in
            PLATFORM|PRODUCT|MODEL_NUMBER|WIFI_MODULE|INTERFACE|LANG|QWS_*|QT_*)
                echo "export $k='$v'" ;;
        esac
    done > "$RUN/nickel.env"
    . "$RUN/nickel.env"
}

# Stop Nickel and its helpers. Leaves the WiFi daemons (wpa_supplicant,
# dhcpcd/udhcpc) alone so a connection Nickel already made stays up.
stop_nickel() {
    import_nickel_env
    killall -q -TERM nickel hindenburg sickel fickel strickel fontickel \
        adobehost foxitpdf iink dhcpcd-dbus 2>/dev/null
    n=0
    while pidof nickel >/dev/null 2>&1 && [ $n -lt 20 ]; do
        msleep 250; n=$((n + 1))
    done
    pidof nickel >/dev/null 2>&1 && killall -q -KILL nickel 2>/dev/null
    log "nickel stopped"
}

start_nickel() {
    [ -f "$RUN/nickel.env" ] && . "$RUN/nickel.env"
    export PLATFORM PRODUCT WIFI_MODULE INTERFACE
    [ -x /usr/local/Kobo/hindenburg ] && /usr/local/Kobo/hindenburg &
    LIBC_FATAL_STDERR_=1 /usr/local/Kobo/nickel -platform kobo -skipFontLoad &
}

# -- WiFi -----------------------------------------------------------------

# Best effort, untested on the device: brings WiFi up if Nickel left it down.
# Based on the usual Kobo layout (modules under /drivers/$PLATFORM/wifi).
# Verify on the device in milestone 2 and adjust.
wifi_up() {
    iface=${INTERFACE:-eth0}
    module=${WIFI_MODULE:-ar6000}
    if ! grep -q "^$module " /proc/modules 2>/dev/null; then
        for m in sdio_wifi_pwr "$module"; do
            f="/drivers/$PLATFORM/wifi/$m.ko"
            [ -f "$f" ] || f="/drivers/$PLATFORM/$m.ko"
            [ -f "$f" ] && insmod "$f" 2>/dev/null
        done
        msleep 2000
    fi
    ifconfig "$iface" up 2>/dev/null
    if ! pidof wpa_supplicant >/dev/null 2>&1; then
        wpa_supplicant -D wext -s -i "$iface" -O /var/run/wpa_supplicant \
            -c /etc/wpa_supplicant/wpa_supplicant.conf -B
        msleep 3000
    fi
    if ! pidof udhcpc dhcpcd >/dev/null 2>&1; then
        udhcpc -S -i "$iface" -s /etc/udhcpc.d/default.script -t 15 -T 5 -A 3 -b -q -n
    fi
}

# Ready when the server answers /version.
server_ok() {
    http_get /version "$RUN/version.check" && [ -s "$RUN/version.check" ]
}
