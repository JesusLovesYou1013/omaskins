"""Headless tests for OmaSkins' data layer. No display or network needed.

Everything runs against a fake home (HOME, XDG_CONFIG_HOME, XDG_STATE_HOME,
XDG_CACHE_HOME all point into a temp dir) and a fake OMARCHY_PATH, and the
suite refuses to start if any of them still points at the real home.

    python3 -m unittest discover -s tests -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_HOME = Path.home()
SANDBOX = Path(tempfile.mkdtemp(prefix="omaskins-test-"))
for var, sub in (("HOME", "home"), ("XDG_CONFIG_HOME", "home/.config"), ("XDG_STATE_HOME", "home/.local/state"),
                 ("XDG_CACHE_HOME", "home/.cache"), ("OMARCHY_PATH", "omarchy")):
    os.environ[var] = str(SANDBOX / sub)
    (SANDBOX / sub).mkdir(parents=True, exist_ok=True)
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
        self.assertIn("omarchy-theme-install https://github.com/guilhermetk/omarchy-all-hallows-eve-theme.git",
                      data.theme_actions(None, self.community[1])[0].command)
        cur = data.theme_actions(self.local["tokyo-night"], None, "tokyo-night")
        self.assertEqual(self.labels(cur), ["Remove"], "current built-in: nothing to apply")
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
        self.assertEqual(acts[1].command,
                         "omarchy-install-font 'FiraCode Nerd Font' ttf-firacode-nerd 'FiraCode Nerd Font'")

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


class BuiltinRemoval(unittest.TestCase):
    def test_remove_deletes_folder_and_keeps_updates_from_restoring_it(self):
        build_fixture()
        a = data.builtin_remove_action("nord")
        self.assertEqual(a.label, "Remove")
        self.assertIn(f"sudo rm -rf '{OMARCHY}/themes/nord'", a.command)
        self.assertIn(f"/^\\[options\\]/a NoExtract = {str(OMARCHY).lstrip('/')}/themes/nord/*", a.command)
        self.assertIn("Restore", a.note)
        self.assertEqual(data.theme_actions([t for t in data.local_themes() if t.name == "tokyo-night"][0],
                                            None, "aura")[-1].command, data.builtin_remove_action("tokyo-night").command)

    def test_restore_drops_exactly_that_rule_and_reinstalls(self):
        c = data.builtin_restore_action("nord", "omarchy").command
        self.assertIn("\\|^NoExtract = ", c)
        self.assertIn("/themes/nord/\\*$|d", c)
        self.assertTrue(c.endswith("sudo pacman -S --noconfirm 'omarchy'"), c)

    def test_odd_folder_names_are_refused(self):
        for bad in ("../etc", "a b", "x/y", "", ".hidden"):
            with self.assertRaises(ValueError):
                data.builtin_remove_action(bad)

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
        cmd = data.corners_action(on, px).command
        self.assertTrue(cmd.endswith(" && omarchy restart shell"), "the shell only re-reads rounding on restart")
        subprocess.run(["sh", "-c", cmd.removesuffix(" && omarchy restart shell")], check=True)

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
        for a in (data.builtin_remove_action("nord"), data.builtin_restore_action("nord", "omarchy")):
            self.assertIn("Built-in themes are part of Omarchy's own system package", a.password)
        pkg = data.FontPackage("ttf-x-nerd", "1", "x", installed=False, omarchy_pick="X Nerd Font")
        self.assertTrue(all(a.password for a in data.font_actions(package=pkg)))
        use = data.font_actions(font=data.Font("X Nerd Font", "ttf-x-nerd"), current_package="other")
        self.assertEqual([a.password for a in use if a.label == "Use"], [""], "switching fonts never asks")


class CommandRunner(unittest.TestCase):
    def setUp(self):
        from omaskins import run
        self.run = run

    def test_prototype_allows_nothing(self):
        self.assertEqual(self.run.ALLOWED, frozenset(), "nothing is live yet")
        with self.assertRaises(self.run.NotAllowed):
            self.run.run(["omarchy-theme-set", "nord"])

    def test_shell_strings_and_odd_input_are_refused(self):
        self.run.ALLOWED, saved = frozenset({"true"}), self.run.ALLOWED
        try:
            for bad in ("true", "", [], ["true", 5], None):
                with self.assertRaises(self.run.NotAllowed, msg=repr(bad)):
                    self.run.check(bad)
            self.assertEqual(self.run.run(["true"]).returncode, 0)
            with self.assertRaises(self.run.NotAllowed):
                self.run.run(["rm", "-rf", "/tmp/x"])
        finally:
            self.run.ALLOWED = saved

    def test_no_shell_anywhere(self):
        for f in ("run.py", "data.py", "app.py"):
            self.assertNotIn("shell=True", (ROOT / "omaskins" / f).read_text(), f)


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
