"""Headless tests for OmaSkins' data layer. No display or network needed.

Everything runs against a fake home (HOME, XDG_CONFIG_HOME, XDG_STATE_HOME,
XDG_CACHE_HOME all point into a temp dir) and a fake OMARCHY_PATH, and the
suite refuses to start if any of them still points at the real home.

    python3 -m unittest discover -s tests -v
"""

import json
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
for var, sub in (("HOME", "home"), ("XDG_CONFIG_HOME", "home/.config"), ("XDG_STATE_HOME", "home/.local/state"),
                 ("XDG_CACHE_HOME", "home/.cache"), ("OMARCHY_PATH", "omarchy"),
                 ("OMASKINS_HIDDEN_THEMES", "hidden-themes"), ("OMASKINS_PACMAN_CONF", "etc/pacman.conf")):
    os.environ[var] = str(SANDBOX / sub)
    (SANDBOX / sub).mkdir(parents=True, exist_ok=True) if not sub.endswith(".conf") else None
for var in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    if os.environ[var].startswith(str(REAL_HOME) + "/") or os.environ[var] == str(REAL_HOME):
        sys.exit(f"refusing to run: {var} points into the real home")

sys.path.insert(0, str(ROOT))
from omaskins import data  # noqa: E402

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

    def test_only_checked_themes_open_while_themes_rotate(self):
        p = self.plan
        self.assertTrue(p.can_open("aura"))  # themes off: any theme opens
        p.themes = True
        self.assertFalse(p.can_open("aura"))
        self.assertTrue(p.can_open("tokyo-night"))

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

    def test_stock_picker_restarts_the_matching_timers(self):
        p = self.plan
        self.assertEqual(p.manual_change(True, True), ([], ""), "nothing rotating: nothing to say")
        p.themes = True
        self.assertEqual(p.manual_change(True, True),
                         (["theme"], "Rotation timer reset: next theme in 1 h."))
        self.assertEqual(p.manual_change(False, True), ([], ""), "background picks don't touch the theme timer")
        p.backgrounds = True
        self.assertEqual(p.manual_change(True, True),
                         (["theme", "background"],
                          "Rotation timers reset: next theme in 1 h and next background in 10 min."))
        self.assertEqual(p.manual_change(False, True),
                         (["background"], "Rotation timer reset: next background in 10 min."))
        p.themes = False
        self.assertEqual(p.manual_change(True, True)[0], ["background"],
                         "a theme change brings a new background, so that timer restarts too")

    def test_minutes_text(self):
        self.assertEqual([data.minutes_text(m) for m in (1, 10, 60, 90, 1440)],
                         ["1 min", "10 min", "1 h", "1 h 30 min", "24 h"])

    def test_removed_themes_are_forgotten(self):
        p = self.plan
        p.set_checked("aura", True, self.bgs["aura"])
        p.forget_missing({"tokyo-night"})
        self.assertFalse(p.is_checked("aura"))
        self.assertNotIn("aura", p.theme_picks)


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


class PrototypeChangesNothing(unittest.TestCase):
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
        for bad in (good + "; rm -rf ~", good.replace("nord", "../x", 1), "echo 'Hiding nord...'; sudo rm -rf /",
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


if __name__ == "__main__":
    unittest.main()
