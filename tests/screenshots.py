"""Dev tool: open the app, walk through each tab, and save a PNG of the window (only the window,
never the screen) for each. Usage: python3 tests/screenshots.py <out-dir>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from omaskins import app  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")  # add --share for just the Share dialog
OUT.mkdir(parents=True, exist_ok=True)


def shot(win, name):
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node:
        win.get_renderer().render_texture(node, None).save_to_png(str(OUT / f"{name}.png"))
        print("saved", OUT / f"{name}.png")


def steps(win):
    def browse(): pass
    def installed(): win.theme_sub_btns["Installed"].set_active(True)
    def theme_page(): win.show_theme(win.theme_flows["Browse"].get_child_at_index(3).entry)
    def backgrounds(): (win.go_back(), win.main_tabs["Backgrounds"].set_active(True))
    def fonts_browse(): (win.main_tabs["Fonts"].set_active(True), win.font_sub_btns["Browse"].set_active(True))
    def fonts_installed(): win.font_sub_btns["Installed"].set_active(True)
    def share(): win.show_export(None)
    return [("7-share", share)] if "--share" in sys.argv else [("1-themes-browse", browse), ("2-themes-installed", installed), ("3-theme-page", theme_page),
            ("4-backgrounds", backgrounds), ("5-fonts-browse", fonts_browse), ("6-fonts-installed", fonts_installed)]


def run(application):
    win = application.props.active_window
    todo = steps(win)

    def tick():
        if not todo:
            application.quit()
            return False
        name, fn = todo.pop(0)
        fn()
        GLib.timeout_add(2500, lambda: (shot(win, name), False)[1])
        return True
    GLib.timeout_add(5000, lambda: (GLib.timeout_add(3500, tick), False)[1])


a = app.App()
a.connect("activate", lambda application: GLib.idle_add(lambda: (run(application), False)[1]))
a.run([])
