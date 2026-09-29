"""The only place OmaSkins changes the system.

An `Action` (data.py) describes its work as `steps`; `perform()` carries them out. Commands are
argument lists (never shell strings, so nothing in a theme name, path or URL can be read as extra
shell syntax) and only programs in ALLOWED may run, some with a stricter shape check. File steps
may only touch your own backgrounds folder, and theme downloads (cloned into a holding folder, then
moved into ~/.config/omarchy/themes in one step once complete). Everything else in the app just reads.

Live so far: batch 1, everyday actions that never ask for a password (themes: apply, add, remove
yours; backgrounds: set, add, copy, remove yours, open folder). Removing a theme is taken out of
themes/ in one rename, then deleted file by file so its bar counts down.
Batch 2, fonts: Use (omarchy-font-set, no password) and adding/removing Nerd Font packages, which
run in Omarchy's floating terminal where pacman asks for the password; OmaSkins waits for them.

(data.py also calls a few programs, all read-only: pacman -Q, fc-list, hyprctl getoption, magick
for thumbnails in the app's own cache.)
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import data


def _git_clone_only(argv):
    # git clone --progress -- <url> <new folder in the partial area>: the clone omarchy-theme-install
    # does (plus progress output for the bar), but into a holding folder that is moved into
    # ~/.config/omarchy/themes only once it is complete.
    return (len(argv) == 6 and argv[1:4] == ["clone", "--progress", "--"]
            and Path(argv[5]).parent == data.PARTIAL_THEMES and not Path(argv[5]).exists())


def _theme_removed_notice(argv):
    # The notification omarchy-theme-remove sends at the end, and nothing else.
    return len(argv) == 3 and argv[1] == "Theme removed"


def _one_arg(argv):
    return len(argv) == 2 and bool(argv[1].strip())


_FONT_TERMINAL = re.compile(r"echo '(Installing|Removing) (\S+)\.\.\.'; omarchy-pkg-(add|drop) (\S+)")


def _font_terminal(argv):
    # Omarchy's floating terminal, and only ever to add or drop one Nerd Font package: the command
    # string is rebuilt from the checked package name and must come out identical.
    m = len(argv) == 2 and _FONT_TERMINAL.fullmatch(argv[1])
    return bool(m and m.group(2) == m.group(4) and data.FONT_PKG_OK.fullmatch(m.group(2))
                and argv[1] == data.font_terminal_command(m.group(3), m.group(2)))


def _reload_signal(argv):
    # The live-reload nudges omarchy-font-set and omarchy-display-text-size send, and nothing else.
    return argv in (["pkill", "-USR1", "kitty"], ["pkill", "-SIGUSR2", "ghostty"])


ALLOWED = {
    "omarchy-theme-set": None,
    "pkill": _reload_signal,
    "omarchy-font-set": _one_arg,
    "omarchy-launch-floating-terminal-with-presentation": _font_terminal,
    "omarchy-notification-send": _theme_removed_notice,
    "omarchy-theme-bg-set": None,
    "omarchy-git-url-check": None,
    "git": _git_clone_only,
    "nautilus": None,
}


class NotAllowed(Exception):
    pass


class StepFailed(Exception):
    pass


def check(argv):
    """Raise NotAllowed unless argv is a non-empty list of strings for an allow-listed program."""
    if isinstance(argv, str) or not argv or not all(isinstance(a, str) for a in argv):
        raise NotAllowed(f"not an argument list: {argv!r}")
    if argv[0] not in ALLOWED:
        raise NotAllowed(f"{argv[0]} is not on OmaSkins' allow-list")
    shape = ALLOWED[argv[0]]
    if shape and not shape(argv):
        raise NotAllowed(f"{argv[0]} is only allowed in one form here: {argv!r}")


def run(argv, timeout=300):
    """Run an allow-listed command and wait; returns the CompletedProcess (no raise on non-zero exit)."""
    check(argv)
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)


# git clone --progress phases -> (start, end) of the overall bar. Theme repos are mostly pictures,
# so receiving them is nearly all of the wait.
_CLONE_PHASES = {"Counting objects": (0, 2), "Compressing objects": (2, 5), "Receiving objects": (5, 90),
                 "Resolving deltas": (90, 96), "Updating files": (96, 100)}
_CLONE_LINE = re.compile(r"(?:remote: )?(%s):\s+(\d+)%%" % "|".join(_CLONE_PHASES))


def clone_percent(line):
    """Overall 0-100 for one line of git clone --progress output, or None if it isn't a progress line."""
    m = _CLONE_LINE.match(line.strip())
    if not m:
        return None
    lo, hi = _CLONE_PHASES[m.group(1)]
    return lo + (hi - lo) * int(m.group(2)) // 100


def run_with_progress(argv, progress, timeout=300):
    """Like run(), but reads the command's progress lines as they come (git writes them to stderr,
    each update ending in \r) and calls progress(percent) whenever the overall figure goes up."""
    check(argv)
    p = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE)
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    lines, buf, best = [], b"", -1
    try:
        while chunk := p.stderr.read1(4096):
            *done, buf = re.split(rb"[\r\n]", buf + chunk)
            for raw in done:
                line = raw.decode(errors="replace")
                if line.strip():
                    lines.append(line)
                pct = clone_percent(line)
                if pct is not None and pct > best:
                    best = pct
                    progress(pct)
        p.wait()
    finally:
        timer.cancel()
    if buf.strip():
        lines.append(buf.decode(errors="replace"))
    if p.returncode == 0 and best < 100:
        progress(100)  # small repos skip the later phases; a finished download is a full bar
    return subprocess.CompletedProcess(argv, p.returncode, "", "\n".join(lines[-20:]))


def launch(argv):
    """Start an allow-listed app (a file manager) and don't wait for it."""
    check(argv)
    subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _own_backgrounds(path):
    """`path`, resolved, if it lies inside ~/.config/omarchy/backgrounds/<theme>/; else refuse."""
    root = data.USER_BACKGROUNDS.resolve()
    p = Path(path).resolve()
    if p == root or root not in p.parents:
        raise NotAllowed(f"{path} is outside your backgrounds folder")
    return p


def copy_in(src, dest_dir):
    """Copy an image into one of your backgrounds folders. Never overwrites: an identical file is
    left as is, a different one with the same name gets -2, -3, ... Returns (dest, already_there)."""
    src, dest_dir = Path(src), _own_backgrounds(dest_dir)
    if not src.is_file():
        raise StepFailed(f"{src.name} isn't a file")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest, n = dest_dir / src.name, 1
    while dest.exists():
        if dest.stat().st_size == src.stat().st_size and dest.read_bytes() == src.read_bytes():
            return dest, True
        n += 1
        dest = dest_dir / f"{src.stem}-{n}{src.suffix}"
    shutil.copy2(src, dest)
    return dest, False


def _partial(path):
    p = Path(path)
    if p.parent != data.PARTIAL_THEMES or not p.name or p.name.startswith("."):
        raise NotAllowed(f"{path} is not a download holding folder")
    return p


def _removing(path):
    p = Path(path)
    if p.parent != data.REMOVING_THEMES or not p.name or p.name.startswith("."):
        raise NotAllowed(f"{path} is not a removal holding folder")
    return p


def take_out(folder, gone):
    """Move one of your themes out of ~/.config/omarchy/themes in a single rename (same rules as
    omarchy-theme-remove: a real folder directly in themes/, no dot-names)."""
    folder, gone = Path(folder), _removing(gone)
    if folder.parent != data.USER_THEMES or folder.name != gone.name or folder.name.startswith("."):
        raise NotAllowed(f"won't remove {folder}")
    if not folder.is_dir():
        raise StepFailed(f"{folder.name} is already gone")
    shutil.rmtree(gone, ignore_errors=True)  # a leftover from an interrupted earlier removal
    gone.parent.mkdir(parents=True, exist_ok=True)
    folder.rename(gone)


def delete_counting(gone, progress=None, at_least=1.0):
    """rm -rf, one file at a time, reporting what's left (100 down to 0, by size) and spread over
    at least `at_least` seconds so the bar can be seen. Never follows a symlink out of the folder."""
    gone = _removing(gone)
    if gone.is_symlink() or not gone.is_dir():
        gone.unlink(missing_ok=True)
        if progress:
            progress(0)
        return
    files, dirs = [], []
    for root, dnames, fnames in os.walk(gone):  # doesn't descend into symlinked folders
        root = Path(root)
        dirs.append(root)
        for n in fnames + [d for d in dnames if (root / d).is_symlink()]:
            files.append((root / n, (root / n).lstat().st_size))
    total = sum(size for _, size in files) or 1
    left, start, shown = total, time.monotonic(), 100
    for path, size in files:
        path.unlink()
        left -= size
        pct = 100 * left // total
        if progress and pct < shown:
            shown = pct
            wait = start + at_least * (1 - left / total) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            progress(pct)
    for d in reversed(dirs):  # deepest first
        d.rmdir()
    if progress and shown > 0:
        progress(0)


def package_installed(pkg):
    return subprocess.run(["pacman", "-Q", pkg], capture_output=True).returncode == 0


def _cmdlines():
    """Every process's command line, as one string each (read from /proc, nothing run)."""
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                out.append((d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip())
            except OSError:
                pass
    return out


def wait_package(pkg, want_installed, term_cmd, poll=1.0, grace=10.0, give_up=900.0,
                 installed=package_installed, cmdlines=_cmdlines):
    """Wait while the terminal does its work: done once pacman has the package added (or dropped) and
    the add/drop script has exited (so the font cache is rebuilt too). If the terminal is gone and
    nothing changed (closed, wrong password, cancelled), say so."""
    verb = "add" if want_installed else "drop"
    start = time.monotonic()
    while True:
        procs = cmdlines()
        working = any(c.endswith(f"omarchy-pkg-{verb} {pkg}") for c in procs)
        if installed(pkg) == want_installed and not working:
            return
        elapsed = time.monotonic() - start
        if elapsed > grace and not working and not any(term_cmd in c for c in procs):
            raise StepFailed("the terminal closed before it finished (nothing changed)")
        if elapsed > give_up:
            raise StepFailed("gave up waiting for the terminal")
        time.sleep(poll)


def use_font(pkg, family="", wait=10.0, fonts=None, fc_list=None):
    """omarchy-font-set for a just-installed package: Omarchy's own family name for it when its menu
    offers it (waiting until fontconfig lists it, the same check omarchy-font-set makes), otherwise
    the package's first monospace family."""
    fonts = fonts or data.installed_fonts
    fc_list = fc_list or (lambda: data._run(["fc-list"]))
    end = time.monotonic() + wait
    while True:
        if family:
            pick = family if family.lower() in fc_list().lower() else ""
            if pick.endswith(" Nerd Font Mono") and any(f.family == pick[:-5] for f in fonts()):
                pick = pick[:-5]  # the owner's choice: the regular name, when it's monospace too (not Iosevka's)
        else:
            pick = next((f.family for f in data.without_icon_twins(fonts()) if f.package == pkg), "")
        if pick:
            break
        if time.monotonic() > end:
            if not family and check_mono(pkg, fonts):
                raise StepFailed(f"{pkg} is installed, but it isn't a monospace font, so Omarchy can't "
                                 "use it as the system font")
            raise StepFailed(f"{pkg} is installed, but its font didn't show up to switch to")
        time.sleep(0.5)
    r = run(["omarchy-font-set", pick])
    if r.returncode != 0:
        why = (r.stderr or r.stdout).strip().splitlines()
        raise StepFailed(why[-1] if why else "omarchy-font-set failed")
    font_size(pick)


# ---- matched font sizes: the same lines `omarchy display text size` edits, and only those

def _cfg(rel):
    return data.HOME / ".config" / rel


def shell_base_size():
    """[font] base-size from ~/.config/omarchy/shell.toml, or Omarchy's default 12."""
    in_font = False
    try:
        for line in _cfg("omarchy/shell.toml").read_text().splitlines():
            if re.match(r"\s*\[", line):
                in_font = bool(re.match(r"\s*\[font\](\s|$)", line))
            elif in_font and (m := re.match(r"\s*base-size\s*=\s*(\d+)", line)):
                return int(m.group(1))
    except OSError:
        pass
    return data.SHELL_DEFAULT_PX


def _set_shell_base_size(px):
    """Upsert base-size under [font], leaving every other line alone (as Omarchy's own script does)."""
    path = _cfg("omarchy/shell.toml")
    lines = path.read_text().splitlines() if path.exists() else []
    out, in_font, done = [], False, False
    for line in lines:
        if re.match(r"\s*\[", line):
            if in_font and not done:
                out.append(f"base-size = {px}")
                done = True
            in_font = bool(re.match(r"\s*\[font\](\s|$)", line))
        elif in_font and re.match(r"\s*base-size\s*=", line):
            if not done:
                out.append(f"base-size = {px}")
                done = True
            continue
        out.append(line)
    if in_font and not done:
        out.append(f"base-size = {px}")
        done = True
    if not done:
        out += ([""] if out else []) + ["[font]", f"base-size = {px}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".omaskins-tmp")
    tmp.write_text("\n".join(out) + "\n")
    tmp.replace(path)


def _sub_in(path, pattern, repl):
    """Replace a size line in an existing config (never creates one); True if the file is there."""
    if not path.is_file():
        return False
    text = path.read_text()
    new = re.sub(pattern, repl, text, flags=re.M)
    if new != text:
        path.write_text(new)
    return True


def _set_terminal_size(pt):
    _sub_in(_cfg("alacritty/alacritty.toml"), r"^size[ \t]*=.*$", f"size = {pt}")
    kitty = _cfg("kitty/kitty.conf")
    if kitty.is_file():
        if re.search(r"^[ \t]*font_size[ \t]+", kitty.read_text(), flags=re.M):
            _sub_in(kitty.resolve(), r"^[ \t]*font_size[ \t]+.*$", f"font_size {float(pt)}")
        else:
            with kitty.open("a") as f:
                f.write(f"\nfont_size {float(pt)}\n")
        run(["pkill", "-USR1", "kitty"])
    if _sub_in(_cfg("ghostty/config"), r"^font-size = .*$", f"font-size = {pt}"):
        run(["pkill", "-SIGUSR2", "ghostty"])
    _sub_in(_cfg("foot/foot.ini"), r"(:size=)[0-9.]+", rf"\g<1>{pt}")


def _text_size_state():
    try:
        return json.loads(data.TEXT_SIZE_STATE.read_text())
    except (OSError, ValueError):
        return {}


def remember_text_size():
    """Your own text size (Omarchy's `display text size`, 12 px if never set), before a font change.
    If the shell's base-size isn't what OmaSkins last wrote, you (or Omarchy) set it since: that
    plain value is your text size now."""
    st, current = _text_size_state(), shell_base_size()
    if st.get("wrote") != current or "base" not in st:
        st = {"base": current, "wrote": current}
        data.TEXT_SIZE_STATE.parent.mkdir(parents=True, exist_ok=True)
        data.TEXT_SIZE_STATE.write_text(json.dumps(st))
    return st["base"]


def font_size(family):
    """Scale the shell's base-size and the terminals' point size so `family` reads as big as
    JetBrains Mono at your text size. JetBrains itself gets exactly Omarchy's own sizes."""
    base = _text_size_state().get("base") or remember_text_size()
    px, pt = data.sizes_for(base, data.font_scale(data.font_x_height(family)))
    if px != shell_base_size() or _cfg("omarchy/shell.toml").exists():
        _set_shell_base_size(px)
    _set_terminal_size(pt)
    data.TEXT_SIZE_STATE.parent.mkdir(parents=True, exist_ok=True)
    data.TEXT_SIZE_STATE.write_text(json.dumps({"base": base, "wrote": px}))


def check_mono(pkg, fonts=None, families=None):
    """After an install: a note if the package brought no monospace face (nothing to fail, but
    Omarchy's font menu won't list it); None when it's fine."""
    fonts = fonts or data.installed_fonts
    families = families or data.package_families
    if any(f.package == pkg for f in fonts()):
        return None
    if families([pkg]).get(pkg):
        data.learn_not_mono(pkg)  # so Browse stops offering it
        return f"{pkg} added, but it isn't a monospace font, so Omarchy can't use it and it's hidden. Remove it?"
    return None


def perform(steps, progress=None):
    """Carry out an action's steps in order; stop at the first failure (StepFailed says why).
    A failed or refused theme download never leaves its half-finished folder behind.
    progress(percent), if given, hears how a theme download is going (called on this thread)."""
    try:
        return _perform(steps, progress)
    except Exception:
        for step in steps:
            if step[0] == "clear_partial":
                shutil.rmtree(_partial(step[1]), ignore_errors=True)
        raise
    finally:
        for step in steps:
            if step[0] == "delete_counting":  # whatever a failed removal left behind
                shutil.rmtree(_removing(step[1]), ignore_errors=True)


def _perform(steps, progress=None):
    """Returns a note to show instead of the usual "done" message, if a step had one."""
    note = None
    for step in steps:
        kind, args = step[0], step[1:]
        if kind == "run":
            if progress and args[0][:2] == ["git", "clone"]:
                r = run_with_progress(args[0], progress)
            else:
                r = run(args[0])
            if r.returncode != 0:
                why = (r.stderr or r.stdout).strip().splitlines()
                raise StepFailed(why[-1] if why else f"{args[0][0]} failed ({r.returncode})")
        elif kind == "launch":
            launch(args[0])
        elif kind == "mkdir":
            _own_backgrounds(args[0]).mkdir(parents=True, exist_ok=True)
        elif kind == "copy_in":
            copy_in(args[0], args[1])
        elif kind == "clear_partial":  # a leftover from an interrupted earlier download
            p = _partial(args[0])
            shutil.rmtree(p, ignore_errors=True)
            p.parent.mkdir(parents=True, exist_ok=True)
        elif kind == "move_in":  # a finished download into ~/.config/omarchy/themes, in one step
            src, dest = _partial(args[0]), Path(args[1])
            if dest.parent != data.USER_THEMES or dest.exists():
                raise NotAllowed(f"won't move a download onto {dest}")
            if not data.theme_has_files(src):
                raise StepFailed("the download came out empty")
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
        elif kind == "terminal":
            launch(["omarchy-launch-floating-terminal-with-presentation", args[0]])
        elif kind == "wait_package":
            wait_package(*args)
        elif kind == "use_font":
            use_font(*args)
        elif kind == "remember_text_size":
            remember_text_size()
        elif kind == "font_size":
            font_size(args[0])
        elif kind == "check_mono":
            note = check_mono(*args) or note
        elif kind == "take_out":
            take_out(args[0], args[1])
        elif kind == "delete_counting":
            delete_counting(args[0], progress)
        elif kind == "remove_file":
            p = _own_backgrounds(args[0])
            if not p.is_file():
                raise StepFailed(f"{p.name} is already gone")
            p.unlink()
        else:
            raise NotAllowed(f"unknown step {kind!r}")
    return note
