"""OmaSkins Manager — browse and manage Omarchy themes, backgrounds and fonts.

The window only. Whatever changes the system goes through run.py and its allow-list;
data.py describes each action (see `Action`) and reads everything shown here.
"""

import itertools
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import data, qtstyle, rotation, run, share, theme  # noqa: E402

APP_ID = "io.github.jesuslovesyou1013.omaskins"
TITLE = "OmaSkins Manager"
CHECK = "\U000f012c"  # nf-md-check — a plain mark, never a checkbox (same as OmaPlugs)
CARD_W, CARD_H = 272, 153  # 16:9, the shape of omarchy.org screenshots


# --------------------------------------------------------------------------- small helpers

_DEBUG = os.environ.get("OMASKINS_DEBUG") == "1"
_T0 = GLib.get_monotonic_time()


def dlog(*what):
    """OMASKINS_DEBUG=1: timestamped trace of refreshes, to find what makes the window lag."""
    if _DEBUG:
        print(f"[{(GLib.get_monotonic_time() - _T0) / 1e6:9.3f}s]", *what, file=sys.stderr, flush=True)


def bg(fn, done=None):
    """Run fn on a worker thread; deliver its result to `done` on the UI thread."""
    def work():
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            result = e
        if done:
            GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=work, daemon=True).start()


def esc(text):
    return GLib.markup_escape_text(str(text))


def label(text="", css="", xalign=0, **kw):
    lab = Gtk.Label(label=text, xalign=xalign, **kw)
    for c in css.split():
        lab.add_css_class(c)
    return lab


def badge(text, kind=""):
    lab = label(text, "badge", xalign=0.5, valign=Gtk.Align.CENTER)
    if kind:
        lab.add_css_class(f"badge-{kind}")
    return lab


def button(text, css="", callback=None, tooltip=None):
    b = Gtk.Button(label=text, tooltip_text=tooltip)
    b.add_css_class("omarchy-btn")
    for c in css.split():
        b.add_css_class(c)
    if callback:
        b.connect("clicked", callback)
    return b


def clear(box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def mark(tip="Installed"):
    return label(CHECK, "installed-mark", xalign=0.5, tooltip_text=tip)


def blocked(widget, tip):
    """Grey `widget` out. The tooltip goes on a wrapper, because GTK passes the pointer
    straight through an insensitive widget and would never show its own tooltip."""
    widget.set_sensitive(False)
    wrap = Gtk.Box(tooltip_text=tip, valign=Gtk.Align.CENTER)
    wrap.append(widget)
    return wrap


def popup_at(menu, x, y):
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    menu.set_pointing_to(rect)
    menu.popup()


def open_url(url):
    Gtk.UriLauncher.new(url).launch(None, None, None, None)


# --------------------------------------------------------------------------- images

class Images:
    """Thumbnails on worker threads, memoised as textures (URL or local path).

    A priority queue, not first-come: a theme page's big image (-1) jumps ahead
    of installed themes (0), which jump ahead of the 146-card Browse list (1),
    so a first run with an empty cache still shows what you're looking at first.
    """

    def __init__(self, workers=4):
        self.queue = queue.PriorityQueue()
        self.memo, self.waiting = {}, {}
        self.lock = threading.Lock()
        self.seq = itertools.count()
        for _ in range(workers):
            threading.Thread(target=self._worker, daemon=True).start()

    def _load(self, src, width):
        if isinstance(src, str) and src.startswith("http"):
            src = data.cached_download(src, data.url_cache_name(src, "shots"), max_bytes=data.MAX_IMAGE)
        path = data.thumbnail(src, width) if width else Path(src)
        return Gdk.Texture.new_from_filename(str(path)) if path else None

    def peek(self, src, width):
        """The texture if it's already loaded, else None (no loading)."""
        with self.lock:
            return self.memo.get((str(src), width))

    def get(self, src, width, cb, priority=1):
        key = (str(src), width)
        with self.lock:
            tex = self.memo.get(key)
            if tex is None:
                first = key not in self.waiting
                self.waiting.setdefault(key, []).append(cb)
        if tex is not None:
            cb(tex)
        elif first:  # the same image asked for twice is fetched once
            self.queue.put((priority, next(self.seq), key, src, width))

    def _worker(self):
        while True:
            _prio, _n, key, src, width = self.queue.get()
            try:
                tex = self._load(src, width)
            except Exception:  # noqa: BLE001 — a missing screenshot just stays blank
                tex = None
            with self.lock:
                if tex is not None:
                    self.memo[key] = tex
                cbs = self.waiting.pop(key, [])
            GLib.idle_add(lambda cbs=cbs, tex=tex: ([cb(tex) for cb in cbs], False)[1])


IMAGES = Images()


class ExactSize(Gtk.Widget):
    """Holds one child at exactly w×h, whatever it asks for. (Clamps weren't enough: a picture wider
    than 16:9, like Aether's 3608×1954 previews, still asked for ~320 px and widened its card.)"""

    def __init__(self, child, w, h):
        super().__init__(halign=Gtk.Align.START, valign=Gtk.Align.START)
        self._child, self._w, self._h = child, w, h
        self.set_overflow(Gtk.Overflow.HIDDEN)
        child.set_parent(self)

    def resize(self, w, h):
        if (w, h) != (self._w, self._h):
            self._w, self._h = w, h
            self.queue_resize()

    def do_measure(self, orientation, _for_size):
        size = self._w if orientation == Gtk.Orientation.HORIZONTAL else self._h
        return size, size, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self._child.allocate(width, height, baseline, None)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None


def picture(w, h, src=None, width=480, priority=1, zoom=False):
    """A w×h image, never bigger or smaller: its texture's own shape can't widen the card (which
    would knock it out of line, or collapse the grid to a single column) or make it taller.
    zoom: a 🔍+ button in its top-right corner opens the big view (ImagePreview)."""
    pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
    pic.add_css_class("thumb")
    if src:
        IMAGES.get(src, width, lambda t: t and pic.set_paintable(t), priority)
    if not (zoom and src):
        return ExactSize(pic, w, h)
    over = Gtk.Overlay(child=pic)
    btn = Gtk.Button(icon_name="zoom-in-symbolic", tooltip_text="View larger", halign=Gtk.Align.END,
                     valign=Gtk.Align.START, margin_top=6, margin_end=6)
    btn.add_css_class("zoom-btn")
    btn.connect("clicked", lambda b: ImagePreview(b.get_root(), src, IMAGES.peek(src, width)).present())
    over.add_overlay(btn)
    return ExactSize(over, w, h)


PREVIEW_WIDTH = 1920   # the big view loads this wide a copy (sharp at ~75% of a 2560 px screen)
PREVIEW_SHARE = 0.75   # of the screen, so it's clearly a window over your desktop


class ImagePreview(Gtk.Window):
    """The big view of a theme's or background's picture: about 75% of the screen and shaped like the
    picture, no frame. The window itself covers the whole screen, see-through and lightly dimmed, so a
    click ANYWHERE closes it (as a picture-sized window, clicks beside it went to whatever was below,
    and it closed only sometimes). Esc, or switching to another window, closes it too."""

    def __init__(self, parent, src, small=None):
        super().__init__(transient_for=parent, modal=True, decorated=False, title="OmaSkins preview")
        self.add_css_class("image-preview")
        monitor = parent.get_display().get_monitor_at_surface(parent.get_surface())
        geo = monitor.get_geometry() if monitor else None
        screen = (geo.width, geo.height) if geo else (1680, 1050)
        self.set_default_size(*screen)
        self._max = (screen[0] * PREVIEW_SHARE, screen[1] * PREVIEW_SHARE)
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        self.holder = ExactSize(self.pic, *self._size(None))
        self.holder.set_halign(Gtk.Align.CENTER)
        self.holder.set_valign(Gtk.Align.CENTER)
        self.set_child(self.holder)
        self._show(small)
        IMAGES.get(src, PREVIEW_WIDTH, self._show, priority=-2)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, key, *_: (self.close(), True)[1] if key == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.close())
        self.add_controller(click)
        self.connect("notify::is-active", self._focus_changed)
        self.connect("close-request", lambda *_: setattr(self, "_was_active", False) or False)  # however it closes: once
        self._was_active = False

    def _focus_changed(self, _w, _p):
        """Switching to another window closes it, once it has had the focus (not before it gets it)."""
        if self.is_active():
            self._was_active = True
        elif self._was_active:
            self.close()

    def _size(self, texture):
        """As big as fits in 75% of the screen, in the picture's own shape (16:9 until known)."""
        aspect = texture.get_width() / texture.get_height() if texture and texture.get_height() else 16 / 9
        max_w, max_h = self._max
        w = int(min(max_w, max_h * aspect))
        return w, int(w / aspect)

    def _show(self, texture):
        """The small card picture first, the sharp copy when it's loaded."""
        if texture is not None:
            self.pic.set_paintable(texture)
            self.holder.resize(*self._size(texture))


# --------------------------------------------------------------------------- font metrics

_XRATIO, _MATCHED = {}, {}


def _x_ink(widget, family, px):
    """Height in px of a lowercase 'x' drawn in `family` at `px` (fractional; hinting included), or 0
    when that font isn't the one Pango actually loads (not installed / not loaded)."""
    layout = widget.create_pango_layout("x")
    desc = Pango.FontDescription()
    desc.set_family(family)
    desc.set_absolute_size(px * Pango.SCALE)
    layout.set_font_description(desc)
    font = layout.get_context().load_font(desc)
    if font is None or font.describe().get_family().casefold() != family.casefold():
        return 0.0
    ink, _log = layout.get_extents()
    return max(0.0, ink.height / Pango.SCALE)


def x_height_ratio(widget, family):
    """Height of a lowercase 'x' as a fraction of the font size, measured at 1000 px (sub-pixel)."""
    if family not in _XRATIO:
        _XRATIO[family] = _x_ink(widget, family, 1000) / 1000
    return _XRATIO[family]


def _load_matched():
    try:
        saved = json.loads(data.FONT_SIZES_CACHE.read_text())
        if saved.get("fonts") == data.font_signature():
            for k, v in saved.get("sizes", {}).items():
                family, base = k.rsplit("@", 1)
                _MATCHED[(family, float(base))] = v
    except (OSError, ValueError, AttributeError):
        pass


def _save_matched():
    try:
        data.FONT_SIZES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = data.FONT_SIZES_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fonts": data.font_signature(),
                                   "sizes": {f"{f}@{b}": v for (f, b), v in _MATCHED.items()}}))
        tmp.replace(data.FONT_SIZES_CACHE)
    except OSError:
        pass


def matched_size(widget, family, base_px):
    """The size at which `family`'s lowercase is as tall as Omarchy's default font (JetBrainsMono
    Nerd Font) at `base_px`: first by the measured ratio, then fine-tuned in 0.1 px steps on what is
    actually drawn (hinting snaps letters to whole pixels), so switching fonts changes the style,
    not the size."""
    key = (family, round(base_px, 2))
    if not _MATCHED:
        _load_matched()
    if key not in _MATCHED:
        ref = data.REFERENCE_FAMILY
        ratio = x_height_ratio(widget, family)
        ref_ratio = x_height_ratio(widget, ref) or data.REFERENCE_X / 1000
        if not ratio:
            _MATCHED[key] = float(base_px)
        else:
            first = data.normalized_size(base_px, ratio, ref_ratio)
            target = _x_ink(widget, ref, base_px) or base_px * ref_ratio
            _MATCHED[key] = _closest_size(lambda s: _x_ink(widget, family, s), target, first)
        GLib.idle_add(lambda: (_save_matched(), False)[1]) if len(_MATCHED) % 8 == 0 else None
    return _MATCHED[key]


def _closest_size(ink, target, first):
    """The size within ±1 px of `first` (0.1 px steps) whose drawn 'x' is closest to `target`, nearest to
    `first` on a tie: exactly what trying all 21 gives, in far fewer measurements. A letter's drawn height
    only grows with the size (hinting makes runs of sizes draw the same height), so halving searches find
    where it crosses `target` and where each run of equal heights starts and ends."""
    steps = [round(first + d / 10, 1) for d in range(-10, 11)]
    mid_i = 10   # `first`
    measured = {}

    def at(i):
        if i not in measured:
            measured[i] = ink(steps[i])
        return measured[i]

    def search(lo, hi, pred):
        """First index in [lo, hi] where pred holds (pred is False...False True...True); hi + 1 if none."""
        while lo <= hi:
            m = (lo + hi) // 2
            if pred(m):
                hi = m - 1
            else:
                lo = m + 1
        return lo

    n = len(steps)
    up = search(0, n - 1, lambda i: at(i) >= target)        # first step at least as tall as the target
    best = None
    for c in (up - 1, up):
        if not 0 <= c < n:
            continue
        v = at(c)
        # the run of steps drawing exactly v, and its step nearest `first`
        start = search(0, c, lambda i: at(i) >= v)
        end = search(c, n - 1, lambda i: at(i) > v) - 1
        pick = min(max(mid_i, start), end)
        key = (abs(v - target), abs(steps[pick] - first))
        if best is None or key < best[0]:
            best = (key, pick)
    return steps[best[1]]


FONT_SLOT = 1.75  # font previews sit in a box this many times the base size tall (tallest line: 1.58)


def font_slot(base_px):
    """A fixed-height box for one font preview. Fonts' own line spacing differs (27-38 px at the same
    letter size), and a note becomes a preview later: same height either way, so nothing moves."""
    box = Gtk.Box(height_request=int(base_px * FONT_SLOT + 0.5), valign=Gtk.Align.START)
    box.set_overflow(Gtk.Overflow.HIDDEN)
    return box


def font_label(widget, family, base_px):
    """`family` drawn in itself, sized so its lowercase matches Omarchy's default font."""
    size = matched_size(widget, family, base_px)
    lab = label(family, ellipsize=Pango.EllipsizeMode.END)
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_family_new(family))
    attrs.insert(Pango.attr_size_new_absolute(int(size * Pango.SCALE)))
    lab.set_attributes(attrs)
    return lab, size / base_px


# --------------------------------------------------------------------------- entries

class ThemeEntry:
    """One theme card: a community listing, a local folder, or both."""

    def __init__(self, community=None, local=None, removed="", hidden=None):
        self.community, self.local = community, local
        self.removed = removed    # folder name of a built-in hidden or removed through OmaSkins
        self.hidden = hidden      # its LocalTheme when only hidden from OmaSkins (still installed)

    @property
    def title(self):
        if self.removed:
            return data.display_name(self.removed)
        return self.local.title if self.local else self.community.name

    @property
    def installed(self):
        return self.local is not None

    @property
    def image(self):
        if self.hidden and self.hidden.preview:
            return self.hidden.preview
        if self.local and self.local.preview:
            return self.local.preview
        return self.community.screenshot_url if self.community else None

    @property
    def repo_url(self):
        if self.removed:
            return ""
        if self.community:
            return self.community.repo_url
        return self.local.repo_url if self.local else ""


# --------------------------------------------------------------------------- widgets

def sub_tabs(names, on_change):
    """Browse / Installed style switch. Returns (bar, {name: count_label}, {name: button})."""
    bar = Gtk.Box()
    bar.add_css_class("subtabbar")
    counts, buttons, first = {}, {}, None
    for name in names:
        btn = Gtk.ToggleButton()
        btn.add_css_class("subtab")
        box = Gtk.Box()
        box.append(Gtk.Label(label=name))
        count = label("", "count")
        box.append(count)
        btn.set_child(box)
        if first:
            btn.set_group(first)
        else:
            first = btn
            btn.set_active(True)
        btn.connect("toggled", lambda b, n=name: b.get_active() and on_change(n))
        bar.append(btn)
        counts[name], buttons[name] = count, btn
    return bar, counts, buttons


def card_tile(child):
    """The card's own box (.tile): what lights up on hover."""
    w = child.get_first_child() if child else None
    while w is not None and not w.has_css_class("tile"):
        w = w.get_first_child()
    return w


def click_on_card(fb, x, y):
    """True when (x, y) in the grid is inside a card's highlighted box, edge to edge. GTK gives each
    card its whole column (up to ~24 px wider than a theme card's box), so a click there would still
    open it without this check."""
    child = fb.get_child_at_pos(int(x), int(y))
    tile = card_tile(child)
    if tile is None:
        return child is not None and child.get_activatable() is False
    ok, b = tile.compute_bounds(fb)
    return ok and b.get_x() <= x < b.get_x() + b.get_width() and b.get_y() <= y < b.get_y() + b.get_height()


def zoom_button_at(fb, x, y):
    """The 🔍+ button under (x, y) in the grid, if that's where the click is."""
    w = fb.pick(x, y, Gtk.PickFlags.DEFAULT)
    while w is not None and w is not fb:
        if isinstance(w, Gtk.Button) and w.has_css_class("zoom-btn"):
            return w
        w = w.get_parent()
    return None


def flow():
    fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, valign=Gtk.Align.START,
                     column_spacing=6, row_spacing=6, max_children_per_line=12, min_children_per_line=1,
                     margin_start=12, margin_end=12, margin_top=6, margin_bottom=12)
    fb.set_activate_on_single_click(True)
    # Clicks outside a card's highlighted box open nothing: taken here, before the grid acts on them.
    guard = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    def pressed(g, _n, x, y):
        zoom = zoom_button_at(fb, x, y)
        if zoom:  # the 🔍+ on a card: the big view only, never also open the card
            g.set_state(Gtk.EventSequenceState.CLAIMED)
            zoom.emit("clicked")
        elif not click_on_card(fb, x, y):
            g.set_state(Gtk.EventSequenceState.CLAIMED)
    guard.connect("pressed", pressed)
    fb.add_controller(guard)
    return fb


STRIP_GAP = 8   # empty space between the controls and the scroll bar below them (owner: no misclicks)


def hstrip(child):
    """Rows of controls above a picture pane that scroll sideways together when the window is too
    narrow for them (owner, 2026-10-01: OmaSkins tiled beside another window).
    - A trackpad's left/right moves them; its up/down is left alone. A mouse wheel's up/down moves
      them sideways (a wheel has no left/right). Middle-click-and-drag moves them too.
    - It takes every scroll before the controls inside see it, so the Global Rounded Corners number
      never changes by itself while you scroll across it; its - / + and typing still work.
    - The scroll bar exists only when the rows don't fit: GTK's thin overlay one that fades away
      until the pointer is over it, with STRIP_GAP of empty space above it."""
    child.set_margin_bottom(STRIP_GAP)
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.NEVER,
                            overlay_scrolling=True, propagate_natural_width=True, propagate_natural_height=True,
                            hexpand=True)
    sw.set_child(child)

    def fits():
        adj = sw.get_hadjustment()
        return adj.get_upper() - adj.get_page_size() <= 0

    def move(by):
        if not fits():
            adj = sw.get_hadjustment()
            adj.set_value(adj.get_value() + by)

    wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES,
                                      propagation_phase=Gtk.PropagationPhase.CAPTURE)

    def on_scroll(c, dx, dy):
        if c.get_unit() == Gdk.ScrollUnit.WHEEL:
            move((dy or dx) * 40)        # a wheel's up/down (or a tilt wheel's left/right): sideways
        else:
            move(dx)                     # a trackpad: its left/right only; up/down does nothing here
        return True                      # never reaches a number box underneath
    wheel.connect("scroll", on_scroll)
    sw.add_controller(wheel)
    drag, start = Gtk.GestureDrag(button=Gdk.BUTTON_MIDDLE), [0.0]
    drag.connect("drag-begin", lambda *_: start.__setitem__(0, sw.get_hadjustment().get_value()))
    drag.connect("drag-update", lambda _g, dx, _dy: fits() or sw.get_hadjustment().set_value(start[0] - dx))
    sw.add_controller(drag)
    return sw


def scrolled(child):
    sw = Gtk.ScrolledWindow(vexpand=True, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                            overlay_scrolling=True)
    sw.set_child(child)
    return sw


class ThemeCard(Gtk.FlowBoxChild):
    def __init__(self, win, entry, current):
        super().__init__()
        self.entry = entry
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER, width_request=CARD_W)
        box.add_css_class("tile")
        is_current = entry.local is not None and entry.local.name == current
        if is_current:
            box.add_css_class("current")
        box.append(picture(CARD_W, CARD_H, entry.image, priority=0 if entry.installed else 1, zoom=True))
        row = Gtk.Box(spacing=6)
        row.add_css_class("card-row")  # one height for every card's title row (Apply button or not)
        # The title fills what's left and ends in "…" when needed, but never asks for more: a card is
        # always CARD_W wide, so its picture lines up with the cards above and below it.
        row.append(label(entry.title, "card-name", hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                         max_width_chars=1))
        if entry.removed:
            row.append(badge("Built-in", "builtin"))
            row.append(badge("Hidden" if entry.hidden else "Removed", "warn"))
        elif entry.local and entry.local.builtin:
            row.append(badge("Built-in", "builtin"))
        elif entry.installed and entry.community is None:
            # Not on omarchy.org: "Aether" (made with it) OR "Unlisted", never both; once a theme is
            # listed, neither shows (owner, 2026-09-30). Short, so a card never grows wider.
            row.append(badge("Aether", "builtin") if entry.local.aether else badge("Unlisted", "warn"))
        if is_current:
            row.append(badge("Current", "verified"))
        if entry.installed and not is_current:
            # One-click Apply right on the card (Browse and Installed), as quick as Omarchy's own picker.
            apply = next((a for a in data.theme_actions(entry.local, entry.community, current) if a.label == "Apply"), None)
            if apply:
                b = win.action_button(apply)
                b.add_css_class("card-apply")  # on the wrapper too when greyed out (rotation on); CSS covers both
                row.append(b)
        if entry.installed:
            row.append(mark())
        box.append(row)
        self.set_child(box)
        self.set_tooltip_text(entry.title)

        # Right-click: Add / Remove / Restore straight from the grid, for working through a long list.
        a = win.quick_theme_action(entry)
        if a:
            group = Gio.SimpleActionGroup()
            act = Gio.SimpleAction.new("do", None)
            act.set_enabled(not a.blocked)
            act.connect("activate", lambda *_: win.quick_theme(entry, a))
            group.add_action(act)
            self.insert_action_group("theme", group)
            menu = Gio.Menu()
            menu.append(f"{a.label} (it's your current theme)" if a.blocked else a.label, "theme.do")
            self.menu = Gtk.PopoverMenu(menu_model=menu, has_arrow=False, halign=Gtk.Align.START)
            self.menu.set_parent(self)
            click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            click.connect("pressed", lambda _g, _n, x, y: popup_at(self.menu, x, y))
            self.add_controller(click)


class BackgroundCard(Gtk.FlowBoxChild):
    def __init__(self, win, bgd):
        super().__init__()
        self.bgd = bgd
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER, width_request=CARD_W)
        box.add_css_class("tile")
        if bgd.current:
            box.add_css_class("current")
        box.append(picture(CARD_W, CARD_H, bgd.path, priority=0, zoom=True))
        row = Gtk.Box(spacing=6)
        row.append(label(bgd.path.name, "small", hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE))
        if bgd.yours:
            row.append(badge("Yours", "enabled"))
        if bgd.current:
            row.append(mark("Current background"))
        box.append(row)
        acts = Gtk.Box(spacing=6)
        for a in data.background_actions(bgd, bgd.theme):
            acts.append(win.action_button(a))
        box.append(acts)
        self.set_child(box)

        # Right-click menu: "Save to Pictures", plus "Copy to current theme's backgrounds" on other themes.
        group = Gio.SimpleActionGroup()
        save = Gio.SimpleAction.new("save", None)
        save.connect("activate", lambda *_: win.save_to_pictures(bgd))
        group.add_action(save)
        menu = Gio.Menu()
        menu.append("Save to Pictures", "bg.save")
        copy = data.copy_to_theme_action(bgd, win.current_theme)
        if copy:
            act = Gio.SimpleAction.new("copy", None)
            act.connect("activate", lambda *_: win.do_action(copy))
            group.add_action(act)
            menu.append(copy.label, "bg.copy")
        self.insert_action_group("bg", group)
        self.menu = Gtk.PopoverMenu(menu_model=menu, has_arrow=False, halign=Gtk.Align.START)
        self.menu.set_parent(self)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_right_click)
        self.add_controller(click)

    def _on_right_click(self, _gesture, _n, x, y):
        popup_at(self.menu, x, y)


class FontRow(Gtk.ListBoxRow):
    """Installed font: its name in its own face, x-height-matched to Omarchy's default font."""

    def __init__(self, win, font, current_package, grouped=False):
        super().__init__(activatable=False)
        self.key = f"{font.family} {font.package}".lower()
        box = Gtk.Box(spacing=12)
        box.add_css_class("font-row")
        if grouped:
            box.add_css_class("font-in-group")  # indented under its package's header
        box.append(mark("Current font") if font.current else Gtk.Box(width_request=24))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        name, scale = font_label(win, font.family, win.font_base * 1.6)
        name.set_valign(Gtk.Align.CENTER)
        slot = font_slot(win.font_base * 1.6)
        slot.append(name)
        col.append(slot)
        bits = [font.package or "not from a package"]
        if abs(scale - 1) >= 0.02:
            bits.append(f"shown at {scale * 100:.0f}% so its letters match {data.REFERENCE_FAMILY}")
        col.append(label(" · ".join(bits), "dim small", wrap=True))
        box.append(col)
        if font.current:
            box.append(badge("Current", "verified"))
        for a in data.font_actions(font=font, current_package=current_package):
            if grouped and a.label == "Remove":
                continue  # removing is per package: the group header has the one Remove
            box.append(win.action_button(a))
        self.set_child(box)


class FontGroupRow(Gtk.ListBoxRow):
    """Header of an installed font package: the fonts it installed follow, indented. One Remove for
    the whole group, because removing the package removes every font in it (owner's design)."""

    def __init__(self, win, package, fonts, pkg=None, current_package=""):
        super().__init__(activatable=False)
        self.key = " ".join([package or "", *(f.family for f in fonts), pkg.description if pkg else ""]).lower()
        box = Gtk.Box(spacing=12)
        box.add_css_class("font-row")
        box.add_css_class("font-group")
        box.append(mark("Installed") if package else Gtk.Box(width_request=24))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        title = Gtk.Box(spacing=8)
        title.append(label(package or "Not from a package", "plugin-name", ellipsize=Pango.EllipsizeMode.END,
                           width_chars=10))
        if pkg and pkg.omarchy_pick:
            title.append(badge("Omarchy pick", "verified"))
        if pkg:
            title.append(badge("Installed", "enabled"))
        elif package:
            title.append(badge("System", "builtin"))
        col.append(title)
        n = len(fonts)
        about = f"{n} font{'s' if n != 1 else ''}" + (f" · {pkg.description} · {pkg.version}" if pkg else
                                                     " · comes with the system, so it stays installed")
        col.append(label(about, "dim small", wrap=True))
        box.append(col)
        if pkg:
            for a in data.font_actions(package=pkg, current_package=current_package):
                box.append(win.action_button(a))
        self.set_child(box)


class FontPackageRow(Gtk.ListBoxRow):
    """A Nerd Font package from the Arch repos."""

    def __init__(self, win, pkg, families, current_package):
        super().__init__(activatable=False)
        self.key = (pkg.package + " " + pkg.description).lower()
        box = Gtk.Box(spacing=12)
        box.add_css_class("font-row")
        box.append(mark() if pkg.installed else Gtk.Box(width_request=24))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        title = Gtk.Box(spacing=8)
        title.append(label(pkg.package, "plugin-name", ellipsize=Pango.EllipsizeMode.END, width_chars=10))
        if pkg.omarchy_pick:
            title.append(badge("Omarchy pick", "verified"))
        if pkg.installed:
            title.append(badge("Installed", "enabled"))
        col.append(title)
        # The preview: an installed font from the system; any other from its downloaded file
        # (data.fetch_preview_font), filled in when it arrives.
        self.win, self.preview = win, font_slot(win.font_base * 1.6)
        col.append(self.preview)
        cached = None if families else data.preview_font(pkg.package)
        family = families[0] if families else cached and cached["family"]
        if family:
            self.show_preview(family)
        else:
            self.show_note("Downloading a preview…")
        col.append(label(f"{pkg.description} · {pkg.version}", "dim small", wrap=True))
        box.append(col)
        for a in data.font_actions(package=pkg, current_package=current_package):
            box.append(win.action_button(a))
        self.set_child(box)

    def show_preview(self, family):
        for c in list(self.preview):
            self.preview.remove(c)
        name, _scale = font_label(self.win, family, self.win.font_base * 1.6)
        name.set_valign(Gtk.Align.CENTER)
        self.preview.append(name)

    def show_note(self, text):
        for c in list(self.preview):
            self.preview.remove(c)
        self.preview.append(label(text, "dim small", valign=Gtk.Align.CENTER))


def load_preview_font(path):
    """Make a downloaded font file usable in this app only (not installed for the system)."""
    try:
        return PangoCairo.FontMap.get_default().add_font_file(str(path))
    except (GLib.Error, TypeError):
        return False


# --------------------------------------------------------------------------- flip book

class FlipBook(Gtk.Box):
    """A theme's cover and backgrounds as a deck: the current card in the middle, its neighbours
    dimmed at the sides. Arrows, the side cards, or the Left/Right keys flip; it wraps around.

    A card only joins the deck once its full-size picture has loaded, so nothing ever shows or
    offers an empty box: the side cards fade in when there is a loaded neighbour to flip to."""

    MAIN_W, MAIN_H, SIDE_W, SIDE_H, FULL = 640, 360, 170, 96, 1280

    def __init__(self, entry, start=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.START)
        self.items, self.ready, self.failed = [], set(), set()   # items: (src, name); sets of indexes
        # A rebuilt page keeps its picture, matched by name: after Add, the GitHub links become local files.
        self.start = start
        self.at, self.slot, self.note = None, "a", ""
        row = Gtk.Box(spacing=10)
        self.prev_btn = self._arrow("go-previous-symbolic", -1, "Previous (←)")
        row.append(self.prev_btn)
        self.left = self._side(-1)
        row.append(self.left)
        self.stack = Gtk.Stack(transition_duration=260, hhomogeneous=True, vhomogeneous=True)
        self.stack.set_size_request(self.MAIN_W, self.MAIN_H)
        self.stack.add_named(label("Loading pictures…", "dim", xalign=0.5), "a")
        self.stack.add_named(Gtk.Box(), "b")
        row.append(self.stack)
        self.right = self._side(1)
        row.append(self.right)
        self.next_btn = self._arrow("go-next-symbolic", 1, "Next (→)")
        row.append(self.next_btn)
        self.append(row)
        # Caption: always one line, so nothing below moves as names change.
        cap = Gtk.Box(spacing=8, margin_start=self.SIDE_W + 58, width_request=self.MAIN_W, halign=Gtk.Align.START)
        self.name = label("", "dim small", hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        cap.append(self.name)
        self.count = label("", "dim small")
        cap.append(self.count)
        self.append(cap)

        if entry.image:
            self._add([(entry.image, "Cover")])
        if entry.local:
            self._add([(b.path, b.path.name) for b in data.backgrounds_for(entry.local)])
        elif entry.community:
            self.note = "looking for backgrounds on GitHub…"
            bg(lambda: data.remote_backgrounds(entry.community), self._found_remote)
        self._refresh()

    def _arrow(self, icon, step, tip):
        b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
        b.add_css_class("icon-btn")
        b.connect("clicked", lambda *_: self.flip(step))
        return b

    def _side(self, step):
        box = Gtk.Box(width_request=self.SIDE_W, height_request=self.SIDE_H, valign=Gtk.Align.CENTER)
        box.add_css_class("flip-side")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.flip(step))
        box.add_controller(click)
        return box

    def _found_remote(self, urls):
        found = [] if isinstance(urls, Exception) else (urls or [])
        self.note = "" if found else "no backgrounds found"
        self._add([(u, unquote(u.rsplit("/", 1)[-1])) for u in found])
        self._refresh()

    def _add(self, found):
        # An installed theme with no preview uses its first background as the cover: don't show it twice.
        seen = {str(src) for src, _n in self.items}
        for src, name in found:
            if str(src) in seen:
                continue
            seen.add(str(src))
            i = len(self.items)
            self.items.append((src, name))
            # In deck order (the queue is first-come within a priority), each at full size.
            IMAGES.get(src, self.FULL, lambda tex, i=i: self._loaded(i, tex), priority=-1)

    def _loaded(self, i, tex):
        (self.ready if tex else self.failed).add(i)
        if tex and self.start and self.items[i][1] == self.start:
            self.start = None
            self._show(i)
        elif self.at is None and tex:
            self._show(i)
        else:
            self._refresh()

    def current_name(self):
        return self.items[self.at][1] if self.at is not None else None

    def _order(self):
        return sorted(self.ready)

    def flip(self, step):
        order = self._order()
        if len(order) > 1 and self.at in order:
            self._show(order[(order.index(self.at) + step) % len(order)], step)

    def _show(self, i, step=0):
        self.at = i
        self.slot = "b" if self.slot == "a" else "a"
        self.stack.remove(self.stack.get_child_by_name(self.slot))
        self.stack.add_named(picture(self.MAIN_W, self.MAIN_H, self.items[i][0], width=self.FULL, zoom=True),
                             self.slot)
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE if not step else
                                       Gtk.StackTransitionType.SLIDE_LEFT if step > 0 else
                                       Gtk.StackTransitionType.SLIDE_RIGHT)
        self.stack.set_visible_child_name(self.slot)
        self._refresh()

    def _refresh(self):
        order = self._order()
        pending = len(self.items) - len(self.ready) - len(self.failed)
        if self.at is None:
            self.name.set_text(self.note or ("" if pending else "No pictures for this theme"))
            self.count.set_text("")
        else:
            name = self.items[self.at][1]
            self.name.set_text(f"{name} · {self.note}" if self.note else name)
            more = f" · {pending} more loading" if pending else ""
            self.count.set_text(f"{order.index(self.at) + 1} / {len(order)}{more}")
        many = self.at is not None and len(order) > 1
        for side, d in ((self.left, -1), (self.right, 1)):
            want = order[(order.index(self.at) + d) % len(order)] if many else None
            if getattr(side, "shows", None) != want:
                side.shows = want
                clear(side)
                if want is not None:  # already loaded, so the picture is there at once
                    side.append(picture(self.SIDE_W, self.SIDE_H, self.items[want][0], width=self.FULL))
            # Invisible and unclickable until there's a loaded card to flip to; its space is kept.
            (side.remove_css_class if many else side.add_css_class)("empty")
            side.set_can_target(many)
        self.prev_btn.set_sensitive(many)
        self.next_btn.set_sensitive(many)


# --------------------------------------------------------------------------- theme page

class ThemePage(Gtk.Box):
    def __init__(self, win, entry, start=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.entry = win, entry
        head = Gtk.Box(spacing=8, margin_start=18, margin_end=18, margin_top=12, margin_bottom=8)
        back = button("‹ Back", "flat", lambda *_: win.go_back(), tooltip="Back (Esc)")
        head.append(back)
        head.append(label(entry.title, "plugin-name", hexpand=True))
        self.append(head)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=18, margin_end=18,
                       margin_bottom=18)
        self.flipbook = FlipBook(entry, start)
        body.append(self.flipbook)

        # actions
        acts = Gtk.Box(spacing=8)
        acts.add_css_class("page-actions")  # Apply / Remove / Backgrounds / GitHub: same height as a card's Apply
        if entry.removed:
            theme_acts = [data.builtin_restore_action(entry.removed, win.builtin_pkg, bool(entry.hidden))]
        else:
            theme_acts = data.theme_actions(entry.local, entry.community, win.current_theme)
        for a in theme_acts:
            done = None
            if a.label in ("Remove", "Hide") and win.rotation.plan.in_rotation(entry.local.name):
                a.note = (a.note + " " if a.note else "") + "It will also be taken out of the rotation."
                done = lambda name=entry.local.name: win.rotation.drop(name)
            acts.append(win.action_button(a, done))
        if entry.local:
            bgs = button("Backgrounds", "", lambda *_: win.show_backgrounds_for(entry.local.name))
            acts.append(blocked(bgs, BGS_BLOCKED) if win.rotation.plan.backgrounds else bgs)
        if entry.repo_url:
            acts.append(button("Open on GitHub", "flat", lambda *_: open_url(entry.repo_url)))
        body.append(acts)

        # palette
        body.append(label("Colors", "section-title"))
        self.swatches = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                                    min_children_per_line=4, column_spacing=6, row_spacing=6)
        body.append(self.swatches)
        if entry.hidden and entry.hidden.colors:
            self._fill_swatches(entry.hidden.colors)
        elif entry.removed:
            self.swatches.append(label("Removed from Omarchy. Its colors come back when it's restored.", "dim"))
        elif entry.local and entry.local.colors:
            self._fill_swatches(entry.local.colors)
        elif entry.community:
            self.swatches.append(label("Loading colors from GitHub…", "dim"))
            bg(lambda: data.remote_colors(entry.community), self._fill_swatches)
        else:
            self.swatches.append(label("This theme has no colors.toml.", "dim"))

        # details
        body.append(label("Details", "section-title"))
        grid = Gtk.Grid(column_spacing=18, row_spacing=6)
        rows = [("Source", self._source())]
        if entry.repo_url:
            rows.append(("Repository", entry.repo_url))
        if entry.removed:
            rows.append(("Restore", "instant, no password: it's still installed" if entry.hidden
                         else "moves the kept copy back, or reinstalls it if Omarchy updated it meanwhile"))
        if entry.local:
            rows.append(("Folder", str(entry.local.path)))
            n = len(data.backgrounds_for(entry.local))
            rows.append(("Backgrounds", str(n)))
            if entry.local.colors.get("mode"):
                rows.append(("Mode", entry.local.colors["mode"]))
        elif entry.community:
            rows.append(("Would install to", str(data.USER_THEMES / entry.community.install_name)))
        for i, (k, v) in enumerate(rows):
            grid.attach(label(k, "kv-key"), 0, i, 1, 1)
            grid.attach(label(v, selectable=True, wrap=True, hexpand=True), 1, i, 1, 1)
        body.append(grid)
        self.append(scrolled(body))

    def _source(self):
        e = self.entry
        if e.removed:
            return ("Built into Omarchy, hidden from OmaSkins (still installed, still in Omarchy's menu)" if e.hidden
                    else "Built into Omarchy, removed from the system")
        if e.local and e.local.builtin:
            return "Built into Omarchy"
        if e.community and e.local:
            return "Community theme from omarchy.org, installed"
        if e.community:
            return "Community theme from omarchy.org"
        return "Added by you from a repository that isn't on omarchy.org"

    def _fill_swatches(self, colors):
        clear(self.swatches)
        if isinstance(colors, Exception) or not colors:
            self.swatches.append(label("Couldn't read this theme's colors.", "dim"))
            return
        for key in data.SWATCH_KEYS:
            value = theme.resolve_color(colors.get(key), {}, None)
            if not value:
                continue
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, tooltip_text=f"{key} {value}")
            sw = Gtk.Box()
            sw.add_css_class("swatch")
            prov = Gtk.CssProvider()
            prov.load_from_string(f"box {{ background: {value}; }}")
            sw.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
            cell.append(sw)
            cell.append(label(key, "swatch-name", xalign=0.5))
            self.swatches.append(cell)


# --------------------------------------------------------------------------- share / import

def _size_text(n):
    return f"{n / 1024 ** 2:.1f} MB" if n >= 1024 ** 2 else f"{max(1, n // 1024)} KB"


class ExportDialog(Adw.Dialog):
    """Share… (top right): your whole setup in one zip, with a manifest (omaskins.json) that tells the
    other OmaSkins what's inside before anything is unpacked."""

    def __init__(self, win):
        super().__init__(title="Share your setup", content_width=560)
        bg(share.register_file_type)
        self.win, self.collected = win, None
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=18, margin_end=18,
                           margin_top=12, margin_bottom=18)
        self.box.append(label("Everything OmaSkins looks after, in one file:", "section-title"))
        self.summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.summary.append(label("Looking…", "dim"))
        self.box.append(self.summary)
        self.box.append(label("Themes from omarchy.org's list and fonts from the Arch repos go in as links, so the "
                              "file stays small; everything else is copied in, so it works anywhere. Importing it "
                              "merges with what's already there and never overwrites anything.", "dim small",
                              wrap=True))
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.save = button("Export all settings…", "primary", lambda *_: self._choose())
        self.save.set_sensitive(False)
        btns.append(self.save)
        self.box.append(btns)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(self.box)
        self.set_child(view)
        bg(lambda: share.collect(win.community), self._show)

    def _show(self, result):
        clear(self.summary)
        if isinstance(result, Exception):
            self.summary.append(label(f"Couldn't look through your setup: {result}", "dim", wrap=True))
            return
        self.collected = result
        m, files = result
        themes = m["themes"]
        kinds = lambda *k: sum(t["kind"] in k for t in themes)  # noqa: E731
        fonts = m["fonts"]
        rot = m["rotation"]
        lines = [
            ("Themes", f"{len(themes)}: {kinds('builtin')} built in, {kinds('link')} as links, "
                       f"{kinds('folder', 'aether', 'edited')} copied in"),
            ("Your backgrounds", str(len(m["backgrounds"]))),
            ("Fonts", f"{len(fonts)}: {sum(f['source'] == 'repo' for f in fonts)} from the Arch repos, "
                      f"{sum(f['source'] != 'repo' for f in fonts)} copied in"),
            ("Rotation", f"set “{rot['set']}” in use" if rot.get("set") else "the settings in use (no saved set)"),
            ("Rotation sets", str(len(m["rotation_sets"]))),
            ("Look", f"{data.display_name(m['current'].get('theme') or '')}, {m['current'].get('font') or 'font'}, "
                     f"corners {m['corners']['px'] if m['corners']['on'] else 'off'}"
                     + (f", text size {m['text_size']}" if m.get("text_size") else "")),
        ]
        if m["hidden_builtins"]:
            lines.append(("Hidden built-ins", str(len(m["hidden_builtins"]))))
        if m["aether_blueprints"]:
            lines.append(("Aether blueprints", str(len(m["aether_blueprints"]))))
        for name, text in lines:
            row = Gtk.Box(spacing=12)
            row.append(label(name, "card-name", width_chars=16))
            row.append(label(text, "dim", wrap=True, hexpand=True))
            self.summary.append(row)
        size = sum(p.stat().st_size for p, _ in files if p.exists())
        self.summary.append(label(f"About {_size_text(size)}.", "dim small"))
        self.save.set_sensitive(True)

    def _choose(self):
        d = Gtk.FileDialog(title="Export all settings",
                           initial_name=f"omaskins-setup-{time.strftime('%Y-%m-%d')}{share.EXT}")
        d.save(self.win, None, self._chosen)

    def _chosen(self, d, res):
        try:
            f = d.save_finish(res)
        except GLib.Error:
            return  # cancelled
        dest = Path(f.get_path())
        if dest.suffix.lower() != share.EXT:
            dest = dest.with_name(dest.name + share.EXT)
        self.close()
        win = self.win
        toast, progress = win.progress_toast("Saving your setup…")
        def done(result):
            toast.dismiss()
            if isinstance(result, Exception):
                win.toasts.add_toast(Adw.Toast(title=f"Couldn't export: {result}", timeout=8))
            else:
                win.toasts.add_toast(Adw.Toast(title=f"Saved {dest.name} ({_size_text(result[1])}).", timeout=5))
        bg(lambda: share.write_zip(dest, win.community, progress, self.collected), done)


class ImportDialog(Adw.Dialog):
    """What's in a shared zip, as tick boxes (all ticked: Everything). Something another item needs sits
    underneath it, ticked and locked while that item is ticked, so it can't be left behind."""

    GROUPS = ("Themes", "Built-in themes", "Your backgrounds", "Fonts", "Rotation sets", "Settings")
    BADGES = {"merge": ("Merge", "verified"), "password": ("Password", "warn"), "have": ("Already here", "builtin")}

    def __init__(self, win, path, manifest):
        super().__init__(title="Import a shared setup", content_width=600, content_height=640)
        self.win, self.path, self.manifest = win, path, manifest
        self.rows = share.items(manifest)
        self.by_id = {r["id"]: r for r in self.rows}
        self.needs = {r["id"]: r["needs"] for r in self.rows}
        # Rows that only remove things (to match the file exactly) start unticked and aren't part of
        # Everything: a merge only adds (owner, 2026-10-02).
        self.chosen = {r["id"] for r in self.rows if r["state"] != "have" and not r.get("opt_in")}
        # A built-in the file removed: the Hide button's own question, as two choices under it.
        self.pick = {r["id"]: r["choice"][0][0] for r in self.rows if r.get("choice")}
        self.radios = {}          # row id -> [radio CheckButton, …]
        self.details = {}         # row id -> its description, when it follows the answer picked
        self.checks = {}          # id -> [CheckButton, …] (a theme can show under its set too)
        self.group_checks = {}
        self._syncing = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=18, margin_end=18,
                      margin_top=12, margin_bottom=18)
        made = manifest.get("created", "")[:10]
        box.append(label(f"{Path(path).name}" + (f" · made {made}" if made else ""), "dim small"))
        tree = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.everything = Gtk.CheckButton(label="Everything")
        self.everything.add_css_class("card-name")
        self.everything.connect("toggled", lambda b: self._set_all(b.get_active(), self.rows))
        tree.append(self.everything)
        for group in self.GROUPS:
            rows = [r for r in self.rows if r["group"] == group]
            if not rows:
                continue
            g = Gtk.CheckButton(label=f"{group} ({len(rows)})", margin_start=22, margin_top=6)
            g.add_css_class("card-name")
            g.connect("toggled", lambda b, rows=rows: self._set_all(b.get_active(), rows))
            self.group_checks[group] = (g, rows)
            tree.append(g)
            for r in rows:
                tree.append(self._row(r, 44))
                for need in r["needs"]:
                    if need in self.by_id:
                        tree.append(self._row(self.by_id[need], 66))
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(tree)
        box.append(scroll)
        box.append(label("Nothing you have is overwritten: a theme with the name of one of yours adds the "
                         "pictures it doesn't have yet to yours, a Rotation set with the name of one of yours is "
                         "combined with it, and anything already here is skipped.", "dim small", wrap=True))
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.go = button("Import…", "primary", lambda *_: self._ask())
        btns.append(self.go)
        box.append(btns)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        self.set_child(view)
        self._sync()

    def _row(self, r, indent):
        line = Gtk.Box(spacing=8, margin_start=indent)
        c = Gtk.CheckButton(label=r["label"])
        c.connect("toggled", lambda b, i=r["id"]: self._toggle(i, b.get_active()))
        self.checks.setdefault(r["id"], []).append(c)
        line.append(c)
        if r["detail"]:
            detail = label(r["detail"], "dim small", ellipsize=Pango.EllipsizeMode.END, hexpand=True,
                           tooltip_text=r["detail"])
            line.append(detail)
            if r.get("detail_for"):   # says what will happen for the answer picked under it
                self.details[r["id"]] = detail
                detail.set_text(r["detail_for"][self.pick[r["id"]]])
        else:
            line.append(Gtk.Box(hexpand=True))
        if r["state"] in self.BADGES:
            text, kind = self.BADGES[r["state"]]
            line.append(badge(text, kind))
        if not r.get("choice"):
            return line
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(line)
        first = None
        for sub, text, detail, state, why in r["choice"]:
            opt = Gtk.Box(spacing=8, margin_start=indent + 26, tooltip_text=why)
            radio = Gtk.CheckButton(label=text, active=self.pick[r["id"]] == sub)
            if first:
                radio.set_group(first)
            else:
                first = radio
            radio.connect("toggled", lambda b, i=r["id"], s=sub: b.get_active() and self._choose(i, s))
            self.radios.setdefault(r["id"], []).append(radio)
            opt.append(radio)
            opt.append(label(detail, "dim small", ellipsize=Pango.EllipsizeMode.END, hexpand=True))
            if state in self.BADGES:
                opt.append(badge(*self.BADGES[state]))
            box.append(opt)
        return box

    def _choose(self, i, sub):
        if self._syncing:
            return
        self.pick[i] = sub
        self.chosen.add(i)
        r = self.by_id[i]
        if i in self.details and sub in r.get("detail_for", {}):
            self.details[i].set_text(r["detail_for"][sub])
            self.details[i].set_tooltip_text(r["detail_for"][sub])
        self._sync()

    def _final(self):
        """The ids to import: what's ticked (and what it needs), a built-in's chosen answer in its place."""
        ids = (self.chosen | self._required()) - {r["id"] for r in self.rows if r["state"] == "have"}
        return {self.pick.get(i, i) for i in ids}

    def _password_items(self):
        """What the one password will cover: font packages, built-ins removed or restored."""
        out = []
        for i in self._final():
            r = self.by_id.get(i)
            if r and r["state"] == "password":
                out.append(r["label"])
            elif i.startswith("remove:"):
                out.append(f"Remove {data.display_name(i.split(':', 1)[1])}")
        return sorted(set(out))

    def _required(self):
        """Ids some ticked item needs: ticked and locked."""
        return share.with_needs({n for c in self.chosen for n in self.needs.get(c, [])}, self.rows)

    def _toggle(self, i, on):
        if self._syncing:
            return
        if on:
            self.chosen.add(i)
        else:
            self.chosen.discard(i)
        self._sync()

    def _set_all(self, on, rows):
        if self._syncing:
            return
        for r in rows:
            if r["state"] != "have" and not r.get("opt_in"):
                (self.chosen.add if on else self.chosen.discard)(r["id"])
        self._sync()

    def _sync(self):
        self._syncing = True
        required = self._required()
        picked = self.chosen | required
        for i, checks in self.checks.items():
            have = self.by_id[i]["state"] == "have"
            for c in checks:
                c.set_active(i in picked and not have)
                c.set_sensitive(not have and i not in required)
                c.set_tooltip_text("Already here" if have else
                                   "Needed by something you're bringing in" if i in required else None)

        for i, radios in self.radios.items():
            for radio in radios:
                radio.set_sensitive(i in picked)

        def mark(check, rows):
            open_rows = [r for r in rows if r["state"] != "have" and not r.get("opt_in")]
            n = sum(r["id"] in picked for r in open_rows)
            check.set_active(bool(open_rows) and n == len(open_rows))
            check.set_inconsistent(0 < n < len(open_rows))
            check.set_sensitive(bool(open_rows))
        for g, rows in self.group_checks.values():
            mark(g, rows)
        mark(self.everything, self.rows)
        self.go.set_sensitive(bool(picked - {r["id"] for r in self.rows if r["state"] == "have"}))
        self._syncing = False

    def _ask(self):
        body = ("Just import adds everything you ticked, so you have more to choose from. "
                "Also apply makes it look exactly as it did there too: its theme and "
                "background, font, corners and the rotation that was in use.")
        pw = self._password_items()
        if pw:
            body += ("\n\nOne terminal will ask for your password once, for all of these:\n• "
                     + "\n• ".join(pw))
        d = Adw.AlertDialog(heading="Just import, or also apply the last settings from the file?", body=body)
        d.add_response("cancel", "Cancel")
        d.add_response("import", "Just import")
        d.add_response("apply", "Also apply")
        d.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("import")
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r != "cancel" and self._run(r == "apply"))
        d.present(self)

    def _run(self, apply):
        chosen = self._final()
        self.close()
        ImportProgress(self.win, self.path, self.manifest, chosen, apply).present(self.win)


class ImportProgress(Adw.Dialog):
    """Over the window for as long as an import runs (it can't be closed meanwhile). Two bars: the
    whole import, counted step by step (each theme, background, font, set, setting, the apply), and
    below it the current download with git's own figures: how much has arrived and how fast, in
    megabits per second. Both are always there (greyed when nothing is downloading), so nothing moves.
    Then, in the same place, the result: what came in, what didn't and why, and Retry for themes that
    couldn't be downloaded."""

    def __init__(self, win, path, manifest, chosen, apply):
        super().__init__(title="Importing", content_width=520, can_close=False)
        self.win, self.path, self.manifest = win, path, manifest
        self._latest, self._queued = None, False
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=24, margin_end=24,
                           margin_top=18, margin_bottom=22)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False))
        view.set_content(self.box)
        self.set_child(view)
        self._build_progress()
        self._start(chosen, apply)

    def _build_progress(self):
        clear(self.box)
        self.box.append(label("Importing…", "section-title"))
        self.count = label("Getting ready…", "card-name")
        self.box.append(self.count)
        self.step = label("", "dim", wrap=True)
        self.step.set_lines(2)
        self.step.set_ellipsize(Pango.EllipsizeMode.END)
        self.step.set_size_request(-1, 40)   # two lines' room, always (no layout shift)
        self.box.append(self.step)
        self.overall = Gtk.ProgressBar()
        self.box.append(self.overall)
        self.box.append(label("Download", "dim small", margin_top=8))
        self.download = Gtk.ProgressBar(valign=Gtk.Align.START, vexpand=False)
        self.download.add_css_class("download-bar")
        self.box.append(self.download)
        self.speed = label("", "small")
        self.box.append(self.speed)
        # Cancel: always in its place (no layout shift), usable while the password terminal is open.
        btns = Gtk.Box(halign=Gtk.Align.END, margin_top=8)
        self.cancel = button("Cancel", "", lambda *_: bg(share.cancel_password_step),
                             tooltip="Close the password terminal: what it was going to do isn't done")
        self.cancel.set_sensitive(False)
        btns.append(self.cancel)
        self.box.append(btns)
        self._show({"step": 0, "total": 1, "text": "", "download": None})

    def _start(self, chosen, apply):
        def progress(info):
            # Git can report many times a second: the window shows the latest, at most once per frame.
            self._latest = info
            if not self._queued:
                self._queued = True
                GLib.idle_add(self._drain)
        bg(lambda: share.run_import(self.path, self.manifest, chosen, apply, progress), self._finished)

    def _drain(self):
        self._queued = False
        if self._latest is not None and self.overall.get_root() is not None:
            self._show(self._latest)
        return False

    def _show(self, info):
        step, total, dl = info["step"], info["total"], info["download"]
        self.cancel.set_sensitive(bool(info.get("cancel")))
        if step:
            self.count.set_text(f"Step {step} of {total}")
            self.step.set_text(info["text"])
        within = (dl["pct"] / 100) if dl else 0
        done = total if info.get("finished") else max(step - 1, 0) + within
        self.overall.set_fraction(min(1, done / total))
        for w in (self.download, self.speed):
            (w.remove_css_class if dl else w.add_css_class)("idle")
        if not dl:
            self.download.set_fraction(0)
            self.speed.set_text("No download in this step")
            return
        self.download.set_fraction(dl["pct"] / 100)
        got = f"{dl['got'] / 1e6:.1f} MB received" if dl["got"] else "Connecting…"
        if dl["rate"] is None:
            self.speed.set_text(got)
        else:
            mbps = dl["rate"] * 8 / 1e6
            self.speed.set_text(f"{got} · {mbps:.2f} Mbps" if mbps < 10 else f"{got} · {mbps:.1f} Mbps")

    def _finished(self, result):
        win = self.win
        clear(self.box)
        self.set_can_close(True)
        self.set_title("Import finished" if not isinstance(result, Exception) else "Import stopped")
        if isinstance(result, Exception):
            self.box.append(label("The import stopped", "section-title"))
            self.box.append(label(str(result), "dim", wrap=True, selectable=True))
            did, notes, failed = [], [], []
        else:
            did, notes, failed = result
            changes = f"{len(did)} change{'s' * (len(did) != 1)}"
            heading = (f"Imported {changes}; {len(notes)} thing{'s' * (len(notes) != 1)} didn't come in"
                       if did and notes else f"Imported {changes}" if did
                       else "Nothing was imported" if notes else "Nothing new: you had it all")
            self.box.append(label(heading, "section-title"))
        lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for text in notes:
            lines.append(label(f"✗  {text}", "", wrap=True, selectable=True))
        for text in did:
            lines.append(label(f"✓  {text}", "dim", wrap=True))
        if did or notes:
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                        max_content_height=360)
            scroll.set_child(lines)
            self.box.append(scroll)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        if failed:
            names = ", ".join(data.display_name(n) for n in failed)
            btns.append(button(f"Retry {names}", "", lambda *_: self._retry(failed)))
        btns.append(button("Close", "primary", lambda *_: self.close()))
        self.box.append(btns)
        # Everything in the window shows what's on disk now (the Rotation tab included).
        win._pending |= {"local", "state", "rotation"}
        win._settle()

    def _retry(self, failed):
        self.set_can_close(False)
        self.set_title("Importing")
        self._build_progress()
        self._start({f"theme:{n}" for n in failed}, False)


# --------------------------------------------------------------------------- rotation
#
# The tab saves its settings (data.ROTATION_FILE); the engine (omaskins-rotate, run by the OmaSkins
# service plugin) does the rotating, OmaSkins open or not, and writes its status back for the tab.
#
# Layout rule (owner's, for every strip in the app): a strip is always as big as the
# largest thing it can hold. Options that don't apply are greyed out in place, never
# hidden, so nothing grows, shrinks or jumps when a switch is flipped.

APPLY_BLOCKED = "Theme rotation is on. Add this theme to the rotation on the Rotation tab."
BGS_BLOCKED = "Background rotation is on. Pick backgrounds on the Rotation tab."

ROT_HINTS = {
    (False, False): "Rotation is off. Turn on Themes or Backgrounds above.",
    (True, False): "Backgrounds aren't rotating: your current background stays when the theme changes.",
    (False, True): ("Click a background to add or remove it. Your current theme stays, background changes "
                      "and the selected ones take turns from any theme you like."),
    (True, True): ("Click a background to add or remove it. This theme's selected ones take turns; after the last "
                     "one, the next theme comes in."),
}


MIX_HINT = ("Mix it up! is on: click a background to add or remove it. Each change pairs a random theme with a "
            "random selected background from any theme in the list.")
UNTICKED_HINT = "Tick it on the left to add it to the rotation; your selected and unselected picks come back as saved."


def engine_alive(st):
    """The engine records its process id; it's running if that process is still omaskins-rotate."""
    try:
        with open(f"/proc/{int(st.get('pid'))}/cmdline", "rb") as f:
            return b"omaskins-rotate" in f.read()
    except (OSError, TypeError, ValueError):
        return False


def rotation_status_text(plan, st, alive):
    """One line for the Rotation tab from the engine's status file."""
    if not (plan.themes or plan.backgrounds):
        return ""
    if not alive:
        return "Engine off: plugin enabled?"
    if plan.themes and st.get("theme_status") in ("single", "empty"):
        return "Check at least one more theme"
    if plan.backgrounds and st.get("bg_status") == "empty":
        return "Pick some backgrounds"
    if plan.backgrounds and st.get("bg_status") == "single":
        return "Select a few more backgrounds"
    nxt = st.get("next_change")
    return f"Next change at {rotation.hhmm(nxt)}" if nxt and st.get("status") == "running" else "Starting…"


def interval_stepper(minutes, on_change):
    """− / + step through data.INTERVALS only (5, 10, 15, 20, 30 min, then hours that divide the day).
    Built from a read-only box and two buttons with the spin button's own icons: a Gtk.SpinButton
    reads its text back as a number before every step ("20 min" -> 20), and PyGObject can't answer
    its "input" signal, so it can't show units and still step."""
    steps = data.INTERVALS
    at = [steps.index(data.snap_interval(minutes))]
    box = Gtk.Box(valign=Gtk.Align.CENTER)
    box.add_css_class("linked")
    shown = Gtk.Entry(editable=False, can_focus=False, width_chars=6, max_width_chars=6, xalign=0.5)
    minus = Gtk.Button(icon_name="value-decrease-symbolic", tooltip_text="Less often")
    plus = Gtk.Button(icon_name="value-increase-symbolic", tooltip_text="More often")

    def show():
        shown.set_text(data.interval_text(steps[at[0]]))
        minus.set_sensitive(at[0] > 0)
        plus.set_sensitive(at[0] < len(steps) - 1)

    def step(by):
        at[0] = min(len(steps) - 1, max(0, at[0] + by))
        show()
        on_change(steps[at[0]])

    minus.connect("clicked", lambda _b: step(-1))
    plus.connect("clicked", lambda _b: step(+1))
    for w in (shown, minus, plus):
        box.append(w)
    def set_minutes(minutes):   # a loaded Rotation set: show its interval, without reporting a change
        at[0] = steps.index(data.snap_interval(minutes))
        show()
    show()
    box.step, box.text, box.set_minutes = step, shown.get_text, set_minutes
    return box


def number_spin(value, lo, hi, on_change):
    s = Gtk.SpinButton.new_with_range(lo, hi, 1)
    s.set_value(value)
    s.set_valign(Gtk.Align.CENTER)
    s.connect("value-changed", lambda w: on_change(int(w.get_value())))
    return s


def field(name, *widgets):
    row = Gtk.Box(spacing=10)
    row.append(label(name, "kv-key", width_chars=12, valign=Gtk.Align.CENTER))
    for w in widgets:
        row.append(w)
    return row


def labelled_switch(text, active, on_change, tooltip=None):
    box = Gtk.Box(spacing=8, tooltip_text=tooltip)
    sw = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
    sw.connect("notify::active", lambda s, _p: on_change(s.get_active()))
    box.append(sw)
    if text:
        box.append(label(text, valign=Gtk.Align.CENTER))
    return box, sw


def rot_card(bgd, on):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER, width_request=CARD_W)
    box.add_css_class("tile")
    box.append(picture(CARD_W, CARD_H, bgd.path, priority=0, zoom=True))
    row = Gtk.Box(spacing=6)
    row.append(label(bgd.path.name, "small", hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE))
    if bgd.yours:
        row.append(badge("Yours", "enabled"))
    box.append(row)
    child = Gtk.FlowBoxChild(child=box)
    child.bgd, child.tile = bgd, box
    set_card_on(child, on)
    return child


def set_card_on(child, on):
    if on:
        child.tile.remove_css_class("off")
    else:
        child.tile.add_css_class("off")
    child.set_tooltip_text("In the rotation" if on else "Not in the rotation")


class RotationPage(Gtk.Box):
    """The Rotation tab: the settings the rotation engine follows, saved as they change."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.plan = data.load_rotation(win.current_theme)
        self._save_id, self._loading, self._hover_set = 0, False, None
        self.selected = None          # name of the theme shown on the right
        self.rows = {}                # theme name -> ListBoxRow
        self._syncing = False
        p = self.plan

        self.top = top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top.add_css_class("toolbar")
        # Two switches, one interval: changes land on the clock (20 min = :00, :20, :40).
        # No "Rotate" label (owner, 2026-10-02): the Themes switch starts at the row's own padding, and
        # the row fits the window's default width without a scroll bar.
        row1 = Gtk.Box(spacing=10)
        box, self.themes_switch = labelled_switch("Themes", p.themes, lambda on: self._on_switch("themes", on),
                                                  "Rotate through the checked themes")
        row1.append(box)
        box, self.bgs_switch = labelled_switch("Backgrounds", p.backgrounds,
                                               lambda on: self._on_switch("backgrounds", on),
                                               "Rotate through the selected backgrounds")
        box.set_margin_start(14)
        row1.append(box)
        self.interval = Gtk.Box(spacing=8, margin_start=14,
                                tooltip_text="Changes land on the clock: every 20 min = at :00, :20 and :40")
        self.interval.append(label("every", "dim", valign=Gtk.Align.CENTER))
        self.stepper = interval_stepper(p.minutes, lambda m: (setattr(self.plan, "minutes", m), self._changed()))
        self.interval.append(self.stepper)
        row1.append(self.interval)
        # Dawn & Dusk: day and night theme sets, switching at sunrise and sunset where you are (worked
        # out once per boot, nothing to set). Greyed unless Themes is on; the wrapper keeps its tooltip.
        sun = data.sun_times_now()
        at = lambda m: f"{m // 60:02d}:{m % 60:02d}"
        tip = (f"Location: {sun['where']}\n"
               f"Dawn (day themes) from sunrise: {at(sun['rise'])}\n"
               f"Dusk (night themes) from sunset: {at(sun['set'])}\n"
               "Worked out once at startup. Needs Themes on.")
        self.dd_box, self.dd_switch = labelled_switch("Dawn & Dusk", p.dawn_dusk, self._on_dawn_dusk, tip)
        self.dd_switch.set_tooltip_text(tip)
        dd_wrap = Gtk.Box(margin_start=14, tooltip_text=tip)  # still shows while greyed (Themes off)
        dd_wrap.append(self.dd_box)
        row1.append(dd_wrap)
        # Mix it up! (owner's "nutty" idea): text left of its switch; needs both Themes and Backgrounds.
        mix_tip = ("Any theme with any background.\nEvery change pairs a random theme with a random selected "
                   "background from any theme in the rotation.")
        self.mix_box = Gtk.Box(spacing=8)
        self.mix_box.append(label("Mix it up!", valign=Gtk.Align.CENTER))
        self.mix_switch = Gtk.Switch(active=p.mix, valign=Gtk.Align.CENTER, tooltip_text=mix_tip)
        self.mix_switch.connect("notify::active", lambda s, _p: self._on_mix(s.get_active()))
        self.mix_box.append(self.mix_switch)
        mix_wrap = Gtk.Box(margin_start=14, tooltip_text=mix_tip)  # tooltip still shows while greyed
        mix_wrap.append(self.mix_box)
        row1.append(mix_wrap)
        # The engine's word on it ("Next change at 09:20"): one line at the end of the row, taking the
        # room left over (all of "Next change at 09:20" at the window's default size); in a narrower
        # window a longer message ends in "…" and its tooltip has it whole. Nothing else moves.
        self.status = label("", "dim", hexpand=True, xalign=1, width_chars=12, max_width_chars=31,
                            ellipsize=Pango.EllipsizeMode.END, valign=Gtk.Align.CENTER, margin_start=14)
        row1.append(self.status)
        top.append(row1)
        # (the window puts this row in its one sideways-scrolling area, with the main bar: tab_rows)

        # The Dawn / Dusk tabs belong to the theme list, so they sit over that column only; the
        # backgrounds on the right start right under the settings row.
        pane = Gtk.Box(vexpand=True)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.period_bar, _c, self.period_btns = sub_tabs(("Dawn", "Dusk"), self._on_period)
        left.append(self.period_bar)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("sidebar")
        self.list.connect("row-selected", self._on_row)
        # 280 px wide when there's room; narrower (names end in "…", ~10 characters always show) before
        # the pictures on the right lose their last column.
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, overlay_scrolling=True,
                                propagate_natural_width=True, max_content_width=280)
        sw.add_css_class("sidebar")
        sw.set_child(self.list)
        left.append(sw)
        left.append(self._build_sets())
        pane.append(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, margin_start=18,
                        margin_end=18, margin_top=14)
        head = Gtk.Box(spacing=8)
        self.title = label("", "plugin-name", hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        head.append(self.title)
        self.count = label("", "dim", valign=Gtk.Align.CENTER)
        head.append(self.count)
        right.append(head)
        # Always two lines tall, whichever hint is showing, so the grid below never moves.
        self.hint = label("", "dim small", wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END, yalign=0)
        right.append(self.hint)
        self.grid = flow()
        self.grid.add_css_class("rot-grid")
        self.grid.set_margin_start(0)
        self.grid.set_margin_end(0)
        self.grid.connect("child-activated", lambda _f, c: self._toggle(c))
        right.append(scrolled(self.grid))
        pane.append(right)
        self.append(pane)
        self._sync_strips()
        self._show_status()
        GLib.timeout_add_seconds(2, self._show_status)

    # ---- saved Rotation sets (owner, 2026-10-01)
    def _build_sets(self):
        """[ name of this set ][ ▾ ][ Save ] under the theme list. Save stores the whole Rotation setup
        under the name (new, or after a question, over another set); ▾ lists the other saved sets
        (picking one switches the whole setup to it, and its name moves into the box); hover one in
        the list and press Delete to delete it (asked first)."""
        bar = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=8, margin_bottom=8)
        bar.add_css_class("sets-bar")
        self.set_entry = Gtk.Entry(placeholder_text="Name this set…", hexpand=True, width_chars=6,
                                   max_length=data.SET_NAME_MAX, text=self.plan.set_name,
                                   tooltip_text="Save the whole Rotation setup under a name, to switch back to later")
        self.set_entry.connect("changed", lambda *_: self._sync_sets())
        self.set_entry.connect("activate", lambda *_: self.set_save.get_sensitive() and self._save_set())
        bar.append(self.set_entry)
        self.sets_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True)
        self.sets_list.connect("row-activated", lambda _l, row: self._use_set(row.set_name))
        pop = Gtk.Popover(child=Gtk.ScrolledWindow(child=self.sets_list, propagate_natural_height=True,
                                                   propagate_natural_width=True, max_content_height=320,
                                                   hscrollbar_policy=Gtk.PolicyType.NEVER))
        pop.connect("show", lambda *_: self._fill_sets())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_sets_key)
        pop.add_controller(keys)
        self.sets_pop = pop
        bar.append(Gtk.MenuButton(icon_name="pan-down-symbolic", popover=pop, valign=Gtk.Align.CENTER,
                                  tooltip_text="Your saved Rotation sets (hover one and press Delete to delete it)"))
        self.set_save = button("Save", "primary", lambda *_: self._save_set())
        self.set_save.set_valign(Gtk.Align.CENTER)
        bar.append(self.set_save)
        self._sync_sets()
        return bar

    def _sync_sets(self):
        """Save is clickable only when there's something to save: a new name, or changes since the set
        was saved."""
        name = data.clean_set_name(self.set_entry.get_text())
        self.set_save.set_sensitive(bool(name) and (name != self.plan.set_name or data.set_has_changes(self.plan)))

    def _fill_sets(self):
        self.sets_list.remove_all()
        self._hover_set = None
        others = [n for n in data.rotation_sets() if n != self.plan.set_name]
        if not others:
            self.sets_list.append(Gtk.ListBoxRow(child=label("No other saved sets yet", "dim", margin_start=10,
                                                             margin_end=10, margin_top=6, margin_bottom=6),
                                                 activatable=False))
        for name in others:
            row = Gtk.ListBoxRow(child=label(name, margin_start=10, margin_end=10, margin_top=6, margin_bottom=6))
            row.set_name = name
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda *_a, n=name: setattr(self, "_hover_set", n))
            hover.connect("leave", lambda *_a, n=name: self._hover_set == n and setattr(self, "_hover_set", None))
            row.add_controller(hover)
            self.sets_list.append(row)

    def _on_sets_key(self, _c, key, *_):
        if key not in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) or not self._hover_set:
            return False
        name = self._hover_set
        d = Adw.AlertDialog(heading=f"Delete the Rotation set “{name}”?",
                            body="Its saved themes, backgrounds and settings are deleted. Your current setup "
                                 "doesn't change.")
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r == "delete" and (data.delete_rotation_set(name), self._fill_sets(),
                                                               self.win.toasts.add_toast(Adw.Toast(
                                                                   title=f"Deleted the set “{name}”.", timeout=3))))
        d.present(self.win)
        return True

    def _save_set(self):
        name = data.clean_set_name(self.set_entry.get_text())
        if not name:
            return

        def save():
            self._save_now_quietly()
            data.save_rotation_set(name, self.plan)
            self.set_entry.set_text(name)
            self._sync_sets()
            self.win.toasts.add_toast(Adw.Toast(title=f"Saved this Rotation setup as “{name}”.", timeout=3))
        if name != self.plan.set_name and name in data.rotation_sets():
            d = Adw.AlertDialog(heading=f"Replace the set “{name}”?",
                                body=f"A Rotation set called “{name}” is already saved. Replace it with this setup?")
            d.add_response("cancel", "Cancel")
            d.add_response("replace", "Replace")
            d.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_close_response("cancel")
            d.connect("response", lambda _d, r: r == "replace" and save())
            d.present(self.win)
        else:
            save()

    def _save_now_quietly(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0

    def _use_set(self, name):
        self.sets_pop.popdown()

        def switch():
            self._save_now_quietly()
            self.plan = data.use_rotation_set(name, self.win.current_theme)
            self._show_plan()
            self.win.toasts.add_toast(Adw.Toast(title=f"Rotation set “{name}” in use.", timeout=3))
        if data.set_has_changes(self.plan) and (self.plan.set_name or self.plan.running()):
            what = f"the set “{self.plan.set_name}”" if self.plan.set_name else "this unsaved setup"
            d = Adw.AlertDialog(heading=f"Switch to “{name}”?",
                                body=f"The changes to {what} haven't been saved as a set. Switch anyway?")
            d.add_response("cancel", "Cancel")
            d.add_response("switch", "Switch")
            d.set_close_response("cancel")
            d.connect("response", lambda _d, r: r == "switch" and switch())
            d.present(self.win)
        else:
            switch()

    def _show_plan(self):
        """Every control on the tab shows self.plan (a set was just loaded), handlers kept quiet."""
        p = self.plan
        self._loading = True
        try:
            self.themes_switch.set_active(p.themes)
            self.bgs_switch.set_active(p.backgrounds)
            self.dd_switch.set_active(p.dawn_dusk)
            self.mix_switch.set_active(p.mix)
            self.stepper.set_minutes(p.minutes)
            self.period_btns[p.period].set_active(True)
            self.set_entry.set_text(p.set_name)
        finally:
            self._loading = False
        self._sync_strips()
        self.win.rotation_changed()
        self._restyle()
        self._sync_sets()

    # ---- data
    def _changed(self):
        """Save shortly after the last change (a spin button held down fires many)."""
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self._save)

    def _save(self):
        self._save_id = 0
        try:
            data.save_rotation(self.plan)
            self._saved_ok = True
            if hasattr(self, "set_save"):
                self._sync_sets()
        except OSError as e:
            self._saved_ok = False
            self.win.toasts.add_toast(Adw.Toast(title=f"Couldn't save the rotation settings: {e}", timeout=8))
        return False

    def save_now(self):
        """Save at once (Update all), whether or not a change is waiting. True if it worked."""
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save()
        return self._saved_ok

    def _show_status(self):
        st = data.rotation_status()
        text = rotation_status_text(self.plan, st, engine_alive(st))
        self.status.set_text(text)
        self.status.set_tooltip_text(text or None)
        return True

    def _theme(self, name):
        return next((t for t in self.win.local if t.name == name), None)

    def _backgrounds(self, name):
        t = self._theme(name)
        return data.backgrounds_for(t) if t else []

    def sync_from_disk(self):
        """Show the settings file as it is now (an import, a set switched elsewhere, the engine). An
        edit of yours still waiting to be saved wins: it's about to be written. True if it changed."""
        if self._save_id:
            return False
        disk = data.load_rotation(self.win.current_theme)
        if json.dumps(disk.to_dict(), sort_keys=True) == json.dumps(self.plan.to_dict(), sort_keys=True):
            self._sync_sets()   # the saved sets may have changed on their own
            return False
        self.plan = disk
        self._show_plan()
        return True

    def refresh(self):
        """Called when themes are (re)loaded. Starts from the file, never from what this window last
        showed: tidying a stale copy and saving it would write old settings over new ones."""
        self.sync_from_disk()
        p = self.plan
        before = json.dumps(p.to_dict(), sort_keys=True)
        p.forget_missing({t.name for t in self.win.local})
        p.current_theme = self.win.current_theme
        p.seed_solo(self._backgrounds(p.current_theme))
        if json.dumps(p.to_dict(), sort_keys=True) != before:
            self._changed()
        self._build_list()

    # ---- left: every installed theme
    def _build_list(self):
        keep = self.selected
        self.list.remove_all()
        self.rows = {}
        cur = self.win.current_theme
        for t in sorted(self.win.local, key=lambda t: (t.name != cur, t.title.lower())):
            row = Gtk.ListBoxRow()
            row.theme_name = t.name
            box = Gtk.Box(spacing=8)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            col.append(label(t.title, ellipsize=Pango.EllipsizeMode.END, width_chars=10))  # never fewer shown
            row.summary = label("", "dim small", ellipsize=Pango.EllipsizeMode.END)
            col.append(row.summary)
            box.append(col)
            # One fixed-size slot on the right: a checkbox (Themes on) or the current mark (Themes off),
            # kept 18 px clear of the scrollbar so a click meant for the box doesn't grab the bar.
            row.slot = Gtk.Stack(hhomogeneous=True, vhomogeneous=True, valign=Gtk.Align.CENTER, margin_end=18)
            row.check = Gtk.CheckButton(valign=Gtk.Align.CENTER, tooltip_text="Include this theme in the rotation")
            row.check.connect("toggled", lambda c, n=t.name: self._on_check(n, c.get_active()))
            row.slot.add_named(row.check, "check")
            row.slot.add_named(mark("Current theme"), "mark")
            row.slot.add_named(Gtk.Box(), "none")
            box.append(row.slot)
            row.set_child(box)
            self.list.append(row)
            self.rows[t.name] = row
        for row in self.rows.values():
            self._style_row(row)
        self._select(keep)

    def _style_row(self, row):
        p = self.plan
        self._syncing = True
        if p.themes:
            on = p.is_checked(row.theme_name)
            row.slot.set_visible_child_name("check")
            row.check.set_active(on)
        else:
            on = True
            row.slot.set_visible_child_name("mark" if row.theme_name == self.win.current_theme else "none")
        self._syncing = False
        # Unticked themes are dimmed but can still be opened, to look at their backgrounds before
        # adding them (owner, 2026-09-30); opening one doesn't tick it.
        (row.remove_css_class if on else row.add_css_class)("unchecked")
        bgs = self._backgrounds(row.theme_name)
        if p.themes and not on:
            text = "Not in the rotation"
        elif not bgs:
            text = "No backgrounds"
        else:
            text = f"{p.picked_count(row.theme_name, bgs)} of {len(bgs)} backgrounds"
        row.summary.set_text(text)

    def _select(self, name=None):
        """Keep `name` selected if it's still listed, else the first theme."""
        rows = list(self.rows.values())
        row = self.rows.get(name) if name in self.rows else (rows[0] if rows else None)
        if row:
            self.list.select_row(row)
            if self.selected == row.theme_name:
                self._show_detail()
        else:
            self.list.unselect_all()
            self.selected = None
            self._show_detail()

    def drop(self, name):
        """A theme is being removed: take it out of the rotation."""
        self.plan.drop(name)
        self._changed()
        self._restyle()

    # ---- handlers
    def _on_switch(self, which, on):
        if self._loading:
            return
        setattr(self.plan, which, on)
        self._changed()
        self._sync_strips()
        self.win.rotation_changed()
        for row in self.rows.values():
            self._style_row(row)
        self._select(self.selected)

    def _sync_strips(self):
        p = self.plan
        self.interval.set_sensitive(p.themes or p.backgrounds)
        self.dd_box.set_sensitive(p.themes)
        self.mix_box.set_sensitive(p.themes and p.backgrounds)
        self.period_bar.set_sensitive(p.themes and p.dawn_dusk)

    def _on_mix(self, on):
        if self._loading:
            return
        self.plan.mix = on
        self._changed()
        self._show_detail()

    def _on_dawn_dusk(self, on):
        if self._loading:
            return
        self.plan.set_dawn_dusk(on)
        self._changed()
        self._sync_strips()
        self._restyle()

    def _on_period(self, name):
        if self._loading:
            return
        self.plan.period = name
        self._changed()
        self._restyle()

    def _restyle(self):
        for row in self.rows.values():
            self._style_row(row)
        self._select(self.selected)

    def _on_check(self, name, on):
        if self._syncing:
            return
        self.plan.set_checked(name, on, self._backgrounds(name))
        self._changed()
        self._style_row(self.rows[name])
        self._select(name if on else self.selected)

    def _on_row(self, _lb, row):
        self.selected = row.theme_name if row else None
        self._show_detail()

    def _toggle(self, child):
        p, name = self.plan, self.selected
        if not name or not p.backgrounds or (p.themes and not p.is_checked(name)):
            return  # an unticked theme's backgrounds are only for looking
        on = p.toggle(name, child.bgd, self._backgrounds(name))
        self._changed()
        set_card_on(child, on)  # in place, so the grid doesn't jump back to the top
        self._style_row(self.rows[name])
        self._update_count()

    # ---- right: the selected theme's backgrounds
    def _update_count(self):
        name = self.selected
        bgs = self._backgrounds(name) if name else []
        self.count.set_text(f"{self.plan.picked_count(name, bgs)} of {len(bgs)} in the rotation" if bgs else "")

    def _show_detail(self):
        p, name = self.plan, self.selected
        # An unticked theme (Themes on) can be looked at: its backgrounds show greyed; ticking it brings
        # back your saved bright / dim choices at once.
        looking = bool(name) and p.themes and not p.is_checked(name)
        self.hint.set_text(UNTICKED_HINT if looking else MIX_HINT if p.mix and p.themes and p.backgrounds
                           else ROT_HINTS[(p.themes, p.backgrounds)])
        self.hint.set_size_request(-1, self.hint.create_pango_layout("x\nx").get_pixel_size()[1])
        self.grid.set_sensitive(p.backgrounds and not looking)
        self.grid.remove_all()
        t = self._theme(name) if name else None
        self.title.set_text(t.title if t else "")
        self._update_count()
        if looking:
            self.count.set_text("Not in the rotation")
        if not t:
            self.grid.append(Gtk.FlowBoxChild(child=label("Check a theme on the left to add it to the rotation.",
                                                          "empty")))
            return
        bgs = self._backgrounds(name)
        for b in bgs:
            self.grid.append(rot_card(b, not looking and p.is_picked(name, b, bgs)))
        if not bgs:
            self.grid.append(Gtk.FlowBoxChild(child=label("No backgrounds for this theme yet.", "empty")))


# --------------------------------------------------------------------------- window

class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=TITLE, default_width=1180, default_height=820)
        self.add_css_class("omaskins")
        self.community, self.local, self.fonts, self.font_pkgs = [], [], [], []
        self.stars = {}  # repo key -> GitHub stars (Top Picks order for themes)
        self.font_pop = data.cached_font_popularity()  # package -> % of Arch users (Top Picks for fonts)
        self.not_mono = {}  # installed font packages with no monospace face: {package: [families]}
        self.builtin_pkg, self.removed_builtins, self.hidden_builtins = "", [], []
        self.current_theme = data.current_theme_name()
        self._last_bg = data.current_background()
        # Set when a theme gets applied while you're elsewhere: the next visit to Backgrounds opens on
        # the (new) current theme; otherwise Backgrounds keeps the theme you last looked at.
        self._bg_follow_current = False
        self._running = set()
        self.preview_rows, self._preview_queue, self._preview_busy = {}, [], False
        self._asked_aether = False
        self._previews_loaded = False   # Browse's downloaded preview fonts: loaded with the Fonts tab
        self._quiet_until, self._pending = 0, set()
        self.current_font = ""
        self.font_base = 12
        self.search_text = ""
        self.bg_theme = None
        self.page = None
        self._timers, self._monitors, self._last_css = {}, [], None
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
        # Transparency for OmaSkins itself: only its background (the previews, text and buttons on it stay
        # solid; Hyprland leaves this window out of its whole-window fade). Its own small style layer, so a
        # fade only rewrites two lines.
        self.alpha_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.alpha_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self._alpha, self._alpha_fade, self._bg = data.transparency_values(data.transparency_step())[:2], 0, None
        self.reload_theme()

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(label(TITLE, "window-title", xalign=0.5))  # centred (owner, 2026-10-02)

        # main tabs + search + share
        bar = Gtk.Box()
        bar.add_css_class("tabbar")
        self.main_tabs = {}
        first = None
        for name in ("Themes", "Backgrounds", "Fonts", "Rotation"):
            btn = Gtk.ToggleButton(label=name)
            btn.add_css_class("tab")
            if first:
                btn.set_group(first)
            else:
                first = btn
                btn.set_active(True)
            btn.connect("toggled", lambda b, n=name: b.get_active() and self._on_main_tab(n))
            if name == "Backgrounds":
                self.bg_tab_wrap = Gtk.Box()
                self.bg_tab_wrap.append(btn)
                bar.append(self.bg_tab_wrap)
            else:
                bar.append(btn)
            self.main_tabs[name] = btn
        # Owner (2026-10-02): Top Picks … Update all sit at the right, Update all as far from the window's
        # edge as the rows below end, so the space after the tabs shows the two groups apart. Same on
        # every tab (one bar for all of them); in a narrow window at least 24 px stay between them.
        bar.append(Gtk.Box(hexpand=True))
        # Owner: how Themes and Fonts lists sort (Browse and Installed), kept between launches.
        # Greyed, never hidden, on the tabs it doesn't apply to (no-layout-shift rule).
        self.sort_mode = data.sort_mode()
        self.sort_box = Gtk.Box(valign=Gtk.Align.CENTER, margin_start=24, margin_end=8)
        self.sort_box.add_css_class("sortbar")
        first_sort = None
        for mode, text, tip in (("top", "Top Picks", "Most popular first: Omarchy's own themes, then GitHub "
                                 "stars; fonts by how many Arch users have them"),
                                ("az", "A → Z", "Alphabetical")):
            btn = Gtk.ToggleButton(label=text, tooltip_text=tip)
            btn.add_css_class("subtab")
            if first_sort:
                btn.set_group(first_sort)
            else:
                first_sort = btn
            btn.set_active(mode == self.sort_mode)
            btn.connect("toggled", lambda b, m=mode: b.get_active() and self._on_sort(m))
            self.sort_box.append(btn)
        bar.append(self.sort_box)
        self.search = Gtk.SearchEntry(placeholder_text="Search themes…", valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", self._on_search)
        bar.append(self.search)
        share_btn = button("Share…", "", lambda *_: self.show_export(), tooltip="Export your themes, backgrounds, fonts and settings as one file")
        share_btn.set_valign(Gtk.Align.CENTER)
        share_btn.set_margin_start(8)
        bar.append(share_btn)
        import_btn = button("Import…", "", lambda *_: self.show_import(),
                            tooltip="Bring in a setup someone shared (or your own backup)")
        import_btn.set_valign(Gtk.Align.CENTER)
        import_btn.set_margin_start(4)
        bar.append(import_btn)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Update all",
                             valign=Gtk.Align.CENTER, margin_start=4)
        refresh.add_css_class("icon-btn")
        refresh.connect("clicked", lambda *_: self.update_all())
        bar.append(refresh)
        # The main bar and the current tab's own row scroll sideways TOGETHER, as one area (owner,
        # 2026-10-01: two separate thin strips were hard to use in a narrow window).
        self.tab_rows = Gtk.Stack(hhomogeneous=False, vhomogeneous=False,
                                  transition_type=Gtk.StackTransitionType.NONE)
        top_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top_rows.append(bar)
        top_rows.append(self.tab_rows)
        main.append(hstrip(top_rows))

        # hhomogeneous off: each tab needs only its own width, not the widest tab's.
        self.main_stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                                    transition_duration=100, hhomogeneous=False)
        self.main_stack.add_named(self._build_themes(), "Themes")
        self.main_stack.add_named(self._build_backgrounds(), "Backgrounds")
        self.main_stack.add_named(self._build_fonts(), "Fonts")
        self.rotation = RotationPage(self)
        self.tab_rows.add_named(self.rotation.top, "Rotation")
        self.main_stack.add_named(self.rotation, "Rotation")
        main.append(self.main_stack)

        self.status = label("Loading…", "statusbar")
        main.append(self.status)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120,
                               hhomogeneous=False)
        self.stack.add_named(main, "main")

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("frame-root")
        frame.set_overflow(Gtk.Overflow.HIDDEN)  # clipped to the frame's rounded corners
        frame.append(self.stack)
        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(frame)
        self.set_content(self.toasts)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._watch_files()
        self.load()

    # ---- builders
    def _build_themes(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar, self.theme_counts, self.theme_sub_btns = sub_tabs(("Browse", "Installed"), self._on_theme_sub)
        bar.append(self._build_corners())
        self.tab_rows.add_named(bar, "Themes")
        self.theme_stack = Gtk.Stack(vexpand=True)
        self.theme_flows = {}
        for name in ("Browse", "Installed"):
            fb = flow()
            fb.set_filter_func(self._theme_filter)
            fb.connect("child-activated", lambda _f, child: self.show_theme(child.entry))
            self.theme_flows[name] = fb
            self.theme_stack.add_named(scrolled(fb), name)
        box.append(self.theme_stack)
        # Owner: a new launch opens on Installed; a switch to Browse lasts until OmaSkins is closed.
        self.theme_sub_btns["Installed"].set_active(True)
        return box

    def _build_backgrounds(self):
        pane = Gtk.Box()
        self.bg_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.bg_list.add_css_class("sidebar")
        self.bg_list.connect("row-selected", self._on_bg_theme)
        # 230 px wide when there's room; narrower (names end in "…") before the pictures lose a column.
        side = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, overlay_scrolling=True,
                                  propagate_natural_width=True, max_content_width=230)
        side.set_child(self.bg_list)
        side.add_css_class("sidebar")
        pane.append(side)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        tools = Gtk.Box(spacing=8)
        tools.add_css_class("toolbar")
        self.bg_title = label("", "plugin-name", hexpand=True)
        tools.append(self.bg_title)
        tools.append(button("Add backgrounds…", "", lambda *_: self.bg_theme and self._pick_backgrounds()))
        tools.append(button("Open folder", "flat", lambda *_: self.bg_theme and self.do_action(
            data.open_folder_action(self.bg_theme.name))))
        tools.set_hexpand(True)
        self.tab_rows.add_named(tools, "Backgrounds")
        self.bg_flow = flow()
        self.bg_flow.set_filter_func(lambda c: not self.search_text or self.search_text in c.bgd.path.name.lower())
        right.append(scrolled(self.bg_flow))
        pane.append(right)
        return pane

    def _build_corners(self):
        """Global Rounded Corners: one radius for windows, menus and popups, whatever the theme."""
        on, px = data.corners_setting()
        # Your saved radius, else the default (not the theme's own: switching on should look the same everywhere).
        self.corners = {"on": on, "px": px or data.CORNERS_DEFAULT}
        box = Gtk.Box(spacing=10, margin_start=24, valign=Gtk.Align.CENTER,
                      tooltip_text="Round the corners of windows, menus and popups for every theme, "
                                   "rotating or not. Off = each theme's own corners.")
        box.append(label("Rounded Corners", valign=Gtk.Align.CENTER))
        sw = Gtk.Switch(active=on, valign=Gtk.Align.CENTER)
        box.append(sw)
        self.corners_switch = sw
        self._corners_syncing = False
        # Greyed, never hidden, while off (no-layout-shift rule).
        self.corner_size = Gtk.Box(spacing=8, sensitive=on)
        self.corners_spin = number_spin(self.corners["px"], 0, data.CORNERS_MAX, lambda v: self._on_corners(px=v))
        self.corner_size.append(self.corners_spin)
        self.corner_size.append(label("px", "dim", valign=Gtk.Align.CENTER))
        box.append(self.corner_size)
        sw.connect("notify::active", lambda w, _p: self._on_corners(on=w.get_active()))
        box.append(self._build_transparency())
        return box

    def _build_transparency(self):
        """Transparency (owner, 2026-10-02): five steps, a fixed width so nothing moves; changing it fades
        the windows over 1.5 s (no toast: the fade is the feedback)."""
        box = Gtk.Box(spacing=10, margin_start=24, valign=Gtk.Align.CENTER,
                      tooltip_text="How see-through windows are (unfocused ones a little more).\n"
                                   "Far left: solid. Second: Omarchy's own. Far right: strongest, with blur behind.")
        box.append(label("Transparency", valign=Gtk.Align.CENTER))
        steps = len(data.TRANSPARENCY_STEPS)
        self.transparency = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, steps - 1, 1)
        self.transparency.set_draw_value(False)
        self.transparency.set_size_request(130, -1)
        self.transparency.set_valign(Gtk.Align.CENTER)
        for i in range(steps):
            self.transparency.add_mark(i, Gtk.PositionType.BOTTOM, None)
        self._transparency_syncing = True
        self.transparency.set_value(data.transparency_step())
        self._transparency_syncing = False
        self._transparency_at = data.transparency_step()
        self.transparency.connect("value-changed", self._on_transparency)
        box.append(self.transparency)
        # Qt apps (VLC, KeePassXC, ...) in the theme's colours and transparency: on by default.
        qt = Gtk.Box(spacing=10, margin_start=24, valign=Gtk.Align.CENTER,
                     tooltip_text="Qt apps (VLC, KeePassXC, ...) in the theme's colours, see-through like the "
                                  "rest. Applies to Qt apps opened from now on.")
        qt.append(label("Qt apps", valign=Gtk.Align.CENTER))
        self.qt_switch = Gtk.Switch(active=qtstyle.enabled(), valign=Gtk.Align.CENTER)
        self.qt_switch.connect("notify::active", lambda w, _p: w.get_active() != qtstyle.enabled()
                               and self.do_action(data.qt_apps_action(w.get_active())))
        qt.append(self.qt_switch)
        outer = Gtk.Box(valign=Gtk.Align.CENTER)
        outer.append(box)
        outer.append(qt)
        return outer

    def _on_transparency(self, scale):
        step = int(round(scale.get_value()))
        if scale.get_value() != step:
            scale.set_value(step)   # snap to the five steps
            return
        if self._transparency_syncing or step == self._transparency_at:
            return
        self._transparency_at = step
        self._debounce("transparency", 350, lambda: (self._fade_alpha(step),
                                                     self.do_action(data.transparency_action(step))))

    def _set_alpha(self, a, b):
        """OmaSkins' own background at `a` (focused) / `b` (unfocused); nothing else in it changes."""
        self._alpha = (a, b)
        bg = self._bg or "#000000"
        self.alpha_css.load_from_string(
            "window.omaskins, window.omaskins.csd { background: none; }\n"
            f".frame-root {{ background: {theme.rgba(bg, a)}; }}\n"
            f"window.omaskins:backdrop .frame-root {{ background: {theme.rgba(bg, b)}; }}\n")

    def _fade_alpha(self, step, seconds=1.5, frames=10):
        """Fade OmaSkins' own background to a step, together with the other windows' fade."""
        if self._alpha_fade:
            GLib.source_remove(self._alpha_fade)
        (a0, b0), (a1, b1) = self._alpha, data.transparency_values(step)[:2]
        frame = [0]

        def tick():
            frame[0] += 1
            k = frame[0] / frames
            self._set_alpha(a0 + (a1 - a0) * k, b0 + (b1 - b0) * k)
            if frame[0] >= frames:
                self._alpha_fade = 0
                return False
            return True
        self._alpha_fade = GLib.timeout_add(int(seconds * 1000 / frames), tick)

    def _debug_widths(self):
        """OMASKINS_DEBUG=1: each tab's own top row against the room the window gives the top rows."""
        sw = self.tab_rows
        while sw is not None and not isinstance(sw, Gtk.ScrolledWindow):
            sw = sw.get_parent()
        room = sw.get_hadjustment().get_page_size() if sw else 0
        c = self.tab_rows.get_first_child()
        while c is not None:
            name = self.tab_rows.get_page(c).get_name()
            dlog(f"top row {name}: needs {c.measure(Gtk.Orientation.HORIZONTAL, -1)[0]} px, room {room:.0f} px")
            c = c.get_next_sibling()
        bar = self.tab_rows.get_parent().get_first_child()
        dlog(f"main bar: needs {bar.measure(Gtk.Orientation.HORIZONTAL, -1)[0]} px")
        return False

    def _sync_transparency(self):
        """The slider shows the saved step (an import, or OmaSkins open twice); the Qt apps switch too."""
        if self.qt_switch.get_active() != qtstyle.enabled():
            self.qt_switch.set_active(qtstyle.enabled())
        if "transparency" in self._timers or self._transparency_at == data.transparency_step():
            return
        self._transparency_syncing = True
        try:
            self._transparency_at = data.transparency_step()
            self.transparency.set_value(self._transparency_at)
        finally:
            self._transparency_syncing = False
        self._fade_alpha(self._transparency_at)

    def _sync_corners(self):
        """The corners controls show the file as it is now (an import, or Omarchy's own reload)."""
        self._sync_transparency()
        if "corners" in self._timers:
            return  # your own change is about to be applied
        on, px = data.corners_setting()
        px = px or self.corners["px"]
        if (on, px) == (self.corners["on"], self.corners["px"]):
            return
        self._corners_syncing = True
        try:
            self.corners.update(on=on, px=px)
            self.corners_switch.set_active(on)
            self.corners_spin.set_value(px)
            self.corner_size.set_sensitive(on)
        finally:
            self._corners_syncing = False

    def _on_corners(self, on=None, px=None):
        if self._corners_syncing:
            return
        if on is not None:
            self.corners["on"] = on
            self.corner_size.set_sensitive(on)
        if px is not None:
            self.corners["px"] = px
        # Wait until the +/- clicking settles: each change reloads Hyprland and restarts the shell.
        self._debounce("corners", 700, lambda: self.do_action(
            data.corners_action(self.corners["on"], self.corners["px"])))

    def _pick_backgrounds(self):
        theme_name = self.bg_theme.name
        images = Gtk.FileFilter(name="Images")
        for ext in data.IMAGE_EXT:
            images.add_suffix(ext.lstrip("."))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dlg = Gtk.FileDialog(title=f"Add backgrounds to {data.display_name(theme_name)}", filters=filters)

        def chosen(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return  # cancelled
            paths = [f.get_path() for f in files if f.get_path()]
            if paths:
                self.do_action(data.add_background_action(theme_name, paths))
        dlg.open_multiple(self, None, chosen)

    def _build_fonts(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar, self.font_counts, self.font_sub_btns = sub_tabs(("Browse", "Installed"), self._on_font_sub)
        self.tab_rows.add_named(bar, "Fonts")
        self.font_stack = Gtk.Stack(vexpand=True)
        self.font_lists = {}
        for name in ("Browse", "Installed"):
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.set_filter_func(lambda r: not self.search_text or self.search_text in r.key)
            self.font_lists[name] = lb
            self.font_stack.add_named(scrolled(lb), name)
        box.append(self.font_stack)
        return box

    def action_button(self, a, on_done=None):
        css = "danger" if a.label in ("Remove", "Hide") else "primary" if a.label in ("Add", "Apply", "Use", "Restore") else ""
        b = button(a.label, css, lambda *_: self.do_action(a, on_done))
        b.set_valign(Gtk.Align.CENTER)
        if a.label == "Apply" and self.rotation.plan.themes:
            return blocked(b, APPLY_BLOCKED)
        if a.blocked:
            return blocked(b, a.blocked)
        return b

    # ---- data
    def update_all(self):
        """The ⟳ button: save the Rotation settings now (no waiting on the half-second pause after a
        change), then re-scan what's installed and fetch omarchy.org's list now, not once a day."""
        saved = self.rotation.save_now()
        self.load(force=True)
        self.toasts.add_toast(Adw.Toast(
            title="Rotation settings saved; themes, backgrounds and fonts re-scanned; omarchy.org's list updated."
            if saved else "Couldn't save the rotation settings (see the message above); lists updated.", timeout=4))

    def load(self, force=False):
        dlog("load (disk)")
        self.set_status("Loading…")

        def work():
            try:
                community = data.community_themes(force)
                err = None
            except Exception as e:  # noqa: BLE001
                community, err = [], e
            pkg = data.builtin_package()
            stars = data.cached_theme_stars()  # instant; fresh counts are fetched after the window shows
            fonts, font_pkgs = data.installed_fonts(), data.repo_fonts()
            mono = {f.package for f in fonts}
            other = data.package_families([p.package for p in font_pkgs if p.installed and p.package not in mono])
            return (community, err, stars, data.local_themes(), fonts, font_pkgs, other,
                    data.current_font(), pkg, data.removed_builtins(data.shipped_builtins(pkg)))
        bg(work, self._on_loaded)

    def _on_loaded(self, res):
        if isinstance(res, Exception):
            self.set_status(f"Couldn't load: {res}")
            return
        (self.community, err, self.stars, self.local, self.fonts, self.font_pkgs, self.not_mono, self.current_font,
         self.builtin_pkg, self.removed_builtins) = res
        self.current_theme = data.current_theme_name()
        # Popularity for Browse: refresh the GitHub star counts in the background (at most once a day),
        # re-sorting only if they changed, so the window never waits on the network.
        community = self.community
        bg(lambda: data.theme_stars(community), self._stars_fetched)
        bg(data.font_popularity, self._font_popularity_fetched)  # "Top Picks" for fonts (Arch pkgstats)
        hidden = data.hidden_in_omaskins()
        self.hidden_builtins = [t for t in self.local if t.builtin and t.name in hidden and t.name != self.current_theme]
        self.local = [t for t in self.local if t not in self.hidden_builtins]  # gone from every list, rotation too
        self.rebuild()
        n_local = len(self.local)
        msg = f"{len(self.community)} community themes · {n_local} installed · {len(data.without_icon_twins(self.fonts))} fonts"
        if err:
            msg = f"omarchy.org unreachable ({err}); showing installed only · " + msg
        self.set_status(msg)
        if not self._asked_aether:
            self._asked_aether = True
            self._offer_aether_merge()

    def _offer_aether_merge(self):
        """At launch: Aether's working copy and the theme it's a copy of are one theme in two folders
        (Aether's plain Apply always writes themes/aether), so Omarchy's own menu lists both. Offer to
        combine them (run.merge_aether); "Not now" asks again next launch."""
        twin = data.aether_twin()
        if not twin:
            self._ask_which_aether_theme()
            return
        title = data.display_name(twin[0])
        newer = "Aether's working copy" if twin[1] else f"“{title}”"
        d = Adw.AlertDialog(
            heading="Two Aether themes are the same theme",
            body=f"“{title}” and Aether's working copy “Aether” are the same theme with different settings, "
                 f"so Omarchy's theme menu lists it twice.\n\nCombine them into “{title}”? The newer colours "
                 f"and settings (from {newer}) are kept, the pictures from both go into one list with no "
                 f"picture twice, and the extra “Aether” is removed.")
        d.add_response("later", "Not now")
        d.add_response("combine", "Combine")
        d.set_response_appearance("combine", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("combine")
        d.set_close_response("later")
        d.connect("response", lambda _d, r: r == "combine" and self.do_action(
            data.merge_aether_action(twin[0]), on_done=lambda: self.load(), confirm=False, explained=True))
        d.present(self)

    def _ask_which_aether_theme(self):
        """Aether's working copy is there but OmaSkins isn't sure which theme it's a copy of (maybe a
        picture was copied between themes, or it's a new theme): ask, best match first; never guess."""
        candidates = data.aether_candidates()[:3]
        if not candidates:
            return
        d = Adw.AlertDialog(
            heading="Which theme is Aether's “Aether” a copy of?",
            body="Aether saved your latest changes as a theme called “Aether”, so Omarchy's theme menu "
                 "shows an extra theme. Pick the theme it belongs to and the two are combined (the newer "
                 "colours kept, every picture once). If it's a brand-new theme, choose Not now and save it "
                 "under its own name in Aether.")
        d.add_response("later", "Not now")
        for c in candidates:
            d.add_response(c["name"], data.display_name(c["name"]))
        d.set_response_appearance(candidates[0]["name"], Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("later")
        d.connect("response", lambda _d, r: r != "later" and self.do_action(
            data.merge_aether_action(r, picked=True), on_done=lambda: self.load(), confirm=False, explained=True))
        d.present(self)

    def _stars_fetched(self, stars):
        if isinstance(stars, dict) and stars and stars != self.stars:
            dlog("stars refreshed:", len(stars))
            self.stars = stars
            self.rebuild()

    def _font_popularity_fetched(self, pop):
        if isinstance(pop, dict) and pop and pop != self.font_pop:
            dlog("font popularity refreshed:", len(pop))
            self.font_pop = pop
            self._rebuild_fonts()

    def _on_sort(self, mode):
        """Top Picks / A -> Z: re-sort Themes and Fonts (Browse and Installed) and remember it."""
        if mode == self.sort_mode:
            return
        self.sort_mode = mode
        data.save_sort_mode(mode)
        dlog("sort ->", mode)
        self.rebuild()

    def _theme_order(self, e):
        """Top Picks: Omarchy's own (built-in) themes first, then most GitHub stars (A-Z among equals
        and themes without a count); A -> Z: alphabetical. Used for Browse and Installed alike."""
        if self.sort_mode == "az":
            return (0, 0, e.title.lower())
        if e.removed or (e.local and e.local.builtin):
            return (0, 0, e.title.lower())
        key = e.community.key if e.community else (e.local.repo_url and data.repo_key(e.local.repo_url))
        return (1, -self.stars.get(key or "", -1), e.title.lower())

    def _font_order(self, package):
        """Top Picks: most-used first (share of Arch users, pkgstats), A-Z among equals; A -> Z."""
        name = (package or "").lower()
        if self.sort_mode == "az":
            return (0, name)
        return (-self.font_pop.get(package or "", -1), name)

    def current_local(self):
        return next((t for t in self.local if t.name == self.current_theme), None)

    def rebuild(self):
        dlog("rebuild grids, current =", self.current_theme)
        matched = data.match_installed(self.community, self.local)
        by_local = {t.name: c for c in self.community if (t := matched.get(c.key))}
        browse = [ThemeEntry(c, matched.get(c.key)) for c in self.community]
        listed = {t.name for t in matched.values() if t}
        browse += [ThemeEntry(None, t) for t in self.local if t.builtin and t.name not in listed]  # built-ins too
        browse += [ThemeEntry(removed=n) for n in self.removed_builtins]  # restorable originals
        browse += [ThemeEntry(removed=t.name, hidden=t) for t in self.hidden_builtins]

        # Owner: one order for both sub-tabs, chosen with the Top Picks / A -> Z toggle.
        browse.sort(key=self._theme_order)
        installed = [ThemeEntry(by_local.get(t.name), t) for t in self.local]
        installed.sort(key=self._theme_order)
        for name, entries in (("Browse", browse), ("Installed", installed)):
            self.theme_counts[name].set_text(str(len(entries)))
        # Browse (~180 cards) is built when it's first shown; OmaSkins opens on Installed.
        self._theme_entries = {"Browse": browse, "Installed": installed}
        self._themes_dirty = {"Browse", "Installed"}
        self._build_theme_grid(self.theme_stack.get_visible_child_name() or "Installed")
        self._rebuild_bg_sidebar()
        self._rebuild_fonts()
        self.rotation.refresh()
        self._refresh_page()

    def _build_theme_grid(self, name):
        if name not in getattr(self, "_themes_dirty", ()):
            return
        self._themes_dirty.discard(name)
        dlog("build theme grid", name)
        fb = self.theme_flows[name]
        fb.remove_all()
        for e in self._theme_entries[name]:
            fb.append(ThemeCard(self, e, self.current_theme))

    def _rebuild_bg_sidebar(self):
        keep = self.bg_theme.name if self.bg_theme else self.current_theme
        self.bg_list.remove_all()
        ordered = sorted(self.local, key=lambda t: (t.name != self.current_theme, t.title.lower()))
        cur_bg = data.current_background()
        select = None
        for t in ordered:
            row = Gtk.ListBoxRow()
            row.theme = t
            box = Gtk.Box(spacing=8)
            box.append(label(t.title, hexpand=True, ellipsize=Pango.EllipsizeMode.END, width_chars=10))
            bgs = data.backgrounds_for(t, self.current_theme, cur_bg)
            box.append(label(str(len(bgs)), "dim small"))
            if t.name == self.current_theme:
                box.append(mark("Current theme"))
            elif any(b.current for b in bgs):
                box.append(mark("The current background comes from this theme"))
            row.set_child(box)
            self.bg_list.append(row)
            if t.name == keep:
                select = row
        if select:
            self.bg_list.select_row(select)

    def _on_bg_theme(self, _lb, row):
        if not row:
            return
        self.bg_theme = row.theme
        self.bg_title.set_text(f"{row.theme.title} backgrounds")
        self.bg_flow.remove_all()
        items = data.backgrounds_for(row.theme, self.current_theme, data.current_background())
        for b in items:
            self.bg_flow.append(BackgroundCard(self, b))
        if not items:
            self.bg_flow.append(Gtk.FlowBoxChild(child=label("No backgrounds for this theme yet.", "empty")))

    def _rebuild_fonts(self):
        """Built when the Fonts tab is (or gets) shown: matching every font's size costs real time, and most
        launches never open Fonts (2026-10-02: it froze the window for ~3 s at every launch)."""
        if self.main_stack.get_visible_child_name() != "Fonts":
            self._fonts_dirty = True
            return
        self._fonts_dirty = False
        if not self._previews_loaded:
            self._previews_loaded = True
            self._load_cached_previews()
        dlog("build fonts tab")
        cur_pkg = next((f.package for f in self.fonts if f.current), "")
        fonts = data.without_icon_twins(self.fonts)
        # Installed fonts grouped by the package that brought them (owner: removing one removes the
        # whole package, installing one brings them all, so show and count each package once).
        groups = {}
        for f in sorted(fonts, key=lambda f: f.family.lower()):
            groups.setdefault(f.package, []).append(f)
        pkg_info = {p.package: p for p in self.font_pkgs}

        inst, brow = self.font_lists["Installed"], self.font_lists["Browse"]
        inst.remove_all()
        brow.remove_all()
        # Installed: one group per package, in the Top Picks / A -> Z order.
        for package in sorted(groups, key=self._font_order):
            inst.append(FontGroupRow(self, package, groups[package], pkg_info.get(package), cur_pkg))
            for f in groups[package]:
                inst.append(FontRow(self, f, cur_pkg, grouped=True))
        # Browse: the downloadable (monospace) Nerd Font packages, one list in the same order; installed
        # ones are marked and previewed in their own face.
        learned = data.learned_not_mono() | set(self.not_mono)
        pkgs = sorted((p for p in self.font_pkgs if data.browsable_font_package(p.package, learned)),
                      key=lambda p: self._font_order(p.package))
        self.preview_rows = {}
        for p in pkgs:
            row = FontPackageRow(self, p, [f.family for f in groups.get(p.package, [])], cur_pkg)
            brow.append(row)
            if not p.installed and not data.preview_font(p.package):
                self.preview_rows[p.package] = row
        self._fetch_previews([p.package for p in pkgs if p.package in self.preview_rows])
        self.font_counts["Installed"].set_text(str(len(groups)))
        self.font_counts["Browse"].set_text(str(len(pkgs)))

    def _load_cached_previews(self):
        """Fonts downloaded for Browse in an earlier session: usable again at once (this app only)."""
        for meta in data.PREVIEW_FONTS.glob("*.json"):
            info = data.preview_font(meta.stem)
            if info:
                load_preview_font(info["file"])

    def _fetch_previews(self, packages):
        """Download the missing previews one at a time, in list order, in the background (each
        package once; only its regular font file is kept). Rows fill in as they arrive."""
        queue = [p for p in packages if p not in self._preview_queue]
        self._preview_queue.extend(queue)
        if self._preview_busy or not self._preview_queue:
            return
        self._preview_busy = True

        def next_one():
            if not self._preview_queue:
                self._preview_busy = False
                return
            package = self._preview_queue.pop(0)
            bg(lambda: data.fetch_preview_font(package), lambda res: arrived(package, res))

        def arrived(package, res):
            row = self.preview_rows.get(package)
            if isinstance(res, Exception) or not res:
                dlog("preview failed:", package, res)
                if row:
                    row.show_note("Preview couldn't be downloaded (tries again next time)")
            elif load_preview_font(res["file"]) and row:
                row.show_preview(res["family"])
            next_one()
        next_one()

    # ---- navigation
    def _on_main_tab(self, name):
        if name == "Backgrounds" and self._bg_follow_current:
            # A theme was just applied: you most likely want to try its own backgrounds next.
            self._bg_follow_current = False
            dlog("Backgrounds opens on the current theme:", self.current_theme)
            self._select_bg_theme(self.current_theme)
        self.main_stack.set_visible_child_name(name)
        self.tab_rows.set_visible_child_name(name)
        if name == "Fonts" and getattr(self, "_fonts_dirty", False):
            self._rebuild_fonts()
        self.search.set_sensitive(name != "Rotation")
        self.sort_box.set_sensitive(name in ("Themes", "Fonts"))  # greyed elsewhere, never moved
        self.search.set_placeholder_text({"Themes": "Search themes…", "Backgrounds": "Search backgrounds…",
                                          "Fonts": "Search fonts…", "Rotation": ""}[name])
        self._apply_search()

    def rotation_changed(self):
        """Background rotation on = the Backgrounds tab is greyed: rotation picks the background now."""
        on = self.rotation.plan.backgrounds
        tab = self.main_tabs["Backgrounds"]
        if on and tab.get_active():
            self.main_tabs["Rotation"].set_active(True)
        tab.set_sensitive(not on)
        self.bg_tab_wrap.set_tooltip_text(BGS_BLOCKED if on else None)

    def _on_theme_sub(self, name):
        self._build_theme_grid(name)
        self.theme_stack.set_visible_child_name(name)

    def _on_font_sub(self, name):
        self.font_stack.set_visible_child_name(name)

    def _theme_filter(self, child):
        return not self.search_text or self.search_text in child.entry.title.lower()

    def _on_search(self, entry):
        self.search_text = entry.get_text().strip().lower()
        self._apply_search()

    def _apply_search(self):
        for fb in self.theme_flows.values():
            fb.invalidate_filter()
        self.bg_flow.invalidate_filter()
        for lb in self.font_lists.values():
            lb.invalidate_filter()

    def show_theme(self, entry, start=None, animate=True):
        # A page still fading out after Back holds the "theme" name; clear it now or the new page won't add.
        old = self.stack.get_child_by_name("theme")
        if old:
            self.stack.remove(old)
        self.page = ThemePage(self, entry, start)
        self.page.state = self._page_state(entry)  # what it showed when built, to compare after reloads
        self.stack.add_named(self.page, "theme")
        if not animate:
            self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_visible_child_name("theme")
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

    @staticmethod
    def _entry_keys(e):
        """What identifies a theme across reloads: its omarchy.org repo and/or its folder name."""
        keys = set()
        if e.community:
            keys.add(("repo", e.community.key))
        name = e.local.name if e.local else e.removed
        if name:
            keys.add(("folder", name))
        return keys

    def _page_state(self, e):
        """Everything a theme page shows that a reload can change; equal = no need to redraw it."""
        local = e.local
        return (e.installed, e.removed, local.name if local else None, local.builtin if local else None,
                bool(local) and local.name == self.current_theme, bool(local) and data.theme_has_files(local.path),
                len(data.backgrounds_for(local)) if local else None, self.rotation.plan.themes,
                self.rotation.plan.backgrounds, self.rotation.plan.in_rotation(local.name) if local else None)

    def _refresh_page(self):
        """An open theme page follows the reloaded data (Add turns into Apply, Apply marks it current,
        ...), staying on the same flip-book picture. If the theme is gone from every list, go back."""
        if not self.page or self.stack.get_visible_child_name() != "theme":
            return
        keys = self._entry_keys(self.page.entry)
        for name in ("Browse", "Installed"):
            i = 0
            while (c := self.theme_flows[name].get_child_at_index(i)):
                if self._entry_keys(c.entry) & keys:
                    if self._page_state(c.entry) != self.page.state:
                        self.show_theme(c.entry, self.page.flipbook.current_name(), animate=False)
                    else:
                        self.page.entry = c.entry  # same look: keep the page as it is, no redraw
                    return
                i += 1
        self.go_back()

    def go_back(self):
        self.stack.set_visible_child_name("main")
        page, self.page = self.page, None
        if page:
            GLib.timeout_add(250, lambda: (page.get_parent() is self.stack and self.stack.remove(page), False)[1])

    def show_backgrounds_for(self, name):
        self.go_back()
        self.main_tabs["Backgrounds"].set_active(True)
        self._select_bg_theme(name)

    def _select_bg_theme(self, name):
        row = self.bg_list.get_first_child()
        while row:
            if getattr(row, "theme", None) and row.theme.name == name:
                self.bg_list.select_row(row)
                return True
            row = row.get_next_sibling()
        return False

    def show_export(self):
        ExportDialog(self).present(self)

    def show_import(self):
        bg(share.register_file_type)
        zips = Gtk.FileFilter(name="OmaSkins setups")
        zips.add_pattern(f"*{share.EXT}")
        zips.add_pattern("*.zip")   # saved before setups had their own file type
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(zips)
        Gtk.FileDialog(title="Import a shared setup", filters=filters).open(self, None, self._import_chosen)

    def _import_chosen(self, d, res):
        try:
            path = d.open_finish(res).get_path()
        except GLib.Error:
            return  # cancelled
        self.import_file(path)

    def import_file(self, path):
        """The import window for a setup file (from Import…, or double-clicked in a file manager)."""
        def shown(result):
            if isinstance(result, Exception):
                self.toasts.add_toast(Adw.Toast(title=f"Can't import {Path(path).name}: {result}", timeout=8))
            else:
                ImportDialog(self, path, result).present(self)
        bg(lambda: share.read_zip(path), shown)

    def progress_toast(self, title):
        """A toast with an ASCII bar under its title; (toast, progress(pct) callable from any thread)."""
        toast = Adw.Toast(title=title, timeout=0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(Gtk.Label(label=title, xalign=0))
        bar = Gtk.Label(label=data.ascii_bar(0), xalign=0)
        bar.add_css_class("mono")
        box.append(bar)
        toast.set_custom_title(box)
        self.toasts.add_toast(toast)
        return toast, lambda pct: GLib.idle_add(lambda: (bar.set_label(data.ascii_bar(pct)), False)[1])

    def _on_key(self, _ctl, keyval, _code, state):
        if self.page and self.stack.get_visible_child_name() == "theme" and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.page.flipbook.flip(-1 if keyval == Gdk.KEY_Left else 1)
            return True
        if keyval == Gdk.KEY_Escape:
            if self.stack.get_visible_child_name() == "theme":
                self.go_back()
            elif self.search.get_text():
                self.search.set_text("")
            else:
                self.close()
            return True
        if keyval == Gdk.KEY_f and state & Gdk.ModifierType.CONTROL_MASK:
            self.search.grab_focus()
            return True
        return False

    def save_to_pictures(self, bgd):
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or str(Path.home() / "Pictures")

        def done(result):
            if isinstance(result, Exception):
                self.toasts.add_toast(Adw.Toast(title=f"Couldn't save {bgd.path.name}: {result}", timeout=6))
                return
            dest, already = result
            text = f"Already in Pictures as {dest.name}" if already else f"Saved to Pictures as {dest.name}"
            self.toasts.add_toast(Adw.Toast(title=text, timeout=4))
        bg(lambda: data.save_to_pictures(bgd.path, pictures), done)

    # ---- a theme card's right-click action
    def quick_theme_action(self, entry):
        """The one thing a theme card's right-click menu offers: Add, Remove or Restore."""
        if entry.removed:
            return data.builtin_restore_action(entry.removed, self.builtin_pkg, bool(entry.hidden))
        acts = data.theme_actions(entry.local, entry.community, self.current_theme)
        return next((a for a in acts if a.label in ("Add", "Remove", "Hide")), None)

    def quick_theme(self, entry, a):
        """Right-click Add/Remove/Restore: no confirmation dialog, it's for speed (and each one can be
        undone from Browse). Removing a rotating theme also takes it out of the rotation."""
        done = None
        if a.label in ("Remove", "Hide") and self.rotation.plan.in_rotation(entry.local.name):
            def done(name=entry.local.name):
                self.rotation.drop(name)
                self.toasts.add_toast(Adw.Toast(title=f"{entry.title} was also taken out of the rotation.", timeout=4))
        self.do_action(a, done, confirm=False)

    def do_action(self, a, on_done=None, confirm=True, explained=False):
        if a.choices:  # a question first; each answer is its own action (already explained here)
            d = Adw.AlertDialog(heading=f"{a.label}?", body=a.note)
            d.add_response("cancel", "Cancel")
            for i, (text, choice) in enumerate(a.choices):
                d.add_response(str(i), text + (" (password)" if choice.password else ""))
            d.set_response_appearance(str(len(a.choices) - 1), Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_default_response("cancel")
            d.set_close_response("cancel")
            d.connect("response", lambda _d, r: r != "cancel" and self.do_action(
                a.choices[int(r)][1], on_done, confirm=False, explained=True))
            d.present(self)
            return
        def show():
            if a.steps:
                self._perform(a, on_done)
                return
            self.toasts.add_toast(Adw.Toast(title=f"Nothing changed. Would run: {a.command}", timeout=6))
            if on_done:
                on_done()
        if a.password and not explained:  # always asked, even from right-click: say why a password will come up
            d = Adw.AlertDialog(heading="This one needs your password",
                                body=f"{a.password}\n\n{a.note + chr(10) * 2 if a.note else ''}"
                                     f"This {'will' if a.steps else 'would'} run:\n\n{a.command}"
                                     + ("\n\nA terminal opens for your password; OmaSkins carries on once it's done."
                                        if any(st[0] == "terminal" for st in a.steps)
                                        else "" if a.steps else "\n\n(Nothing will actually change.)"))
            d.add_response("cancel", "Cancel")
            d.add_response("ok", a.label)
            d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if a.label in ("Remove", "Hide")
                                      else Adw.ResponseAppearance.SUGGESTED)
            d.set_default_response("cancel")
            d.set_close_response("cancel")
            d.connect("response", lambda _d, r: r == "ok" and show())
            d.present(self)
        elif a.label == "Remove" and confirm:
            note = f"{a.note}\n\n" if a.note else ""
            tail = "" if a.steps else "\n\n(Nothing will actually be removed.)"
            d = Adw.AlertDialog(heading="Remove?", body=f"{note}This {'will' if a.steps else 'would'} run:\n\n"
                                                         f"{a.command}{tail}")
            d.add_response("cancel", "Cancel")
            d.add_response("ok", "Remove")
            d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_default_response("cancel")
            d.set_close_response("cancel")
            d.connect("response", lambda _d, r: r == "ok" and show())
            d.present(self)
        else:
            show()

    def _perform(self, a, on_done=None):
        """Run a live action's steps off the UI thread; say what's happening, then how it went."""
        if a.command in self._running:
            return  # a double-click shouldn't run it twice
        self._running.add(a.command)
        busy = progress = None
        if a.busy:
            busy = Adw.Toast(title=a.busy, timeout=0)
            if a.bar >= 0:
                # Adding or removing a theme: an ASCII bar under the title, in the same toast, fixed width.
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                box.append(Gtk.Label(label=a.busy, xalign=0))
                bar = Gtk.Label(label=data.ascii_bar(a.bar), xalign=0)
                bar.add_css_class("mono")
                box.append(bar)
                busy.set_custom_title(box)
                progress = lambda pct: GLib.idle_add(lambda: (bar.set_label(data.ascii_bar(pct)), False)[1])
            self.toasts.add_toast(busy)

        def finished(result):
            self._running.discard(a.command)
            if busy:
                busy.dismiss()
            if isinstance(result, Exception):
                self.toasts.add_toast(Adw.Toast(title=f"{a.label} didn't work: {result}", timeout=8))
                return
            if result:  # a step's note replaces the usual message (e.g. a font that isn't monospace)
                t = Adw.Toast(title=result, timeout=0)
                pkg = next((st[1] for st in a.steps if st[0] == "check_mono"), None)
                if pkg:  # it's hidden everywhere now, so offer to take it straight back out
                    t.set_button_label("Remove")
                    t.connect("button-clicked", lambda *_: self.do_action(data._remove_font_package(pkg)))
                self.toasts.add_toast(t)
            elif a.done or a.label not in ("Open folder", "Transparency"):
                self.toasts.add_toast(Adw.Toast(title=a.done or f"{a.label}: done.", timeout=4))
            if on_done:
                on_done()
            applies_theme = any(st[0] == "run" and st[1][0] in ("omarchy-theme-set", "omarchy-theme-bg-set")
                                for st in a.steps)
            if applies_theme:
                self._pending.add("marks")  # only which theme/background is current changed: no list reload
            elif not any(st[0] == "apply_corners" for st in a.steps):  # corners change no list: restyle only
                self._pending.add("local")  # lists, counts and ✓ marks follow the change, in one refresh
            if any(st[0] in ("use_font", "apply_corners") or st[0] == "run" and st[1][0] == "omarchy-font-set"
                   for st in a.steps):
                self._pending.add("state")  # the window's own lettering follows the new font
        def finished_then_settle(result):
            finished(result)
            # Omarchy's last touches (background link, caches) land just after; refresh once they have.
            self._quiet_until = GLib.get_monotonic_time() + 1_500_000
            self._debounce("settle", 1600, self._settle)
        # A step can say "live now" mid-action (the new corner radius): restyle right then, so the
        # window's own frame changes together with Hyprland's border instead of seconds later.
        notify = lambda what: GLib.idle_add(lambda: (self.reload_theme(), False)[1])  # noqa: E731
        bg(lambda: run.perform(a.steps, progress, notify), finished_then_settle)

    def _settle(self):
        """The single refresh after OmaSkins' own action(s): recolour once, rebuild once."""
        if self._running:
            return  # another action is still going; its own settle will do it
        pending, self._pending = self._pending, set()
        dlog("settle, pending:", sorted(pending))
        if "rotation" in pending:
            self.rotation.sync_from_disk()
        if "corners" in pending or "local" in pending:
            self._sync_corners()
        if "state" in pending or "font" in pending:
            self.reload_theme()
        if pending - {"state", "marks"}:
            self.current_theme, self._last_bg = data.current_theme_name(), data.current_background()
            self.load()  # something beyond the current marks changed: re-read from disk
        elif pending:
            self._show_current_marks()  # a theme/background was applied: marks only, if not already done

    # ---- live theme
    def set_status(self, text):
        self.status.set_text(text)

    def reload_theme(self):
        t = theme.load_theme()
        self.font_base = t.font_px
        # OmaSkins draws its own frame (Omarchy's menu style) just inside Hyprland's border. Round it to
        # exactly Hyprland's window radius, or its square corner shows as a bright patch at the curve.
        r = data.current_rounding() or 0
        css = theme.build_css(t) + (f"\nwindow.omaskins, window.omaskins.csd, .frame-root {{ border-radius: {r}px; }}\n"
                                    if r else "")
        if t.bg != self._bg:
            self._bg = t.bg
            self._set_alpha(*self._alpha)
        if css == self._last_css:
            return
        self._last_css = css
        self.css.load_from_string(css)
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_LIGHT if t.mode == "light" else Adw.ColorScheme.FORCE_DARK)

    def _debounce(self, key, ms, fn):
        if key in self._timers:
            GLib.source_remove(self._timers[key])

        def fire():
            self._timers.pop(key, None)
            fn()
            return False
        self._timers[key] = GLib.timeout_add(ms, fire)

    def _theme_landed(self):
        """omarchy-theme-set just swapped a new theme in (it writes theme.name right then): recolour,
        and move the "Current" marks straight away, with the bar, instead of after the whole switch."""
        dlog("theme.name changed")
        self.reload_theme()
        self._show_current_marks()

    def _show_current_marks(self):
        """Re-read the current theme and background; redraw the cards only if either differs from
        what's on screen (no disk reload: only which theme/background is current changed)."""
        cur, bgnow = data.current_theme_name(), data.current_background()
        if (cur, bgnow) == (self.current_theme, self._last_bg):
            dlog("marks already current:", cur)
            return False
        theme_moved = cur != self.current_theme
        self.current_theme, self._last_bg = cur, bgnow
        if theme_moved:
            dlog("marks -> theme", cur)
            if self.main_stack.get_visible_child_name() != "Backgrounds":
                self._bg_follow_current = True  # don't pull the rug if you're looking at Backgrounds now
            self.rebuild()
        else:  # only the background moved (Omarchy sets it a moment after the theme): Backgrounds tab only
            dlog("marks -> background only")
            self._rebuild_bg_sidebar()
        return True

    def _state_changed(self):
        # Themes are swapped in several steps (rm + mv + hooks): settle, then re-read.
        def apply():
            dlog("state settled (Omarchy's picker)")
            self.reload_theme()
            self._show_current_marks()  # redraws only if the marks moved (theme.name may have done it)
        # omarchy-theme-set touches these files several times over a few seconds: wait until they settle.
        self._debounce("state", 1000, apply)

    def _watch(self, path, cb, only=None):
        """Watch a folder; `only` = react to that one file name in it."""
        try:
            mon = Gio.File.new_for_path(str(path)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        mon.connect("changed", lambda _m, f, *_: (only is None or f.get_basename() == only) and cb())
        self._monitors.append(mon)

    def _watch_files(self):
        # omarchy-theme-set writes theme.name right after the new theme is swapped in, the moment the
        # bar recolours: recolour this window then too (colours only; lists refresh once at the end),
        # instead of seconds later when the whole switch has finished. Works for OmaSkins' own Apply
        # and for Omarchy's picker alike.
        self._watch(data.STATE_DIR, lambda: self._debounce("recolor", 120, self._theme_landed), only="theme.name")
        self._watch(data.STATE_DIR, lambda: self._on_files_changed("state"))
        self._watch(data.USER_THEMES, lambda: self._on_files_changed("local"))
        self._watch(data.USER_BACKGROUNDS, lambda: self._on_files_changed("local"))
        # The font (Omarchy's menu, or OmaSkins) and the text size (the widget's slider writes
        # [font] base-size here, the same value the bar sizes itself from): the window follows live.
        self._watch(Path.home() / ".config/fontconfig", lambda: self._on_files_changed("font"))
        self._watch(Path.home() / ".config/omarchy", lambda: self._on_files_changed("font"), only="shell.toml")
        # OmaSkins' own settings, whoever writes them (an import, the rotation engine, a set switch):
        # every tab shows what's on disk now, never an old copy (owner, 2026-10-01).
        self._watch(data.ROTATION_FILE.parent, lambda: self._on_files_changed("rotation"))
        self._watch(data.HYPR_DIR, lambda: self._on_files_changed("corners"), only=data.CORNERS_FILE.name)
        if _DEBUG:
            GLib.timeout_add(5000, self._debug_widths)
        self._watch(data.TRANSPARENCY_FILE.parent, lambda: self._on_files_changed("corners"),
                    only=data.TRANSPARENCY_FILE.name)
        self._watch(data.OMASKINS_STATE, lambda: self._on_files_changed("local"), only=data.BUILTIN_STATE.name)
        self._watch(data.AETHER_BLUEPRINTS, lambda: self._on_files_changed("local"))
        # Fonts and built-in themes come and go with packages (pacman's list of what's installed).
        self._watch(Path("/var/lib/pacman/local"), lambda: self._on_files_changed("local"))
        self._watch(share.IMPORTED_FONTS, lambda: self._on_files_changed("local"))

    def _on_files_changed(self, kind):
        """Omarchy's files changed. While OmaSkins' own action is still running (and briefly after),
        just note it: one refresh at the end beats redrawing the window at every step of a theme
        switch, which made the UI flash and jump."""
        if self._running or GLib.get_monotonic_time() < self._quiet_until:
            dlog("noted during own action:", kind)
            self._pending.add(kind)
        elif kind == "state":
            dlog("state files changed")
            self._state_changed()
        elif kind == "font":
            self._debounce("font", 300, self._font_changed)  # a dragged slider writes many times
        elif kind == "rotation":
            self._debounce("rotation", 300, self.rotation.sync_from_disk)
        elif kind == "corners":
            self._debounce("corners-file", 300, self._sync_corners)
        else:
            self._debounce("local", 600, self.load)

    def _font_changed(self):
        """Restyle with the new font / text size, then redraw the lists (their previews are sized from it)."""
        old = (self.font_base, self._last_css)
        self.reload_theme()
        if (self.font_base, self._last_css) != old:
            self.load()


class App(Adw.Application):
    def __init__(self):
        # HANDLES_OPEN: a double-clicked .omaskins file arrives here (in the OmaSkins already open, if any).
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_open(self, files, _n, _hint):
        self.do_activate()
        win = self.props.active_window
        for f in files[:1]:   # one setup at a time
            if f.get_path():
                GLib.idle_add(lambda p=f.get_path(): (win.import_file(p), False)[1])

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()
        bg(share.register_file_type)   # keeps the file type and double-click opening set up (yours only)
        if _DEBUG:  # which renderer GTK really uses (the VM launcher asks for software, reports 04c/07)
            GLib.timeout_add(800, lambda: (dlog("renderer:", type(win.get_renderer()).__name__,
                                                "GSK_RENDERER=" + os.environ.get("GSK_RENDERER", ""),
                                                "zink=" + os.environ.get("MESA_LOADER_DRIVER_OVERRIDE", "")), False)[1])


def main():
    return App().run(sys.argv)
