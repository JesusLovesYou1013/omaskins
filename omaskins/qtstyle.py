"""Qt apps in the Omarchy theme's colours, see-through at the Transparency step ("Qt apps", on by default).

Omarchy themes only GTK apps; Qt5 apps (VLC, KeePassXC, ...) keep Qt's plain default look. OmaSkins ships
its own Qt style (qt-style/ in this folder: Qt's Fusion look, windows see-through at the step, menu bars and
toolbars showing that background, pop-up menus and video solid) and builds it here, as you, the first time a
Qt5 app is installed: Omarchy always has the compiler (base-devel), and Qt5 brings its own build kit.
No packages, no password, nothing of Omarchy's changed.

- The palette (~/.config/omaskins/qt-palette.conf): the theme's colours, the window colour carrying the
  step. The rotation engine rewrites it within half a second of a theme or Transparency change; the style
  watches it, so open Qt apps follow live.
- Turning it on for the session: omaskins.lua (OmaSkins' Hyprland file) sets QT_STYLE_OVERRIDE=OmaSkins
  and the plugin path, only while the built style and OmaSkins itself are there. Apps opened afterwards
  use it; Omarchy's own Qt setting (QT_QPA_PLATFORMTHEME=gtk3) stays as it is.
- Hyprland's whole-window fade: an app with see-through windows leaves a marker
  ($XDG_RUNTIME_DIR/omaskins-qt/<pid>); the engine takes its windows out of that fade, so they aren't
  see-through twice (and a video stays solid). Apps that bring their own style (KeePassXC's dark/light
  themes) leave no marker and keep Hyprland's fade.
Switched off: the palette and the built style go, omaskins.lua stops setting the two variables, and apps
opened from then on look as they did before.
"""

import hashlib
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

from . import data

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", data.HOME / ".config"))
SETTING = CONFIG / "omaskins" / "qt-apps"          # "off" = switched off; no file = on (the default)
PALETTE = CONFIG / "omaskins" / "qt-palette.conf"
COLORS = data.STATE_DIR / "theme" / "colors.toml"
ICONS = data.STATE_DIR / "theme" / "icons.theme"
PLUGINS = data.HOME / ".local/share/omaskins/qt5"  # QT_PLUGIN_PATH: Qt looks in styles/ under it
LIBRARY = PLUGINS / "styles" / "libomaskins.so"
STAMP = PLUGINS / "built-from"                     # what the library was built from (source + Qt version)
SOURCE = Path(__file__).resolve().parent.parent / "qt-style"
SOURCE_FILES = ("omaskins_style.cpp", "omaskins.json", "omaskins-qt5.pro")
BUILD_DIR = data.CACHE_DIR / "qt5-build"
BUILD_LOG = data.CACHE_DIR / "qt5-build.log"
QMAKE = "qmake-qt5"
PACKAGES = Path("/var/lib/pacman/local")
MARKS = Path(os.environ.get("XDG_RUNTIME_DIR") or data.OMASKINS_STATE) / "omaskins-qt"
WINDOW_TAG = "omaskins-qt"   # windows our style makes see-through: no Hyprland fade on top


def enabled():
    try:
        return SETTING.read_text().strip() != "off"
    except OSError:
        return True


def save_enabled(on):
    if on:
        SETTING.unlink(missing_ok=True)
    else:
        _write(SETTING, "off\n")


def signature():
    """What the palette is made from: the theme's colours and icons, and the Transparency step."""
    def stamp(p):
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None
    return stamp(COLORS), stamp(ICONS), stamp(data.TRANSPARENCY_FILE)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    tmp.replace(path)   # whole or not at all: an app never reads half a file


def _mix(a, b, t):
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t):02x}" for i in (0, 2, 4))


def colours():
    """The theme's colours for Qt, any missing ones blended from the others."""
    c = tomllib.loads(COLORS.read_text())
    bg, fg = c["background"], c["foreground"]
    accent = c.get("accent", fg)
    return {
        "bg": bg, "fg": fg, "accent": accent,
        "lbg": c.get("lighter_background", _mix(bg, fg, 0.08)),
        "dbg": c.get("dark_background", _mix(bg, "#000000" if c.get("mode") != "light" else "#ffffff", 0.25)),
        "muted": c.get("muted", c.get("dark_foreground", _mix(fg, bg, 0.45))),
        "sel_bg": c.get("selection_background", c.get("selection", _mix(bg, accent, 0.35))),
        "sel_fg": c.get("selection_foreground", c.get("bright_foreground", fg)),
        "bright": c.get("bright_foreground", fg),
    }


def palette_text(k, focused=1.0, unfocused=1.0, icons=None):
    """Qt's 21 palette roles per group, in Qt's order (qt5ct's colour scheme layout). The window colour
    carries the Transparency step (focused in the active group, unfocused in the inactive one)."""
    def h(x, a="ff"):
        return "#" + a + x.lstrip("#").lower()

    def roles(text, hl_text, alpha):
        return [text, h(k["lbg"]), h(_mix(k["lbg"], k["fg"], 0.15)), h(_mix(k["lbg"], k["fg"], 0.08)), h(k["dbg"]),
                h(_mix(k["bg"], k["lbg"], 0.5)), text, h(k["bright"]), text, h(k["dbg"]),
                h(k["bg"], f"{round(alpha * 255):02x}"), "#ff000000",
                h(k["sel_bg"]), hl_text, h(k["accent"]), h(k["muted"]), h(k["lbg"]), "#ff000000", h(k["lbg"]),
                h(k["fg"]), h(k["muted"], "80")]
    text = ("# Written by OmaSkins from the current Omarchy theme and Transparency step.\n[ColorScheme]\n"
            f"active_colors={', '.join(roles(h(k['fg']), h(k['sel_fg']), focused))}\n"
            f"disabled_colors={', '.join(roles(h(k['muted']), h(k['muted']), focused))}\n"
            f"inactive_colors={', '.join(roles(h(k['fg']), h(k['sel_fg']), unfocused))}\n")
    if icons:
        text += f"\n[Icons]\ntheme={icons}\n"
    return text


def rebuild():
    """Write the palette from the current theme and Transparency step (only when switched on)."""
    if not enabled():
        return False
    a, b, _ = data.transparency_values(data.transparency_step())
    icons = ICONS.read_text().strip() if ICONS.exists() else None
    text = palette_text(colours(), a, b, icons)
    try:
        if PALETTE.read_text() == text:
            return True   # unchanged: don't make every Qt app reload its colours
    except OSError:
        pass
    _write(PALETTE, text)
    return True


# --------------------------------------------------------------------------- building the style

def qt5_version():
    """Installed Qt5 (from pacman's own records, no command run), or None when there's no Qt5 at all."""
    try:
        return next((p.name for p in PACKAGES.iterdir() if p.name.startswith("qt5-base-")), None)
    except OSError:
        return None


def wanted_stamp():
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        digest.update((SOURCE / name).read_bytes())
    return f"{digest.hexdigest()[:16]} {qt5_version()}"


def needs_build():
    """Switched on, Qt5 here, and the library missing or built from other source or another Qt5."""
    if not enabled() or not qt5_version() or not shutil.which(QMAKE) or not shutil.which("make"):
        return False
    try:
        return not LIBRARY.exists() or STAMP.read_text().strip() != wanted_stamp()
    except OSError:
        return True


def build(runner=subprocess.run):
    """Build the style as you (no password) in OmaSkins' cache, then put it in place whole. Returns
    (ok, why); the compiler's output goes to qt5-build.log."""
    stamp = wanted_stamp()
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True)
    for name in SOURCE_FILES:
        shutil.copy2(SOURCE / name, BUILD_DIR / name)
    BUILD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(BUILD_LOG, "w") as log:
        for argv in ([QMAKE, "omaskins-qt5.pro"], ["make", "-j2"]):
            done = runner(argv, cwd=BUILD_DIR, stdout=log, stderr=subprocess.STDOUT, timeout=600,
                          env=data.child_env())
            if done.returncode != 0:
                return False, f"{argv[0]} failed (see {BUILD_LOG})"
    built = BUILD_DIR / "libomaskins.so"
    if not built.exists():
        return False, "nothing was built"
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = LIBRARY.with_name(".libomaskins.so.tmp")
    shutil.copy2(built, tmp)
    tmp.replace(LIBRARY)   # apps starting right now get the old one or the new one, never half
    _write(STAMP, stamp + "\n")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    return True, "built"


def remove():
    """Switched off: the palette and the built style go (apps already open keep their look until closed)."""
    PALETTE.unlink(missing_ok=True)
    shutil.rmtree(PLUGINS, ignore_errors=True)
    shutil.rmtree(BUILD_DIR, ignore_errors=True)


# --------------------------------------------------------------------------- Hyprland's fade

def _alive(pid):
    return Path(f"/proc/{pid}").exists()


def marked_pids(alive=_alive):
    """Apps with see-through windows from our style; markers of closed apps are cleared."""
    pids = set()
    try:
        marks = list(MARKS.iterdir())
    except OSError:
        return pids
    for mark in marks:
        if mark.name.isdigit() and alive(int(mark.name)):
            pids.add(int(mark.name))
        else:
            mark.unlink(missing_ok=True)
    return pids


def marks_stamp():
    try:
        return MARKS.stat().st_mtime_ns
    except OSError:
        return None


def unfade_rule():
    return f"hl.window_rule({{ match = {{ tag = '{WINDOW_TAG}' }}, opacity = '1 1' }})"


def unfade_windows(clients, evaluate, alive=_alive):
    """Take the marked apps' windows out of Hyprland's whole-window fade (they do their own). Returns the
    windows changed."""
    pids = marked_pids(alive)
    changed = []
    for c in clients():
        if c.get("pid") not in pids:
            continue
        tags = [t.rstrip("*") for t in c.get("tags", [])]
        if WINDOW_TAG in tags:
            continue   # done (Omarchy's own "default-opacity" tag comes back from its rule; ours overrides it)
        address = c["address"]
        evaluate(f"hl.dispatch(hl.dsp.window.tag({{ window = 'address:{address}', tag = '-default-opacity' }}))")
        if WINDOW_TAG not in tags:
            evaluate(f"hl.dispatch(hl.dsp.window.tag({{ window = 'address:{address}', tag = '+{WINDOW_TAG}' }}))")
        changed.append(address)
    return changed
