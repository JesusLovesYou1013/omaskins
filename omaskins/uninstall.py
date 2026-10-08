"""Removing OmaSkins puts the desktop back the way Omarchy ships it and leaves nothing of OmaSkins behind.

`omarchy plugin remove` deletes the plugin's folder the moment it has switched the plugin off, so nothing
in that folder can do the cleaning up. While OmaSkins is on, its engine keeps a copy of this code outside
the plugin folder (stage(), in OmaSkins' state folder); Service.qml starts that copy when the plugin goes
off, and it acts only once the plugin's folder is really gone. Switching OmaSkins off (not removing it)
changes none of this: everything stays as you set it.

What removal undoes, in order:
- Transparency: faded back to Omarchy's own, with OmaSkins' blocks out of GTK's stylesheets (Nautilus,
  file dialogs) and Omarchy's shell.toml;
- Qt apps: the built style and its palette;
- Rounded Corners and the rest of OmaSkins' Hyprland file: the file and its one line in looknfeel.lua;
- built-in themes you removed entirely: restored (one password, in Omarchy's own terminal; cancel it and
  they stay removed, which the closing notification says);
- a background from a different theme than the current one (OmaSkins can mix them, Omarchy can't): you
  choose which of the two to keep;
- OmaSkins' own folders: settings, state, cache, built files.

Left alone (owner, 2026-10-08): fonts and text size (Omarchy's own menus change those too), and the
themes and backgrounds you added, which are Omarchy's as much as OmaSkins'.
"""

import fcntl
import os
import random
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import data

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", data.HOME / ".config"))
PLUGINS = CONFIG / "omarchy" / "plugins"
STAGED = data.OMASKINS_STATE / "uninstall"
ENTRY = STAGED / "omaskins-uninstall"
ENTRY_TEXT = """#!/usr/bin/python3
# OmaSkins' own remover: a copy kept outside the plugin folder (see omaskins/uninstall.py there).
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from omaskins.uninstall import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
"""
WAIT_GONE = 10        # seconds to see the plugin's folder go; still there = only switched off
KEEP_THEME, KEEP_BACKGROUND = "theme", "background"
MENU_WIDTH = 720      # px, for the "keep which?" menu
APP_CLASS = "io.github.jesuslovesyou1013.omaskins"


# --------------------------------------------------------------------------- the copy outside the plugin

def _package_files():
    here = Path(__file__).resolve().parent
    return sorted(p for p in here.glob("*.py"))


def stage():
    """Keep the copy up to date (the engine calls this at every start). True when something was written."""
    wanted = {STAGED / "omaskins" / p.name: p.read_bytes() for p in _package_files()}
    wanted[ENTRY] = ENTRY_TEXT.encode()
    changed = False
    for dest, content in wanted.items():
        try:
            if dest.read_bytes() == content:
                continue
        except OSError:
            pass
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name("." + dest.name + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(dest)
        changed = True
    for old in (STAGED / "omaskins").glob("*.py"):   # a module a newer version no longer has
        if old not in wanted:
            old.unlink()
            changed = True
    return changed


# --------------------------------------------------------------------------- is it really gone?

def removed(plugin_id, wait=WAIT_GONE, sleep=time.sleep, enabled=None):
    """True once the plugin's folder is gone and its id is out of shell.json. A folder still there after
    `wait` seconds means it was only switched off (or the shell is restarting): nothing to undo."""
    if enabled is None:
        from . import rotation
        enabled = rotation.plugin_enabled
    folder = PLUGINS / plugin_id
    end = time.monotonic() + wait
    while True:
        if not folder.exists() and not folder.is_symlink() and not enabled(plugin_id):
            return True
        if time.monotonic() >= end:
            return False
        sleep(0.25)


def wait_for_engine(wait=5.0, sleep=time.sleep):
    """The engine rewrites OmaSkins' blocks as it runs: give it a moment to have stopped (it holds this lock)."""
    lock_file = Path(os.environ.get("XDG_RUNTIME_DIR") or data.OMASKINS_STATE) / "omaskins-rotate.lock"
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            with open(lock_file, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
        except OSError:
            sleep(0.25)
    return False


# --------------------------------------------------------------------------- the look, back to Omarchy's

def strip_require(looknfeel=None):
    """OmaSkins' line and every comment OmaSkins put above it out of looknfeel.lua; everything else as
    it was. Earlier versions added their comment again without the line (2026-10-08: a real file had
    five), so every such comment goes, wherever it sits. True when the file changed."""
    looknfeel = Path(looknfeel or data.HYPR_DIR / "looknfeel.lua")
    try:
        lines = looknfeel.read_text().split("\n")
    except OSError:
        return False
    out = []
    for line in lines:
        if line.strip() == data.CORNERS_REQUIRE or line.startswith("-- Added by OmaSkins Manager"):
            while out and out[-1] == "":     # the blank line OmaSkins put before its comment
                out.pop()
            continue
        out.append(line)
    text = "\n".join(out)
    if not text.endswith("\n") and text:
        text += "\n"
    if text == "\n".join(lines):
        return False
    tmp = looknfeel.with_name(".looknfeel.lua.omaskins-tmp")
    tmp.write_text(text)
    shutil.copymode(looknfeel, tmp)
    tmp.replace(looknfeel)
    return True


def restore_look(run=None, log=print):
    """Transparency, file dialogs, Qt apps and corners back to Omarchy's own. Each part on its own: one
    that fails is reported and the rest still happen. Returns what couldn't be done."""
    if run is None:
        from . import run
    from . import qtstyle
    failed = []

    def part(name, work):
        try:
            work()
        except Exception as e:
            log(f"{name}: {e!r}")
            failed.append(name)

    def transparency():
        if data.transparency_step() != data.TRANSPARENCY_DEFAULT:
            run.apply_transparency(data.TRANSPARENCY_DEFAULT)   # live fade; also the shell and Nautilus blocks
        data.save_transparency(data.TRANSPARENCY_DEFAULT)
        data.write_shell_block(data.TRANSPARENCY_DEFAULT)
        data.write_nautilus_css(data.TRANSPARENCY_DEFAULT)

    def dialogs():
        if data.write_dialog_css(data.TRANSPARENCY_DEFAULT):
            run.hypr_eval(run.FADE_LUA + "dialogs(false)")
            if run.dialog_service_running() and not any(c.get("class") == data.DIALOG_CLASS
                                                        for c in run.hypr_clients()):
                run.restart_dialog_service()

    def qt_apps():
        qtstyle.remove()
        # Windows the style made see-through go back to Omarchy's own fade; apps opened from now on get
        # Qt's own style (an empty override is ignored: Hyprland can't unset one).
        for c in run.hypr_clients():
            if qtstyle.WINDOW_TAG in run._tags(c):
                run.hypr_eval(f"hl.dispatch(hl.dsp.window.tag({{ window = 'address:{c['address']}', "
                              f"tag = '-{qtstyle.WINDOW_TAG}' }}))")
        run.hypr_eval('if os.getenv("QT_STYLE_OVERRIDE") == "OmaSkins" then hl.env("QT_STYLE_OVERRIDE", "") end')

    def hyprland():
        had = data.CORNERS_FILE.exists()
        changed = strip_require()            # the line first: nothing may require a file that's gone
        data.CORNERS_FILE.unlink(missing_ok=True)
        if had or changed:
            # Saving looknfeel.lua makes Hyprland reload by itself; this one makes sure of it, so the
            # rules OmaSkins set live go too. Then the shell re-reads the corner radius.
            time.sleep(0.5)
            run.run(["hyprctl", "reload"])
            run.shell_restyle()

    part("transparency", transparency)
    part("file dialogs", dialogs)
    part("Qt apps", qt_apps)
    part("rounded corners", hyprland)
    return failed


# --------------------------------------------------------------------------- built-in themes

def restore_builtins(run=None, log=print):
    """Built-ins removed entirely come back: one terminal, one password. Returns the ones still missing."""
    if run is None:
        from . import run
    package = data.builtin_package()
    missing = data.removed_builtins(data.shipped_builtins(package))
    if not missing:
        return []
    unhide, reinstall = [], []
    current = data.package_version(package)
    for name in missing:   # the same choice the Restore button makes
        (unhide if data.builtin_held_aside(name) and current and current == data.removed_at_version(name)
         else reinstall).append(name)
    cmd = data.password_terminal_command((), (), (), unhide, reinstall, package)
    run.clear_password_marks()
    try:
        run.perform((("terminal", cmd),))
        result, why = run.wait_import_password(data.parse_password_terminal_head(cmd))
    finally:
        run.clear_password_marks()
    log(f"built-in themes: {why}")
    return sorted(name for (kind, name), ok in result.items() if not ok)


# --------------------------------------------------------------------------- theme and background that don't match

def background_owner(path):
    """The theme a background belongs to (its folder name), or "" for a picture that is no theme's."""
    path = Path(path)
    for root, depth in ((data.USER_THEMES, 2), (data.BUILTIN_THEMES, 2), (data.USER_BACKGROUNDS, 1)):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) == depth + 1 and (depth == 1 or rel.parts[1] == "backgrounds"):
            return rel.parts[0]
    return ""


def own_backgrounds(theme):
    """Every background Omarchy itself would offer for a theme: the theme's own and yours for it."""
    added = data.USER_THEMES / theme   # an added theme shadows a built-in of the same name
    found = []
    for folder in ((added if added.is_dir() else data.BUILTIN_THEMES / theme) / "backgrounds",
                   data.USER_BACKGROUNDS / theme):
        if folder.is_dir():
            found += sorted(str(p) for p in folder.iterdir()
                            if p.is_file() and p.name.lower().endswith(data.IMAGE_EXT))
    return found


def mismatch():
    """(current theme, the other theme the background is from), or None when they belong together."""
    from . import rotation
    theme = data.current_theme_name()
    owner = background_owner(rotation.canonical_background(theme))
    if not theme or not owner or owner == theme:
        return None
    if not ((data.USER_THEMES / owner).is_dir() or (data.BUILTIN_THEMES / owner).is_dir()):
        return None   # a picture kept for a theme that's no longer installed: nothing to switch to
    return theme, owner


def ask(theme, owner):
    """Omarchy's own menu. KEEP_THEME, KEEP_BACKGROUND, or None when it's closed without choosing."""
    keep_theme = f"Keep the {data.display_name(theme)} theme"
    keep_bg = "Keep this background"
    try:
        # Omarchy's menu is 300 px unless told: too narrow for these rows (owner, 2026-10-08: the first
        # one was cut off mid-word). Wide enough for the longest theme name at a large text size.
        r = subprocess.run(["omarchy-menu-select", "Theme and background don't match. Keep which?",
                            f"\U000F03D8\t{keep_theme}\twith one of its own backgrounds",
                            f"\U000F02E9\t{keep_bg}\tand switch to its theme, {data.display_name(owner)}",
                            "--", "--width", str(MENU_WIDTH)],
                           capture_output=True, text=True, timeout=600, env=data.child_env())
    except (OSError, subprocess.SubprocessError):
        return None
    choice = r.stdout.strip().split("\t")[0]
    return KEEP_THEME if choice == keep_theme else KEEP_BACKGROUND if choice == keep_bg else None


def match_up(choose=ask, run=None, rng=random, log=print):
    """Theme and background from different themes: ask which to keep. Returns a line for the closing
    notification ("" when there was nothing to ask)."""
    if run is None:
        from . import run
    from . import rotation
    pair = mismatch()
    if not pair:
        return ""
    theme, owner = pair
    current = rotation.canonical_background(theme)
    choice = choose(theme, owner)
    log(f"theme {theme}, background from {owner}: {choice or 'left as they are'}")
    if choice == KEEP_THEME:
        pool = own_backgrounds(theme)
        if not pool:
            return f"{data.display_name(theme)} has no backgrounds of its own: the background was left."
        run.run(["omarchy-theme-bg-set", rng.choice(pool)], timeout=60)
        return f"Kept the {data.display_name(theme)} theme, with one of its own backgrounds."
    if choice == KEEP_BACKGROUND:
        run.set_theme(data.theme_folder_for(owner), current)
        return f"Kept your background, with its theme, {data.display_name(owner)}."
    return "Theme and background were left as they are."


# --------------------------------------------------------------------------- OmaSkins' own folders

def own_folders():
    from . import qtstyle
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or data.OMASKINS_STATE)
    return ([CONFIG / "omaskins", data.CACHE_DIR, qtstyle.PLUGINS.parent, data.PARTIAL_THEMES,
             data.REMOVING_THEMES]
            + sorted(runtime.glob("omaskins-*")) + [data.OMASKINS_STATE])   # the state folder (this copy) last


def delete_own(folders=None):
    for path in folders if folders is not None else own_folders():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- all of it

def close_windows(run=None, kill=os.kill, wait=3.0, sleep=time.sleep):
    """An OmaSkins window still open would go on writing its cache (2026-10-08: ~/.cache/omaskins came
    back that way), and it runs from a folder that no longer exists: it's closed first."""
    if run is None:
        from . import run
    pids = {int(c["pid"]) for c in run.hypr_clients() if c.get("class") == APP_CLASS and c.get("pid")}
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
        except OSError:
            pass
    end = time.monotonic() + wait
    while pids and time.monotonic() < end and any(c.get("class") == APP_CLASS for c in run.hypr_clients()):
        sleep(0.2)
    return len(pids)


def uninstall(run=None, choose=ask, notify=None, log=print):
    try:
        close_windows(run)
    except Exception as e:
        log(f"closing OmaSkins' window: {e!r}")
    failed = restore_look(run, log)
    try:
        still_removed = restore_builtins(run, log)
    except Exception as e:
        log(f"built-in themes: {e!r}")
        still_removed = ["?"]
    try:
        said = match_up(choose, run, log=log)
    except Exception as e:
        log(f"theme and background: {e!r}")
        said = ""
    delete_own()
    lines = [said] if said else []
    if still_removed:
        names = ", ".join(data.display_name(n) for n in still_removed if n != "?") or "some built-in themes"
        lines.append(f"Still removed, because the password step didn't finish: {names}. "
                     f"Add OmaSkins again and use Restore to bring them back.")
    if failed:
        lines.append("Couldn't undo: " + ", ".join(failed) + ".")
    headline = "OmaSkins removed"
    body = " ".join(lines) or "Transparency, corners and everything else are Omarchy's own again."
    (notify or _notify)(headline, body)
    return 1 if failed or still_removed else 0


def _notify(headline, body):
    try:
        subprocess.run(["omarchy-notification-send", "--app-name", "OmaSkins", headline, body],
                       capture_output=True, timeout=10, env=data.child_env())
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv):
    """omaskins-uninstall <plugin id>: undo everything, but only once that plugin's folder is gone.
    --now skips the wait (the folder must still be gone): for a plugin removed while it was switched off."""
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("usage: omaskins-uninstall <plugin id> [--now]", file=sys.stderr)
        return 2
    if not removed(args[0], wait=0 if "--now" in argv else WAIT_GONE):
        return 0
    wait_for_engine()
    return uninstall()
