#!/bin/sh
# Home Assistant dashboard client for the Kobo Touch.
#
#   sh hadash.sh             normal start (stops Nickel, needs hadash.conf)
#   HADASH_FG=1 sh hadash.sh   run in the foreground over SSH; Ctrl-C stops it
#   sh hadash.sh --keep-nickel   don't stop Nickel (quick display tests)
#
# Three loops run side by side:
#   taps    finger lift -> GET /tap -> redraw if the server says so
#   poller  GET /version every POLL_SECS -> redraw when it changes
#   home    Home button -> exit
# Redraws are serialised with a lock directory, since two loops can draw.

HADASH_DIR=$(cd "$(dirname "$0")" && pwd)
export HADASH_DIR

# Own process group, so `kill 0` on exit takes every child with it
# (blocked dd readers included) and nothing else.
if [ -z "$HADASH_FG" ] && [ -z "$HADASH_SESSION" ] && command -v setsid >/dev/null 2>&1; then
    HADASH_SESSION=1 exec setsid /bin/sh "$0" "$@"
fi

. "$HADASH_DIR/lib.sh"

KEEP_NICKEL=0
[ "$1" = "--keep-nickel" ] && KEEP_NICKEL=1

if [ -z "$SERVER" ]; then
    echo "hadash: set SERVER in $HADASH_DIR/hadash.conf" >&2
    "$FBINK" -q -m -M "hadash: SERVER is not set in hadash.conf" 2>/dev/null
    exit 1
fi

if [ -f "$RUN/pid" ] && kill -0 "$(cat "$RUN/pid")" 2>/dev/null; then
    echo "hadash: already running" >&2
    exit 0
fi
echo $$ > "$RUN/pid"
rm -rf "$RUN/lock" "$RUN/offline" "$RUN/version" "$RUN/count"
date +%s > "$RUN/last_full"
touch "$RUN/needfull"

trap 'trap - INT TERM; rm -f "$RUN/pid"; kill 0' INT TERM

# -- drawing ----------------------------------------------------------------

lock() {
    n=0
    until mkdir "$RUN/lock" 2>/dev/null; do
        n=$((n + 1))
        # A holder that died leaves the lock behind; take it over after ~10 s.
        [ $n -gt 100 ] && { rm -rf "$RUN/lock"; n=0; }
        msleep 100
    done
}
unlock() { rmdir "$RUN/lock" 2>/dev/null; }

# Shown once when the server can't be reached; cleared by the next good draw.
show_offline() {
    [ -f "$RUN/offline" ] && return
    touch "$RUN/offline" "$RUN/needfull"
    rm -f "$RUN/version"
    log "server unreachable"
    fb_message "Can't reach the dashboard server" "$SERVER" "Retrying..."
}

# draw: fetch version + image, show it with a partial or full refresh.
draw() {
    lock
    if http_get /version "$RUN/version.new" && http_get /dashboard.png "$RUN/dash.tmp" \
            && [ -s "$RUN/dash.tmp" ]; then
        mv "$RUN/dash.tmp" "$RUN/dash.png"
        mv "$RUN/version.new" "$RUN/version"
        count=$(cat "$RUN/count" 2>/dev/null || echo 0)
        last_full=$(cat "$RUN/last_full" 2>/dev/null || echo 0)
        now=$(date +%s)
        full=0
        if [ -f "$RUN/needfull" ] || [ "$count" -ge "$FULL_EVERY" ] \
                || [ $((now - last_full)) -ge "$FULL_SECS" ]; then
            full=1
        fi
        fb_show "$RUN/dash.png" $full
        if [ $full = 1 ]; then
            echo 0 > "$RUN/count"; echo "$now" > "$RUN/last_full"
            rm -f "$RUN/needfull"
        else
            echo $((count + 1)) > "$RUN/count"
        fi
        rm -f "$RUN/offline"
    else
        show_offline
    fi
    unlock
}

# -- exit -------------------------------------------------------------------

exit_dashboard() {
    log "exit ($EXIT_ACTION)"
    rm -f "$RUN/pid"
    case $EXIT_ACTION in
        nickel)
            if command -v setsid >/dev/null 2>&1; then
                fb_message "Starting Nickel..."
                # setsid: keep Nickel out of our process group, which dies below.
                setsid /bin/sh -c ". '$HADASH_DIR/lib.sh'; start_nickel" &
            else
                fb_message "Restarting..."; sync; reboot
            fi
            ;;
        none)
            fb_message "hadash stopped"
            ;;
        *)
            fb_message "Restarting..."
            sync
            reboot
            ;;
    esac
    kill 0
}

# -- loops ------------------------------------------------------------------

# Once a day at NIGHTLY_FULL (HH:MM, Kobo local time): flash the screen to
# white, then redraw with a full refresh. Clears any faint image retention
# from a page that stayed up all day. Runs at most once per date.
nightly_refresh() {
    [ -n "$NIGHTLY_FULL" ] || return 0
    [ "$(date +%H:%M)" = "$NIGHTLY_FULL" ] || return 0
    today=$(date +%Y-%m-%d)
    [ "$(cat "$RUN/nightly" 2>/dev/null)" = "$today" ] && return 0
    echo "$today" > "$RUN/nightly"
    log "nightly full refresh"
    lock; "$FBINK" -q -c -f; msleep 500; "$FBINK" -q -c -f -h; msleep 500
    "$FBINK" -q -c -f; touch "$RUN/needfull"; unlock
    draw
}

poller() {
    fails=0
    while :; do
        sleep "$POLL_SECS"
        nightly_refresh
        if http_get /version "$RUN/version.poll"; then
            [ $fails -gt 0 ] && log "poll ok again after $fails failure(s)"
            fails=0
            if [ "$(cat "$RUN/version.poll")" != "$(cat "$RUN/version" 2>/dev/null)" ]; then
                draw
            fi
        else
            # One lost request is normal on WiFi; only show the offline
            # screen after OFFLINE_AFTER failures in a row.
            fails=$((fails + 1))
            log "poll failed ($fails): $(last_http_error)"
            if [ $fails -ge "$OFFLINE_AFTER" ]; then
                lock; show_offline; unlock
            fi
        fi
    done
}

# hold_feedback <x> <y> <token>  Runs in the background when a finger lands.
# On a button with a long press (e.g. unlock), it shows the button pressed
# after 1 s and back to normal once the hold time has passed, so you know when
# to lift. It stops quietly as soon as the finger lifts: handle_taps removes
# $RUN/hold, and the token is checked under the draw lock before every draw.
hold_feedback() {
    # $WGET_T is empty or "-T 10", and $resp is split into words: both unquoted on purpose.
    # shellcheck disable=SC2086
    resp=$(wget -q $WGET_T -O - "$SERVER/press?x=$1&y=$2" 2>/dev/null)
    token=$3
    # shellcheck disable=SC2086
    set -- $resp
    [ "$1" = long ] || return 0
    id=$2; ms=$3
    http_get "/dashboard.png?pressed=$id" "$RUN/pressed.png" &
    msleep 1000
    wait
    [ -s "$RUN/pressed.png" ] || return 0
    holding() { [ "$(cat "$RUN/hold" 2>/dev/null)" = "$token" ]; }
    lock; holding && fb_show "$RUN/pressed.png" 0; unlock
    # Everything before this point took at least 1 s, so the release cue
    # always comes after the server will count the press as long.
    msleep $((ms - 1000))
    lock
    if holding; then
        fb_show "$RUN/dash.png" 0
        log "hold $id: long press reached, lift now"
    fi
    unlock
}

handle_taps() {
    # Taps made while an earlier tap was still being handled are dropped,
    # so impatient double taps don't toggle a light twice.
    busy_until=0
    n=0
    while read -r kind x y dur rx ry lift_sec; do
        if [ "$kind" = down ]; then
            # Not subject to the busy drop: a hold often starts while the
            # previous tap (e.g. a slow Z-Wave lock call) is still running,
            # and its feedback must still show. If this press's lift ends up
            # dropped, removing $RUN/hold below stops the feedback anyway.
            n=$((n + 1))
            echo "$n" > "$RUN/hold"
            hold_feedback "$x" "$y" "$n" &
            continue
        fi
        [ "$kind" = tap ] || continue
        rm -f "$RUN/hold"
        log "tap $x,$y ${dur}ms (raw $rx,$ry)"
        if [ "$lift_sec" -lt "$busy_until" ]; then
            log "dropped (queued during previous tap)"
            continue
        fi
        if [ "$dur" -ge "$LONGPRESS_MS" ] && [ "$x" -lt 120 ] && [ "$y" -lt 120 ]; then
            exit_dashboard
        fi
        # Unquoted on purpose, as in hold_feedback.
        # shellcheck disable=SC2086
        resp=$(wget -q $WGET_T -O - "$SERVER/tap?x=$x&y=$y&ms=$dur" 2>/dev/null)
        # shellcheck disable=SC2086
        set -- $resp
        case $1 in
            full) touch "$RUN/needfull"; draw ;;
            partial) draw ;;
            none) ;;
            *) lock; show_offline; unlock ;;
        esac
        busy_until=$(date +%s)
    done
}

# -- start ------------------------------------------------------------------

log "start (server $SERVER)"
"$FBINK" -q -c -f
"$FBINK" -q -m -M -S 2 "Starting dashboard..."

[ $KEEP_NICKEL = 1 ] || stop_nickel

tries=0
until server_ok; do
    tries=$((tries + 1))
    if [ $tries = 3 ]; then
        log "server not reachable, trying to bring WiFi up"
        wifi_up
    fi
    [ $tries = 3 ] && fb_message "Waiting for the network..." "$SERVER"
    sleep 3
done

draw

poller &
on_home_key exit_dashboard &
read_taps | handle_taps
