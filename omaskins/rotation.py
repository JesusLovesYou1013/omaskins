"""The rotation engine: changes theme and background on the Rotation tab's schedule, OmaSkins open or not.

Run by the OmaSkins service plugin (Service.qml) as `omaskins-rotate run`. It runs on the clock, not a
timer (owner's design): one interval for both switches, and a change lands when the time of day is a
multiple of it, counted from midnight (20 min = :00, :20, :40). Every TICK seconds it only compares
the clock and three small files with the last look (microseconds); it does real work once a minute,
when something changed, in the GRACE before a slot (a desktop glance every LOOK seconds) and at slots.

A slot is skipped (nothing changes; the next slot tries again) when:
- the screen is locked, the screensaver runs, or an app is fullscreen (no cost under a game or video),
  or one of those ended less than GRACE ago (seen by the glances before the slot);
- you arrived at the desktop (login, reboot, waking from sleep) less than GRACE ago;
- it's the first slot after you picked a theme or background yourself with Omarchy's own switchers
  (left untouched; the engine only notices the change): your pick skips the next rotation, and a
  notification says until when it stays.

What a slot changes, and how:
- Only the backgrounds you picked, the way you picked them: themes change with Omarchy's
  OMARCHY_THEME_SKIP_BACKGROUND (Omarchy never picks one); the new theme's bright background, if any,
  lands together with its colours. See change() for the order.
- Changes go through Omarchy's own `omarchy-theme-set` and `omarchy-theme-bg-set`.
- Dawn & Dusk = sunrise and sunset where you are (data.sun_times_now, worked out once per boot).
  They only decide which list the next regular slot picks from; they never cause a change of their
  own (owner's rule). If the current theme isn't in that list, that slot changes the theme.
"""

import fcntl
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import data, qtstyle

TICK = 5                     # seconds between looks at the clock
GRACE = 5 * 60               # no change within 5 min of arriving or coming back
LOOK = 30                    # in the GRACE before a slot, glance at the desktop this often
LOG_FILE = data.OMASKINS_STATE / "rotation.log"
LOG_MAX = 256 * 1024
SCREENSAVER_CLASS = "org.omarchy.screensaver"
SHELL_JSON = Path(os.environ.get("XDG_CONFIG_HOME", data.HOME / ".config")) / "omarchy" / "shell.json"


def log(*parts):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX:
            keep = LOG_FILE.read_bytes()[-LOG_MAX // 2:]
            LOG_FILE.write_bytes(keep[keep.find(b"\n") + 1:])
        with LOG_FILE.open("a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(p) for p in parts) + "\n")
    except OSError:
        pass


def real(path):
    return os.path.realpath(path) if path else ""


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def canonical_background(theme_name):
    """The current background as a real file path. For the current theme Omarchy points the link at
    its copy in state/current/theme; that copy is mapped back to the theme's own file, so it matches
    the paths the Rotation tab picked."""
    link = data.STATE_DIR / "background"
    target = real(link) if link.is_symlink() or link.exists() else ""
    copies = real(data.STATE_DIR / "theme" / "backgrounds")
    if target and os.path.dirname(target) == copies and theme_name:
        for root in (data.USER_THEMES, data.BUILTIN_THEMES):
            own = root / theme_name / "backgrounds" / os.path.basename(target)
            if own.is_file():
                return real(own)
    return target


def fullscreen_on_screen(monitors, clients):
    """A window covering a monitor completely (Hyprland fullscreen mode 2, not "maximised", which
    leaves the bar showing) on a workspace that monitor is showing: a game or a video."""
    shown = set()
    for m in monitors:
        for key in ("activeWorkspace", "specialWorkspace"):
            ws = m.get(key) or {}
            if ws.get("id"):
                shown.add(ws["id"])
    return any(int(c.get("fullscreen") or 0) & 2 and (c.get("workspace") or {}).get("id") in shown for c in clients)


class Desktop:
    """Everything the engine asks of the running desktop, in one place (tests replace it)."""

    def away(self, sure=False):
        """Why nobody would see a change now: "locked", "screensaver", "fullscreen", or "". One batched
        hyprctl call (~13 ms) answers all three; the lock the way Omarchy's own
        omarchy-hyprland-session-locked reads it (LOCK among a monitor's solitaryBlockedBy). `sure`
        (at a slot) also asks the shell's lock screen directly (~57 ms, so not on every glance).
        Unknown counts as here, so a desktop that doesn't answer never stalls the rotation for good."""
        monitors, clients = self._snapshot()
        if any("LOCK" in (m.get("solitaryBlockedBy") or []) for m in monitors) or (sure and self._shell_locked()):
            return "locked"
        if any(c.get("class") == SCREENSAVER_CLASS for c in clients):
            return "screensaver"
        return "fullscreen" if fullscreen_on_screen(monitors, clients) else ""

    def _snapshot(self):
        """(monitors, clients) from one `hyprctl --batch` call; ([], []) if it can't be asked."""
        try:
            out = subprocess.run(["hyprctl", "--batch", "j/monitors;j/clients"], capture_output=True, text=True,
                                 timeout=5).stdout
            dec, docs, i = json.JSONDecoder(), [], 0
            while len(docs) < 2:
                while i < len(out) and out[i].isspace():
                    i += 1
                doc, i = dec.raw_decode(out, i)
                docs.append([d for d in doc if isinstance(d, dict)] if isinstance(doc, list) else [])
            return docs[0], docs[1]
        except (OSError, subprocess.SubprocessError, ValueError):
            return [], []

    def _shell_locked(self):
        try:
            return subprocess.run(["omarchy-shell", "lock", "isLocked"], capture_output=True, text=True,
                                  timeout=5).stdout.strip() == "true"
        except (OSError, subprocess.SubprocessError):
            return False

    def set_theme(self, name, background=None):
        """Apply a theme's colours without Omarchy picking a background (OMARCHY_THEME_SKIP_BACKGROUND,
        the same switch omarchy-theme-refresh uses). `background`, if given, goes in the moment the new
        colours do (theme.name is written just before them), so both land together."""
        env = dict(os.environ, OMARCHY_THEME_SKIP_BACKGROUND="1")
        try:
            # Aether's newer working copy of this theme, if there is one (data.theme_folder_for)
            proc = subprocess.Popen(["omarchy-theme-set", data.theme_folder_for(name)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if background:
                deadline = time.time() + 30
                while proc.poll() is None and time.time() < deadline and data.current_theme_name() != name:
                    time.sleep(0.05)
                if data.current_theme_name() == name:
                    self.set_background(background)
            return proc.wait(timeout=180) == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def apply_theme(self, name):
        """Omarchy's own apply (it picks the theme's background as usual)."""
        try:
            return subprocess.run(["omarchy-theme-set", data.theme_folder_for(name)], capture_output=True,
                                  timeout=180).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def next_background(self):
        try:
            return subprocess.run(["omarchy-theme-bg-next"], capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def set_background(self, path):
        try:
            return subprocess.run(["omarchy-theme-bg-set", path], capture_output=True, text=True,
                                  timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def notify(self, headline, description):
        try:
            subprocess.run(["omarchy-notification-send", "--app-name", "OmaSkins", headline, description],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


def load_state():
    st = data.rotation_status()
    st.setdefault("bg_shown", [])
    return st


def save_state(st):
    data.ROTATION_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = data.ROTATION_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True))
    os.replace(tmp, data.ROTATION_STATE)


def choose(pool, shown, current, rng=random, key=real):
    """A random pick not shown yet this round. What's on screen now counts as having had its turn
    (so the one you started with doesn't come straight back). When every one has had its turn a new
    round starts. None = nothing else to change to. Backgrounds compare as real paths, themes
    (key=str) by name. `shown` is updated in place."""
    if current and current not in shown:
        shown.append(current)
    fresh = [p for p in pool if key(p) not in shown]
    if not fresh:
        shown[:] = [current] if current else []
        fresh = [p for p in pool if key(p) != current]
    return rng.choice(fresh) if fresh else None


def installed_theme(name):
    return any((root / name).is_dir() for root in (data.USER_THEMES, data.BUILTIN_THEMES))


def session_id():
    """Changes with every desktop login (and so every reboot); a shell or plugin restart keeps it."""
    return os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or boot_id()


def minute_of_day(t):
    return t.tm_hour * 60 + t.tm_min


def slot_times(now, minutes, count=400):
    """The next slots after `now` (epoch seconds): local times whose minute of the day is a multiple
    of `minutes`, counted from midnight."""
    t = time.localtime(now)
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    k = minute_of_day(t) // minutes + 1
    for i in range(count):
        m = (k + i) * minutes
        day, m = divmod(m, 1440)
        tm = time.localtime(midnight + day * 86400 + 43200)  # noon of that day: safe across DST
        yield time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday, m // 60, m % 60, 0, 0, 0, -1))


def next_change(now, st, minutes):
    """The first slot after `now` that isn't inside a grace period or skipped for your own pick."""
    after = max(st.get("session_start", 0), st.get("away_at", 0)) + GRACE
    return next((t for t in slot_times(now, minutes) if t > after and t > st.get("pick_skip", 0)), None)


def hhmm(epoch):
    return time.strftime("%H:%M", time.localtime(epoch)) if epoch else ""


_LAST_LOOK = {}  # when this engine last looked (in memory: a gap since then = asleep or paused)


def _signature(clock):
    """What the 5-second look compares: while it stays the same (and no glance is due), nothing
    else happens."""
    try:
        settings = data.ROTATION_FILE.stat().st_mtime_ns
    except OSError:
        settings = 0
    try:
        bg = os.readlink(data.STATE_DIR / "background")
    except OSError:
        bg = ""
    return [session_id(), settings, data.current_theme_name(), bg, time.strftime("%Y-%m-%d %H:%M", clock)]


def next_moment(now, plan):
    """The next time something could change: the next slot (sunrise and sunset never are)."""
    return next(slot_times(now, plan.minutes), None)


def tick(desktop, now=None, rng=random):
    """One look (every TICK seconds). Normally only whether the clock's minute, the theme, the
    background or the settings changed (microseconds); a full check once a minute or when one did;
    the desktop is only asked in the GRACE before a slot (every LOOK s) and when a slot lands.
    Returns the state (also what the Rotation tab shows)."""
    now = time.time() if now is None else now
    clock = time.localtime(now)
    st = load_state()
    sig = _signature(clock)
    # Looks come every TICK seconds; a much bigger gap means the computer slept or the VM was paused
    # (2026-09-30: 13:05 -> 16:55, and a change landed 22 s after waking). Waking up is coming back.
    woke = now - _LAST_LOOK.get("at", now) > 60
    _LAST_LOOK["at"] = now
    upcoming = st.get("next_moment")
    glance_due = bool(upcoming) and upcoming - now <= GRACE + LOOK and now - st.get("last_look", 0) >= LOOK
    if st.get("sig") == sig and not glance_due and not woke:
        return st
    if woke:
        st["away_at"] = now
        log("back after the computer slept or paused; no change before", hhmm(now + GRACE))
    theme = data.current_theme_name()
    plan = data.load_rotation(theme)
    before = json.dumps(st, sort_keys=True)
    current = canonical_background(theme)
    for key in ("theme_shown", "bg_shown"):
        st[key] = [s for s in st.get(key, []) if isinstance(s, str)]

    if st.get("session") != session_id():
        # A new desktop session (login or reboot): same theme and background (Omarchy keeps them); no
        # change in the first GRACE seconds, so nothing jumps right after you arrive.
        st.update(session=session_id(), session_start=now, last_theme=theme, last_bg=current)
        log("desktop session started; first change at", hhmm(next_change(now, st, plan.minutes)))

    # A change the engine didn't make: you used Omarchy's own theme or background switcher. Either one
    # skips the next rotation slot (owner's rule: you went out of your way, enjoy it a while). A theme
    # change's own new background lands a moment after it and belongs to it: one notification.
    theme_changed = st.get("last_theme") not in (None, theme)
    bg_changed = st.get("last_bg") is not None and current != st.get("last_bg")
    if theme_changed or bg_changed:
        quiet = not theme_changed and now - st.get("manual_at", 0) < 15
        st["manual_at"] = now
        st["pick_skip"] = next(slot_times(now, plan.minutes), None)
        if theme_changed:
            st["bg_shown"] = [current] if plan.themes else st["bg_shown"]
        if plan.running() and not quiet:
            log("changed with Omarchy's switcher:", theme, current, "- skipping", hhmm(st["pick_skip"]))
            desktop.notify("Rotation", f"Your pick stays until {hhmm(next_change(now, st, plan.minutes))}.")
    st.update(last_theme=theme, last_bg=current)

    # Dawn or Dusk (by the sun) only decides which list the next regular slot picks from.
    period = plan.period_now(clock)
    themes = [n for n in plan.checked.get(period, []) if installed_theme(n)]
    st["period"] = period
    st.pop("period_switch", None)

    others = [n for n in themes if n != theme]
    pool = plan.pool(theme) if plan.backgrounds else []
    minutes = plan.minutes
    slot = time.strftime("%Y-%m-%d %H:%M", clock) if minute_of_day(clock) % minutes == 0 else None
    landed = plan.running() and slot and slot != st.get("last_slot")

    # Locked, screensaver or fullscreen in the GRACE before a slot: a glance every LOOK seconds, so a
    # change never lands within GRACE of you coming back (owner's rule, the same as after a login).
    away = ""
    if plan.running() and (glance_due or landed):
        away = desktop.away(sure=bool(landed))
        st["last_look"] = now
        if away:
            st["away_at"] = now

    if landed:
        st["last_slot"] = slot
        # Measured from the slot's own time (hh:mm:00), and 5 min or less counts: arriving at 8:55
        # with 20 min, the first change is 9:20, not 9:00 (owner's example).
        # The slot's exact minute (hh:mm:00.0). Not now - seconds: the real clock has fractions
        # (18:05:00.4), which made "at or before the skipped 18:05" false and a pick got cut short.
        slot_at = time.mktime(clock[:5] + (0, 0, 0, -1))
        reason = (away or
                  ("you just arrived" if slot_at - st.get("session_start", 0) <= GRACE else
                   "your own pick" if slot_at <= (st.get("pick_skip") or 0) else
                   "you just came back" if slot_at - st.get("away_at", 0) <= GRACE else ""))
        if reason:
            log("slot skipped:", reason)
            st["last_skip"] = {"at": now, "reason": reason}
        else:
            theme, current = change(desktop, plan, st, theme, current, themes, others, pool, rng, now)

    st.update(
        status="running" if plan.running() else "off",
        theme_status=("off" if not plan.themes else "ok" if [n for n in themes if n != theme] else
                      "single" if themes else "empty"),
        bg_status=("off" if not plan.backgrounds else "empty" if not plan.pool(theme) else
                   "single" if [real(p) for p in plan.pool(theme)] == [current] and not plan.themes else "ok"),
        next_change=next_change(now, st, minutes) if plan.running() else None,
        next_moment=next_moment(now, plan) if plan.running() else None,
        sig=_signature(clock))
    if json.dumps(st, sort_keys=True) != before:
        save_state(st)
    return st


def change(desktop, plan, st, theme, current, themes, others, pool, rng, now):
    """What one slot changes (owner's design, one interval for both switches):
    - Backgrounds only: the next bright background, a round of all of them before any repeats.
    - Themes only: the next theme; your background stays.
    - Both: the current theme's next bright background; when it has shown them all, the next theme
      comes in with one of its own.
    - Whenever the current theme isn't in the list that applies now (Dawn, Dusk or the everyday one,
      e.g. you just took it out, or Dusk began), the slot changes the theme.
    - Mix it up! (with both on): see mix_it_up()."""
    if plan.mix and plan.themes and plan.backgrounds:
        return mix_it_up(desktop, plan, st, theme, current, themes, others, rng, now)
    bg_left = [p for p in pool if real(p) not in st["bg_shown"] and real(p) != current]
    theme_turn = plan.themes and others and (not plan.backgrounds or theme not in themes or not bg_left)
    if theme_turn:
        pick = choose(themes, st["theme_shown"], theme, rng, key=str)
        bg = choose(plan.pool(pick), [], None, rng) if plan.backgrounds else None
        if real(data.STATE_DIR / "background") != current and os.path.isfile(current):
            # The link points at Omarchy's copy of the current theme, which the new theme replaces:
            # point it at the same picture in the theme's own folder first (no visible change).
            desktop.set_background(current)
        if desktop.set_theme(pick, bg):
            log("theme:", pick, "background:", real(bg) if bg else "kept")
            st["theme_shown"].append(pick)
            theme, current = pick, canonical_background(pick)
            st.update(bg_shown=[current], last_theme=theme, last_bg=current,
                      last_change={"at": now, "theme": theme, "path": current})
        else:
            log("omarchy-theme-set failed:", pick)
    elif plan.backgrounds:
        bg = choose(pool, st["bg_shown"], current, rng)
        if bg and desktop.set_background(bg):
            current = real(bg)
            st["bg_shown"].append(current)
            st.update(last_bg=current, last_change={"at": now, "path": current})
            log("background:", current)
        elif bg:
            log("omarchy-theme-bg-set failed:", bg)
    return theme, current


_ENABLED = {}


# --------------------------------------------------------------------------- skip ahead (the bar's palette menu)
#
# The palette menu asks the running engine (one writer for the rotation's state): it leaves the request in
# REQUEST and the engine carries it out within half a second. Skipping ahead is the rotation moving on early,
# not "your pick": the schedule carries on as before (no pause, no notification).
REQUEST = Path(os.environ.get("XDG_RUNTIME_DIR") or data.OMASKINS_STATE) / "omaskins-next"
WHATS = ("theme", "background")


def request_next(what):
    """Ask the engine; if none is running (the plugin is off), do it right here."""
    if what not in WHATS:
        return False
    lock = REQUEST.with_name("omaskins-rotate.lock")
    try:
        with open(lock, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            running = False
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        running = True
    if not running:
        return skip_ahead(Desktop(), what)
    REQUEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = REQUEST.with_name(".omaskins-next.tmp")
    tmp.write_text(what + "\n")
    tmp.replace(REQUEST)
    return True


def take_request():
    try:
        what = REQUEST.read_text().strip()
        REQUEST.unlink()
    except OSError:
        return None
    return what if what in WHATS else None


def all_themes():
    """Installed themes in Omarchy's own order (by name), the ones hidden in OmaSkins left out."""
    names = set()
    for root in (data.USER_THEMES, data.BUILTIN_THEMES):
        try:
            names |= {d.name for d in root.iterdir() if d.is_dir()}
        except OSError:
            pass
    return sorted(names - data.hidden_in_omaskins(), key=str.lower)


def skip_ahead(desktop, what, rng=random, now=None):
    """The next theme or background now. From the rotation's own lists, in its no-repeat rounds, when it
    rotates that; otherwise Omarchy's own next background, or the next installed theme (Omarchy's own
    apply, which picks its background as usual)."""
    now = time.time() if now is None else now
    st = load_state()
    theme = data.current_theme_name()
    plan = data.load_rotation(theme)
    current = canonical_background(theme)
    for key in ("theme_shown", "bg_shown"):
        st[key] = [s for s in st.get(key, []) if isinstance(s, str)]
    themes = [n for n in plan.checked.get(plan.period_now(time.localtime(now)), []) if installed_theme(n)]
    ok = False
    if what == "theme":
        if plan.themes and [n for n in themes if n != theme]:
            pick = choose(themes, st["theme_shown"], theme, rng, key=str)
            bg = choose(plan.pool(pick), [], None, rng) if plan.backgrounds else None
            if real(data.STATE_DIR / "background") != current and os.path.isfile(current):
                desktop.set_background(current)   # off Omarchy's copy of the old theme first (no visible change)
            ok = desktop.set_theme(pick, bg)
            if ok:
                st["theme_shown"].append(pick)
        else:
            names = all_themes()
            if theme in names and len(names) > 1:
                pick = names[(names.index(theme) + 1) % len(names)]
            else:
                pick = names[0] if names else None
            ok = bool(pick) and desktop.apply_theme(pick)
        if ok:
            theme = data.current_theme_name()
            current = canonical_background(theme)
            st["bg_shown"] = [current]
    elif what == "background":
        pool = plan.mixed_pool(themes) if plan.mix and plan.themes and plan.backgrounds else \
            plan.pool(theme) if plan.backgrounds else []
        shown = st.setdefault("mix_shown", []) if plan.mix and plan.themes and plan.backgrounds else st["bg_shown"]
        bg = choose(pool, shown, current, rng) if pool else None
        ok = desktop.set_background(bg) if bg else desktop.next_background()
        if ok:
            current = canonical_background(theme)
            if not bg:
                st["bg_shown"].append(current)
    if ok:
        log("skipped ahead:", what, theme, current)
        # The engine's own change: not counted as your pick (no pause of the schedule).
        st.update(last_theme=theme, last_bg=current, last_change={"at": now, "theme": theme, "path": current})
        save_state(st)
    return ok


def mix_it_up(desktop, plan, st, theme, current, themes, others, rng, now):
    """Mix it up! (owner, 2026-09-30): every slot pairs the next theme with the next background drawn
    from ALL the bright backgrounds of ALL the themes in the list (the current period's, with Dawn &
    Dusk). Two separate no-repeat rounds, one of themes, one of backgrounds, so the pairs keep changing."""
    pick = choose(themes, st["theme_shown"], theme, rng, key=str) if others else None
    st["mix_shown"] = [s for s in st.get("mix_shown", []) if isinstance(s, str)]
    bg = choose(plan.mixed_pool(themes), st["mix_shown"], current, rng)
    if pick:
        if real(data.STATE_DIR / "background") != current and os.path.isfile(current):
            desktop.set_background(current)  # off Omarchy's copy of the old theme first (no visible change)
        if not desktop.set_theme(pick, bg):
            log("omarchy-theme-set failed:", pick)
            return theme, current
        st["theme_shown"].append(pick)
        theme = pick
    elif bg and not desktop.set_background(bg):
        log("omarchy-theme-bg-set failed:", bg)
        return theme, current
    if bg:
        st["mix_shown"].append(real(bg))
    current = canonical_background(theme)
    log("mix it up:", theme, "background:", real(bg) if bg else "kept")
    st.update(bg_shown=[current], last_theme=theme, last_bg=current,
              last_change={"at": now, "theme": theme, "path": current})
    return theme, current


def plugin_enabled(plugin_id):
    """Omarchy's own rule (PluginRegistry.isEnabled): a third-party plugin is on while its id is anywhere
    in shell.json, in plugins[] or, as OmaSkins' palette icon is, in the bar's layout (re-read only when it
    changes). Unreadable = assume yes (never stop on a glitch)."""
    try:
        mtime = SHELL_JSON.stat().st_mtime_ns
        if _ENABLED.get("key") != (plugin_id, mtime):
            config = json.loads(SHELL_JSON.read_text())
            entries = list(config.get("plugins", []))
            for section in ((config.get("bar") or {}).get("layout") or {}).values():
                entries += section if isinstance(section, list) else []
            _ENABLED.update(key=(plugin_id, mtime),
                            on=any(isinstance(e, dict) and e.get("id") == plugin_id for e in entries))
    except (OSError, ValueError, AttributeError):
        return True
    return _ENABLED["on"]


class DialogLook:
    """File dialogs like Nautilus (data.dialog_css): the block rewritten within half a second of a theme or
    Transparency change; the dialog service restarted once no dialog is open, so the next one has it."""

    def __init__(self, clients=None, evaluate=None, running=None, restart=None):
        from . import run as _run
        self.clients = clients or _run.hypr_clients
        self.evaluate = evaluate or _run.hypr_eval
        self.running = running or _run.dialog_service_running
        self.restart = restart or _run.restart_dialog_service
        self.seen = None
        self.own = None
        self.pending = False

    def signature(self):
        def stamp(p):
            try:
                return p.stat().st_mtime_ns
            except OSError:
                return None
        return (stamp(data.STATE_DIR / "theme" / "colors.toml"), stamp(data.TRANSPARENCY_FILE),
                data.gtk3_dark_available())

    def glance(self):
        from . import run as _run
        try:
            sig = self.signature()
            if sig != self.seen:
                self.seen = sig
                step = data.transparency_step()
                # Nautilus's block too (an OmaSkins update may change it): read when Nautilus next starts. Only
                # OmaSkins' slider restarts an open Nautilus (you're looking at it then).
                if data.write_nautilus_css(step):
                    log("Nautilus look rewritten")
                if data.write_dialog_css(step):
                    self.pending = True
                    log("file dialog look rewritten")
                own = bool(data.dialog_css(step, data.theme_mode()))
                if own != self.own:
                    self.evaluate(_run.FADE_LUA + f"dialogs({'true' if own else 'false'})")
                    self.own = own
            if self.pending:
                if not self.running():
                    self.pending = False   # it reads the new look whenever it next starts
                elif not any(c.get("class") == data.DIALOG_CLASS for c in self.clients()):
                    self.restart()
                    self.pending = False
                    log("dialog service restarted for the new look")
        except Exception as e:
            log("file dialog look failed:", repr(e))


class QtApps:
    """Qt apps in the theme (qtstyle.py): the palette within half a second of a change; the style built
    when it's missing or out of date (in the background: the rotation never waits for a compiler); the
    marked apps' windows kept out of Hyprland's fade. Nothing here may stop the engine."""

    def __init__(self):
        self.seen = None
        self.marks = None
        self.builder = None
        self.failed = None   # (stamp, why) of the last failed build: not retried until something changes
        self.rule_set = False

    def glance(self):
        try:
            if qtstyle.enabled() and qtstyle.signature() != self.seen:
                self.seen = qtstyle.signature()
                qtstyle.rebuild()
                log("Qt apps' colours rebuilt")
        except Exception as e:  # a theme without some colour must not stop the engine
            log("Qt colours failed:", repr(e))
        try:
            if qtstyle.marks_stamp() != self.marks:
                self.marks = qtstyle.marks_stamp()
                self.unfade()
        except Exception as e:
            log("Qt windows failed:", repr(e))

    def tick(self):
        try:
            if not qtstyle.enabled():
                if qtstyle.LIBRARY.exists() or qtstyle.PALETTE.exists():
                    qtstyle.remove()
                    log("Qt apps switched off: style and palette removed")
                    from . import run as _run
                    _run.update_hypr_file()
                return
            if self.builder is None and qtstyle.needs_build():
                stamp = qtstyle.wanted_stamp()
                if not self.failed or self.failed[0] != stamp:
                    self.builder = threading.Thread(target=self._build, args=(stamp,), daemon=True)
                    self.builder.start()
            self.unfade()   # also catches a window Hyprland gave its fade back (it re-checks rules sometimes)
        except Exception as e:
            log("Qt apps failed:", repr(e))

    def _build(self, stamp):
        try:
            log("building OmaSkins' Qt style")
            ok, why = qtstyle.build()
            log("Qt style:", why)
            if ok:
                self.failed = None
                self.seen = None   # write the palette right away
                from . import run as _run
                if _run.update_hypr_file():
                    log("omaskins.lua updated: Qt apps opened from now on use OmaSkins' style")
            else:
                self.failed = (stamp, why)
        except Exception as e:
            self.failed = (stamp, repr(e))
            log("Qt style build failed:", repr(e))
        finally:
            self.builder = None

    def unfade(self):
        if not qtstyle.marked_pids():
            return
        from . import run as _run
        if not self.rule_set:   # the same rule omaskins.lua has, for windows before Hyprland next loads it
            _run.hypr_eval(qtstyle.unfade_rule())
            self.rule_set = True
        changed = qtstyle.unfade_windows(_run.hypr_clients, _run.hypr_eval)
        if changed:
            log("Qt windows doing their own transparency:", len(changed))


def run(plugin_id=None):
    """Keep ticking. One engine at a time (a second copy exits at once)."""
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or data.OMASKINS_STATE)
    runtime.mkdir(parents=True, exist_ok=True)
    lock = open(runtime / "omaskins-rotate.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log("engine started, pid", os.getpid())
    st = load_state()
    st["pid"] = os.getpid()  # the Rotation tab checks this process is alive
    save_state(st)
    desktop = Desktop()
    # After an OmaSkins update: its Hyprland file brought up to date, if you use it (corners or transparency
    # set once); nobody else gets one. Saving it reloads Hyprland once.
    if data.CORNERS_FILE.exists():
        try:
            from . import run as _run
            if _run.update_hypr_file():
                log("omaskins.lua brought up to date")
        except Exception as e:
            log("omaskins.lua update failed:", repr(e))
    # OmaSkins in the app launcher, in Style › OmaSkins, and double-clicking an exported .omaskins file
    # opens it, from the moment it's installed (Service.qml takes them away when the plugin goes off).
    launcher = Path(__file__).resolve().parent.parent / "omaskins-manager"
    try:
        from . import menu, share
        share.register_file_type(launcher)
        if menu.add_row(launcher):
            log("added Style › OmaSkins to Omarchy's menu")
    except Exception as e:
        log("launcher and menu registration failed:", repr(e))
    qt = QtApps()
    dialogs = DialogLook()
    try:
        while True:
            if plugin_id and not plugin_enabled(plugin_id):
                log("plugin disabled, engine stopping")
                return 0
            try:
                tick(desktop)
            except Exception as e:  # one bad tick (a file mid-write) must not end the rotation
                log("tick failed:", repr(e))
            qt.tick()
            # Between ticks, look every half second for a theme or Transparency change for Qt apps (a few
            # file stamps: next to nothing), so they follow within a second instead of up to five.
            end = time.monotonic() + TICK
            while True:
                what = take_request()
                if what:
                    try:
                        skip_ahead(desktop, what)
                    except Exception as e:
                        log("skip ahead failed:", repr(e))
                qt.glance()
                dialogs.glance()
                left = end - time.monotonic()
                if left <= 0:
                    break
                time.sleep(min(0.5, left))
    finally:
        log("engine stopped, pid", os.getpid())


def main(argv):
    if len(argv) >= 1 and argv[0] == "run":
        pid = argv[argv.index("--plugin-id") + 1] if "--plugin-id" in argv[:-1] else None
        return run(pid)
    if argv == ["tick"]:
        print(json.dumps(tick(Desktop()), indent=1, sort_keys=True))
        return 0
    if len(argv) == 2 and argv[0] == "next":
        return 0 if request_next(argv[1]) else 1
    if argv == ["status"]:
        print(json.dumps(data.rotation_status(), indent=1, sort_keys=True))
        return 0
    print("usage: omaskins-rotate run [--plugin-id <id>] | tick | status | next theme|background", file=sys.stderr)
    return 2
