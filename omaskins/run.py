"""The only place OmaSkins changes the system.

An `Action` (data.py) describes its work as `steps`; `perform()` carries them out. Commands are
argument lists (never shell strings, so nothing in a theme name, path or URL can be read as extra
shell syntax) and only programs in ALLOWED may run, some with a stricter shape check. File steps
may only touch your own backgrounds folder. Everything else in the app just reads.

Live so far: batch 1, everyday actions that never ask for a password (themes: apply, add, remove
yours; backgrounds: set, add, copy, remove yours, open folder).

(data.py also calls a few programs, all read-only: pacman -Q, fc-list, hyprctl getoption, magick
for thumbnails in the app's own cache.)
"""

import shutil
import subprocess
from pathlib import Path

from . import data


def _git_clone_only(argv):
    # git clone -- <url> <folder in ~/.config/omarchy/themes>, the same clone omarchy-theme-install does
    return (len(argv) == 5 and argv[1:3] == ["clone", "--"]
            and Path(argv[4]).parent == data.USER_THEMES and not Path(argv[4]).exists())


ALLOWED = {
    "omarchy-theme-set": None,
    "omarchy-theme-remove": None,
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


def perform(steps):
    """Carry out an action's steps in order; stop at the first failure (StepFailed says why)."""
    for step in steps:
        kind, args = step[0], step[1:]
        if kind == "run":
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
        elif kind == "remove_file":
            p = _own_backgrounds(args[0])
            if not p.is_file():
                raise StepFailed(f"{p.name} is already gone")
            p.unlink()
        else:
            raise NotAllowed(f"unknown step {kind!r}")
