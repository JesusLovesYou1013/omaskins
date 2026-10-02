"""The only place OmaSkins changes the system.

An `Action` (data.py) describes its work as `steps`; `perform()` carries them out. Commands are
argument lists (never shell strings, so nothing in a theme name, path or URL can be read as extra
shell syntax) and only programs in ALLOWED may run, some with a stricter shape check. File steps
may only touch your own backgrounds folder, theme downloads (cloned into a holding folder, then
moved into ~/.config/omarchy/themes in one step once complete), and merge_aether (Aether's working copy
into the Aether theme it's a copy of, both checked Aether's). Everything else in the app just reads.

Live so far: batch 1, everyday actions that never ask for a password (themes: apply, add, remove
yours; backgrounds: set, add, copy, remove yours, open folder). Removing a theme is taken out of
themes/ in one rename, then deleted file by file so its bar counts down.
Batch 2, fonts: Use (omarchy-font-set, no password) and adding/removing Nerd Font packages, which
run in Omarchy's floating terminal where pacman asks for the password; OmaSkins waits for them.

(data.py also calls a few programs, all read-only: pacman -Q, fc-list, hyprctl getoption, magick
for thumbnails in the app's own cache, and `gh api graphql` for the themes' GitHub star counts.)
"""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import data


def _git_clone_only(argv):
    # git clone --progress -- <url> <new folder in the partial area>: exactly the clone
    # omarchy-theme-install does (--progress only adds the output the bar reads; owner, 2026-10-01:
    # theme downloads mirror Omarchy's own, full history included, never a faster variant of our own),
    # into a holding folder that is moved into ~/.config/omarchy/themes only once it is complete.
    return (len(argv) == 6 and argv[1:4] == ["clone", "--progress", "--"]
            and Path(argv[5]).parent == data.PARTIAL_THEMES and not Path(argv[5]).exists())


def _theme_removed_notice(argv):
    # The notification omarchy-theme-remove sends at the end, and nothing else.
    return len(argv) == 3 and argv[1] == "Theme removed"


def _one_arg(argv):
    return len(argv) == 2 and bool(argv[1].strip())


_FONT_TERMINAL = re.compile(r"echo '(Installing|Removing) (\S+(?: \S+)*)\.\.\.'; omarchy-pkg-(add|drop) (\S+(?: \S+)*)")


_BUILTIN_TERMINAL = re.compile(r"echo '(Removing|Restoring|Reinstalling) ([a-z0-9][a-z0-9._-]*)\.\.\.'; .*", re.S)


def _font_terminal(argv):
    # Omarchy's floating terminal, and only ever for one of two things, each command string rebuilt
    # from a checked name and required to come out identical: adding/dropping one Nerd Font package,
    # or hiding/restoring one built-in theme (move aside or back + its pacman.conf NoExtract rule).
    if len(argv) != 2:
        return False
    m = _FONT_TERMINAL.fullmatch(argv[1])
    if m:
        # one package, or several in one terminal (an import: one password prompt for all of them)
        names = m.group(2).split(" ")
        return bool(m.group(2) == m.group(4) and all(data.FONT_PKG_OK.fullmatch(n) for n in names)
                    and argv[1] == data.font_terminal_command(m.group(3), m.group(2)))
    lists = data.parse_password_terminal_head(argv[1])
    if lists is not None:
        # an import's password work, all in one terminal: rebuilt from its own list of items (each name
        # checked) and accepted only if it comes out identical
        try:
            return argv[1] == data.password_terminal_command(
                lists["add-fonts"], lists["drop-fonts"], lists["remove-themes"], lists["unhide-themes"],
                lists["reinstall-themes"], data.builtin_package())
        except ValueError:
            return False
    m = _BUILTIN_TERMINAL.fullmatch(argv[1])
    if not m:
        return False
    kind = {"Removing": "hide", "Restoring": "unhide", "Reinstalling": "reinstall"}[m.group(1)]
    try:
        return argv[1] == data.builtin_terminal_command(kind, m.group(2), data.builtin_package())
    except ValueError:
        return False


def _reload_signal(argv):
    # The live-reload nudges omarchy-font-set and omarchy-display-text-size send, and nothing else.
    return argv in (["pkill", "-USR1", "kitty"], ["pkill", "-SIGUSR2", "ghostty"])


_B64 = re.compile(r"[A-Za-z0-9+/=]*")


def _shell_restyle(argv):
    # The one omarchy-shell call OmaSkins makes: the "re-read your style" message Omarchy's own
    # theme switch sends (colors.toml / shell.toml of the current theme, base64), nothing else.
    return len(argv) == 5 and argv[1:3] == ["shell", "applyTheme"] and all(_B64.fullmatch(a) for a in argv[3:])


ALLOWED = {
    "omarchy-theme-set": None,
    "omarchy-shell": _shell_restyle,
    "hyprctl": lambda argv: argv == ["hyprctl", "reload"],
    "pkill": _reload_signal,
    "omarchy-font-set": _one_arg,
    "omarchy-launch-floating-terminal-with-presentation": _font_terminal,
    "omarchy-notification-send": _theme_removed_notice,
    "omarchy-theme-bg-set": None,
    # an import: the Dawn & Dusk location, through Omarchy's own command (only when none is set)
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
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, env=data.child_env())


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


DOWNLOAD_STALL = 120       # seconds with nothing at all from a download: it has stopped
DOWNLOAD_LIMIT = 20 * 60   # the longest one download may take (owner, 2026-10-01: 20 minutes)

# "Receiving objects:  66% (40/60), 2.58 MiB | 2.52 MiB/s": git's own count of what has arrived and
# how fast it's arriving now.
_RECEIVED = re.compile(r"Receiving objects:.*?,\s*([\d.]+)\s*(bytes|KiB|MiB|GiB)"
                       r"(?:\s*\|\s*([\d.]+)\s*(bytes|KiB|MiB|GiB)/s)?")
_UNIT = {"bytes": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3}


def clone_transfer(line):
    """(bytes received, bytes per second or None) from one git clone --progress line, else None."""
    m = _RECEIVED.search(line)
    if not m:
        return None
    got = float(m.group(1)) * _UNIT[m.group(2)]
    rate = float(m.group(3)) * _UNIT[m.group(4)] if m.group(3) else None
    return int(got), rate


def run_with_progress(argv, progress, stall=DOWNLOAD_STALL, limit=DOWNLOAD_LIMIT, transfer=None):
    """Like run(), but reads the command's progress lines as they come (git writes them to stderr,
    each update ending in \r) and calls progress(percent) whenever the overall figure goes up.
    transfer(bytes received, bytes per second), if given, hears git's own figures as they change.
    Stopped when it sends nothing at all for `stall` seconds, or once it has taken `limit` seconds."""
    check(argv)
    p = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE, env=data.child_env())
    start = time.monotonic()
    last = [start]
    why = []

    def watchdog():
        while p.poll() is None:
            now = time.monotonic()
            if now - last[0] > stall:
                why.append(f"stopped: nothing arrived for {stall // 60} minutes")
            elif now - start > limit:
                why.append(f"stopped: still not finished after {limit // 60} minutes")
            if why:
                p.kill()
                return
            time.sleep(1)
    threading.Thread(target=watchdog, daemon=True).start()
    lines, buf, best = [], b"", -1
    while chunk := p.stderr.read1(4096):
        last[0] = time.monotonic()
        *done, buf = re.split(rb"[\r\n]", buf + chunk)
        for raw in done:
            line = raw.decode(errors="replace")
            if line.strip():
                lines.append(line)
            pct = clone_percent(line)
            if pct is not None and pct > best:
                best = pct
                progress(pct)
            if transfer and (got := clone_transfer(line)):
                transfer(*got)
    p.stderr.close()
    p.wait()
    if buf.strip():
        lines.append(buf.decode(errors="replace"))
    lines += why
    if p.returncode == 0 and best < 100:
        progress(100)  # small repos skip the later phases; a finished download is a full bar
    return subprocess.CompletedProcess(argv, p.returncode, "", "\n".join(lines[-20:]))


def launch(argv):
    """Start an allow-listed app (a file manager) and don't wait for it."""
    check(argv)
    subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, env=data.child_env())


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


AETHER_MERGED = data.CACHE_DIR / "aether-merged"   # a copy of both folders as they were, just in case


def set_theme(name, background=None):
    """Switch to a theme; with a background, that very one (set first, then the colours with Omarchy
    picking none: OMARCHY_THEME_SKIP_BACKGROUND), as the rotation does."""
    env = data.child_env()
    if background:
        check(["omarchy-theme-bg-set", str(background)])
        subprocess.run(["omarchy-theme-bg-set", str(background)], capture_output=True, timeout=60, env=env)
        env["OMARCHY_THEME_SKIP_BACKGROUND"] = "1"
    check(["omarchy-theme-set", name])
    r = subprocess.run(["omarchy-theme-set", name], capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        raise StepFailed(f"couldn't switch to {name}: {r.stderr.strip()[-200:]}")


def merge_aether(named, picked=False):
    """Combine Aether's working copy (themes/aether) into the named Aether theme it's a copy of
    (owner, 2026-10-01: Aether's plain Apply always writes that one folder, so Omarchy's own menu and
    OmaSkins showed the same theme twice):
    - colours and settings (every file but the pictures) from whichever folder is newer;
    - pictures: both sets in the named folder, compared by content so none is kept twice; a different
      picture with a name already taken is kept under a new name;
    - if the working copy was the current theme: switch to the combined theme keeping the very same
      background (Omarchy picks none: OMARCHY_THEME_SKIP_BACKGROUND);
    - rotation entries for the working copy move to the named theme; then the working copy goes.
    Returns a note for the window."""
    # The sure match, or (picked=True) the theme you chose when OmaSkins asked which one it belongs to.
    chosen = next((c for c in data.aether_candidates() if c["name"] == named), None)
    if not chosen or not (chosen["sure"] or picked) or not data.THEME_NAME_OK.fullmatch(named):
        raise NotAllowed(f"themes/{data.AETHER_SCRATCH} isn't a working copy of {named!r}")
    twin = (named, chosen["newer"])
    scratch, target = data.USER_THEMES / data.AETHER_SCRATCH, data.USER_THEMES / named
    for folder in (scratch, target):
        if folder.is_symlink() or not (folder / ".aether-managed").is_file():
            raise NotAllowed(f"{folder.name} isn't a theme folder Aether made")
    keep = AETHER_MERGED / time.strftime("%Y%m%d-%H%M%S")
    shutil.copytree(scratch, keep / data.AETHER_SCRATCH, symlinks=True)
    shutil.copytree(target, keep / named, symlinks=True)

    if twin[1]:  # the working copy is newer: its colours and settings replace the named folder's
        for old in target.iterdir():
            if old.name not in ("backgrounds", ".aether-managed") and not (scratch / old.name).exists():
                shutil.rmtree(old) if old.is_dir() and not old.is_symlink() else old.unlink()
        for new in scratch.iterdir():
            if new.name in ("backgrounds", ".aether-managed") or new.is_symlink():
                continue
            dest = target / new.name
            if new.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(new, dest)
            else:
                shutil.copy2(new, dest)

    # Pictures: one list, no picture twice (by content), nothing overwritten.
    moved = {}                       # working-copy picture -> the same picture in the named folder
    have = {}
    (target / "backgrounds").mkdir(exist_ok=True)
    for p in data._images(target / "backgrounds"):
        have.setdefault(hashlib.sha1(p.read_bytes()).hexdigest(), p)
    for p in data._images(scratch / "backgrounds"):
        digest = hashlib.sha1(p.read_bytes()).hexdigest()
        if digest not in have:
            dest, n = target / "backgrounds" / p.name, 2
            while dest.exists():
                dest = target / "backgrounds" / f"{p.stem}-{n}{p.suffix}"
                n += 1
            shutil.copy2(p, dest)
            have[digest] = dest
        moved[os.path.realpath(p)] = str(have[digest])

    # The current theme: the combined one, the very same background.
    current = (data.STATE_DIR / "theme.name").read_text().strip() if (data.STATE_DIR / "theme.name").exists() else ""
    if current == data.AETHER_SCRATCH:
        link = data.STATE_DIR / "background"
        shown = os.path.realpath(link) if link.exists() else ""
        digest = hashlib.sha1(Path(shown).read_bytes()).hexdigest() if shown and Path(shown).is_file() else ""
        same = str(have.get(digest, "")) if digest else ""
        if same:
            check(["omarchy-theme-bg-set", same])
            subprocess.run(["omarchy-theme-bg-set", same], capture_output=True, timeout=60, env=data.child_env())
        check(["omarchy-theme-set", named])
        env = data.child_env()
        if same:
            env["OMARCHY_THEME_SKIP_BACKGROUND"] = "1"   # keep the background just set
        r = subprocess.run(["omarchy-theme-set", named], capture_output=True, text=True, timeout=300, env=env)
        if r.returncode != 0:
            raise StepFailed(f"couldn't switch to {named}: {r.stderr.strip()[-200:]}")

    # Rotation entries for the working copy now mean the named theme.
    plan = data.load_rotation()
    changed = False
    for period, names in plan.checked.items():
        if data.AETHER_SCRATCH in names:
            plan.checked[period] = list(dict.fromkeys(named if n == data.AETHER_SCRATCH else n for n in names))
            changed = True
    if data.AETHER_SCRATCH in plan.theme_picks:
        picks = plan.theme_picks.pop(data.AETHER_SCRATCH)
        plan.theme_picks.setdefault(named, set()).update(Path(moved.get(os.path.realpath(p), p)) for p in picks)
        changed = True
    if plan.solo_picks:
        new = {Path(moved.get(os.path.realpath(p), p)) for p in plan.solo_picks}
        changed |= new != plan.solo_picks
        plan.solo_picks = new
    if changed:
        data.save_rotation(plan)

    shutil.rmtree(scratch)
    return None


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
    return subprocess.run(["pacman", "-Q", pkg], capture_output=True, env=data.child_env()).returncode == 0


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


def _wait_for_terminal(done, working, term_cmd, poll=1.0, grace=10.0, give_up=900.0, cmdlines=_cmdlines):
    """Wait while Omarchy's floating terminal does its work: finished once done() and nothing is still
    `working`; a clear failure if the terminal closes with the job not done (closed, wrong password)."""
    start = time.monotonic()
    while True:
        procs = cmdlines()
        busy = working(procs)
        if done() and not busy:
            return
        elapsed = time.monotonic() - start
        if elapsed > grace and not busy and not any(term_cmd in c for c in procs):
            raise StepFailed("the terminal closed before it finished (nothing changed)")
        if elapsed > give_up:
            raise StepFailed("gave up waiting for the terminal")
        time.sleep(poll)


def _procs():
    """(pid, parent pid, command line) of every process (read from /proc, nothing run)."""
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                stat = (d / "stat").read_text()
                ppid = int(stat[stat.rindex(")") + 2:].split()[1])
                cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
                out.append((int(d.name), ppid, cmd))
            except (OSError, ValueError, IndexError):
                pass
    return out


def close_terminal(term_cmd, procs=_procs, kill=os.kill):
    """Close the terminal window running `term_cmd` (the import's password terminal): its outermost
    process, so the window goes and everything in it stops; nothing else is touched."""
    ps = procs()
    mine = {pid: ppid for pid, ppid, c in ps if term_cmd in c}
    for pid, ppid in mine.items():
        if ppid not in mine:
            try:
                kill(pid, signal.SIGTERM)
            except OSError:
                pass


def import_password_checks(lists):
    """{item: done?} for each thing an import's password terminal is doing (all readable without root)."""
    out = {}
    for p in lists["add-fonts"]:
        out[("add-font", p)] = lambda p=p: package_installed(p)
    for p in lists["drop-fonts"]:
        out[("drop-font", p)] = lambda p=p: not package_installed(p)
    for n in lists["remove-themes"]:
        out[("remove-theme", n)] = lambda n=n: builtin_state(n) == (False, True)
    for n in lists["unhide-themes"] + lists["reinstall-themes"]:
        out[("restore-theme", n)] = lambda n=n: builtin_state(n) == (True, False)
    return out


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def clear_password_marks(mark=None):
    mark = Path(mark or data.PASSWORD_MARK)
    for end in (".started", ".done"):
        Path(f"{mark}{end}").unlink(missing_ok=True)


def omarchy_terminal_open():
    """True while one of Omarchy's own installer terminals (class org.omarchy.terminal) is on screen."""
    try:
        out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=5,
                             env=data.child_env()).stdout
        return any(c.get("class") == "org.omarchy.terminal" for c in json.loads(out or "[]"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def wait_import_password(lists, mark=None, poll=1.0, grace=60.0, give_up=1800.0, settle=5.0, checks=None,
                         alive=_alive, window_open=omarchy_terminal_open):
    """Wait for an import's one password terminal and say how each item came out: ({item: True/False},
    why it ended). The terminal itself writes <mark>.started (its process id) and <mark>.done ("done", or
    "cancelled" for Ctrl+C at the password); closing the window ends that process.
    Never decides early (2026-10-02: it once gave up 30 s in, while Omarchy's terminal was still coming up,
    and reported a restore that then happened as not done): while the terminal hasn't said it started, it
    keeps waiting as long as an Omarchy terminal window is open, and only calls it "never started" after
    `grace` seconds with no such window at all. Once it's over it looks at the disk again for up to
    `settle` seconds, so the result always says what really happened."""
    mark = Path(mark or data.PASSWORD_MARK)
    started, finished = Path(f"{mark}.started"), Path(f"{mark}.done")
    checks = checks or import_password_checks(lists)
    start = last_window = time.monotonic()

    def result():
        return {k: bool(f()) for k, f in checks.items()}

    def final(why):
        end = time.monotonic() + settle
        r = result()
        while not all(r.values()) and time.monotonic() < end:
            time.sleep(min(poll, 0.5) or 0.05)
            r = result()
        return r, why
    while True:
        r = result()
        if all(r.values()):
            return r, "all done"
        if finished.exists():
            return final("terminal finished: " + finished.read_text().strip())
        try:
            pid = int(started.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            pid = None
        now = time.monotonic()
        if pid is not None and not alive(pid):
            return final("terminal closed")
        if pid is None:
            if window_open():
                last_window = now
            elif now - last_window > grace:
                return final("terminal never started")
        if now - start > give_up:
            return final("gave up waiting")
        time.sleep(poll)


def wait_package(pkg, want_installed, term_cmd, poll=1.0, grace=10.0, give_up=900.0,
                 installed=package_installed, cmdlines=_cmdlines):
    """Done once pacman has the package added (or dropped) AND the add/drop script has exited (so the
    font cache is rebuilt too)."""
    verb = "add" if want_installed else "drop"
    _wait_for_terminal(lambda: installed(pkg) == want_installed,
                       lambda procs: any(c.endswith(f"omarchy-pkg-{verb} {pkg}") for c in procs),
                       term_cmd, poll, grace, give_up, cmdlines)


def builtin_state(name):
    """(folder in Omarchy's themes, its NoExtract rule in pacman.conf) -- both readable without root."""
    try:
        rule = data._no_extract(name) in data.PACMAN_CONF.read_text().splitlines()
    except OSError:
        rule = False
    return (data.BUILTIN_THEMES / name).is_dir(), rule


def wait_builtin(name, hide, term_cmd, poll=1.0, grace=10.0, give_up=900.0, cmdlines=_cmdlines):
    """Hidden = folder gone from Omarchy's themes + rule in pacman.conf; restored = folder back + rule
    gone, and pacman (a reinstall) finished."""
    want = (False, True) if hide else (True, False)
    pacman_busy = lambda procs: any(re.search(r"(^|/)pacman -S ", c) for c in procs)  # noqa: E731
    _wait_for_terminal(lambda: builtin_state(name) == want, pacman_busy, term_cmd, poll, grace, give_up, cmdlines)


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


def write_corners(on, px):
    """OmaSkins' own ~/.config/hypr/omaskins.lua, then (once) the require line at the end of your
    looknfeel.lua: after the theme's own look, so it wins for every theme; before Omarchy's toggles,
    so "no gaps" can still square the corners. Nothing else in ~/.config/hypr is touched."""
    looknfeel = data.HYPR_DIR / "looknfeel.lua"
    if not looknfeel.is_file():
        raise StepFailed("~/.config/hypr/looknfeel.lua is missing, so there's nowhere to load the corners from")
    tmp = data.CORNERS_FILE.with_name(".omaskins.lua.tmp")
    tmp.write_text(data.corners_file_text(on, max(0, min(data.CORNERS_MAX, int(px)))))
    tmp.replace(data.CORNERS_FILE)  # the file exists before anything requires it
    text = looknfeel.read_text()
    if data.CORNERS_REQUIRE not in (l.strip() for l in text.splitlines()):
        with looknfeel.open("a") as f:
            f.write(("" if text.endswith("\n") or not text else "\n")
                    + "\n-- Added by OmaSkins Manager: Rounded Corners and Transparency (set them in OmaSkins).\n"
                    + data.CORNERS_REQUIRE + "\n")


def apply_corners(on, px, rounding=None, wait=2.0, poll=0.1):
    """Save the corners and wait until Hyprland shows the new radius. Saving a file Hyprland loads
    makes it reload by itself; a second, forced reload would blank this VM's screen twice. Only if
    nothing changed within `wait` seconds is `hyprctl reload` run."""
    rounding = rounding or data.current_rounding
    before = rounding()
    write_corners(on, px)
    px = max(0, min(data.CORNERS_MAX, int(px)))
    live = (lambda r: r == px) if on else (lambda r: r != before)
    if on and before == px:
        return  # already that radius: nothing for Hyprland to change
    for forced in (False, True):
        if forced:
            run(["hyprctl", "reload"])
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if live(rounding()):
                return
            time.sleep(poll)
    # Off can legitimately end on the same radius (the theme's own equals the old one): not an error.


def hypr_eval(code):
    """One Lua statement for the running Hyprland (built here from numbers and fixed names only)."""
    return subprocess.run(["hyprctl", "eval", code], capture_output=True, text=True, timeout=5,
                          env=data.child_env()).stdout


def hypr_clients():
    try:
        return json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True,
                                         timeout=5, env=data.child_env()).stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _tags(client):
    return [t.rstrip("*") for t in client.get("tags", [])]


def nautilus_windows():
    """{window: [folder URIs, one per tab]} from Nautilus itself (D-Bus), {} when it isn't running."""
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                              "--object-path", "/org/freedesktop/FileManager1", "--method",
                              "org.freedesktop.DBus.Properties.Get", "org.freedesktop.FileManager1",
                              "OpenWindowsWithLocations"], capture_output=True, text=True, timeout=5,
                             env=data.child_env())
    except (OSError, subprocess.SubprocessError):
        return {}
    found = re.findall(r"'(/org/gnome/Nautilus/window/\d+)': \[([^\]]*)\]", out.stdout)
    return {w: re.findall(r"'([^']+)'", uris) for w, uris in found}


def nautilus_restart(windows, evaluate=None, clients=None, sleep=time.sleep, quit_=None, open_=None):
    """Quit Nautilus and reopen each of its windows on the same folders and workspaces, so it reads its
    stylesheet again (GTK reads it only when Nautilus starts)."""
    evaluate = evaluate or hypr_eval
    clients = clients or hypr_clients
    quit_ = quit_ or (lambda: subprocess.run(["nautilus", "-q"], capture_output=True, timeout=10, env=data.child_env()))
    open_ = open_ or (lambda uris: subprocess.Popen(["uwsm-app", "--", "nautilus", "--new-window", *uris],
                                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                    start_new_session=True, env=data.child_env()))
    where = [(c.get("title", ""), c["workspace"]["name"]) for c in clients()   # to put each window back
             if c.get("class") == "org.gnome.Nautilus"]
    quit_()
    for _ in range(30):
        if not subprocess.run(["pgrep", "-x", "nautilus"], capture_output=True).stdout.strip():
            break
        sleep(0.1)
    before = {c["address"] for c in clients()}
    new = []
    # The first window starts Nautilus; the rest only once its window is up: launched together while it
    # starts, each launch opened two windows (owner, 2026-10-02: two windows came back as four).
    for k, uris in enumerate(u for u in windows.values() if u):
        open_(uris)
        if k == 0:
            for _ in range(50):
                if any(c.get("class") == "org.gnome.Nautilus" and c["address"] not in before for c in clients()):
                    break
                sleep(0.1)
    for _ in range(40):   # each window back on its workspace, as Nautilus opens them
        new = [c for c in clients() if c.get("class") == "org.gnome.Nautilus" and c["address"] not in before]
        if len(new) >= len(windows):
            break
        sleep(0.1)
    # A new window has no title for a moment: wait for them (owner, 2026-10-02: one stayed where it opened).
    for _ in range(20):
        if all(c.get("title") for c in new):
            break
        sleep(0.1)
        fresh = {c["address"]: c for c in clients()}
        new = [fresh.get(c["address"], c) for c in new]
    # Each old window's place is used once: two windows on the same folder (same title) each get their own.
    unused = list(where)
    for c in new:
        match = next((w for w in unused if w[0] == c.get("title", "")), None) or (unused[0] if unused else None)
        if match is None:
            break
        unused.remove(match)
        ws = match[1]
        if ws and c["workspace"]["name"] != ws:
            evaluate(f"hl.dispatch(hl.dsp.window.move({{ workspace = '{ws}', follow = false, window = 'address:{c['address']}' }}))")


# Hyprland keeps every window rule until it next loads its config (no remove, but each rule can be switched
# off), and a window keeps every tag it's given. So the fade keeps one rule per opacity level, made once and
# reused (held in Hyprland's own Lua state, which a reload clears along with the rules), and a window carries
# at most one fade tag while fading and none after. A rule is newest-wins: only one "default-opacity" rule of
# ours is switched on at a time.
FADE_LUA = """
if not omaskins_fade then
  omaskins_fade = { levels = {}, default = {} }
end
local F = omaskins_fade
local function level(a, b)
  local key = string.format("%.4f %.4f", a, b)
  local tag = "omaskins-o" .. key:gsub("[ .]", "")
  if not F.levels[key] then
    F.levels[key] = hl.window_rule({ match = { tag = tag }, opacity = key })
  end
  return tag
end
local function default_rule(a, b)
  local key = a and string.format("%.4f %.4f", a, b)
  for k, r in pairs(F.default) do
    r:set_enabled(k == key)
  end
  if key and not F.default[key] then
    F.default[key] = hl.window_rule({ match = { tag = "default-opacity" }, opacity = key })
  end
end
local function nautilus(own, a, b)
  F.nautilus = F.nautilus or {}
  local key = own and "own" or "omarchy"
  for k, r in pairs(F.nautilus) do
    r:set_enabled(k == key)
  end
  if not F.nautilus[key] then
    local m = { class = "^org.gnome.Nautilus$" }
    F.nautilus[key] = own and hl.window_rule({ match = m, tag = "-default-opacity", opacity = "1 1" })
      or hl.window_rule({ match = m, opacity = a .. " " .. b })
  end
end
local function shell_blur(on)
  -- Blur behind Omarchy's menus, panels and notifications (see data.SHELL_BLUR_RULE).
  if not F.shell_blur then
    F.shell_blur = """ + data.SHELL_BLUR_RULE + """
  end
  F.shell_blur:set_enabled(on)
end
local function dialogs(own)
  -- File dialogs (Omarchy's dialog service) out of Hyprland's fade while OmaSkins styles them.
  if not F.dialogs then
    F.dialogs = hl.window_rule({ match = { class = "^xdg-desktop-portal-gtk$" }, tag = "-default-opacity", opacity = "1 1" })
  end
  F.dialogs:set_enabled(own)
end
local function retag(windows, tag)
  for _, w in ipairs(windows) do
    local addr = "address:" .. w
    if tag then
      hl.dispatch(hl.dsp.window.tag({ window = addr, tag = "+" .. tag }))
    end
    for _, t in ipairs(F.on and F.on[w] or {}) do
      if t ~= tag then
        hl.dispatch(hl.dsp.window.tag({ window = addr, tag = "-" .. t }))
      end
    end
    F.on = F.on or {}
    F.on[w] = tag and { tag } or nil
  end
end
"""


def _lua_list(items):
    return "{ " + ", ".join(f"'{i}'" for i in items) + " }"


def apply_transparency(step, fade=1.5, frames=10, evaluate=None, clients=None, sleep=time.sleep):
    """Fade every see-through window (Omarchy's "default-opacity" tag) to `step` over `fade` seconds,
    live, without a Hyprland reload (in this VM a reload blanks the screen): each frame moves the windows
    to the tag of the in-between level. New windows get the new values from a rule too. Then the step is
    saved for the next time Hyprland loads (omaskins.lua reads it; saving it doesn't make Hyprland reload)."""
    evaluate = evaluate or hypr_eval
    clients = clients or hypr_clients
    before, after = data.transparency_values(data.transparency_step()), data.transparency_values(step)
    open_windows = clients()
    windows = [c["address"] for c in open_windows if "default-opacity" in _tags(c)   # Nautilus and file
               and c.get("class") not in ("org.gnome.Nautilus", data.DIALOG_CLASS)]  # dialogs do their own
    # Tags left by the fade OmaSkins used before 2026-10-02 (a new set every move): cleared once.
    stale = {c["address"]: [t for t in _tags(c) if t.startswith("omaskins-t")] for c in open_windows}
    if after[2] and not before[2]:
        evaluate(data.TRANSPARENCY_BLUR)          # blur first, so it's there as the windows fade
    wl = _lua_list(windows)
    for k in range(1, frames + 1):
        a = before[0] + (after[0] - before[0]) * k / frames
        b = before[1] + (after[1] - before[1]) * k / frames
        # First frame: our "default-opacity" rule off, so the frame's level is the newest rule that applies.
        evaluate(FADE_LUA + ("default_rule(nil)\n" if k == 1 else "") + f"retag({wl}, level({a:.4f}, {b:.4f}))")
        sleep(fade / frames)
    # The end level for these windows and the ones opened from now on (until Hyprland next loads
    # omaskins.lua, which says the same), then the windows lose their fade tag.
    evaluate(FADE_LUA + f"default_rule({after[0]:.4f}, {after[1]:.4f})\nretag({wl}, nil)")
    for address, tags in stale.items():
        for tag in tags:
            evaluate(f"hl.dispatch(hl.dsp.window.tag({{ window = 'address:{address}', tag = '-{tag}' }}))")
    if before[2] and not after[2]:
        evaluate("hl.config({ decoration = { blur = { enabled = false } } })")
    # Nautilus: its own stylesheet (sidebar a little more solid, icons solid) instead of Hyprland's
    # whole-window fade; back to Hyprland's at the default step. GTK reads it only at start: reopen it.
    if step == data.TRANSPARENCY_DEFAULT:
        evaluate(FADE_LUA + f"nautilus(false, '{data.OMARCHY_OPACITY[0]:g}', '{data.OMARCHY_OPACITY[1]:g}')")
    else:
        evaluate(FADE_LUA + "nautilus(true)")
    # Omarchy's menus, panels and notifications: the shell re-reads its file live; blur behind them with
    # the windows' blur.
    data.write_shell_block(step)
    evaluate(FADE_LUA + f"shell_blur({'true' if after[2] else 'false'})")
    if data.write_nautilus_css(step):
        open_windows = nautilus_windows()
        if open_windows:
            nautilus_restart(open_windows, evaluate, clients, sleep)
    data.save_transparency(step)
    update_hypr_file()


def set_qt_apps(on):
    from . import qtstyle
    qtstyle.save_enabled(on)
    if not on:
        qtstyle.remove()
        update_hypr_file()
    # On: the rotation engine builds the style, then updates omaskins.lua (it watches for this).


def dialog_service_running():
    try:
        return subprocess.run(["systemctl", "--user", "is-active", "--quiet", data.DIALOG_CLASS + ".service"],
                              timeout=5, env=data.child_env()).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def restart_dialog_service():
    """GTK3 reads its stylesheet when the dialog service starts: restarted while no dialog is open (nothing on
    screen; Omarchy's system starts it again on its own when it's next needed)."""
    subprocess.run(["systemctl", "--user", "try-restart", data.DIALOG_CLASS + ".service"], capture_output=True,
                   timeout=15, env=data.child_env())


def update_hypr_file():
    """omaskins.lua brought up to date when OmaSkins' Hyprland part changed (a file Hyprland loads: that one
    save reloads it); otherwise it's left alone, so changing the step never reloads. True when written."""
    on, px = data.corners_setting()
    px = px if px is not None else data.CORNERS_DEFAULT
    try:
        current = data.CORNERS_FILE.read_text()
    except OSError:
        current = ""
    if current == data.corners_file_text(on, px) or not (data.HYPR_DIR / "looknfeel.lua").is_file():
        return False
    write_corners(on, px)
    return True


def shell_restyle(theme_dir=None):
    """Ask the running Omarchy shell to re-read its style (it re-asks Hyprland for the corner
    radius), with the current theme's colours, the same call omarchy-theme-set makes. No restart."""
    import base64
    theme_dir = Path(theme_dir or data.STATE_DIR / "theme")
    payload = []
    for name in ("colors.toml", "shell.toml"):
        f = theme_dir / name
        payload.append(base64.b64encode(f.read_bytes()).decode() if f.is_file() else "")
    run(["omarchy-shell", "shell", "applyTheme", *payload])


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


def perform(steps, progress=None, notify=None, transfer=None):
    """Carry out an action's steps in order; stop at the first failure (StepFailed says why).
    A failed or refused theme download never leaves its half-finished folder behind.
    progress(percent), if given, hears how a theme download is going (called on this thread)."""
    try:
        return _perform(steps, progress, notify, transfer)
    except Exception:
        for step in steps:
            if step[0] == "clear_partial":
                shutil.rmtree(_partial(step[1]), ignore_errors=True)
        raise
    finally:
        for step in steps:
            if step[0] == "delete_counting":  # whatever a failed removal left behind
                shutil.rmtree(_removing(step[1]), ignore_errors=True)
        # The holding folders themselves go too once empty (owner, 2026-10-01: an import left an empty
        # ~/.config/omarchy/.omaskins-partial behind). Only when empty: another download may be using it.
        for holding in (data.PARTIAL_THEMES, data.REMOVING_THEMES):
            try:
                holding.rmdir()
            except OSError:
                pass


def _perform(steps, progress=None, notify=None, transfer=None):
    """Returns a note to show instead of the usual "done" message, if a step had one."""
    note = None
    for step in steps:
        kind, args = step[0], step[1:]
        if kind == "run":
            if progress and args[0][:2] == ["git", "clone"]:
                r = run_with_progress(args[0], progress, transfer=transfer)
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
        elif kind == "hide_in_omaskins":
            data.set_hidden_in_omaskins(args[0], args[1])
        elif kind == "note_removed_version":
            data.note_removed_version(*args)
        elif kind == "forget_removed_version":
            data.forget_removed_version(*args)
        elif kind == "wait_builtin":
            wait_builtin(*args)
        elif kind == "write_corners":
            write_corners(*args)
        elif kind == "apply_corners":
            apply_corners(*args)
        elif kind == "apply_transparency":
            apply_transparency(*args)
        elif kind == "qt_apps":
            set_qt_apps(*args)
        elif kind == "notify":  # tell the window something is live now (it restyles itself)
            if notify:
                notify(args[0])
        elif kind == "shell_restyle":
            shell_restyle()
        elif kind == "remember_text_size":
            remember_text_size()
        elif kind == "font_size":
            font_size(args[0])
        elif kind == "check_mono":
            note = check_mono(*args) or note
        elif kind == "take_out":
            take_out(args[0], args[1])
        elif kind == "merge_aether":
            note = merge_aether(args[0], *args[1:]) or note
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
