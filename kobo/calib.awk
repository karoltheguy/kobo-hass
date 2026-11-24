# Works out the touch-to-screen mapping from three raw taps.
#
#   awk -v W=600 -v H=800 -v tl="x y" -v tr="x y" -v bl="x y" -f calib.awk
#
# tl/tr/bl are the raw coordinates reported for taps near the top-left,
# top-right and bottom-left corners of the screen. Prints hadash.conf lines.

function abs(v) { return v < 0 ? -v : v }

# Full raw range along one axis, from two taps near opposite edges.
# Assumes the taps are about the same distance from their edges, so
# range ~= low + high. If that lands within 15% of the screen size, the
# panel already reports pixels and no scaling is needed.
function range(a, b, screen,    lo, hi, est) {
    lo = a < b ? a : b
    hi = a < b ? b : a
    est = lo + hi
    if (abs(est - screen) <= screen * 0.15) return 0
    return int(est + 0.5)
}

BEGIN {
    if (!W) W = 600
    if (!H) H = 800
    split(tl, a, " "); split(tr, b, " "); split(bl, c, " ")

    # Moving right on screen (TL -> TR): which raw axis changed more?
    swap = abs(b[2] - a[2]) > abs(b[1] - a[1]) ? 1 : 0
    if (swap) {
        ax = a[2]; ay = a[1]; bx = b[2]; cy = c[1]
    } else {
        ax = a[1]; ay = a[2]; bx = b[1]; cy = c[2]
    }
    mirror_x = bx < ax ? 1 : 0
    mirror_y = cy < ay ? 1 : 0
    raw_w = range(ax, bx, W)
    raw_h = range(ay, cy, H)

    print "TOUCH_SWAP=" swap
    print "TOUCH_MIRROR_X=" mirror_x
    print "TOUCH_MIRROR_Y=" mirror_y
    print "TOUCH_RAW_W=" raw_w
    print "TOUCH_RAW_H=" raw_h
}
