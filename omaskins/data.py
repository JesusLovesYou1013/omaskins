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
# Downloads land here first and move into USER_THEMES in one step once complete, so neither this app
# nor Omarchy's own menu ever sees (or applies) a half-downloaded theme. Same filesystem = atomic move.
PARTIAL_THEMES = HOME / ".config/omarchy/.omaskins-partial"
REMOVING_THEMES = HOME / ".config/omarchy/.omaskins-removing"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "omarchy" / "current"
MENU_DEFAULTS = OMARCHY_PATH / "default/omarchy/omarchy-menu.jsonc"
HYPR_DIR = HOME / ".config/hypr"          # Omarchy's hyprland.lua requires "hypr.<name>" from here
# Built-in themes you hide are moved here (outside Omarchy's themes folder, which lists every
# subfolder), so Restore is an instant move back instead of a reinstall. Root-owned, like the themes.
HIDDEN_BUILTINS = Path(os.environ.get("OMASKINS_HIDDEN_THEMES", "/usr/local/share/omaskins/hidden-themes"))
PACMAN_CONF = Path(os.environ.get("OMASKINS_PACMAN_CONF", "/etc/pacman.conf"))
CORNERS_FILE = HYPR_DIR / "omaskins.lua"  # OmaSkins' own file: rounded corners for every theme
CORNERS_REQUIRE = 'require("hypr.omaskins")'
CORNERS_DEFAULT, CORNERS_MAX = 15, 40     # 15 = the owner's pick (2026-09-29): looks right on every theme
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "omaskins"
OMASKINS_STATE = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "omaskins"

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
    bar: int = -1         # >= 0: the busy toast carries an ASCII progress bar starting at this percent
    choices: tuple = ()   # set = a question first: ((button label, Action), ...), `note` is the question


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
    apply_as: str = ""         # folder Omarchy applies for it, when not its own (Aether's newer working copy)

    @property
    def title(self):
        return display_name(self.name)

    @property
    def aether(self):
        """Made with Aether (Omarchy's theme maker): it marks the theme folders it writes."""
        return not self.builtin and (self.path / ".aether-managed").is_file()


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


STARS_CACHE = CACHE_DIR / "stars.json"
STARS_TTL = 24 * 3600


PKGSTATS_CACHE = CACHE_DIR / "pkgstats.json"
PKGSTATS_TTL = 7 * 24 * 3600
PKGSTATS_API = "https://pkgstats.archlinux.de/api/packages"


def cached_font_popularity():
    """Last fetched {package: % of Arch users with it installed}, however old (instant, no network)."""
    try:
        return json.loads(PKGSTATS_CACHE.read_text()).get("popularity", {})
    except (OSError, ValueError):
        return {}


def font_popularity(force=False):
    """{package: popularity} from Arch's pkgstats (the share of Arch users who have it installed): the
    "Top Picks" order for fonts. All Nerd Fonts share one GitHub repo, so stars can't rank them. Paged
    over the ttf-/otf- names, cached for a week; {} offline (fonts then sort A-Z)."""
    try:
        cached = json.loads(PKGSTATS_CACHE.read_text())
        if not force and time.time() - cached.get("at", 0) < PKGSTATS_TTL:
            return cached["popularity"]
    except (OSError, ValueError, KeyError):
        pass
    popularity = {}
    for prefix in ("ttf-", "otf-"):
        offset = 0
        while True:
            try:
                with urllib.request.urlopen(f"{PKGSTATS_API}?query={prefix}&limit=250&offset={offset}",
                                            timeout=20) as r:
                    page = json.loads(r.read().decode())
            except (OSError, ValueError):
                break
            rows = page.get("packagePopularities") or []
            for row in rows:
                if isinstance(row.get("popularity"), (int, float)):
                    popularity[row["name"]] = row["popularity"]
            offset += len(rows)
            if not rows or offset >= int(page.get("total", 0)):
                break
    if popularity:
        try:
            PKGSTATS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            PKGSTATS_CACHE.write_text(json.dumps({"at": time.time(), "popularity": popularity}))
        except OSError:
            pass
    return popularity


# The owner's sort choice for Themes and Fonts ("top" = Top Picks, "az" = A -> Z), kept between launches.
UI_STATE = OMASKINS_STATE / "ui.json"


def sort_mode():
    try:
        mode = json.loads(UI_STATE.read_text()).get("sort")
    except (OSError, ValueError):
        mode = None
    return mode if mode in ("top", "az") else "top"


def save_sort_mode(mode):
    try:
        st = json.loads(UI_STATE.read_text())
    except (OSError, ValueError):
        st = {}
    st["sort"] = mode
    try:
        UI_STATE.parent.mkdir(parents=True, exist_ok=True)
        UI_STATE.write_text(json.dumps(st))
    except OSError:
        pass


def cached_theme_stars():
    """The last star counts fetched, however old (instant, no network): for sorting at startup."""
    try:
        return json.loads(STARS_CACHE.read_text()).get("stars", {})
    except (OSError, ValueError):
        return {}


def theme_stars(community, force=False):
    """{repo key: GitHub stars} for the omarchy.org themes: the only popularity measure there is (the
    page itself has none). One batched, read-only GraphQL query through the signed-in `gh`, cached for
    a day. Empty when gh or the network isn't there: Browse then sorts those themes A-Z."""
    keys = sorted({t.key for t in community if t.key and "/" in t.key})
    try:
        cached = json.loads(STARS_CACHE.read_text())
        if not force and time.time() - cached.get("at", 0) < STARS_TTL and set(keys) <= set(cached["stars"]):
            return cached["stars"]
    except (OSError, ValueError, KeyError):
        pass
    if not keys or not shutil.which("gh"):
        return {}
    stars = {}
    for start in range(0, len(keys), 100):
        chunk = keys[start:start + 100]
        parts = []
        for i, key in enumerate(chunk):
            owner, name = key.split("/", 1)
            parts.append(f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ stargazerCount }}")
        out = _run(["gh", "api", "graphql", "-f", "query=query { " + " ".join(parts) + " }"], 30)
        try:
            answered = json.loads(out or "{}").get("data") or {}
        except ValueError:
            answered = {}
        for alias, repo in answered.items():
            if repo and isinstance(repo.get("stargazerCount"), int):
                stars[chunk[int(alias[1:])]] = repo["stargazerCount"]
    if stars:
        try:
            STARS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            STARS_CACHE.write_text(json.dumps({"at": time.time(), "stars": stars}))
        except OSError:
            pass
    return stars


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
    """The current theme, as OmaSkins lists it: Aether's working copy counts as the theme it's a copy of."""
    try:
        name = (STATE_DIR / "theme.name").read_text().strip()
    except OSError:
        return ""
    if name == AETHER_SCRATCH:
        twin = aether_twin()
        return twin[0] if twin else name
    return name


# --------------------------------------------------------------------------- Aether's working copy
#
# Aether's plain Apply (and live apply while editing) always writes ONE fixed folder, themes/aether,
# whatever theme you're editing; only "Save and Apply" with a name writes your named theme folder. So
# an edited Aether theme exists twice: its named folder and the scratch `aether`. Aether copies your
# picture into both, so the same picture file ties them (Aether's blueprint, saved under the theme's
# name with the same colours, is the backup clue). OmaSkins shows them as ONE theme, under the name,
# always using the newer of the two (owner, 2026-10-01). Only one scratch folder exists, so however many
# Aether themes there are, at most one is "being edited". A scratch with a picture of its own isn't a
# copy of anything: it's a new unsaved theme and shows as "Aether".

AETHER_SCRATCH = "aether"
AETHER_BLUEPRINTS = HOME / ".config/aether/blueprints"
_DIGESTS = {}


def _picture_digests(folder):
    """Fingerprints of the pictures in a theme's backgrounds/ (cached by size and time)."""
    out = set()
    for p in _images(folder / "backgrounds"):
        try:
            st = p.stat()
        except OSError:
            continue
        key = (str(p), st.st_size, st.st_mtime_ns)
        if key not in _DIGESTS:
            try:
                _DIGESTS[key] = hashlib.sha1(p.read_bytes()).hexdigest()
            except OSError:
                continue
        out.add(_DIGESTS[key])
    return out


def _accent(path):
    return str(read_toml(path).get("accent", "")).lower()


def _hex_rgb(value):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(value).strip())
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else None


def _colour_closeness(a, b):
    """0..1: how alike two colors.toml palettes are (1 = identical), over the colours both have."""
    pairs = [(_hex_rgb(a[k]), _hex_rgb(b[k])) for k in a if k in b]
    pairs = [(x, y) for x, y in pairs if x and y]
    if not pairs:
        return 0.0
    dist = sum(sum((p - q) ** 2 for p, q in zip(x, y)) ** 0.5 for x, y in pairs) / len(pairs)
    return max(0.0, 1 - dist / 120)   # on average within ~120 (of 441) per colour: still "the same theme"


def aether_candidates():
    """Which named Aether theme Aether's working copy (themes/aether) is a copy of, best first:
    [{"name", "score", "sure", "newer", "why"}]. Only that one folder is ever compared, so pictures
    copied between your other themes never count. A shared picture alone is never proof (you may have
    copied it into another theme): sure = the same picture AND close colours, or Aether's blueprint of
    that name with exactly these colours; and only when no other theme fits as well."""
    scratch = USER_THEMES / AETHER_SCRATCH
    if not (scratch / ".aether-managed").is_file():
        return []
    try:
        named = [d for d in USER_THEMES.iterdir()
                 if d.is_dir() and d.name != AETHER_SCRATCH and (d / ".aether-managed").is_file()]
    except OSError:
        return []
    colours = read_toml(scratch / "colors.toml")
    pictures = _picture_digests(scratch)
    picture_names = {p.name for p in _images(scratch / "backgrounds")}
    out = []
    for d in named:
        why, score = [], 0.0
        same_picture = bool(pictures & _picture_digests(d))
        if same_picture:
            score += 40
            why.append("same picture")
        elif picture_names & {p.name for p in _images(d / "backgrounds")}:
            score += 15
            why.append("a picture with the same name")
        close = _colour_closeness(colours, read_toml(d / "colors.toml"))
        score += 50 * close
        blueprint_match = False
        try:
            bp = json.loads((AETHER_BLUEPRINTS / f"{d.name}.json").read_text())
            score += 5
            bp_accent = str(bp.get("palette", {}).get("extendedColors", {}).get("accent", "")).lower()
            blueprint_match = bool(bp_accent) and bp_accent == str(colours.get("accent", "")).lower()
            if blueprint_match:
                score += 80
                why.append("Aether's blueprint of that name has these colours")
        except (OSError, ValueError, AttributeError):
            pass
        if close >= 0.5:
            why.append("close colours")
        out.append({"name": d.name, "score": round(score, 1), "why": why,
                    "sure": blueprint_match or (same_picture and close >= 0.5),
                    "newer": _mtime(scratch / "colors.toml") > _mtime(d / "colors.toml")})
    out.sort(key=lambda c: -c["score"])
    if sum(c["sure"] for c in out) > 1:      # two themes fit: don't guess, ask
        for c in out:
            c["sure"] = False
    return out


def aether_twin():
    """(name, the working copy is newer) for the theme Aether's working copy is SURELY a copy of, else
    None (then it shows as its own theme, and OmaSkins asks at launch which theme it belongs to)."""
    best = next(iter(aether_candidates()), None)
    return (best["name"], best["newer"]) if best and best["sure"] else None


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def theme_folder_for(name):
    """The folder Omarchy should apply for a theme: Aether's working copy when it's that theme's newer copy."""
    twin = aether_twin()
    return AETHER_SCRATCH if twin and twin == (name, True) else name


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
    twin = aether_twin()
    if twin and twin[0] in themes:
        # Aether's working copy and the theme it's a copy of: one entry, under the name, the newer copy's look
        scratch = themes.pop(AETHER_SCRATCH, None)
        if twin[1] and scratch:
            t = themes[twin[0]]
            t.apply_as, t.preview, t.colors = AETHER_SCRATCH, scratch.preview, scratch.colors
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
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=child_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------- OmaSkins' own font config
#
# Omarchy's `omarchy-font-set` writes ~/.config/fontconfig/fonts.conf with "prepend the chosen font
# whenever `monospace` is asked for" (qual="any", binding strong). Fontconfig tags every known monospace
# family with `monospace`, so EVERY monospace font asked for by name comes out as the chosen one, in
# every app: OmaSkins drew every font name in the current font, and couldn't match sizes. OmaSkins (its
# own process only, set up by the launcher) uses the system's font config without that one user file,
# so each font draws as itself; Omarchy's files are untouched. Programs OmaSkins starts get the normal
# environment back (child_env), so they, the shell above all, keep the user's real font setup.

PRIVATE_FONTCONFIG = CACHE_DIR / "fontconfig" / "fonts.conf"
SYSTEM_FONTCONFIG = Path("/etc/fonts/fonts.conf")


def private_fontconfig():
    """Write OmaSkins' font config: /etc/fonts/fonts.conf with its conf.d listed file by file, minus
    50-user.conf's ~/.config/fontconfig/fonts.conf (your conf.d folder stays). None if it can't."""
    try:
        base = SYSTEM_FONTCONFIG.read_text()
    except OSError:
        return None
    marker = '<include ignore_missing="yes">conf.d</include>'
    confd = SYSTEM_FONTCONFIG.parent / "conf.d"
    if marker not in base or not confd.is_dir():
        return None
    parts = [f'\t<include ignore_missing="yes">{p}</include>' for p in sorted(confd.glob("*.conf"))
             if p.name != "50-user.conf"]
    parts.append('\t<include ignore_missing="yes" prefix="xdg">fontconfig/conf.d</include>')
    text = base.replace(marker, "<!-- OmaSkins: conf.d without 50-user.conf's fonts.conf (Omarchy's "
                                "every-monospace-font rule) -->\n" + "\n".join(parts))
    try:
        PRIVATE_FONTCONFIG.parent.mkdir(parents=True, exist_ok=True)
        tmp = PRIVATE_FONTCONFIG.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, PRIVATE_FONTCONFIG)
    except OSError:
        return None
    return PRIVATE_FONTCONFIG


def child_env():
    """The environment for programs OmaSkins starts: without OmaSkins' own font config."""
    env = dict(os.environ)
    if env.pop("OMASKINS_FONTCONFIG", None):
        env.pop("FONTCONFIG_FILE", None)
    return env


# --------------------------------------------------------------------------- font previews (Browse)
#
# A font you haven't installed is previewed from its own file: the package is downloaded once from the
# Arch mirrors (the same one `pacman` would install), only its regular font file is kept, and the
# package is deleted. The app loads that file privately (not installed, only OmaSkins sees it).

PREVIEW_FONTS = CACHE_DIR / "font-previews"
MAX_FONT_PACKAGE = 200 * 1024 * 1024


FONT_SIZES_CACHE = CACHE_DIR / "font-sizes.json"   # matched preview sizes (app.matched_size), per font set


def font_signature():
    """Changes when fonts are installed or removed (fontconfig's own cache folders), so remembered sizes
    are measured again then."""
    stamps = []
    for d in (Path("/var/cache/fontconfig"), HOME / ".cache/fontconfig", HOME / ".local/share/fonts"):
        try:
            stamps.append(d.stat().st_mtime_ns)
        except OSError:
            stamps.append(0)
    return stamps


def preview_font(package):
    """{"family", "file"} for a downloaded preview, or None."""
    try:
        meta = json.loads((PREVIEW_FONTS / f"{package}.json").read_text())
        return meta if Path(meta.get("file", "")).is_file() and meta.get("family") else None
    except (OSError, ValueError, AttributeError):
        return None


def _preview_file_in(names):
    """The package's plain regular face: <Name>NerdFont-Regular (not Mono / Propo), else any regular."""
    fonts = [n for n in names if n.lower().endswith((".ttf", ".otf"))]
    for pattern in (r"NerdFont-Regular\.(ttf|otf)$", r"-Regular\.(ttf|otf)$", r"Regular"):
        hit = [n for n in fonts if re.search(pattern, n) and not re.search(r"(Mono|Propo)-Regular", n)]
        hit = hit or [n for n in fonts if re.search(pattern, n)]
        if hit:
            return sorted(hit, key=len)[0]
    return fonts[0] if fonts else None


# A Browse preview only draws the font's own name (and OmaSkins measures its "x"), but the regular face of a
# Nerd Font is 2-14 MB, mostly icons and other scripts. So each one is slimmed to basic Latin (A-Z, a-z, 0-9,
# punctuation, "…") with HarfBuzz's subsetter (part of GTK/Pango, on every Omarchy install; no package, no
# password). Every table is kept and nothing else changes, so it draws exactly as before: checked
# pixel-for-pixel on 66 fonts, 5 sizes, 3 texts (2026-10-02; 221 MB -> 11 MB). The whole basic Latin set,
# not just the name's letters: FreeType's auto-hinter places lowercase/capital heights from reference
# letters ("o", "x", "H"...), and without them some fonts drew 1-2 px taller. A font HarfBuzz can't slim
# stays as it is.
PREVIEW_KEEP = "".join(chr(c) for c in range(0x20, 0x7f)) + "\u2026"
_HB = {}


def _harfbuzz():
    if "lib" not in _HB:
        import ctypes
        from ctypes import POINTER, c_char_p, c_int, c_uint, c_void_p
        try:
            hb = ctypes.CDLL("libharfbuzz-subset.so.0")
            for name, res, args in [
                    ("hb_blob_create_from_file_or_fail", c_void_p, [c_char_p]),
                    ("hb_face_create", c_void_p, [c_void_p, c_uint]),
                    ("hb_subset_input_create_or_fail", c_void_p, []),
                    ("hb_subset_input_unicode_set", c_void_p, [c_void_p]),
                    ("hb_subset_input_set", c_void_p, [c_void_p, c_int]),
                    ("hb_subset_input_set_flags", None, [c_void_p, c_uint]),
                    ("hb_set_add", None, [c_void_p, c_uint]),
                    ("hb_set_clear", None, [c_void_p]),
                    ("hb_set_invert", None, [c_void_p]),
                    ("hb_subset_or_fail", c_void_p, [c_void_p, c_void_p]),
                    ("hb_face_reference_blob", c_void_p, [c_void_p]),
                    ("hb_blob_get_data", POINTER(ctypes.c_char), [c_void_p, POINTER(c_uint)]),
                    ("hb_blob_destroy", None, [c_void_p]),
                    ("hb_face_destroy", None, [c_void_p]),
                    ("hb_subset_input_destroy", None, [c_void_p])]:
                f = getattr(hb, name)
                f.restype, f.argtypes = res, args
        except (OSError, AttributeError):
            hb = None
        _HB["lib"] = hb
    return _HB["lib"]


def slim_font(path, text):
    """`path` rewritten with only the characters in `text` (every table kept, glyph ids kept, unknown tables
    passed through). False (file untouched) if HarfBuzz isn't there or can't subset this font."""
    import ctypes
    hb = _harfbuzz()
    if not hb:
        return False
    blob = hb.hb_blob_create_from_file_or_fail(str(path).encode())
    if not blob:
        return False
    face = hb.hb_face_create(blob, 0)
    inp = hb.hb_subset_input_create_or_fail()
    try:
        if not inp:
            return False
        unicodes = hb.hb_subset_input_unicode_set(inp)
        for ch in set(text):
            hb.hb_set_add(unicodes, ord(ch))
        for which in (4, 5, 6, 7):   # every name id, name language, layout feature and script
            keep = hb.hb_subset_input_set(inp, which)
            hb.hb_set_clear(keep)
            hb.hb_set_invert(keep)
        hb.hb_set_clear(hb.hb_subset_input_set(inp, 3))   # drop no tables
        hb.hb_subset_input_set_flags(inp, 0x2 | 0x8 | 0x20 | 0x100)   # glyph ids, legacy names, unknown tables, ranges
        out = hb.hb_subset_or_fail(face, inp)
        if not out:
            return False
        out_blob = hb.hb_face_reference_blob(out)
        size = ctypes.c_uint()
        payload = ctypes.string_at(hb.hb_blob_get_data(out_blob, ctypes.byref(size)), size.value)
        hb.hb_blob_destroy(out_blob)
        hb.hb_face_destroy(out)
        if not payload:
            return False
        tmp = Path(path).with_name("." + Path(path).name + ".slim")
        tmp.write_bytes(payload)
        tmp.replace(path)
        return True
    finally:
        if inp:
            hb.hb_subset_input_destroy(inp)
        hb.hb_face_destroy(face)
        hb.hb_blob_destroy(blob)


def slim_preview_fonts():
    """Slim the previews downloaded before slimming existed (once each). Returns how many."""
    done = 0
    for meta_file in PREVIEW_FONTS.glob("*.json"):
        try:
            meta = json.loads(meta_file.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("slim") or not Path(meta.get("file", "")).is_file():
            continue
        meta["slim"] = bool(slim_font(meta["file"], meta.get("family", "") + PREVIEW_KEEP))
        meta_file.write_text(json.dumps(meta))
        done += meta["slim"]
    return done


def fetch_preview_font(package):
    """Download the package, keep its regular font file, delete the package. Returns preview_font()."""
    urls = [u for u in _run(["pacman", "-Sp", package], 30).splitlines() if "://" in u]
    if not urls:
        raise ValueError(f"no download link for {package}")
    PREVIEW_FONTS.mkdir(parents=True, exist_ok=True)
    tmp = PREVIEW_FONTS / f".{package}.pkg"
    try:
        req = urllib.request.Request(urls[-1], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as out:
            total = 0
            while chunk := r.read(1 << 20):
                total += len(chunk)
                if total > MAX_FONT_PACKAGE:
                    raise ValueError(f"{package} is larger than {MAX_FONT_PACKAGE >> 20} MB")
                out.write(chunk)
        name = _preview_file_in(_run(["bsdtar", "-tf", str(tmp)], 60).splitlines())
        if not name:
            raise ValueError(f"no font file in {package}")
        dest = PREVIEW_FONTS / Path(name).name
        with dest.open("wb") as out:
            if subprocess.run(["bsdtar", "-xOf", str(tmp), name], stdout=out, timeout=120,
                              env=child_env()).returncode != 0:
                raise ValueError(f"couldn't unpack {package}")
        family = _run(["fc-scan", "--format", "%{family[0]}", str(dest)], 10).strip()
        if not family:
            raise ValueError(f"unreadable font in {package}")
        slim = slim_font(dest, family + PREVIEW_KEEP)
        (PREVIEW_FONTS / f"{package}.json").write_text(json.dumps({"family": family, "file": str(dest), "slim": slim}))
    finally:
        tmp.unlink(missing_ok=True)
    return preview_font(package)


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


def package_families(packages):
    """{package: [font families it installed]}, any spacing: for installed font packages that have
    no monospace face (Omarchy's font menu, and the Installed list, only show monospace ones)."""
    if not packages:
        return {}
    owner = {}
    for line in _run(["pacman", "-Ql", *packages], 30).splitlines():
        pkg, _, path = line.partition(" ")
        if path and not path.endswith("/"):
            owner[path] = pkg
    out = {}
    for line in _run(["fc-list", "-f", "%{family[0]}|%{file}\n"]).splitlines():
        fam, _, path = line.partition("|")
        if path in owner and fam not in out.setdefault(owner[path], []):
            out[owner[path]].append(fam)
    return {p: sorted(f) for p, f in out.items()}


# Hidden from Browse: Omarchy's font menu only offers monospace fonts. Checked 2026-09-28 against
# the Arch file lists of all 76 Nerd Font packages: these three have no monospace family at all
# (the rest do, some alongside sans/serif ones), and the symbols packages are icon glyphs only.
# ttf-heavydata-nerd: measured 2026-09-28, its faces do not report fixed spacing either.
NOT_MONO_PACKAGES = {"ttf-arimo-nerd", "ttf-tinos-nerd", "ttf-ubuntu-nerd", "ttf-heavydata-nerd"}
LEARNED_NOT_MONO = CACHE_DIR / "not-monospace.txt"  # any other package found out after an install


def learned_not_mono():
    try:
        return {l.strip() for l in LEARNED_NOT_MONO.read_text().splitlines() if l.strip()}
    except OSError:
        return set()


def learn_not_mono(package):
    if package in NOT_MONO_PACKAGES or package in learned_not_mono():
        return
    try:
        LEARNED_NOT_MONO.parent.mkdir(parents=True, exist_ok=True)
        with LEARNED_NOT_MONO.open("a") as f:
            f.write(package + "\n")
    except OSError:
        pass


def browsable_font_package(package, learned=frozenset()):
    """False for packages that can never be Omarchy's font: no monospace face, or symbols only."""
    return not (package in NOT_MONO_PACKAGES or package in learned or "nerd-fonts-symbols" in package)


def without_icon_twins(fonts):
    """Drop each "<X> Nerd Font Mono" whose "<X> Nerd Font" is also there: same letters, only the
    icons are shrunk to one character cell, so to the owner it's a duplicate. A twin that is the
    font in use stays, so its ✓ still shows."""
    names = {f.family for f in fonts}
    return [f for f in fonts
            if f.current or not (f.family.endswith(" Nerd Font Mono") and f.family[:-5] in names)]


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


REPO_FONTS_CACHE = CACHE_DIR / "repo-fonts.json"


def _repo_font_search():
    """`pacman -Ss` for Nerd Font packages (~0.4 s), remembered until pacman's package lists change (a sync,
    an install or a removal), so a launch doesn't wait for it."""
    def stamp(d):
        try:
            return Path(d).stat().st_mtime_ns
        except OSError:
            return 0
    key = [stamp("/var/lib/pacman/sync"), stamp("/var/lib/pacman/local")]
    try:
        saved = json.loads(REPO_FONTS_CACHE.read_text())
        if saved.get("key") == key:
            return saved["out"]
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    out = _run(["pacman", "-Ss", r"^(ttf|otf)-.*nerd"], 30)
    if out:
        try:
            REPO_FONTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = REPO_FONTS_CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "out": out}))
            tmp.replace(REPO_FONTS_CACHE)
        except OSError:
            pass
    return out


def repo_fonts():
    """Nerd Font packages in the Arch repos: the same source Omarchy installs fonts from."""
    picks = omarchy_font_picks()
    out, pkg = [], None
    for line in _repo_font_search().splitlines():
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


# --------------------------------------------------------------------------- matched font sizes
#
# Faces at the same point size read very differently (Ubuntu Mono's lowercase is 16% shorter than
# JetBrains Mono's). When a font is set, OmaSkins scales Omarchy's font sizes so its x-height matches
# JetBrains Mono (Omarchy's default font) at your text size. The x-heights were measured ONCE
# (font_heights.py); a font that isn't in that table is measured the first time and remembered.

REFERENCE_X = 550            # JetBrains Mono: x-height in px at 1000 pt
REFERENCE_FAMILY = "JetBrainsMono Nerd Font"   # Omarchy's default font: every font is sized to match it
SCALE_MIN, SCALE_MAX = 0.8, 1.25
TERM_DEFAULT_PT, SHELL_DEFAULT_PX = 9, 12   # Omarchy's anchors: 12 px text size == 9 pt terminals
LEARNED_HEIGHTS = CACHE_DIR / "font-x-heights.json"
TEXT_SIZE_STATE = OMASKINS_STATE / "text-size.json"


def learned_heights():
    try:
        return json.loads(LEARNED_HEIGHTS.read_text())
    except (OSError, ValueError):
        return {}


def font_x_height(family):
    """From the table measured once; else measured now (render an 'x' with ImageMagick, as for
    thumbnails) and remembered, so each font is only ever measured once."""
    from .font_heights import X_HEIGHTS
    known = X_HEIGHTS.get(family) or learned_heights().get(family)
    if known:
        return known
    faces = [(l.split("|") + ["", ""])[:3]
             for l in _run(["fc-list", "-f", "%{family[0]}|%{style[0]}|%{file}\n"]).splitlines()]
    files = [f for fam, style, f in faces if fam == family and style in ("Regular", "Book")] \
        or [f for fam, _, f in faces if fam == family]
    if not files or not shutil.which("magick"):
        return None
    out = _run(["magick", "-background", "white", "-fill", "black", "-font", files[0], "-pointsize", "1000",
                "label:x", "-trim", "-format", "%h", "info:"]).strip()
    if not out.isdigit():
        return None
    learned = learned_heights()
    learned[family] = int(out)
    try:
        LEARNED_HEIGHTS.parent.mkdir(parents=True, exist_ok=True)
        LEARNED_HEIGHTS.write_text(json.dumps(learned, indent=1, sort_keys=True))
    except OSError:
        pass
    return int(out)


def font_scale(x_height):
    """How much to scale a face so its lowercase reads as big as JetBrains Mono's."""
    if not x_height:
        return 1.0
    return max(SCALE_MIN, min(SCALE_MAX, REFERENCE_X / x_height))


def sizes_for(text_size_px, scale):
    """(shell base-size px, terminal pt) for your text size and a font's scale. At scale 1 these are
    exactly what `omarchy display text size` sets (terminal pt rounded the same way)."""
    base_pt = int(text_size_px * TERM_DEFAULT_PT / SHELL_DEFAULT_PX + 0.5)
    pt = round(base_pt * scale, 1)
    return max(1, int(text_size_px * scale + 0.5)), (int(pt) if pt == int(pt) else pt)


# --------------------------------------------------------------------------- actions (described, never run)

def q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def theme_actions(local=None, community=None, current=""):
    acts = []
    if community and not local:
        acts.append(add_theme_action(community))
    if local:
        if local.name != current:
            folder = local.apply_as or local.name   # Aether's newer working copy, if that's what it is
            apply = Action("Apply", f"omarchy-theme-set {q(folder)}",
                           steps=(("run", ["omarchy-theme-set", folder]),),
                           busy=f"Applying {local.title}…", done=f"{local.title} applied.")
            if not local.builtin and not theme_has_files(local.path):
                # Applying now would stage an empty theme (it happened once, mid-download).
                apply.blocked = "Still downloading, or this theme folder is empty."
            acts.append(apply)
        if local.builtin:
            rm = builtin_hide_action(local.name, builtin_package_cached())
        else:
            rm = remove_theme_action(local)
        if local.name == current:
            rm.blocked = "This is your current theme. Switch to another one first."
        acts.append(rm)
    return acts


def theme_has_files(path):
    """False for a folder holding nothing but dot-entries (.git): a clone still in progress, or broken."""
    try:
        return any(not p.name.startswith(".") for p in Path(path).iterdir())
    except OSError:
        return False


def ascii_bar(percent, width=24):
    """Omarchy-style progress bar, always the same width: [#########...............]  37%"""
    percent = max(0, min(100, int(percent)))
    n = width * percent // 100
    return f"[{'#' * n}{'.' * (width - n)}] {percent:>3}%"


THEME_NAME_OK = re.compile(r"[a-z0-9_][a-z0-9._+-]*")  # omarchy-theme-install's own rule


def remove_theme_action(local):
    """What omarchy-theme-remove does (rm -rf the folder, then its "Theme removed" notification), but
    the folder first leaves themes/ in one rename, so neither this app nor Omarchy's menu ever sees
    a half-deleted theme, and is then deleted file by file so the bar can count down to 0%."""
    folder, gone = USER_THEMES / local.name, REMOVING_THEMES / local.name
    cmd = f"rm -rf ~/.config/omarchy/themes/{q(local.name)} && omarchy-notification-send 'Theme removed' {q(local.name)}"
    steps = [("take_out", folder, gone), ("delete_counting", gone)]
    twin = aether_twin()
    if twin and twin[0] == local.name:  # its Aether working copy goes too, or it would pop up on its own
        scratch, scratch_gone = USER_THEMES / AETHER_SCRATCH, REMOVING_THEMES / AETHER_SCRATCH
        steps = [("take_out", scratch, scratch_gone), ("delete_counting", scratch_gone)] + steps
        cmd = f"rm -rf ~/.config/omarchy/themes/{AETHER_SCRATCH} && " + cmd
    return Action("Remove", cmd, busy=f"Removing {local.title}…", bar=100,
                  done=f"{local.title} removed. You can add it again from Browse.",
                  steps=tuple(steps) + (("run", ["omarchy-notification-send", "Theme removed", local.name]),))


def merge_aether_action(named, picked=False):
    """Combine Aether's working copy into the named theme (run.merge_aether): asked for at launch.
    picked = you chose the theme when OmaSkins asked which one it belongs to."""
    title = display_name(named)
    return Action("Combine", f"(combine themes/{AETHER_SCRATCH} into themes/{named})",
                  busy=f"Combining the two {title} themes…",
                  done=f"Combined: one {title}, with the newer colours and every picture from both.",
                  steps=(("merge_aether", named, picked),))


def add_theme_action(community):
    """Download a theme the way omarchy-theme-install does (its URL check, then the same git clone
    into the same folder) but WITHOUT applying it: right-click Add through a list would otherwise
    switch the whole desktop for every theme."""
    url, name = community.repo_url, install_name_from_url(community.repo_url)
    dest, tmp = USER_THEMES / name, PARTIAL_THEMES / name
    cmd = f"omarchy-git-url-check {q(url)} && git clone -- {q(url)} {q(tmp)} && mv {q(tmp)} {q(dest)}"
    if not THEME_NAME_OK.fullmatch(name):
        return Action("Add", cmd, blocked="This repository's name doesn't make a usable theme folder name.")
    return Action("Add", cmd, busy=f"Adding {community.name}…", bar=0,
                  done=f"{community.name} added. Apply it whenever you like.",
                  steps=(("clear_partial", tmp), ("run", ["omarchy-git-url-check", url]),
                         ("run", ["git", "clone", "--progress", "--", url, str(tmp)]), ("move_in", tmp, dest)))


# The owner doesn't want password prompts for everyday adding/removing. The few actions that can't
# avoid one (they change system packages) always confirm first and say why.
PASSWORD_BUILTIN = ("Built-in themes are part of Omarchy's own system package, so removing or restoring one "
                    "entirely asks for your password. Hiding one from OmaSkins, and themes you added, never do.")
PASSWORD_FONT = ("Fonts are system packages, so adding or removing one asks for your password. "
                 "Switching between installed fonts never does.")

# Built-in themes belong to Omarchy's package, so omarchy-theme-remove won't touch them and an
# update would bring a deleted one back. Removing = delete the folder as root + a NoExtract rule
# (it must sit in pacman.conf's [options] section). Restoring = drop the rule, reinstall the package.

_BUILTIN_PKG = []


def builtin_package_cached():
    if not _BUILTIN_PKG:
        _BUILTIN_PKG.append(builtin_package())
    return _BUILTIN_PKG[0]


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
    """Built-ins the package ships whose folder isn't in Omarchy's themes folder (hidden or removed)."""
    return [n for n in shipped if not (BUILTIN_THEMES / n).is_dir()]


def builtin_held_aside(name):
    """True when a removed built-in's folder is held aside, so Restore can be an instant move back."""
    return (HIDDEN_BUILTINS / name).is_dir()


# Built-ins you hid from OmaSkins only (no password): still installed, still in Omarchy's own menu,
# still updated. Also the package version each built-in was removed at (for Restore).
BUILTIN_STATE = OMASKINS_STATE / "builtin-themes.json"


def _builtin_state():
    try:
        st = json.loads(BUILTIN_STATE.read_text())
    except (OSError, ValueError):
        st = {}
    return {"hidden": list(st.get("hidden", [])), "removed_at": dict(st.get("removed_at", {}))}


def _save_builtin_state(st):
    BUILTIN_STATE.parent.mkdir(parents=True, exist_ok=True)
    BUILTIN_STATE.write_text(json.dumps(st, indent=1, sort_keys=True))


def hidden_in_omaskins():
    return set(_builtin_state()["hidden"])


def set_hidden_in_omaskins(name, hidden):
    st = _builtin_state()
    names = set(st["hidden"])
    names.add(name) if hidden else names.discard(name)
    st["hidden"] = sorted(names)
    _save_builtin_state(st)


def package_version(package):
    return (_run(["pacman", "-Q", package], 10).split() + ["", ""])[1] if package else ""


def note_removed_version(name, package):
    st = _builtin_state()
    st["removed_at"][name] = package_version(package)
    _save_builtin_state(st)


def forget_removed_version(name):
    """A restored built-in isn't removed any more: drop its "removed at" entry (owner, 2026-10-02)."""
    st = _builtin_state()
    if st["removed_at"].pop(name, None) is not None:
        _save_builtin_state(st)


def removed_at_version(name):
    return _builtin_state()["removed_at"].get(name, "")


NAME_OK = re.compile(r"[a-z0-9][a-z0-9._-]*")
PKG_OK = re.compile(r"[a-z0-9][a-z0-9._+-]*")


def _no_extract(name):
    if not NAME_OK.fullmatch(name):
        raise ValueError(f"unexpected theme folder name: {name!r}")
    return f"NoExtract = {str(BUILTIN_THEMES / name).lstrip('/')}/*"


def builtin_terminal_command(kind, name, package=""):
    """What runs in Omarchy's floating terminal (sudo asks for the password there). Built only from a
    checked folder name / package name and fixed paths; run.py rebuilds it and must get the same text.
    hide: move the folder aside + a NoExtract rule so package updates don't bring it back.
    unhide: move it back + drop the rule. reinstall (no hidden copy): drop the rule + pacman -S."""
    rule, themes, hidden, conf = _no_extract(name), BUILTIN_THEMES, HIDDEN_BUILTINS, PACMAN_CONF
    drop_rule = f"sudo sed -i '\\|^{rule.replace('*', '[*]')}$|d' {conf}"
    if kind == "hide":
        return (f"echo 'Removing {name}...'; [ ! -e {hidden}/{name} ] && sudo mkdir -p {hidden} && "
                f"sudo mv {themes}/{name} {hidden}/{name} && "
                f"(grep -qxF '{rule}' {conf} || sudo sed -i '/^\\[options\\]/a {rule}' {conf})")
    if kind == "unhide":
        return (f"echo 'Restoring {name}...'; [ ! -e {themes}/{name} ] && "
                f"sudo mv {hidden}/{name} {themes}/{name} && {drop_rule}")
    if kind == "reinstall":
        if not PKG_OK.fullmatch(package):
            raise ValueError(f"unexpected package name: {package!r}")
        return (f"echo 'Reinstalling {name}...'; {drop_rule} && sudo pacman -S --noconfirm {package} && "
                f"sudo rm -rf {hidden}/{name}")
    raise ValueError(kind)


def builtin_hide_action(name, package=""):
    """A built-in's Hide button asks first (owner's design): hide it from OmaSkins only (no password,
    it stays installed and updated), or remove it entirely (password; gone from Omarchy's menu too)."""
    if not NAME_OK.fullmatch(name):
        raise ValueError(f"unexpected theme folder name: {name!r}")
    title = display_name(name)
    soft = Action("Hide from OmaSkins", f"(OmaSkins' own list: {BUILTIN_STATE})",
                  done=f"{title} hidden from OmaSkins. Restore it from Browse whenever you like.",
                  steps=(("hide_in_omaskins", name, True),))
    cmd = builtin_terminal_command("hide", name)
    hard = Action("Remove entirely", f"sudo mv {BUILTIN_THEMES / name} {HIDDEN_BUILTINS / name}  (+ NoExtract in pacman.conf)",
                  password=PASSWORD_BUILTIN, busy=f"Removing {title}: type your password in the terminal…",
                  done=f"{title} removed. Restore it from Browse whenever you like.",
                  steps=(("terminal", cmd), ("wait_builtin", name, True, cmd), ("note_removed_version", name, package)))
    return Action("Hide", f"hide {name}",
                  note=(f"Do you want to hide this stock theme from OmaSkins, or remove it entirely?\n\n"
                        f"Hide from OmaSkins: no password. It stays installed, keeps getting Omarchy's updates, "
                        f"and still shows in Omarchy's own theme menu.\n\n"
                        f"Remove entirely: asks for your password (it's part of Omarchy's system package). "
                        f"It's gone from Omarchy's menu too, and updates won't bring it back.\n\n"
                        f"Either way it stays under Browse, and Restore brings it back."),
                  choices=(("Hide from OmaSkins", soft), ("Remove entirely", hard)))


def builtin_restore_action(name, package, hidden_only=False):
    title = display_name(name)
    if hidden_only:
        return Action("Restore", f"(OmaSkins' own list: {BUILTIN_STATE})", done=f"{title} is back.",
                      steps=(("hide_in_omaskins", name, False),))
    current = package_version(package)
    if builtin_held_aside(name) and current and current == removed_at_version(name):
        cmd = builtin_terminal_command("unhide", name)  # nothing changed since: the kept copy is current
        shown = f"sudo mv {HIDDEN_BUILTINS / name} {BUILTIN_THEMES / name}  (- NoExtract in pacman.conf)"
    else:  # Omarchy was updated meanwhile (or no copy was kept): reinstall for the fresh version
        cmd = builtin_terminal_command("reinstall", name, package)
        shown = f"sudo pacman -S {package}  (- NoExtract in pacman.conf; fresh copy, Omarchy updated it)"
    return Action("Restore", shown, password=PASSWORD_BUILTIN,
                  busy=f"Restoring {title}: type your password in the terminal…", done=f"{title} is back.",
                  steps=(("terminal", cmd), ("wait_builtin", name, False, cmd), ("forget_removed_version", name)))


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


FONT_PKG_OK = re.compile(r"(ttf|otf)-[a-z0-9][a-z0-9.+-]*nerd[a-z0-9.+-]*")  # nothing a shell reads specially


def font_terminal_command(verb, package):
    """What runs in Omarchy's floating terminal, where pacman asks for the password (the same
    presentation omarchy-install-font uses). `verb` is "add" or "drop"."""
    doing = {"add": "Installing", "drop": "Removing"}[verb]
    return f"echo '{doing} {package}...'; omarchy-pkg-{verb} {package}"


OMARCHY_DEFAULT_FONT_PKG = "ttf-jetbrains-mono-nerd"
# The password terminal tells OmaSkins itself that it started (its process id) and how it ended ("done" or
# "cancelled"), instead of OmaSkins having to find its process (2026-10-02: that wait once gave up 15 s in
# and reported a removal that then happened as not done).
PASSWORD_MARK = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "omaskins-password"   # Omarchy's own default: an import never removes it

_IMPORT_KEYS = ("add-fonts", "drop-fonts", "remove-themes", "unhide-themes", "reinstall-themes")


def fonts_in_use():
    """Every font family set up to be used: Omarchy's (fontconfig) and each terminal's own setting. An
    import never offers to remove a package holding one of these (2026-10-02: removing UbuntuMono while
    the terminals still named it scrambled Omarchy's logo in its own terminal)."""
    out = {current_font()}
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
    pats = (("ghostty/config", r'^font-family\s*=\s*"?([^"\n]+?)"?\s*$'),
            ("alacritty/alacritty.toml", r'family\s*=\s*"([^"]+)"'),
            ("kitty/kitty.conf", r"^font_family\s+(.+?)\s*$"),
            ("foot/foot.ini", r"^font=([^:\n]+)"))
    for rel, pat in pats:
        try:
            out.update(re.findall(pat, (cfg / rel).read_text(), flags=re.M))
        except OSError:
            pass
    return {f.strip() for f in out if f and f.strip()}


def password_terminal_command(add_fonts=(), drop_fonts=(), remove_themes=(), unhide_themes=(),
                              reinstall_themes=(), package=""):
    """Everything an import needs the password for, in ONE terminal (owner, 2026-10-02: one password for
    all of it, as the two fonts earlier): fonts added/removed and built-ins removed/restored to match the
    file. sudo asks once at the start (sudo -v) and is kept fresh while the work runs, so a long download
    can't make it ask again. The first line names every item, so run.py can rebuild the whole command
    from that list and accept it only if the text comes out identical."""
    lists = [sorted(set(x)) for x in (add_fonts, drop_fonts, remove_themes, unhide_themes, reinstall_themes)]
    for name in lists[0] + lists[1]:
        if not FONT_PKG_OK.fullmatch(name):
            raise ValueError(f"unexpected font package: {name!r}")
    for name in lists[2] + lists[3] + lists[4]:
        if not NAME_OK.fullmatch(name):
            raise ValueError(f"unexpected theme folder name: {name!r}")
    if not any(lists):
        raise ValueError("nothing to do")
    parts = []
    if lists[0]:
        parts.append(font_terminal_command("add", " ".join(lists[0])))
    if lists[1]:
        parts.append(font_terminal_command("drop", " ".join(lists[1])))
    parts += [builtin_terminal_command("hide", n) for n in lists[2]]
    parts += [builtin_terminal_command("unhide", n) for n in lists[3]]
    parts += [builtin_terminal_command("reinstall", n, package) for n in lists[4]]
    head = " ".join(f"{k}={','.join(v)}" for k, v in zip(_IMPORT_KEYS, lists))
    say = ([f"Install font: {p}" for p in lists[0]] + [f"Remove font: {p}" for p in lists[1]]
           + [f"Remove built-in theme: {display_name(n)}" for n in lists[2]]
           + [f"Restore built-in theme: {display_name(n)}" for n in lists[3] + lists[4]])
    # Line 1 is for OmaSkins' safety check only (":" does nothing); what you see is plain words, and
    # Ctrl+C at the password closes the window with nothing changed (exit 130: Omarchy's wrapper then
    # skips its "Done" screen).
    return (f": 'OmaSkins import: {head}'; echo 'OmaSkins: one password covers all of this:'; "
            + "; ".join(f"echo '  - {x}'" for x in say)
            + "; echo; echo 'Press Ctrl+C to cancel. Nothing will be changed.'; echo; "
            f"echo $$ > {PASSWORD_MARK}.started; "
            f"sudo -v || {{ echo 'Cancelled: nothing was changed.'; echo cancelled > {PASSWORD_MARK}.done; exit 130; }}; "
            f"(while sleep 50; do sudo -n -v; done) & keep=$!; "
            + "; ".join(f"( {p} )" for p in parts) + f"; kill $keep; echo done > {PASSWORD_MARK}.done")


def parse_password_terminal_head(cmd):
    """The item lists from a password_terminal_command's first line, or None."""
    m = re.match(r": 'OmaSkins import: (\S+(?: \S+){4})'; ", cmd)
    if not m:
        return None
    out = {}
    for pair in m.group(1).split(" "):
        k, _, v = pair.partition("=")
        out[k] = [x for x in v.split(",") if x]
    return out if tuple(out) == _IMPORT_KEYS else None


def font_actions(font=None, package=None, current_package=""):
    """`current_package` owns the font in use; removing it would pull that font out from under Omarchy.
    Package changes happen in Omarchy's floating terminal (pacman asks for the password there); OmaSkins
    waits for pacman to finish, then refreshes."""
    if package:
        pkg = package.package
        if not FONT_PKG_OK.fullmatch(pkg):
            return []
        if package.installed:
            if pkg == current_package:
                return []
            return [_remove_font_package(pkg)]
        term = font_terminal_command("add", pkg)
        wait = ("wait_package", pkg, True, term)
        fam = package.omarchy_pick
        return [Action("Add", f"omarchy-pkg-add {pkg}   (in a terminal)", password=PASSWORD_FONT,
                       busy=f"Installing {pkg}: type your password in the terminal…", done=f"{pkg} added.",
                       steps=(("terminal", term), wait, ("check_mono", pkg))),
                Action("Add and use", f"omarchy-pkg-add {pkg}   (in a terminal), then "
                                      f"omarchy-font-set {q(fam or '<its font>')}", password=PASSWORD_FONT,
                       busy=f"Installing {pkg}: type your password in the terminal…",
                       done=f"{pkg} added and in use.",
                       steps=(("terminal", term), wait, ("remember_text_size",), ("use_font", pkg, fam)))]
    acts = []
    if font and not font.current:
        acts.append(Action("Use", f"omarchy-font-set {q(font.family)}, then match its size",
                           busy=f"Switching to {font.family}…", done=f"{font.family} is your font now.",
                           steps=(("remember_text_size",), ("run", ["omarchy-font-set", font.family]),
                                  ("font_size", font.family))))
    # Only Nerd Font packages are offered for removal: others (adwaita-fonts, ttf-liberation)
    # are pulled in by the system, and the font in use is never removable.
    if font and font.package != current_package and re.search(r"-nerd(-|$)", font.package) \
            and FONT_PKG_OK.fullmatch(font.package):
        acts.append(_remove_font_package(font.package))
    return acts


def _remove_font_package(pkg):
    term = font_terminal_command("drop", pkg)
    return Action("Remove", f"omarchy-pkg-drop {pkg}   (in a terminal)", password=PASSWORD_FONT,
                  busy=f"Removing {pkg}: type your password in the terminal…", done=f"{pkg} removed.",
                  steps=(("terminal", term), ("wait_package", pkg, False, term)))


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
                       capture_output=True, timeout=30, check=True, env=child_env())
        tmp.replace(dest)
        return dest
    except (OSError, subprocess.SubprocessError):
        tmp.unlink(missing_ok=True)
        return src


# --------------------------------------------------------------------------- rotation (prototype: in memory only)

# Dawn & Dusk follow the sun where you are: sunrise to sunset is Dawn's theme set, sunset to sunrise
# Dusk's. Where you are = the weather widget's location (Omarchy's own setting, with coordinates), else
# your time zone's reference city (tzdata), else 07:00 / 19:00. Worked out ONCE PER BOOT (owner's rule:
# a laptop may change time zones between boots, not while it's up) and kept in SUN_STATE; no network.
WEATHER_SETTINGS = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "omarchy/settings/weather.json"
ZONEINFO = Path("/usr/share/zoneinfo")
SUN_STATE = OMASKINS_STATE / "sun.json"


def _iso6709(text):
    """'+404251-0740023' -> (40.714, -74.006)."""
    m = re.fullmatch(r"([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?", text)
    if not m:
        return None
    lat = int(m[2]) + int(m[3]) / 60 + int(m[4] or 0) / 3600
    lon = int(m[6]) + int(m[7]) / 60 + int(m[8] or 0) / 3600
    return (lat if m[1] == "+" else -lat, lon if m[5] == "+" else -lon)


def home_location():
    """(latitude, longitude, where it came from), or None."""
    try:
        w = json.loads(WEATHER_SETTINGS.read_text())
        if isinstance(w.get("latitude"), (int, float)) and isinstance(w.get("longitude"), (int, float)):
            return w["latitude"], w["longitude"], f"{w.get('name') or 'your location'}, from the weather widget"
    except (OSError, ValueError, AttributeError):
        pass
    try:
        zone = os.path.realpath("/etc/localtime").split("/zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return None
    try:  # an old-style name (US/Eastern) is a link to the real zone (America/New_York)
        for line in (ZONEINFO / "tzdata.zi").read_text().splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == "L" and parts[2] == zone:
                zone = parts[1]
                break
    except OSError:
        pass
    for table in ("zone1970.tab", "zone.tab"):
        try:
            for line in (ZONEINFO / table).read_text().splitlines():
                parts = line.split("\t")
                if not line.startswith("#") and len(parts) >= 3 and parts[2] == zone:
                    coords = _iso6709(parts[1])
                    if coords:
                        return coords[0], coords[1], f"{zone.split('/')[-1].replace('_', ' ')}, from your time zone"
        except OSError:
            continue
    return None


def sun_times(lat, lon, year, month, day, utc_offset_min):
    """Sunrise and sunset as local minutes of the day (NOAA's approximation, within a couple of
    minutes). None where the sun doesn't rise or set that day."""
    import math
    doy = time.localtime(time.mktime((year, month, day, 12, 0, 0, 0, 0, -1))).tm_yday
    g = 2 * math.pi / 365 * (doy - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(decl)) - math.tan(la) * math.tan(decl)
    if not -1 <= cos_ha <= 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    return (round(720 - 4 * (lon + ha) - eqtime + utc_offset_min),
            round(720 - 4 * (lon - ha) - eqtime + utc_offset_min))


def _boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


_SUN = {}


def sun_times_now():
    """{"rise", "set" (local minutes), "where"}: worked out on the first call after a boot, then read
    back from SUN_STATE until the next boot."""
    boot = _boot_id()
    if _SUN.get("boot") == boot:
        return _SUN
    try:
        saved = json.loads(SUN_STATE.read_text())
        if saved.get("boot") == boot and {"rise", "set", "where"} <= saved.keys():
            _SUN.clear()
            _SUN.update(saved)
            return _SUN
    except (OSError, ValueError, AttributeError):
        pass
    t = time.localtime()
    loc = home_location()
    times = loc and sun_times(loc[0], loc[1], t.tm_year, t.tm_mon, t.tm_mday, t.tm_gmtoff // 60)
    rise, down = times or (7 * 60, 19 * 60)
    _SUN.clear()
    _SUN.update(boot=boot, rise=rise, set=down, where=loc[2] if times else "07:00 / 19:00 (location unknown)")
    try:
        SUN_STATE.parent.mkdir(parents=True, exist_ok=True)
        SUN_STATE.write_text(json.dumps(_SUN))
    except OSError:
        pass
    return _SUN


# Rotation runs on the clock, not a timer: changes land when the time of day is a multiple of the
# interval, counted from midnight (20 min = :00, :20, :40). Only intervals that divide the hour or
# the day evenly, so every slot lines up. The − / + buttons step through exactly these.
INTERVALS = (5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440)


def snap_interval(minutes):
    """The allowed interval nearest above (a saved 1 min becomes 5, 25 becomes 30)."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return 60
    return next((i for i in INTERVALS if i >= m), INTERVALS[-1])


def interval_text(minutes):
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h"


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
        self.minutes = 60                  # one interval for both switches (see INTERVALS)
        self.dawn_dusk = False             # Dawn = sunrise to sunset where you are (see sun_times_now)
        self.mix = False                   # "Mix it up!": any theme with any bright background (mixed_pool)
        self.set_name = ""                 # the saved Rotation set this setup came from or was saved as
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

    def period_now(self, t):
        """Which theme set the clock calls for (t = time.struct_time): "All day", or Dawn (sunrise to
        sunset) / Dusk (sunset to sunrise)."""
        if not self.dawn_dusk:
            return "All day"
        sun = sun_times_now()
        return "Dawn" if sun["rise"] <= t.tm_hour * 60 + t.tm_min < sun["set"] else "Dusk"

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

    def pool(self, current_theme):
        """The backgrounds that take turns now: the current theme's picks while themes rotate, else the
        backgrounds-only pool. Only files that still exist."""
        if not self.themes:
            paths = self.solo_picks or set()
        elif current_theme in self.theme_picks:
            paths = self.theme_picks[current_theme]
        else:
            # Never opened on the Rotation tab: all its backgrounds are bright, as the tab shows them
            # (a checked theme starts with all of them). Found directly, without listing every theme.
            folder = next((r / current_theme for r in (USER_THEMES, BUILTIN_THEMES) if (r / current_theme).is_dir()),
                          None)
            paths = ({b.path for b in backgrounds_for(LocalTheme(current_theme, folder, folder.parent == BUILTIN_THEMES))}
                     if folder else set())
        return sorted(str(p) for p in paths if Path(p).is_file())

    def mixed_pool(self, theme_names):
        """Mix it up!: every bright background of every theme in the list, as one pool."""
        return sorted({p for name in theme_names for p in self.pool(name)})

    def to_dict(self):
        return {"version": 1, "themes": self.themes, "backgrounds": self.backgrounds,
                "minutes": self.minutes,
                "dawn_dusk": self.dawn_dusk, "mix": self.mix, "period": self.period, "set_name": self.set_name,
                "checked": self.checked,
                "theme_picks": {n: sorted(str(p) for p in s) for n, s in self.theme_picks.items()},
                "solo_picks": None if self.solo_picks is None else sorted(str(p) for p in self.solo_picks)}

    @classmethod
    def from_dict(cls, d, current_theme=""):
        plan = cls(current_theme)
        for key in ("themes", "backgrounds", "dawn_dusk", "mix"):
            setattr(plan, key, bool(d.get(key, getattr(plan, key))))
        # One interval; settings saved before it (a timer per switch) keep the one that was in use.
        old = d.get("theme_minutes") if plan.themes or not plan.backgrounds else d.get("bg_minutes")
        plan.minutes = snap_interval(d.get("minutes", old if old is not None else plan.minutes))
        if isinstance(d.get("set_name"), str):
            plan.set_name = d["set_name"]
        if d.get("period") in ("Dawn", "Dusk"):
            plan.period = d["period"]
        checked = d.get("checked")
        if isinstance(checked, dict):
            plan.checked = {p: [n for n in checked.get(p, []) if isinstance(n, str)] for p in cls.PERIODS}
        picks = d.get("theme_picks")
        if isinstance(picks, dict):
            plan.theme_picks = {n: {Path(p) for p in ps} for n, ps in picks.items() if isinstance(ps, list)}
        if isinstance(d.get("solo_picks"), list):
            plan.solo_picks = {Path(p) for p in d["solo_picks"]}
        return plan


# The Rotation tab's settings, read by the rotation engine (omaskins-rotate) that keeps running with
# OmaSkins closed. The engine's own bookkeeping (timers, what was shown) lives in ROTATION_STATE.
ROTATION_FILE = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "omaskins" / "rotation.json"
ROTATION_STATE = OMASKINS_STATE / "rotation-state.json"
# What the bar's palette menu paused: "theme", "background", both or nothing, on one line. Its own small
# file (not rotation.json, your settings, nor the engine's state): the menu's command writes it, the engine
# and the menu read it. It stays across restarts until you resume.
ROTATION_PAUSED = OMASKINS_STATE / "rotation-paused"
PAUSABLE = ("theme", "background")


def rotation_paused():
    try:
        return {w for w in ROTATION_PAUSED.read_text().split() if w in PAUSABLE}
    except OSError:
        return set()


def set_rotation_paused(what, paused):
    """Pause or resume one of the two; the other is left as it is. Returns what is paused now."""
    if what not in PAUSABLE:
        return None
    now = rotation_paused()
    now.add(what) if paused else now.discard(what)
    ROTATION_PAUSED.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROTATION_PAUSED.with_name(".rotation-paused.tmp")
    tmp.write_text(" ".join(w for w in PAUSABLE if w in now) + "\n")
    tmp.replace(ROTATION_PAUSED)
    return now


def load_rotation(current_theme=""):
    try:
        return RotationPlan.from_dict(json.loads(ROTATION_FILE.read_text()), current_theme)
    except (OSError, ValueError, AttributeError):
        return RotationPlan(current_theme)


def save_rotation(plan):
    """Atomic, so the engine never reads half a file."""
    ROTATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROTATION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan.to_dict(), indent=1, sort_keys=True))
    os.replace(tmp, ROTATION_FILE)


# Saved Rotation sets (owner, 2026-10-01): whole Rotation setups under a name, to switch between
# without re-ticking everything. One file, so Share / backup can carry them.
ROTATION_SETS = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "omaskins" / "rotation-sets.json"
SET_NAME_MAX = 40


def _setup(plan):
    """What a set holds: everything on the Rotation tab, without the set's own name."""
    d = plan.to_dict()
    d.pop("set_name", None)
    return d


def rotation_sets():
    """{name: setup}, in the order they were saved."""
    try:
        sets = json.loads(ROTATION_SETS.read_text())
        return {k: v for k, v in sets.items() if isinstance(k, str) and isinstance(v, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def _write_sets(sets):
    ROTATION_SETS.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROTATION_SETS.with_suffix(".tmp")
    tmp.write_text(json.dumps(sets, indent=1))
    os.replace(tmp, ROTATION_SETS)


def clean_set_name(name):
    return " ".join(str(name).split())[:SET_NAME_MAX]


def save_rotation_set(name, plan):
    """Store the current setup as `name` (new, or replacing that set); it becomes the current set."""
    name = clean_set_name(name)
    if not name:
        raise ValueError("a set needs a name")
    sets = rotation_sets()
    sets[name] = _setup(plan)
    _write_sets(sets)
    plan.set_name = name
    save_rotation(plan)


def use_rotation_set(name, current_theme=""):
    """Switch the whole Rotation setup to a saved set; returns the new plan (also saved as current)."""
    setup = rotation_sets().get(name)
    if setup is None:
        raise ValueError(f"no saved set called {name!r}")
    plan = RotationPlan.from_dict(setup, current_theme)
    plan.set_name = name
    save_rotation(plan)
    return plan


def delete_rotation_set(name):
    sets = rotation_sets()
    if sets.pop(name, None) is not None:
        _write_sets(sets)


def set_has_changes(plan):
    """True when the setup differs from its saved set (or it isn't a saved set yet)."""
    saved = rotation_sets().get(plan.set_name)
    return saved is None or json.dumps(saved, sort_keys=True) != json.dumps(_setup(plan), sort_keys=True)


def rotation_status():
    """What the engine last wrote about itself, or {} if it has never run."""
    try:
        st = json.loads(ROTATION_STATE.read_text())
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


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
        lines.append("-- Rounded Corners is off: each theme's own rounding applies.")
    return "\n".join(lines) + "\n" + TRANSPARENCY_LUA + QT_LUA


# --------------------------------------------------------------------------- transparency
#
# Five steps (owner, 2026-10-02): off, Omarchy's own default, then three stronger ones; unfocused windows a
# bit more see-through than the focused one. Only the windows Omarchy itself makes see-through (its
# "default-opacity" tag); apps it keeps solid stay solid. Step 2 = exactly Omarchy's (and each theme's) own
# values: OmaSkins sets nothing. Blur behind from step 3 up (owner, 2026-10-02), so text stays easy to read.
# OmaSkins itself is left out of Hyprland's whole-window fade: it makes only its own background see-through
# (same values), so theme and background previews, text and buttons stay solid.
# The step lives in ~/.config/omaskins/transparency, which omaskins.lua reads when Hyprland loads; changing
# it never makes Hyprland reload (in this VM a reload blanks the screen), OmaSkins fades it in live.
TRANSPARENCY_FILE = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "omaskins" / "transparency"
OMARCHY_OPACITY = (0.985, 0.96)
TRANSPARENCY_DEFAULT = 1
# Steps 3-5 one notch stronger than first tried (owner, 2026-10-02: the blur made it less obvious), then 10
# points apart instead of 5 (owner, 2026-10-02: step 4 = the old step 5, which is what they use).
TRANSPARENCY_STEPS = ((1.0, 1.0, False), None, (0.85, 0.80, True), (0.75, 0.68, True), (0.65, 0.56, True))
TRANSPARENCY_BLUR = "hl.config({ decoration = { blur = { enabled = true, size = 5, passes = 2 } } })"
# Omarchy's own menus, panels (the bar's dropdowns) and notifications: the shell draws them itself, so
# Hyprland's window fade never reaches them. The shell has its own setting for each (`background-alpha`,
# solid by default), read live from your ~/.config/omarchy/shell.toml, where Omarchy's text size also lives
# (its command edits only its own line). OmaSkins keeps its values there in a marked block, at the focused
# level of the step; none at the default step. Hyprland blurs behind them by the names Omarchy gives them.
SHELL_TOML = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "omarchy" / "shell.toml"
SHELL_START = "# >>> OmaSkins: menus, panels and notifications see-through at the Transparency step (set it in OmaSkins)"
SHELL_END = "# <<< OmaSkins"
SHELL_SURFACES = ("menu", "popups", "notifications")
SHELL_NAMESPACES = "^(omarchy-menu|omarchy-keyboard-panel|omarchy-notifications|omarchy-clipboard|omarchy-emojis)$"
SHELL_BLUR_RULE = f'hl.layer_rule({{ match = {{ namespace = "{SHELL_NAMESPACES}" }}, blur = true, ignore_alpha = 0.05 }})'


def shell_block(step):
    if step == TRANSPARENCY_DEFAULT:
        return ""
    a = transparency_values(step)[0]
    return "\n".join([SHELL_START] + [f"[{s}]\nbackground-alpha = {a:g}\n" for s in SHELL_SURFACES] + [SHELL_END]) + "\n"


def write_shell_block(step):
    """OmaSkins' block in ~/.config/omarchy/shell.toml (the shell picks it up live). True if changed."""
    return _write_css_block(SHELL_TOML, SHELL_START, SHELL_END, shell_block(step))


TRANSPARENCY_LUA = """
-- OmaSkins makes only its own background see-through (its previews stay solid), so Hyprland leaves its
-- windows out of the whole-window fade.
o.window({ class = "^io.github.jesuslovesyou1013.omaskins$" }, { tag = "-default-opacity", opacity = "1 1" })

-- Transparency (OmaSkins): the step chosen in OmaSkins, read from ~/.config/omaskins/transparency, so
-- changing it never needs a Hyprland reload. No file: Omarchy's and the theme's own transparency.
local omaskins_t = nil
if type(io) == "table" and type(io.open) == "function" and type(os) == "table" and type(os.getenv) == "function" then
  local dir = os.getenv("XDG_CONFIG_HOME") or ((os.getenv("HOME") or "") .. "/.config")
  local ok, f = pcall(io.open, dir .. "/omaskins/transparency", "r")
  if ok and f then
    omaskins_t = f:read("*l")
    f:close()
  end
end
if type(omaskins_t) == "string" then
  local a, b, blur = omaskins_t:match("^([%d.]+) ([%d.]+) (%a+)$")
  if a then
    -- Nautilus does its own (sidebar a little more solid, its icons solid): see NAUTILUS_CSS in OmaSkins.
    o.window({ class = "^org.gnome.Nautilus$" }, { tag = "-default-opacity", opacity = "1 1" })
    -- File dialogs too, while OmaSkins' dialog block is in GTK3's stylesheet (see DIALOG_CSS in OmaSkins).
    local config_dir = os.getenv("XDG_CONFIG_HOME") or ((os.getenv("HOME") or "") .. "/.config")
    local okd, fd = pcall(io.open, config_dir .. "/gtk-3.0/gtk.css", "r")
    if okd and fd then
      local css = fd:read("*a") or ""
      fd:close()
      if css:find("OmaSkins Manager: file dialog", 1, true) then
        o.window({ class = "^xdg-desktop-portal-gtk$" }, { tag = "-default-opacity", opacity = "1 1" })
      end
    end
    o.window({ tag = "default-opacity" }, { opacity = a .. " " .. b })
    if blur == "blur" then
      """ + TRANSPARENCY_BLUR + """
      """ + SHELL_BLUR_RULE + """
    end
  end
end
"""

# Qt apps (OmaSkins' Qt style, see qtstyle.py): turned on for apps opened from now on, only while the built
# style, OmaSkins itself (removing the plugin turns it off at the next login) and the "Qt apps" switch are
# all there. Omarchy's own QT_QPA_PLATFORMTHEME=gtk3 stays; Qt6 apps ignore a Qt5 style. Windows the style
# makes see-through do their own transparency: the engine tags them, and this keeps Hyprland's fade off.
QT_LUA = """
o.window({ tag = "omaskins-qt" }, { opacity = "1 1" })
if type(io) == "table" and type(io.open) == "function" and type(os) == "table" and type(os.getenv) == "function" then
  local home = os.getenv("HOME") or ""
  local config = os.getenv("XDG_CONFIG_HOME") or (home .. "/.config")
  local function exists(path)
    local ok, f = pcall(io.open, path, "r")
    if ok and f then
      f:close()
      return true
    end
    return false
  end
  local switched_off = false
  local ok, f = pcall(io.open, config .. "/omaskins/qt-apps", "r")
  if ok and f then
    switched_off = (f:read("*l") or "") == "off"
    f:close()
  end
  local plugins = home .. "/.local/share/omaskins/qt5"
  if not switched_off and exists(plugins .. "/styles/libomaskins.so")
      and exists(config .. "/omarchy/plugins/io.github.jesuslovesyou1013.omaskins/manifest.json") then
    local path = os.getenv("QT_PLUGIN_PATH") or ""
    if not path:find(plugins, 1, true) then   -- a reload must not add it twice
      hl.env("QT_PLUGIN_PATH", path ~= "" and (plugins .. ":" .. path) or plugins)
    end
    hl.env("QT_STYLE_OVERRIDE", "OmaSkins")
  elseif os.getenv("QT_STYLE_OVERRIDE") == "OmaSkins" then
    hl.env("QT_STYLE_OVERRIDE", "")   -- switched off: Qt ignores an empty one (no unset in Hyprland)
  end
end
"""


GTK4_CSS = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "gtk-4.0" / "gtk.css"
NAUTILUS_CSS_START = "/* >>> OmaSkins Manager: Nautilus transparency (set it in OmaSkins; at Omarchy's default this block goes) */"
NAUTILUS_CSS_END = "/* <<< OmaSkins Manager */"
NAUTILUS_SIDEBAR_MORE_SOLID = 0.10   # owner, 2026-10-02: the folders/bookmarks sidebar 10% less see-through


def nautilus_css(step):
    """Nautilus's own transparency (GTK reads ~/.config/gtk-4.0/gtk.css; Omarchy doesn't use that file):
    only its backgrounds, the sidebar a bit more solid than the files; icons and text stay solid. Scoped to
    Nautilus's window. Empty at the default step (then Hyprland fades it as Omarchy does)."""
    if step == TRANSPARENCY_DEFAULT:
        return ""
    a, b, _ = transparency_values(step)
    w = "window.nautilus-window"

    def greys(mode):
        # The same greys as the file dialogs (owner, 2026-10-02: the dialog's grey, and both alike).
        view, sidebar = DIALOG_GREYS[mode]
        return [
            # The see-through background is the whole window's: when Nautilus is narrow it folds the sidebar
            # away and its panes stop painting (owner, 2026-10-02: a narrow Nautilus went fully clear).
            f"{w}, {w}.background {{ background-color: alpha({view}, {a:g}); }}",
            f"{w}:backdrop, {w}.background:backdrop {{ background-color: alpha({view}, {b:g}); }}",
            f"{w} .sidebar-pane {{ background-color: alpha({sidebar}, {_sidebar_layer(a):g}); }}",
            f"{w}:backdrop .sidebar-pane {{ background-color: alpha({sidebar}, {_sidebar_layer(b):g}); }}"]
    return "\n".join([
        NAUTILUS_CSS_START,
        f"{w} .content-pane, {w} .content-pane .view, {w} .nautilus-grid-view, {w} .nautilus-list-view,",
        f"{w} .content-pane scrolledwindow, {w} .content-pane toolbarview, {w} .sidebar-pane toolbarview,",
        f"{w} placessidebar, {w} .navigation-sidebar,",
        f"{w} headerbar, {w} .top-bar {{ background-color: transparent; box-shadow: none; }}",
        # Light or dark follows Omarchy's setting live (GTK 4.16+), so a theme switch needs no restart.
        *greys("light"),
        "@media (prefers-color-scheme: dark) {",
        *("  " + line for line in greys("dark")),
        "}",
        NAUTILUS_CSS_END]) + "\n"


def _sidebar_layer(base):
    """The sidebar's own layer on top of the window's, so the two together are 10 points more solid."""
    target = min(1.0, base + NAUTILUS_SIDEBAR_MORE_SOLID)
    return round((target - base) / (1 - base), 3) if base < 1 else 0


def _write_css_block(path, start_mark, end_mark, block):
    """Put one OmaSkins block in a GTK stylesheet (replacing its last one), leaving anything else in the file
    alone; an empty block removes it (and the file too, if nothing else was ever in it). True if changed."""
    try:
        text = path.read_text()
    except OSError:
        text = ""
    start, end = text.find(start_mark), text.find(end_mark)
    if start >= 0 and end > start:
        before, after = text[:start].rstrip("\n"), text[end + len(end_mark):].lstrip("\n")
        rest = (before + "\n\n" + after) if before and after else (before + "\n" if before else after)
    else:
        rest = text
    new = (rest.rstrip("\n") + "\n\n" + block) if rest.strip() and block else (block or rest)
    if new == text:
        return False
    if new.strip():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(".gtk.css.omaskins-tmp")
        tmp.write_text(new)
        tmp.replace(path)
    else:
        path.unlink(missing_ok=True)
    return True


def write_nautilus_css(step):
    """OmaSkins' Nautilus block in ~/.config/gtk-4.0/gtk.css; gone at the default step. True if changed."""
    return _write_css_block(GTK4_CSS, NAUTILUS_CSS_START, NAUTILUS_CSS_END, nautilus_css(step))


# --------------------------------------------------------------------------- file dialogs
#
# Open/Save dialogs come from Omarchy's dialog service (xdg-desktop-portal-gtk), which draws them with GTK3.
# Owner, 2026-10-02: they should look like Nautilus (its stock grey, its background-only transparency at the
# slider's level). GTK3 reads ~/.config/gtk-3.0/gtk.css only when the service starts, so after a change
# OmaSkins restarts the service while no dialog is open (nothing on screen); the next dialog has the new look.
# Dark themes need GTK3's dark theme (Omarchy asks for "Adwaita-dark", from gnome-themes-extra): without it
# the dialog text is dark, so no block is written then. GTK3 can't tell a file dialog's window from another
# dialog's, so the block covers GTK3 dialogs (in practice the service's: pickers, "open with", permissions).
GTK3_CSS = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "gtk-3.0" / "gtk.css"
DIALOG_CSS_START = "/* >>> OmaSkins Manager: file dialog transparency, like Nautilus (set it in OmaSkins) */"
DIALOG_CSS_END = "/* <<< OmaSkins Manager */"
DIALOG_CLASS = "xdg-desktop-portal-gtk"
# libadwaita's own greys (Nautilus): its view (the window's see-through background) and sidebar.
DIALOG_GREYS = {"dark": ("#1d1d20", "#2e2e32"), "light": ("#ffffff", "#ebebed")}
# GTK3's dark Adwaita: Omarchy's gnome-themes-extra puts it in /usr/share/themes, and that theme is one line
# pointing at the dark style built into GTK3 itself. Where the package is missing (the Try Omarchy VM image),
# OmaSkins writes the same line to your own themes folder, which GTK3 also reads: no package, no password.
# Only then; it's removed again as soon as the real one is there, and when OmaSkins is removed (Service.qml).
GTK3_USER_DARK = (Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share")) / "themes"
                  / "Adwaita-dark" / "gtk-3.0" / "gtk.css")
GTK3_SYSTEM_DARK = [Path(d) / "Adwaita-dark/gtk-3.0/gtk.css" for d in (HOME / ".themes", "/usr/share/themes")]
GTK3_DARK_MARK = "/* Written by OmaSkins: GTK3's own dark Adwaita, as gnome-themes-extra has it. Removed with OmaSkins. */"
GTK3_DARK_CSS = GTK3_DARK_MARK + '\n@import url("resource:///org/gtk/libgtk/theme/Adwaita/gtk-contained-dark.css");\n'


def theme_mode():
    """"dark" or "light" for the current theme, by Omarchy's own resolver (colors.toml's mode, the legacy
    light.mode marker, or the background's brightness); dark if it can't tell."""
    colors = STATE_DIR / "theme" / "colors.toml"
    try:
        out = subprocess.run(["omarchy-theme-color", "--file", str(colors), "mode"], capture_output=True,
                             text=True, timeout=5, env=child_env()).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return "light" if out == "light" else "dark"


def gtk3_dark_available():
    return GTK3_USER_DARK.exists() or any(p.exists() for p in GTK3_SYSTEM_DARK)


def ensure_gtk3_dark():
    """OmaSkins' copy of GTK3's dark theme while the real one is missing, and gone once it's there. Never
    touches a file of yours (only one with OmaSkins' mark). True if it changed anything."""
    try:
        ours = GTK3_USER_DARK.read_text().startswith(GTK3_DARK_MARK)
    except OSError:
        ours = None   # nothing there
    if any(p.exists() for p in GTK3_SYSTEM_DARK):
        if not ours:
            return False
        GTK3_USER_DARK.unlink()
        for folder in (GTK3_USER_DARK.parent, GTK3_USER_DARK.parent.parent):
            try:
                folder.rmdir()   # only if empty
            except OSError:
                break
        return True
    if ours is not None:
        return False   # ours already, or one of yours: left alone
    GTK3_USER_DARK.parent.mkdir(parents=True, exist_ok=True)
    tmp = GTK3_USER_DARK.with_name(".gtk.css.omaskins-tmp")
    tmp.write_text(GTK3_DARK_CSS)
    tmp.replace(GTK3_USER_DARK)
    return True


def dialog_css(step, mode):
    """GTK3 dialogs like Nautilus: its grey, the background see-through at the step, the sidebar 10 points
    more solid, files and text solid. Empty at the default step, and for dark themes without GTK3's dark theme."""
    if step == TRANSPARENCY_DEFAULT or (mode == "dark" and not gtk3_dark_available()):
        return ""
    a, b, _ = transparency_values(step)
    view, sidebar = DIALOG_GREYS[mode]
    extra = _sidebar_layer
    d = "dialog"
    fc = "dialog filechooser"
    return "\n".join([
        DIALOG_CSS_START,
        # The window and its title bar (GTK3 doesn't paint the window's background behind the title bar).
        f"{d}.background, {d}.background.csd, {d} headerbar.titlebar, {d} .titlebar headerbar "
        f"{{ background-color: alpha({view}, {a:g}); background-image: none; box-shadow: none; }}",
        f"{d}.background:backdrop, {d}.background.csd:backdrop, {d}:backdrop headerbar.titlebar, "
        f"{d}:backdrop .titlebar headerbar {{ background-color: alpha({view}, {b:g}); }}",
        f"{fc}, {fc} box, {fc} stack, {fc} paned, {fc} scrolledwindow, {fc} viewport, {fc} treeview.view, {fc} .view,",
        f"{fc} treeview.view header button, {fc} placessidebar list, {fc} actionbar, {fc} revealer, {fc} searchbar,",
        f"{d} .dialog-action-area {{ background-color: transparent; background-image: none; box-shadow: none; }}",
        f"{fc} placessidebar {{ background-color: alpha({sidebar}, {extra(a):g}); }}",
        f"{d}:backdrop filechooser placessidebar {{ background-color: alpha({sidebar}, {extra(b):g}); }}",
        DIALOG_CSS_END]) + "\n"


def write_dialog_css(step, mode=None):
    """OmaSkins' file dialog block in ~/.config/gtk-3.0/gtk.css. True if changed."""
    return _write_css_block(GTK3_CSS, DIALOG_CSS_START, DIALOG_CSS_END, dialog_css(step, mode or theme_mode()))


def transparency_values(step):
    """(focused, unfocused, blur) for a step; Omarchy's own for the default step."""
    v = TRANSPARENCY_STEPS[step]
    return v if v else (*OMARCHY_OPACITY, False)


def transparency_line(step):
    a, b, blur = transparency_values(step)
    return f"{a:g} {b:g} {'blur' if blur else 'noblur'}"


def transparency_step():
    """The step in use (0-4): the default when OmaSkins sets nothing."""
    try:
        line = TRANSPARENCY_FILE.read_text().strip()
    except OSError:
        return TRANSPARENCY_DEFAULT
    for i in range(len(TRANSPARENCY_STEPS)):
        if i != TRANSPARENCY_DEFAULT and transparency_line(i) == line:
            return i
    return TRANSPARENCY_DEFAULT


def save_transparency(step):
    if step == TRANSPARENCY_DEFAULT:
        TRANSPARENCY_FILE.unlink(missing_ok=True)
        return
    TRANSPARENCY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRANSPARENCY_FILE.with_name(".transparency.tmp")
    tmp.write_text(transparency_line(step) + "\n")
    tmp.replace(TRANSPARENCY_FILE)


def transparency_action(step):
    """No password, no reload: fades every see-through window to the new step over 1.5 s."""
    step = max(0, min(len(TRANSPARENCY_STEPS) - 1, int(step)))
    return Action("Transparency", f"fade windows to step {step + 1} of 5; save {TRANSPARENCY_FILE}",
                  steps=(("apply_transparency", step),))


def qt_apps_action(on):
    """No password. On: the engine builds OmaSkins' Qt style within a few seconds (once per Qt5 update), then
    omaskins.lua turns it on for Qt apps opened from then on. Off: style and palette removed at once."""
    return Action("Qt apps", "theme Qt apps opened from now on" if on else
                  "stop theming Qt apps opened from now on; remove OmaSkins' Qt style",
                  steps=(("qt_apps", bool(on)),))


def corners_action(on, px):
    """No password. OmaSkins' own file + one require line; saving it makes Hyprland reload by itself
    (windows), OmaSkins restyles its own frame the moment the new radius is live, then the running
    shell re-reads its style (bar, menus, popups) without restarting. A reload is only forced if
    Hyprland didn't pick the change up by itself."""
    px = max(0, min(CORNERS_MAX, int(px)))
    return Action("Round corners" if on else "Square corners",
                  f"write {CORNERS_FILE} (+ require line in looknfeel.lua), wait for Hyprland, "
                  "omarchy-shell shell applyTheme (restyle, no restart)",
                  busy="Rounding corners…" if on else "Back to each theme's own corners…",
                  done=f"Corners: {px} px for every theme." if on else "Corners: each theme's own again.",
                  steps=(("apply_corners", on, px), ("notify", "restyle"), ("shell_restyle",)))
