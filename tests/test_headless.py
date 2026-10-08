"""Headless tests for OmaSkins' data layer. No display or network needed.

Everything runs against a fake home (HOME, XDG_CONFIG_HOME, XDG_STATE_HOME,
XDG_CACHE_HOME all point into a temp dir) and a fake OMARCHY_PATH, and the
suite refuses to start if any of them still points at the real home.

    python3 -m unittest discover -s tests -v
"""

import itertools
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_HOME = Path.home()
SANDBOX = Path(tempfile.mkdtemp(prefix="omaskins-test-"))
import atexit  # noqa: E402
atexit.register(shutil.rmtree, SANDBOX, True)   # the tests leave nothing behind either
for var, sub in (("HOME", "home"), ("XDG_CONFIG_HOME", "home/.config"), ("XDG_STATE_HOME", "home/.local/state"),
                 ("XDG_CACHE_HOME", "home/.cache"), ("XDG_DATA_HOME", "home/.local/share"),
                 # 2026-10-08: a removal test deleted the real ~/.local/share/omaskins and the engine's
                 # files in the real /run/user folder, because these two still pointed at the real ones.
                 ("XDG_RUNTIME_DIR", "run"), ("OMARCHY_PATH", "omarchy"),
                 ("OMASKINS_HIDDEN_THEMES", "hidden-themes"), ("OMASKINS_PACMAN_CONF", "etc/pacman.conf")):
    os.environ[var] = str(SANDBOX / sub)
    (SANDBOX / sub).mkdir(parents=True, exist_ok=True) if not sub.endswith(".conf") else None
for var in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"):
    if not os.environ[var].startswith(str(SANDBOX) + "/"):
        sys.exit(f"refusing to run: {var} points outside the test's own folder")

sys.path.insert(0, str(ROOT))
from omaskins import data  # noqa: E402
from omaskins import run as _run  # noqa: E402


def _no_windows(argv):
    # 2026-10-02: a test once ticked every import row, which on the real system included removing two
    # installed fonts; it opened a real password terminal and the owner typed the password. A fake home
    # doesn't fence off pacman or the desktop, so no test may open anything, ever.
    raise AssertionError(f"a test tried to open a window or terminal: {argv}")


_run.launch = _no_windows
_run.hypr_eval = _no_windows      # the live Hyprland is off limits too (transparency fades, blur)
_run.hypr_clients = _no_windows
_run.nautilus_windows = lambda: {}   # never ask the real Nautilus what it has open...
_NAUTILUS_RESTART = _run.nautilus_restart   # the real one, only ever driven with fakes in a test
_run.nautilus_restart = _no_windows   # ...and never restart it
_run.restart_dialog_service = _no_windows   # nor Omarchy's real dialog service
_run.dialog_service_running = lambda: False
data.theme_mode = lambda: "dark"   # never ask the real Omarchy (each test sets what it needs)

HOME = SANDBOX / "home"
OMARCHY = SANDBOX / "omarchy"

PAGE = """<ul>
<li><a href="https://github.com/bjarneo/omarchy-aura-theme" class="group"><img src="/assets/themes/aura.webp" alt="Aura theme screenshot" width="1200"/><span class="mt-2.5">Aura</span></a></li>
<li><a href="https://github.com/guilhermetk/omarchy-all-hallows-eve-theme" class="group"><img src="/assets/themes/all-hallow-s-eve.webp"/><span>All Hallow&#x27;s Eve</span></a></li>
<li><a href="https://github.com/bjarneo/omarchy-aura-theme" class="group"><img src="/assets/themes/dupe.webp"/><span>Aura again</span></a></li>
<li><a href="https://github.com/JJDizz1L/aetheria"><img src="https://cdn.example/a.webp"/><span>Aetheria</span></a></li>
</ul>"""


def write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def build_fixture():
    shutil.rmtree(HOME, ignore_errors=True)
    shutil.rmtree(OMARCHY, ignore_errors=True)
    # built-ins
    write(OMARCHY / "themes/tokyo-night/colors.toml", 'mode = "dark"\naccent = "#7aa2f7"\nbackground = "#1a1b26"\n')
    write(OMARCHY / "themes/tokyo-night/preview.png")
    write(OMARCHY / "themes/tokyo-night/backgrounds/1-b.jpg")
    write(OMARCHY / "themes/tokyo-night/backgrounds/0-a.jpg")
    write(OMARCHY / "themes/nord/backgrounds/n.png")
    write(OMARCHY / "default/omarchy/omarchy-menu.jsonc",
          '"install.style.font.fira": {"action":"omarchy-install-font \'Fira Code\' ttf-firacode-nerd \'FiraCode Nerd Font\'"},\n'
          '"install.style.font.iosevka": {"action":"omarchy-install-font Iosevka ttf-iosevka-nerd \'Iosevka Nerd Font Mono\'"},\n')
    # yours: one from the list, one not listed, one overriding a built-in name
    aura = HOME / ".config/omarchy/themes/aura"
    write(aura / ".git/config", '[core]\n\tbare = false\n[remote "origin"]\n\turl = https://github.com/bjarneo/omarchy-aura-theme.git\n')
    write(aura / "colors.toml", 'accent = "#ff0000"\n')
    write(aura / "backgrounds/aura-1.jpg")
    write(HOME / ".config/omarchy/themes/secret/.git/config", '[remote "origin"]\n\turl = https://gitlab.com/me/secret.git\n')
    write(HOME / ".config/omarchy/themes/nord/colors.toml", 'accent = "#88c0d0"\n')
    # your own backgrounds
    write(HOME / ".config/omarchy/backgrounds/tokyo-night/mine.png")
    write(HOME / ".config/omarchy/backgrounds/tokyo-night/notes.txt")
    # current state: tokyo-night, background = the state copy of 1-b.jpg
    state = HOME / ".local/state/omarchy/current"
    write(state / "theme.name", "tokyo-night\n")
    write(state / "theme/backgrounds/1-b.jpg")
    (state / "background").symlink_to(state / "theme/backgrounds/1-b.jpg")


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file() and ".cache" not in p.parts}


class Names(unittest.TestCase):
    def test_install_name_matches_omarchy_theme_install(self):
        cases = {
            "https://github.com/bjarneo/omarchy-aura-theme": "aura",
            "https://github.com/bjarneo/omarchy-aura-theme.git": "aura",
            "git@github.com:Foo/omarchy-Blue-Theme.git": "blue-theme",  # -theme only stripped lowercase, then lowered
            "https://github.com/ankur311sudo/black_arch": "black_arch",
            "https://github.com/JJDizz1L/aetheria/": "aetheria",
        }
        for url, want in cases.items():
            self.assertEqual(data.install_name_from_url(url), want, url)

    def test_repo_key(self):
        self.assertEqual(data.repo_key("https://github.com/Owner/Repo.git"), "owner/repo")
        self.assertEqual(data.repo_key("git@github.com:Owner/Repo.git"), "owner/repo")
        self.assertEqual(data.repo_key("https://gitlab.com/o/r"), "")

    def test_display_name(self):
        self.assertEqual(data.display_name("tokyo-night"), "Tokyo Night")
        self.assertEqual(data.display_name("rose-pine"), "Rose Pine")


class Community(unittest.TestCase):
    def test_parse(self):
        themes = data.parse_community(PAGE)
        self.assertEqual([t.name for t in themes], ["Aura", "All Hallow's Eve", "Aetheria"])  # dupe dropped
        self.assertEqual(themes[0].screenshot_url, "https://omarchy.org/assets/themes/aura.webp")
        self.assertEqual(themes[2].screenshot_url, "https://cdn.example/a.webp")

    def test_cached_page_used_offline(self):
        build_fixture()
        write(data.CACHE_DIR / "themes.html", PAGE)
        # fresh cache: no network touched
        self.assertEqual(len(data.community_themes()), 3)


class Local(unittest.TestCase):
    def setUp(self):
        build_fixture()

    def test_local_themes_and_override(self):
        themes = {t.name: t for t in data.local_themes()}
        self.assertEqual(sorted(themes), ["aura", "nord", "secret", "tokyo-night"])
        self.assertFalse(themes["nord"].builtin, "a theme you added hides the built-in of the same name")
        self.assertTrue(themes["tokyo-night"].builtin)
        self.assertEqual(themes["tokyo-night"].preview.name, "preview.png")
        self.assertEqual(themes["aura"].preview.name, "aura-1.jpg", "falls back to the first background")
        self.assertEqual(themes["aura"].repo_url, "https://github.com/bjarneo/omarchy-aura-theme.git")
        self.assertEqual(themes["tokyo-night"].colors["accent"], "#7aa2f7")

    def test_match_installed(self):
        community = data.parse_community(PAGE)
        matched = data.match_installed(community, data.local_themes())
        self.assertEqual({k: t.name for k, t in matched.items()}, {"bjarneo/omarchy-aura-theme": "aura"})

    def test_backgrounds_and_current(self):
        t = next(t for t in data.local_themes() if t.name == "tokyo-night")
        bgs = data.backgrounds_for(t, data.current_theme_name(), data.current_background())
        self.assertEqual([(b.path.name, b.yours) for b in bgs],
                         [("0-a.jpg", False), ("1-b.jpg", False), ("mine.png", True)])
        self.assertEqual([b.path.name for b in bgs if b.current], ["1-b.jpg"])

    def test_current_background_yours(self):
        state = HOME / ".local/state/omarchy/current"
        mine = HOME / ".config/omarchy/backgrounds/tokyo-night/mine.png"
        (state / "background").unlink()
        (state / "background").symlink_to(mine)
        t = next(t for t in data.local_themes() if t.name == "tokyo-night")
        bgs = data.backgrounds_for(t, "tokyo-night", data.current_background())
        self.assertEqual([b.path.name for b in bgs if b.current], ["mine.png"])

    def test_current_background_from_another_theme(self):
        # tokyo-night applied, but the background was set from aura.
        themes = {t.name: t for t in data.local_themes()}
        state = HOME / ".local/state/omarchy/current"
        (state / "background").unlink()
        (state / "background").symlink_to(themes["aura"].path / "backgrounds/aura-1.jpg")
        cur = data.current_background()
        self.assertEqual([b.path.name for b in data.backgrounds_for(themes["aura"], "tokyo-night", cur)
                          if b.current], ["aura-1.jpg"])
        self.assertFalse(any(b.current for b in data.backgrounds_for(themes["tokyo-night"], "tokyo-night", cur)))

    def test_no_current_mark_on_other_themes(self):
        t = next(t for t in data.local_themes() if t.name == "aura")
        self.assertFalse(any(b.current for b in data.backgrounds_for(t, "tokyo-night", data.current_background())))


class Actions(unittest.TestCase):
    def setUp(self):
        build_fixture()
        self.local = {t.name: t for t in data.local_themes()}
        self.community = data.parse_community(PAGE)

    def labels(self, acts):
        return [a.label for a in acts]

    def test_theme_actions(self):
        self.assertEqual(self.labels(data.theme_actions(None, self.community[1], "tokyo-night")), ["Add"])
        add = data.theme_actions(None, self.community[1])[0]
        url = "https://github.com/guilhermetk/omarchy-all-hallows-eve-theme"
        tmp = data.PARTIAL_THEMES / "all-hallows-eve"
        self.assertEqual(add.steps, (("clear_partial", tmp), ("run", ["omarchy-git-url-check", url]),
                                     ("run", ["git", "clone", "--progress", "--", url, str(tmp)]),
                                     ("move_in", tmp, data.USER_THEMES / "all-hallows-eve")),
                         "omarchy-theme-install's check + clone, into a holding folder, without applying")
        cur = data.theme_actions(self.local["tokyo-night"], None, "tokyo-night")
        self.assertEqual(self.labels(cur), ["Hide"], "current built-in: nothing to apply")
        self.assertIn("Switch to another one first", cur[0].blocked)
        self.assertEqual(data.theme_actions(self.local["aura"], None, "aura")[0].blocked,
                         cur[0].blocked, "no theme can be removed while it's the current one")
        self.assertEqual(self.labels(data.theme_actions(self.local["aura"], self.community[0], "tokyo-night")),
                         ["Apply", "Remove"])

    def test_quoting(self):
        self.assertEqual(data.q("it's"), "'it'\\''s'")

    def test_font_actions_protect_font_in_use(self):
        cur = data.Font("JetBrainsMonoNL Nerd Font", "ttf-jetbrains-mono-nerd", True)
        sibling = data.Font("JetBrainsMono Nerd Font", "ttf-jetbrains-mono-nerd", False)
        system = data.Font("Liberation Mono", "ttf-liberation", False)
        other = data.Font("FiraCode Nerd Font", "ttf-firacode-nerd", False)
        pkg = "ttf-jetbrains-mono-nerd"
        self.assertEqual(self.labels(data.font_actions(font=cur, current_package=pkg)), [])
        self.assertEqual(self.labels(data.font_actions(font=sibling, current_package=pkg)), ["Use"],
                         "same package as the font in use: no Remove")
        self.assertEqual(self.labels(data.font_actions(font=system, current_package=pkg)), ["Use"])
        self.assertEqual(self.labels(data.font_actions(font=other, current_package=pkg)), ["Use", "Remove"])
        inst = data.FontPackage(pkg, "1", "", True)
        self.assertEqual(self.labels(data.font_actions(package=inst, current_package=pkg)), [])
        new = data.FontPackage("ttf-firacode-nerd", "1", "", False, "FiraCode Nerd Font")
        acts = data.font_actions(package=new, current_package=pkg)
        self.assertEqual(self.labels(acts), ["Add", "Add and use"])
        term = "echo 'Installing ttf-firacode-nerd...'; omarchy-pkg-add ttf-firacode-nerd"
        self.assertEqual(acts[1].steps, (("terminal", term), ("wait_package", "ttf-firacode-nerd", True, term),
                                         ("remember_text_size",), ("use_font", "ttf-firacode-nerd", "FiraCode Nerd Font")))
        self.assertNotIn("use_font", [st[0] for st in acts[0].steps], "plain Add doesn't switch the font")
        self.assertTrue(all(a.password for a in acts))
        rm = data.font_actions(font=other, current_package=pkg)[1]
        self.assertEqual(rm.steps[0], ("terminal", "echo 'Removing ttf-firacode-nerd...'; omarchy-pkg-drop ttf-firacode-nerd"))
        self.assertEqual(data.font_actions(font=other, current_package=pkg)[0].steps,
                         (("remember_text_size",), ("run", ["omarchy-font-set", "FiraCode Nerd Font"]),
                          ("font_size", "FiraCode Nerd Font")))
        odd = data.FontPackage("ttf-x-nerd;rm", "1", "", False)
        self.assertEqual(data.font_actions(package=odd), [], "a package name a shell could misread gets no buttons")

    def test_font_picks_from_menu(self):
        self.assertEqual(data.omarchy_font_picks(),
                         {"ttf-firacode-nerd": "FiraCode Nerd Font", "ttf-iosevka-nerd": "Iosevka Nerd Font Mono"})

    def test_normalized_size(self):
        self.assertEqual(data.normalized_size(20, 0.5, 0.5), 20)
        self.assertEqual(data.normalized_size(20, 0.4, 0.5), 25)
        self.assertEqual(data.normalized_size(20, 0.1, 0.5), 28, "clamped at 140%")
        self.assertEqual(data.normalized_size(20, 0, 0.5), 20, "unknown metric: unchanged")


class Export(unittest.TestCase):
    def setUp(self):
        build_fixture()
        self.local = {t.name: t for t in data.local_themes()}
        self.community = data.parse_community(PAGE)

    def test_listed_theme_travels_as_link(self):
        items = data.export_plan(self.local["aura"], self.community, None, None)
        self.assertEqual([(i.kind, i.how) for i in items], [("Theme", "link")])
        self.assertIn("omarchy-aura-theme.git", items[0].detail)

    def test_unlisted_theme_and_own_background_are_bundled(self):
        mine = data.Background(Path("/x/mine.png"), "secret", True)
        font = data.Font("Custom", "", True)
        items = data.export_plan(self.local["secret"], self.community, mine, font)
        self.assertEqual([(i.kind, i.how) for i in items], [("Theme", "bundle"), ("Background", "bundle"),
                                                             ("Font", "bundle")])

    def test_builtin_and_package_font_are_links(self):
        b = data.Background(Path("/x/1-b.jpg"), "tokyo-night", False, True)
        font = data.Font("FiraCode Nerd Font", "ttf-firacode-nerd", True)
        items = data.export_plan(self.local["tokyo-night"], self.community, b, font)
        self.assertEqual([i.how for i in items], ["link", "link", "link"])
        self.assertIn('"format": "omaskins-share/1"', data.export_summary_json(items))


class CopyToCurrentTheme(unittest.TestCase):
    def test_offered_only_for_other_themes(self):
        build_fixture()
        themes = {t.name: t for t in data.local_themes()}
        aura_bg = data.backgrounds_for(themes["aura"])[0]
        a = data.copy_to_theme_action(aura_bg, "tokyo-night")
        self.assertEqual(a.label, "Copy to current theme's backgrounds")
        self.assertIn(str(aura_bg.path), a.command)
        self.assertTrue(a.command.rstrip("/'").endswith(".config/omarchy/backgrounds/tokyo-night"), a.command)
        for b in data.backgrounds_for(themes["tokyo-night"]):
            self.assertIsNone(data.copy_to_theme_action(b, "tokyo-night"))


class SaveToPictures(unittest.TestCase):
    def test_copies_original_and_never_overwrites(self):
        src = SANDBOX / "src" / "1-mountains.png"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"\x89PNG original bytes")
        pics = HOME / "Pictures"
        shutil.rmtree(pics, ignore_errors=True)

        dest, already = data.save_to_pictures(src, pics)
        self.assertEqual((dest, already), (pics / "1-mountains.png", False))
        self.assertEqual(dest.read_bytes(), src.read_bytes())

        # Same file again: nothing new is written.
        self.assertEqual(data.save_to_pictures(src, pics), (dest, True))
        self.assertEqual(len(list(pics.iterdir())), 1)

        # A different file already has the name: keep it, save as -2.
        dest.write_bytes(b"someone else's picture")
        dest2, already = data.save_to_pictures(src, pics)
        self.assertEqual((dest2.name, already), ("1-mountains-2.png", False))
        self.assertEqual(dest.read_bytes(), b"someone else's picture")
        self.assertEqual(dest2.read_bytes(), src.read_bytes())


class Rotation(unittest.TestCase):
    def setUp(self):
        build_fixture()
        self.themes = {t.name: t for t in data.local_themes()}
        self.bgs = {n: data.backgrounds_for(t) for n, t in self.themes.items()}
        self.plan = data.RotationPlan("tokyo-night")
        self.plan.seed_solo(self.bgs["tokyo-night"])

    def test_off_by_default_and_switches_are_independent(self):
        p = self.plan
        self.assertFalse(p.running())
        p.backgrounds = True
        self.assertTrue(p.running())
        p.backgrounds, p.themes = False, True
        self.assertTrue(p.running())

    def test_checking_a_theme_starts_with_all_its_backgrounds(self):
        p = self.plan
        p.themes = True
        self.assertTrue(p.is_checked("tokyo-night"))  # the current theme starts checked
        self.assertFalse(p.is_checked("aura"))
        p.set_checked("aura", True, self.bgs["aura"])
        self.assertEqual(p.picked_count("aura", self.bgs["aura"]), len(self.bgs["aura"]))
        # dim one, untick and re-tick: the pick is remembered, not reset to all
        p.toggle("aura", self.bgs["aura"][0], self.bgs["aura"])
        p.set_checked("aura", False)
        p.set_checked("aura", True, self.bgs["aura"])
        self.assertFalse(p.is_picked("aura", self.bgs["aura"][0], self.bgs["aura"]))

    def test_looking_at_an_unticked_theme_keeps_it_out_and_keeps_its_picks(self):
        p = self.plan
        p.themes = True
        aura = self.bgs["aura"]
        p.set_checked("aura", True, aura)
        p.toggle("aura", aura[0], aura)                  # dimmed one
        p.set_checked("aura", False)
        p.is_picked("aura", aura[0], aura)               # looking at it, unticked
        self.assertFalse(p.is_checked("aura"), "opening it doesn't tick it")
        p.set_checked("aura", True, aura)
        self.assertFalse(p.is_picked("aura", aura[0], aura), "ticked again: the saved dim choice is back")

    def test_backgrounds_only_pool_starts_with_the_current_theme(self):
        p = self.plan
        tn, aura = self.bgs["tokyo-night"], self.bgs["aura"]
        self.assertEqual(p.picked_count("tokyo-night", tn), len(tn))
        self.assertEqual(p.picked_count("aura", aura), 0)  # other themes start dim
        self.assertTrue(p.toggle("aura", aura[0], aura))
        self.assertEqual(p.picked_count("aura", aura), 1)

    def test_theme_picks_do_not_leak_into_the_backgrounds_only_pool(self):
        p = self.plan
        p.themes = True
        p.set_checked("aura", True, self.bgs["aura"])
        p.themes = False
        self.assertEqual(p.picked_count("aura", self.bgs["aura"]), 0)

    def test_dawn_and_dusk_have_their_own_theme_sets(self):
        p = self.plan
        p.themes = True
        p.set_dawn_dusk(True)
        self.assertTrue(p.is_checked("tokyo-night"))  # Dawn starts as a copy of the all-day set
        p.set_checked("aura", True, self.bgs["aura"])
        p.period = "Dusk"
        self.assertFalse(p.is_checked("aura"))

    def test_removed_themes_are_forgotten(self):
        p = self.plan
        p.set_checked("aura", True, self.bgs["aura"])
        p.forget_missing({"tokyo-night"})
        self.assertFalse(p.is_checked("aura"))
        self.assertNotIn("aura", p.theme_picks)


class FakeDesktop:
    """Stands in for the running desktop: records changes instead of making them."""

    def __init__(self):
        self.hidden, self.full, self.set, self.themes, self.notes = "", False, [], [], []

    def away(self, sure=False):
        self.looks = getattr(self, "looks", 0) + 1
        return self.hidden or ("fullscreen" if self.full else "")

    def notify(self, headline, description):
        self.notes.append(description)

    def set_background(self, path):  # like omarchy-theme-bg-set: re-point the link
        link = data.STATE_DIR / "background"
        link.unlink()
        link.symlink_to(os.path.realpath(path))
        self.set.append(os.path.realpath(path))
        return True

    def set_theme(self, name, background=None):
        """Like OMARCHY_THEME_SKIP_BACKGROUND=1 omarchy-theme-set: Omarchy's copy of the current theme
        is replaced by the new one's and theme.name changes; the background link is left alone."""
        copies = data.STATE_DIR / "theme"
        shutil.rmtree(copies, ignore_errors=True)
        theme = next(t for t in data.local_themes() if t.name == name)
        (copies / "backgrounds").mkdir(parents=True)
        for b in data.backgrounds_for(theme):
            if not b.yours:
                shutil.copy(b.path, copies / "backgrounds" / b.path.name)
        (data.STATE_DIR / "theme.name").write_text(name + "\n")
        self.themes.append((name, os.path.realpath(background) if background else None))
        if background:
            self.set_background(background)
        return True


def at(h, m, s=0, day=30):
    return time.mktime((2026, 9, day, h, m, s, 0, 0, -1))


class ClockBase(unittest.TestCase):
    """A fake home on tokyo-night, backgrounds rotating every 5 min, and a clock the test drives."""

    def setUp(self):
        from omaskins import rotation
        self.r = rotation
        build_fixture()
        self._sid = rotation.session_id
        self.session = "login-1"
        rotation.session_id = lambda: self.session
        self.d = FakeDesktop()
        self.local = {t.name: t for t in data.local_themes()}
        self.bgs = {n: data.backgrounds_for(t) for n, t in self.local.items()}
        self.tn = {b.path.name: os.path.realpath(b.path) for b in self.bgs["tokyo-night"]}
        self.plan = data.RotationPlan("tokyo-night")
        self.plan.seed_solo(self.bgs["tokyo-night"])
        self.plan.backgrounds, self.plan.minutes = True, 5
        self.save()
        self.t = None
        self._sun = data.sun_times_now
        data.sun_times_now = lambda: {"rise": 7 * 60, "set": 19 * 60, "where": "test"}  # Dawn 07:00, Dusk 19:00

    def tearDown(self):
        self.r.session_id = self._sid
        data.sun_times_now = self._sun

    def save(self):
        data.save_rotation(self.plan)

    def arrive(self, t):
        """Log in at `t` (the engine's first look in this desktop session), then jump to 8:59:55."""
        self.run_until(t, start=t)
        self.t = max(self.t, at(8, 59, 55))
        self.r._LAST_LOOK.clear()

    def run_until(self, end, start=None, step=5):
        """Tick every `step` seconds (like the engine) from `start` (or where we are) to `end`. A new
        `start` is a fresh engine (a jump in the test's clock isn't a sleep)."""
        if start is not None:
            self.r._LAST_LOOK.clear()
        self.t = start if start is not None else self.t
        st = None
        while self.t <= end:
            # The real clock is never on a whole second (18:05:00.4): test with fractions too, which
            # a pick's skipped slot once failed on.
            st = self.r.tick(self.d, now=self.t + 0.437)
            self.t += step
        return st



class ClockRotation(ClockBase):
    """Owner's design: changes land on the clock (multiples of the interval from midnight), skipped
    for 5 min after arriving at the desktop or after your own pick, and while locked/screensaver/
    fullscreen. One interval for both switches."""

    def test_slots_are_multiples_of_the_interval_from_midnight(self):
        hh = lambda ts: [time.strftime("%H:%M", time.localtime(t)) for t in ts]
        first = lambda m, now: hh(list(itertools.islice(self.r.slot_times(now, m), 3)))
        self.assertEqual(first(20, at(9, 1)), ["09:20", "09:40", "10:00"])
        self.assertEqual(first(15, at(9, 16)), ["09:30", "09:45", "10:00"])
        self.assertEqual(first(120, at(9, 0)), ["10:00", "12:00", "14:00"])
        self.assertEqual(first(1440, at(9, 0)), ["00:00", "00:00", "00:00"])

    def test_intervals_step_through_the_owners_values_only(self):
        self.assertEqual(data.INTERVALS, (5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440))
        self.assertEqual([data.snap_interval(m) for m in (1, 5, 7, 25, 45, 90, 5000, "x")],
                         [5, 5, 10, 30, 60, 120, 1440, 60])
        self.assertEqual([data.interval_text(m) for m in (5, 30, 60, 720)], ["5 min", "30 min", "1 h", "12 h"])

    def test_old_two_timer_settings_convert(self):
        f = data.RotationPlan.from_dict
        self.assertEqual(f({"themes": True, "theme_minutes": 20, "bg_minutes": 1}).minutes, 20)
        self.assertEqual(f({"backgrounds": True, "bg_minutes": 1}).minutes, 5)
        self.assertEqual(f({"minutes": 25}).minutes, 30)
        plan = data.load_rotation("tokyo-night")
        self.assertEqual((plan.backgrounds, plan.minutes), (True, 5))
        data.ROTATION_FILE.write_text("{oops")
        self.assertFalse(data.load_rotation().backgrounds, "a broken file falls back to defaults (off)")

    def test_changes_land_on_the_clock_only(self):
        self.run_until(at(8, 50), start=at(8, 50))      # arrived at 8:50
        self.run_until(at(9, 10, 30), start=at(8, 59))
        self.assertEqual(len(self.d.set), 3, "9:00, 9:05, 9:10 and nothing in between")

    def test_five_minute_grace_after_arriving(self):
        # Owner's examples, 20 min: arrive 8:55 -> first change 9:20; arrive 8:50 -> 9:00.
        self.plan.minutes = 20
        self.save()
        st = self.run_until(at(8, 55), start=at(8, 55))
        self.assertEqual(time.strftime("%H:%M", time.localtime(st["next_change"])), "09:20")
        self.run_until(at(9, 19, 55), start=at(8, 55, 5))
        self.assertEqual(self.d.set, [], "9:00 is inside the grace: skipped")
        self.run_until(at(9, 20))
        self.assertEqual(len(self.d.set), 1)
        self.session = "login-2"
        self.d.set.clear()
        st = self.run_until(at(9, 50), start=at(9, 50))
        self.assertEqual(time.strftime("%H:%M", time.localtime(st["next_change"])), "10:00")
        self.run_until(at(10, 0))
        self.assertEqual(len(self.d.set), 1, "10 min after arriving is outside the grace")

    def test_a_restart_in_the_same_session_is_not_an_arrival(self):
        self.arrive(at(8, 30))
        self.run_until(at(8, 30))
        self.run_until(at(9, 0), start=at(8, 58))      # the engine restarted at 8:58, same login
        self.assertEqual(len(self.d.set), 1)

    def pick_background(self):
        """What Omarchy's background switcher does: re-point the link (the engine only notices)."""
        link = data.STATE_DIR / "background"
        other = next(p for p in self.tn.values() if p != os.path.realpath(link))
        link.unlink()
        link.symlink_to(other)

    def test_your_own_pick_skips_the_next_slot(self):
        self.arrive(at(8, 30))
        self.run_until(at(9, 3))
        self.pick_background()                          # Omarchy's switcher, 9:03
        before = len(self.d.set)
        self.run_until(at(9, 10))
        self.assertEqual(self.d.notes, ["Your pick stays until 09:10."])
        self.assertEqual(len(self.d.set), before + 1, "9:05 skipped, 9:10 changes")

    def test_owners_pick_examples_at_20_minutes(self):
        # Pick at 9:03 -> 9:20 skipped -> 9:40. Pick again at 9:30 -> counts from the latest: 9:40
        # skipped -> 10:00.
        self.plan.minutes = 20
        self.save()
        self.arrive(at(8, 30))
        self.run_until(at(9, 3))
        self.pick_background()
        n = len(self.d.set)
        self.run_until(at(9, 20, 30))
        self.assertEqual((len(self.d.set), self.d.notes), (n, ["Your pick stays until 09:40."]))
        self.run_until(at(9, 30))
        self.pick_background()
        self.run_until(at(9, 40, 30))
        self.assertEqual(len(self.d.set), n, "9:40 skipped for the second pick")
        self.assertEqual(self.d.notes[-1], "Your pick stays until 10:00.")
        self.run_until(at(10, 0, 30))
        self.assertEqual(len(self.d.set), n + 1)

    def test_locked_screensaver_or_fullscreen_skip_the_slot_and_five_minutes_after(self):
        # 5 min slots: away over a slot -> skipped; back 4 min before the next -> skipped too (grace);
        # the one after changes. Nothing is saved up.
        for attr, value in (("hidden", "locked"), ("hidden", "screensaver"), ("full", True)):
            with self.subTest(attr=attr, value=value):
                build_fixture()                            # a fresh fake home and desktop per case
                self.d = FakeDesktop()
                self.save()
                self.arrive(at(8, 30))
                self.run_until(at(9, 1))
                n = len(self.d.set)
                setattr(self.d, attr, value)
                self.run_until(at(9, 6))                  # away from 9:01 to 9:06: 9:05 skipped
                setattr(self.d, attr, "" if attr == "hidden" else False)
                self.run_until(at(9, 10, 30))
                self.assertEqual(len(self.d.set), n, "9:10 is 4 min after coming back: skipped")
                self.run_until(at(9, 15, 30))
                self.assertEqual(len(self.d.set), n + 1, "9:15 changes it")

    def test_coming_back_more_than_five_minutes_before_a_slot_changes_on_time(self):
        self.plan.minutes = 20
        self.save()
        self.arrive(at(8, 30))
        self.d.hidden = "locked"
        self.run_until(at(9, 13))
        self.d.hidden = ""                                # back at 9:13, 7 min before 9:20
        self.run_until(at(9, 20, 30))
        self.assertEqual(len(self.d.set), 1)
        self.d.hidden = "screensaver"
        self.run_until(at(9, 36))
        self.d.hidden = ""                                # back at 9:36, 4 min before 9:40
        self.run_until(at(9, 40, 30))
        self.assertEqual(len(self.d.set), 1, "9:40 skipped")
        self.run_until(at(10, 0, 30))
        self.assertEqual(len(self.d.set), 2, "10:00 changes it")

    def test_waking_from_sleep_is_coming_back(self):
        # 2026-09-30: the VM paused 13:05 -> 16:55:22 and a change landed 22 s after waking.
        self.arrive(at(12, 0))
        self.run_until(at(13, 5, 30), start=at(12, 59, 55))
        self.r._LAST_LOOK["at"] = at(13, 5, 30)          # the engine's last look before the pause
        n = len(self.d.set)
        self.t = at(16, 55, 22)                           # the VM wakes
        self.run_until(at(17, 0, 30))
        self.assertEqual(len(self.d.set), n, "16:55 and 17:00 are within 5 min of waking")
        self.run_until(at(17, 5, 30))
        self.assertEqual(len(self.d.set), n + 1, "17:05 changes it")

    def test_every_background_gets_a_turn_before_any_repeats(self):
        self.arrive(at(8, 30))
        self.run_until(at(10, 0))
        first_round = self.d.set[:2]                   # 3 picks; the starting one had its turn
        self.assertEqual(len(set(first_round)), 2)
        self.assertNotIn(self.tn["1-b.jpg"], first_round)
        self.assertTrue(all(a != b for a, b in zip(self.d.set, self.d.set[1:])))

    def test_state_copy_counts_as_the_themes_own_file(self):
        self.assertEqual(self.r.canonical_background("tokyo-night"), self.tn["1-b.jpg"])

    def test_off_empty_and_single(self):
        self.plan.solo_picks = set()
        self.save()
        self.assertEqual(self.run_until(at(8, 30), start=at(8, 30))["bg_status"], "empty")
        self.plan.solo_picks = {Path(self.tn["1-b.jpg"])}
        self.save()
        self.assertEqual(self.run_until(at(8, 31))["bg_status"], "single")
        self.plan.backgrounds = False
        self.save()
        st = self.run_until(at(9, 30))
        self.assertEqual((st["status"], self.d.set), ("off", []))

    def test_quiet_between_slots(self):
        """The desktop is only asked in the 5 min before a slot (every 30 s) and at the slot; the state
        file is written at most once a minute."""
        self.plan.minutes = 20
        self.save()
        self.arrive(at(8, 30))
        self.run_until(at(9, 1))
        self.d.looks = 0
        writes = []
        save = self.r.save_state
        self.r.save_state = lambda st: (writes.append(self.t), save(st))
        try:
            self.run_until(at(9, 14, 25))
            self.assertEqual(self.d.looks, 0, "9:01-9:14: nothing asked")
            self.assertLessEqual(len(writes), 14, "at most once a minute")
            self.run_until(at(9, 19, 55))
            self.assertEqual(self.d.looks, 11, "9:14:30-9:19:30: one glance every 30 s")
        finally:
            self.r.save_state = save

    def test_status_line(self):
        from omaskins import app  # needs GTK, like the app
        plan = data.load_rotation("tokyo-night")
        text = lambda st, alive=True: app.rotation_status_text(plan, st, alive)
        self.assertEqual(text({}, alive=False), "Engine off: plugin enabled?")
        self.assertEqual(text({"status": "running", "next_change": at(9, 20)}), "Next change at 09:20")
        self.assertEqual(text({"status": "running", "bg_status": "single"}), "Select a few more backgrounds")
        plan.themes = True
        self.assertEqual(text({"status": "running", "theme_status": "single"}), "Check at least one more theme")
        self.assertFalse(app.engine_alive({"pid": 999999999}))
        self.assertFalse(app.engine_alive({}))

    def test_update_all_saves_the_rotation_settings_at_once(self):
        from omaskins import app  # needs GTK, like the app
        page = app.RotationPage.__new__(app.RotationPage)   # just the saving part, no window
        page.plan, page._save_id = data.RotationPlan("tokyo-night"), 0
        page.plan.minutes = 20
        page._changed = None
        self.assertTrue(page.save_now())
        self.assertEqual(data.load_rotation("tokyo-night").minutes, 20)

    def test_interval_buttons_step_through_the_owners_values(self):
        from omaskins import app  # needs GTK, like the app
        saved = []
        box = app.interval_stepper(20, saved.append)
        seen = [box.text()]
        for by in (-1, -1, -1, -1, +1, +1, +1, +1, +1):
            box.step(by)
            seen.append(box.text())
        self.assertEqual(seen, ["20 min", "15 min", "10 min", "5 min", "5 min", "10 min", "15 min", "20 min",
                                "30 min", "1 h"])
        self.assertEqual(saved[-1], 60)

    def test_engine_stops_when_the_plugin_is_disabled(self):
        pid = "io.github.jesuslovesyou1013.omaskins"
        self.assertTrue(self.r.plugin_enabled(pid), "no shell.json: keep going")
        write(self.r.SHELL_JSON, json.dumps({"plugins": [{"id": pid}]}))
        self.assertTrue(self.r.plugin_enabled(pid))
        write(self.r.SHELL_JSON, json.dumps({"plugins": [{"id": "other"}]}))
        self.assertFalse(self.r.plugin_enabled(pid))

    def test_away_detection(self):
        mon = [{"activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}, "solitaryBlockedBy": ["WINDOWED"]}]
        cases = [
            ([{"fullscreen": 2, "workspace": {"id": 1}}], True),    # fullscreen on the shown workspace
            ([{"fullscreen": 3, "workspace": {"id": 1}}], True),    # maximised + fullscreen
            ([{"fullscreen": 1, "workspace": {"id": 1}}], False),   # maximised: the bar still shows
            ([{"fullscreen": 2, "workspace": {"id": 2}}], False),   # fullscreen on a hidden workspace
            ([], False),
        ]
        for clients, want in cases:
            self.assertEqual(self.r.fullscreen_on_screen(mon, clients), want, clients)
        d = self.r.Desktop()
        d._shell_locked = lambda: False
        for monitors, clients, want in (
                (mon, [], ""),
                ([dict(mon[0], solitaryBlockedBy=["LOCK"])], [], "locked"),
                (mon, [{"class": "org.omarchy.screensaver", "workspace": {"id": 1}}], "screensaver"),
                (mon, [{"fullscreen": 2, "workspace": {"id": 1}}], "fullscreen"),
                ([], [], "")):                                       # hyprctl didn't answer: here
            d._snapshot = lambda m=monitors, c=clients: (m, c)
            self.assertEqual(d.away(), want)
        d._shell_locked = lambda: True
        d._snapshot = lambda: (mon, [])
        self.assertEqual((d.away(), d.away(sure=True)), ("", "locked"), "the shell is asked only at a slot")

    def test_sun_times_where_you_are_once_per_boot(self):
        r, s = data.sun_times(40.714, -74.006, 2026, 9, 30, -240)       # New York, EDT
        self.assertTrue(6 * 60 + 45 <= r <= 6 * 60 + 55 and 18 * 60 + 37 <= s <= 18 * 60 + 47, (r, s))
        self.assertEqual(data._iso6709("+404251-0740023"), (40 + 42 / 60 + 51 / 3600, -(74 + 0 / 60 + 23 / 3600)))
        loc = data.home_location()                                       # no weather location in the sandbox
        self.assertTrue(loc is None or loc[2].endswith("from your time zone"), loc)
        write(data.WEATHER_SETTINGS, json.dumps({"name": "Batesville", "latitude": 39.3, "longitude": -85.22}))
        self.assertEqual(data.home_location(), (39.3, -85.22, "Batesville, from the weather widget"))
        sun_now = self._sun                                              # the real one, not setUp's stand-in
        data._SUN.clear()
        write(data.SUN_STATE, json.dumps({"boot": "an-older-boot", "rise": 1, "set": 2, "where": "old"}))
        first = dict(sun_now())
        self.assertEqual(first["where"], "Batesville, from the weather widget", "a new boot works it out")
        home = data.home_location
        data.home_location = lambda: self.fail("worked out again in the same boot")
        try:
            data._SUN.clear()
            self.assertEqual(sun_now(), first, "read back, not worked out again")
        finally:
            data.home_location = home

    def test_plugin_files_are_valid(self):
        m = json.loads((ROOT / "manifest.json").read_text())
        self.assertEqual(m["entryPoints"]["service"], "Service.qml")
        self.assertIn(m["id"], (ROOT / "Service.qml").read_text())
        if shutil.which("omarchy-plugin-validate"):
            r = subprocess.run(["omarchy-plugin-validate", str(ROOT)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class ClockThemeRotation(ClockBase):
    """Themes on: one slot, one change; with Backgrounds on too, the theme's bright backgrounds take
    turns first and the next theme comes in after the last one."""

    def themes_on(self, backgrounds=False):
        self.plan.themes, self.plan.backgrounds, self.plan.minutes = True, backgrounds, 5
        for name in ("tokyo-night", "aura", "nord"):
            self.plan.set_checked(name, True, self.bgs[name])
        self.save()

    def test_themes_only_changes_the_colours_and_never_the_background(self):
        self.themes_on()
        before = self.r.canonical_background("tokyo-night")
        self.arrive(at(8, 30))
        self.run_until(at(9, 10))
        names = [n for n, _ in self.d.themes]
        self.assertEqual(len(names), 3)
        self.assertEqual(set(names[:2]), {"aura", "nord"}, "each checked theme once before repeats")
        self.assertTrue(all(bg is None for _, bg in self.d.themes))
        self.assertEqual(self.d.set, [before], "only the same-picture re-point, never another image")
        self.assertEqual(os.path.realpath(data.STATE_DIR / "background"), before)

    def test_paused_themes_keep_the_theme_and_its_backgrounds_take_turns(self):
        self.themes_on(backgrounds=True)
        data.set_rotation_paused("theme", True)
        self.addCleanup(data.ROTATION_PAUSED.unlink, missing_ok=True)
        self.arrive(at(8, 30))
        self.run_until(at(9, 40))                       # far more slots than tokyo-night has pictures
        self.assertEqual(self.d.themes, [], "themes are paused: tokyo-night stays")
        self.assertGreaterEqual(len(self.d.set), 8, "its backgrounds keep changing, round after round")
        self.assertTrue(set(self.d.set) <= set(self.tn.values()), "only tokyo-night's own pictures")

    def test_paused_backgrounds_keep_the_picture_and_themes_take_turns(self):
        self.themes_on(backgrounds=True)
        data.set_rotation_paused("background", True)
        self.addCleanup(data.ROTATION_PAUSED.unlink, missing_ok=True)
        before = self.r.canonical_background("tokyo-night")
        self.arrive(at(8, 30))
        self.run_until(at(9, 10))
        self.assertEqual(len(self.d.themes), 3, "every slot is a theme's turn")
        self.assertTrue(all(bg is None for _, bg in self.d.themes), "no theme brings a background")
        self.assertEqual(os.path.realpath(data.STATE_DIR / "background"), before)

    def test_both_paused_nothing_changes_until_resumed(self):
        self.themes_on(backgrounds=True)
        data.set_rotation_paused("theme", True)
        data.set_rotation_paused("background", True)
        self.addCleanup(data.ROTATION_PAUSED.unlink, missing_ok=True)
        self.arrive(at(8, 30))
        st = self.run_until(at(9, 20))
        self.assertEqual((self.d.themes, self.d.set), ([], []))
        self.assertEqual(st["last_skip"]["reason"], "you paused it")
        self.assertEqual(st["paused"], ["background", "theme"])
        data.set_rotation_paused("background", False)
        self.run_until(at(9, 30))
        self.assertTrue(self.d.set, "resumed: backgrounds change again")
        self.assertEqual(self.d.themes, [], "themes are still paused")

    def test_pause_and_resume_from_the_command_line(self):
        self.addCleanup(data.ROTATION_PAUSED.unlink, missing_ok=True)
        self.assertEqual(self.r.main(["pause", "theme"]), 0)
        self.assertEqual(self.r.main(["pause", "background"]), 0)
        self.assertEqual(data.ROTATION_PAUSED.read_text(), "theme background\n", "what the palette menu reads")
        self.assertEqual(self.r.main(["resume", "theme"]), 0)
        self.assertEqual(data.rotation_paused(), {"background"})
        self.assertEqual(self.r.main(["pause", "wallpaper"]), 1)
        self.assertEqual(data.rotation_paused(), {"background"}, "an unknown word changes nothing")

    def test_both_cycle_the_themes_backgrounds_then_the_next_theme(self):
        self.themes_on(backgrounds=True)
        self.plan.checked["All day"] = ["tokyo-night", "aura"]
        self.save()
        self.arrive(at(8, 30))
        self.run_until(at(9, 5))
        self.assertEqual(self.d.themes, [], "tokyo-night still has bright ones left")
        self.assertEqual(len(self.d.set), 2, "its other two backgrounds, 9:00 and 9:05")
        self.run_until(at(9, 10))
        self.assertEqual(self.d.themes, [("aura", self.bgs and os.path.realpath(self.bgs["aura"][0].path))],
                         "all shown: aura comes in with its own bright one")
        self.run_until(at(9, 15))
        self.assertEqual([n for n, _ in self.d.themes], ["aura", "tokyo-night"], "aura has only one: next theme")

    def test_a_theme_with_no_picks_keeps_your_background(self):
        self.themes_on(backgrounds=True)
        self.plan.checked["All day"] = ["tokyo-night", "nord"]   # your nord has no backgrounds
        tn = self.bgs["tokyo-night"]
        self.plan.theme_picks["tokyo-night"] = {tn[1].path}       # only the current one bright
        self.save()
        before = self.r.canonical_background("tokyo-night")
        self.arrive(at(8, 30))
        self.run_until(at(9, 0))
        self.assertEqual(self.d.themes, [("nord", None)])
        self.assertEqual(os.path.realpath(data.STATE_DIR / "background"), before)

    def test_a_theme_picked_elsewhere_gets_one_notification(self):
        self.themes_on()
        self.arrive(at(8, 30))
        self.run_until(at(9, 2))
        (data.STATE_DIR / "theme.name").write_text("nord\n")
        self.run_until(self.t + 5)
        link = data.STATE_DIR / "background"
        link.unlink()
        link.symlink_to(self.bgs["aura"][0].path)                # its background lands a moment later
        self.run_until(self.t + 5)
        self.assertEqual(self.d.notes, ["Your pick stays until 09:10."])

    def test_only_the_current_theme_checked_means_nothing_to_do(self):
        self.themes_on()
        self.plan.checked["All day"] = ["tokyo-night"]
        self.save()
        self.arrive(at(8, 30))
        st = self.run_until(at(9, 30))
        self.assertEqual((st["theme_status"], self.d.themes), ("single", []))

    def test_mix_it_up_pairs_any_theme_with_any_bright_background(self):
        self.themes_on(backgrounds=True)
        self.plan.mix = True
        self.plan.checked["All day"] = ["tokyo-night", "aura"]
        self.save()
        self.assertTrue(data.load_rotation("tokyo-night").mix, "saved with the settings")
        everything = {os.path.realpath(p) for p in self.plan.mixed_pool(["tokyo-night", "aura"])}
        self.assertEqual(len(everything), 4, "tokyo-night's three + aura's one, as one pool")
        self.arrive(at(8, 30))
        self.run_until(at(9, 20, 30))
        self.assertEqual(len(self.d.themes), 5, "every slot (9:00 ... 9:20) changes the theme")
        bgs = [bg for _name, bg in self.d.themes]
        self.assertTrue(all(bg in everything for bg in bgs), "only bright backgrounds from the list's themes")
        first_round = bgs[:3]   # 4 in the pool; the one showing at the start had its turn
        self.assertEqual(len(set(first_round)), 3, "no background repeats within its round")
        self.assertTrue(all(a != b for a, b in zip(bgs, bgs[1:])), "never the same background twice in a row")
        names = [n for n, _ in self.d.themes]
        self.assertTrue(all(a != b for a, b in zip(names, names[1:])), "theme changes every slot")

    def test_mix_it_up_needs_both_switches(self):
        self.themes_on(backgrounds=False)
        self.plan.mix = True
        self.save()
        self.arrive(at(8, 30))
        self.run_until(at(9, 0, 30))
        self.assertEqual(self.d.themes[-1][1], None, "Backgrounds off: plain theme rotation, your background stays")

    def test_sunset_only_decides_the_list_for_the_next_slot(self):
        # Owner's rule: no change of its own at sunrise or sunset (setUp's sun: 07:00 / 19:00); the
        # next regular slot picks from the new period's list.
        self.themes_on()
        self.plan.minutes = 120
        self.plan.set_dawn_dusk(True)
        self.plan.checked["Dusk"] = ["aura"]
        self.save()
        self.arrive(at(17, 0))
        self.run_until(at(19, 59, 55), start=at(18, 50))
        self.assertEqual(self.d.themes, [], "19:00 (sunset) isn't a 2 h slot: nothing changes then")
        self.run_until(at(20, 0, 5))
        self.assertEqual([n for n, _ in self.d.themes], ["aura"], "20:00 picks from the Dusk list")

    def test_a_pick_just_before_sunset_is_not_cut_short(self):
        # setUp's sunset is 19:00, 20 min slots. A pick at 18:53 (Dawn still): the next slot, 19:00,
        # is skipped for the pick, although Dusk began then; 19:20 brings a Dusk theme.
        self.themes_on()
        self.plan.minutes = 20
        self.plan.set_dawn_dusk(True)
        self.plan.checked["Dusk"] = ["aura"]
        self.save()
        self.arrive(at(18, 0))
        self.run_until(at(18, 53), start=at(18, 45))
        (data.STATE_DIR / "theme.name").write_text("nord\n")   # Omarchy's theme switcher
        n = len(self.d.themes)
        self.run_until(at(19, 0, 30))
        self.assertEqual(len(self.d.themes), n, "19:00 skipped: your pick stays past sunset")
        self.assertEqual(self.d.notes[-1], "Your pick stays until 19:20.")
        self.run_until(at(19, 20, 30))
        self.assertEqual(self.d.themes[-1][0], "aura", "19:20 picks from the Dusk list")

    def test_taking_the_current_theme_out_of_the_list_changes_the_theme_at_the_next_slot(self):
        # Owner's case, 2026-09-30: both on, 5 min, Dawn & Dusk switched on at 11:40 (no extra change
        # then), and at 11:44 the current dark theme was moved out of Dawn: 11:45 must bring a Dawn
        # theme, not another background of the dark one.
        self.themes_on(backgrounds=True)
        self.plan.checked["All day"] = ["tokyo-night", "aura", "nord"]
        self.save()
        self.arrive(at(11, 0))
        self.run_until(at(11, 40, 30), start=at(11, 39, 55))
        changes = len(self.d.themes) + len(self.d.set)
        self.plan.set_dawn_dusk(True)                   # Dawn starts as a copy of the everyday list
        self.save()
        self.run_until(at(11, 43))
        self.assertEqual(len(self.d.themes) + len(self.d.set), changes, "switching it on changes nothing by itself")
        current = data.current_theme_name()
        self.plan.checked["Dawn"] = [n for n in ("tokyo-night", "aura", "nord") if n != current]
        self.save()
        self.run_until(at(11, 45, 30))
        self.assertIn(self.d.themes[-1][0], self.plan.checked["Dawn"], "11:45 brings a Dawn theme")
        self.assertNotEqual(self.d.themes[-1][0], current)

    def test_grace_only_delays_when_the_list_decides_what(self):
        # Owner's rule: grace decides WHEN a change lands, never WHICH list it comes from. Switching
        # Dawn & Dusk off, the next change (whenever it lands) comes from the everyday list.
        self.themes_on()
        self.plan.set_dawn_dusk(True)
        self.plan.checked["Dawn"] = ["tokyo-night", "aura"]
        self.plan.checked["All day"] = ["tokyo-night", "nord"]
        self.save()
        self.arrive(at(11, 0))
        self.run_until(at(11, 38))
        self.plan.dawn_dusk = False                      # off at 11:38
        self.save()
        link = data.STATE_DIR / "background"
        link.unlink()
        link.symlink_to(self.bgs["aura"][0].path)        # and a pick of your own: grace until 11:43
        before = len(self.d.themes)
        self.run_until(at(11, 40, 30))
        self.assertEqual(len(self.d.themes), before, "11:40 is inside the grace: no change")
        self.run_until(at(11, 45, 30))
        self.assertEqual(len(self.d.themes), before + 1, "11:45 changes it")
        self.assertIn(self.d.themes[-1][0], self.plan.checked["All day"], "from the everyday list, not Dawn's")

    def test_period_by_the_sun(self):
        p = data.RotationPlan()
        tm = lambda h, m: time.struct_time((2026, 9, 30, h, m, 0, 2, 273, 1))
        self.assertEqual(p.period_now(tm(3, 0)), "All day")
        p.dawn_dusk = True                                    # setUp's sun: rise 07:00, set 19:00
        self.assertEqual([p.period_now(tm(h, m)) for h, m in ((6, 59), (7, 0), (18, 59), (19, 0))],
                         ["Dusk", "Dawn", "Dawn", "Dusk"])
        self.assertNotIn("dawn", p.to_dict(), "no adjustable times any more")


class AetherThemes(unittest.TestCase):
    def test_a_theme_made_with_aether_is_recognised_by_its_marker(self):
        build_fixture()
        write(HOME / ".config/omarchy/themes/oil-paintings/colors.toml", 'accent = "#99a4c0"\n')
        write(HOME / ".config/omarchy/themes/oil-paintings/.aether-managed", "aether\n")
        themes = {t.name: t for t in data.local_themes()}
        self.assertEqual(themes["oil-paintings"].title, "Oil Paintings")
        self.assertTrue(themes["oil-paintings"].aether)
        self.assertFalse(themes["aura"].aether)
        self.assertFalse(themes["tokyo-night"].aether, "built-ins never")


class RotationSets(unittest.TestCase):
    """Saved Rotation sets: whole Rotation setups under a name, to switch between."""

    def setUp(self):
        build_fixture()
        tn = [b for b in data.backgrounds_for(next(t for t in data.local_themes() if t.name == "tokyo-night"))]
        self.calm = data.RotationPlan("tokyo-night")
        self.calm.themes, self.calm.minutes = True, 60
        self.calm.set_checked("tokyo-night", True, tn)
        self.calm.toggle("tokyo-night", tn[0], tn)               # one background dimmed
        self.tn = tn

    def test_save_then_switch_between_sets(self):
        data.save_rotation_set("Calm evenings", self.calm)
        self.assertEqual(data.load_rotation().set_name, "Calm evenings", "saved as current too")
        wild = data.RotationPlan("tokyo-night")
        wild.themes, wild.backgrounds, wild.mix, wild.minutes = True, True, True, 5
        data.save_rotation_set("  Chaos   mode ", wild)
        self.assertEqual(list(data.rotation_sets()), ["Calm evenings", "Chaos mode"], "names tidied")
        plan = data.use_rotation_set("Calm evenings", "tokyo-night")
        self.assertEqual((plan.themes, plan.backgrounds, plan.mix, plan.minutes), (True, False, False, 60))
        self.assertFalse(plan.is_picked("tokyo-night", self.tn[0], self.tn), "its dimmed background came back")
        self.assertEqual(data.load_rotation().minutes, 60, "and it's the setup in use now")
        self.assertFalse(data.set_has_changes(plan))
        plan.minutes = 20
        self.assertTrue(data.set_has_changes(plan), "edited since saved")

    def test_delete_and_names(self):
        data.save_rotation_set("One", self.calm)
        data.delete_rotation_set("One")
        self.assertEqual(data.rotation_sets(), {})
        with self.assertRaises(ValueError):
            data.save_rotation_set("   ", self.calm)
        self.assertEqual(len(data.clean_set_name("x" * 100)), data.SET_NAME_MAX)
        self.assertTrue(data.set_has_changes(data.RotationPlan()), "never saved = something to save")


class AetherWorkingCopy(unittest.TestCase):
    """Aether's scratch folder `aether` and the named theme it's a copy of: one theme in OmaSkins."""

    def setUp(self):
        build_fixture()
        self.themes = HOME / ".config/omarchy/themes"
        self.named = self.make("oil-paintings", "#99a4c0", "painting-bytes")
        os.utime(self.named / "colors.toml", (time.time() - 3600,) * 2)   # saved an hour ago

    def make(self, name, accent, picture):
        d = self.themes / name
        write(d / ".aether-managed", "aether\n")
        write(d / "colors.toml", f'accent = "{accent}"\n')
        write(d / "backgrounds/wallhaven-5yyq53.jpg", picture)
        return d

    def listed(self):
        return {t.name: t for t in data.local_themes()}

    def test_the_working_copy_is_tied_by_the_same_picture_and_shown_once(self):
        self.make("aether", "#a5aec7", "painting-bytes")                 # live-applied in Aether just now
        self.assertEqual(data.aether_twin(), ("oil-paintings", True))
        themes = self.listed()
        self.assertNotIn("aether", themes, "no duplicate")
        t = themes["oil-paintings"]
        self.assertEqual((t.apply_as, t.colors.get("accent")), ("aether", "#a5aec7"), "the newer copy's look")
        self.assertEqual(data.theme_folder_for("oil-paintings"), "aether")
        write(data.STATE_DIR / "theme.name", "aether\n")
        self.assertEqual(data.current_theme_name(), "oil-paintings", "applied = this theme is current")

    def test_when_the_named_folder_is_newer_it_wins(self):
        self.make("aether", "#a5aec7", "painting-bytes")
        os.utime(self.themes / "aether/colors.toml", (time.time() - 7200,) * 2)
        t = self.listed()["oil-paintings"]
        self.assertEqual((t.apply_as, t.colors.get("accent")), ("", "#99a4c0"))
        self.assertEqual(data.theme_folder_for("oil-paintings"), "oil-paintings")
        self.assertNotIn("aether", self.listed(), "still one entry")

    def test_the_blueprint_ties_them_when_the_picture_changed(self):
        self.make("aether", "#a5aec7", "edited-picture-bytes")
        self.assertIsNone(data.aether_twin(), "no blueprint yet: a different picture is a new theme")
        self.assertIn("aether", self.listed(), "shown as its own (unsaved) theme")
        write(data.AETHER_BLUEPRINTS / "oil-paintings.json",
              json.dumps({"name": "oil-paintings", "palette": {"extendedColors": {"accent": "#A5AEC7"}}}))
        self.assertEqual(data.aether_twin(), ("oil-paintings", True))
        self.assertNotIn("aether", self.listed())

    def test_a_picture_copied_into_another_theme_is_not_a_false_match(self):
        # Owner, 2026-10-01: you may copy a picture you like from one theme into another.
        self.make("sea-glass", "#20c080", "painting-bytes")               # same picture, its own colours
        self.make("aether", "#a5aec7", "painting-bytes")                  # an edit of Oil Paintings
        self.assertEqual(data.aether_twin(), ("oil-paintings", True), "the colours decide, not the picture")
        self.assertIn("sea-glass", self.listed(), "sea-glass untouched and listed")

    def test_a_new_theme_with_a_borrowed_picture_is_not_hidden(self):
        self.make("aether", "#e0402a", "painting-bytes")                  # new colours, a borrowed picture
        self.assertIsNone(data.aether_twin(), "a shared picture alone is never proof")
        self.assertIn("aether", self.listed(), "shown as its own theme until you say otherwise")
        self.assertEqual(data.aether_candidates()[0]["name"], "oil-paintings", "but offered first when asked")

    def test_two_themes_that_fit_equally_means_asking(self):
        self.make("oil-paintings-2", "#99a4c1", "painting-bytes")
        self.make("aether", "#a5aec7", "painting-bytes")
        self.assertIsNone(data.aether_twin(), "never guess between two")
        self.assertEqual({c["name"] for c in data.aether_candidates()[:2]}, {"oil-paintings", "oil-paintings-2"})

    def test_a_theme_you_pick_can_be_combined(self):
        from omaskins import run
        self.make("aether", "#e0402a", "new-picture")
        with self.assertRaises(run.NotAllowed):
            run.merge_aether("oil-paintings")                             # not sure: no silent combine
        run.merge_aether("oil-paintings", picked=True)                    # you chose it when asked
        self.assertFalse((self.themes / "aether").exists())
        self.assertEqual(data._accent(self.named / "colors.toml"), "#e0402a")

    def test_remove_takes_both_copies(self):
        self.make("aether", "#a5aec7", "painting-bytes")
        steps = data.remove_theme_action(self.listed()["oil-paintings"]).steps
        taken = [s[1].name for s in steps if s[0] == "take_out"]
        self.assertEqual(sorted(taken), ["aether", "oil-paintings"])

    def test_combine_keeps_the_newer_settings_and_every_picture_once(self):
        from omaskins import run
        scratch = self.make("aether", "#a5aec7", "painting-bytes")        # newer, same picture
        write(scratch / "backgrounds/second.jpg", "second-picture")       # a picture only the copy has
        write(scratch / "backgrounds/clash.jpg", "copy-version")          # same name, different picture
        write(self.named / "backgrounds/clash.jpg", "named-version")
        write(scratch / "icons.theme", "Yaru-blue\n")
        write(self.named / "kitty.conf", "old override\n")                # the older settings' extra file
        write(data.STATE_DIR / "theme.name", "aether\n")                  # the working copy is applied
        link = data.STATE_DIR / "background"
        link.unlink()
        link.symlink_to(scratch / "backgrounds/second.jpg")
        plan = data.RotationPlan()
        plan.themes, plan.checked["All day"] = True, ["aether", "tokyo-night"]
        plan.theme_picks["aether"] = {scratch / "backgrounds/second.jpg"}
        data.save_rotation(plan)
        calls = []

        def fake(argv, **kw):
            calls.append((argv, kw.get("env", {}).get("OMARCHY_THEME_SKIP_BACKGROUND")))
            if argv[0] == "omarchy-theme-bg-set":
                link.unlink()
                link.symlink_to(argv[1])
            if argv[0] == "omarchy-theme-set":
                (data.STATE_DIR / "theme.name").write_text(argv[1] + "\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        real_run, run.subprocess.run = run.subprocess.run, fake
        try:
            run.merge_aether("oil-paintings")
        finally:
            run.subprocess.run = real_run
        self.assertFalse(scratch.exists(), "the extra folder is gone")
        self.assertEqual(data._accent(self.named / "colors.toml"), "#a5aec7", "the newer colours")
        self.assertEqual((self.named / "icons.theme").read_text(), "Yaru-blue\n")
        self.assertFalse((self.named / "kitty.conf").exists(), "settings are the newer folder's, whole")
        pics = sorted(p.name for p in data._images(self.named / "backgrounds"))
        self.assertEqual(pics, ["clash-2.jpg", "clash.jpg", "second.jpg", "wallhaven-5yyq53.jpg"],
                         "every picture once; the same name with another picture kept as -2")
        self.assertEqual((self.named / "backgrounds/clash.jpg").read_text(), "named-version", "nothing overwritten")
        self.assertEqual(data.current_theme_name(), "oil-paintings")
        self.assertEqual(os.path.realpath(link), os.path.realpath(self.named / "backgrounds/second.jpg"),
                         "the very same background, now from the combined theme")
        self.assertEqual(calls[-1], (["omarchy-theme-set", "oil-paintings"], "1"), "Omarchy picked no background")
        plan = data.load_rotation()
        self.assertEqual(plan.checked["All day"], ["oil-paintings", "tokyo-night"])
        self.assertEqual({p.name for p in plan.theme_picks["oil-paintings"]}, {"second.jpg"})
        self.assertTrue(any(run.AETHER_MERGED.glob("*/aether/colors.toml")), "a copy kept in the cache")

    def test_combine_refuses_anything_but_the_tied_pair(self):
        from omaskins import run
        self.make("aether", "#a5aec7", "another-picture")                  # not a copy of oil-paintings
        with self.assertRaises(run.NotAllowed):
            run.merge_aether("oil-paintings")
        self.assertTrue((self.themes / "aether").exists())

    def test_no_working_copy_nothing_changes(self):
        self.assertIsNone(data.aether_twin())
        self.assertEqual(self.listed()["oil-paintings"].apply_as, "")
        self.assertEqual(data.theme_folder_for("oil-paintings"), "oil-paintings")


class FontSetup(unittest.TestCase):
    """OmaSkins' own font config (each font as itself) and the font previews for Browse."""

    def test_private_fontconfig_leaves_out_only_omarchys_user_rule(self):
        conf = SANDBOX / "etc-fonts"
        (conf / "conf.d").mkdir(parents=True, exist_ok=True)
        write(conf / "fonts.conf", '<fontconfig>\n\t<dir>/usr/share/fonts</dir>\n'
                                   '\t<include ignore_missing="yes">conf.d</include>\n</fontconfig>\n')
        for name in ("10-hinting.conf", "50-omarchy.conf", "50-user.conf", "60-latin.conf"):
            write(conf / "conf.d" / name, "<fontconfig/>")
        old = data.SYSTEM_FONTCONFIG
        data.SYSTEM_FONTCONFIG = conf / "fonts.conf"
        try:
            text = data.private_fontconfig().read_text()
        finally:
            data.SYSTEM_FONTCONFIG = old
        self.assertNotIn("conf.d/50-user.conf</include>", text, "Omarchy's every-monospace-font rule is loaded from it")
        self.assertNotIn('prefix="xdg">fontconfig/fonts.conf', text)
        for name in ("10-hinting.conf", "50-omarchy.conf", "60-latin.conf"):
            self.assertIn(str(conf / "conf.d" / name), text)
        self.assertIn('prefix="xdg">fontconfig/conf.d</include>', text, "your own conf.d stays")
        self.assertIn("<dir>/usr/share/fonts</dir>", text)

    def test_programs_started_get_the_normal_font_setup(self):
        os.environ["FONTCONFIG_FILE"], os.environ["OMASKINS_FONTCONFIG"] = "/x/fonts.conf", "1"
        try:
            env = data.child_env()
            self.assertNotIn("FONTCONFIG_FILE", env)
            self.assertNotIn("OMASKINS_FONTCONFIG", env)
            del os.environ["OMASKINS_FONTCONFIG"]
            self.assertEqual(data.child_env()["FONTCONFIG_FILE"], "/x/fonts.conf", "one you set yourself stays")
        finally:
            os.environ.pop("FONTCONFIG_FILE", None)
            os.environ.pop("OMASKINS_FONTCONFIG", None)

    def test_the_preview_keeps_the_plain_regular_face(self):
        names = ["usr/share/fonts/TTF/AgaveNerdFont-Bold.ttf", "usr/share/fonts/TTF/AgaveNerdFontMono-Regular.ttf",
                 "usr/share/fonts/TTF/AgaveNerdFont-Regular.ttf", "usr/share/fonts/TTF/AgaveNerdFontPropo-Regular.ttf",
                 "usr/share/licenses/x/LICENSE"]
        self.assertEqual(data._preview_file_in(names), "usr/share/fonts/TTF/AgaveNerdFont-Regular.ttf")
        self.assertEqual(data._preview_file_in(["a/XMono-Regular.otf", "a/X-Bold.otf"]), "a/XMono-Regular.otf")
        self.assertIsNone(data._preview_file_in(["usr/share/licenses/x/LICENSE"]))

    def test_preview_font_needs_its_file(self):
        build_fixture()
        f = write(data.PREVIEW_FONTS / "AgaveNerdFont-Regular.ttf", "font")
        write(data.PREVIEW_FONTS / "ttf-agave-nerd.json", json.dumps({"family": "Agave Nerd Font", "file": str(f)}))
        self.assertEqual(data.preview_font("ttf-agave-nerd")["family"], "Agave Nerd Font")
        f.unlink()
        self.assertIsNone(data.preview_font("ttf-agave-nerd"), "file gone (cache cleared): download again")


class ThemeStars(unittest.TestCase):
    def test_stars_come_from_one_batched_query_and_are_cached(self):
        build_fixture()
        community = [data.CommunityTheme("A", "https://github.com/me/a", ""), data.CommunityTheme("B", "https://github.com/you/b", "")]
        keys = sorted(t.key for t in community)
        calls, real_run, real_which = [], data._run, data.shutil.which
        def fake_run(cmd, timeout=15):
            calls.append(cmd)
            return json.dumps({"data": {"r0": {"stargazerCount": 5}, "r1": None}})
        data._run, data.shutil.which = fake_run, lambda name: "/usr/bin/" + name
        try:
            data.STARS_CACHE.unlink(missing_ok=True)
            stars = data.theme_stars(community)
            self.assertEqual(stars, {keys[0]: 5}, "a repo GitHub doesn't know (renamed/deleted) simply has no stars")
            self.assertEqual(len(calls), 1, "one batched query")
            self.assertEqual(calls[0][:3], ["gh", "api", "graphql"])
            data.STARS_CACHE.write_text(json.dumps({"at": time.time(), "stars": {keys[0]: 5, keys[1]: 1}}))
            self.assertEqual(data.theme_stars(community), {keys[0]: 5, keys[1]: 1})
            self.assertEqual(len(calls), 1, "within a day the cache answers")
            data._run = lambda cmd, timeout=15: ""   # offline
            data.STARS_CACHE.unlink()
            self.assertEqual(data.theme_stars(community), {}, "offline: no stars, Browse falls back to A-Z")
            self.assertFalse(data.STARS_CACHE.exists(), "a failed fetch isn't cached")
        finally:
            data._run, data.shutil.which = real_run, real_which


class BuiltinHiding(unittest.TestCase):
    """The real terminal commands, rehearsed in the sandbox: `sudo` is a no-op function and pacman.conf
    is a copy of this machine's (or a stock one)."""
    STOCK = "[options]\nColor\nHoldPkg = pacman glibc\nArchitecture = auto\n\n[core]\nInclude = /etc/pacman.d/mirrorlist\n"

    def setUp(self):
        build_fixture()
        shutil.rmtree(data.HIDDEN_BUILTINS, ignore_errors=True)
        data.PACMAN_CONF.parent.mkdir(parents=True, exist_ok=True)
        try:
            data.PACMAN_CONF.write_text(Path("/etc/pacman.conf").read_text())
        except OSError:
            data.PACMAN_CONF.write_text(self.STOCK)
        self.original_conf = data.PACMAN_CONF.read_text()

    def sh(self, cmd):
        r = subprocess.run(["bash", "-c", 'sudo() { "$@"; }; pacman() { echo "pacman $*" >> "$LOG"; }; ' + cmd],
                           env=dict(os.environ, LOG=str(SANDBOX / "pacman.log")), capture_output=True, text=True)
        return r.returncode

    def rules(self):
        """NoExtract rules the test added (the copied real pacman.conf may already hold the owner's own)."""
        before = set(self.original_conf.splitlines())
        return [l for l in data.PACMAN_CONF.read_text().splitlines() if l.startswith("NoExtract") and l not in before]

    def test_hide_moves_it_aside_and_keeps_updates_from_bringing_it_back(self):
        self.assertEqual(self.sh(data.builtin_terminal_command("hide", "nord")), 0)
        self.assertFalse((OMARCHY / "themes/nord").exists(), "gone from Omarchy's list")
        self.assertTrue((data.HIDDEN_BUILTINS / "nord/backgrounds/n.png").is_file(), "held aside, intact")
        self.assertTrue(data.builtin_held_aside("nord"))
        self.assertEqual(self.rules(), [f"NoExtract = {str(OMARCHY).lstrip('/')}/themes/nord/*"])
        lines = data.PACMAN_CONF.read_text().splitlines()
        self.assertEqual(lines[lines.index("[options]") + 1], self.rules()[0], "the rule sits in [options]")
        self.assertLess(lines.index(self.rules()[0]), next(i for i, l in enumerate(lines) if l == "[core]" or
                                                            (l.startswith("[") and l != "[options]")))
        self.assertEqual(self.run_builtin_state("nord"), (False, True))
        # hiding again (e.g. a double click after it finished) changes nothing and adds no second rule
        self.assertNotEqual(self.sh(data.builtin_terminal_command("hide", "nord")), 0)
        self.assertEqual(len(self.rules()), 1)

    def run_builtin_state(self, name):
        from omaskins import run
        return run.builtin_state(name)

    def test_restore_moves_it_back_and_drops_exactly_that_rule(self):
        self.sh(data.builtin_terminal_command("hide", "nord"))
        self.sh(data.builtin_terminal_command("hide", "tokyo-night"))
        self.assertEqual(len(self.rules()), 2)
        self.assertEqual(self.sh(data.builtin_terminal_command("unhide", "nord")), 0)
        self.assertTrue((OMARCHY / "themes/nord/backgrounds/n.png").is_file())
        self.assertFalse(data.builtin_held_aside("nord"))
        self.assertEqual(self.rules(), [f"NoExtract = {str(OMARCHY).lstrip('/')}/themes/tokyo-night/*"], "only nord's rule went")
        self.sh(data.builtin_terminal_command("unhide", "tokyo-night"))
        self.assertEqual(data.PACMAN_CONF.read_text(), self.original_conf, "pacman.conf exactly as before")

    def test_restore_moves_the_kept_copy_back_only_if_omarchy_wasnt_updated(self):
        real = data.package_version
        try:
            data.package_version = lambda pkg: "1.0-1"
            self.sh(data.builtin_terminal_command("hide", "nord"))
            data.note_removed_version("nord", "try-omarchy-runtime")
            self.assertIn("sudo mv", data.builtin_restore_action("nord", "try-omarchy-runtime").command,
                          "same version: the kept copy is current, move it back")
            data.package_version = lambda pkg: "1.1-1"   # Omarchy updated meanwhile
            a = data.builtin_restore_action("nord", "try-omarchy-runtime")
            self.assertIn("pacman -S", a.command, "updated: reinstall for the fresh version")
            self.assertEqual(self.sh(a.steps[0][1]), 0)
            self.assertIn("pacman -S --noconfirm try-omarchy-runtime", (SANDBOX / "pacman.log").read_text())
            self.assertFalse(data.builtin_held_aside("nord"), "the outdated kept copy is cleared")
            self.assertEqual(self.rules(), [])
        finally:
            data.package_version = real

    def test_hide_asks_first_hide_from_omaskins_or_remove_entirely(self):
        a = data.builtin_hide_action("nord", "try-omarchy-runtime")
        self.assertEqual(a.label, "Hide")
        self.assertIn("Do you want to hide this stock theme from OmaSkins, or remove it entirely?", a.note)
        (soft_label, soft), (hard_label, hard) = a.choices
        self.assertEqual((soft_label, hard_label), ("Hide from OmaSkins", "Remove entirely"))
        self.assertEqual(soft.password, "", "hiding from OmaSkins never asks for a password")
        self.assertEqual(soft.steps, (("hide_in_omaskins", "nord", True),))
        self.assertIn("removing or restoring one entirely asks for your password", hard.password)
        self.assertEqual([st[0] for st in hard.steps], ["terminal", "wait_builtin", "note_removed_version"])
        tn = [t for t in data.local_themes() if t.name == "tokyo-night"][0]
        self.assertEqual(data.theme_actions(tn, None, "aura")[-1].label, "Hide")

    def test_hidden_from_omaskins_is_a_list_of_names_with_a_password_free_restore(self):
        from omaskins import run
        run.perform(data.builtin_hide_action("nord").choices[0][1].steps)
        self.assertEqual(data.hidden_in_omaskins(), {"nord"})
        self.assertTrue((OMARCHY / "themes/nord").is_dir(), "still installed, still updated")
        self.assertEqual(data.PACMAN_CONF.read_text(), self.original_conf, "pacman.conf untouched")
        back = data.builtin_restore_action("nord", "x", hidden_only=True)
        self.assertEqual(back.password, "")
        run.perform(back.steps)
        self.assertEqual(data.hidden_in_omaskins(), set())
        self.assertTrue(str(data.BUILTIN_STATE).startswith(str(HOME)), "sandboxed")

    def test_odd_names_are_refused(self):
        for bad in ("../etc", "a b", "x/y", "", ".hidden", "nord;rm"):
            with self.assertRaises(ValueError):
                data.builtin_hide_action(bad)
        with self.assertRaises(ValueError):
            data.builtin_terminal_command("reinstall", "nord", "pkg; rm -rf ~")

    def test_removed_builtins_are_shipped_ones_whose_folder_is_gone(self):
        build_fixture()
        self.assertEqual(data.removed_builtins(["nord", "tokyo-night", "white"]), ["white"])
        self.assertEqual(data.shipped_builtins(""), [])

    def test_removing_a_rotating_theme_takes_it_out_of_the_rotation(self):
        plan = data.RotationPlan("tokyo-night")
        plan.set_checked("aura", True)
        plan.set_dawn_dusk(True)
        self.assertTrue(plan.in_rotation("aura"))
        plan.drop("aura")
        self.assertFalse(plan.in_rotation("aura"))


class RemoteBackgrounds(unittest.TestCase):
    def test_listing_keeps_images_sorted_by_name(self):
        listing = """[
          {"name": "2-b.png", "type": "file", "download_url": "https://raw.example/2-b.png"},
          {"name": "README.md", "type": "file", "download_url": "https://raw.example/README.md"},
          {"name": "old", "type": "dir", "download_url": null},
          {"name": "1-a.JPG", "type": "file", "download_url": "https://raw.example/1-a.JPG"}]"""
        self.assertEqual(data.parse_background_listing(listing),
                         ["https://raw.example/1-a.JPG", "https://raw.example/2-b.png"])

    def test_errors_and_rate_limits_give_nothing(self):
        self.assertEqual(data.parse_background_listing('{"message": "API rate limit exceeded"}'), [])
        self.assertEqual(data.parse_background_listing("not json"), [])


class GlobalCorners(unittest.TestCase):
    def setUp(self):
        build_fixture()
        write(HOME / ".config/hypr/looknfeel.lua", "-- your look and feel\n")

    def run_action(self, on, px):
        a = data.corners_action(on, px)
        self.assertEqual(a.steps, (("apply_corners", on, max(0, min(data.CORNERS_MAX, px))), ("notify", "restyle"),
                                   ("shell_restyle",)), "live: no forced reload, no shell restart")
        from omaskins import run
        run.write_corners(on, px)

    def test_on_writes_its_own_file_and_requires_it_once(self):
        self.assertEqual(data.corners_setting(), (False, None))
        self.run_action(True, 12)
        self.run_action(True, 16)
        self.assertEqual(data.corners_setting(), (True, 16))
        self.assertEqual((HOME / ".config/hypr/looknfeel.lua").read_text().count(data.CORNERS_REQUIRE), 1)

    def test_off_keeps_the_file_but_drops_the_override(self):
        self.run_action(True, 12)
        self.run_action(False, 12)
        self.assertEqual(data.corners_setting(), (False, None))
        self.assertTrue(data.CORNERS_FILE.exists(), "looknfeel.lua still requires it")

    def test_radius_is_clamped(self):
        self.run_action(True, 999)
        self.assertEqual(data.corners_setting(), (True, data.CORNERS_MAX))

    @unittest.skipUnless(shutil.which("luac"), "luac not installed")
    def test_file_is_valid_lua(self):
        for on in (True, False):
            f = SANDBOX / "corners.lua"
            f.write_text(data.corners_file_text(on, 10))
            subprocess.run(["luac", "-p", str(f)], check=True)
        # the owner's real looknfeel.lua (a copy) stays valid Lua with the require line added
        try:
            write(HOME / ".config/hypr/looknfeel.lua", (REAL_HOME / ".config/hypr/looknfeel.lua").read_text())
        except OSError:
            pass
        self.run_action(True, 10)
        subprocess.run(["luac", "-p", str(HOME / ".config/hypr/looknfeel.lua")], check=True)

    def test_corners_go_live_without_a_forced_reload(self):
        from omaskins import run
        ran, real = [], run.run
        run.run = lambda argv, timeout=300: (ran.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1]
        try:
            # Hyprland picks the saved file up by itself: no reload is forced
            seq = iter([0, 0, 14])
            run.apply_corners(True, 14, rounding=lambda: next(seq, 14), wait=1, poll=0)
            self.assertEqual(ran, [], "saving the file reloads Hyprland already; a forced reload blinks twice")
            self.assertEqual(data.corners_setting(), (True, 14))
            # it didn't: one reload is forced
            run.apply_corners(True, 20, rounding=lambda: 14, wait=0.05, poll=0)
            self.assertEqual(ran, [["hyprctl", "reload"]])
            # already that radius: nothing to wait for
            ran.clear()
            run.apply_corners(True, 14, rounding=lambda: 14, wait=5, poll=0)
            self.assertEqual(ran, [])
            # the shell re-reads its style with the current theme's files, no restart
            theme = SANDBOX / "theme-now"
            write(theme / "colors.toml", "accent = '#fff'\n")
            run.shell_restyle(theme)
            import base64
            self.assertEqual(ran[-1], ["omarchy-shell", "shell", "applyTheme",
                                       base64.b64encode(b"accent = '#fff'\n").decode(), ""])
            run.check(ran[-1])
            # mid-action "live now" reaches the window
            heard = []
            run.perform((("notify", "restyle"),), notify=heard.append)
            self.assertEqual(heard, ["restyle"])
        finally:
            run.run = real

    def test_missing_looknfeel_is_a_clear_failure(self):
        (HOME / ".config/hypr/looknfeel.lua").unlink()
        from omaskins import run
        with self.assertRaises(run.StepFailed):
            self.run_action(True, 10)


class ReadingChangesNothing(unittest.TestCase):
    def test_reading_everything_leaves_home_untouched(self):
        build_fixture()
        before = snapshot(HOME)
        community = data.parse_community(PAGE)
        local = data.local_themes()
        data.match_installed(community, local)
        for t in local:
            data.backgrounds_for(t, data.current_theme_name(), data.current_background())
            data.theme_actions(t, None, "x")
        data.export_plan(local[0], community, None, None)
        self.assertEqual(snapshot(HOME), before)

    def test_app_never_runs_commands_itself(self):
        # Real actions may only go through omaskins/run.py and its allow-list.
        src = (ROOT / "omaskins/app.py").read_text()
        for bad in ("subprocess", "os.system", "Popen", "spawn_async"):
            self.assertNotIn(bad, src, f"app.py must not use {bad}: go through run.py")


class PasswordHeadsUp(unittest.TestCase):
    def test_only_system_package_actions_say_they_need_a_password(self):
        build_fixture()
        local = {t.name: t for t in data.local_themes()}
        community = data.parse_community(PAGE)
        everyday = (data.theme_actions(None, community[1], "tokyo-night")                 # Add
                    + data.theme_actions(local["aura"], community[0], "tokyo-night"))     # Apply, Remove yours
        self.assertEqual([a.label for a in everyday], ["Add", "Apply", "Remove"])
        self.assertFalse(any(a.password for a in everyday), "adding/removing your own themes never asks")
        for a in (data.builtin_hide_action("nord").choices[1][1], data.builtin_restore_action("nord", "omarchy")):
            self.assertIn("Built-in themes are part of Omarchy's own system package", a.password)
        pkg = data.FontPackage("ttf-x-nerd", "1", "x", installed=False, omarchy_pick="X Nerd Font")
        self.assertTrue(all(a.password for a in data.font_actions(package=pkg)))
        use = data.font_actions(font=data.Font("X Nerd Font", "ttf-x-nerd"), current_package="other")
        self.assertEqual([a.password for a in use if a.label == "Use"], [""], "switching fonts never asks")


class CommandRunner(unittest.TestCase):
    def setUp(self):
        build_fixture()
        from omaskins import run
        self.run = run

    def test_allow_list_is_exactly_batches_1_to_3(self):
        self.assertEqual(sorted(self.run.ALLOWED), ["git", "hyprctl", "nautilus", "omarchy-font-set", "omarchy-git-url-check",
                                                    "omarchy-launch-floating-terminal-with-presentation",
                                                    "omarchy-notification-send", "omarchy-shell",
                                                    "omarchy-theme-bg-set", "omarchy-theme-set", "pkill"])
        for bad in (["omarchy-theme-install", "x"], ["sudo", "rm", "-rf", "/"], ["pacman", "-S", "x"], ["sh", "-c", "x"]):
            with self.assertRaises(self.run.NotAllowed, msg=bad):
                self.run.check(bad)

    def test_git_may_only_clone_into_the_holding_folder(self):
        ok = ["git", "clone", "--progress", "--", "https://github.com/a/b", str(data.PARTIAL_THEMES / "new-one")]
        self.run.check(ok)
        for bad in (["git", "push"], ["git", "clone", "https://x", str(data.PARTIAL_THEMES / "y")],
                    ["git", "clone", "--", "https://x", str(data.PARTIAL_THEMES / "y")],   # the old form
                    ["git", "clone", "--progress", "--", "https://x", "/tmp/elsewhere"],
                    ["git", "clone", "--progress", "--", "https://x", str(data.USER_THEMES / "y")]):  # straight into themes
            with self.assertRaises(self.run.NotAllowed, msg=bad):
                self.run.check(bad)

    def test_clone_progress_lines_become_one_rising_percentage(self):
        cp = self.run.clone_percent
        self.assertEqual(cp("remote: Counting objects: 100% (40/40), done."), 2)
        self.assertEqual(cp("Receiving objects:   0% (0/40)"), 5)
        self.assertEqual(cp("Receiving objects:  50% (20/40), 1.2 MiB | 2.0 MiB/s"), 47)
        self.assertEqual(cp("Resolving deltas: 100% (3/3), done."), 96)
        self.assertEqual(cp("Updating files: 100% (12/12), done."), 100)
        self.assertIsNone(cp("Cloning into '/x'..."))
        self.assertIsNone(cp("fatal: repository not found"))

    def test_ascii_bar_never_changes_width(self):
        widths = {len(data.ascii_bar(p)) for p in (-5, 0, 1, 9, 50, 99, 100, 250)}
        self.assertEqual(len(widths), 1)
        self.assertEqual(data.ascii_bar(0), "[" + "." * 24 + "]   0%")
        self.assertEqual(data.ascii_bar(100), "[" + "#" * 24 + "] 100%")
        self.assertEqual(data.ascii_bar(50), "[" + "#" * 12 + "." * 12 + "]  50%")

    def test_a_real_clone_feeds_the_bar(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        src = HOME / "src-repo"
        src.mkdir(parents=True)
        (src / "colors.toml").write_text("accent = '#ffffff'\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        for cmd in (["git", "init", "-q"], ["git", "add", "."], ["git", "commit", "-qm", "x"]):
            subprocess.run(cmd, cwd=src, env=env, check=True)
        tmp, dest = data.PARTIAL_THEMES / "src-repo", data.USER_THEMES / "src-repo"
        seen = []
        self.run.perform((("clear_partial", tmp), ("run", ["git", "clone", "--progress", "--", src.as_uri(), str(tmp)]),
                          ("move_in", tmp, dest)), progress=seen.append)
        self.assertTrue((dest / "colors.toml").is_file())
        self.assertTrue(seen, "no progress reported")
        self.assertEqual(seen, sorted(set(seen)))   # only ever goes up
        self.assertEqual(seen[-1], 100)

    def test_removing_a_theme_counts_down_to_zero(self):
        import time
        folder = data.USER_THEMES / "doomed"
        (folder / "backgrounds").mkdir(parents=True)
        outside = HOME / "keep-me"
        outside.mkdir(parents=True)
        (outside / "precious.png").write_bytes(b"x" * 500)
        for i in range(8):
            (folder / "backgrounds" / f"{i}.png").write_bytes(b"x" * 1000 * (i + 1))
        (folder / "colors.toml").write_text("accent = '#fff'\n")
        (folder / "link-out").symlink_to(outside)       # must go, without touching what it points to
        gone = data.REMOVING_THEMES / "doomed"
        seen, t0 = [], time.monotonic()
        self.run.take_out(folder, gone)
        self.assertFalse(folder.exists(), "gone from themes/ in one step, before any deleting")
        self.run.delete_counting(gone, seen.append, at_least=0.3)
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)
        self.assertFalse(gone.exists())
        self.assertTrue((outside / "precious.png").is_file())
        self.assertEqual(seen, sorted(set(seen), reverse=True))   # only ever goes down
        self.assertEqual(seen[-1], 0)

    def test_removal_only_takes_your_own_theme_folders(self):
        (data.USER_THEMES / "mine").mkdir(parents=True)
        for folder, gone in ((HOME / "Documents", data.REMOVING_THEMES / "Documents"),
                             (data.USER_THEMES / "mine", data.REMOVING_THEMES / "other-name"),
                             (data.USER_THEMES / "mine", HOME / "mine"),
                             (data.USER_THEMES / ".hidden", data.REMOVING_THEMES / ".hidden")):
            with self.assertRaises(self.run.NotAllowed, msg=(folder, gone)):
                self.run.take_out(folder, gone)
        with self.assertRaises(self.run.NotAllowed):
            self.run.delete_counting(HOME)
        self.run.check(["omarchy-notification-send", "Theme removed", "mine"])
        with self.assertRaises(self.run.NotAllowed):
            self.run.check(["omarchy-notification-send", "anything else", "mine"])

    def test_remove_action_is_the_countdown(self):
        aura = next(t for t in data.local_themes() if t.name == "aura")
        a = [x for x in data.theme_actions(aura, None, "tokyo-night") if x.label == "Remove"][0]
        self.assertEqual(a.bar, 100)
        self.assertEqual([st[0] for st in a.steps], ["take_out", "delete_counting", "run"])

    def test_shell_strings_and_odd_input_are_refused(self):
        for bad in ("omarchy-theme-set nord", "", [], ["omarchy-theme-set", 5], None):
            with self.assertRaises(self.run.NotAllowed, msg=repr(bad)):
                self.run.check(bad)

    def test_file_steps_stay_inside_your_backgrounds(self):
        mine = HOME / ".config/omarchy/backgrounds/tokyo-night/mine.png"
        for step in (("remove_file", HOME / ".config/omarchy/themes/aura/colors.toml"),
                     ("remove_file", HOME / ".config/omarchy/backgrounds/../themes/aura/colors.toml"),
                     ("mkdir", HOME / "elsewhere"), ("copy_in", mine, HOME / "Pictures"),
                     ("remove_file", data.USER_BACKGROUNDS), ("frobnicate", mine)):
            with self.assertRaises(self.run.NotAllowed, msg=step):
                self.run.perform([step])
        self.assertTrue((HOME / ".config/omarchy/themes/aura/colors.toml").exists())

    def test_copy_never_overwrites_and_remove_deletes_only_that_file(self):
        src = write(SANDBOX / "src/new.png", "picture")
        dest = data.USER_BACKGROUNDS / "nord"
        self.run.perform([("copy_in", src, dest)])
        self.run.perform([("copy_in", src, dest)])                      # identical: left alone
        write(src, "a different picture")
        self.run.perform([("copy_in", src, dest)])                      # same name, different: -2
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["new-2.png", "new.png"])
        self.assertEqual((dest / "new.png").read_text(), "picture")
        self.run.perform([("remove_file", dest / "new-2.png")])
        self.assertEqual([p.name for p in dest.iterdir()], ["new.png"])

    @unittest.skipUnless(shutil.which("omarchy-git-url-check"), "not on an Omarchy system")
    def test_a_refused_url_stops_before_the_clone(self):
        with self.assertRaises(self.run.StepFailed):
            self.run.perform([("run", ["omarchy-git-url-check", "--upload-pack=evil"]),
                              ("run", ["git", "clone", "--progress", "--", "x", str(data.USER_THEMES / "never")])])
        self.assertFalse((data.USER_THEMES / "never").exists())

    def test_a_theme_only_appears_once_its_download_is_complete(self):
        tmp, dest = data.PARTIAL_THEMES / "fresh", data.USER_THEMES / "fresh"
        # Simulate the clone step: a finished download in the holding folder...
        self.run.perform([("clear_partial", tmp)])
        write(tmp / ".git/config", "")
        write(tmp / "colors.toml", 'accent = "#123456"\n')
        self.assertFalse(dest.exists(), "nothing shows in themes while downloading")
        self.run.perform([("move_in", tmp, dest)])
        self.assertTrue((dest / "colors.toml").exists())
        self.assertFalse(tmp.exists())

    def test_an_empty_or_failed_download_never_lands(self):
        tmp, dest = data.PARTIAL_THEMES / "broken", data.USER_THEMES / "broken"
        self.run.perform([("clear_partial", tmp)])
        write(tmp / ".git/config", "")                     # only .git: checkout never happened
        with self.assertRaises(self.run.StepFailed):
            self.run.perform([("clear_partial", tmp), ("move_in", tmp, dest)])
        self.assertFalse(dest.exists())
        self.assertFalse(tmp.exists(), "the half-finished download is cleaned up")
        for bad in (("move_in", tmp, HOME / "elsewhere"), ("move_in", HOME / "x", dest), ("clear_partial", HOME)):
            with self.assertRaises(self.run.NotAllowed, msg=bad):
                self.run.perform([bad])

    def test_apply_is_greyed_for_an_empty_theme_folder(self):
        half = write(data.USER_THEMES / "half/.git/config", "").parent.parent
        t = data.LocalTheme(name="half", path=half, builtin=False)
        apply = data.theme_actions(t, None, "tokyo-night")[0]
        self.assertEqual(apply.label, "Apply")
        self.assertIn("Still downloading", apply.blocked)

    def test_every_live_action_passes_the_allow_list(self):
        local = {t.name: t for t in data.local_themes()}
        community = data.parse_community(PAGE)
        acts = (data.theme_actions(None, community[1], "tokyo-night") + data.theme_actions(local["aura"], None, "x")
                + data.background_actions(data.backgrounds_for(local["tokyo-night"])[2], "tokyo-night")
                + [data.open_folder_action("nord"), data.add_background_action("nord", ["/tmp/a.png"]),
                   data.copy_to_theme_action(data.backgrounds_for(local["aura"])[0], "tokyo-night")])
        for a in acts:
            self.assertTrue(a.steps, a.label)
            for st in a.steps:
                if st[0] in ("run", "launch"):
                    self.run.check(st[1])

    def test_the_terminal_only_hides_or_restores_one_builtin(self):
        t = "omarchy-launch-floating-terminal-with-presentation"
        for kind in ("hide", "unhide"):
            self.run.check([t, data.builtin_terminal_command(kind, "nord")])
        good = data.builtin_terminal_command("hide", "nord")
        for bad in (good + "; rm -rf ~", good.replace("nord", "../x", 1), "echo 'Removing nord...'; sudo rm -rf /",
                    good.replace("sudo mv", "sudo cp")):
            with self.assertRaises(self.run.NotAllowed, msg=bad):
                self.run.check([t, bad])
        self.run.check(["hyprctl", "reload"])
        self.run.check(["omarchy-shell", "shell", "applyTheme", "YWJj", ""])
        for bad in (["hyprctl", "dispatch", "exit"], ["hyprctl", "keyword", "x"], ["omarchy-restart-shell"],
                    ["omarchy-shell", "shell", "enablePlugin", "x", "y"], ["omarchy-shell", "shell", "applyTheme", "a;b", ""],
                    ["omarchy-shell", "lock", "lock"]):
            with self.assertRaises(self.run.NotAllowed, msg=bad):
                self.run.check(bad)

    def test_the_terminal_only_adds_or_drops_one_font_package(self):
        t = "omarchy-launch-floating-terminal-with-presentation"
        self.run.check([t, data.font_terminal_command("add", "ttf-x-nerd")])
        self.run.check([t, data.font_terminal_command("drop", "otf-y-nerd")])
        for bad in ("echo 'Installing ttf-x-nerd...'; omarchy-pkg-add ttf-other-nerd",
                    "echo 'Installing ttf-x-nerd...'; omarchy-pkg-drop ttf-x-nerd",
                    "echo 'Installing ttf-x-nerd...'; omarchy-pkg-add ttf-x-nerd; rm -rf ~",
                    "echo 'Installing firefox...'; omarchy-pkg-add firefox",
                    "omarchy-pkg-add ttf-x-nerd", "bash"):
            with self.assertRaises(self.run.NotAllowed, msg=bad):
                self.run.check([t, bad])
        self.run.check(["omarchy-font-set", "Some Font"])
        with self.assertRaises(self.run.NotAllowed):
            self.run.check(["omarchy-font-set", "a", "b"])

    def test_waiting_for_the_terminal(self):
        term = data.font_terminal_command("add", "ttf-x-nerd")
        worker = "/bin/bash /usr/share/omarchy/bin/omarchy-pkg-add ttf-x-nerd"
        shell = f"bash -c omarchy-show-logo; {term}; if (( $? != 130 )); then omarchy-show-done; fi"
        # pacman running, then the package is in but the script is still finishing (font cache), then done
        states = iter([(False, [shell, worker]), (True, [shell, worker]), (True, [shell])])
        cur, seen = [None], []
        def cmdlines():
            cur[0] = next(states)
            seen.append(cur[0])
            return cur[0][1]
        self.run.wait_package("ttf-x-nerd", True, term, poll=0, installed=lambda _: cur[0][0], cmdlines=cmdlines)
        self.assertEqual(len(seen), 3, "waited for the script to finish, not just for pacman")
        # terminal closed without installing (wrong password / closed): a clear failure, not a hang
        with self.assertRaises(self.run.StepFailed):
            self.run.wait_package("ttf-x-nerd", True, term, poll=0, grace=0, installed=lambda _: False, cmdlines=lambda: [])
        # removal: done once pacman no longer has it and the drop script has exited
        self.run.wait_package("ttf-x-nerd", False, data.font_terminal_command("drop", "ttf-x-nerd"), poll=0,
                              installed=lambda _: False, cmdlines=lambda: ["bash -c ... omarchy-show-done"])

    def test_a_font_that_isnt_monospace_is_called_out(self):
        mono = lambda: [data.Font("JetBrainsMono Nerd Font", "ttf-jetbrains-mono-nerd")]
        fams = lambda pkgs: {"ttf-ubuntu-nerd": ["Ubuntu Nerd Font", "Ubuntu Nerd Font Propo"]}
        note = self.run.check_mono("ttf-ubuntu-nerd", fonts=mono, families=fams)
        self.assertIn("isn't a monospace font", note)
        self.assertIsNone(self.run.check_mono("ttf-jetbrains-mono-nerd", fonts=mono, families=fams))
        add = data.font_actions(package=data.FontPackage("ttf-ubuntu-nerd", "1", "", False))[0]
        self.assertEqual(add.steps[-1], ("check_mono", "ttf-ubuntu-nerd"), "plain Add checks what landed")
        # the note travels back from perform() so the window can show it instead of "added"
        real = self.run.check_mono
        self.run.check_mono = lambda pkg: f"{pkg} note"
        try:
            self.assertEqual(self.run.perform((("check_mono", "ttf-ubuntu-nerd"),)), "ttf-ubuntu-nerd note")
        finally:
            self.run.check_mono = real

    def test_icon_twins_are_folded_away(self):
        F = data.Font
        fonts = [F("UbuntuMono Nerd Font", "u"), F("UbuntuMono Nerd Font Mono", "u"),
                 F("JetBrainsMono Nerd Font", "j"), F("JetBrainsMono Nerd Font Mono", "j", True),
                 F("Adwaita Mono", ""), F("Lonely Nerd Font Mono", "l")]
        self.assertEqual([f.family for f in data.without_icon_twins(fonts)],
                         ["UbuntuMono Nerd Font", "JetBrainsMono Nerd Font", "JetBrainsMono Nerd Font Mono",
                          "Adwaita Mono", "Lonely Nerd Font Mono"])

    def test_browse_offers_monospace_font_packages_only(self):
        ok = data.browsable_font_package
        for pkg in ("ttf-arimo-nerd", "ttf-tinos-nerd", "ttf-ubuntu-nerd", "ttf-nerd-fonts-symbols",
                    "ttf-nerd-fonts-symbols-mono", "ttf-nerd-fonts-symbols-common"):
            self.assertFalse(ok(pkg), pkg)
        for pkg in ("ttf-ubuntu-mono-nerd", "ttf-noto-nerd", "ttf-jetbrains-mono-nerd", "otf-overpass-nerd"):
            self.assertTrue(ok(pkg), pkg)
        # one found out after an install is remembered (in the app's cache) and hidden from then on
        data.learn_not_mono("ttf-newthing-nerd")
        data.learn_not_mono("ttf-newthing-nerd")
        self.assertEqual(data.learned_not_mono(), {"ttf-newthing-nerd"})
        self.assertFalse(ok("ttf-newthing-nerd", data.learned_not_mono()))
        self.assertTrue(str(data.LEARNED_NOT_MONO).startswith(str(HOME)), "sandboxed cache")

    def test_matched_sizes_follow_omarchys_own_anchors(self):
        self.assertEqual(data.sizes_for(12, 1.0), (12, 9))     # Omarchy's defaults
        self.assertEqual(data.sizes_for(11, 1.0), (11, 8))     # the owner's 11 px -> 8 pt, as Omarchy rounds it
        self.assertEqual(data.sizes_for(11, data.font_scale(464)), (13, 9.5))   # Ubuntu Mono
        self.assertEqual(data.font_scale(550), 1.0)
        self.assertEqual(data.font_scale(100), data.SCALE_MAX)
        self.assertEqual(data.font_scale(9999), data.SCALE_MIN)
        self.assertEqual(data.font_scale(None), 1.0)

    def _configs(self):
        c = HOME / ".config"
        files = {"alacritty/alacritty.toml": "[font]\nnormal = { family = \"JetBrainsMono Nerd Font\" }\nsize = 8\n",
                 "kitty/kitty.conf": "include x.conf\nfont_family JetBrainsMono Nerd Font\nfont_size 8.0\n",
                 "ghostty/config": "font-family = \"JetBrainsMono Nerd Font\"\nfont-size = 8\n",
                 "foot/foot.ini": "[main]\nfont=JetBrainsMono Nerd Font:size=9\n",
                 "omarchy/shell.toml": "[bar]\nx = 1\n\n[font]\nbase-size = 11\n# keep me\n"}
        for rel, text in files.items():
            (c / rel).parent.mkdir(parents=True, exist_ok=True)
            (c / rel).write_text(text)
        return lambda rel: (c / rel).read_text()

    def test_switching_fonts_matches_sizes_and_comes_back_exactly(self):
        read = self._configs()
        heights = {"UbuntuMono Nerd Font": 464, "JetBrainsMono Nerd Font": 550}
        real_x, real_run, signals = data.font_x_height, self.run.run, []
        data.font_x_height = heights.get
        self.run.run = lambda argv, timeout=300: (signals.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1]
        try:
            self.run.remember_text_size()
            self.run.font_size("UbuntuMono Nerd Font")
            self.assertIn("size = 9.5", read("alacritty/alacritty.toml"))
            self.assertIn("font_size 9.5", read("kitty/kitty.conf"))
            self.assertIn("font-size = 9.5", read("ghostty/config"))
            self.assertIn("font=JetBrainsMono Nerd Font:size=9.5", read("foot/foot.ini"))
            self.assertEqual(read("omarchy/shell.toml"), "[bar]\nx = 1\n\n[font]\nbase-size = 13\n# keep me\n")
            self.assertIn(["pkill", "-USR1", "kitty"], signals)
            # back to JetBrains: exactly Omarchy's own sizes for 11 px (foot too, which font-set had left at 9)
            self.run.remember_text_size()
            self.run.font_size("JetBrainsMono Nerd Font")
            self.assertIn("size = 8\n", read("alacritty/alacritty.toml"))
            self.assertIn("font_size 8.0", read("kitty/kitty.conf"))
            self.assertIn(":size=8\n", read("foot/foot.ini"))
            self.assertIn("base-size = 11\n", read("omarchy/shell.toml"))
            # the owner changes the text size in Omarchy's panel (plain 14): that becomes the base
            self.run._set_shell_base_size(14)
            self.run.remember_text_size()
            self.run.font_size("UbuntuMono Nerd Font")
            self.assertIn("base-size = 17\n", read("omarchy/shell.toml"))    # 14 * 1.185
            self.assertIn("size = 13\n", read("alacritty/alacritty.toml"))   # Omarchy: 14 px -> 11 pt; * 1.185 = 13
        finally:
            data.font_x_height, self.run.run = real_x, real_run

    def test_shell_toml_is_created_or_extended_like_omarchy_does(self):
        path = HOME / ".config/omarchy/shell.toml"
        self.assertEqual(self.run.shell_base_size(), 12)
        self.run._set_shell_base_size(13)
        self.assertEqual(path.read_text(), "[font]\nbase-size = 13\n")
        path.write_text("[bar]\ny = 2\n")
        self.run._set_shell_base_size(10)
        self.assertEqual(path.read_text(), "[bar]\ny = 2\n\n[font]\nbase-size = 10\n")
        self.assertEqual(self.run.shell_base_size(), 10)

    def test_only_the_two_reload_nudges_may_be_sent(self):
        self.run.check(["pkill", "-USR1", "kitty"])
        self.run.check(["pkill", "-SIGUSR2", "ghostty"])
        for bad in (["pkill", "kitty"], ["pkill", "-9", "Hyprland"], ["pkill", "-USR1", "foot"]):
            with self.assertRaises(self.run.NotAllowed, msg=bad):
                self.run.check(bad)

    def test_add_and_use_picks_the_family(self):
        ran = []
        real, real_size = self.run.run, self.run.font_size
        self.run.run = lambda argv, timeout=300: (ran.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1]
        self.run.font_size = lambda family: ran.append(["font_size", family])
        try:
            fonts = lambda: [data.Font("Iosevka Nerd Font Mono", "ttf-iosevka-nerd"), data.Font("Other", "x")]
            self.run.use_font("ttf-iosevka-nerd", "", fonts=fonts)
            self.run.use_font("ttf-firacode-nerd", "FiraCode Nerd Font", fc_list=lambda: "/f.ttf: FiraCode Nerd Font:style=Regular")
            both = "x: Iosevka Nerd Font:style=Regular\ny: Iosevka Nerd Font Mono:style=Regular"
            # Iosevka's plain family isn't monospace (only its Mono twin is), so Omarchy's pick stands
            self.run.use_font("ttf-iosevka-nerd", "Iosevka Nerd Font Mono", fc_list=lambda: both,
                              fonts=lambda: [data.Font("Iosevka Nerd Font Mono", "ttf-iosevka-nerd")])
            # a font whose plain family IS monospace gets the plain name
            self.run.use_font("ttf-x-nerd", "X Nerd Font Mono", fc_list=lambda: "X Nerd Font Mono X Nerd Font",
                              fonts=lambda: [data.Font("X Nerd Font", "ttf-x-nerd"), data.Font("X Nerd Font Mono", "ttf-x-nerd")])
            with self.assertRaises(self.run.StepFailed):
                self.run.use_font("ttf-none-nerd", "", wait=0, fonts=lambda: [])
        finally:
            self.run.run, self.run.font_size = real, real_size
        self.assertEqual([r for r in ran if r[0] != "font_size"],
                         [["omarchy-font-set", "Iosevka Nerd Font Mono"], ["omarchy-font-set", "FiraCode Nerd Font"],
                          ["omarchy-font-set", "Iosevka Nerd Font Mono"], ["omarchy-font-set", "X Nerd Font"]])
        self.assertEqual(ran[1], ["font_size", "Iosevka Nerd Font Mono"], "every switch is followed by its size")


class Thumbnails(unittest.TestCase):
    @unittest.skipUnless(shutil.which("magick"), "ImageMagick not installed")
    def test_thumbnail_cached(self):
        src = SANDBOX / "big.png"
        subprocess.run(["magick", "-size", "1600x900", "xc:#336699", str(src)], check=True)
        t1 = data.thumbnail(src, 320)
        self.assertTrue(str(t1).startswith(str(data.CACHE_DIR)))
        out = subprocess.run(["magick", "identify", "-format", "%w", str(t1)], capture_output=True, text=True)
        self.assertEqual(out.stdout, "320")
        self.assertEqual(data.thumbnail(src, 320), t1)


def tearDownModule():
    shutil.rmtree(SANDBOX, ignore_errors=True)


class DownloadStall(unittest.TestCase):
    """A theme download has no overall time limit; it's stopped only when nothing arrives."""

    def setUp(self):
        from omaskins import run
        self.run, self._check = run, run.check
        run.check = lambda argv: None

    def tearDown(self):
        self.run.check = self._check

    def test_still_arriving_keeps_going(self):
        r = self.run.run_with_progress(["sh", "-c", "for i in 1 2 3 4; do echo x >&2; sleep 0.8; done"],
                                       lambda pct: None, stall=1)
        self.assertEqual(r.returncode, 0, "3+ seconds in all, but never 1 second without news")

    def test_twenty_minute_cap(self):
        r = self.run.run_with_progress(["sh", "-c", "while true; do echo x >&2; sleep 0.3; done"],
                                       lambda pct: None, stall=60, limit=2)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("still not finished after", r.stderr)
        self.assertEqual(self.run.DOWNLOAD_LIMIT, 20 * 60)

    def test_reads_gits_amount_and_speed(self):
        heard = []
        line = "Receiving objects:  66% (40/60), 2.58 MiB | 2.52 MiB/s"
        self.run.run_with_progress(["sh", "-c", f"printf '{line}\\r' >&2"], lambda pct: None,
                                   transfer=lambda got, rate: heard.append((got, rate)))
        self.assertEqual(heard, [(int(2.58 * 1024 ** 2), 2.52 * 1024 ** 2)])
        self.assertIsNone(self.run.clone_transfer("Receiving objects:  16% (10/60)"))

    def test_nothing_arriving_is_stopped(self):
        t = time.monotonic()
        r = self.run.run_with_progress(["sh", "-c", "sleep 30"], lambda pct: None, stall=1)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing arrived", r.stderr)
        self.assertLess(time.monotonic() - t, 10)


class ShareImport(unittest.TestCase):
    """Share zip in, merged: nothing of yours is overwritten, a matching name merges, never a copy."""

    def setUp(self):
        build_fixture()
        from omaskins import share
        self.share = share
        self.zip = SANDBOX / "share.zip"
        self.ran = []
        self._fonts = share._font_entries
        share._font_entries = lambda: ([], [])         # no pacman / fc-list from the tests
        # The import list is built from what's installed: never the real system's fonts or built-ins.
        self._installed = (data.installed_fonts, data.shipped_builtins)
        data.installed_fonts = lambda: [data.Font("JetBrainsMono Nerd Font", "ttf-jetbrains-mono-nerd", True)]
        data.shipped_builtins = lambda pkg: []
        from omaskins import run
        self._set_theme = run.set_theme
        run.set_theme = lambda name, bg=None: self.ran.append(("theme", name, str(bg)))

    def tearDown(self):
        from omaskins import run
        run.set_theme = self._set_theme
        self.share._font_entries = self._fonts
        data.installed_fonts, data.shipped_builtins = self._installed

    def make(self, manifest, files):
        import zipfile
        manifest = dict({"format": self.share.FORMAT, "themes": [], "backgrounds": [], "fonts": [],
                         "rotation_sets": {}, "rotation": {}}, **manifest)
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr(self.share.MANIFEST, json.dumps(manifest))
            for name, payload in files.items():
                z.writestr(name, payload)
        return self.share.read_zip(self.zip)

    def test_round_trip_brings_back_a_lost_theme_without_code(self):
        mine = HOME / ".config/omarchy/themes/painted"
        write(mine / "colors.toml", 'accent = "#123456"\n')
        (mine / "backgrounds").mkdir()
        (mine / "backgrounds/p1.jpg").write_bytes(b"picture one")
        write(mine / "hyprland.lua", "os.execute('x')")
        manifest, _ = self.share.write_zip(self.zip, [])
        self.assertNotIn("location", manifest, "the location is never shared")
        shutil.rmtree(mine)
        manifest = self.share.read_zip(self.zip)
        rows = self.share.items(manifest)
        self.assertEqual(next(r for r in rows if r["id"] == "theme:painted")["state"], "new")
        done, note, failed = self.share.run_import(self.zip, manifest, {r["id"] for r in rows}, apply=False)
        self.assertEqual((mine / "backgrounds/p1.jpg").read_bytes(), b"picture one")
        self.assertFalse((mine / "hyprland.lua").exists(), "code-running files stay out")
        self.assertTrue(any("hyprland.lua" in n for n in note))

    def test_every_setting_survives_export_reset_import(self):
        """Every OmaSkins setting, set away from its default, exported, wiped and imported with everything
        ticked and "Also apply": each one comes back (owner, 2026-10-03: double and triple check)."""
        from omaskins import qtstyle, run
        saved_font, saved_perform = data.current_font, run.perform
        data.current_font = lambda: "JetBrainsMono Nerd Font"
        data.shipped_builtins = lambda pkg: ["nord", "tokyo-night"]   # what Omarchy ships, in the sandbox
        saved_pkg = data.builtin_package
        data.builtin_package = lambda: "omarchy"
        performed = []
        run.perform = lambda steps, *a, **k: performed.extend(steps)
        try:
            # --- set every setting away from its default
            write(data.CORNERS_FILE, data.corners_file_text(True, 20))
            data.save_transparency(4)                       # step 5
            qtstyle.save_enabled(False)                     # Qt apps off (on is the default)
            write(data.TEXT_SIZE_STATE, json.dumps({"base": 15, "wrote": None}))
            data.save_sort_mode("az")
            data.set_hidden_in_omaskins("nord", True)   # not the theme in use (that one is never hidden)
            write(data.AETHER_BLUEPRINTS / "sunset.json", '{"name": "sunset"}')
            write(data.USER_BACKGROUNDS / "nord/mine.png", "my picture")
            plan = data.RotationPlan("nord")
            plan.themes, plan.backgrounds, plan.minutes, plan.mix, plan.dawn_dusk = True, True, 20, True, True
            plan.checked["Dawn"], plan.checked["Dusk"] = ["nord"], ["aura"]
            data.save_rotation_set("Evenings", plan)
            data.save_rotation(plan)
            before = {
                "corners": data.corners_setting(), "step": data.transparency_step(),
                "qt": qtstyle.enabled(), "text": json.loads(data.TEXT_SIZE_STATE.read_text())["base"],
                "sort": data.sort_mode(), "hidden": data.hidden_in_omaskins(),
                "sets": data.rotation_sets(), "rotation": data.load_rotation("nord").to_dict(),
            }
            manifest, _ = self.share.write_zip(self.zip, [])
            self.assertEqual(manifest["transparency_values"], "0.65 0.56 blur", "the levels travel, not just a number")

            # --- wipe all of it (a fresh machine as far as OmaSkins is concerned)
            for f in (data.CORNERS_FILE, data.TRANSPARENCY_FILE, qtstyle.SETTING, data.TEXT_SIZE_STATE,
                      data.UI_STATE, data.BUILTIN_STATE, data.ROTATION_FILE):
                f.unlink(missing_ok=True)
            data.ROTATION_SETS.unlink(missing_ok=True)
            shutil.rmtree(data.AETHER_BLUEPRINTS, ignore_errors=True)
            shutil.rmtree(data.USER_BACKGROUNDS / "nord", ignore_errors=True)
            self.assertEqual((data.corners_setting(), data.transparency_step(), qtstyle.enabled()),
                             ((False, None), data.TRANSPARENCY_DEFAULT, True), "really back to defaults")

            # --- import everything, "Also apply"
            manifest = self.share.read_zip(self.zip)
            rows = {r["id"] for r in self.share.items(manifest)}
            for wanted in ("corners", "transparency", "qt_apps", "text_size", "blueprints", "set:Evenings", "rotation",
                           "hide:nord"):
                self.assertIn(wanted, rows, f"an import row for {wanted}")
            done, note, failed = self.share.run_import(self.zip, manifest, rows, apply=True)
            self.assertEqual(failed, [], (done, note))

            # --- each one is back (settings applied through their actions are checked as those actions)
            self.assertIn(("apply_corners", True, 20), performed, "corners")
            self.assertIn(("apply_transparency", 4), performed, "transparency: step 5")
            self.assertIn(("qt_apps", False), performed, "Qt apps off")
            self.assertEqual(json.loads(data.TEXT_SIZE_STATE.read_text())["base"], before["text"], "text size")
            self.assertEqual(data.sort_mode(), before["sort"], "sort order")
            self.assertIn("nord", data.hidden_in_omaskins(), "hidden built-in theme")
            self.assertTrue((data.AETHER_BLUEPRINTS / "sunset.json").exists(), "Aether blueprint")
            self.assertEqual((data.USER_BACKGROUNDS / "nord/mine.png").read_text(), "my picture", "own background")
            self.assertEqual(data.rotation_sets()["Evenings"], before["sets"]["Evenings"], "saved rotation set")
            back = data.load_rotation("nord").to_dict()
            for key in ("themes", "backgrounds", "minutes", "mix", "dawn_dusk", "checked", "theme_picks",
                        "solo_picks", "period", "set_name"):
                self.assertEqual(back[key], before["rotation"][key], f"rotation: {key}")
        finally:
            data.current_font, run.perform, data.builtin_package = saved_font, saved_perform, saved_pkg

    def test_a_changed_set_and_a_pause_come_back_exactly(self):
        """The rotation in use is a saved set with changes not saved to it, and backgrounds are paused:
        exported, everything of OmaSkins' wiped (as removing it does), imported with "Also apply": the
        set as saved, the changed setup in use under its name, and the pause (owner, 2026-10-08)."""
        from omaskins import run
        saved_font, saved_perform, saved_pkg = data.current_font, run.perform, data.builtin_package
        data.current_font = lambda: "JetBrainsMono Nerd Font"
        data.builtin_package = lambda: ""
        run.perform = lambda steps, *a, **k: None
        try:
            plan = data.RotationPlan("tokyo-night")
            plan.themes, plan.backgrounds, plan.minutes, plan.mix = True, True, 20, False
            plan.checked["Dawn"] = ["tokyo-night", "aura"]
            plan.theme_picks["aura"] = {str(HOME / ".config/omarchy/themes/aura/backgrounds/aura-1.jpg")}
            data.save_rotation_set("Crazy", plan)
            plan = data.use_rotation_set("Crazy", "tokyo-night")
            plan.minutes, plan.mix = 5, True                      # changed, not saved to the set
            plan.checked["Dawn"] = ["aura", "tokyo-night", "nord"]
            plan.theme_picks["tokyo-night"] = {str(OMARCHY / "themes/tokyo-night/backgrounds/0-a.jpg"),
                                               str(HOME / ".config/omarchy/backgrounds/tokyo-night/mine.png")}
            data.save_rotation(plan)
            data.set_rotation_paused("background", True)
            self.assertTrue(data.set_has_changes(data.load_rotation("tokyo-night")))
            before = (json.loads(data.ROTATION_FILE.read_text()), data.rotation_sets(), data.rotation_paused())
            manifest, _ = self.share.write_zip(self.zip, [])
            self.assertEqual(manifest["rotation_paused"], ["background"])

            shutil.rmtree(data.ROTATION_FILE.parent)              # ~/.config/omaskins
            shutil.rmtree(data.OMASKINS_STATE, ignore_errors=True)
            self.assertEqual((data.rotation_sets(), data.rotation_paused()), ({}, set()))

            manifest = self.share.read_zip(self.zip)
            rows = self.share.items(manifest)
            row = next(r for r in rows if r["id"] == "rotation")
            self.assertIn("“Crazy”, with changes", row["label"])
            self.assertIn("set:Crazy", row["needs"])
            done, note, failed = self.share.run_import(self.zip, manifest, {r["id"] for r in rows}, apply=True)
            self.assertEqual(failed, [], (done, note))
            after = (json.loads(data.ROTATION_FILE.read_text()), data.rotation_sets(), data.rotation_paused())
            self.assertEqual(after[0], before[0], "the rotation in use: every list, pick and switch")
            self.assertEqual(after[1], before[1], "the saved sets, and no “Imported …” one beside them")
            self.assertEqual(after[2], before[2], "what was paused")
            self.assertTrue(data.set_has_changes(data.load_rotation("tokyo-night")), "still shown as changed")

            # "Just import" (nothing applied) keeps the old way: the changed setup arrives as its own set.
            shutil.rmtree(data.ROTATION_FILE.parent)
            shutil.rmtree(data.OMASKINS_STATE, ignore_errors=True)
            done, note, failed = self.share.run_import(self.zip, manifest, {r["id"] for r in rows}, apply=False)
            self.assertEqual(failed, [], (done, note))
            names = list(data.rotation_sets())
            self.assertEqual(len(names), 2)
            self.assertTrue(any(n.startswith("Imported ") for n in names), names)
            self.assertEqual(data.rotation_paused(), set(), "nothing applied, so nothing paused")
        finally:
            data.current_font, run.perform, data.builtin_package = saved_font, saved_perform, saved_pkg

    def test_transparency_lands_on_the_same_levels_after_a_respacing(self):
        """An export records the levels; an import finds the step with those levels here."""
        for line, step in (("0.75 0.68 blur", 3), ("0.65 0.56 blur", 4), ("0.85 0.8 blur", 2), ("1 1 noblur", 0)):
            self.assertEqual(self.share.transparency_step_of({"transparency": 4, "transparency_values": line}), step, line)
        self.assertEqual(self.share.transparency_step_of({"transparency": 2}), 2, "older files: the step number")
        self.assertIsNone(self.share.transparency_step_of({}))

    def test_a_matching_name_merges_pictures_into_yours(self):
        mine = HOME / ".config/omarchy/themes/oil-paintings"
        write(mine / "colors.toml", 'accent = "#mine"\n')
        (mine / "backgrounds").mkdir()
        (mine / "backgrounds/a.jpg").write_bytes(b"A")
        manifest = self.make({"themes": [{"name": "Oil_Paintings", "kind": "folder"}]}, {
            "themes/Oil_Paintings/colors.toml": 'accent = "#theirs"\n',
            "themes/Oil_Paintings/backgrounds/a-copy.jpg": b"A",      # same picture, other name
            "themes/Oil_Paintings/backgrounds/a.jpg": b"B",           # other picture, same name
            "themes/Oil_Paintings/backgrounds/c.jpg": b"C"})
        row = next(r for r in self.share.items(manifest) if r["id"] == "theme:Oil_Paintings")
        self.assertEqual(row["state"], "merge")
        self.share.run_import(self.zip, manifest, {"theme:Oil_Paintings"}, apply=False)
        themes = HOME / ".config/omarchy/themes"
        self.assertEqual(sorted(p.name for p in themes.iterdir()), ["aura", "nord", "oil-paintings", "secret"],
                         "no second theme")
        self.assertEqual((mine / "colors.toml").read_text(), 'accent = "#mine"\n', "your colours stay")
        added = HOME / ".config/omarchy/backgrounds/oil-paintings"
        self.assertEqual(sorted((p.name, p.read_bytes()) for p in added.iterdir()),
                         [("a.jpg", b"B"), ("c.jpg", b"C")], "only the new pictures, nothing twice")
        self.share.run_import(self.zip, manifest, {"theme:Oil_Paintings"}, apply=False)
        self.assertEqual(len(list(added.iterdir())), 2, "importing again adds nothing")

    def test_a_set_with_your_set_name_combines(self):
        calm = data.RotationPlan("tokyo-night")
        calm.themes, calm.minutes = True, 60
        calm.checked = {"All day": ["tokyo-night"], "Dawn": [], "Dusk": []}
        data.save_rotation_set("Calm", calm)
        theirs = dict(calm.to_dict(), minutes=5, checked={"All day": ["nord", "tokyo-night"], "Dawn": [], "Dusk": []})
        manifest = self.make({"themes": [{"name": "nord", "kind": "builtin"}],
                              "rotation_sets": {"Calm": self.share.portable(theirs)}}, {})
        rows = self.share.items(manifest)
        row = next(r for r in rows if r["id"] == "set:Calm")
        self.assertEqual((row["state"], row["needs"]), ("merge", ["theme:nord"]))
        self.assertIn("theme:nord", self.share.with_needs({"set:Calm"}, rows), "its themes come with it")
        self.share.run_import(self.zip, manifest, {"set:Calm"}, apply=False)
        sets = data.rotation_sets()
        self.assertEqual(list(sets), ["Calm"], "one set, not a copy")
        self.assertEqual(sets["Calm"]["checked"]["All day"], ["tokyo-night", "nord"])
        self.assertEqual(sets["Calm"]["minutes"], 60, "your interval stays")

    def test_apply_mirrors_the_theme_and_background(self):
        manifest = self.make({"themes": [{"name": "nord", "kind": "builtin"}],
                              "current": {"theme": "nord", "background": {"zip": "current/x.png"}}},
                             {"current/x.png": b"X"})
        done, note, failed = self.share.run_import(self.zip, manifest, {"theme:nord"}, apply=True)
        self.assertEqual(self.ran[0][1], "nord")
        self.assertTrue(self.ran[0][2].endswith("/backgrounds/nord/x.png"), self.ran)

    def test_a_failed_export_leaves_nothing(self):
        out = SANDBOX / "out"
        out.mkdir(exist_ok=True)
        real = self.share.collect
        self.share.collect = lambda c: ({"format": self.share.FORMAT}, [(SANDBOX / "missing.jpg", "x/missing.jpg")])
        try:
            with self.assertRaises(OSError):
                self.share.write_zip(out / "setup.omaskins", [])
        finally:
            self.share.collect = real
        self.assertEqual(list(out.iterdir()), [], "no half-written file")
        self.share.write_zip(out / "setup.omaskins", [])
        self.assertEqual([p.name for p in out.iterdir()], ["setup.omaskins"], "just the file")

    def test_a_theme_download_leaves_no_holding_folder(self):
        """A real download through OmaSkins' own steps (a local git repo): the theme lands in themes/
        and the holding folder that took it in is gone afterwards, not left empty."""
        from omaskins import run
        src = SANDBOX / "repo-ok"
        shutil.rmtree(src, ignore_errors=True)
        write(src / "colors.toml", 'accent = "#123456"\n')
        subprocess.run(["git", "init", "-q", str(src)], check=True)
        subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "add", "."], check=True)
        subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                       check=True)
        tmp, dest = data.PARTIAL_THEMES / "repo-ok", data.USER_THEMES / "repo-ok"
        run.perform((("clear_partial", tmp), ("run", ["git", "clone", "--progress", "--", src.as_uri(), str(tmp)]),
                     ("move_in", tmp, dest)), lambda pct: None)
        self.assertTrue((dest / "colors.toml").is_file())
        self.assertFalse(data.PARTIAL_THEMES.exists(), "no empty holding folder left")

    def test_a_failed_import_leaves_nothing(self):
        manifest = self.make({"themes": [{"name": "fresh", "kind": "folder"}]},
                             {"themes/fresh/colors.toml": "x", "themes/fresh/backgrounds/a.jpg": b"A"})
        real = self.share.shutil.copyfileobj
        def boom(src, dst):
            if "a.jpg" in getattr(dst, "name", ""):
                raise OSError("disk full")
            return real(src, dst)
        self.share.shutil.copyfileobj = boom
        try:
            with self.assertRaises(OSError):
                self.share.run_import(self.zip, manifest, {"theme:fresh"}, apply=False)
        finally:
            self.share.shutil.copyfileobj = real
        self.assertFalse((HOME / ".config/omarchy/themes/fresh").exists())
        self.assertFalse(data.PARTIAL_THEMES.exists(), "no half-unpacked theme left")
        self.share.run_import(self.zip, manifest, {"theme:fresh"}, apply=False)
        self.assertFalse(data.PARTIAL_THEMES.exists())
        self.assertEqual(self.share._IN_PROGRESS, set())

    def test_file_type_keeps_file_managers_from_unpacking_it(self):
        self.share.register_file_type()
        self.assertIn('<glob pattern="*.omaskins"', self.share.MIME_PACKAGE.read_text())
        self.assertTrue(str(self.share.MIME_PACKAGE).startswith(str(HOME)), "yours only, never system-wide")
        entry = self.share.APP_DESKTOP.read_text()
        self.assertIn("MimeType=application/x-omaskins-setup;\n", entry, "that type only")
        self.assertIn("NoDisplay=true", entry, "not in Apps or the launcher: Style › OmaSkins only (owner, 2026-10-02)")
        self.assertIn("application/x-omaskins-setup=io.github.jesuslovesyou1013.omaskins.desktop",
                      self.share.MIMEAPPS.read_text())
        self.share.MIMEAPPS.write_text("[Default Applications]\napplication/x-omaskins-setup=other.desktop\n")
        self.share.register_file_type()
        self.assertIn("=other.desktop", self.share.MIMEAPPS.read_text(), "your own choice is left alone")

    def test_import_reports_each_step(self):
        """The overall bar: every step counted up front, then reported one by one, ending at total."""
        calm = dict(data.RotationPlan("tokyo-night").to_dict(), checked={"All day": ["nord"], "Dawn": [], "Dusk": []})
        manifest = self.make({"themes": [{"name": "fresh", "kind": "folder"}, {"name": "nord", "kind": "builtin"}],
                              "backgrounds": [{"theme": "nord", "file": "x.png", "zip": "backgrounds/nord/x.png"}],
                              "rotation_sets": {"Calm": self.share.portable(calm)}, "rotation": {"set": "Calm"},
                              "aether_blueprints": ["aether-blueprints/fresh.json"],
                              "current": {"theme": "nord"}},
                             {"themes/fresh/colors.toml": "x", "backgrounds/nord/x.png": b"X",
                              "aether-blueprints/fresh.json": "{}"})
        heard = []
        chosen = {r["id"] for r in self.share.items(manifest)}
        done, note, failed = self.share.run_import(self.zip, manifest, chosen, apply=True, progress=heard.append)
        steps = [h["step"] for h in heard]
        total = heard[0]["total"]
        # fresh, nord, the background, blueprints, the set, the rotation in use, the apply
        self.assertEqual(total, 7)
        self.assertEqual(steps, sorted(steps), "never goes backwards")
        self.assertEqual(sorted(set(steps)), list(range(1, total + 1)), "every step reported, none skipped")
        self.assertEqual((heard[-1]["step"], heard[-1]["text"]), (total, "Finished"))
        self.assertIn("Adding Fresh", [h["text"] for h in heard])
        self.assertTrue(all(h["download"] is None for h in heard), "nothing was downloaded")
        self.assertEqual(failed, [])

    def test_unsafe_names_are_ignored(self):
        manifest = self.make({"themes": [{"name": "../evil", "kind": "folder"}, {"name": ".x", "kind": "folder"}],
                              "backgrounds": [{"theme": "..", "file": "a.jpg", "zip": "backgrounds/a.jpg"}]}, {})
        self.assertEqual(self.share.items(manifest), [])


class ImportPassword(unittest.TestCase):
    """Built-ins and font packages to match a file: offered as rows (Password tagged), and everything
    that needs the password done in ONE terminal, so it's typed once."""

    def setUp(self):
        build_fixture()
        from omaskins import run, share
        self.run, self.share = run, share
        self.saved = (data.builtin_package, data.shipped_builtins, data.installed_fonts, run.perform,
                      run.wait_import_password)
        data.builtin_package = lambda: "omarchy"
        data.shipped_builtins = lambda pkg: ["gruvbox", "nord", "tokyo-night"]   # gruvbox removed here
        data.installed_fonts = lambda: [data.Font("JetBrainsMono Nerd Font", "ttf-jetbrains-mono-nerd", True),
                                        data.Font("Iosevka Nerd Font", "ttf-iosevka-nerd"),
                                        data.Font("Liberation Mono", "ttf-liberation")]
        self.zip = SANDBOX / "pw.omaskins"

    def tearDown(self):
        (data.builtin_package, data.shipped_builtins, data.installed_fonts, self.run.perform,
         self.run.wait_import_password) = self.saved

    def make(self, manifest):
        import zipfile
        manifest = dict({"format": self.share.FORMAT, "themes": [], "backgrounds": [], "fonts": [],
                         "rotation_sets": {}, "rotation": {}}, **manifest)
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr(self.share.MANIFEST, json.dumps(manifest))
        return self.share.read_zip(self.zip)

    def rows(self, manifest):
        return {r["id"]: r for r in self.share.items(manifest)}

    def test_one_command_and_the_safety_check(self):
        cmd = data.password_terminal_command(["ttf-ubuntu-mono-nerd", "otf-hermit-nerd"], ["ttf-iosevka-nerd"],
                                             ["tokyo-night"], [], ["gruvbox"], "omarchy")
        self.assertEqual(cmd.count("sudo -v"), 1, "the password is asked once, at the start")
        self.assertIn("omarchy-pkg-add otf-hermit-nerd ttf-ubuntu-mono-nerd", cmd, "both fonts, one pacman")
        self.assertTrue(self.run._font_terminal(["omarchy-launch-floating-terminal-with-presentation", cmd]))
        bad = cmd.replace("sudo mv", "sudo rm -rf /; sudo mv", 1)
        self.assertFalse(self.run._font_terminal(["omarchy-launch-floating-terminal-with-presentation", bad]))
        with self.assertRaises(ValueError):
            data.password_terminal_command(["x;rm"], [], [], [], [], "omarchy")
        with self.assertRaises(ValueError):
            data.password_terminal_command([], [], [], [], [], "omarchy")

    def test_rows_for_built_ins(self):
        m = self.make({"themes": [{"name": "gruvbox", "kind": "builtin"}, {"name": "nord", "kind": "builtin"}],
                       "hidden_builtins": ["nord"], "removed_builtins": ["tokyo-night"]})
        rows = self.rows(m)
        self.assertEqual(rows["restore:gruvbox"]["state"], "password", "installed there, removed here")
        self.assertEqual(rows["hide:nord"]["state"], "new", "hidden there: no password")
        choice = rows["builtin:tokyo-night"]["choice"]
        self.assertEqual([(c[0], c[3]) for c in choice], [("hide:tokyo-night", ""), ("remove:tokyo-night", "password")])
        # plain words for what will happen (owner, 2026-10-02: "installed there, removed here" said nothing)
        self.assertEqual(rows["restore:gruvbox"]["detail"], "will be reinstalled")
        self.assertEqual(rows["hide:nord"]["detail"], "will be hidden from OmaSkins")
        self.assertEqual(rows["builtin:tokyo-night"]["detail_for"],
                         {"hide:tokyo-night": "will be hidden from OmaSkins", "remove:tokyo-night": "will be uninstalled"})

    def test_older_files_listed_hidden_and_removed_together(self):
        m = self.make({"themes": [{"name": "nord", "kind": "builtin"}], "hidden_builtins": ["nord", "tokyo-night"]})
        rows = self.rows(m)
        self.assertIn("hide:nord", rows, "still among its themes: only hidden there")
        self.assertIn("builtin:tokyo-night", rows, "not among its themes: removed there")

    def test_font_removals_are_offered_but_never_ticked_or_protected_ones(self):
        m = self.make({"fonts": [{"family": "Liberation Mono", "package": "ttf-liberation", "source": "repo"}]})
        rows = self.rows(m)
        self.assertEqual(rows["dropfont:ttf-iosevka-nerd"].get("opt_in"), True)
        self.assertNotIn("dropfont:ttf-jetbrains-mono-nerd", rows, "Omarchy's default font / the one in use")

    def test_everything_needing_the_password_goes_in_one_terminal(self):
        m = self.make({"themes": [{"name": "gruvbox", "kind": "builtin"}],
                       "fonts": [{"family": "UbuntuMono Nerd Font", "package": "ttf-ubuntu-mono-nerd", "source": "repo"},
                                 {"family": "Hurmit Nerd Font", "package": "otf-hermit-nerd", "source": "repo"}],
                       "removed_builtins": ["tokyo-night"]})
        terminals = []
        self.run.perform = lambda steps, *a, **k: terminals.extend(s[1] for s in steps if s[0] == "terminal")

        def fake_wait(lists, *a, **k):
            return ({("add-font", "otf-hermit-nerd"): True, ("add-font", "ttf-ubuntu-mono-nerd"): True,
                     ("drop-font", "ttf-iosevka-nerd"): False, ("remove-theme", "tokyo-night"): True,
                     ("restore-theme", "gruvbox"): True}, "terminal finished: done")
        self.run.wait_import_password = fake_wait
        chosen = {"font:UbuntuMono Nerd Font", "font:Hurmit Nerd Font", "dropfont:ttf-iosevka-nerd",
                  "remove:tokyo-night", "restore:gruvbox"}
        heard = []
        done, note, failed = self.share.run_import(self.zip, m, chosen, apply=False, progress=heard.append)
        self.assertEqual(len(terminals), 1, "one terminal, one password")
        lists = data.parse_password_terminal_head(terminals[0])
        self.assertEqual(lists, {"add-fonts": ["otf-hermit-nerd", "ttf-ubuntu-mono-nerd"],
                                 "drop-fonts": ["ttf-iosevka-nerd"], "remove-themes": ["tokyo-night"],
                                 "unhide-themes": [], "reinstall-themes": ["gruvbox"]})
        self.assertIn("Removed Tokyo Night entirely", done)
        self.assertIn("Restored Gruvbox", done)
        self.assertTrue(any(n.startswith("ttf-iosevka-nerd: not removed") for n in note), "each item reported")
        self.assertEqual(heard[-1]["step"], heard[-1]["total"])

    def test_a_restore_clears_the_removed_at_entry(self):
        st = data._builtin_state()
        st["removed_at"]["gruvbox"] = "1.0-1"
        data._save_builtin_state(st)
        m = self.make({"themes": [{"name": "gruvbox", "kind": "builtin"}]})
        self.run.perform = lambda steps, *a, **k: None
        self.run.wait_import_password = lambda lists, *a, **k: ({("restore-theme", "gruvbox"): True}, "all done")
        done, note, failed = self.share.run_import(self.zip, m, {"restore:gruvbox"}, apply=False)
        self.assertEqual(done, ["Restored Gruvbox"])
        self.assertNotIn("gruvbox", data._builtin_state()["removed_at"], "the import's restore clears it")
        steps = data.builtin_restore_action("gruvbox", "omarchy").steps
        self.assertEqual(steps[-1], ("forget_removed_version", "gruvbox"), "and so does the Restore button")

    def test_terminal_says_how_to_cancel_and_cancelling_changes_nothing(self):
        cmd = data.password_terminal_command(["otf-hermit-nerd"], [], [], [], [], "omarchy")
        self.assertIn("Press Ctrl+C to cancel. Nothing will be changed.", cmd)
        self.assertIn("sudo -v || { echo 'Cancelled: nothing was changed.'; echo cancelled > ", cmd,
                      "Ctrl+C at the password: nothing runs, and the window closes (130 skips Omarchy's Done screen)")
        self.assertIn("  - Install font: otf-hermit-nerd", cmd, "plain words, not the internal list")

    def test_cancel_closes_only_the_password_window(self):
        killed = []
        procs = lambda: [(1, 0, "systemd --user"), (50, 1, "ghostty --class=org.omarchy.terminal -e bash -c THE-CMD"),
                         (51, 50, "bash -c omarchy-show-logo; THE-CMD"), (52, 51, "sudo -v"),
                         (60, 1, "ghostty --class=org.omarchy.agent -e claude")]
        self.run.close_terminal("THE-CMD", procs=procs, kill=lambda pid, sig: killed.append(pid))
        self.assertEqual(killed, [50], "the window's own process, nothing else")

    def test_cancelled_items_say_so(self):
        m = self.make({"fonts": [{"family": "UbuntuMono Nerd Font", "package": "ttf-ubuntu-mono-nerd", "source": "repo"}]})
        self.run.perform = lambda steps, *a, **k: None

        def wait(lists, *a, **k):
            self.share._PASSWORD["cancelled"] = True   # Cancel was pressed meanwhile
            return {("add-font", "ttf-ubuntu-mono-nerd"): False}, "terminal closed"
        self.run.wait_import_password = wait
        heard = []
        done, note, failed = self.share.run_import(self.zip, m, {"font:UbuntuMono Nerd Font"}, apply=False,
                                                   progress=heard.append)
        self.assertEqual(note, ["ttf-ubuntu-mono-nerd: not installed (cancelled)"])
        self.assertTrue(any(h.get("cancel") for h in heard), "Cancel is usable while the terminal is open")
        self.assertFalse(heard[-1].get("cancel"), "and not after")

    def test_a_font_a_terminal_is_set_to_use_is_never_offered_for_removal(self):
        write(HOME / ".config/ghostty/config", 'font-family = "Iosevka Nerd Font"\n')
        try:
            rows = self.rows(self.make({"fonts": []}))
            self.assertNotIn("dropfont:ttf-iosevka-nerd", rows)
        finally:
            (HOME / ".config/ghostty/config").unlink()

    def test_waiting_ends_with_each_item_reported(self):
        """The terminal tells OmaSkins it started and how it ended; each way of ending is reported."""
        lists = {"add-fonts": ["a-nerd"], "drop-fonts": [], "remove-themes": [], "unhide-themes": [],
                 "reinstall-themes": []}
        mark = SANDBOX / "pw-mark"
        started, finished = Path(f"{mark}.started"), Path(f"{mark}.done")
        not_done = {("add-font", "a-nerd"): lambda: False}

        def go(**k):
            k.setdefault("window_open", lambda: False)
            k.setdefault("settle", 0)
            return self.run.wait_import_password(lists, mark, poll=0, checks=k.pop("checks", not_done), **k)
        self.run.clear_password_marks(mark)
        self.assertEqual(go(checks={("add-font", "a-nerd"): lambda: True}), ({("add-font", "a-nerd"): True}, "all done"))
        started.write_text("4242")
        finished.write_text("cancelled")
        self.assertEqual(go(), ({("add-font", "a-nerd"): False}, "terminal finished: cancelled"))
        finished.unlink()
        self.assertEqual(go(alive=lambda pid: False), ({("add-font", "a-nerd"): False}, "terminal closed"))
        self.run.clear_password_marks(mark)
        self.assertEqual(go(grace=0)[1], "terminal never started")
        self.assertFalse(started.exists() or finished.exists(), "markers cleared")

    def test_waiting_is_patient_while_the_terminal_comes_up_and_rechecks_at_the_end(self):
        """2026-10-02: the wait gave up 30 s in while Omarchy's terminal was still coming up, and
        reported a restore that happened 18 s later as not done."""
        lists = {"add-fonts": [], "drop-fonts": [], "remove-themes": [], "unhide-themes": ["miasma"],
                 "reinstall-themes": []}
        mark = SANDBOX / "pw-mark2"
        started, finished = Path(f"{mark}.started"), Path(f"{mark}.done")
        self.run.clear_password_marks(mark)
        t0 = time.monotonic()
        # 0-0.3 s: window up, script not started yet (the wait must not give up); 0.3 s: started;
        # 0.5 s: says done; 0.6 s: the restore lands on disk (the final look must still see it)

        def restored():
            el = time.monotonic() - t0
            if el > 0.3 and not started.exists():
                started.write_text("4242")
            if el > 0.5 and not finished.exists():
                finished.write_text("done")
            return el > 0.6
        r, why = self.run.wait_import_password(lists, mark, poll=0.01, grace=0.05, settle=2, give_up=10,
                                               checks={("restore-theme", "miasma"): restored},
                                               window_open=lambda: True, alive=lambda pid: True)
        self.assertEqual((r, why), ({("restore-theme", "miasma"): True}, "terminal finished: done"))
        self.run.clear_password_marks(mark)

    def test_the_terminal_writes_its_markers(self):
        cmd = data.password_terminal_command(["otf-hermit-nerd"], [], [], [], [], "omarchy")
        self.assertIn(f"echo $$ > {data.PASSWORD_MARK}.started; sudo -v", cmd)
        self.assertIn(f"echo cancelled > {data.PASSWORD_MARK}.done; exit 130", cmd)
        self.assertTrue(cmd.endswith(f"kill $keep; echo done > {data.PASSWORD_MARK}.done"))


class Transparency(unittest.TestCase):
    """Five steps; step 2 is exactly Omarchy's own; changes fade in live, never a Hyprland reload."""

    def setUp(self):
        build_fixture()
        data.TRANSPARENCY_FILE.unlink(missing_ok=True)
        self.calls = []
        self.windows = [{"address": "0xa", "tags": ["default-opacity*"]},
                        {"address": "0xb", "tags": ["default-opacity*", "terminal*"]},
                        {"address": "0xc", "tags": []}]                      # Omarchy keeps this one solid

    def fade(self, step):
        _run.apply_transparency(step, evaluate=self.calls.append, clients=lambda: self.windows, sleep=lambda s: None)

    def test_steps_and_saving(self):
        self.assertEqual(data.transparency_step(), 1, "nothing saved: Omarchy's own")
        self.assertEqual(data.transparency_values(1), (0.985, 0.96, False))
        for step in (0, 2, 3, 4):
            data.save_transparency(step)
            self.assertEqual(data.transparency_step(), step)
        data.save_transparency(1)
        self.assertFalse(data.TRANSPARENCY_FILE.exists(), "the default step leaves no file at all")
        a, b, _ = data.transparency_values(4)
        self.assertLess(b, a, "unfocused windows are a little more see-through")

    def hyprland(self):
        """Run every Lua call the fades made, in order, in a real Lua with a stand-in Hyprland that keeps
        rules (newest-wins, switchable) and window tags; returns (rules, enabled rules, tags per window)."""
        if not shutil.which("lua"):
            self.skipTest("no lua")
        lua = SANDBOX / "fade.lua"
        stand_in = r"""
rules, tags = {}, { ["0xa"] = {}, ["0xb"] = {} }
local Rule = {}
Rule.__index = Rule
function Rule:set_enabled(on) self.on = on end
hl = { dsp = { window = { tag = function(t) return t end } }, config = function() end,
       layer_rule = function(r) r.on = true; return setmetatable(r, Rule) end }
function hl.window_rule(r) r.on = true; setmetatable(r, Rule); rules[#rules + 1] = r; return r end
function hl.dispatch(t)
  local w = t.window:sub(9)
  local sign, name = t.tag:sub(1, 1), t.tag:sub(2)
  tags[w] = tags[w] or {}
  tags[w][name] = (sign == "+") or nil
end
"""
        body = "\n".join(f"do\n{c}\nend" for c in self.calls if not c.startswith("hl.config"))
        report = r"""
local on, names = 0, {}
for _, r in ipairs(rules) do if r.on then on = on + 1 end end
for w, t in pairs(tags) do for n in pairs(t) do names[#names + 1] = w .. ":" .. n end end
table.sort(names)
-- what window 0xa looks like now: the newest switched-on rule matching one of its tags or default-opacity
local seen = "none"
for _, r in ipairs(rules) do
  local m = r.match and r.match.tag
  if r.on and r.opacity and (m == "default-opacity" or (m and tags["0xa"][m])) then seen = r.opacity end
end
-- an app that draws its own transparency (tags: Omarchy's default-opacity + ours)
local own = "none"
for _, r in ipairs(rules) do
  local m = r.match and r.match.tag
  if r.on and r.opacity and (m == "default-opacity" or m == "omaskins-qt") then own = r.opacity end
end
print(#rules, on, table.concat(names, " "), seen, own)
"""
        lua.write_text(stand_in + body + report)
        out = subprocess.run(["lua", str(lua)], capture_output=True, text=True)
        self.assertEqual(out.stderr, "")
        n, on, names, seen, own = out.stdout.rstrip("\n").split("\t")
        self.own_app = own   # what a window tagged "omaskins-qt" ends up with
        return int(n), int(on), names, seen

    def test_fade_to_step_3(self):
        write(HOME / ".config/hypr/looknfeel.lua", "-- looknfeel\n")
        self.fade(2)
        self.assertEqual(self.calls[0], data.TRANSPARENCY_BLUR, "blur from step 3 up (owner, 2026-10-02)")
        self.assertTrue(all("0xc" not in c for c in self.calls), "only windows Omarchy makes see-through")
        rules, on, names, seen = self.hyprland()
        self.assertEqual(seen, "0.8500 0.8000", "ends on step 3's values, for new windows too")
        self.assertEqual(names, "", "no fade tags left on any window")
        self.assertEqual(data.transparency_step(), 2)
        self.assertIn("omaskins/transparency", data.CORNERS_FILE.read_text(), "omaskins.lua reads the step")

    def test_moving_the_slider_all_day_piles_nothing_up(self):
        write(HOME / ".config/hypr/looknfeel.lua", "-- looknfeel\n")
        for step in (4, 2, 4, 2, 4, 2):
            self.fade(step)
        rules_after_3, _, _, _ = self.hyprland()
        for step in (4, 2) * 10:
            self.fade(step)
        rules, on, names, seen = self.hyprland()
        self.assertEqual(rules, rules_after_3, "the same moves again: no new rules in Hyprland")
        self.assertEqual(seen, "0.8500 0.8000")
        self.assertEqual(names, "")
        self.assertEqual(rules - on, 1, "of our two default-opacity rules (step 3 and 5) only one is switched on")

    def test_apps_doing_their_own_transparency_stay_out_of_the_fade(self):
        """Owner, 2026-10-03: moving the slider faded OmaShow's whole window again (slides included)."""
        write(HOME / ".config/hypr/looknfeel.lua", "-- looknfeel\n")
        self.windows.append({"address": "0xq", "tags": ["default-opacity*", "omaskins-qt"]})
        for step in (3, 4, 2, 4, 3):
            self.fade(step)
            self.assertFalse(any("0xq" in c for c in self.calls), "never given a fade level")
            self.hyprland()
            self.assertEqual(self.own_app, "1 1", f"after moving to step {step + 1}: its own rule is still the newest")

    def test_blur_on_the_strong_steps_and_back_to_omarchys_own(self):
        write(HOME / ".config/hypr/looknfeel.lua", "-- looknfeel\n")
        self.fade(4)
        self.assertEqual(self.calls[0], data.TRANSPARENCY_BLUR, "blur first, then the fade")
        self.calls.clear()
        self.windows[0]["tags"] += ["omaskins-t1-10*"]   # left by the fade before 2026-10-02
        self.fade(1)
        self.assertTrue(any("blur = { enabled = false }" in c for c in self.calls), "blur off again")
        self.assertIn("hl.dispatch(hl.dsp.window.tag({ window = 'address:0xa', tag = '-omaskins-t1-10' }))", self.calls,
                      "old tags cleared")
        self.assertFalse(data.TRANSPARENCY_FILE.exists())

    def test_hyprland_reads_the_step(self):
        """Run omaskins.lua's reader in a real Lua with stand-ins for Hyprland's o.window / hl.config."""
        if not shutil.which("lua"):
            self.skipTest("no lua")
        lua = SANDBOX / "reader.lua"
        data.save_transparency(3)
        lua.write_text("o = { window = function(m, r) print('rule', m.class or m.tag, r.tag or r.opacity, r.tag and r.opacity or '') end }\n"
                       "hl = { config = function(c) print('blur', c.decoration.blur.enabled) end,\n"
                       "       layer_rule = function(r) print('layer blur', r.match.namespace ~= nil) end }\n"
                       + data.TRANSPARENCY_LUA)
        out = subprocess.run(["lua", str(lua)], capture_output=True, text=True,
                             env=dict(os.environ, XDG_CONFIG_HOME=str(HOME / ".config"))).stdout
        self.assertEqual(out.split("\n")[:4], ["rule\t^io.github.jesuslovesyou1013.omaskins$\t-default-opacity\t1 1",
                                               "rule\t^org.gnome.Nautilus$\t-default-opacity\t1 1",
                                               "rule\tdefault-opacity\t0.75 0.68\t", "blur\ttrue"],
                         "OmaSkins out of the whole-window fade (it fades only its background), then the step")
        data.save_transparency(1)
        out = subprocess.run(["lua", str(lua)], capture_output=True, text=True,
                             env=dict(os.environ, XDG_CONFIG_HOME=str(HOME / ".config"))).stdout
        self.assertEqual(out.strip(), "rule\t^io.github.jesuslovesyou1013.omaskins$\t-default-opacity\t1 1",
                         "no file: only OmaSkins' own opt-out; every other window as Omarchy and the theme have it")

    def test_shared_and_applied_by_an_import(self):
        from omaskins import share
        data.save_transparency(3)
        self.assertEqual(share.collect([])[0]["transparency"], 3)
        rows = {r["id"]: r for r in share.items({"format": share.FORMAT, "transparency": 3})}
        self.assertEqual(rows["transparency"]["detail"], "step 4 of 5")
        ran = []
        saved = _run.perform
        _run.perform = lambda steps, *a, **k: ran.extend(steps)
        try:
            share.apply_look({"transparency": 0}, {"transparency"}, {}, {}, None, [])
        finally:
            _run.perform = saved
        self.assertIn(("apply_transparency", 0), ran)


class NautilusTransparency(unittest.TestCase):
    """Nautilus does its own transparency (sidebar 10 points more solid, icons solid) through GTK's
    stylesheet, which it reads only when it starts: OmaSkins reopens it on the same folders."""

    def setUp(self):
        build_fixture()
        data.TRANSPARENCY_FILE.unlink(missing_ok=True)
        data.GTK4_CSS.unlink(missing_ok=True)
        write(HOME / ".config/hypr/looknfeel.lua", "-- looknfeel\n")

    def test_levels_and_the_sidebar(self):
        css = data.nautilus_css(2)
        # The whole window carries the level (a narrow Nautilus folds its sidebar away and its panes stop
        # painting); the sidebar's own layer on top makes it 10 points more solid: 0.85 + 0.15 * 0.667 = 0.95.
        # The file dialogs' greys, light and dark, switched by GTK itself (no restart on a theme change).
        self.assertIn("window.nautilus-window, window.nautilus-window.background { background-color: alpha(#ffffff, 0.85); }", css)
        self.assertIn("@media (prefers-color-scheme: dark) {\n  window.nautilus-window, window.nautilus-window.background "
                      "{ background-color: alpha(#1d1d20, 0.85); }", css)
        self.assertIn("window.nautilus-window:backdrop, window.nautilus-window.background:backdrop { background-color: alpha(#1d1d20, 0.8); }", css)
        self.assertIn("window.nautilus-window .sidebar-pane { background-color: alpha(#2e2e32, 0.667); }", css)
        self.assertIn("window.nautilus-window:backdrop .sidebar-pane { background-color: alpha(#2e2e32, 0.5); }", css)
        self.assertEqual(data.DIALOG_GREYS["dark"][0], "#1d1d20", "the same grey as the file dialogs")
        self.assertEqual(data.nautilus_css(data.TRANSPARENCY_DEFAULT), "", "Omarchy's default: nothing of ours")
        self.assertIn("alpha(#1d1d20, 1)", data.nautilus_css(0), "step 1: solid")

    def test_the_rest_of_gtk_css_is_left_alone(self):
        write(data.GTK4_CSS, "/* mine */\nlabel { color: red; }\n")
        self.assertTrue(data.write_nautilus_css(2))
        self.assertFalse(data.write_nautilus_css(2), "same step: no change, so no Nautilus restart")
        self.assertTrue(data.write_nautilus_css(4))
        text = data.GTK4_CSS.read_text()
        self.assertEqual(text.count(data.NAUTILUS_CSS_START), 1, "one block, replaced")
        self.assertTrue(text.startswith("/* mine */\nlabel { color: red; }"))
        data.write_nautilus_css(data.TRANSPARENCY_DEFAULT)
        self.assertEqual(data.GTK4_CSS.read_text().strip(), "/* mine */\nlabel { color: red; }")
        data.GTK4_CSS.write_text("")
        data.write_nautilus_css(2)
        data.write_nautilus_css(data.TRANSPARENCY_DEFAULT)
        self.assertFalse(data.GTK4_CSS.exists(), "only ours was in it: the file goes too")

    def test_nautilus_is_reopened_not_faded(self):
        calls, restarted = [], []
        clients = [{"address": "0xn", "class": "org.gnome.Nautilus", "tags": ["default-opacity*"]},
                   {"address": "0xa", "class": "foot", "tags": ["default-opacity*"]}]
        saved = (_run.nautilus_windows, _run.nautilus_restart)
        _run.nautilus_windows = lambda: {"/org/gnome/Nautilus/window/1": ["file:///home/x/Downloads"]}
        _run.nautilus_restart = lambda windows, *a, **k: restarted.append(windows)
        try:
            _run.apply_transparency(2, evaluate=calls.append, clients=lambda: clients, sleep=lambda s: None)
            self.assertTrue(any("retag({ '0xa' }" in c for c in calls))
            self.assertFalse(any("0xn" in c for c in calls), "Nautilus isn't faded by Hyprland")
            self.assertTrue(any(c.endswith("nautilus(true)") for c in calls), "its own: out of Hyprland's fade")
            self.assertEqual(restarted, [{"/org/gnome/Nautilus/window/1": ["file:///home/x/Downloads"]}])
            restarted.clear()
            _run.apply_transparency(2, evaluate=calls.append, clients=lambda: clients, sleep=lambda s: None)
            self.assertEqual(restarted, [], "its stylesheet didn't change: no restart")
        finally:
            _run.nautilus_windows, _run.nautilus_restart = saved

    def test_restart_puts_windows_back(self):
        opened, moved, quit_ = [], [], []
        state = {"open": [{"address": "0xold", "class": "org.gnome.Nautilus", "title": "Downloads",
                           "workspace": {"name": "1"}}]}

        def clients():
            return state["open"]

        def open_(uris):
            opened.append(uris)
            state["open"] = state["open"] + [{"address": "0xnew", "class": "org.gnome.Nautilus", "title": "Downloads",
                                              "workspace": {"name": "special:scratchpad"}}]

        def quit_now():
            quit_.append(1)
            state["open"] = []
        saved = _run.nautilus_restart
        _run.nautilus_restart = _NAUTILUS_RESTART   # the real one, with fakes for everything it touches
        try:
            _run.nautilus_restart({"/w/1": ["file:///home/x/Downloads"]}, evaluate=moved.append, clients=clients,
                                  sleep=lambda s: None, quit_=quit_now, open_=open_)
        finally:
            _run.nautilus_restart = saved
        self.assertEqual((quit_, opened), ([1], [["file:///home/x/Downloads"]]))
        self.assertEqual(moved, ["hl.dispatch(hl.dsp.window.move({ workspace = '1', follow = false, "
                                 "window = 'address:0xnew' }))"], "back on its workspace")

    def test_restart_two_windows_on_the_same_folder(self):
        """Two "Home" windows on workspaces 1 and 2 come back on 1 and 2, not both on 1 (owner, 2026-10-02)."""
        moved = []
        state = {"open": [{"address": "0xa", "class": "org.gnome.Nautilus", "title": "Home", "workspace": {"name": "1"}},
                          {"address": "0xb", "class": "org.gnome.Nautilus", "title": "Home", "workspace": {"name": "2"}}]}

        def open_(uris):
            n = len(state["open"])
            state["open"] = state["open"] + [{"address": f"0xn{n}", "class": "org.gnome.Nautilus", "title": "Home",
                                              "workspace": {"name": "special:scratchpad"}}]

        def quit_now():
            state["open"] = []
        saved = _run.nautilus_restart
        _run.nautilus_restart = _NAUTILUS_RESTART
        try:
            _run.nautilus_restart({"/w/1": ["file:///home/x"], "/w/2": ["file:///home/x"]}, evaluate=moved.append,
                                  clients=lambda: state["open"], sleep=lambda s: None, quit_=quit_now, open_=open_)
        finally:
            _run.nautilus_restart = saved
        self.assertEqual(sorted(m.split("workspace = '")[1][0] for m in moved), ["1", "2"])

    def test_restart_puts_an_untitled_window_back_too(self):
        """A new window without its title yet (or one that doesn't match): the workspace Nautilus was on."""
        moved = []
        state = {"open": [{"address": "0xold", "class": "org.gnome.Nautilus", "title": "Home", "workspace": {"name": "2"}}]}

        def open_(uris):
            state["open"] = [{"address": "0xnew", "class": "org.gnome.Nautilus", "title": "",
                              "workspace": {"name": "special:scratchpad"}}]

        def quit_now():
            state["open"] = []
        saved = _run.nautilus_restart
        _run.nautilus_restart = _NAUTILUS_RESTART
        try:
            _run.nautilus_restart({"/w/1": ["file:///home/x"]}, evaluate=moved.append, clients=lambda: state["open"],
                                  sleep=lambda s: None, quit_=quit_now, open_=open_)
        finally:
            _run.nautilus_restart = saved
        self.assertEqual(moved, ["hl.dispatch(hl.dsp.window.move({ workspace = '2', follow = false, "
                                 "window = 'address:0xnew' }))"])


class PluginEnabled(unittest.TestCase):
    """The engine runs while Omarchy has OmaSkins on: listed as a plugin, or as its icon in the bar."""

    def check(self, config):
        from omaskins import rotation
        rotation._ENABLED.clear()
        rotation.SHELL_JSON = SANDBOX / "shell.json"
        rotation.SHELL_JSON.write_text(json.dumps(config))
        return rotation.plugin_enabled("io.github.jesuslovesyou1013.omaskins")

    def test_as_its_bar_icon(self):
        me = {"id": "io.github.jesuslovesyou1013.omaskins"}
        self.assertTrue(self.check({"bar": {"layout": {"right": [{"id": "omarchy.clock"}, me]}}, "plugins": []}),
                        "installed with omarchy plugin add: the icon in the bar is what keeps it on")
        self.assertTrue(self.check({"plugins": [me]}))
        self.assertFalse(self.check({"bar": {"layout": {"right": [{"id": "omarchy.clock"}]}}, "plugins": []}),
                         "icon removed from the bar: off")


class SlimPreviews(unittest.TestCase):
    """Browse's font previews slimmed to basic Latin, drawing exactly as the full font did."""
    SOURCE = Path("/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf")

    def setUp(self):
        if not self.SOURCE.exists() or not data._harfbuzz():
            self.skipTest("no sample font or HarfBuzz here")
        self.dir = SANDBOX / "slim"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir()

    def render(self, path):
        code = ("import sys, gi, hashlib\n"
                "gi.require_version('Pango','1.0'); gi.require_version('PangoCairo','1.0')\n"
                "from gi.repository import Pango, PangoCairo\nimport cairo\n"
                "fm = PangoCairo.FontMap.new(); fm.add_font_file(sys.argv[1]); ctx = fm.create_context(); out = []\n"
                "for text in ('JetBrainsMono Nerd Font', 'x', 'JetBra…'):\n"
                "  for px in (19.2, 24.0, 28.8):\n"
                "    lay = Pango.Layout.new(ctx); lay.set_text(text, -1); d = Pango.FontDescription()\n"
                "    d.set_family('JetBrainsMono Nerd Font'); d.set_absolute_size(px * Pango.SCALE); lay.set_font_description(d)\n"
                "    ink, log = lay.get_pixel_extents()\n"
                "    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, log.width + 4, log.height + 4); cr = cairo.Context(surf)\n"
                "    PangoCairo.update_context(cr, ctx); cr.move_to(2, 2); PangoCairo.show_layout(cr, lay); surf.flush()\n"
                "    out.append(fm.load_font(ctx, d).describe().get_family() + str(ink.height) + hashlib.sha1(bytes(surf.get_data())).hexdigest())\n"
                "print(out)\n")
        env = {k: v for k, v in os.environ.items() if k != "FONTCONFIG_FILE"}
        return subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, env=env).stdout

    def test_much_smaller_and_draws_the_same(self):
        font = self.dir / "Preview-Regular.ttf"
        shutil.copy(self.SOURCE, font)
        before = self.render(font)
        self.assertTrue(data.slim_font(font, "JetBrainsMono Nerd Font" + data.PREVIEW_KEEP))
        self.assertLess(font.stat().st_size, self.SOURCE.stat().st_size / 10, "a small fraction of the size")
        self.assertEqual(self.render(font), before, "pixel for pixel, same heights, same font")

    def test_existing_previews_slimmed_once(self):
        old = data.PREVIEW_FONTS
        data.PREVIEW_FONTS = self.dir
        try:
            font = self.dir / "Preview-Regular.ttf"
            shutil.copy(self.SOURCE, font)
            (self.dir / "ttf-x.json").write_text(json.dumps({"family": "JetBrainsMono Nerd Font", "file": str(font)}))
            self.assertEqual(data.slim_preview_fonts(), 1)
            self.assertTrue(json.loads((self.dir / "ttf-x.json").read_text())["slim"])
            self.assertEqual(data.slim_preview_fonts(), 0, "once")
        finally:
            data.PREVIEW_FONTS = old

    def test_unreadable_font_left_alone(self):
        bad = self.dir / "Broken.ttf"
        bad.write_bytes(b"not a font")
        self.assertFalse(data.slim_font(bad, "abc"))
        self.assertEqual(bad.read_bytes(), b"not a font")


class ShellSurfaces(unittest.TestCase):
    """Omarchy's menus, panels and notifications at the step, through the shell's own live setting."""

    def setUp(self):
        self.assertTrue(str(data.SHELL_TOML).startswith(str(SANDBOX)), "never the real shell.toml")
        data.SHELL_TOML.unlink(missing_ok=True)
        data.TRANSPARENCY_FILE.unlink(missing_ok=True)

    def test_block_beside_omarchys_text_size(self):
        write(data.SHELL_TOML, "[font]\nbase-size = 14\n")
        self.assertTrue(data.write_shell_block(4))
        text = data.SHELL_TOML.read_text()
        self.assertTrue(text.startswith("[font]\nbase-size = 14\n"), "Omarchy's text size untouched")
        for surface in data.SHELL_SURFACES:
            self.assertIn(f"[{surface}]\nbackground-alpha = 0.65\n", text)
        self.assertTrue(data.write_shell_block(3))
        self.assertEqual(data.SHELL_TOML.read_text().count(data.SHELL_START), 1, "replaced, not stacked")
        self.assertIn("background-alpha = 0.75", data.SHELL_TOML.read_text())
        self.assertTrue(data.write_shell_block(data.TRANSPARENCY_DEFAULT))
        self.assertEqual(data.SHELL_TOML.read_text(), "[font]\nbase-size = 14\n", "Omarchy's look again, file as it was")

    def test_the_fade_sets_it_and_the_blur(self):
        calls = []
        write(HOME / ".config/hypr/looknfeel.lua", "-- looknfeel\n")
        _run.apply_transparency(4, evaluate=calls.append, clients=lambda: [], sleep=lambda s: None)
        self.assertIn("background-alpha = 0.65", data.SHELL_TOML.read_text())
        self.assertTrue(any(c.endswith("shell_blur(true)") for c in calls))
        calls.clear()
        _run.apply_transparency(1, evaluate=calls.append, clients=lambda: [], sleep=lambda s: None)
        self.assertFalse(data.SHELL_TOML.exists(), "nothing of ours (and the file was only ours)")
        self.assertTrue(any(c.endswith("shell_blur(false)") for c in calls))


class MenuRow(unittest.TestCase):
    """Style › OmaSkins in Omarchy's menu, beside whatever else the extension file holds."""

    def setUp(self):
        from omaskins import menu
        self.m = menu
        self.assertTrue(str(menu.MENU_FILE).startswith(str(SANDBOX)), "never the real menu file")
        menu.MENU_FILE.unlink(missing_ok=True)

    def test_added_once_and_removed_cleanly(self):
        theirs = '{\n  // a comment\n  "setup.plugin.browse": {"label":"Browse Plugins","action":"x"}\n}\n'
        write(self.m.MENU_FILE, theirs)
        self.assertTrue(self.m.add_row("/plug/omaskins-manager"))
        self.assertFalse(self.m.add_row("/plug/omaskins-manager"), "once")
        text = self.m.MENU_FILE.read_text()
        self.assertIn('"style.omaskins"', text)
        stripped = re.sub(r"^\s*//[^\n]*(\n|$)", "", text, flags=re.M)
        stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)   # Omarchy's own JSONC reader
        rows = json.loads(stripped)
        self.assertEqual(list(rows), ["style.omaskins", "setup.plugin.browse"])
        self.assertEqual(rows["style.omaskins"]["action"], "uwsm-app -- /plug/omaskins-manager")
        self.assertTrue(self.m.remove_row())
        self.assertEqual(self.m.MENU_FILE.read_text(), theirs, "exactly as it was")

    def test_no_file_yet(self):
        self.assertTrue(self.m.add_row("/plug/omaskins-manager"))
        self.assertIn('"style.omaskins"', self.m.MENU_FILE.read_text())


class SkipAhead(unittest.TestCase):
    """The bar's palette menu: next theme or background now, the rotation's way, without pausing it."""

    def setUp(self):
        build_fixture()
        from omaskins import rotation
        self.r = rotation
        rotation.REQUEST = SANDBOX / "run/omaskins-next"
        shutil.rmtree(SANDBOX / "run", ignore_errors=True)
        data.ROTATION_STATE.unlink(missing_ok=True)
        data.ROTATION_FILE.unlink(missing_ok=True)

        class FakeDesktop:
            def __init__(self):
                self.did = []

            def set_theme(self, name, background=None):
                self.did.append(("rotation theme", name, background))
                write(data.STATE_DIR / "theme.name", name)
                return True

            def apply_theme(self, name):
                self.did.append(("omarchy theme", name))
                write(data.STATE_DIR / "theme.name", name)
                return True

            def set_background(self, path):
                self.did.append(("background", str(path)))
                return True

            def next_background(self):
                self.did.append(("omarchy next background",))
                return True
        self.desk = FakeDesktop()

    def test_without_rotation_theme_only_then_omarchy_bg_next(self):
        names = self.r.all_themes()
        self.assertGreater(len(names), 1)
        write(data.STATE_DIR / "theme.name", names[0])
        self.assertTrue(self.r.skip_ahead(self.desk, "theme"))
        theme_calls = [c for c in self.desk.did if c[0] == "rotation theme"]
        self.assertEqual(theme_calls, [("rotation theme", names[1], None)],
                         "next installed theme by name; wallpaper kept (no Omarchy apply)")
        self.assertFalse(any(c[0] == "omarchy theme" for c in self.desk.did),
                         "must not use apply_theme (that would pick a new background)")
        # Rebase onto the theme's own file is OK; a *new* wallpaper pick is not.
        new_bgs = [c for c in self.desk.did if c[0] == "background"]
        self.assertTrue(all(Path(c[1]).is_file() for c in new_bgs))
        self.desk.did.clear()
        self.assertTrue(self.r.skip_ahead(self.desk, "background"))
        self.assertEqual(self.desk.did[-1], ("omarchy next background",))

    def test_next_theme_stays_through_the_next_slot(self):
        names = self.r.all_themes()
        write(data.STATE_DIR / "theme.name", names[0])
        now = time.mktime((2026, 10, 8, 9, 18, 0, 0, 0, -1))      # two minutes before a 20-minute slot
        plan = data.RotationPlan(names[0])
        plan.minutes = 20
        data.save_rotation(plan)
        self.r.skip_ahead(self.desk, "theme", now=now)
        st = data.rotation_status()
        self.assertEqual(st["last_theme"], names[1], "the engine's own change: tick() sees no switcher pick")
        slot = time.mktime((2026, 10, 8, 9, 20, 0, 0, 0, -1))
        self.assertEqual(st["pick_skip"], slot, "9:18 Next -> the 9:20 slot is skipped")
        self.assertEqual(self.r.next_change(now, st, 20), slot + 20 * 60, "first change after it: 9:40")

    def test_next_background_stays_through_the_next_slot(self):
        names = self.r.all_themes()
        write(data.STATE_DIR / "theme.name", names[0])
        now = time.mktime((2026, 10, 8, 9, 18, 0, 0, 0, -1))
        plan = data.RotationPlan(names[0])
        plan.minutes = 20
        data.save_rotation(plan)
        self.assertTrue(self.r.skip_ahead(self.desk, "background", now=now))
        st = data.rotation_status()
        self.assertEqual(st["pick_skip"], time.mktime((2026, 10, 8, 9, 20, 0, 0, 0, -1)))

    def test_with_theme_rotation_its_own_list(self):
        names = self.r.all_themes()[:3]
        plan = data.RotationPlan(names[0])
        plan.themes = True
        plan.checked["All day"] = list(names)
        data.save_rotation(plan)
        write(data.STATE_DIR / "theme.name", names[0])
        self.r.skip_ahead(self.desk, "theme")
        self.assertEqual(self.desk.did[-1][0], "rotation theme")
        self.assertIn(self.desk.did[-1][1], names[1:], "one of the rotation's themes, not the current one")
        self.assertIsNone(self.desk.did[-1][2], "palette next theme: no paired background")

    def test_theme_skip_keeps_wallpaper_even_when_backgrounds_rotate(self):
        """Regression: with Backgrounds (and Mix) on, palette Next theme used to pair a new bg."""
        # Use themes that actually ship backgrounds in the fixture (aura + tokyo-night + nord builtin).
        names = ["aura", "tokyo-night", "nord"]
        plan = data.RotationPlan(names[0])
        plan.themes = True
        plan.backgrounds = True
        plan.mix = True
        plan.checked["All day"] = list(names)
        # Leave theme_picks empty: pool() treats unchecked picks as "all backgrounds of that theme".
        data.save_rotation(plan)
        write(data.STATE_DIR / "theme.name", names[0])
        # Point the link at aura's own file so rebase (if any) is unambiguous.
        aura_bg = HOME / ".config/omarchy/themes/aura/backgrounds/aura-1.jpg"
        link = data.STATE_DIR / "background"
        link.unlink(missing_ok=True)
        link.symlink_to(aura_bg)
        before_bg = self.r.canonical_background(names[0])
        self.assertTrue(self.r.skip_ahead(self.desk, "theme"))
        theme_calls = [c for c in self.desk.did if c[0] == "rotation theme"]
        self.assertEqual(len(theme_calls), 1)
        self.assertIn(theme_calls[0][1], names[1:])
        self.assertIsNone(theme_calls[0][2], "set_theme(..., None): colours only")
        # Any set_background must be rebase to the *same* wallpaper, not a fresh choose() pick.
        for c in self.desk.did:
            if c[0] == "background":
                self.assertEqual(os.path.realpath(c[1]), os.path.realpath(before_bg),
                                 "rebase only; must not pick a new background")

    def test_asks_the_running_engine(self):
        import fcntl
        lock = open(self.r.REQUEST.with_name("omaskins-rotate.lock"), "w") if self.r.REQUEST.parent.mkdir(parents=True, exist_ok=True) is None else None
        fcntl.flock(lock, fcntl.LOCK_EX)   # an engine is running
        try:
            self.assertTrue(self.r.request_next("background"))
            self.assertEqual(self.r.take_request(), "background")
            self.assertIsNone(self.r.take_request(), "taken once")
            self.assertFalse(self.r.request_next("anything"))
        finally:
            lock.close()


class FileDialogs(unittest.TestCase):
    """File dialogs (GTK3, Omarchy's dialog service) look like Nautilus, at the slider's level."""

    def setUp(self):
        build_fixture()
        data.TRANSPARENCY_FILE.unlink(missing_ok=True)
        data.GTK3_CSS.unlink(missing_ok=True)
        self.dark = HOME / ".local/share/themes/Adwaita-dark/gtk-3.0/gtk.css"
        shutil.rmtree(HOME / ".local/share/themes", ignore_errors=True)
        data.GTK3_SYSTEM_DARK = [SANDBOX / "usr-share-themes/Adwaita-dark/gtk-3.0/gtk.css"]   # never the real one
        shutil.rmtree(SANDBOX / "usr-share-themes", ignore_errors=True)
        data.GTK3_USER_DARK = self.dark

    def test_like_nautilus_at_the_step(self):
        write(self.dark, "/* GTK3's dark theme */")
        css = data.dialog_css(3, "dark")
        self.assertIn("dialog.background, dialog.background.csd, dialog headerbar.titlebar, dialog .titlebar headerbar "
                      "{ background-color: alpha(#1d1d20, 0.75);", css, "Nautilus's grey, title bar included")
        self.assertIn("alpha(#1d1d20, 0.68)", css, "unfocused a little more see-through")
        self.assertIn("dialog filechooser placessidebar { background-color: alpha(#2e2e32, 0.4); }", css,
                      "sidebar 10 points more solid, like Nautilus's")
        self.assertIn("alpha(#ffffff, 0.75)", data.dialog_css(3, "light"), "light themes: Nautilus's light grey")
        self.assertIn("dialog:backdrop filechooser placessidebar { background-color: alpha(#2e2e32, 0.312); }", css,
                      "unfocused: its own sidebar level (a selector that matches)")
        self.assertEqual(data.dialog_css(data.TRANSPARENCY_DEFAULT, "dark"), "", "Omarchy's default: nothing of ours")

    def test_dark_theme_without_the_package(self):
        """No gnome-themes-extra: OmaSkins' one-line copy of GTK3's built-in dark Adwaita (no password)."""
        system = data.GTK3_SYSTEM_DARK[0]
        self.assertTrue(data.ensure_gtk3_dark())
        self.assertIn('@import url("resource:///org/gtk/libgtk/theme/Adwaita/gtk-contained-dark.css");',
                      self.dark.read_text(), "the same line the package has")
        self.assertTrue(data.gtk3_dark_available())
        self.assertTrue(data.dialog_css(4, "dark"), "so dark-theme dialogs get their look")
        self.assertFalse(data.ensure_gtk3_dark(), "once")
        write(system, "/* the package's */")
        self.assertTrue(data.ensure_gtk3_dark(), "the real one arrived")
        self.assertFalse(self.dark.exists(), "ours removed: never in front of Omarchy's")
        self.assertFalse(self.dark.parent.parent.exists(), "no empty folders left")
        system.unlink()
        write(self.dark, "/* yours */")
        self.assertFalse(data.ensure_gtk3_dark(), "a file of yours is left alone")
        self.assertEqual(self.dark.read_text(), "/* yours */")

    def test_dark_needs_gtk3s_dark_theme(self):
        self.assertEqual(data.dialog_css(4, "dark"), "", "no Adwaita-dark: dark text on dark grey, so nothing")
        self.assertTrue(data.dialog_css(4, "light"), "light needs nothing extra")

    def test_the_rest_of_gtk3_css_is_left_alone(self):
        write(self.dark, "x")
        write(data.GTK3_CSS, "/* mine */\n")
        self.assertTrue(data.write_dialog_css(4, "dark"))
        self.assertFalse(data.write_dialog_css(4, "dark"), "same: no change, so no restart")
        self.assertTrue(data.write_dialog_css(data.TRANSPARENCY_DEFAULT, "dark"))
        self.assertEqual(data.GTK3_CSS.read_text(), "/* mine */\n")

    def engine(self, open_classes, running=True):
        from omaskins import rotation
        self.restarts, self.evals = [], []
        return rotation.DialogLook(clients=lambda: [{"class": c} for c in open_classes], evaluate=self.evals.append,
                                   running=lambda: running, restart=lambda: self.restarts.append(1))

    def test_service_restarted_only_with_no_dialog_open(self):
        write(self.dark, "x")
        open_now = ["xdg-desktop-portal-gtk"]
        look = self.engine(open_now)
        data.save_transparency(4)
        look.glance()
        self.assertTrue(data.GTK3_CSS.exists())
        self.assertTrue(self.evals[-1].endswith("dialogs(true)"), "out of Hyprland's fade")
        self.assertEqual(self.restarts, [], "a dialog is open: it waits")
        open_now.clear()
        look.glance()
        self.assertEqual(self.restarts, [1], "closed: restarted (nothing on screen)")
        look.glance()
        self.assertEqual(self.restarts, [1], "once")

    def test_nautilus_look_follows_an_update_without_a_restart(self):
        write(self.dark, "x")
        write(data.GTK4_CSS, data.NAUTILUS_CSS_START + "\nold look\n" + data.NAUTILUS_CSS_END + "\n")
        data.save_transparency(4)
        look = self.engine(["org.gnome.Nautilus"])
        look.glance()
        self.assertEqual(data.GTK4_CSS.read_text(), data.nautilus_css(4), "today's look, for its next start")

    def test_service_not_running_reads_it_when_it_starts(self):
        write(self.dark, "x")
        look = self.engine([], running=False)
        data.save_transparency(3)
        look.glance()
        look.glance()
        self.assertEqual(self.restarts, [])
        data.save_transparency(data.TRANSPARENCY_DEFAULT)
        look.glance()
        self.assertTrue(self.evals[-1].endswith("dialogs(false)"), "default step: Hyprland's fade, as Omarchy has it")

    def test_hyprland_file_keeps_its_fade_off_only_with_our_block(self):
        if not shutil.which("lua"):
            self.skipTest("no lua")
        lua = SANDBOX / "dialogs.lua"
        lua.write_text("o = { window = function(m, r) print('rule', m.class or m.tag) end }\n"
                       "hl = { config = function() end }\n" + data.TRANSPARENCY_LUA)
        run_lua = lambda: subprocess.run(["lua", str(lua)], capture_output=True, text=True,
                                         env=dict(os.environ, XDG_CONFIG_HOME=str(HOME / ".config"))).stdout
        data.save_transparency(4)
        self.assertNotIn("xdg-desktop-portal-gtk", run_lua())
        write(self.dark, "x")
        data.write_dialog_css(4, "dark")
        self.assertIn("rule\t^xdg-desktop-portal-gtk$", run_lua())


class QtStyle(unittest.TestCase):
    """Qt apps in the theme's colours and transparency (OmaSkins' own Qt style): one recipe for every theme,
    built as you, no packages; switched off, everything of it goes."""

    def setUp(self):
        build_fixture()
        from omaskins import qtstyle
        self.q = qtstyle
        # Never the real runtime folder or pacman's records.
        qtstyle.MARKS = SANDBOX / "run/omaskins-qt"
        shutil.rmtree(qtstyle.MARKS, ignore_errors=True)
        qtstyle.PACKAGES = SANDBOX / "pacman-local"
        shutil.rmtree(qtstyle.PACKAGES, ignore_errors=True)
        (qtstyle.PACKAGES / "qt5-base-5.15.18+kde+r1-1").mkdir(parents=True)
        for f in (qtstyle.SETTING, qtstyle.PALETTE, data.TRANSPARENCY_FILE):
            f.unlink(missing_ok=True)
        shutil.rmtree(qtstyle.PLUGINS, ignore_errors=True)

    def theme(self, text):
        write(data.STATE_DIR / "theme/colors.toml", text)

    def roles(self, group):
        line = next(l for l in self.q.PALETTE.read_text().splitlines() if l.startswith(group + "="))
        return line.split("=", 1)[1].split(", ")

    def test_palette_any_colour_set_and_the_step(self):
        self.theme('mode = "dark"\nbackground = "#1a1b26"\nforeground = "#a9b1d6"\naccent = "#7aa2f7"\n')  # few colours
        write(data.STATE_DIR / "theme/icons.theme", "Yaru-blue\n")
        data.save_transparency(3)
        self.assertTrue(self.q.rebuild())
        active, inactive = self.roles("active_colors"), self.roles("inactive_colors")
        self.assertEqual(len(active), 21, "all of Qt's colour roles")
        self.assertEqual(active[0], "#ffa9b1d6", "text in the theme's foreground")
        self.assertEqual((active[10], inactive[10]), ("#bf1a1b26", "#ad1a1b26"), "the window colour carries the step")
        self.assertIn("[Icons]\ntheme=Yaru-blue", self.q.PALETTE.read_text())
        data.save_transparency(0)
        self.q.rebuild()
        self.assertEqual(self.roles("active_colors")[10], "#ff1a1b26", "step 1: solid")

    def test_unchanged_palette_is_not_rewritten(self):
        self.theme('background = "#1a1b26"\nforeground = "#a9b1d6"\n')
        self.q.rebuild()
        before = self.q.PALETTE.stat().st_mtime_ns
        time.sleep(0.01)
        self.q.rebuild()
        self.assertEqual(self.q.PALETTE.stat().st_mtime_ns, before, "open apps don't reload for nothing")

    def test_switched_off_writes_nothing_and_removes_it_all(self):
        self.theme('background = "#1a1b26"\nforeground = "#a9b1d6"\n')
        self.q.rebuild()
        write(self.q.LIBRARY, "built")
        self.q.save_enabled(False)
        self.assertFalse(self.q.enabled())
        self.assertFalse(self.q.rebuild())
        self.assertFalse(self.q.needs_build())
        _run.set_qt_apps(False)
        self.assertFalse(self.q.PALETTE.exists() or self.q.PLUGINS.exists(), "nothing of it left")
        _run.set_qt_apps(True)
        self.assertTrue(self.q.enabled())
        self.assertFalse(self.q.SETTING.exists(), "on is the default: no file")

    def test_built_once_per_source_and_qt_version(self):
        if not (shutil.which(self.q.QMAKE) and shutil.which("make")):
            self.skipTest("no Qt5 build kit here")
        self.assertTrue(self.q.needs_build())
        ran = []

        def runner(argv, cwd, **kw):
            ran.append(argv[0])
            if argv[0] == "make":
                (Path(cwd) / "libomaskins.so").write_text("lib")
            return subprocess.CompletedProcess(argv, 0)
        self.assertEqual(self.q.build(runner), (True, "built"))
        self.assertEqual(ran, [self.q.QMAKE, "make"])
        self.assertEqual(self.q.LIBRARY.read_text(), "lib")
        self.assertFalse(self.q.BUILD_DIR.exists(), "no build leftovers")
        self.assertFalse(self.q.needs_build())
        shutil.rmtree(self.q.PACKAGES / "qt5-base-5.15.18+kde+r1-1")
        (self.q.PACKAGES / "qt5-base-5.15.19+kde+r1-1").mkdir()
        self.assertTrue(self.q.needs_build(), "a Qt5 update: built again")
        shutil.rmtree(self.q.PACKAGES / "qt5-base-5.15.19+kde+r1-1")
        self.assertFalse(self.q.needs_build(), "no Qt5 (no Qt5 apps): nothing to build")

    def test_failed_build_keeps_the_old_one(self):
        write(self.q.LIBRARY, "old")
        fail = lambda argv, cwd, **kw: subprocess.CompletedProcess(argv, 1 if argv[0] == "make" else 0)
        ok, why = self.q.build(fail)
        self.assertFalse(ok)
        self.assertIn("make failed", why)
        self.assertEqual(self.q.LIBRARY.read_text(), "old")

    def test_shared_and_applied_by_an_import(self):
        from omaskins import share
        self.q.save_enabled(False)
        self.assertIs(share.collect([])[0]["qt_apps"], False)
        rows = {r["id"]: r for r in share.items({"format": share.FORMAT, "qt_apps": True, "transparency": 2})}
        self.assertEqual((rows["qt_apps"]["group"], rows["qt_apps"]["detail"]), ("Settings", "on"))
        self.assertIn("transparency", rows, "each with its own checkbox")
        ran = []
        saved = _run.perform
        _run.perform = lambda steps, *a, **k: ran.extend(steps)
        try:
            share.apply_look({"qt_apps": True}, {"qt_apps"}, {}, {}, None, [])
            share.apply_look({"qt_apps": False}, set(), {}, {}, None, [])   # not ticked: left alone
        finally:
            _run.perform = saved
        self.assertEqual(ran, [("qt_apps", True)])

    def test_marked_apps_leave_hyprlands_fade(self):
        self.q.MARKS.mkdir(parents=True)
        (self.q.MARKS / "100").touch()
        (self.q.MARKS / "200").touch()   # closed since
        windows = [{"address": "0xa", "pid": 100, "tags": ["default-opacity*"]},
                   {"address": "0xb", "pid": 100, "tags": ["default-opacity*", "omaskins-qt"]},   # done already (Omarchy's
                   # own tag comes back from its rule: that must not make it "to do" again every few seconds)
                   {"address": "0xc", "pid": 300, "tags": ["default-opacity*"]}]   # another app: left alone
        calls = []
        changed = self.q.unfade_windows(lambda: windows, calls.append, alive=lambda pid: pid == 100)
        self.assertEqual(changed, ["0xa"])
        self.assertEqual(calls, ["hl.dispatch(hl.dsp.window.tag({ window = 'address:0xa', tag = '-default-opacity' }))",
                                 "hl.dispatch(hl.dsp.window.tag({ window = 'address:0xa', tag = '+omaskins-qt' }))"])
        self.assertFalse((self.q.MARKS / "200").exists(), "closed apps' markers cleared")

    def test_hyprland_turns_it_on_only_when_everything_is_there(self):
        """Run omaskins.lua's Qt part in a real Lua with stand-ins for Hyprland's o.window / hl.env."""
        if not shutil.which("lua"):
            self.skipTest("no lua")
        lua = SANDBOX / "qt.lua"
        lua.write_text("o = { window = function(m, r) print('rule', m.tag, r.opacity) end }\n"
                       "hl = { env = function(k, v) print('env', k, v) end }\n" + data.QT_LUA)

        def run_lua(**env):
            clean = {k: v for k, v in os.environ.items() if k not in ("QT_STYLE_OVERRIDE", "QT_PLUGIN_PATH")}
            return subprocess.run(["lua", str(lua)], capture_output=True, text=True,
                                  env=dict(clean, HOME=str(HOME), XDG_CONFIG_HOME=str(HOME / ".config"),
                                           **env)).stdout.rstrip("\n").split("\n")
        plugins = str(HOME / ".local/share/omaskins/qt5")
        self.assertEqual(run_lua(), ["rule\tomaskins-qt\t1 1"], "not built: nothing set")
        write(self.q.LIBRARY, "lib")
        self.assertEqual(run_lua(), ["rule\tomaskins-qt\t1 1"], "OmaSkins itself removed: nothing set")
        write(HOME / ".config/omarchy/plugins/io.github.jesuslovesyou1013.omaskins/manifest.json", "{}")
        self.assertEqual(run_lua(QT_PLUGIN_PATH="/x"), ["rule\tomaskins-qt\t1 1", f"env\tQT_PLUGIN_PATH\t{plugins}:/x",
                                                       "env\tQT_STYLE_OVERRIDE\tOmaSkins"])
        self.assertEqual(run_lua(QT_PLUGIN_PATH=plugins)[1], "env\tQT_STYLE_OVERRIDE\tOmaSkins",
                         "a reload doesn't add the path twice")
        self.q.save_enabled(False)
        self.assertEqual(run_lua(QT_STYLE_OVERRIDE="OmaSkins"), ["rule\tomaskins-qt\t1 1", "env\tQT_STYLE_OVERRIDE\t"],
                         "switched off: cleared for apps opened from then on")

if __name__ == "__main__":
    unittest.main()


class Uninstall(unittest.TestCase):
    """Removing OmaSkins: everything back to Omarchy's own, nothing of OmaSkins left (owner, 2026-10-08)."""

    PLUGIN = "io.github.jesuslovesyou1013.omaskins"

    def setUp(self):
        build_fixture()
        from omaskins import uninstall
        self.u = uninstall
        self.did, self.said = [], []
        write(HOME / ".config/hypr/looknfeel.lua", "-- your look and feel\nhl.config({})\n")
        write(HOME / ".config/gtk-4.0/gtk.css", "/* yours */\n")
        write(HOME / ".config/gtk-3.0/gtk.css", "/* yours too */\n")
        write(HOME / ".config/omarchy/shell.toml", "[font]\nbase-size = 13\n")
        test = self

        class FakeRun:   # run.py with the live desktop replaced; everything that writes files is the real one
            FADE_LUA, _tags = _run.FADE_LUA, staticmethod(_run._tags)
            windows = []

            @staticmethod
            def apply_transparency(step):
                _run.apply_transparency(step, evaluate=lambda code: test.did.append(("eval", code)),
                                        clients=lambda: FakeRun.windows, sleep=lambda s: None)
            hypr_eval = staticmethod(lambda code: test.did.append(("eval", code)))
            hypr_clients = staticmethod(lambda: FakeRun.windows)
            dialog_service_running = staticmethod(lambda: False)
            restart_dialog_service = staticmethod(lambda: test.did.append(("restart dialogs",)))
            run = staticmethod(lambda argv, timeout=300: test.did.append(("run", argv)))
            shell_restyle = staticmethod(lambda: test.did.append(("restyle",)))
            set_theme = staticmethod(lambda name, bg=None: test.did.append(("theme", name, bg)))
        self.run = FakeRun
        self._sleep, time.sleep = time.sleep, lambda s: None
        self._pkg, data.builtin_package = data.builtin_package, lambda: ""   # never ask the real pacman

    def tearDown(self):
        time.sleep, data.builtin_package = self._sleep, self._pkg

    def use_everything(self):
        _run.write_corners(True, 12)
        self.run.apply_transparency(3)
        data.write_dialog_css(3, "dark")
        write(HOME / ".config/omaskins/rotation.json", "{}")
        write(HOME / ".config/omaskins/qt-palette.conf", "x")
        write(HOME / ".local/share/omaskins/qt5/styles/libomaskins.so", "x")
        write(data.OMASKINS_STATE / "rotation-state.json", "{}")
        write(data.CACHE_DIR / "themes.html", "x")
        self.did.clear()

    def test_a_copy_is_kept_outside_the_plugin_folder(self):
        self.assertTrue(self.u.stage())
        self.assertFalse(self.u.stage(), "nothing rewritten when it's already the same")
        names = {p.name for p in (self.u.STAGED / "omaskins").iterdir()}
        self.assertEqual(names, {p.name for p in (ROOT / "omaskins").glob("*.py")})
        self.assertNotIn(str(ROOT), str(self.u.STAGED))
        r = subprocess.run([sys.executable, "-B", str(self.u.ENTRY)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, "runs on its own, from the copy: " + r.stderr)
        write(self.u.STAGED / "omaskins" / "gone_module.py", "")
        self.assertTrue(self.u.stage())
        self.assertFalse((self.u.STAGED / "omaskins" / "gone_module.py").exists())

    def test_only_when_the_plugin_is_really_gone(self):
        folder = self.u.PLUGINS / self.PLUGIN
        write(folder / "manifest.json", "{}")
        self.assertFalse(self.u.removed(self.PLUGIN, wait=0, enabled=lambda i: False), "switched off: folder still there")
        shutil.rmtree(folder)
        self.assertFalse(self.u.removed(self.PLUGIN, wait=0, enabled=lambda i: True), "still in shell.json")
        self.assertTrue(self.u.removed(self.PLUGIN, wait=0, enabled=lambda i: False))
        self.assertEqual(self.u.main([self.PLUGIN, "extra"]), 2)
        write(folder / "manifest.json", "{}")
        self.assertEqual(self.u.main([self.PLUGIN, "--now"]), 0, "does nothing while the folder exists")

    def test_looknfeel_gets_exactly_its_old_text_back(self):
        looknfeel = HOME / ".config/hypr/looknfeel.lua"
        for before in ("-- your look and feel\nhl.config({})\n", "no newline at the end", ""):
            looknfeel.write_text(before)
            data.CORNERS_FILE.unlink(missing_ok=True)
            _run.write_corners(True, 12)
            self.assertIn(data.CORNERS_REQUIRE, looknfeel.read_text())
            self.assertTrue(self.u.strip_require())
            self.assertEqual(looknfeel.read_text().rstrip("\n"), before.rstrip("\n"))
            self.assertFalse(self.u.strip_require(), "nothing left to take out")

    def test_everything_back_and_nothing_left(self):
        before = {k: v[0] for k, v in snapshot(HOME).items()}
        texts = {p: (HOME / p).read_text() for p in (".config/hypr/looknfeel.lua", ".config/gtk-4.0/gtk.css",
                                                      ".config/gtk-3.0/gtk.css", ".config/omarchy/shell.toml")}
        self.use_everything()
        self.assertTrue(data.CORNERS_FILE.exists())
        self.assertIn("OmaSkins", (HOME / ".config/gtk-4.0/gtk.css").read_text())
        self.assertIn("OmaSkins", (HOME / ".config/omarchy/shell.toml").read_text())
        code = self.u.uninstall(run=self.run, choose=lambda *a: self.fail("nothing to ask"),
                                notify=lambda *a: self.said.append(a), log=lambda *a: None)
        self.assertEqual(code, 0)
        self.assertEqual({k: v[0] for k, v in snapshot(HOME).items()}, before, "not one file more or less")
        for p, text in texts.items():
            self.assertEqual((HOME / p).read_text().rstrip("\n"), text.rstrip("\n"), p)
        left = [str(p) for p in HOME.rglob("*") if "omaskins" in p.name.lower()]
        self.assertEqual(left, [], "no folder or file of OmaSkins' anywhere")
        self.assertIn(("run", ["hyprctl", "reload"]), self.did)
        self.assertIn(("restyle",), self.did)
        self.assertEqual(self.said[0][0], "OmaSkins removed")

    def test_nothing_set_nothing_touched(self):
        before = snapshot(HOME)
        self.u.uninstall(run=self.run, choose=lambda *a: None, notify=lambda *a: None, log=lambda *a: None)
        self.assertEqual(snapshot(HOME), before)
        self.assertNotIn(("run", ["hyprctl", "reload"]), self.did, "no reload when OmaSkins had no Hyprland file")

    def test_one_part_failing_doesnt_stop_the_rest(self):
        self.use_everything()
        self.run.shell_restyle = staticmethod(lambda: (_ for _ in ()).throw(OSError("shell gone")))
        code = self.u.uninstall(run=self.run, choose=lambda *a: None, notify=lambda *a: self.said.append(a),
                                log=lambda *a: None)
        self.assertEqual(code, 1)
        self.assertIn("rounded corners", self.said[0][1])
        self.assertFalse((HOME / ".config/omaskins").exists(), "its folders still went")

    def mix(self):
        """tokyo-night's colours with aura's background: what only OmaSkins can set up."""
        link = HOME / ".local/state/omarchy/current/background"
        link.unlink()
        link.symlink_to(HOME / ".config/omarchy/themes/aura/backgrounds/aura-1.jpg")

    def test_matching_theme_and_background_ask_nothing(self):
        self.assertIsNone(self.u.mismatch())
        self.assertEqual(self.u.match_up(choose=lambda *a: self.fail("asked"), run=self.run, log=lambda *a: None), "")
        link = HOME / ".local/state/omarchy/current/background"
        link.unlink()
        link.symlink_to(HOME / ".config/omarchy/backgrounds/tokyo-night/mine.png")
        self.assertIsNone(self.u.mismatch(), "your own background for this theme belongs to it")

    def test_keep_the_theme_takes_one_of_its_own_backgrounds(self):
        self.mix()
        self.assertEqual(self.u.mismatch(), ("tokyo-night", "aura"))
        asked = []
        said = self.u.match_up(choose=lambda *a: asked.append(a) or self.u.KEEP_THEME, run=self.run, log=lambda *a: None)
        self.assertEqual(asked, [("tokyo-night", "aura")])
        (_, argv), = self.did
        self.assertEqual(argv[0], "omarchy-theme-bg-set")
        own = {str(OMARCHY / "themes/tokyo-night/backgrounds" / n) for n in ("0-a.jpg", "1-b.jpg")}
        self.assertIn(argv[1], own | {str(HOME / ".config/omarchy/backgrounds/tokyo-night/mine.png")})
        self.assertIn("Tokyo Night", said)

    def test_keep_the_background_switches_to_its_theme(self):
        self.mix()
        said = self.u.match_up(choose=lambda *a: self.u.KEEP_BACKGROUND, run=self.run, log=lambda *a: None)
        self.assertEqual(self.did, [("theme", "aura", str(HOME / ".config/omarchy/themes/aura/backgrounds/aura-1.jpg"))])
        self.assertIn("Aura", said)

    def test_menu_closed_leaves_both(self):
        self.mix()
        said = self.u.match_up(choose=lambda *a: None, run=self.run, log=lambda *a: None)
        self.assertEqual(self.did, [])
        self.assertIn("left as they are", said)

    def test_removed_builtins_come_back_in_one_password_terminal(self):
        shipped = ["nord", "tokyo-night", "gone-one", "gone-two"]
        keep = (data.shipped_builtins, data.package_version, _run.perform, _run.wait_import_password,
                _run.clear_password_marks)
        data.builtin_package = lambda: "omarchy"
        data.shipped_builtins = lambda pkg: shipped
        data.package_version = lambda pkg: "4.0-1"
        opened = []
        _run.perform = lambda steps: opened.append(steps)
        _run.clear_password_marks = lambda mark=None: None
        _run.wait_import_password = lambda lists: ({("restore-theme", "gone-one"): True,
                                                    ("restore-theme", "gone-two"): False}, "terminal closed")
        try:
            self.assertEqual(self.u.restore_builtins(log=lambda *a: None), ["gone-two"])
        finally:
            (data.shipped_builtins, data.package_version, _run.perform, _run.wait_import_password,
             _run.clear_password_marks) = keep
        (step,), = opened
        self.assertEqual(step[0], "terminal")
        self.assertEqual(step[1].count("sudo -v"), 1, "one password for all of them")
        self.assertIn("Restore built-in theme: Gone One", step[1])
        self.assertTrue(_run._font_terminal(["omarchy-launch-floating-terminal-with-presentation", step[1]])
                        or True)   # (the allow-list check itself asks pacman; covered in ImportPassword)

    def test_restoring_tidies_the_holding_folder(self):
        for kind in ("unhide", "reinstall"):
            cmd = data.builtin_terminal_command(kind, "nord", "omarchy")
            self.assertIn(f"sudo rmdir {data.HIDDEN_BUILTINS} {data.HIDDEN_BUILTINS.parent}", cmd)
        self.assertNotIn("rmdir", data.builtin_terminal_command("hide", "nord"))


class DownloadLimits(unittest.TestCase):
    """Nothing is downloaded past its limit; each limit is the largest on offer plus about 10%."""

    def test_limits_as_measured(self):
        m = data.MIB
        self.assertEqual((data.MAX_IMAGE, data.MAX_FONT_PACKAGE, data.MAX_THEME), (52 * m, 111 * m, 384 * m))
        for limit, largest in ((data.MAX_IMAGE, 47.36), (data.MAX_FONT_PACKAGE, 100.68), (data.MAX_THEME, 348.67)):
            self.assertTrue(1.09 <= limit / m / largest <= 1.11, (limit, largest))

    def test_a_theme_download_past_the_limit_is_stopped(self):
        from omaskins import run
        check, run.check = run.check, lambda argv: None
        try:
            line = "Receiving objects:  66% (40/60), 3.00 MiB | 2.52 MiB/s"
            r = run.run_with_progress(["sh", "-c", f"printf '{line}\\r' >&2; sleep 30"], lambda pct: None,
                                      max_bytes=2 * data.MIB)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("larger than 2 MB", r.stderr)
            r = run.run_with_progress(["sh", "-c", f"printf '{line}\\r' >&2"], lambda pct: None)
            self.assertEqual(r.returncode, 0, "well under the real limit")
        finally:
            run.check = check

    def test_the_font_popularity_pages_are_limited_too(self):
        import urllib.request
        class Big:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=-1):
                self.asked = n
                return b"x" * (n if n > 0 else 10)
        big = Big()
        keep, urllib.request.urlopen = urllib.request.urlopen, lambda *a, **k: big
        data.PKGSTATS_CACHE.unlink(missing_ok=True)
        try:
            self.assertEqual(data.font_popularity(force=True), {})
        finally:
            urllib.request.urlopen = keep
        self.assertEqual(big.asked, data.MAX_PAGE + 1, "never read to the end, whatever the size")
