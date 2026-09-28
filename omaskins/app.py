"""OmaSkins Manager — browse and manage Omarchy themes, backgrounds and fonts.

PROTOTYPE: every button is real-looking but only shows the Omarchy command it
would run. Nothing on the system is changed (see data.py).
"""

import itertools
import queue
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import data, theme  # noqa: E402

APP_ID = "io.github.jesuslovesyou1013.omaskins"
TITLE = "OmaSkins Manager"
CHECK = "\U000f012c"  # nf-md-check — a plain mark, never a checkbox (same as OmaPlugs)
CARD_W, CARD_H = 272, 153  # 16:9, the shape of omarchy.org screenshots
PROTOTYPE_NOTE = "PROTOTYPE: buttons only show what they would do. Nothing on your system is changed."


# --------------------------------------------------------------------------- small helpers

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
            src = data.cached_download(src, data.url_cache_name(src, "shots"))
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
    """A w×h image. The clamp stops the loaded texture's own size from widening the
    card, which would otherwise collapse the grid to a single column."""
    pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
    pic.set_size_request(w, h)
    pic.add_css_class("thumb")
    if src:
        IMAGES.get(src, width, lambda t: t and pic.set_paintable(t), priority)
    return Adw.Clamp(child=pic, maximum_size=w, tightening_threshold=w, halign=Gtk.Align.START)


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

    def __init__(self, community=None, local=None):
        self.community, self.local = community, local

    @property
    def title(self):
        return self.local.title if self.local else self.community.name

    @property
    def installed(self):
        return self.local is not None

    @property
    def image(self):
        if self.local and self.local.preview:
            return self.local.preview
        return self.community.screenshot_url if self.community else None

    @property
    def repo_url(self):
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
    def __init__(self, entry, current):
        super().__init__()
        self.entry = entry
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER, width_request=CARD_W)
        box.add_css_class("tile")
        is_current = entry.local is not None and entry.local.name == current
        if is_current:
            box.add_css_class("current")
        box.append(picture(CARD_W, CARD_H, entry.image, priority=0 if entry.installed else 1))
        row = Gtk.Box(spacing=6)
        row.append(label(entry.title, "card-name", hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        if entry.local and entry.local.builtin:
            row.append(badge("Built-in", "builtin"))
        elif entry.installed and entry.community is None:
            row.append(badge("Not listed", "warn"))
        if is_current:
            row.append(badge("Current", "verified"))
        if entry.installed:
            row.append(mark())
        box.append(row)
        self.set_child(box)
        self.set_tooltip_text(entry.title)


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
            acts.append(button(a.label, "danger" if a.label == "Remove" else "",
                               lambda _b, a=a: win.do_action(a)))
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
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.menu.set_pointing_to(rect)
        self.menu.popup()


class FontRow(Gtk.ListBoxRow):
    """Installed font: its name in its own face, x-height-matched to the current font."""

    def __init__(self, win, font, current_package):
        super().__init__(activatable=False)
        self.key = font.family.lower()
        box = Gtk.Box(spacing=12)
        box.add_css_class("font-row")
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


# --------------------------------------------------------------------------- theme page

class ThemePage(Gtk.Box):
    def __init__(self, win, entry):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.entry = win, entry
        head = Gtk.Box(spacing=8, margin_start=18, margin_end=18, margin_top=12, margin_bottom=8)
        back = button("‹ Back", "flat", lambda *_: win.go_back(), tooltip="Back (Esc)")
        head.append(back)
        head.append(label(entry.title, "plugin-name", hexpand=True))
        self.append(head)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=18, margin_end=18,
                       margin_bottom=18)
        big = picture(720, 405, entry.image, width=1280, priority=-1)
        body.append(big)

        # actions
        acts = Gtk.Box(spacing=8)
        for a in data.theme_actions(entry.local, entry.community, win.current_theme):
            acts.append(win.action_button(a))
        if entry.local:
            acts.append(button("Backgrounds", "", lambda *_: win.show_backgrounds_for(entry.local.name)))
        acts.append(button("Share…", "", lambda *_: win.show_export(entry)))
        if entry.repo_url:
            acts.append(button("Open on GitHub", "flat", lambda *_: open_url(entry.repo_url)))
        body.append(acts)

        # palette
        body.append(label("Colors", "section-title"))
        self.swatches = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                                    min_children_per_line=4, column_spacing=6, row_spacing=6)
        body.append(self.swatches)
        if entry.local and entry.local.colors:
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
    def __init__(self, win, entry=None):
        super().__init__(title="Share your setup", content_width=620)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=18, margin_end=18,
                      margin_top=18, margin_bottom=18)
        t = entry.local if entry else win.current_local()
        community = win.community
        cur_bg = None
        if t and not entry:
            cur_bg = next((b for b in data.backgrounds_for(t, win.current_theme, data.current_background())
                           if b.current), None)
        font = None if entry else next((f for f in win.fonts if f.current), None)
        items = data.export_plan(t, community, cur_bg, font) if t else []
        if entry and not t and entry.community:  # sharing a theme you haven't installed: just its link
            items = [data.ExportItem("Theme", entry.title, "link", f"{entry.community.repo_url}.git")]

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

ROT_HINTS = {
    (False, False): "Rotation is off. Turn on Themes or Backgrounds above.",
    (True, False): "Backgrounds aren't rotating: your current background stays when the theme changes.",
    (False, True): "Click a background to add or remove it. Picks can come from any theme.",
    (True, True): "Click a background to add or remove it. This theme shows its bright ones.",
}


def minutes_spin(value, on_change):
    s = Gtk.SpinButton.new_with_range(1, 1440, 1)
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
        row1 = Gtk.Box(spacing=24)
        themes_sw, self.themes_switch = labelled_switch("Themes", p.themes, lambda on: self._on_switch("themes", on),
                                       "Rotate through the checked themes")
        bgs_sw, self.bgs_switch = labelled_switch("Backgrounds", p.backgrounds, lambda on: self._on_switch("backgrounds", on),
                                    "Rotate through the bright backgrounds")
        rotate = field("Rotate", themes_sw)
        rotate.append(bgs_sw)
        rotate.set_spacing(18)
        row1.append(rotate)
        self.bg_timer = Gtk.Box(spacing=8)
        self.bg_timer.append(label("Next background every", "kv-key", valign=Gtk.Align.CENTER))
        self.bg_timer.append(minutes_spin(p.bg_minutes, lambda v: setattr(p, "bg_minutes", v)))
        self.bg_timer.append(label("min", "dim", valign=Gtk.Align.CENTER))
        row1.append(self.bg_timer)
        self.theme_timer = Gtk.Box(spacing=8)
        self.theme_timer.append(label("Next theme every", "kv-key", valign=Gtk.Align.CENTER))
        self.theme_timer.append(minutes_spin(p.theme_minutes, lambda v: setattr(p, "theme_minutes", v)))
        self.theme_timer.append(label("min", "dim", valign=Gtk.Align.CENTER))
        row1.append(self.theme_timer)
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
        self.hint = label("", "dim small", ellipsize=Pango.EllipsizeMode.END)
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

    # ---- handlers
    def _on_switch(self, which, on):
        was = self.plan.running()
        setattr(self.plan, which, on)
        self._sync_strips()
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
        self.current_theme = data.current_theme_name()
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
            bar.append(btn)
            self.main_tabs[name] = btn
        bar.append(Gtk.Box(hexpand=True))
        self.search = Gtk.SearchEntry(placeholder_text="Search themes…", valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", self._on_search)
        bar.append(self.search)
        share = button("Share…", "", lambda *_: self.show_export(None), tooltip="Export your current setup as a zip")
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
        tools.append(button("Add backgrounds…", "", lambda *_: self.bg_theme and self.do_action(
            data.add_background_action(self.bg_theme.name))))
        tools.append(button("Open folder", "flat", lambda *_: self.bg_theme and self.do_action(
            data.Action("Open folder", f"nautilus {data.q(data.USER_BACKGROUNDS / self.bg_theme.name)}"))))
        right.append(tools)
        self.bg_flow = flow()
        self.bg_flow.set_filter_func(lambda c: not self.search_text or self.search_text in c.bgd.path.name.lower())
        right.append(scrolled(self.bg_flow))
        pane.append(right)
        return pane

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

    def action_button(self, a):
        css = "danger" if a.label == "Remove" else "primary" if a.label in ("Add", "Apply", "Use") else ""
        b = button(a.label, css, lambda *_: self.do_action(a))
        b.set_valign(Gtk.Align.CENTER)
        return b

    # ---- data
    def load(self, force=False):
        self.set_status("Loading…")

        def work():
            try:
                community = data.community_themes(force)
                err = None
            except Exception as e:  # noqa: BLE001
                community, err = [], e
            return (community, err, data.local_themes(), data.installed_fonts(), data.repo_fonts(),
                    data.current_font())
        bg(work, self._on_loaded)

    def _on_loaded(self, res):
        if isinstance(res, Exception):
            self.set_status(f"Couldn't load: {res}")
            return
        self.community, err, self.local, self.fonts, self.font_pkgs, self.current_font = res
        self.current_theme = data.current_theme_name()
        self.rebuild()
        n_local = len(self.local)
        msg = f"{len(self.community)} community themes · {n_local} installed · {len(self.fonts)} fonts"
        if err:
            msg = f"omarchy.org unreachable ({err}); showing installed only · " + msg
        self.set_status(msg)

    def current_local(self):
        return next((t for t in self.local if t.name == self.current_theme), None)

    def rebuild(self):
        matched = data.match_installed(self.community, self.local)
        by_local = {t.name: c for c in self.community if (t := matched.get(c.key))}
        browse = [ThemeEntry(c, matched.get(c.key)) for c in sorted(self.community, key=lambda c: c.name.lower())]
        installed = [ThemeEntry(by_local.get(t.name), t) for t in self.local]
        installed.sort(key=lambda e: (e.local.name != self.current_theme, e.local.builtin, e.title.lower()))
        for name, entries in (("Browse", browse), ("Installed", installed)):
            fb = self.theme_flows[name]
            fb.remove_all()
            for e in entries:
                fb.append(ThemeCard(e, self.current_theme))
            self.theme_counts[name].set_text(str(len(entries)))
        self._rebuild_bg_sidebar()
        self._rebuild_fonts()
        self.rotation.refresh()

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
        fams = {}
        for f in self.fonts:
            if f.package:
                fams.setdefault(f.package, []).append(f.family)
        inst, brow = self.font_lists["Installed"], self.font_lists["Browse"]
        inst.remove_all()
        brow.remove_all()
        for f in sorted(self.fonts, key=lambda f: (not f.current, f.family.lower())):
            inst.append(FontRow(self, f, cur_pkg))
        pkgs = sorted(self.font_pkgs, key=lambda p: (not p.omarchy_pick, not p.installed, p.package))
        for p in pkgs:
            brow.append(FontPackageRow(self, p, fams.get(p.package, []), cur_pkg))
        self.font_counts["Installed"].set_text(str(len(self.fonts)))
        self.font_counts["Browse"].set_text(str(len(self.font_pkgs)))

    # ---- navigation
    def _on_main_tab(self, name):
        self.main_stack.set_visible_child_name(name)
        self.search.set_sensitive(name != "Rotation")
        self.search.set_placeholder_text({"Themes": "Search themes…", "Backgrounds": "Search backgrounds…",
                                          "Fonts": "Search fonts…", "Rotation": ""}[name])
        self._apply_search()

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

    def show_theme(self, entry):
        if self.page:
            self.stack.remove(self.page)
        self.page = ThemePage(self, entry)
        self.stack.add_named(self.page, "theme")
        self.stack.set_visible_child_name("theme")

    def go_back(self):
        self.stack.set_visible_child_name("main")
        page, self.page = self.page, None
        if page:
            GLib.timeout_add(250, lambda: (self.stack.remove(page), False)[1])

    def show_backgrounds_for(self, name):
        self.go_back()
        self.main_tabs["Backgrounds"].set_active(True)
        row = self.bg_list.get_first_child()
        while row:
            if getattr(row, "theme", None) and row.theme.name == name:
                self.bg_list.select_row(row)
                break
            row = row.get_next_sibling()

    def show_export(self, entry):
        ExportDialog(self, entry).present(self)

    def _on_key(self, _ctl, keyval, _code, state):
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
    def do_action(self, a):
        def show():
            self.toasts.add_toast(Adw.Toast(title=f"Prototype, nothing changed. Would run: {a.command}", timeout=6))
        if a.label == "Remove":
            d = Adw.AlertDialog(heading="Remove?", body=f"This would run:\n\n{a.command}\n\n"
                                "(Prototype: nothing will actually be removed.)")
            d.add_response("cancel", "Cancel")
            d.add_response("ok", "Remove")
            d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_default_response("cancel")
            d.set_close_response("cancel")
            d.connect("response", lambda _d, r: r == "ok" and show())
            d.present(self)
        else:
            show()

    # ---- live theme
    def set_status(self, text):
        self.status.set_text(text)

    def reload_theme(self):
        t = theme.load_theme()
        self.font_base = t.font_px
        css = theme.build_css(t)
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

    def _state_changed(self):
        # Themes are swapped in several steps (rm + mv + hooks): settle, then re-read.
        def apply():
            self.reload_theme()
            self.current_theme = data.current_theme_name()
            self.rebuild()  # the current theme and background marks may both have moved
        self._debounce("state", 400, apply)

    def _watch(self, path, cb):
        try:
            mon = Gio.File.new_for_path(str(path)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        mon.connect("changed", lambda *_: cb())
        self._monitors.append(mon)

    def _watch_files(self):
        self._watch(data.STATE_DIR, self._state_changed)
        self._watch(data.USER_THEMES, lambda: self._debounce("local", 600, self.load))
        self._watch(data.USER_BACKGROUNDS, lambda: self._debounce("local", 600, self.load))
        self._watch(Path.home() / ".config/fontconfig", lambda: self._debounce("local", 600, self.load))


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()


def main():
    return App().run(sys.argv)
