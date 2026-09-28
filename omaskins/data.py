"""Read-only data for OmaSkins: themes, backgrounds, fonts, and what an export would hold.

PROTOTYPE RULE: nothing in this module changes the system. It reads Omarchy's
theme folders, asks fontconfig/pacman what is installed, and downloads public
pages and screenshots into its own cache (~/.cache/omaskins). Every "action"
only *describes* the Omarchy command it would run (see `Action`); the UI shows
that text instead of running it. The one exception is `save_to_pictures`, which
copies a background into ~/Pictures on request (never overwriting anything).

Where things live (Omarchy 4.x):
    /usr/share/omarchy/themes/<name>/          built-in themes ($OMARCHY_PATH/themes)
    ~/.config/omarchy/themes/<name>/            themes you added (git clones)
    ~/.config/omarchy/backgrounds/<name>/       your own backgrounds for a theme
    ~/.local/state/omarchy/current/theme.name   the current theme
    ~/.local/state/omarchy/current/background   symlink to the current background
    https://omarchy.org/themes/                 the community list (name, screenshot, GitHub repo)
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
import urllib.request
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

HOME = Path.home()
OMARCHY_PATH = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy"))
BUILTIN_THEMES = OMARCHY_PATH / "themes"
USER_THEMES = HOME / ".config/omarchy/themes"
USER_BACKGROUNDS = HOME / ".config/omarchy/backgrounds"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "omarchy" / "current"
MENU_DEFAULTS = OMARCHY_PATH / "default/omarchy/omarchy-menu.jsonc"
HYPR_DIR = HOME / ".config/hypr"          # Omarchy's hyprland.lua requires "hypr.<name>" from here
CORNERS_FILE = HYPR_DIR / "omaskins.lua"  # OmaSkins' own file: rounded corners for every theme
CORNERS_REQUIRE = 'require("hypr.omaskins")'
CORNERS_DEFAULT, CORNERS_MAX = 8, 40      # 8 = the example value in Omarchy's own looknfeel.lua
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "omaskins"

THEMES_PAGE = "https://omarchy.org/themes/"
SITE = "https://omarchy.org"
PAGE_TTL = 24 * 3600
USER_AGENT = "OmaSkins/0.1 (+prototype)"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
PREVIEW_NAMES = ("preview.png", "preview.jpg", "preview.jpeg", "preview.webp", "preview.gif", "preview.bmp")
MAX_DOWNLOAD = 8 * 1024 * 1024  # a screenshot or colors.toml; anything bigger is refused
MAX_IMAGE = 40 * 1024 * 1024    # a background can be a big 4K PNG

# The palette keys shown on a theme page, in Omarchy's own order.
SWATCH_KEYS = ("background", "foreground", "accent", "selection", "red", "yellow", "orange",
               "green", "cyan", "blue", "magenta", "brown")


# --------------------------------------------------------------------------- model

@dataclass
class Action:
    """What a button *would* do. The prototype shows `command` and runs nothing."""
    label: str
    command: str
    note: str = ""        # extra line for the confirmation dialog
    blocked: str = ""     # set = the button is greyed out, and this says why
    password: str = ""    # set = it asks for your password, and this says why (always confirmed first)
    steps: tuple = ()     # set = LIVE: what run.perform() really does; empty = prototype, shown only
    busy: str = ""        # shown while a live action runs, e.g. "Applying Nord…"
    done: str = ""        # shown when it worked (default: "<label>: done")


@dataclass
class CommunityTheme:
    name: str
    repo_url: str
    screenshot_url: str

    @property
    def key(self):
        return repo_key(self.repo_url)

    @property
    def install_name(self):
        return install_name_from_url(self.repo_url)


@dataclass
class LocalTheme:
    name: str                  # folder name, e.g. "tokyo-night"
    path: Path
    builtin: bool
    repo_url: str = ""         # for themes you added (read from .git/config)
    preview: Path | None = None
    colors: dict = field(default_factory=dict)

    @property
    def title(self):
        return display_name(self.name)


@dataclass
class Background:
    path: Path
    theme: str
    yours: bool                # True = in ~/.config/omarchy/backgrounds/<theme>/, so removable
    current: bool = False


@dataclass
class Font:
    family: str
    package: str = ""          # pacman package that owns it, if any
    current: bool = False


@dataclass
class FontPackage:
    package: str
    version: str
    description: str
    installed: bool
    omarchy_pick: str = ""     # the family name when Omarchy's own Install › Font menu offers it


# --------------------------------------------------------------------------- names

def display_name(folder):
    """Same rule as omarchy-theme-list: tokyo-night -> Tokyo Night."""
    return re.sub(r"(^|-)([a-z])", lambda m: m.group(1) + m.group(2).upper(), folder).replace("-", " ")


def install_name_from_url(url):
    """The folder omarchy-theme-install would create for this repo URL (same rule as that script)."""
    path = url
    if "://" not in path and ":" in path and "/" not in path.split(":", 1)[0]:
        path = path.split(":", 1)[1]
    base = path.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[:-4]
    base = re.sub(r"^omarchy-", "", base)
    base = re.sub(r"-theme$", "", base)
    return base.lower()


def repo_key(url):
    """'https://github.com/Owner/Repo(.git)' -> 'owner/repo', for matching installs to the list."""
    m = re.search(r"github\.com[/:]([^/]+)/([^/#?]+?)(?:\.git)?/?$", url or "")
    return f"{m.group(1)}/{m.group(2)}".lower() if m else ""


# --------------------------------------------------------------------------- network + cache

def _cache(*parts):
    p = CACHE_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def download(url, dest, max_bytes=MAX_DOWNLOAD):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"{url} is larger than {max_bytes // 1024 // 1024} MB")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def cached_download(url, name, ttl=None, max_bytes=MAX_DOWNLOAD):
    """Download once into the cache (re-fetch after `ttl` seconds, if given)."""
    dest = _cache(name)
    if dest.exists() and (ttl is None or time.time() - dest.stat().st_mtime < ttl):
        return dest
    try:
        return download(url, dest, max_bytes)
    except Exception:
        if dest.exists():  # offline: an old copy beats nothing
            return dest
        raise


def url_cache_name(url, folder):
    ext = os.path.splitext(url.split("?", 1)[0])[1][:6] or ".bin"
    return os.path.join(folder, hashlib.sha1(url.encode()).hexdigest()[:16] + ext)


# --------------------------------------------------------------------------- community themes

_CARD = re.compile(
    r'<li><a href="(?P<href>https://github\.com/[^"]+)"[^>]*>\s*<img src="(?P<img>[^"]+)"[^>]*/?>\s*'
    r'<span[^>]*>(?P<name>[^<]+)</span>', re.S)


def parse_community(html):
    seen, out = set(), []
    for m in _CARD.finditer(html):
        url = unescape(m.group("href")).strip()
        if repo_key(url) in seen:
            continue
        seen.add(repo_key(url))
        img = unescape(m.group("img"))
        out.append(CommunityTheme(name=unescape(m.group("name")).strip(), repo_url=url,
                                  screenshot_url=img if img.startswith("http") else SITE + img))
    return out


def community_themes(force=False):
    page = cached_download(THEMES_PAGE, "themes.html", ttl=0 if force else PAGE_TTL)
    return parse_community(page.read_text(encoding="utf-8", errors="replace"))


def remote_colors(theme):
    """colors.toml straight from the theme's GitHub repo, so un-installed themes get swatches too."""
    key = theme.key
    if not key:
        return {}
    url = f"https://raw.githubusercontent.com/{key}/HEAD/colors.toml"
    try:
        path = cached_download(url, f"colors/{key.replace('/', '__')}.toml", ttl=7 * 24 * 3600)
        return read_toml(path)
    except Exception:
        return {}


def parse_background_listing(text):
    """Image download URLs from a GitHub contents-API listing, sorted by name like Omarchy's own."""
    try:
        items = json.loads(text)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    files = [(i.get("name", ""), i.get("download_url")) for i in items
             if isinstance(i, dict) and i.get("type") == "file" and i.get("download_url")]
    return [url for name, url in sorted(files) if name.lower().endswith(IMAGE_EXT)]


def remote_backgrounds(theme):
    """A not-yet-installed theme's backgrounds, listed from its GitHub repo (cached a week).
    Only the list is fetched here; each image downloads when it's first shown."""
    key = theme.key
    if not key:
        return []
    url = f"https://api.github.com/repos/{key}/contents/backgrounds"
    try:
        path = cached_download(url, f"bglists/{key.replace('/', '__')}.json", ttl=7 * 24 * 3600)
        return parse_background_listing(path.read_text())
    except Exception:  # no backgrounds folder, offline, or GitHub's hourly limit
        return []


# --------------------------------------------------------------------------- local themes

def read_toml(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _git_remote(path):
    """Read the clone's origin URL from .git/config without running git."""
    try:
        text = (path / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text, re.S)
    return m.group(1) if m else ""


def _images(folder):
    try:
        return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT),
                      key=lambda p: p.name)
    except OSError:
        return []


def find_preview(path):
    """Same order as omarchy-theme-switcher: a preview.* file, else the first background."""
    for name in PREVIEW_NAMES:
        for p in (path / name, path / name.upper()):
            if p.is_file():
                return p
    imgs = _images(path / "backgrounds")
    return imgs[0] if imgs else None


def current_theme_name():
    try:
        return (STATE_DIR / "theme.name").read_text().strip()
    except OSError:
        return ""


def local_themes():
    """Built-ins plus yours; a theme you added with the same name hides the built-in (like omarchy-theme-dir)."""
    themes = {}
    for root, builtin in ((BUILTIN_THEMES, True), (USER_THEMES, False)):
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for d in entries:
            if not d.is_dir() or d.name.startswith("."):
                continue
            themes[d.name] = LocalTheme(name=d.name, path=d, builtin=builtin,
                                        repo_url="" if builtin else _git_remote(d),
                                        preview=find_preview(d), colors=read_toml(d / "colors.toml"))
    return sorted(themes.values(), key=lambda t: t.name)


def match_installed(community, local):
    """{community key -> LocalTheme} for list entries that are already installed."""
    by_repo = {repo_key(t.repo_url): t for t in local if t.repo_url and not t.builtin}
    by_name = {t.name: t for t in local if not t.builtin}
    out = {}
    for c in community:
        t = by_repo.get(c.key) or by_name.get(c.install_name)
        if t:
            out[c.key] = t
    return out


# --------------------------------------------------------------------------- backgrounds

def current_background():
    link = STATE_DIR / "background"
    try:
        return Path(os.path.realpath(link)) if link.is_symlink() or link.exists() else None
    except OSError:
        return None


def backgrounds_for(theme, current_theme="", current_bg=None):
    """The theme's own backgrounds, then yours from ~/.config/omarchy/backgrounds/<theme>/."""
    out = [Background(p, theme.name, False) for p in _images(theme.path / "backgrounds")]
    out += [Background(p, theme.name, True) for p in _images(USER_BACKGROUNDS / theme.name)]
    if current_bg:
        state_copy = STATE_DIR / "theme" / "backgrounds"
        for b in out:
            # Any theme's background can be the current one (omarchy-theme-bg-set takes any image).
            # For the current theme, Omarchy copies it into state/current/theme, so the link
            # usually points at that copy instead.
            if b.path == current_bg or (theme.name == current_theme and current_bg.parent == state_copy
                                        and b.path.name == current_bg.name and not b.yours):
                b.current = True
                break
    return out


# --------------------------------------------------------------------------- fonts

def _run(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def current_font():
    return (_run(["omarchy-font-current"], 5).strip().splitlines() or [""])[0]


def installed_fonts():
    """The monospace families Omarchy's font picker offers (same filter as omarchy-font-list)."""
    files = {}
    for line in _run(["fc-list", ":spacing=100", "-f", "%{family[0]}|%{file}\n"]).splitlines():
        fam, _, path = line.partition("|")
        if fam and not re.search(r"emoji|signwriting|omarchy", fam, re.I):
            files.setdefault(fam, path)
    owners = {}
    if files and shutil.which("pacman"):
        for line in _run(["pacman", "-Qo", *files.values()], 30).splitlines():
            m = re.match(r"(\S+) is owned by (\S+) ", line)
            if m:
                owners[m.group(1)] = m.group(2)
    cur = current_font()
    return [Font(f, owners.get(p, ""), f == cur) for f, p in sorted(files.items())]


def omarchy_font_picks():
    """{package: family} from Omarchy's Install › Font menu entries."""
    try:
        text = MENU_DEFAULTS.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    picks = {}
    for m in re.finditer(r"omarchy-install-font\s+(?:'[^']*'|\S+)\s+(\S+)\s+'([^']+)'", text):
        picks[m.group(1)] = m.group(2)
    return picks


def repo_fonts():
    """Nerd Font packages in the Arch repos: the same source Omarchy installs fonts from."""
    picks = omarchy_font_picks()
    out, pkg = [], None
    for line in _run(["pacman", "-Ss", r"^(ttf|otf)-.*nerd"], 30).splitlines():
        if not line.startswith(" "):
            m = re.match(r"\S+/(\S+) (\S+)(.*)", line)
            pkg = FontPackage(m.group(1), m.group(2), "", "[installed" in m.group(3),
                              picks.get(m.group(1), "")) if m else None
            if pkg:
                out.append(pkg)
        elif pkg:
            pkg.description = line.strip()
    return out


def normalized_size(base_px, x_height_ratio, reference_ratio):
    """Font size that gives this face the same x-height as the reference face.

    Faces at the same point size can look very different in size; matching the
    x-height (height of a lowercase 'x') makes them read the same, which is what
    keeps Omarchy's Display scaling feeling identical whatever font is chosen.
    Clamped so a broken metric can't produce something absurd.
    """
    if not x_height_ratio or not reference_ratio:
        return float(base_px)
    return round(base_px * max(0.7, min(1.4, reference_ratio / x_height_ratio)), 1)


# --------------------------------------------------------------------------- actions (described, never run)

def q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def theme_actions(local=None, community=None, current=""):
    acts = []
    if community and not local:
        acts.append(add_theme_action(community))
    if local:
        if local.name != current:
            acts.append(Action("Apply", f"omarchy-theme-set {q(local.name)}",
                               steps=(("run", ["omarchy-theme-set", local.name]),),
                               busy=f"Applying {local.title}…", done=f"{local.title} applied."))
        if local.builtin:
            rm = builtin_remove_action(local.name)
        else:
            rm = Action("Remove", f"omarchy-theme-remove {q(local.name)}",
                        steps=(("run", ["omarchy-theme-remove", local.name]),), busy=f"Removing {local.title}…",
                        done=f"{local.title} removed. You can add it again from Browse.")
        if local.name == current:
            rm.blocked = "This is your current theme. Switch to another one first."
        acts.append(rm)
    return acts


THEME_NAME_OK = re.compile(r"[a-z0-9_][a-z0-9._+-]*")  # omarchy-theme-install's own rule


def add_theme_action(community):
    """Download a theme the way omarchy-theme-install does (its URL check, then the same git clone
    into the same folder) but WITHOUT applying it: right-click Add through a list would otherwise
    switch the whole desktop for every theme."""
    url, name = community.repo_url, install_name_from_url(community.repo_url)
    dest = USER_THEMES / name
    cmd = f"omarchy-git-url-check {q(url)} && git clone -- {q(url)} {q(dest)}"
    if not THEME_NAME_OK.fullmatch(name):
        return Action("Add", cmd, blocked="This repository's name doesn't make a usable theme folder name.")
    return Action("Add", cmd, busy=f"Adding {community.name}…",
                  done=f"{community.name} added. Apply it whenever you like.",
                  steps=(("run", ["omarchy-git-url-check", url]), ("run", ["git", "clone", "--", url, str(dest)])))


# The owner doesn't want password prompts for everyday adding/removing. The few actions that can't
# avoid one (they change system packages) always confirm first and say why.
PASSWORD_BUILTIN = ("Built-in themes are part of Omarchy's own system package, so removing or restoring one "
                    "asks for your password. Themes you added never do.")
PASSWORD_FONT = ("Fonts are system packages, so adding or removing one asks for your password. "
                 "Switching between installed fonts never does.")

# Built-in themes belong to Omarchy's package, so omarchy-theme-remove won't touch them and an
# update would bring a deleted one back. Removing = delete the folder as root + a NoExtract rule
# (it must sit in pacman.conf's [options] section). Restoring = drop the rule, reinstall the package.

def builtin_package():
    """The pacman package that owns the built-in themes ('' if unknown)."""
    return (_run(["pacman", "-Qqo", str(BUILTIN_THEMES)], 10).split() or [""])[0]


def shipped_builtins(package):
    """Every theme the package ships, whether or not its folder is still there."""
    if not package:
        return []
    prefix = str(BUILTIN_THEMES) + "/"
    names = set()
    for line in _run(["pacman", "-Qlq", package], 15).splitlines():
        rest = line[len(prefix):] if line.startswith(prefix) else ""
        if rest.count("/") == 1 and rest.endswith("/"):
            names.add(rest[:-1])
    return sorted(names)


def removed_builtins(shipped):
    """Built-ins the package ships whose folder is gone (removed through OmaSkins)."""
    return [n for n in shipped if not (BUILTIN_THEMES / n).is_dir()]


def _no_extract(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
        raise ValueError(f"unexpected theme folder name: {name!r}")
    return f"NoExtract = {str(BUILTIN_THEMES / name).lstrip('/')}/*"


def builtin_remove_action(name):
    return Action("Remove", f"sudo rm -rf {q(BUILTIN_THEMES / name)} && "
                            f"sudo sed -i '/^\\[options\\]/a {_no_extract(name)}' /etc/pacman.conf",
                  note="It's built into Omarchy: it will stay listed under Browse, marked Built-in, "
                       "with a Restore button.", password=PASSWORD_BUILTIN)


def builtin_restore_action(name, package):
    rule = _no_extract(name).replace("*", "\\*")
    return Action("Restore", f"sudo sed -i '\\|^{rule}$|d' /etc/pacman.conf && sudo pacman -S --noconfirm {q(package)}",
                  password=PASSWORD_BUILTIN)


def background_actions(bg, theme_name):
    acts = []
    if not bg.current:
        acts.append(Action("Set as background", f"omarchy-theme-bg-set {q(bg.path)}",
                           steps=(("run", ["omarchy-theme-bg-set", str(bg.path)]),), done="Background set."))
    if bg.yours:
        rm = Action("Remove", f"rm {q(bg.path)}", steps=(("remove_file", bg.path),), done=f"{bg.path.name} removed.")
        if bg.current:
            rm.blocked = "This is your current background. Set another one first."
        acts.append(rm)
    return acts


def save_to_pictures(src, pictures):
    """Copy a background's original file into `pictures`, byte for byte (full quality).

    The one real action in the prototype (owner's request). Never overwrites: a different
    file with the same name gets "-2", "-3"... Returns (destination, already_there).
    """
    src, pictures = Path(src), Path(pictures)
    pictures.mkdir(parents=True, exist_ok=True)
    dest, n = pictures / src.name, 1
    while dest.exists():
        if dest.stat().st_size == src.stat().st_size and dest.read_bytes() == src.read_bytes():
            return dest, True
        n += 1
        dest = pictures / f"{src.stem}-{n}{src.suffix}"
    shutil.copy2(src, dest)
    return dest, False


def copy_to_theme_action(bg, current_theme):
    """Right-click on another theme's background: copy it into your backgrounds for the current theme,
    kept apart from that theme's own (so it shows as "Yours" and can be removed). None if not offered."""
    if not current_theme or bg.theme == current_theme:
        return None
    dest = USER_BACKGROUNDS / current_theme
    return Action("Copy to current theme's backgrounds", f"cp -n {q(bg.path)} {q(dest)}/",
                  steps=(("copy_in", bg.path, dest),), done=f"Copied into your {display_name(current_theme)} backgrounds.")


def add_background_action(theme_name, files):
    """Copy the images chosen in the file picker into your backgrounds for this theme (never overwriting)."""
    dest = USER_BACKGROUNDS / theme_name
    return Action("Add backgrounds…", f"cp -n {' '.join(q(f) for f in files)} {q(dest)}/",
                  steps=tuple(("copy_in", Path(f), dest) for f in files),
                  done=f"Added {len(files)} background{'s' if len(files) != 1 else ''} to {display_name(theme_name)}.")


def open_folder_action(theme_name):
    dest = USER_BACKGROUNDS / theme_name
    return Action("Open folder", f"mkdir -p {q(dest)} && nautilus {q(dest)}",
                  steps=(("mkdir", dest), ("launch", ["nautilus", str(dest)])))


def font_actions(font=None, package=None, current_package=""):
    """`current_package` owns the font in use; removing it would pull that font out from under Omarchy."""
    if package:
        if package.installed:
            if package.package == current_package:
                return []
            return [Action("Remove", f"omarchy-pkg-remove {package.package}", password=PASSWORD_FONT)]
        fam = package.omarchy_pick or "<its family name>"
        return [Action("Add", f"omarchy-pkg-add {package.package}", password=PASSWORD_FONT),
                Action("Add and use", f"omarchy-install-font {q(fam)} {package.package} {q(fam)}",
                       password=PASSWORD_FONT)]
    acts = []
    if font and not font.current:
        acts.append(Action("Use", f"omarchy-font-set {q(font.family)}"))
    # Only Nerd Font packages are offered for removal: others (adwaita-fonts, ttf-liberation)
    # are pulled in by the system, and the font in use is never removable.
    if font and font.package != current_package and re.search(r"-nerd(-|$)", font.package):
        acts.append(Action("Remove", f"omarchy-pkg-remove {font.package}", password=PASSWORD_FONT))
    return acts


# --------------------------------------------------------------------------- export plan

@dataclass
class ExportItem:
    kind: str       # Theme | Background | Font
    name: str
    how: str        # "link" (fetched on import) or "bundle" (inside the zip)
    detail: str


def export_plan(theme, community, background, font, fonts_pkgs=None):
    """What a share-zip for the current setup would contain.

    Anything listed on omarchy.org (or in the Arch repos, for fonts) travels as
    the same link/package the stock installer uses; everything else is copied
    into the zip so the other person gets it even if they've never seen it.
    """
    items = []
    if theme:
        listed = next((c for c in community if repo_key(theme.repo_url) == c.key
                       or (not theme.builtin and c.install_name == theme.name)), None)
        if theme.builtin:
            items.append(ExportItem("Theme", theme.title, "link", "Built into Omarchy, nothing to bundle"))
        elif listed:
            items.append(ExportItem("Theme", theme.title, "link", f"{listed.repo_url}.git (omarchy.org list)"))
        else:
            items.append(ExportItem("Theme", theme.title, "bundle",
                                    f"Whole theme folder ({theme.repo_url or 'not on omarchy.org'})"))
    if background:
        if background.yours:
            items.append(ExportItem("Background", background.path.name, "bundle", "Your own image, copied in"))
        else:
            items.append(ExportItem("Background", background.path.name, "link",
                                    "Comes with the theme above, no copy needed"))
    if font:
        if font.package:
            items.append(ExportItem("Font", font.family, "link", f"Arch package {font.package}"))
        else:
            items.append(ExportItem("Font", font.family, "bundle", "Not from a package, font files copied in"))
    return items


def export_summary_json(items):
    """The manifest the zip would carry (shown in the prototype, never written)."""
    return json.dumps({"format": "omaskins-share/1",
                       "items": [{"kind": i.kind, "name": i.name, "how": i.how, "detail": i.detail} for i in items]},
                      indent=2)


# --------------------------------------------------------------------------- thumbnails

def thumbnail(src, width=480):
    """A small PNG of `src` in the cache (4K backgrounds would eat hundreds of MB as textures)."""
    src = Path(src)
    try:
        st = src.stat()
    except OSError:
        return None
    key = hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}|{width}".encode()).hexdigest()[:20]
    dest = _cache("thumbs", key + ".png")
    if dest.exists():
        return dest
    if not shutil.which("magick"):
        return src
    tmp = dest.with_suffix(".part.png")
    try:
        subprocess.run(["magick", f"{src}[0]", "-thumbnail", f"{width}x", "-strip", str(tmp)],
                       capture_output=True, timeout=30, check=True)
        tmp.replace(dest)
        return dest
    except (OSError, subprocess.SubprocessError):
        tmp.unlink(missing_ok=True)
        return src


# --------------------------------------------------------------------------- rotation (prototype: in memory only)

def minutes_text(m):
    """10 -> '10 min', 60 -> '1 h', 90 -> '1 h 30 min'."""
    h, m = divmod(int(m), 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m or not h else "") if p)


class RotationPlan:
    """What the Rotation tab has chosen. Nothing here runs or is saved yet.

    Two independent switches: `themes` rotates the checked themes, `backgrounds`
    rotates the bright (picked) backgrounds. Both off = no rotation. Order is
    always random. Themes on, backgrounds off = the current background stays.

    Picks are kept per mode, so switching Themes off never floods the
    background-only rotation with every background of every checked theme:
    - themes on: each checked theme has its own picks, all of its backgrounds
      the first time it's checked (unticking it later keeps them for next time);
    - themes off: one pool, from any theme, seeded with the current theme's.
    """

    PERIODS = ("All day", "Dawn", "Dusk")

    def __init__(self, current_theme=""):
        self.themes = False
        self.backgrounds = False
        self.bg_minutes, self.theme_minutes = 10, 60
        self.dawn_dusk, self.dawn, self.dusk = False, "07:00", "19:00"
        self.period = "Dawn"
        self.checked = {p: [] for p in self.PERIODS}   # theme names, in the order they were checked
        self.theme_picks = {}                          # theme name -> set of background paths
        self.solo_picks = None                         # set of paths; None = not seeded yet
        self.current_theme = current_theme
        if current_theme:
            self.checked["All day"].append(current_theme)

    def running(self):
        return self.themes or self.backgrounds

    def active_period(self):
        return self.period if self.dawn_dusk else "All day"

    def set_dawn_dusk(self, on):
        self.dawn_dusk = on
        if on and not self.checked["Dawn"] and not self.checked["Dusk"]:
            self.checked["Dawn"] = list(self.checked["All day"])

    def is_checked(self, name):
        return name in self.checked[self.active_period()]

    def set_checked(self, name, on, backgrounds=()):
        """Tick or untick a theme; `backgrounds` = its Background list, used to seed first-time picks."""
        names = self.checked[self.active_period()]
        if on and name not in names:
            names.append(name)
            if name not in self.theme_picks:
                self.theme_picks[name] = {b.path for b in backgrounds}
        elif not on and name in names:
            names.remove(name)

    def can_open(self, name):
        """Themes on: only checked themes open. Themes off: any theme does."""
        return not self.themes or self.is_checked(name)

    def _picks(self, name, backgrounds):
        if self.themes:
            return self.theme_picks.setdefault(name, {b.path for b in backgrounds})
        if self.solo_picks is None:
            self.solo_picks = set()
        return self.solo_picks

    def seed_solo(self, current_backgrounds):
        if self.solo_picks is None:
            self.solo_picks = {b.path for b in current_backgrounds}

    def is_picked(self, name, bgd, backgrounds=()):
        return bgd.path in self._picks(name, backgrounds)

    def toggle(self, name, bgd, backgrounds=()):
        picks = self._picks(name, backgrounds)
        picks.symmetric_difference_update({bgd.path})
        return bgd.path in picks

    def picked_count(self, name, backgrounds):
        paths = {b.path for b in backgrounds}
        return len(paths & self._picks(name, backgrounds))

    def manual_change(self, theme_changed, bg_changed):
        """Omarchy's own theme/background picker was used. It keeps working as normal; the matching
        rotation timers just start over so the pick gets a full turn. Omarchy's theme change always
        brings a new background too, so it restarts the background timer as well.
        Returns (timers restarted, message for the notification), or ([], "") if nothing is rotating."""
        resets = []
        if self.themes and theme_changed:
            resets.append(("theme", self.theme_minutes))
        if self.backgrounds and (bg_changed or theme_changed):
            resets.append(("background", self.bg_minutes))
        if not resets:
            return [], ""
        which = "timers" if len(resets) > 1 else "timer"
        return ([what for what, _m in resets],
                f"Rotation {which} reset: " + " and ".join(f"next {what} in {minutes_text(m)}" for what, m in resets) + ".")

    def in_rotation(self, name):
        return any(name in names for names in self.checked.values())

    def drop(self, name):
        """Take a theme out of every period (its picks are kept in case it comes back)."""
        for p in self.PERIODS:
            if name in self.checked[p]:
                self.checked[p].remove(name)

    def forget_missing(self, names):
        """Drop themes that are no longer installed."""
        for p in self.PERIODS:
            self.checked[p] = [n for n in self.checked[p] if n in names]
        self.theme_picks = {n: s for n, s in self.theme_picks.items() if n in names}


# --------------------------------------------------------------------------- global rounded corners
#
# One number rounds everything: Hyprland's decoration.rounding (px) shapes windows, and the Omarchy
# shell reads the same value (hyprctl getoption) for its menus, popups and bar. Load order is
# Omarchy defaults (0) -> current theme's hyprland.lua (Solitude sets 6) -> ~/.config/hypr files,
# so a value set in the user's files wins for every theme. OmaSkins keeps it in its own file,
# required from the end of looknfeel.lua: that is still before Omarchy's toggles, so the "no gaps"
# toggle can keep squaring corners. Switching off leaves the file with no override (the require
# must keep finding it), and each theme's own rounding comes back. The shell only re-reads the
# value at startup or on a theme change, hence the restart.

def current_rounding():
    """Hyprland's live decoration.rounding, or None when it can't be asked."""
    try:
        return int(json.loads(_run(["hyprctl", "-j", "getoption", "decoration:rounding"], 5) or "{}")["int"])
    except (ValueError, KeyError, TypeError):
        return None


def corners_setting():
    """(on, px) as saved in OmaSkins' file; (False, None) when it has no override."""
    try:
        m = re.search(r"rounding\s*=\s*(\d+)", CORNERS_FILE.read_text())
    except OSError:
        return False, None
    return (True, int(m.group(1))) if m else (False, None)


def corners_file_text(on, px):
    lines = ["-- Written by OmaSkins Manager: rounded corners for every theme."]
    if on:
        lines.append(f"hl.config({{ decoration = {{ rounding = {int(px)} }} }})")
    else:
        lines.append("-- Global Rounded Corners is off: each theme's own rounding applies.")
    return "\n".join(lines) + "\n"


def corners_action(on, px):
    px = max(0, min(CORNERS_MAX, int(px)))
    looknfeel = HYPR_DIR / "looknfeel.lua"
    text = corners_file_text(on, px).replace("'", "'\\''")
    cmd = (f"printf '%s' '{text}' > {q(CORNERS_FILE)} && "
           f"(grep -qxF {q(CORNERS_REQUIRE)} {q(looknfeel)} || echo {q(CORNERS_REQUIRE)} >> {q(looknfeel)}) && "
           "omarchy restart shell")
    return Action("Round corners" if on else "Square corners", cmd)

