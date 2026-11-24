#!/bin/sh
# Touch calibration for the Kobo Touch (zForce IR panel).
#
#   sh calibrate.sh              stop Nickel, calibrate, write touch.conf
#   sh calibrate.sh --keep-nickel  leave Nickel running (it will also see the taps)
#   sh calibrate.sh --raw        just print raw taps until Ctrl-C
#
# Run it over SSH or telnet so you can watch the output.
#
# Steps: tap the + in three corners (top-left, top-right, bottom-left). The
# script works out swap/mirror/scale from those, saves touch.conf, then
# enters a test mode where each tap draws a marker where it landed. Hold a
# finger down for 1.5 s, anywhere, to finish the test.

HADASH_DIR=$(cd "$(dirname "$0")" && pwd)
export HADASH_DIR
. "$HADASH_DIR/lib.sh"

case $1 in
    --raw)
        echo "Raw taps from $TOUCH_DEV (Ctrl-C to stop):"
        echo "  kind  mapped_x mapped_y  duration_ms  raw_x raw_y  lift_sec"
        read_taps raw
        exit 0
        ;;
    --keep-nickel) ;;
    *) stop_nickel ;;
esac

trap 'trap - INT TERM; kill 0' INT TERM

M=28   # marker inset from the screen edge, px

marker() {  # marker <x> <y> <text>
    "$FBINK" -q -S 3 -X "$1" -Y "$2" "$3"
}

prompt() {  # prompt <where> <x> <y>
    "$FBINK" -q -c -f
    "$FBINK" -q -m -M -S 2 "Tap the + in the" "$1 corner"
    marker "$2" "$3" "+"
}

calibrate() {
    prompt "TOP-LEFT" 4 4
    read -r _ _ _ _ x y _ || exit 1; tl="$x $y"; echo "top-left:     raw $tl"

    prompt "TOP-RIGHT" $((SCREEN_W - M)) 4
    read -r _ _ _ _ x y _ || exit 1; tr="$x $y"; echo "top-right:    raw $tr"

    prompt "BOTTOM-LEFT" 4 $((SCREEN_H - M))
    read -r _ _ _ _ x y _ || exit 1; bl="$x $y"; echo "bottom-left:  raw $bl"

    awk -v W="$SCREEN_W" -v H="$SCREEN_H" -v tl="$tl" -v tr="$tr" -v bl="$bl" \
        -f "$HADASH_DIR/calib.awk" > "$RUN/touch.conf"
    {
        echo "# Written by calibrate.sh on $(date). Raw taps: TL=$tl TR=$tr BL=$bl"
        cat "$RUN/touch.conf"
    } > "$HADASH_DIR/touch.conf"
    echo
    echo "Saved $HADASH_DIR/touch.conf:"
    cat "$RUN/touch.conf"
}

# Same mapping as touch.awk, for the raw coordinates the test mode reads.
map_tap() {  # map_tap <raw_x> <raw_y>  -> sets mx my
    mx=$1; my=$2
    if [ "$TOUCH_SWAP" = 1 ]; then mx=$2; my=$1; fi
    [ "$TOUCH_RAW_W" -gt 0 ] && mx=$((mx * SCREEN_W / TOUCH_RAW_W))
    [ "$TOUCH_RAW_H" -gt 0 ] && my=$((my * SCREEN_H / TOUCH_RAW_H))
    [ "$TOUCH_MIRROR_X" = 1 ] && mx=$((SCREEN_W - 1 - mx))
    [ "$TOUCH_MIRROR_Y" = 1 ] && my=$((SCREEN_H - 1 - my))
}

# Test mode keeps reading the same raw stream (a second reader would only
# start once the first had seen one more event, losing the first test tap).
test_mapping() {
    . "$HADASH_DIR/touch.conf"
    "$FBINK" -q -c -f
    "$FBINK" -q -m -M -S 2 "Tap anywhere to test." "Hold 1.5 s to finish."
    echo
    echo "Test mode: a marker should appear under each tap. Hold to finish."
    while read -r _ _ _ dur rx ry _; do
        if [ "$dur" -ge "$LONGPRESS_MS" ]; then
            "$FBINK" -q -c -f
            "$FBINK" -q -m -M -S 2 "Calibration done." "touch.conf saved."
            echo "done"
            kill 0
        fi
        map_tap "$rx" "$ry"
        echo "tap screen $mx,$my (raw $rx,$ry)"
        # The glyph is ~24 px; centre it on the tap.
        px=$((mx - 12)); py=$((my - 12))
        [ $px -lt 0 ] && px=0
        [ $py -lt 0 ] && py=0
        marker "$px" "$py" "x"
    done
}

read_taps raw | { calibrate && test_mapping; }
