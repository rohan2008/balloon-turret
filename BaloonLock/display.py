"""
display.py  -  DISPLAY-ONLY helpers shared by the scripts:
    - letterboxing: show a frame as big as the window allows WITHOUT
      stretching it (leftover space is black bars)
    - zoom keys:    + / = bigger, - smaller, 0 original size
    - info bar:     columns of text below the video that can never overlap
    - window focus: is our window the active one? (Windows only)
    - read_all_keys: read EVERY waiting key each frame (fixes the freeze
      when a key is held down)

Nothing here ever changes the camera frame that detection and control use.
We build a SEPARATE picture (the "canvas") exactly the size of the window,
copy a scaled version of the frame into it, and draw text on the canvas.

Why not just use cv2.WINDOW_KEEPRATIO? In OpenCV that flag's value is 0 and
the Windows version of OpenCV ignores it, so windows stretched the picture
anyway. Because our canvas is always exactly the window's size, OpenCV has
nothing to stretch.
"""

import math
import sys

import cv2
import numpy as np

# Create windows with these flags: resizable, and no stretching by OpenCV
# (we do the fitting ourselves).
WINDOW_FLAGS = cv2.WINDOW_NORMAL | cv2.WINDOW_FREERATIO

MIN_WIDTH = 320             # window never narrower than this
ZOOM_STEP = 1.10            # 10% per key press

FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SCALES = (0.5, 0.45, 0.4, 0.35)   # info-bar text sizes to try, biggest first
MAX_BAR_FRACTION = 0.35     # the info bar may use at most 35% of the window height
MIN_COLUMN_WIDTH = 200      # don't split the info bar into columns narrower than this
PAD = 8                     # padding inside the info bar (pixels)


def nothing(value):
    """OpenCV requires a function to call when a slider moves.
    We read the sliders ourselves each frame, so this does nothing."""
    pass


def draw_center_crosshair(frame, center_x, center_y):
    """Draw a white crosshair at the middle of the image."""
    size = 20
    color = (255, 255, 255)  # OpenCV colors are (Blue, Green, Red)
    cv2.line(frame, (center_x - size, center_y), (center_x + size, center_y), color, 1)
    cv2.line(frame, (center_x, center_y - size), (center_x, center_y + size), color, 1)
    cv2.circle(frame, (center_x, center_y), 4, color, 1)


# ---------------------------------------------------------------------------
# Window size, screen size, focus
# ---------------------------------------------------------------------------
_user32 = None
if sys.platform.startswith("win"):
    try:
        import ctypes
        _user32 = ctypes.windll.user32
        _user32.GetForegroundWindow.restype = ctypes.c_void_p
        _user32.FindWindowW.restype = ctypes.c_void_p
    except (ImportError, AttributeError, OSError):
        _user32 = None


def screen_width():
    """Width of the main screen in pixels (falls back to 1920 if unknown)."""
    if _user32 is not None:
        return int(_user32.GetSystemMetrics(0))         # 0 = screen width
    return 1920


def window_has_focus(window_name):
    """True if the OpenCV window with this title is the active window.
    (Only checkable on Windows; elsewhere we assume it is.)"""
    if _user32 is None:
        return True
    our_window = _user32.FindWindowW(None, window_name)
    return bool(our_window) and _user32.GetForegroundWindow() == our_window


def window_size(window_name, fallback):
    """Current (width, height) of a window's picture area, or `fallback`
    if it can't be read (e.g. the window is minimized)."""
    try:
        _x, _y, w, h = cv2.getWindowImageRect(window_name)
        if w > 0 and h > 0:
            return w, h
    except (cv2.error, AttributeError):
        pass
    return fallback


# ---------------------------------------------------------------------------
# Letterboxing
# ---------------------------------------------------------------------------
class Letterbox:
    """Remembers where a frame was placed on the canvas:
        canvas position = frame position * scale + offset
    so overlays can be drawn at the right spot, and mouse clicks on the
    canvas can be turned back into frame positions."""

    def __init__(self, scale, off_x, off_y, frame_w, frame_h):
        self.scale = scale
        self.off_x = off_x
        self.off_y = off_y
        self.frame_w = frame_w
        self.frame_h = frame_h

    def to_canvas(self, x, y):
        return int(round(self.off_x + x * self.scale)), int(round(self.off_y + y * self.scale))

    def to_frame(self, cx, cy):
        """Canvas point -> frame point, or None if it's on a black bar."""
        x = (cx - self.off_x) / self.scale
        y = (cy - self.off_y) / self.scale
        if 0 <= x < self.frame_w and 0 <= y < self.frame_h:
            return int(x), int(y)
        return None


def letterbox_into(canvas, image, x0, y0, w, h):
    """Copy `image` into the rectangle (x0, y0, w, h) of `canvas`, as big as
    possible WITHOUT changing its shape. Returns a Letterbox."""
    ih, iw = image.shape[:2]
    w, h = max(w, 1), max(h, 1)
    scale = min(w / iw, h / ih)                 # the side that fits first wins
    new_w = max(1, int(round(iw * scale)))
    new_h = max(1, int(round(ih * scale)))
    # INTER_AREA gives the cleanest result when shrinking; INTER_LINEAR when enlarging
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (new_w, new_h), interpolation=interpolation)
    off_x = x0 + (w - new_w) // 2               # center it
    off_y = y0 + (h - new_h) // 2
    canvas[off_y:off_y + new_h, off_x:off_x + new_w] = resized
    return Letterbox(new_w / iw, off_x, off_y, iw, ih)


def letterbox(image, window_name):
    """Make a canvas the size of the window with `image` letterboxed in it.
    Returns (canvas, Letterbox)."""
    ih, iw = image.shape[:2]
    win_w, win_h = window_size(window_name, (iw, ih))
    canvas = np.zeros((win_h, win_w) + image.shape[2:], image.dtype)
    return canvas, letterbox_into(canvas, image, 0, 0, win_w, win_h)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def line_height(scale):
    """Height of one line of text, measured with cv2.getTextSize (+ a gap)."""
    (_w, h), baseline = cv2.getTextSize("Ag", FONT, scale, 1)
    return h + baseline + 5


def fit_text(text, max_width, scale):
    """Shorten `text` (ending in "..") until it fits in max_width pixels."""
    if cv2.getTextSize(text, FONT, scale, 1)[0][0] <= max_width:
        return text
    while len(text) > 1 and cv2.getTextSize(text + "..", FONT, scale, 1)[0][0] > max_width:
        text = text[:-1]
    return text + ".."


def shade(canvas, x, y, w, h, alpha=0.6):
    """Darken a rectangle (semi-transparent black) so text on it is readable."""
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, canvas.shape[1]), min(y + h, canvas.shape[0])
    if x1 > x0 and y1 > y0:
        region = canvas[y0:y1, x0:x1]
        canvas[y0:y1, x0:x1] = (region * (1 - alpha)).astype(canvas.dtype)


def draw_badge(canvas, text, x, y, color, scale=0.6):
    """Small label with a shaded background, e.g. "LASER ON" on the video."""
    x, y = int(x), int(y)          # OpenCV drawing needs whole-number positions
    (w, h), baseline = cv2.getTextSize(text, FONT, scale, 2)
    shade(canvas, x - 4, y - h - 4, w + 8, h + baseline + 8, alpha=0.6)
    cv2.putText(canvas, text, (x, y), FONT, scale, color, 2, cv2.LINE_AA)


# ---------------------------------------------------------------------------
# Info bar: groups of lines laid out in columns
# ---------------------------------------------------------------------------
# groups = [(title, [(text, (B, G, R)), ...]), ...]

class BarPlan:
    """The result of planning: how many columns, which font size, how tall."""

    def __init__(self, columns, scale, line_h, height):
        self.columns = columns      # list of columns; each is a list of groups
        self.scale = scale
        self.line_h = line_h
        self.height = height


def _rows(group):
    title, lines = group
    return 1 + len(lines)           # one row for the title + one per line


def _split_into_columns(groups, n_columns):
    """Put whole groups into columns, in order, keeping columns about equal."""
    total = sum(_rows(g) for g in groups)
    target = math.ceil(total / n_columns)
    columns = [[]]
    for group in groups:
        current_rows = sum(_rows(g) for g in columns[-1])
        if columns[-1] and current_rows + _rows(group) > target and len(columns) < n_columns:
            columns.append([])
        columns[-1].append(group)
    return columns


def plan_info_bar(groups, width, max_height):
    """Choose the biggest font, then the FEWEST columns, that fit in max_height.
    Columns are never narrower than MIN_COLUMN_WIDTH. If nothing fits, use
    the smallest font with as many columns as the width allows."""
    max_columns = max(1, min(3, width // MIN_COLUMN_WIDTH))
    for scale in FONT_SCALES:
        lh = line_height(scale)
        for n in range(1, max_columns + 1):
            columns = _split_into_columns(groups, n)
            rows = max(sum(_rows(g) for g in col) for col in columns)
            height = rows * lh + 2 * PAD
            if height <= max_height:
                return BarPlan(columns, scale, lh, height)
    # Nothing fit: smallest font, most columns (the bar will just be taller)
    scale = FONT_SCALES[-1]
    lh = line_height(scale)
    columns = _split_into_columns(groups, max_columns)
    rows = max(sum(_rows(g) for g in col) for col in columns)
    return BarPlan(columns, scale, lh, rows * lh + 2 * PAD)


def draw_info_bar(canvas, plan, top):
    """Draw the planned columns of text starting at row `top` of the canvas."""
    width = canvas.shape[1]
    shade(canvas, 0, top, width, canvas.shape[0] - top, alpha=0.7)
    cv2.line(canvas, (0, top), (width, top), (90, 90, 90), 1)
    col_w = width // len(plan.columns)
    for c, column in enumerate(plan.columns):
        x = c * col_w + PAD
        y = top + PAD
        for title, lines in column:
            for i, (text, color) in enumerate([(title, (255, 200, 0))] + list(lines)):
                y += plan.line_h
                if y > canvas.shape[0] - 2:
                    return                                  # out of room: stop drawing
                thickness = 2 if i == 0 else 1              # titles in bold
                cv2.putText(canvas, fit_text(text, col_w - 2 * PAD, plan.scale), (x, y - 5),
                            FONT, plan.scale, color, thickness, cv2.LINE_AA)


def bar_height_for(groups, width, video_height):
    """Info-bar height for a window `width` wide whose video area is
    `video_height` tall (used when zooming, so the window gets room for both)."""
    max_height = MAX_BAR_FRACTION / (1 - MAX_BAR_FRACTION) * video_height
    return plan_info_bar(groups, width, max_height).height


def render_with_info_bar(window_name, frame, groups, draw_overlays=None):
    """Build the full display canvas for a window:
        [ video, letterboxed, with small overlays ]
        [ info bar with the text groups           ]
    draw_overlays(canvas, letterbox) is called to draw crosshairs/boxes at
    the right (scaled) positions. Returns the canvas."""
    fh, fw = frame.shape[:2]
    fallback_h = fh + bar_height_for(groups, fw, fh)
    win_w, win_h = window_size(window_name, (fw, fallback_h))
    plan = plan_info_bar(groups, win_w, MAX_BAR_FRACTION * win_h)
    bar_h = min(plan.height, win_h - 40)            # always leave some room for video
    video_h = win_h - bar_h
    canvas = np.zeros((win_h, win_w, 3), np.uint8)
    box = letterbox_into(canvas, frame, 0, 0, win_w, video_h)
    if draw_overlays is not None:
        draw_overlays(canvas, box)
    draw_info_bar(canvas, plan, video_h)
    return canvas


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
MAX_KEYS_PER_FRAME = 100    # safety cap when emptying the key queue


def read_all_keys():
    """Return EVERY key waiting in the window's queue (oldest first), as a
    list of key codes (empty if none).

    cv2.waitKey(1) gives back only ONE key. A held key auto-repeats ~30x per
    second, so with a ~14 FPS loop keys piled up in Windows' message queue
    faster than we read them - and Windows only repaints a window when that
    queue is empty, so the picture FROZE while a key was held (and 'q' had to
    wait behind all the queued keys). cv2.pollKey() reads the next waiting
    key without waiting, so we keep calling it until the queue is empty."""
    keys = []
    key = cv2.waitKey(1)
    poll = getattr(cv2, "pollKey", None)        # pollKey exists in OpenCV 4.5+
    while key != -1 and len(keys) < MAX_KEYS_PER_FRAME:
        keys.append(key & 0xFF)
        key = poll() if poll is not None else -1
    return keys


# ---------------------------------------------------------------------------
# Zoom keys
# ---------------------------------------------------------------------------
class WindowZoom:
    """+ / = bigger, - smaller, 0 original size, for one or more windows
    (resized together). The zoom keeps the video's shape; extra_height(width,
    video_height) can add room below the video (for an info bar)."""

    def __init__(self, window_names, extra_height=None):
        self.names = list(window_names)
        self.extra_height = extra_height
        self.frame_size = None
        self.zoom = 1.0
        self.max_width = screen_width()

    def set_base(self, frame_w, frame_h):
        """Call with the camera frame size. The first call sizes the windows
        to zoom 1.0 (one screen pixel per camera pixel)."""
        if self.frame_size is None:
            self.frame_size = (frame_w, frame_h)
            self._apply(1.0)

    def handle_key(self, key):
        """Handle + = - 0. Returns True if the key was one of ours."""
        if self.frame_size is None:
            return False
        if key in (ord("+"), ord("=")):
            self._apply(self._current_zoom() * ZOOM_STEP)
        elif key == ord("-"):
            self._apply(self._current_zoom() / ZOOM_STEP)
        elif key == ord("0"):
            self._apply(1.0)
        else:
            return False
        return True

    def _current_zoom(self):
        """Zoom right now, from the actual window width (it may have been dragged)."""
        width, _h = window_size(self.names[0], (0, 0))
        return width / self.frame_size[0] if width > 0 else self.zoom

    def _apply(self, zoom):
        frame_w, frame_h = self.frame_size
        width = int(round(max(MIN_WIDTH, min(frame_w * zoom, self.max_width))))
        self.zoom = width / frame_w
        video_h = int(round(frame_h * self.zoom))
        extra = self.extra_height(width, video_h) if self.extra_height else 0
        for name in self.names:
            cv2.resizeWindow(name, width, video_h + extra)
