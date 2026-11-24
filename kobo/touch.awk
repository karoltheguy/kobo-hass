# Turns Linux input events into taps.
#
# Input: one input_event per line as 8 unsigned 16-bit numbers, as printed by
# `od -An -v -tu2` (or `od -d`, whose leading address column is skipped):
#   sec_lo sec_hi usec_lo usec_hi type code value_lo value_hi
# That is the 16-byte struct on 32-bit little-endian ARM.
#
# Output, one line per finger lift (flushed right away):
#   tap <screen_x> <screen_y> <duration_ms> <raw_x> <raw_y> <lift_epoch_sec>
# With -v downs=1, also one line as soon as a finger lands (for hold feedback):
#   down <screen_x> <screen_y> 0 <raw_x> <raw_y> <epoch_sec>
#
# Mapping variables (pass with -v; all default to 0 = off):
#   swap=1        raw x/y axes are swapped relative to the screen
#   raw_w, raw_h  raw range along screen x / screen y after the swap (0 = already pixels)
#   mirror_x=1    flip horizontally after scaling
#   mirror_y=1    flip vertically after scaling
#   W, H          screen size (default 600x800)

BEGIN {
    if (!W) W = 600
    if (!H) H = 800
    touching = 0; lift = 0; seen = 0
}

function down(t) {
    if (!touching) { touching = 1; t0 = t; fresh = 1 }
}

# Sets mx, my: the raw position mapped to screen pixels.
function map_xy(    x, y) {
    x = rx; y = ry
    if (swap) { x = ry; y = rx }
    if (raw_w > 0) x = int(x * W / raw_w)
    if (raw_h > 0) y = int(y * H / raw_h)
    if (mirror_x) x = W - 1 - x
    if (mirror_y) y = H - 1 - y
    if (x < 0) x = 0
    if (y < 0) y = 0
    if (x >= W) x = W - 1
    if (y >= H) y = H - 1
    mx = x; my = y
}

function emit(t) {
    map_xy()
    print "tap", mx, my, t - t0, rx, ry, int(t / 1000)
    fflush()
}

function emit_down(t) {
    map_xy()
    print "down", mx, my, 0, rx, ry, int(t / 1000)
    fflush()
}

$1 == "*" { next }   # od collapsed a repeated line; nothing useful to recover

{
    off = NF - 8
    if (off < 0) next
    sec  = $(off + 1) + $(off + 2) * 65536
    usec = $(off + 3) + $(off + 4) * 65536
    type = $(off + 5) + 0
    code = $(off + 6) + 0
    val  = $(off + 7) + $(off + 8) * 65536
    if (val >= 2147483648) val -= 4294967296
    t = sec * 1000 + int(usec / 1000)

    if (type == 3) {                                   # EV_ABS
        if (code == 0 || code == 53)      { rx = val; seen = 1 }   # ABS_X, ABS_MT_POSITION_X
        else if (code == 1 || code == 54) { ry = val; seen = 1 }   # ABS_Y, ABS_MT_POSITION_Y
        else if (code == 57) { if (val < 0) lift = 1; else down(t) }  # ABS_MT_TRACKING_ID
        else if (code == 24) { if (val == 0) lift = 1; else down(t) } # ABS_PRESSURE
    } else if (type == 1 && code == 330) {             # EV_KEY BTN_TOUCH
        # A press always restarts the clock: if the previous lift was missed,
        # its duration must not leak into this tap (a stale "long press"
        # could otherwise unlock a door).
        if (val == 0) lift = 1; else { touching = 1; t0 = t; fresh = 1 }
    } else if (type == 0 && code == 0) {               # EV_SYN SYN_REPORT
        if (seen && !lift) down(t)
        if (fresh && seen && !lift) { if (downs) emit_down(t); fresh = 0 }
        if (lift && touching && seen) emit(t)
        if (lift) { touching = 0; seen = 0; fresh = 0 }
        lift = 0
    }
}
