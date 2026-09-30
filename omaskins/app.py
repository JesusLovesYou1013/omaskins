"""OmaSkins Manager — browse and manage Omarchy themes, backgrounds and fonts.

PROTOTYPE: every button is real-looking but only shows the Omarchy command it
would run. Nothing on the system is changed (see data.py).
"""

import itertools
import os
import queue
import sys
import threading
from pathlib import Path
from urllib.parse import unquote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import data, run, theme  # noqa: E402

APP_ID = "io.github.jesuslovesyou1013.omaskins"
TITLE = "OmaSkins Manager"
CHECK = "\U000f012c"  # nf-md-check — a plain mark, never a checkbox (same as OmaPlugs)
CARD_W, CARD_H = 272, 153  # 16:9, the shape of omarchy.org screenshots
PROTOTYPE_NOTE = ("PARTLY LIVE: themes (built-in ones too), backgrounds, fonts and rounded corners really change. "
                  "Rotation and Share still only show what they would do.")


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


def picture(w, h, src=None, width=480, priority=1):
    """A w×h image, never bigger: the clamps stop the loaded texture's own size from widening the
    card (which would collapse the grid to a single column) or, for a 4:3 or taller picture,
    making it taller than its neighbours."""
    pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
    pic.set_size_request(w, h)
    pic.add_css_class("thumb")
    if src:
        IMAGES.get(src, width, lambda t: t and pic.set_paintable(t), priority)
    wide = Adw.Clamp(child=pic, maximum_size=w, tightening_threshold=w, halign=Gtk.Align.START)
    return Adw.Clamp(child=wide, orientation=Gtk.Orientation.VERTICAL, maximum_size=h, tightening_threshold=h,
                     valign=Gtk.Align.START)


# --------------------------------------------------------------------------- font metrics

_XRATIO = {}


def x_height_ratio(widget, family):
    """Height of a lowercase 'x' as a fraction of the font size, measured with Pango."""
    if family not in _XRATIO:
        layout = widget.create_pango_layout("x")
        desc = Pango.FontDescription.from_string(family)
        desc.set_absolute_size(100 * Pango.SCALE)
        layout.set_font_description(desc)
        ink, _log = layout.get_pixel_extents()
        _XRATIO[family] = ink.height / 100 if ink.height > 0 else 0
    return _XRATIO[family]


def font_label(widget, family, reference, base_px):
    """`family` drawn in itself, sized so its x-height matches the reference font."""
    size = data.normalized_size(base_px, x_height_ratio(widget, family), x_height_ratio(widget, reference))
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


def flow():
    fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, valign=Gtk.Align.START,
                     column_spacing=6, row_spacing=6, max_children_per_line=12, min_children_per_line=1,
                     margin_start=12, margin_end=12, margin_top=6, margin_bottom=12)
    fb.set_activate_on_single_click(True)
    return fb


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
        box.append(picture(CARD_W, CARD_H, entry.image, priority=0 if entry.installed else 1))
        row = Gtk.Box(spacing=6)
        row.add_css_class("card-row")  # one height for every card's title row (Apply button or not)
        row.append(label(entry.title, "card-name", hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        if entry.removed:
            row.append(badge("Built-in", "builtin"))
            row.append(badge("Hidden" if entry.hidden else "Removed", "warn"))
        elif entry.local and entry.local.builtin:
            row.append(badge("Built-in", "builtin"))
        elif entry.installed and entry.community is None:
            row.append(badge("Not listed", "warn"))
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
        box.append(picture(CARD_W, CARD_H, bgd.path, priority=0))
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
    """Installed font: its name in its own face, x-height-matched to the current font."""

    def __init__(self, win, font, current_package, grouped=False):
        super().__init__(activatable=False)
        self.key = f"{font.family} {font.package}".lower()
        box = Gtk.Box(spacing=12)
        box.add_css_class("font-row")
        if grouped:
            box.add_css_class("font-in-group")  # indented under its package's header
        box.append(mark("Current font") if font.current else Gtk.Box(width_request=24))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        name, scale = font_label(win, font.family, win.current_font or font.family, win.font_base * 1.6)
        col.append(name)
        bits = [font.package or "not from a package"]
        if abs(scale - 1) >= 0.02:
            bits.append(f"shown at {scale * 100:.0f}% so its letters match {win.current_font}")
        col.append(label(" · ".join(bits), "dim small"))
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
        title.append(label(package or "Not from a package", "plugin-name"))
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
        title.append(label(pkg.package, "plugin-name"))
        if pkg.omarchy_pick:
            title.append(badge("Omarchy pick", "verified"))
        if pkg.installed:
            title.append(badge("Installed", "enabled"))
        col.append(title)
        if families:
            name, _ = font_label(win, families[0], win.current_font or families[0], win.font_base * 1.6)
            col.append(name)
        else:
            col.append(label("Preview: the font would be downloaded to a cache and shown here "
                             "(not in this prototype)", "dim small", wrap=True))
        col.append(label(f"{pkg.description} · {pkg.version}", "dim small", wrap=True))
        box.append(col)
        for a in data.font_actions(package=pkg, current_package=current_package):
            box.append(win.action_button(a))
        self.set_child(box)


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
        self.stack.add_named(picture(self.MAIN_W, self.MAIN_H, self.items[i][0], width=self.FULL), self.slot)
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


# --------------------------------------------------------------------------- export dialog

class ExportDialog(Adw.Dialog):
    """The whole setup (top-right Share…). A single theme needs no zip: it's on omarchy.org by name."""

    def __init__(self, win):
        super().__init__(title="Share your setup", content_width=620)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=18, margin_end=18,
                      margin_top=18, margin_bottom=18)
        t = win.current_local()
        cur_bg = None
        if t:
            cur_bg = next((b for b in data.backgrounds_for(t, win.current_theme, data.current_background())
                           if b.current), None)
        font = next((f for f in win.fonts if f.current), None)
        items = data.export_plan(t, win.community, cur_bg, font) if t else []

        box.append(label("The zip would hold:" if items else "Nothing to share.", "section-title"))
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lst.add_css_class("export-list")
        for it in items:
            row = Gtk.Box(spacing=10)
            row.append(badge("Link" if it.how == "link" else "In zip", "verified" if it.how == "link" else "warn"))
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            col.append(label(f"{it.kind}: {it.name}", "card-name"))
            col.append(label(it.detail, "dim small", wrap=True, selectable=True))
            row.append(col)
            lst.append(row)
        box.append(lst)
        box.append(label("Links point at the same place Omarchy's own installer uses, so the zip stays small. "
                         "Anything not on omarchy.org or in the Arch repos is copied in, so it works even if "
                         "the other person has never seen it.", "dim small", wrap=True))
        exp = Gtk.Expander(label="Manifest (omaskins.json)")
        exp.set_child(label(data.export_summary_json(items), "mono small", selectable=True))
        box.append(exp)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        btns.append(button("Import a shared zip…", "", lambda *_: win.do_action(
            data.Action("Import", "unzip <file> → install each link, copy bundled files into ~/.config/omarchy"))))
        btns.append(button("Save zip…", "primary", lambda *_: win.do_action(
            data.Action("Save zip", "write omaskins.json + bundled files to <chosen>.zip"))))
        box.append(btns)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        self.set_child(view)


# --------------------------------------------------------------------------- rotation (prototype: in memory only)
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
                      "and the bright ones take turns from any theme you like."),
    (True, True): "Click a background to add or remove it. While this theme is on, its bright ones take turns.",
}


def minutes_spin(value, on_change):
    return number_spin(value, 1, 1440, on_change)


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
    box.append(picture(CARD_W, CARD_H, bgd.path, priority=0))
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
    """The Rotation tab. PROTOTYPE: settings live only while the window is open; nothing runs."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.plan = data.RotationPlan(win.current_theme)
        self.selected = None          # name of the theme shown on the right
        self.rows = {}                # theme name -> ListBoxRow
        self._syncing = False
        p = self.plan

        self.top = top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top.add_css_class("toolbar")
        # Each switch has its timer right beside it; the wide gap between the pairs says which is which.
        row1 = field("Rotate")
        self.themes_switch, self.theme_timer, pair = self._switch_pair(
            "Themes", "themes", "theme_minutes", "Rotate through the checked themes")
        row1.append(pair)
        self.bgs_switch, self.bg_timer, pair = self._switch_pair(
            "Backgrounds", "backgrounds", "bg_minutes", "Rotate through the bright backgrounds")
        pair.set_margin_start(48)
        row1.append(pair)
        top.append(row1)
        self.row2 = Gtk.Box(spacing=12)
        dd, self.dd_switch = labelled_switch("", p.dawn_dusk, self._on_dawn_dusk, "Separate theme sets for day and night")
        self.row2.append(field("Dawn & Dusk", dd))
        self.times = Gtk.Box(spacing=8)
        for name, attr in (("Dawn starts", "dawn"), ("Dusk starts", "dusk")):
            self.times.append(label(name, "dim", valign=Gtk.Align.CENTER, margin_start=12))
            e = Gtk.Entry(text=getattr(p, attr), width_chars=5, max_width_chars=5, valign=Gtk.Align.CENTER)
            e.connect("changed", lambda w, a=attr: setattr(p, a, w.get_text()))
            self.times.append(e)
        self.row2.append(self.times)
        top.append(self.row2)
        self.append(top)

        self.period_bar, _c, self.period_btns = sub_tabs(("Dawn", "Dusk"), self._on_period)
        self.append(self.period_bar)

        pane = Gtk.Box(vexpand=True)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("sidebar")
        self.list.connect("row-selected", self._on_row)
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, overlay_scrolling=True,
                                width_request=280)
        sw.add_css_class("sidebar")
        sw.set_child(self.list)
        pane.append(sw)

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

    def _switch_pair(self, text, attr, minutes, tip):
        p = self.plan
        pair = Gtk.Box(spacing=14)
        box, sw = labelled_switch(text, getattr(p, attr), lambda on: self._on_switch(attr, on), tip)
        pair.append(box)
        timer = Gtk.Box(spacing=8)
        timer.append(label("every", "dim", valign=Gtk.Align.CENTER))
        timer.append(minutes_spin(getattr(p, minutes), lambda v: setattr(p, minutes, v)))
        timer.append(label("min", "dim", valign=Gtk.Align.CENTER))
        pair.append(timer)
        return sw, timer, pair

    # ---- data
    def _theme(self, name):
        return next((t for t in self.win.local if t.name == name), None)

    def _backgrounds(self, name):
        t = self._theme(name)
        return data.backgrounds_for(t) if t else []

    def refresh(self):
        """Called when themes are (re)loaded."""
        p = self.plan
        p.forget_missing({t.name for t in self.win.local})
        p.current_theme = self.win.current_theme
        p.seed_solo(self._backgrounds(p.current_theme))
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
            col.append(label(t.title, ellipsize=Pango.EllipsizeMode.END))
            row.summary = label("", "dim small", ellipsize=Pango.EllipsizeMode.END)
            col.append(row.summary)
            box.append(col)
            # One fixed-size slot on the right: a checkbox (Themes on) or the current mark (Themes off).
            row.slot = Gtk.Stack(hhomogeneous=True, vhomogeneous=True, valign=Gtk.Align.CENTER)
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
        (row.remove_css_class if on else row.add_css_class)("unchecked")
        row.set_selectable(on)
        row.set_activatable(on)
        bgs = self._backgrounds(row.theme_name)
        if p.themes and not on:
            text = "Not in the rotation"
        elif not bgs:
            text = "No backgrounds"
        else:
            text = f"{p.picked_count(row.theme_name, bgs)} of {len(bgs)} backgrounds"
        row.summary.set_text(text)

    def _select(self, name=None):
        """Keep `name` selected if it can still be opened, else the first theme that can."""
        rows = [r for r in self.rows.values() if self.plan.can_open(r.theme_name)]
        row = self.rows.get(name) if name and self.plan.can_open(name) else (rows[0] if rows else None)
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
        self._restyle()

    # ---- handlers
    def _on_switch(self, which, on):
        was = self.plan.running()
        setattr(self.plan, which, on)
        self._sync_strips()
        self.win.rotation_changed()
        for row in self.rows.values():
            self._style_row(row)
        self._select(self.selected)
        if self.plan.running() != was:
            self.win.toasts.add_toast(Adw.Toast(
                title=f"Prototype, nothing changed. Rotation would {'start' if self.plan.running() else 'stop'}.",
                timeout=4))

    def _sync_strips(self):
        p = self.plan
        self.bg_timer.set_sensitive(p.backgrounds)
        self.theme_timer.set_sensitive(p.themes)
        self.row2.set_sensitive(p.themes)
        self.times.set_sensitive(p.dawn_dusk)
        self.period_bar.set_sensitive(p.themes and p.dawn_dusk)

    def _on_dawn_dusk(self, on):
        self.plan.set_dawn_dusk(on)
        self._sync_strips()
        self._restyle()

    def _on_period(self, name):
        self.plan.period = name
        self._restyle()

    def _restyle(self):
        for row in self.rows.values():
            self._style_row(row)
        self._select(self.selected)

    def _on_check(self, name, on):
        if self._syncing:
            return
        self.plan.set_checked(name, on, self._backgrounds(name))
        self._style_row(self.rows[name])
        self._select(name if on else self.selected)

    def _on_row(self, _lb, row):
        self.selected = row.theme_name if row else None
        self._show_detail()

    def _toggle(self, child):
        p, name = self.plan, self.selected
        if not name or not p.backgrounds:
            return
        on = p.toggle(name, child.bgd, self._backgrounds(name))
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
        self.hint.set_text(ROT_HINTS[(p.themes, p.backgrounds)])
        self.hint.set_size_request(-1, self.hint.create_pango_layout("x\nx").get_pixel_size()[1])
        self.grid.set_sensitive(p.backgrounds)
        self.grid.remove_all()
        t = self._theme(name) if name else None
        self.title.set_text(t.title if t else "")
        self._update_count()
        if not t:
            self.grid.append(Gtk.FlowBoxChild(child=label("Check a theme on the left to add it to the rotation.",
                                                          "empty")))
            return
        bgs = self._backgrounds(name)
        for b in bgs:
            self.grid.append(rot_card(b, p.is_picked(name, b, bgs)))
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
        self._last_bg, self._theme_changed_at = data.current_background(), 0
        # Set when a theme gets applied while you're elsewhere: the next visit to Backgrounds opens on
        # the (new) current theme; otherwise Backgrounds keeps the theme you last looked at.
        self._bg_follow_current = False
        self._own_change_until, self._running = 0, set()
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
        self.reload_theme()

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(label(TITLE, "window-title"))

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
        bar.append(Gtk.Box(hexpand=True))
        # Owner: how Themes and Fonts lists sort (Browse and Installed), kept between launches.
        # Greyed, never hidden, on the tabs it doesn't apply to (no-layout-shift rule).
        self.sort_mode = data.sort_mode()
        self.sort_box = Gtk.Box(valign=Gtk.Align.CENTER, margin_end=8)
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
        share = button("Share…", "", lambda *_: self.show_export(), tooltip="Export your current setup as a zip")
        share.set_valign(Gtk.Align.CENTER)
        share.set_margin_start(8)
        bar.append(share)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Re-read everything",
                             valign=Gtk.Align.CENTER, margin_start=4)
        refresh.add_css_class("icon-btn")
        refresh.connect("clicked", lambda *_: self.load(force=True))
        bar.append(refresh)
        main.append(bar)

        self.main_stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                                    transition_duration=100)
        self.main_stack.add_named(self._build_themes(), "Themes")
        self.main_stack.add_named(self._build_backgrounds(), "Backgrounds")
        self.main_stack.add_named(self._build_fonts(), "Fonts")
        self.rotation = RotationPage(self)
        self.main_stack.add_named(self.rotation, "Rotation")
        main.append(self.main_stack)

        self.status = label("Loading…", "statusbar")
        main.append(self.status)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.stack.add_named(main, "main")

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("frame-root")
        frame.set_overflow(Gtk.Overflow.HIDDEN)  # the banner is clipped to the frame's rounded corners
        frame.append(label(PROTOTYPE_NOTE, "prototype-banner", wrap=True))
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
        box.append(bar)
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
        side = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=230,
                                  overlay_scrolling=True)
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
        right.append(tools)
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
        box.append(label("Global Rounded Corners", valign=Gtk.Align.CENTER))
        sw = Gtk.Switch(active=on, valign=Gtk.Align.CENTER)
        box.append(sw)
        # Greyed, never hidden, while off (no-layout-shift rule).
        self.corner_size = Gtk.Box(spacing=8, sensitive=on)
        self.corner_size.append(number_spin(self.corners["px"], 0, data.CORNERS_MAX,
                                            lambda v: self._on_corners(px=v)))
        self.corner_size.append(label("px", "dim", valign=Gtk.Align.CENTER))
        box.append(self.corner_size)
        sw.connect("notify::active", lambda w, _p: self._on_corners(on=w.get_active()))
        return box

    def _on_corners(self, on=None, px=None):
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
        box.append(bar)
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
            fb = self.theme_flows[name]
            fb.remove_all()
            for e in entries:
                fb.append(ThemeCard(self, e, self.current_theme))
            self.theme_counts[name].set_text(str(len(entries)))
        self._rebuild_bg_sidebar()
        self._rebuild_fonts()
        self.rotation.refresh()
        self._refresh_page()

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
            box.append(label(t.title, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
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
        for p in pkgs:
            brow.append(FontPackageRow(self, p, [f.family for f in groups.get(p.package, [])], cur_pkg))
        self.font_counts["Installed"].set_text(str(len(groups)))
        self.font_counts["Browse"].set_text(str(len(pkgs)))

    # ---- navigation
    def _on_main_tab(self, name):
        if name == "Backgrounds" and self._bg_follow_current:
            # A theme was just applied: you most likely want to try its own backgrounds next.
            self._bg_follow_current = False
            dlog("Backgrounds opens on the current theme:", self.current_theme)
            self._select_bg_theme(self.current_theme)
        self.main_stack.set_visible_child_name(name)
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

    # ---- the prototype's one "action"
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
            self.toasts.add_toast(Adw.Toast(title=f"Prototype, nothing changed. Would run: {a.command}", timeout=6))
            if on_done:
                on_done()
        if a.password and not explained:  # always asked, even from right-click: say why a password will come up
            d = Adw.AlertDialog(heading="This one needs your password",
                                body=f"{a.password}\n\n{a.note + chr(10) * 2 if a.note else ''}"
                                     f"This {'will' if a.steps else 'would'} run:\n\n{a.command}"
                                     + ("\n\nA terminal opens for your password; OmaSkins carries on once it's done."
                                        if any(st[0] == "terminal" for st in a.steps)
                                        else "" if a.steps else "\n\n(Prototype: nothing will actually change.)"))
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
            tail = "" if a.steps else "\n\n(Prototype: nothing will actually be removed.)"
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
        if any(st[0] == "run" and st[1][0] in ("omarchy-theme-set", "omarchy-theme-bg-set") for st in a.steps):
            # OmaSkins' own change: not a manual pick, so it mustn't reset the rotation timers.
            self._own_change_until = GLib.get_monotonic_time() + 15_000_000

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
            elif a.done or a.label not in ("Open folder",):
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
            old_theme, old_bg = self.current_theme, self._last_bg
            self._show_current_marks()  # redraws only if the marks moved (theme.name may have done it)
            self._manual_change(self.current_theme != old_theme, self._last_bg != old_bg)
        # omarchy-theme-set touches these files several times over a few seconds: wait until they settle.
        self._debounce("state", 1000, apply)

    def _manual_change(self, theme_changed, bg_changed):
        """Omarchy's own picker was used (in the prototype nothing else changes these): the matching
        rotation timers start over. A theme change's own new background, landing a moment after
        the theme, belongs to that change and doesn't get a second message."""
        now = GLib.get_monotonic_time()
        if now < self._own_change_until:
            return  # OmaSkins made this change itself
        if theme_changed:
            self._theme_changed_at = now
        elif bg_changed and now - self._theme_changed_at < 5_000_000:
            return
        _timers, msg = self.rotation.plan.manual_change(theme_changed, bg_changed)
        if msg:
            self.toasts.add_toast(Adw.Toast(title=f"Prototype: {msg}", timeout=6))

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
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()
        if _DEBUG:  # which renderer GTK really uses (the VM launcher asks for software, reports 04c/07)
            GLib.timeout_add(800, lambda: (dlog("renderer:", type(win.get_renderer()).__name__,
                                                "GSK_RENDERER=" + os.environ.get("GSK_RENDERER", ""),
                                                "zink=" + os.environ.get("MESA_LOADER_DRIVER_OVERRIDE", "")), False)[1])


def main():
    return App().run(sys.argv)
